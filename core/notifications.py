"""Notifications by e-mail and web push, only on the channels each person turned on (everything starts off).

`notify(users, message)` is the single entry point; the senders never raise (a failed notification must not break
the action that caused it) and every attempt is written to NotificationLog (kind and result, never the text).

The title and body of a `Message` are lazy texts (`lazy_text`): they are only built when `deliver` shows them, inside
`translation.override(<the recipient's language>)`, so one message reaches each person in their own language."""
import json
import logging
import re
import smtplib
import threading
from collections import namedtuple
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import connection
from django.db.models import Q
from django.urls import reverse
from django.utils import formats, timezone, translation
from django.utils.translation import gettext as _, ngettext

from . import telegram
from .i18n import language_for, lazy_text
from .models import NotificationLog, NotificationPreference, StudentEvent

logger = logging.getLogger(__name__)

Message = namedtuple('Message', 'kind title body path')

# Which per-type switch (NotificationPreference) a kind of message obeys; kinds not listed (the test) always go
KIND_FLAG = {
    'request_new': 'notify_changes', 'request_answered': 'notify_changes', 'class_moved': 'notify_changes',
    'class_cancelled': 'notify_changes', 'move_undone': 'notify_changes',
    'class_reminder': 'notify_class_reminder', 'plan_expiry': 'notify_plan_expiry', 'package_low': 'notify_plan_expiry',
    'package_attention': 'notify_renewals',
}
Status = NotificationLog.Status


def push_configured():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def telegram_configured():
    return telegram.configured()


def email_configured():
    return 'console' not in settings.EMAIL_BACKEND


def absolute(path):
    base = (settings.SITE_URL or '').rstrip('/')
    return f'{base}{path}' if base else ''


def when(moment):
    """'qui 15/10 às 07:00' in the local time zone (words in the active language)."""
    local = timezone.localtime(moment)
    return _('%(date)s às %(time)s') % {'date': formats.date_format(local, 'D d/m'), 'time': formats.date_format(local, 'H:i')}


def device_name(user_agent):
    """'Chrome no Windows' from a User-Agent string (good enough to tell devices apart in the list)."""
    ua = user_agent or ''
    browser = next((name for key, name in (('Edg/', 'Edge'), ('OPR/', 'Opera'), ('SamsungBrowser', 'Samsung Internet'),
                                           ('Firefox/', 'Firefox'), ('CriOS', 'Chrome'), ('Chrome/', 'Chrome'),
                                           ('Safari/', 'Safari')) if key in ua), _('Navegador'))
    system = next((name for key, name in (('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Android', 'Android'),
                                          ('Windows', 'Windows'), ('Mac OS X', 'Mac'), ('Linux', 'Linux')) if key in ua), '')
    return _('%(browser)s no %(system)s') % {'browser': browser, 'system': system} if system else browser


def staff_users():
    return list(User.objects.filter(is_staff=True, is_active=True))


# --- messages (no health data, no amounts; just what changed and when) -------------------------------------
# Titles and bodies are lazy: built when shown, in the recipient's language. Everything they need from the database is
# read here, up front, so building the text later (maybe in another thread) touches no query.

def request_created(asked):
    name = asked.appointment.person.person_name
    cancel = asked.kind == 'cancel'
    starts = asked.appointment.starts_at

    def body():
        if cancel:
            return _('%(name)s pediu para cancelar a aula de %(when)s.') % {'name': name, 'when': when(starts)}
        return _('%(name)s pediu para remarcar a aula de %(when)s.') % {'name': name, 'when': when(starts)}

    return Message('request_new', lazy_text(lambda: _('Novo pedido de %(name)s') % {'name': name}), lazy_text(body),
                   reverse('core:agenda'))


def request_answered(asked, note=''):
    starts = asked.appointment.starts_at
    proposed = asked.proposed_starts_at
    path = reverse('core:my_agenda')
    title_approved = lazy_text(lambda: _('Pedido aprovado'))
    if asked.status == 'approved':
        if asked.kind == 'cancel':
            return Message('request_answered', title_approved,
                           lazy_text(lambda: _('Sua aula de %(start)s foi cancelada.') % {'start': when(starts)}), path)
        return Message('request_answered', title_approved,
                       lazy_text(lambda: _('Sua aula de %(start)s foi remarcada para %(new)s.')
                                 % {'start': when(starts), 'new': when(proposed)}), path)

    def declined():
        if note:
            return _('Sua personal não pôde atender o pedido para a aula de %(start)s. Motivo: %(note)s') % {
                'start': when(starts), 'note': note}
        return _('Sua personal não pôde atender o pedido para a aula de %(start)s.') % {'start': when(starts)}

    return Message('request_answered', lazy_text(lambda: _('Pedido recusado')), lazy_text(declined), path)


