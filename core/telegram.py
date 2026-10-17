"""The Telegram channel: a bot writes to the chat a person linked with a one-time code.

Linking: the person taps "Ligar o Telegram" in Notificações, which makes a code (valid for a few minutes) and a link
`https://t.me/<bot>?start=<code>`; opening it makes Telegram send the bot `/start <code>`. Telegram calls our webhook
(`/telegram/webhook/`, protected by a secret that Telegram sends back in a header) and the code is matched to the
account, so the app never asks for a phone number and the bot only knows the chat id.

Messages carry the same text as the other channels (what and when, never health data or values). The bot token is a
secret: it is part of every API URL, so errors are scrubbed before they are logged (see notifications._scrub)."""
import hmac
import logging
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from .i18n import clean, language_for
from .models import NotificationPreference

logger = logging.getLogger(__name__)

API = 'https://api.telegram.org'
CODE_MINUTES = 15
TIMEOUT = 10


class TelegramError(Exception):
    """The API answered with an error (`description` is Telegram's own text)."""


class ChatGone(TelegramError):
    """The chat is no longer reachable: the person blocked the bot or deleted the chat."""


def configured():
    """Linking needs the bot, its @username (for the link) and the webhook secret (so only Telegram can call us)."""
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME and settings.TELEGRAM_WEBHOOK_SECRET)


def call(method, payload):
    """One Bot API call; returns Telegram's `result`. Raises TelegramError (ChatGone when the chat is lost)."""
    response = requests.post(f'{API}/bot{settings.TELEGRAM_BOT_TOKEN}/{method}', json=payload, timeout=TIMEOUT)
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 200 and body.get('ok'):
        return body.get('result')
    description = str(body.get('description') or f'HTTP {response.status_code}')
    if response.status_code == 403 or (response.status_code == 400 and 'chat not found' in description.lower()):
        raise ChatGone(description)
    raise TelegramError(description)


def send(chat_id, text):
    """Plain text (no markup, so a name can never be read as formatting) and no link preview."""
    return call('sendMessage', {'chat_id': chat_id, 'text': text[:4000], 'disable_web_page_preview': True})


def new_code(pref):
    """A fresh one-time code for this person (the previous one stops working)."""
    pref.telegram_code = secrets.token_urlsafe(9)   # 12 characters of A-Z a-z 0-9 _ -, which /start accepts
    pref.telegram_code_expires = timezone.now() + timedelta(minutes=CODE_MINUTES)
    pref.save(update_fields=['telegram_code', 'telegram_code_expires'])
    return pref.telegram_code


def active_code(pref):
    """The code while it is still valid, else ''."""
    if pref.telegram_code and pref.telegram_code_expires and pref.telegram_code_expires > timezone.now():
        return pref.telegram_code
    return ''


def link_url(code):
    return f'https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={code}'


def valid_secret(given):
    secret = settings.TELEGRAM_WEBHOOK_SECRET
    return bool(secret) and hmac.compare_digest(str(given or '').encode(), secret.encode())


def _reply(chat_id, text):
    try:
        send(chat_id, text)
    except Exception as exc:   # a reply that cannot be sent must not fail the webhook (Telegram would retry it)
        logger.warning('telegram reply failed: %s', type(exc).__name__)


def handle_update(update):
    """React to one update from Telegram: `/start <code>` links the chat, `/stop` unlinks it, anything else helps."""
    message = update.get('message') if isinstance(update, dict) else None
    chat = (message or {}).get('chat') or {}
    chat_id = chat.get('id')
    if chat.get('type') != 'private' or not isinstance(chat_id, int):   # never groups or channels
        return
    command, _space, argument = str((message or {}).get('text') or '').strip().partition(' ')
    command = command.split('@')[0].lower()
    # until the account is known, answer in the language Telegram says the person uses
    language = clean(str(((message or {}).get('from') or {}).get('language_code') or '')) or settings.LANGUAGE_CODE
    with translation.override(language):
        _dispatch(chat_id, command, argument.strip())


def _dispatch(chat_id, command, argument):
    if command == '/start' and argument:
        return _link(chat_id, argument)
    if command in ('/stop', '/parar'):
        return _unlink(chat_id)
    _reply(chat_id, _('Olá! Para receber os avisos do %(brand)s aqui, abra Notificações no app e toque em "Ligar o Telegram".')
           % {'brand': settings.STUDIO_NAME or 'Muscle Track'})


def _link(chat_id, code):
    from . import notifications   # the channel switch (and the file's history line) live there

    now = timezone.now()
    pref = NotificationPreference.objects.select_related('user').filter(
        telegram_code=code, telegram_code_expires__gt=now, user__is_active=True).first()
    if pref is None:
        return _reply(chat_id, _('Esse código não vale mais. Volte ao app, abra Notificações e toque em "Ligar o Telegram" de novo.'))
    for other in NotificationPreference.objects.filter(telegram_chat_id=chat_id).exclude(pk=pref.pk).select_related('user'):
        notifications.set_channel(other.user, getattr(other.user, 'person', None), other, 'telegram', False)   # one chat, one account
    pref.telegram_chat_id = chat_id
    pref.telegram_code, pref.telegram_code_expires = '', None
    pref.save(update_fields=['telegram_chat_id', 'telegram_code', 'telegram_code_expires'])
    notifications.set_channel(pref.user, getattr(pref.user, 'person', None), pref, 'telegram', True)
    with translation.override(language_for(pref.user)):
        _reply(chat_id, _('Pronto! Você vai receber aqui os avisos do %(brand)s. Para parar, envie /stop ou desligue no app.')
               % {'brand': settings.STUDIO_NAME or 'Muscle Track'})


def _unlink(chat_id):
    from . import notifications

    pref = NotificationPreference.objects.select_related('user').filter(telegram_chat_id=chat_id).first()
    if pref is None:
        return _reply(chat_id, _('Este chat não está ligado a nenhuma conta.'))
    with translation.override(language_for(pref.user)):
        notifications.set_channel(pref.user, getattr(pref.user, 'person', None), pref, 'telegram', False)
        _reply(chat_id, _('Avisos desligados. Para voltar, toque em "Ligar o Telegram" nas Notificações do app.'))
