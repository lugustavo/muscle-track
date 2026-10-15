"""Workouts left running: close or drop them when the student is idle or loses access.

A running session (`is_completed=False`) has a timer. Left alone it would stay "in progress" forever, and come back
to life when a paused or former student is reactivated. So a session is ended when:
- nothing happened in it for IDLE_MINUTES (a set marked or edited, a pause), or it has run longer than
  MAX_SESSION_MINUTES: the student walked away without finishing;
- the student is paused or becomes a former student (`Person.set_status`).
A session with nothing logged is deleted (like "Descartar treino"); one with sets or comments is finished at its last
sign of life, never at "now", so a forgotten workout does not turn into an hours-long training."""
from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

from .models import SessionSet, WorkoutSession

IDLE_MINUTES = 60
MAX_SESSION_MINUTES = 120


def last_activity(session):
    """The last sign of life of a running workout: its start, a pause, or the last time a set changed."""
    moments = [session.start_time]
    if session.paused_at:
        moments.append(session.paused_at)
    touched = SessionSet.objects.filter(session_exercise__session=session).aggregate(
        edited=Max('updated_at'), done=Max('completed_at'))
    moments += [m for m in touched.values() if m]
    return max(moments)


def is_stale(session, now=None):
    now = now or timezone.now()
    return (now > session.start_time + timedelta(minutes=MAX_SESSION_MINUTES)
            or now > last_activity(session) + timedelta(minutes=IDLE_MINUTES))


def _has_content(session):
    return (SessionSet.objects.filter(session_exercise__session=session, is_done=True).exists()
            or bool(session.notes.strip()) or session.exercise_logs.exclude(notes='').exists())


def end_or_drop(session, now=None):
    """Finish a running session at its last sign of life, or delete it when nothing was logged. 'closed'/'deleted'."""
    now = now or timezone.now()
    if not _has_content(session):
        session.delete()
        return 'deleted'
    session.end_session(at=min(now, last_activity(session)))
    return 'closed'


def close_if_stale(session, now=None):
    """End `session` when it is idle or too long. True when it is gone from the running ones."""
    if not session or session.is_completed:
        return False
    if not is_stale(session, now):
        return False
    end_or_drop(session, now)
    return True


def close_stale(person=None, now=None):
    """End every idle running session (of one student, or of everybody). Returns how many."""
    now = now or timezone.now()
    running = WorkoutSession.objects.filter(is_completed=False)
    if person is not None:
        running = running.filter(person_routine__person=person)
    return sum(1 for session in list(running) if close_if_stale(session, now))


def close_open(person, now=None):
    """The student lost access: end all their running sessions, idle or not. Returns how many."""
    now = now or timezone.now()
    sessions = list(WorkoutSession.objects.filter(person_routine__person=person, is_completed=False))
    for session in sessions:
        end_or_drop(session, now)
    return len(sessions)
