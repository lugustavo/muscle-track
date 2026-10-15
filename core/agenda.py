"""Calendar: generation of classes from fixed slots, the week / day / month / list views, conflicts and attendance."""
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from .models import Appointment, ScheduleSlot

# How far back opening the calendar still fills in classes, so last week's attendance can be marked
BACKFILL_DAYS = 14


def week_start(day):
    return day - timedelta(days=day.weekday())


def parse_week(value, today=None):
    """Monday of the week named by 'YYYY-MM-DD'; anything else -> the current week."""
    today = today or timezone.localdate()
    try:
        return week_start(date.fromisoformat(value))
    except (ValueError, TypeError):
        return week_start(today)


def on_week_cycle(day, valid_from, interval_weeks):
    """True when `day` falls in a week of the cycle that starts at the week of `valid_from` (1 = every week)."""
    weeks = (week_start(day) - week_start(valid_from)).days // 7
    return weeks >= 0 and weeks % max(interval_weeks, 1) == 0


def at(day, clock):
    return timezone.make_aware(datetime.combine(day, clock))


def ensure_appointments(start, end, today=None):
    """Create the missing classes of every active slot for dates in [start, end]. Idempotent.

    Only for students with access, never before the slot's `valid_from`, and not further back
    than BACKFILL_DAYS. Returns how many were created."""
    today = today or timezone.localdate()
    start = max(start, today - timedelta(days=BACKFILL_DAYS))
    if start > end:
        return 0
    slots = list(ScheduleSlot.objects.filter(is_active=True, person__is_active=True))
    if not slots:
        return 0
    existing = set(Appointment.objects.filter(
        starts_at__gte=at(start, datetime.min.time()), starts_at__lt=at(end + timedelta(days=1), datetime.min.time()),
    ).values_list('person_id', 'starts_at'))
    new = []
    day = start
    while day <= end:
        for slot in slots:
            if slot.weekday != day.weekday() or day < slot.valid_from:
                continue
            if slot.valid_until and day > slot.valid_until:
                continue
            if not on_week_cycle(day, slot.valid_from, slot.interval_weeks):
                continue
            starts = at(day, slot.start_time)
            if (slot.person_id, starts) not in existing:
                new.append(Appointment(person_id=slot.person_id, starts_at=starts, slot=slot,
                                       duration_minutes=slot.duration_minutes))
        day += timedelta(days=1)
    if new:
        Appointment.objects.bulk_create(new, ignore_conflicts=True)
    return len(new)


def conflicting_ids(appointments):
    """pks of classes that overlap another (cancelled and rescheduled ones do not count)."""
    live = sorted((a for a in appointments if a.status not in (Appointment.Status.CANCELLED,
                                                                 Appointment.Status.RESCHEDULED)),
                  key=lambda a: a.starts_at)
    flagged = set()
    previous = None
    for a in live:
        if previous is not None and a.starts_at < previous.ends_at:
            flagged.update((a.pk, previous.pk))
        if previous is None or a.ends_at > previous.ends_at:
            previous = a
    return flagged


def overlapping(appointment):
    """Other live classes that overlap `appointment` (cancelled and rescheduled ones do not count)."""
    clash = Appointment.objects.filter(
        starts_at__lt=appointment.ends_at, starts_at__gte=appointment.starts_at - timedelta(minutes=480)
    ).exclude(pk=appointment.pk).exclude(
        status__in=(Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED)).select_related('person')
    return [a for a in clash if a.ends_at > appointment.starts_at]


def overlap_names(appointment):
    return sorted({a.person.person_name for a in overlapping(appointment)})


def live_class_at(person, starts, exclude=None):
    """Another class of this student at exactly `starts` that still counts (not cancelled, not rescheduled)."""
    queryset = Appointment.objects.filter(person=person, starts_at=starts).exclude(
        status__in=(Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED))
    if exclude is not None:
        queryset = queryset.exclude(pk=exclude.pk)
    return queryset.first()


class MoveError(Exception):
    """A move that cannot happen; the message is meant for the trainer."""


def move_appointment(appointment, starts, duration_minutes=None, kind=None, notes=None):
    """Reschedule: a new class at `starts`, the original becomes "Remarcada" and points to it (so it can be undone).

    Returns the new class. Raises MoveError when the student already has a class at that time."""
    if starts == appointment.starts_at:
        raise MoveError('A aula já está neste horário.')
    with transaction.atomic():
        if live_class_at(appointment.person, starts) is not None:
            raise MoveError('Este aluno já tem aula neste horário.')
        new = Appointment.objects.create(
            person=appointment.person, starts_at=starts,
            duration_minutes=duration_minutes or appointment.duration_minutes,
            kind=kind or appointment.kind, notes=appointment.notes if notes is None else notes)
        appointment.status = Appointment.Status.RESCHEDULED
        appointment.status_changed_at = timezone.now()
        appointment.rescheduled_to = new
        appointment.save(update_fields=['status', 'status_changed_at', 'rescheduled_to'])
    return new


