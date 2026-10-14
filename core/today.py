"""Data for the student's "Hoje" screen and the bottom bar: which workout is next, what is open."""
from datetime import datetime, time, timedelta

from django.db.models import Avg, Count, Max, OuterRef, Q, Subquery
from django.utils import timezone

from .models import Plan, PersonRoutine, WorkoutSession

# A session left open longer than this is closed by the workout views (see _close_if_stale)
MAX_SESSION_MINUTES = 120

WEEKDAY_LETTERS = ['S', 'T', 'Q', 'Q', 'S', 'S', 'D']


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
    goal = None
    if person.plan_id:
        goal = Plan.objects.filter(pk=person.plan_id).values_list('sessions_per_week', flat=True).first()

    completed = WorkoutSession.objects.filter(person_routine__person=person, is_completed=True)
    stats = completed.aggregate(
        total=Count('pk'),
        recent=Count('pk', filter=Q(start_time__gte=now - timedelta(days=30))),
        avg=Avg('duration_minutes'),
    )
    return {
        'state': state, 'first_name': person.person_name.split()[0] if person.person_name.strip() else '',
        'today': today, 'in_progress': in_progress, 'suggested': suggested, 'done_today': done_today,
        'week': strip, 'week_done': sum(1 for d in strip if d['done']), 'week_goal': goal,
        'total_workouts': stats['total'], 'recent_workouts': stats['recent'],
        'avg_duration': round(stats['avg'] or 0),
    }
