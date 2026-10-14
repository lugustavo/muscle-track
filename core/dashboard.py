"""Data for the trainer's home page: who needs attention today."""
from datetime import datetime, time, timedelta

from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone

from .models import Person, PersonRoutine, SessionExercise, StudentEvent, WorkoutSession

INACTIVE_DAYS = 7
EXPIRING_DAYS = 14
COMMENT_DAYS = 7
TRIAL_DAYS = 14
LIST_LIMIT = 8

# Student account that never set a password, i.e. never accepted the invite
INVITE_PENDING = Q(user__password__startswith=UNUSABLE_PASSWORD_PREFIX)


def _start_of_today():
    return timezone.make_aware(datetime.combine(timezone.localdate(), time.min))


def inactive_students(now=None):
    """Active students with a running routine who have not trained for INACTIVE_DAYS (or never).

    Students who never accepted the invite are not here: they cannot train yet (see pending_invites)."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=INACTIVE_DAYS)
    last_session = WorkoutSession.objects.filter(
        person_routine__person=OuterRef('pk'), is_completed=True
    ).order_by('-start_time').values('start_time')[:1]

    students = Person.objects.filter(is_active=True).annotate(
        has_routine=Exists(PersonRoutine.objects.filter(person=OuterRef('pk'), is_active=True)),
        last_session_at=Subquery(last_session),
    ).filter(has_routine=True).exclude(INVITE_PENDING).filter(
        Q(last_session_at__lt=cutoff) | Q(last_session_at__isnull=True, created_at__lt=cutoff)
    )
    rows = []
    for student in students:
        reference = student.last_session_at or student.created_at
        rows.append({
            'student': student,
            'last_session_at': student.last_session_at,
            'days': (now - reference).days,
        })
    rows.sort(key=lambda r: -r['days'])
    return rows


def pending_invites(now=None):
    """Active students who have not accepted the invite yet, oldest first."""
    now = now or timezone.now()
    rows = [
        {'student': p, 'days': (now - p.created_at).days}
        for p in Person.objects.filter(is_active=True).filter(INVITE_PENDING).select_related('user')
    ]
    rows.sort(key=lambda r: -r['days'])
    return rows


def long_trials(now=None):
    """Trial students who have been in trial for more than TRIAL_DAYS: time to decide."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=TRIAL_DAYS)
    rows = []
    for p in Person.objects.filter(status=Person.Status.TRIAL):
        since = p.status_changed_at or p.created_at
        if since < cutoff:
            rows.append({'student': p, 'days': (now - since).days})
    rows.sort(key=lambda r: -r['days'])
    return rows


def erasure_requests():
    """Students who asked for their data to be deleted (the event goes away with the student)."""
    seen, rows = set(), []
    for event in StudentEvent.objects.filter(kind=StudentEvent.Kind.ERASURE_REQUEST).select_related('person').order_by('at'):
        if event.person_id not in seen:
            seen.add(event.person_id)
            rows.append({'student': event.person, 'at': event.at})
    return rows


def expiring_plans():
    """Active students whose plan is expired or expires within EXPIRING_DAYS."""
    limit = timezone.localdate() + timedelta(days=EXPIRING_DAYS)
    return list(Person.objects.filter(
        is_active=True, plan_expires_on__isnull=False, plan_expires_on__lte=limit
    ).order_by('plan_expires_on'))


def ending_routines():
    """Running routines whose end date is past or within EXPIRING_DAYS."""
    limit = timezone.localdate() + timedelta(days=EXPIRING_DAYS)
    return list(PersonRoutine.objects.filter(
        is_active=True, person__is_active=True, end_date__isnull=False, end_date__lte=limit
    ).select_related('person').order_by('end_date'))


def todays_sessions():
    return list(WorkoutSession.objects.filter(
        start_time__gte=_start_of_today()
    ).select_related('person_routine__person').order_by('-start_time'))


def recent_comments(now=None):
    """Latest comments students left on workouts and exercises."""
    since = (now or timezone.now()) - timedelta(days=COMMENT_DAYS)
    items = []
    for session in WorkoutSession.objects.filter(
        start_time__gte=since).exclude(notes='').select_related('person_routine__person'):
        items.append({'when': session.start_time, 'student': session.person_routine.person,
                      'routine': session.person_routine.routine_name, 'exercise': None,
                      'text': session.notes, 'session_pk': session.pk})
    for log in SessionExercise.objects.filter(
        session__start_time__gte=since).exclude(notes='').select_related(
            'session__person_routine__person', 'person_routine_exercise__exercise'):
        items.append({'when': log.session.start_time, 'student': log.session.person_routine.person,
                      'routine': log.session.person_routine.routine_name,
                      'exercise': log.person_routine_exercise.exercise.exercise_name,
                      'text': log.notes, 'session_pk': log.session_id})
    items.sort(key=lambda i: i['when'], reverse=True)
    return items[:LIST_LIMIT]


def trainer_dashboard():
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    counts = {
        'students': Person.objects.filter(is_active=True).count(),
        'routines': PersonRoutine.objects.filter(is_active=True, person__is_active=True).count(),
        'sessions_week': WorkoutSession.objects.filter(is_completed=True, start_time__gte=week_ago).count(),
    }
    inactive = inactive_students(now)
    invites = pending_invites(now)
    trials = long_trials(now)
    erasures = erasure_requests()
    plans = expiring_plans()
    routines = ending_routines()
    sessions = todays_sessions()
    return {
        'counts': counts,
        'inactive': inactive[:LIST_LIMIT], 'inactive_total': len(inactive),
        'inactive_more': max(0, len(inactive) - LIST_LIMIT),
        'trials': trials[:LIST_LIMIT], 'trials_total': len(trials),
        'erasures': erasures, 'erasures_total': len(erasures),
        'invites': invites[:LIST_LIMIT], 'invites_total': len(invites),
        'invites_more': max(0, len(invites) - LIST_LIMIT),
        'plans': plans[:LIST_LIMIT], 'plans_total': len(plans),
        'routines': routines[:LIST_LIMIT], 'routines_total': len(routines),
        'sessions': sessions[:LIST_LIMIT], 'sessions_total': len(sessions),
        'comments': recent_comments(now),
    }