def class_moved(old, new):
    old_starts, new_starts = old.starts_at, new.starts_at
    return Message('class_moved', lazy_text(lambda: _('Aula remarcada')),
                   lazy_text(lambda: _('Sua aula de %(old)s foi remarcada para %(new)s.')
                             % {'old': when(old_starts), 'new': when(new_starts)}),
                   reverse('core:my_agenda'))


def class_cancelled(appointment):
    starts = appointment.starts_at
    return Message('class_cancelled', lazy_text(lambda: _('Aula cancelada')),
                   lazy_text(lambda: _('Sua aula de %(start)s foi cancelada.') % {'start': when(starts)}),
                   reverse('core:my_agenda'))


def move_undone(original):
    starts = original.starts_at
    return Message('move_undone', lazy_text(lambda: _('Aula mantida')),
                   lazy_text(lambda: _('A remarcação foi desfeita: sua aula continua em %(start)s.') % {'start': when(starts)}),
                   reverse('core:my_agenda'))


def day_word(moment, now=None):
    """'hoje', 'amanhã' or 'qui 15/10', by the local calendar date (relative to `now`)."""
    local = timezone.localtime(moment).date()
    today = timezone.localdate(now)
    if local == today:
        return _('hoje')
    if (local - today).days == 1:
        return _('amanhã')
    return formats.date_format(local, 'D d/m')


def class_reminder(appointment, now=None):
    starts = appointment.starts_at

    def body():
        clock = formats.date_format(timezone.localtime(starts), 'H:i')
        return _('Sua aula é %(day)s às %(time)s.') % {'day': day_word(starts, now), 'time': clock}

    return Message('class_reminder', lazy_text(lambda: _('Lembrete de aula')), lazy_text(body), reverse('core:my_agenda'))


def _in_days(days):
    """'em 3 dias' (the days from now, once 'hoje' and 'amanhã' are ruled out)."""
    return _('em %(n)d dias') % {'n': days}


def plan_expiring(days, left=None):
    def body():
        when_ = _('hoje') if days == 0 else (_('amanhã') if days == 1 else _in_days(days))
        text = _('Seu pacote vence %(when)s.') % {'when': when_}
        if left is not None:
            text += ' ' + ngettext('Ainda resta %(n)d aula.', 'Ainda restam %(n)d aulas.', left) % {'n': left}
        return text + ' ' + _('Fale com a sua personal para combinar.')

    return Message('plan_expiry', lazy_text(lambda: _('Seu pacote está vencendo')), lazy_text(body), reverse('core:my_agenda'))


def package_low(left):
    return Message('package_low', lazy_text(lambda: _('Seu pacote está acabando')),
                   lazy_text(lambda: ngettext('Resta %(n)d aula no seu pacote. Fale com a sua personal para renovar.',
                                              'Restam %(n)d aulas no seu pacote. Fale com a sua personal para renovar.',
                                              left) % {'n': left}),
                   reverse('core:my_agenda'))


def package_attention(rows, today):
    """One notice to the trainer about every package that just needed attention (see packages.alerts)."""
    items = [(' + '.join(p.person_name for p in r['people']), r['expired'], r['expiring'],
              (r['ends_on'] - today).days if r['expiring'] else None, r['left']) for r in rows]

    def line(name, expired, expiring, days, left):
        values = {'name': name, 'n': left}
        if expired:
            return ngettext('%(name)s: pacote vencido, %(n)d aula sem usar',
                            '%(name)s: pacote vencido, %(n)d aulas sem usar', left) % values
        if expiring:
            values['when'] = _('hoje') if days <= 0 else (_('amanhã') if days == 1 else _in_days(days))
            return ngettext('%(name)s: vence %(when)s, %(n)d aula restante',
                            '%(name)s: vence %(when)s, %(n)d aulas restantes', left) % values
        return ngettext('%(name)s: %(n)d aula restante', '%(name)s: %(n)d aulas restantes', left) % values

    def body():
        lines = [line(*item) for item in items]
        shown = lines[:5] + ([_('e mais %(n)d') % {'n': len(lines) - 5}] if len(lines) > 5 else [])
        return '; '.join(shown) + '.'

    return Message('package_attention',
                   lazy_text(lambda: ngettext('%(n)d pacote para renovar', '%(n)d pacotes para renovar', len(items))
                             % {'n': len(items)}),
                   lazy_text(body), reverse('core:finance'))


