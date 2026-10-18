"""Repeating classes (like a calendar's "custom recurrence"): which dates, how it ends, and creating the series.

A series is a set of ScheduleSlot rows (one per weekday) sharing time, duration, interval and period; the
calendar generates the concrete classes lazily (core/agenda.ensure_appointments). "Ends after N classes" is
turned into an end date when the series is created, so the slots only ever store a date."""
import calendar
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import dateformat, timezone
from django.utils.text import capfirst
from django.utils.translation import get_language, gettext

from .agenda import at, monthly_match, on_week_cycle, slot_matches
from .i18n import short_date
from .models import Appointment, ScheduleSlot

MAX_INTERVAL = 8
MAX_OCCURRENCES = 200
# "Never ends" is fine (classes are generated week by week); a fixed end may not run past this
MAX_SPAN_DAYS = 3 * 366


def occurrence_dates(starts_on, weekdays, interval_weeks=1, limit=None, until=None, month_rule='', interval_months=1):
    """Yield the dates of a series in order (at most `limit`, up to `until`; one of them must be given).

    Weekly by default; with `month_rule` ('day', 'nth', 'last', see agenda.monthly_match) a monthly series anchored on
    `starts_on`, every `interval_months` months."""
    if limit is None and until is None:
        raise ValueError('limit or until is required')
    weekdays = set(weekdays)
    day, count = starts_on, 0
    while until is None or day <= until:
        if month_rule:
            hit = monthly_match(day, starts_on, month_rule, interval_months)
        else:
            hit = day.weekday() in weekdays and on_week_cycle(day, starts_on, interval_weeks)
        if hit:
            yield day
            count += 1
            if limit is not None and count >= limit:
                return
        day += timedelta(days=1)
        if (day - starts_on).days > MAX_SPAN_DAYS:
            return


def end_date(starts_on, weekdays, interval_weeks, ends, ends_on=None, ends_after=None, month_rule='', interval_months=1):
    """The last day of the series: None (never), the given date, or the date of the N-th class."""
    if ends == 'on':
        return ends_on
    if ends == 'after':
        last = None
        for last in occurrence_dates(starts_on, weekdays, interval_weeks, limit=ends_after, month_rule=month_rule,
                                     interval_months=interval_months):
            pass
        return last
    return None


def month_rule_options(day):
    """The monthly rules a class on `day` can start, as {rule: text}: the same day of the month, the same weekday of
    the same week (not for a 5th one: that is the last), and the last such weekday (only when `day` is it)."""
    weekday = dateformat.format(day, 'l')   # as the language writes it: Portuguese in lower case, English capitalized
    if get_language().startswith('pt'):
        weekday = weekday.lower()
    options = {'day': gettext('Todo dia %(day)d do mês') % {'day': day.day}}
    nth = (day.day - 1) // 7 + 1
    if nth <= 4:
        options['nth'] = capfirst(gettext('%(weekday)s da %(n)dª semana do mês') % {'weekday': weekday, 'n': nth})
    if day.day + 7 > calendar.monthrange(day.year, day.month)[1]:
        options['last'] = capfirst(gettext('%(weekday)s da última semana do mês') % {'weekday': weekday})
    return options


def monthly_phrase(rule, first_day, interval_months=1):
    """"todo dia 15 do mês" / "toda ter da 2ª semana do mês", plus "a cada N meses" when it is not every month."""
    text = month_rule_options(first_day).get(rule, '') or gettext('Todo mês')
    if interval_months > 1:
        text += ', ' + gettext('a cada %(n)d meses') % {'n': interval_months}
    return text


def describe(weekdays, start_time, interval_weeks, valid_until=None, month_rule='', interval_months=1, starts_on=None):
    names = {value: str(label) for value, label in ScheduleSlot.WEEKDAYS}
    if month_rule and starts_on is not None:
        parts = [gettext('%(rule)s às %(time)s') % {'rule': monthly_phrase(month_rule, starts_on, interval_months),
                                                    'time': f'{start_time:%H:%M}'}]
    else:
        days = ', '.join(names[d][:3].lower() for d in sorted(weekdays))
        parts = [gettext('%(days)s às %(time)s') % {'days': days, 'time': f'{start_time:%H:%M}'}]
        parts.append(gettext('toda semana') if interval_weeks == 1
                     else gettext('a cada %(n)d semanas') % {'n': interval_weeks})
    if valid_until:
        parts.append(gettext('até %(date)s') % {'date': short_date(valid_until)})
    return ', '.join(parts)


def describe_slot(slot):
    """The same words for a slot already saved (the first class is its `valid_from`)."""
    return describe([slot.weekday], slot.start_time, slot.interval_weeks, slot.valid_until, slot.month_rule,
                    slot.interval_months, slot.valid_from)


