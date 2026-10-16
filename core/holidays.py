"""Holidays that "Preencher a agenda" skips by itself: the national ones of the trainer's country plus her own days.

The national list is computed (the fixed days and the ones that follow Easter), so nothing needs updating each year:
`HOLIDAYS_REGION` = 'PT' (default), 'BR' or '' (none). Local holidays (the city's patron saint, a day the studio is
closed) are `Holiday` rows the trainer adds in Agenda → Feriados."""
from datetime import date, timedelta

from django.conf import settings

from .models import Holiday


def easter(year):
    """Easter Sunday (the usual Gregorian computus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _portugal(year, paschal):
    easter_day = paschal
    return {
        date(year, 1, 1): 'Ano Novo',
        easter_day - timedelta(days=2): 'Sexta-feira Santa',
        easter_day: 'Páscoa',
        date(year, 4, 25): 'Dia da Liberdade',
        date(year, 5, 1): 'Dia do Trabalhador',
        easter_day + timedelta(days=60): 'Corpo de Deus',
        date(year, 6, 10): 'Dia de Portugal',
        date(year, 8, 15): 'Assunção de Nossa Senhora',
        date(year, 10, 5): 'Implantação da República',
        date(year, 11, 1): 'Todos os Santos',
        date(year, 12, 1): 'Restauração da Independência',
        date(year, 12, 8): 'Imaculada Conceição',
        date(year, 12, 25): 'Natal',
    }


def _brazil(year, paschal):
    easter_day = paschal
    return {
        date(year, 1, 1): 'Confraternização Universal',
        easter_day - timedelta(days=48): 'Carnaval (segunda)',
        easter_day - timedelta(days=47): 'Carnaval (terça)',
        easter_day - timedelta(days=2): 'Sexta-feira Santa',
        date(year, 4, 21): 'Tiradentes',
        date(year, 5, 1): 'Dia do Trabalho',
        easter_day + timedelta(days=60): 'Corpus Christi',
        date(year, 9, 7): 'Independência',
        date(year, 10, 12): 'Nossa Senhora Aparecida',
        date(year, 11, 2): 'Finados',
        date(year, 11, 15): 'Proclamação da República',
        date(year, 11, 20): 'Consciência Negra',
        date(year, 12, 25): 'Natal',
    }


REGIONS = {'PT': _portugal, 'BR': _brazil}


def national(year, region=None):
    """{date: name} of the national holidays of `year` ({} when no region is set)."""
    region = (settings.HOLIDAYS_REGION if region is None else region).upper()
    builder = REGIONS.get(region)
    return builder(year, easter(year)) if builder else {}


def between(start, end):
    """{date: name} of every holiday (national + the trainer's own) from `start` to `end`, both included. The trainer's
    own name wins when both fall on the same day."""
    found = {}
    for year in range(start.year, end.year + 1):
        found.update({day: name for day, name in national(year).items() if start <= day <= end})
    found.update({h.date: h.name for h in Holiday.objects.filter(date__gte=start, date__lte=end)})
    return found
