"""Data for the student's "Hoje" screen and the bottom bar: which workout is next, what is open."""
import calendar
from collections import Counter
from datetime import datetime, time, timedelta

from django.db.models import Avg, Count, Max, OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.translation import pgettext_lazy

from .consistency import effective_goal, motivation
from .models import Appointment, PersonRoutine, WorkoutSession
from .workout_sessions import MAX_SESSION_MINUTES, close_stale  # noqa: F401  (MAX_SESSION_MINUTES is read from here by the tests)

# One letter per weekday, Monday first (the context tells the identical letters apart)
WEEKDAY_LETTERS = [
    pgettext_lazy('weekday letter, Monday', 'S'), pgettext_lazy('weekday letter, Tuesday', 'T'),
    pgettext_lazy('weekday letter, Wednesday', 'Q'), pgettext_lazy('weekday letter, Thursday', 'Q'),
    pgettext_lazy('weekday letter, Friday', 'S'), pgettext_lazy('weekday letter, Saturday', 'S'),
    pgettext_lazy('weekday letter, Sunday', 'D')]


def suggest_index(pairs):
    """Index of the suggested routine among (is_open, last_done) pairs, or None when empty.

    The one in progress first; otherwise the one done longest ago (never done comes first),
    which rotates A/B/C naturally."""
    if not pairs:
        return None
    candidates = [i for i, (is_open, _) in enumerate(pairs) if is_open] or list(range(len(pairs)))
    return min(candidates, key=lambda i: (pairs[i][1] is not None, pairs[i][1] or 0))


def routine_rows(person, now=None):
    """The student's active routines with what is needed to pick the next one (one query)."""
    now = now or timezone.now()
    open_start = WorkoutSession.objects.filter(
        person_routine=OuterRef('pk'), is_completed=False).order_by('-start_time').values('start_time')[:1]
    rows = []
    for routine in PersonRoutine.objects.filter(person=person, is_active=True).annotate(
        exercise_count=Count('exercises', distinct=True),
        last_done=Max('sessions__start_time', filter=Q(sessions__is_completed=True)),
        open_start=Subquery(open_start),
    ).order_by('routine_name'):
        started = routine.open_start
        if started and now > started + timedelta(minutes=MAX_SESSION_MINUTES):
            started = None  # abandoned: the workout views close it, it is not "in progress"
        rows.append({'routine': routine, 'open_start': started, 'last_done': routine.last_done,
                     'exercise_count': routine.exercise_count})
    return rows


def rows_for(request, person):
    """routine_rows cached on the request, so the bottom bar and the page share one query."""
    if getattr(request, '_today_rows', None) is None:
        close_stale(person)   # an idle workout is not "in progress": end it before the screens read the state
        request._today_rows = routine_rows(person)
    return request._today_rows


def suggested_row(rows):
    index = suggest_index([(bool(r['open_start']), r['last_done']) for r in rows])
    return None if index is None else rows[index]


def open_row(rows):
    """The routine whose session is in progress (the most recent, if there are several)."""
    started = [r for r in rows if r['open_start']]
    return max(started, key=lambda r: r['open_start']) if started else None


def start_of_day(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def week_strip(person, today):
    """Seven dots Monday to Sunday: which days of this week had a finished workout."""
    monday = today - timedelta(days=today.weekday())
    starts = WorkoutSession.objects.filter(
        person_routine__person=person, is_completed=True,
        start_time__gte=start_of_day(monday), start_time__lt=start_of_day(monday + timedelta(days=7)),
    ).values_list('start_time', flat=True)
    done_days = {timezone.localtime(s).date() for s in starts}
    days = []
    for i in range(7):
        day = monday + timedelta(days=i)
        days.append({'date': day, 'letter': WEEKDAY_LETTERS[i], 'done': day in done_days,
                     'is_today': day == today, 'future': day > today})
    return days


def month_summary(person, today, now=None):
    """The student's month so far: workouts, days trained, classes, the same stretch of last month and a day grid.

    Only about her own history (last month is shown as a plain number, never as a score)."""
    now = now or timezone.now()
    first = today.replace(day=1)
    prev_first = (first - timedelta(days=1)).replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    # the same stretch of last month: up to the same day number (or its last day, when it is shorter)
    until = min(today.day, calendar.monthrange(prev_first.year, prev_first.month)[1])
    starts = WorkoutSession.objects.filter(
        person_routine__person=person, is_completed=True,
        start_time__gte=start_of_day(prev_first), start_time__lt=start_of_day(next_first),
    ).values_list('start_time', flat=True)
    this_days, previous = Counter(), 0
    for start in starts:
        day = timezone.localtime(start).date()
        if day >= first:
            this_days[day.day] += 1
        elif day.day <= until:
            previous += 1
    classes = person.appointments.filter(starts_at__gte=start_of_day(first), starts_at__lt=start_of_day(next_first)).aggregate(
        done=Count('pk', filter=Q(status=Appointment.Status.ATTENDED)),
        upcoming=Count('pk', filter=Q(status=Appointment.Status.SCHEDULED, starts_at__gte=now)))
    cells = [None] * first.weekday()   # blank cells before the 1st (the week starts on Monday)
    for number in range(1, calendar.monthrange(first.year, first.month)[1] + 1):
        cells.append({'day': number, 'trained': number in this_days, 'is_today': number == today.day,
                      'future': number > today.day})
    return {
        'month': first, 'workouts': sum(this_days.values()), 'days_trained': len(this_days),
        'classes_done': classes['done'], 'classes_upcoming': classes['upcoming'],
        'previous': {'month': prev_first, 'workouts': previous, 'until': until},
        'cells': cells, 'letters': WEEKDAY_LETTERS,
    }


def student_today(request, person):
    """Everything the "Hoje" screen shows."""
    now = timezone.now()
    today = timezone.localdate()
    rows = rows_for(request, person)
    in_progress = open_row(rows)
    suggested = suggested_row(rows)
    done_today = None
    if not in_progress:
        done_today = WorkoutSession.objects.filter(
            person_routine__person=person, is_completed=True, start_time__gte=start_of_day(today)
        ).select_related('person_routine').order_by('-start_time').first()

    if in_progress:
        state = 'in_progress'
    elif done_today:
        state = 'done'
    elif suggested:
        state = 'ready'
    else:
        state = 'empty'

    strip = week_strip(person, today)
    goal = effective_goal(person)
    week_done = sum(1 for d in strip if d['done'])

    completed = WorkoutSession.objects.filter(person_routine__person=person, is_completed=True)
    stats = completed.aggregate(
        total=Count('pk'),
        recent=Count('pk', filter=Q(start_time__gte=now - timedelta(days=30))),
        avg=Avg('duration_minutes'),
    )
    next_class = person.appointments.filter(
        status=Appointment.Status.SCHEDULED, starts_at__gte=now).order_by('starts_at').first()
    return {
        'next_class': next_class,
        'state': state, 'first_name': person.person_name.split()[0] if person.person_name.strip() else '',
        'today': today, 'in_progress': in_progress, 'suggested': suggested, 'done_today': done_today,
        'week': strip, 'week_done': week_done, 'week_goal': goal, 'month': month_summary(person, today, now),
        'motivation': motivation(person, week_done, today),
        'total_workouts': stats['total'], 'recent_workouts': stats['recent'],
        'avg_duration': round(stats['avg'] or 0),
    }
