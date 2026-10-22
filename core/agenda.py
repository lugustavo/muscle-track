"""Calendar: generation of classes from fixed slots, the week / day / month / list views, conflicts and attendance."""
import calendar
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext

from .models import Appointment, AppointmentRequest, ScheduleSlot, WorkingHours

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


def on_month_cycle(day, valid_from, interval_months):
    """True when `day` falls in a month of the cycle that starts at the month of `valid_from` (1 = every month)."""
    months = (day.year * 12 + day.month) - (valid_from.year * 12 + valid_from.month)
    return months >= 0 and months % max(interval_months, 1) == 0


def monthly_match(day, anchor, rule, interval_months=1):
    """Does `day` belong to the monthly series whose first class is `anchor`?

    'day': the same day of the month (a month without that day, e.g. the 31st, has no class); 'nth': the same weekday
    in the same week of the month (the 2nd Tuesday...); 'last': the last such weekday of the month."""
    if not on_month_cycle(day, anchor, interval_months):
        return False
    if rule == 'day':
        return day.day == anchor.day
    if day.weekday() != anchor.weekday():
        return False
    if rule == 'nth':
        return (day.day - 1) // 7 == (anchor.day - 1) // 7
    if rule == 'last':
        return day.day + 7 > calendar.monthrange(day.year, day.month)[1]
    return False


def slot_matches(slot, day):
    """Is `day` one of the days a fixed time repeats on? (the cycle only: not its dates, not the skipped days)"""
    if slot.month_rule:
        return monthly_match(day, slot.valid_from, slot.month_rule, slot.interval_months)
    return slot.weekday == day.weekday() and on_week_cycle(day, slot.valid_from, slot.interval_weeks)


def slot_on(slot, day):
    """Is there a class of this fixed time on `day`? (inside its dates, weekly or monthly; skipped days aside)"""
    if day < slot.valid_from or (slot.valid_until and day > slot.valid_until):
        return False
    return slot_matches(slot, day)


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
            if not slot_on(slot, day):
                continue
            if slot.skipped_dates and day.isoformat() in slot.skipped_dates:
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
        raise MoveError(gettext('A aula já está neste horário.'))
    with transaction.atomic():
        if live_class_at(appointment.person, starts) is not None:
            raise MoveError(gettext('Este aluno já tem aula neste horário.'))
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
            raise MoveError(gettext('Esta aula não veio de uma remarcação.'))
        if new.status != Appointment.Status.SCHEDULED:
            raise MoveError(gettext('A aula nova já foi alterada; não dá mais para desfazer.'))
        if live_class_at(original.person, original.starts_at, exclude=new) is not None:
            raise MoveError(gettext('O aluno já tem outra aula no horário original; não dá mais para desfazer.'))
        new.delete()  # the link on the database row is cleared by SET_NULL
        original.rescheduled_to = None
        original.status = Appointment.Status.SCHEDULED
        original.status_changed_at = None
        original.save(update_fields=['status', 'status_changed_at'])
    return original


def annotate_history(appointments):
    """Attach what a class went through, for the student's page: `moved_to` (where a rescheduled class went),
    `moved_from` (the class this one replaced) and `by_student` (the student asked for it and it was approved).

    Expects `requests` and `rescheduled_from__requests` prefetched and `rescheduled_to` / `rescheduled_from` selected."""
    Request = AppointmentRequest

    def approved(appointment, kind):
        return any(r.status == Request.Status.APPROVED and r.kind == kind for r in appointment.requests.all())

    for a in appointments:
        a.moved_to = a.rescheduled_to
        try:
            a.moved_from = a.rescheduled_from
        except Appointment.DoesNotExist:
            a.moved_from = None
        if a.status == Appointment.Status.RESCHEDULED:
            a.by_student = approved(a, Request.Kind.RESCHEDULE)
        elif a.status == Appointment.Status.CANCELLED:
            a.by_student = approved(a, Request.Kind.CANCEL)
        else:
            a.by_student = bool(a.moved_from and approved(a.moved_from, Request.Kind.RESCHEDULE))
    return appointments


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

VIEWS = ('semana', 'dia', 'mes', 'lista', 'previsao')
DEFAULT_VIEW = 'semana'
LIST_DAYS = 14
FORECAST_CHOICES = (14, 30, 60)   # how many days the forecast can look ahead (the classes of those days are generated on view)
DEFAULT_FORECAST_DAYS = 30
LIST_DAYS_STUDENT = 60   # the list of one student's classes looks further ahead


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