def test_message():
    return Message('test', lazy_text(lambda: _('Teste de notificação')),
                   lazy_text(lambda: _('Tudo certo: este aparelho recebe os avisos do Muscle Track.')),
                   reverse('core:notification_settings'))


# --- errors: short, in the recipient's language, and never with a push endpoint (it works like a password) --

def _scrub(text):
    text = str(text)
    if settings.TELEGRAM_BOT_TOKEN:   # the token is part of every Telegram API URL: it works like a password
        text = text.replace(settings.TELEGRAM_BOT_TOKEN, '***')
    return re.sub(r'https?://\S+', _('<endereço>'), text)[:150]


def _email_error(exc):
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return _('o servidor de e-mail recusou o login: confira EMAIL_HOST_USER e a senha de app (EMAIL_HOST_PASSWORD)')
    if isinstance(exc, (smtplib.SMTPConnectError, ConnectionError, TimeoutError, OSError)):
        return _('não foi possível conectar ao servidor de e-mail (%(host)s:%(port)s); '
                 'confira a rede do servidor e o EMAIL_HOST') % {'host': settings.EMAIL_HOST, 'port': settings.EMAIL_PORT}
    return f'{type(exc).__name__}: {_scrub(exc)}'


def _push_error(exc, endpoint):
    import requests

    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        host = urlparse(endpoint).hostname or _('serviço de push')
        return _('o servidor não conseguiu abrir conexão com %(host)s (porta 443); '
                 'confira se ele tem acesso à internet, DNS e proxy (HTTPS_PROXY)') % {'host': host}
    return f'{type(exc).__name__}: {_scrub(exc)}'


# --- channels ------------------------------------------------------------------------------------------------

def set_channel(user, person, pref, channel, enabled):
    """Turn one channel on/off, keeping when (the consent) and, for a student, a line in the file's history."""
    field = f'{channel}_enabled'
    if getattr(pref, field) == enabled:
        return
    setattr(pref, field, enabled)
    setattr(pref, f'{channel}_at', timezone.now() if enabled else None)
    if channel == 'telegram' and not enabled:   # withdrawing also forgets the chat and any pending code
        pref.telegram_chat_id, pref.telegram_code, pref.telegram_code_expires = None, '', None
    pref.save()
    if channel == 'push' and not enabled:
        user.push_subscriptions.all().delete()   # withdrawing also forgets the devices
    if person is not None:
        label = {'email': _('e-mail'), 'push': _('push (aparelho)'), 'telegram': 'Telegram'}[channel]
        StudentEvent.objects.create(
            person=person, kind=StudentEvent.Kind.CONSENT, actor=user,
            summary=(_('Ativou avisos por %(label)s') if enabled else _('Desativou avisos por %(label)s')) % {'label': label},
            changes={'channel': channel, 'granted': enabled})


# --- delivery ------------------------------------------------------------------------------------------------

def notify(users, message, sync=False):
    """Send `message` to each user on the channels they enabled (and for the kinds they want). Returns without
    waiting when running async; `sync=True` (the reminders command) always sends inline."""
    prefs = NotificationPreference.objects.filter(
        user__in=[u for u in users if u is not None], user__is_active=True).filter(
        Q(email_enabled=True) | Q(push_enabled=True) | Q(telegram_enabled=True))
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
    """Send to one user now. `only` limits it to a channel ('email', 'push' or 'telegram'), used by the "send a test" button.

    Everything the person receives (and what is logged) is written in their own language."""
    pref = NotificationPreference.objects.filter(user=user).first()
    if pref is None:
        return
    with translation.override(language_for(user)):
        if pref.email_enabled and only in (None, 'email'):
            _send_email(user, message)
        if pref.push_enabled and only in (None, 'push'):
            _send_push(user, message)
        if pref.telegram_enabled and only in (None, 'telegram'):
            _send_telegram(user, message)