@transaction.atomic
def create_series(person, weekdays, start_time, duration_minutes, starts_on, interval_weeks=1, valid_until=None,
                  month_rule='', interval_months=1):
    """One slot per weekday. A slot that already exists for the same weekday and time is updated and reactivated.

    A monthly series is a single slot on the weekday of its first class; one that is still running with the same
    weekday and time is refused (it would be overwritten). Returns (slots, number of slots created)."""
    if month_rule:
        weekdays = [starts_on.weekday()]
    slots, created_count = [], 0
    for weekday in sorted(set(weekdays)):
        if month_rule:
            running = ScheduleSlot.objects.filter(person=person, weekday=weekday, start_time=start_time,
                                                  month_rule=month_rule, is_active=True).filter(
                Q(valid_until__isnull=True) | Q(valid_until__gte=timezone.localdate())).exists()
            if running:
                raise SeriesError(gettext('Este aluno já tem um horário mensal nesse dia da semana e horário: '
                                          'edite ou remova o que existe.'))
        slot, created = ScheduleSlot.objects.get_or_create(
            person=person, weekday=weekday, start_time=start_time, month_rule=month_rule,
            defaults={'duration_minutes': duration_minutes, 'valid_from': starts_on, 'valid_until': valid_until,
                      'interval_weeks': interval_weeks, 'interval_months': interval_months})
        if not created:
            slot.duration_minutes = duration_minutes
            slot.valid_from = starts_on
            slot.valid_until = valid_until
            slot.interval_weeks = interval_weeks
            slot.interval_months = interval_months
            slot.is_active = True
            slot.save()
        else:
            created_count += 1
        slots.append(slot)
    return slots, created_count


@transaction.atomic
def end_series_from(appointment, now=None):
    """"This class and the following ones": the slot stops the day before, and the scheduled classes of the
    series from this one on leave the calendar. Classes already marked (attended, missed...) stay."""
    slot = appointment.slot
    if slot is None:
        return None
    local_day = timezone.localtime(appointment.starts_at).date()
    slot.valid_until = local_day - timedelta(days=1)
    slot.save(update_fields=['valid_until'])
    removed = slot.appointments.filter(
        status=Appointment.Status.SCHEDULED, starts_at__gte=appointment.starts_at).delete()[0]
    return slot, removed


class SeriesError(Exception):
    """A change to a fixed time that cannot be done; the message is for the trainer."""


def _untouched(slot, from_day, now):
    """The future scheduled classes a change may move: generated by the slot and never edited by hand (no move, no
    request from the student, same time and length as the slot)."""
    since = max(now, at(from_day, datetime.min.time()))
    found = slot.appointments.filter(
        status=Appointment.Status.SCHEDULED, starts_at__gte=since, rescheduled_from__isnull=True,
        duration_minutes=slot.duration_minutes, requests__isnull=True)
    return [a for a in found if timezone.localtime(a.starts_at).time().replace(second=0, microsecond=0) == slot.start_time]


@transaction.atomic
def edit_series(slot, weekday, start_time, duration_minutes, valid_until, from_day, now=None):
    """Change a fixed time from `from_day` on, like a calendar's "this and the following".

    Same day and time: the slot is updated in place. A new day or time: the slot stops the day before and a new one
    starts at its first date (when nothing happened before `from_day` the slot is simply changed), so past classes
    keep their old time. Only future classes nobody touched are replaced; the calendar generates the new ones.
    Returns (current slot, number of classes that left the calendar)."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    from_day = max(from_day, today)
    if slot.month_rule:
        weekday = slot.weekday   # a monthly rule hangs on the weekday of its first class: only the time and the end change
    if valid_until and valid_until < from_day:
        raise SeriesError(gettext('A data final não pode ser antes do início da mudança.'))
    if slot.valid_until and slot.valid_until < from_day:
        raise SeriesError(gettext('Este horário já terminou; crie um novo horário fixo.'))
    same_place = weekday == slot.weekday and start_time == slot.start_time
    if not same_place and ScheduleSlot.objects.filter(person=slot.person, weekday=weekday, start_time=start_time).exists():
        raise SeriesError(gettext('Este aluno já tem um horário fixo nesse dia e horário (talvez encerrado: remova-o antes).'))
    old = list(_untouched(slot, from_day, now))
    removed = 0
    if same_place:
        for a in old:
            if valid_until and timezone.localtime(a.starts_at).date() > valid_until:
                a.delete()
                removed += 1
            elif a.duration_minutes != duration_minutes:
                Appointment.objects.filter(pk=a.pk).update(duration_minutes=duration_minutes)
        slot.duration_minutes, slot.valid_until = duration_minutes, valid_until
        slot.save()
        return slot, removed
    for a in old:   # the new day/time replaces them; the calendar creates the new ones
        a.delete()
        removed += 1
    if slot.valid_from >= from_day:   # nothing before: change it in place
        slot.weekday, slot.start_time, slot.duration_minutes, slot.valid_until = weekday, start_time, duration_minutes, valid_until
        slot.save()
        return slot, removed
    first = from_day   # first date of the new day that falls on the old cycle
    if slot.month_rule:
        for _ in range(31 * 12 * slot.interval_months + 31):
            if slot_matches(slot, first):
                break
            first += timedelta(days=1)
        weekday = first.weekday()
    else:
        for _ in range(7 * slot.interval_weeks):
            if first.weekday() == weekday and on_week_cycle(first, slot.valid_from, slot.interval_weeks):
                break
            first += timedelta(days=1)
    if valid_until and valid_until < first:
        raise SeriesError(gettext('A data final fica antes da primeira aula no novo dia.'))
    slot.valid_until = from_day - timedelta(days=1)
    slot.save(update_fields=['valid_until'])
    new = ScheduleSlot.objects.create(
        person=slot.person, weekday=weekday, start_time=start_time, duration_minutes=duration_minutes,
        valid_from=first, valid_until=valid_until, interval_weeks=slot.interval_weeks, month_rule=slot.month_rule,
        interval_months=slot.interval_months, package=slot.package)
    return new, removed
