"""Repeating classes (like a calendar's "custom recurrence"): which dates, how it ends, and creating the series.

A series is a set of ScheduleSlot rows (one per weekday) sharing time, duration, interval and period; the
calendar generates the concrete classes lazily (core/agenda.ensure_appointments). "Ends after N classes" is
turned into an end date when the series is created, so the slots only ever store a date."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .agenda import on_week_cycle
from .models import Appointment, ScheduleSlot

MAX_INTERVAL = 8
MAX_OCCURRENCES = 200
# "Never ends" is fine (classes are generated week by week); a fixed end may not run past this
MAX_SPAN_DAYS = 3 * 366


def occurrence_dates(starts_on, weekdays, interval_weeks=1, limit=None, until=None):
    """Yield the dates of a series in order (at most `limit`, up to `until`; one of them must be given)."""
    if limit is None and until is None:
        raise ValueError('limit or until is required')
    weekdays = set(weekdays)
    day, count = starts_on, 0
    while until is None or day <= until:
        if day.weekday() in weekdays and on_week_cycle(day, starts_on, interval_weeks):
            yield day
            count += 1
            if limit is not None and count >= limit:
                return
        day += timedelta(days=1)
        if (day - starts_on).days > MAX_SPAN_DAYS:
            return


def end_date(starts_on, weekdays, interval_weeks, ends, ends_on=None, ends_after=None):
    """The last day of the series: None (never), the given date, or the date of the N-th class."""
    if ends == 'on':
        return ends_on
    if ends == 'after':
        last = None
        for last in occurrence_dates(starts_on, weekdays, interval_weeks, limit=ends_after):
            pass
        return last
    return None


def describe(weekdays, start_time, interval_weeks, valid_until=None):
    names = {value: label for value, label in ScheduleSlot.WEEKDAYS}
    days = ', '.join(names[d][:3].lower() for d in sorted(weekdays))
    text = f'{days} às {start_time:%H:%M}'
    text += ', toda semana' if interval_weeks == 1 else f', a cada {interval_weeks} semanas'
    if valid_until:
        text += f', até {valid_until:%d/%m/%Y}'
    return text


@transaction.atomic
def create_series(person, weekdays, start_time, duration_minutes, starts_on, interval_weeks=1, valid_until=None):
    """One slot per weekday. A slot that already exists for the same weekday and time is updated and reactivated.

    Returns (slots, number of slots created)."""
    slots, created_count = [], 0
    for weekday in sorted(set(weekdays)):
        slot, created = ScheduleSlot.objects.get_or_create(
            person=person, weekday=weekday, start_time=start_time,
            defaults={'duration_minutes': duration_minutes, 'valid_from': starts_on, 'valid_until': valid_until,
                      'interval_weeks': interval_weeks})
        if not created:
            slot.duration_minutes = duration_minutes
            slot.valid_from = starts_on
            slot.valid_until = valid_until
            slot.interval_weeks = interval_weeks
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
