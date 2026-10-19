"""The trainer is told when the automatic backups (backup/backup.sh) are not happening.

The backup service writes BACKUP_STATUS_FILE after every run; this module only reads it. Nothing is shown when no file is
configured (a computer running the app without Docker has no backup service) or while everything is fine."""
import json
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _


def _parse(value):
    try:
        moment = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S%z')
    except (TypeError, ValueError):
        return None
    return moment


def problem(now=None):
    """None when backups are fine (or not configured); otherwise {'level': 'bad'|'warn', 'text': str}."""
    path = getattr(settings, 'BACKUP_STATUS_FILE', '')
    if not path:
        return None
    now = now or timezone.now()
    try:
        with open(path, encoding='utf-8') as handle:
            status = json.load(handle)
    except FileNotFoundError:
        return {'level': 'warn', 'text': _('Ainda não há nenhum backup. Se isso continuar, confira se o serviço "backup" está rodando (docker compose ps).')}
    except (OSError, ValueError):
        return {'level': 'warn', 'text': _('Não consegui ler o estado dos backups. Confira o serviço "backup" (docker compose logs backup).')}

    last_ok = _parse(status.get('last_ok'))
    max_age = timedelta(hours=settings.BACKUP_MAX_AGE_HOURS)
    if status.get('ok') is False:
        when = last_ok.astimezone(timezone.get_current_timezone()).strftime('%d/%m %H:%M') if last_ok else None
        text = _('O último backup falhou.') + ' ' + (
            _('O último que deu certo foi em %(when)s.') % {'when': when} if when else _('Ainda não houve nenhum backup bem-sucedido.'))
        return {'level': 'bad', 'text': text + ' ' + _('Veja: docker compose logs backup')}
    if last_ok is None or now - last_ok > max_age:
        return {'level': 'bad', 'text': _('O último backup tem mais de %(hours)s horas. Confira o serviço "backup" (docker compose logs backup).') % {'hours': settings.BACKUP_MAX_AGE_HOURS}}
    if status.get('offsite') == 'failed':
        return {'level': 'warn', 'text': _('O backup foi feito, mas a cópia para fora do servidor falhou. Veja: docker compose logs backup')}
    return None
