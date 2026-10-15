"""Notifications by e-mail and web push, only on the channels each person turned on (everything starts off).

`notify(users, message)` is the single entry point; the senders never raise (a failed notification must not break
the action that caused it) and every attempt is written to NotificationLog (kind and result, never the text)."""
import json
import logging
import re
import smtplib
import threading
from collections import namedtuple
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import connection
from django.db.models import Q
from django.urls import reverse
from django.utils import formats, timezone

from .models import NotificationLog, NotificationPreference

logger = logging.getLogger(__name__)

Message = namedtuple('Message', 'kind title body path')

# Which per-type switch (NotificationPreference) a kind of message obeys; kinds not listed (the test) always go
KIND_FLAG = {
    'request_new': 'notify_changes', 'request_answered': 'notify_changes', 'class_moved': 'notify_changes',
    'class_cancelled': 'notify_changes', 'move_undone': 'notify_changes',
    'class_reminder': 'notify_class_reminder', 'plan_expiry': 'notify_plan_expiry',
}
Status = NotificationLog.Status


def push_configured():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def email_configured():
    return 'console' not in settings.EMAIL_BACKEND


def absolute(path):
    base = (settings.SITE_URL or '').rstrip('/')
    return f'{base}{path}' if base else ''


def when(moment):
    """'qui 15/10 às 07:00' in the local time zone."""
    local = timezone.localtime(moment)
    return f"{formats.date_format(local, 'D d/m')} às {formats.date_format(local, 'H:i')}"


def device_name(user_agent):
    """'Chrome no Windows' from a User-Agent string (good enough to tell devices apart in the list)."""
    ua = user_agent or ''
    browser = next((name for key, name in (('Edg/', 'Edge'), ('OPR/', 'Opera'), ('SamsungBrowser', 'Samsung Internet'),
                                           ('Firefox/', 'Firefox'), ('CriOS', 'Chrome'), ('Chrome/', 'Chrome'),
                                           ('Safari/', 'Safari')) if key in ua), 'Navegador')
    system = next((name for key, name in (('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Android', 'Android'),
                                          ('Windows', 'Windows'), ('Mac OS X', 'Mac'), ('Linux', 'Linux')) if key in ua), '')
    return f'{browser} no {system}' if system else browser


def staff_users():
    return list(User.objects.filter(is_staff=True, is_active=True))


# --- messages (no health data, no amounts; just what changed and when) -------------------------------------

def request_created(asked):
    name = asked.appointment.person.person_name
    what = 'cancelar' if asked.kind == 'cancel' else 'remarcar'
    return Message('request_new', f'Novo pedido de {name}', f'{name} pediu para {what} a aula de {when(asked.appointment.starts_at)}.',
                   reverse('core:agenda'))


def request_answered(asked, note=''):
    start = when(asked.appointment.starts_at)
    if asked.status == 'approved':
        if asked.kind == 'cancel':
            return Message('request_answered', 'Pedido aprovado', f'Sua aula de {start} foi cancelada.', reverse('core:my_agenda'))
        return Message('request_answered', 'Pedido aprovado', f'Sua aula de {start} foi remarcada para {when(asked.proposed_starts_at)}.',
                       reverse('core:my_agenda'))
    reason = f' Motivo: {note}' if note else ''
    return Message('request_answered', 'Pedido recusado', f'Sua personal não pôde atender o pedido para a aula de {start}.{reason}',
                   reverse('core:my_agenda'))


def class_moved(old, new):
    return Message('class_moved', 'Aula remarcada', f'Sua aula de {when(old.starts_at)} foi remarcada para {when(new.starts_at)}.',
                   reverse('core:my_agenda'))


def class_cancelled(appointment):
    return Message('class_cancelled', 'Aula cancelada', f'Sua aula de {when(appointment.starts_at)} foi cancelada.',
                   reverse('core:my_agenda'))


def move_undone(original):
    return Message('move_undone', 'Aula mantida', f'A remarcação foi desfeita: sua aula continua em {when(original.starts_at)}.',
                   reverse('core:my_agenda'))


def day_word(moment, now=None):
    """'hoje', 'amanhã' or 'qui 15/10', by the local calendar date (relative to `now`)."""
    local = timezone.localtime(moment).date()
    today = timezone.localdate(now)
    if local == today:
        return 'hoje'
    if (local - today).days == 1:
        return 'amanhã'
    return formats.date_format(local, 'D d/m')


def class_reminder(appointment, now=None):
    clock = formats.date_format(timezone.localtime(appointment.starts_at), 'H:i')
    return Message('class_reminder', 'Lembrete de aula', f'Sua aula é {day_word(appointment.starts_at, now)} às {clock}.',
                   reverse('core:my_agenda'))


def plan_expiring(days, left=None):
    when_ = 'hoje' if days == 0 else ('amanhã' if days == 1 else f'em {days} dias')
    sessions = '' if left is None else f' Ainda {"resta" if left == 1 else "restam"} {left} aula{"" if left == 1 else "s"}.'
    return Message('plan_expiry', 'Seu pacote está vencendo', f'Seu pacote vence {when_}.{sessions} Fale com a sua personal para combinar.',
                   reverse('core:my_agenda'))


def test_message():
    return Message('test', 'Teste de notificação', 'Tudo certo: este aparelho recebe os avisos do Muscle Track.',
                   reverse('core:notification_settings'))


