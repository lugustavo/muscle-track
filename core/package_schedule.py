"""Fill the agenda from a package: the trainer picks weekdays, first day and time; the app counts the classes the
package still needs and creates all of them at once (so the calendar and the balance show the whole package).

- `build_preview` lists the dates (the end date follows from the number of classes and the weekdays);
- `fill_series` creates the fixed times (ScheduleSlot, tied to the package) and every class;
- `top_up` marks more classes at the end of the package's series, e.g. after a class was cancelled without discount.
Days the trainer unticks in the preview (holidays, trips) are skipped and replaced by the next date; they are kept in
`ScheduleSlot.skipped_dates` so the calendar never generates them."""
from datetime import time, timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import holidays
from .agenda import at, on_week_cycle
from .models import Appointment, ScheduleSlot
from .recurrence import occurrence_dates

MAX_SKIPS = 60
HORIZON_DAYS = 3 * 366   # how far ahead holidays are looked up
DEAD = (Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED)


class FillError(Exception):
    """Something that cannot be done; the message is for the trainer."""


def _live_starts(person, since):
    """When the student already has a class that counts, from `since` on."""
    return set(person.appointments.filter(starts_at__gte=at(since, time.min)).exclude(status__in=DEAD)
               .values_list('starts_at', flat=True))


def previous_pattern(person):
    """The student's latest fixed times as one pattern to continue (a renewal): weekdays, time, duration, interval.

    The latest series is the one that ends last (no end = running now); its slots share package and end date. None when
    the student never had one, or when its times differ (then the trainer picks them by hand)."""
    slots = list(person.slots.all())
    if not slots:
        return None
    key = lambda s: (s.valid_until is None, s.valid_until or s.valid_from, s.valid_from)
    best = max(slots, key=key)
    group = [s for s in slots if s.package_id == best.package_id and s.valid_until == best.valid_until]
    if len({(s.start_time, s.duration_minutes, s.interval_weeks) for s in group}) != 1:
        return None
    weekdays = sorted({s.weekday for s in group})
    names = dict(ScheduleSlot.WEEKDAYS)
    days = [names[d][:3].lower() for d in weekdays]
    return {'weekdays': weekdays, 'start_time': best.start_time, 'duration': best.duration_minutes,
            'interval': best.interval_weeks,
            'label': _('%(days)s às %(time)s') % {'days': _join_days(days), 'time': f'{best.start_time:%H:%M}'}}


def _join_days(days):
    """'seg, ter e qua' (a single day stays as it is)."""
    if len(days) > 1:
        return _('%(first)s e %(last)s') % {'first': ', '.join(days[:-1]), 'last': days[-1]}
    return days[0]


def continue_from(person, today=None):
    """First day a continued schedule can start: the day after the student's last class that still counts (never in
    the past)."""
    today = today or timezone.localdate()
    last = person.appointments.exclude(status__in=DEAD).order_by('-starts_at').first()
    if last is None:
        return today
    return max(today, timezone.localtime(last.starts_at).date() + timedelta(days=1))


def _deadline(plan, first_day):
    """Last day the package covers: its end date, or (not started yet) the first class + the deadline."""
    if plan['ends_on']:
        return plan['ends_on']
    package = plan['package']
    if plan['state'] == 'not_started' and package.valid_days:
        return first_day + timedelta(days=package.valid_days + package.extension_days)
    return None


def build_preview(person, plan, weekdays, start, start_time, duration=60, interval=1, skip=(), keep=()):
    """The dates a fill would create.

    Returns rows (every date looked at, in order: state 'new', 'skipped' by the trainer, 'holiday' = a national or own
    holiday (skipped by itself unless the trainer put the day in `keep`) or 'exists' = the student already has a class
    then), the new dates, the last day, and warnings (past the package's end, overlap with another student's class)."""
    wanted = plan['to_schedule']
    skip, keep = set(skip), set(keep)
    live = _live_starts(person, start)
    off = holidays.between(start, start + timedelta(days=HORIZON_DAYS))
    rows, new = [], []
    if wanted > 0 and weekdays:
        for day in occurrence_dates(start, set(weekdays), interval, limit=wanted + len(skip) + len(live) + len(off)):
            starts = at(day, start_time)
            if day in skip:
                rows.append({'date': day, 'state': 'skipped'})
            elif day in off and day not in keep:
                rows.append({'date': day, 'state': 'holiday', 'holiday': off[day]})
            elif starts in live:
                rows.append({'date': day, 'state': 'exists'})
            else:
                rows.append({'date': day, 'state': 'new', **({'holiday': off[day], 'kept': True} if day in off else {})})
                new.append(day)
                if len(new) == wanted:
                    break
    last = new[-1] if new else None
    deadline = _deadline(plan, new[0]) if new else None
    if new:
        end = timedelta(minutes=duration)
        others = list(Appointment.objects.filter(
            starts_at__gte=at(new[0], time.min) - timedelta(days=1), starts_at__lt=at(last + timedelta(days=1), time.min),
        ).exclude(person=person).exclude(status__in=DEAD).select_related('person'))
        for row in rows:
            if row['state'] != 'new':
                continue
            starts = at(row['date'], start_time)
            row['overlap'] = sorted({a.person.person_name for a in others
                                     if a.starts_at < starts + end and a.ends_at > starts})
            row['beyond'] = bool(deadline and row['date'] > deadline)
    return {
        'rows': rows, 'new_dates': new, 'last': last, 'count': len(new), 'wanted': wanted, 'deadline': deadline,
        'beyond': sum(1 for r in rows if r.get('beyond')), 'overlaps': sum(1 for r in rows if r.get('overlap')),
        'short': len(new) < wanted,
    }


