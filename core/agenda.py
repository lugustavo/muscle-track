"""Weekly calendar: generation of classes from fixed slots, conflicts and attendance."""
from datetime import date, datetime, timedelta

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