# --- errors: short, in Portuguese, and never with a push endpoint (it works like a password) ------------

def _scrub(text):
    return re.sub(r'https?://\S+', '<endereço>', str(text))[:150]


def _email_error(exc):
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return 'o servidor de e-mail recusou o login: confira EMAIL_HOST_USER e a senha de app (EMAIL_HOST_PASSWORD)'
    if isinstance(exc, (smtplib.SMTPConnectError, ConnectionError, TimeoutError, OSError)):
        return f'não foi possível conectar ao servidor de e-mail ({settings.EMAIL_HOST}:{settings.EMAIL_PORT}); confira a rede do servidor e o EMAIL_HOST'
    return f'{type(exc).__name__}: {_scrub(exc)}'


def _push_error(exc, endpoint):
    import requests

    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        host = urlparse(endpoint).hostname or 'serviço de push'
        return (f'o servidor não conseguiu abrir conexão com {host} (porta 443); '
                'confira se ele tem acesso à internet, DNS e proxy (HTTPS_PROXY)')
    return f'{type(exc).__name__}: {_scrub(exc)}'


# --- delivery ------------------------------------------------------------------------------------------------

def notify(users, message, sync=False):
    """Send `message` to each user on the channels they enabled (and for the kinds they want). Returns without
    waiting when running async; `sync=True` (the reminders command) always sends inline."""
    prefs = NotificationPreference.objects.filter(
        user__in=[u for u in users if u is not None], user__is_active=True).filter(
        Q(email_enabled=True) | Q(push_enabled=True))
    if message.kind in KIND_FLAG:
        prefs = prefs.filter(**{KIND_FLAG[message.kind]: True})
    ids = list(prefs.values_list('user_id', flat=True))
    if not ids:
        return
    if settings.NOTIFICATIONS_ASYNC and not sync:
        threading.Thread(target=_run, args=(ids, message, True), daemon=True).start()
    else:
        _run(ids, message, False)


def _run(user_ids, message, close):
    try:
        for user in User.objects.filter(pk__in=user_ids):
            deliver(user, message)
    except Exception:   # never let a notification take anything else down
        logger.exception('notification run failed')
    finally:
        if close:
            connection.close()


def deliver(user, message, only=None):
    """Send to one user now. `only` limits it to a channel ('email' or 'push'), used by the "send a test" button."""
    pref = NotificationPreference.objects.filter(user=user).first()
    if pref is None:
        return
    if pref.email_enabled and only in (None, 'email'):
        _send_email(user, message)
    if pref.push_enabled and only in (None, 'push'):
        _send_push(user, message)


def _log(user, message, channel, status, detail=''):
    NotificationLog.objects.create(user=user, kind=message.kind, channel=channel, status=status, detail=detail[:200])


def _send_email(user, message):
    if not user.email:
        return _log(user, message, 'email', Status.SKIPPED, 'sem e-mail cadastrado')
    link = absolute(message.path)
    settings_link = absolute(reverse('core:notification_settings'))
    body = message.body + (f'\n\nAbrir: {link}' if link else '')
    body += ('\n\n—\nVocê recebe este aviso porque ativou as notificações por e-mail.'
             + (f' Para parar: {settings_link}' if settings_link else ' Para parar, desative em Notificações no app.'))
    error = ''
    for _ in range(2):   # one retry: SMTP servers drop connections now and then
        try:
            send_mail(message.title, body, None, [user.email], fail_silently=False)
            return _log(user, message, 'email', Status.SENT)
        except Exception as exc:
            error = _email_error(exc)
    logger.warning('e-mail notification failed for user %s: %s', user.pk, error)
    _log(user, message, 'email', Status.FAILED, error)


def _send_push(user, message):
    subscriptions = list(user.push_subscriptions.all())
    if not push_configured():
        return _log(user, message, 'push', Status.SKIPPED, 'web push não configurado no servidor')
    if not subscriptions:
        return _log(user, message, 'push', Status.SKIPPED, 'nenhum aparelho inscrito')
    from pywebpush import WebPushException, webpush

    payload = json.dumps({'title': message.title, 'body': message.body, 'url': message.path, 'tag': message.kind})
    subject = settings.VAPID_SUBJECT or f'mailto:{settings.PRIVACY_CONTACT_EMAIL or settings.EMAIL_HOST_USER or "admin@example.com"}'
    for sub in subscriptions:
        try:
            webpush({'endpoint': sub.endpoint, 'keys': {'p256dh': sub.p256dh, 'auth': sub.auth}}, data=payload,
                    vapid_private_key=settings.VAPID_PRIVATE_KEY, vapid_claims={'sub': subject}, ttl=86400, timeout=10)
        except WebPushException as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status in (404, 410):   # the browser dropped this subscription
                sub.delete()
                _log(user, message, 'push', Status.SKIPPED, 'aparelho removido (assinatura expirada)')
            else:
                error = _push_error(exc, sub.endpoint)
                logger.warning('push failed for user %s: %s', user.pk, error)
                _log(user, message, 'push', Status.FAILED, error)
        except Exception as exc:
            error = _push_error(exc, sub.endpoint)
            logger.warning('push failed for user %s: %s', user.pk, error)
            _log(user, message, 'push', Status.FAILED, error)
        else:
            sub.last_success_at = timezone.now()
            sub.save(update_fields=['last_success_at'])
            _log(user, message, 'push', Status.SENT)