def _log(user, message, channel, status, detail=''):
    NotificationLog.objects.create(user=user, kind=message.kind, channel=channel, status=status, detail=detail[:200])


def _send_email(user, message):
    """Runs inside `deliver`'s language override: the lazy title and body are turned into text here."""
    if not user.email:
        return _log(user, message, 'email', Status.SKIPPED, _('sem e-mail cadastrado'))
    link = absolute(message.path)
    settings_link = absolute(reverse('core:notification_settings'))
    title = str(message.title)
    body = str(message.body) + ('\n\n' + _('Abrir: %(link)s') % {'link': link} if link else '')
    if settings_link:
        footer = _('Você recebe este aviso porque ativou as notificações por e-mail. Para parar: %(link)s') % {
            'link': settings_link}
    else:
        footer = _('Você recebe este aviso porque ativou as notificações por e-mail. '
                   'Para parar, desative em Notificações no app.')
    body += '\n\n—\n' + footer
    error = ''
    for attempt in range(2):   # one retry: SMTP servers drop connections now and then
        try:
            send_mail(title, body, None, [user.email], fail_silently=False)
            return _log(user, message, 'email', Status.SENT)
        except Exception as exc:
            error = _email_error(exc)
    logger.warning('e-mail notification failed for user %s: %s', user.pk, error)
    _log(user, message, 'email', Status.FAILED, error)


def _telegram_error(exc):
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return _('o servidor não conseguiu abrir conexão com api.telegram.org (porta 443); '
                 'confira se ele tem acesso à internet, DNS e proxy (HTTPS_PROXY)')
    return f'{type(exc).__name__}: {_scrub(exc)}'


def _send_telegram(user, message):
    """Runs inside `deliver`'s language override. A chat that is gone (the person blocked the bot) turns the channel off."""
    pref = NotificationPreference.objects.get(user=user)
    if not telegram.configured():
        return _log(user, message, 'telegram', Status.SKIPPED, _('Telegram não configurado no servidor'))
    if not pref.telegram_chat_id:
        return _log(user, message, 'telegram', Status.SKIPPED, _('chat do Telegram não ligado'))
    link = absolute(message.path)
    text = f'{message.title}\n{message.body}' + (f'\n\n{link}' if link else '')
    try:
        telegram.send(pref.telegram_chat_id, text)
    except telegram.ChatGone:
        set_channel(user, getattr(user, 'person', None), pref, 'telegram', False)
        return _log(user, message, 'telegram', Status.SKIPPED, _('chat removido (o bot foi bloqueado); aviso desligado'))
    except Exception as exc:
        error = _telegram_error(exc)
        logger.warning('telegram notification failed for user %s: %s', user.pk, error)
        return _log(user, message, 'telegram', Status.FAILED, error)
    _log(user, message, 'telegram', Status.SENT)


def _send_push(user, message):
    subscriptions = list(user.push_subscriptions.all())
    if not push_configured():
        return _log(user, message, 'push', Status.SKIPPED, _('web push não configurado no servidor'))
    if not subscriptions:
        return _log(user, message, 'push', Status.SKIPPED, _('nenhum aparelho inscrito'))
    from pywebpush import WebPushException, webpush

    payload = json.dumps({'title': str(message.title), 'body': str(message.body), 'url': message.path, 'tag': message.kind})
    subject = settings.VAPID_SUBJECT or f'mailto:{settings.PRIVACY_CONTACT_EMAIL or settings.EMAIL_HOST_USER or "admin@example.com"}'
    for sub in subscriptions:
        try:
            webpush({'endpoint': sub.endpoint, 'keys': {'p256dh': sub.p256dh, 'auth': sub.auth}}, data=payload,
                    vapid_private_key=settings.VAPID_PRIVATE_KEY, vapid_claims={'sub': subject}, ttl=86400, timeout=10)
        except WebPushException as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status in (404, 410):   # the browser dropped this subscription
                sub.delete()
                _log(user, message, 'push', Status.SKIPPED, _('aparelho removido (assinatura expirada)'))
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