def view_range(view, params, today, list_days=LIST_DAYS):
    """(view, first day, last day, pivot) for a view and the request's parameters."""
    if view == 'mes':
        first = parse_month(params.get('mes'), today)
        start, end = month_range(first)
        return view, start, end, first
    if view == 'dia':
        day = parse_day(params.get('dia'), today)
        return view, day, day, day
    if view in ('lista', 'previsao'):
        day = parse_day(params.get('inicio'), today)
        return view, day, day + timedelta(days=list_days - 1), day
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


def parse_forecast_days(value):
    """The forecast length asked for (?dias=), one of FORECAST_CHOICES; anything else -> the default."""
    try:
        days = int(value)
    except (ValueError, TypeError):
        return DEFAULT_FORECAST_DAYS
    return days if days in FORECAST_CHOICES else DEFAULT_FORECAST_DAYS


# ---- Working hours (the window the trainer takes classes in) and the forecast ---------------------------

LIVE_OUT = (Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED)


def working_hours():
    """{weekday: (start, end)} of the days the trainer works; an empty dict when nothing is configured (the agenda then
    neither warns about hours nor counts free time)."""
    return {row.weekday: (row.start, row.end) for row in WorkingHours.objects.all()}


def hours_text(window):
    """'06:00–20:00' for a (start, end) window, 'folga' (day off) for None."""
    return f'{window[0]:%H:%M}–{window[1]:%H:%M}' if window else ''


def outside_hours(starts_at, ends_at, hours):
    """Is the class (local time) outside the working window of its day? Always False when no hours are configured."""
    if not hours:
        return False
    begin, finish = timezone.localtime(starts_at), timezone.localtime(ends_at)
    window = hours.get(begin.weekday())
    if window is None or finish.date() != begin.date():
        return True
    return begin.time() < window[0] or finish.time() > window[1]


def _minutes(clock):
    return clock.hour * 60 + clock.minute


def forecast(appointments, start, days, today, hours, with_free=True):
    """The next `days` days from `start` at a glance: classes, booked time and, when working hours are configured,
    the free time of each working day. Returns {'days': [...], 'weeks': [...], 'totals': {...}}."""
    by_day = {}
    for item in appointments:
        if item.status in LIVE_OUT:
            continue
        by_day.setdefault(timezone.localtime(item.starts_at).date(), []).append(item)
    conflicts = conflicting_ids([a for items in by_day.values() for a in items])
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        items = sorted(by_day.get(day, []), key=lambda a: a.starts_at)
        window = hours.get(day.weekday()) if hours else None
        booked = sum(a.duration_minutes for a in items)
        row = {'date': day, 'is_today': day == today, 'classes': len(items), 'booked': booked,
               'first': timezone.localtime(items[0].starts_at).time() if items else None,
               'last': timezone.localtime(items[-1].ends_at).time() if items else None,
               'window': window, 'off': bool(hours) and window is None, 'conflict': any(a.pk in conflicts for a in items),
               'outside': sum(1 for a in items if outside_hours(a.starts_at, a.ends_at, hours)),
               'free': None, 'busy_pct': None}
        if with_free and window:
            length = _minutes(window[1]) - _minutes(window[0])
            inside = sum(max(0, min(_minutes(timezone.localtime(a.ends_at).time()), _minutes(window[1]))
                             - max(_minutes(timezone.localtime(a.starts_at).time()), _minutes(window[0]))) for a in items
                         if timezone.localtime(a.ends_at).date() == day)
            row['free'] = max(0, length - inside)
            row['busy_pct'] = min(100, round(100 * inside / length)) if length > 0 else None
        rows.append(row)
    weeks = []
    for row in rows:
        monday = week_start(row['date'])
        if not weeks or weeks[-1]['monday'] != monday:
            weeks.append({'monday': monday, 'sunday': monday + timedelta(days=6), 'days': [], 'classes': 0, 'booked': 0})
        weeks[-1]['days'].append(row)
        weeks[-1]['classes'] += row['classes']
        weeks[-1]['booked'] += row['booked']
    total_window = sum(_minutes(r['window'][1]) - _minutes(r['window'][0]) for r in rows if r['window']) if with_free else 0
    total_free = sum(r['free'] for r in rows if r['free'] is not None)
    totals = {'classes': sum(r['classes'] for r in rows), 'booked': sum(r['booked'] for r in rows),
              'busy_days': sum(1 for r in rows if r['classes']), 'outside': sum(r['outside'] for r in rows),
              'conflicts': sum(1 for r in rows if r['conflict']),
              'busy_pct': round(100 * (total_window - total_free) / total_window) if total_window else None,
              'free': total_free if total_window else None}
    return {'days': rows, 'weeks': weeks, 'totals': totals}