@transaction.atomic
def fill_series(person, plan, weekdays, start, start_time, duration=60, interval=1, skip=(), keep=(), now=None):
    """Create the fixed times and all the classes the package still needs. Returns {'created', 'last', 'slots'}."""
    preview = build_preview(person, plan, weekdays, start, start_time, duration, interval, skip, keep)
    if not preview['new_dates']:
        raise FillError(_('Não há aulas a marcar: o pacote já está completo ou todas as datas foram puladas.'))
    first, last = preview['new_dates'][0], preview['last']
    package = plan['package']
    skipped = sorted(r['date'].isoformat() for r in preview['rows']
                     if r['state'] in ('skipped', 'holiday') and first <= r['date'] <= last)
    labels = dict(ScheduleSlot.WEEKDAYS)
    slots = {}
    for weekday in sorted({d.weekday() for d in preview['new_dates']}):
        slot = ScheduleSlot.objects.filter(person=person, weekday=weekday, start_time=start_time).first()
        if slot and slot.is_active and (slot.valid_until is None or slot.valid_until >= first):
            raise FillError(_('Já existe um horário fixo na %(day)s às %(time)s: '
                              'edite-o ou remova-o antes de preencher de novo.')
                            % {'day': str(labels[weekday]).lower(), 'time': f'{start_time:%H:%M}'})
        values = dict(duration_minutes=duration, valid_from=start, valid_until=last, interval_weeks=interval,
                      package=package, skipped_dates=skipped, is_active=True)
        if slot:
            for name, value in values.items():
                setattr(slot, name, value)
            slot.save()
        else:
            slot = ScheduleSlot.objects.create(person=person, weekday=weekday, start_time=start_time, **values)
        slots[weekday] = slot
    Appointment.objects.bulk_create([
        Appointment(person=person, starts_at=at(day, start_time), duration_minutes=duration, slot=slots[day.weekday()])
        for day in preview['new_dates']])
    return {'created': len(preview['new_dates']), 'last': last, 'slots': list(slots.values()), 'preview': preview}


@transaction.atomic
def top_up(person, plan, now=None):
    """Mark the classes the package is missing at the end of its series (same days and times). Returns
    {'created', 'last'}."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    wanted = plan['to_schedule']
    if wanted <= 0:
        raise FillError(_('Todas as aulas que restam do pacote já estão marcadas.'))
    slots = list(ScheduleSlot.objects.filter(person=person, package=plan['package'], is_active=True))
    if not slots:
        raise FillError(_('Este pacote ainda não tem horário fixo: use "Preencher a agenda".'))
    last_live = person.appointments.exclude(status__in=DEAD).order_by('-starts_at').first()
    start = today + timedelta(days=1)
    if last_live is not None:
        start = max(start, timezone.localtime(last_live.starts_at).date() + timedelta(days=1))
    live = _live_starts(person, start)
    off = holidays.between(start, start + timedelta(days=HORIZON_DAYS))
    new, day, limit = [], start, start + timedelta(days=HORIZON_DAYS)
    while len(new) < wanted and day <= limit:
        for slot in sorted(slots, key=lambda s: s.start_time):
            if slot.weekday != day.weekday() or not on_week_cycle(day, slot.valid_from, slot.interval_weeks):
                continue
            if day in off or day.isoformat() in (slot.skipped_dates or []) or at(day, slot.start_time) in live:
                continue
            new.append((day, slot))
            if len(new) == wanted:
                break
        day += timedelta(days=1)
    if not new:
        raise FillError(_('Não achei datas livres nos horários do pacote.'))
    Appointment.objects.bulk_create([
        Appointment(person=person, starts_at=at(d, s.start_time), duration_minutes=s.duration_minutes, slot=s)
        for d, s in new])
    for slot in slots:   # the series now runs to its new last class
        latest = max((d for d, s in new if s.pk == slot.pk), default=None)
        if latest and slot.valid_until is not None and slot.valid_until < latest:
            slot.valid_until = latest
            slot.save(update_fields=['valid_until'])
    return {'created': len(new), 'last': new[-1][0]}