def undo_move(new):
    """Take back a move: drop the new class and bring the original back as "Marcada". Returns the original.

    Only while the new class is still untouched (marked, no attendance recorded)."""
    with transaction.atomic():
        original = Appointment.objects.select_for_update().filter(rescheduled_to=new).first()
        if original is None:
            raise MoveError('Esta aula não veio de uma remarcação.')
        if new.status != Appointment.Status.SCHEDULED:
            raise MoveError('A aula nova já foi alterada; não dá mais para desfazer.')
        if live_class_at(original.person, original.starts_at, exclude=new) is not None:
            raise MoveError('O aluno já tem outra aula no horário original; não dá mais para desfazer.')
        new.delete()  # the link on the database row is cleared by SET_NULL
        original.rescheduled_to = None
        original.status = Appointment.Status.SCHEDULED
        original.status_changed_at = None
        original.save(update_fields=['status', 'status_changed_at'])
    return original


def attendance_stats(person, days=30, today=None):
    """Attended / missed in the last `days` days and the attendance rate (None without data)."""
    today = today or timezone.localdate()
    since = at(today - timedelta(days=days), datetime.min.time())
    counts = {Appointment.Status.ATTENDED: 0, Appointment.Status.MISSED: 0}
    for status in person.appointments.filter(starts_at__gte=since, starts_at__lt=timezone.now(),
                                             status__in=counts).values_list('status', flat=True):
        counts[status] += 1
    attended, missed = counts[Appointment.Status.ATTENDED], counts[Appointment.Status.MISSED]
    total = attended + missed
    return {'attended': attended, 'missed': missed, 'rate': round(100 * attended / total) if total else None}


# ---- Views of the calendar (week is the default) -------------------------------------------------------

VIEWS = ('semana', 'dia', 'mes', 'lista')
DEFAULT_VIEW = 'semana'
LIST_DAYS = 14


def parse_day(value, today=None):
    """The day named by 'YYYY-MM-DD'; anything else -> today."""
    today = today or timezone.localdate()
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return today


def parse_month(value, today=None):
    """First day of the month named by 'YYYY-MM'; anything else -> the current month."""
    today = today or timezone.localdate()
    try:
        year, month = (int(part) for part in value.split('-'))
        return date(year, month, 1)
    except (ValueError, TypeError, AttributeError):
        return today.replace(day=1)


def add_months(first, months):
    """First day of the month `months` away from `first` (a first-of-month date)."""
    index = first.year * 12 + (first.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def month_range(first):
    """(grid start, grid end) of the month: whole weeks, Monday to Sunday."""
    last = add_months(first, 1) - timedelta(days=1)
    return week_start(first), week_start(last) + timedelta(days=6)


def view_range(view, params, today):
    """(view, first day, last day, pivot) for a view and the request's parameters."""
    if view == 'mes':
        first = parse_month(params.get('mes'), today)
        start, end = month_range(first)
        return view, start, end, first
    if view == 'dia':
        day = parse_day(params.get('dia'), today)
        return view, day, day, day
    if view == 'lista':
        day = parse_day(params.get('inicio'), today)
        return view, day, day + timedelta(days=LIST_DAYS - 1), day
    monday = parse_week(params.get('semana'), today)
    return 'semana', monday, monday + timedelta(days=6), monday


def month_cells(first, by_day, today, conflicts):
    """The month grid: a list of weeks, each a list of 7 day cells with counts (for the compact view)."""
    start, end = month_range(first)
    weeks, day = [], start
    while day <= end:
        row = []
        for _ in range(7):
            items = by_day.get(day, [])
            live = [a for a in items if a.status not in (Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED)]
            row.append({
                'date': day, 'in_month': day.month == first.month, 'is_today': day == today, 'items': items,
                'live': len(live), 'total': len(items),
                'attended': sum(1 for a in items if a.status == Appointment.Status.ATTENDED),
                'missed': sum(1 for a in items if a.status == Appointment.Status.MISSED),
                'conflict': any(a.pk in conflicts for a in items),
            })
            day += timedelta(days=1)
        weeks.append(row)
    return weeks
