"""Workouts done with a weak or no connection: the device keeps the whole workout and sends it in one go.

The phone is the source of truth while it is offline. `sync` takes the full state of a workout (every set, the comments,
the pauses, when it started and ended) and makes the server match it. It is idempotent (absolute values, and the
workout is found again by the id the device gave it), so a request that is repeated after a dropped connection changes
nothing. It also covers the awkward cases of being offline for a long time:
- the server closed the idle session meanwhile (core/workout_sessions.py): it is reopened, as the student was training;
- the session was deleted for having nothing logged: a new one is created from the device's state;
- the workout was started with no signal at all: there is no session yet, it is created now, with the device's start time.
The device's clock is only trusted inside sane limits (never in the future, not older than a working day)."""
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import PersonRoutine, SessionExercise, SessionSet, WorkoutSession

MAX_AGE = timedelta(hours=12)         # a device clock older than this is not believed
REOPEN_WINDOW = timedelta(hours=24)   # an idle-closed session can come back for this long


class SyncError(Exception):
    """The state sent cannot be applied; the message is only for the logs, the device is told "invalid"."""


def client_time(value, now, oldest=None):
    """An aware datetime from the device's milliseconds, or None when absent or not believable (future, too old)."""
    try:
        moment = datetime.fromtimestamp(int(value) / 1000, tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    floor = oldest or (now - MAX_AGE)
    if moment > now + timedelta(minutes=2) or moment < floor:
        return None
    return min(moment, now)


def apply_set_values(workout_set, data, now):
    """Apply weight / reps / done (any of them, absolute values) to an unsaved set; ValueError / InvalidOperation on bad input.

    Shared by the one-set endpoint (a set marked while online) and the full sync (a workout done offline)."""
    if 'weight' in data:
        raw = data['weight']
        if raw is None or str(raw).strip() == '':
            workout_set.weight = None
        else:
            weight = Decimal(str(raw).replace(',', '.')).quantize(Decimal('0.01'))
            if not weight.is_finite() or weight < 0 or weight > Decimal('9999.99'):
                raise ValueError('weight')
            workout_set.weight = weight
    if 'reps' in data:
        raw = data['reps']
        if raw is None or str(raw).strip() == '':
            workout_set.reps = None
        else:
            reps = int(str(raw))
            if reps < 0 or reps > 999:
                raise ValueError('reps')
            workout_set.reps = reps
    if 'done' in data:
        if not isinstance(data['done'], bool):
            raise ValueError('done')
        if data['done'] and not workout_set.is_done:
            workout_set.completed_at = data.get('completed_at') or now
        elif not data['done']:
            workout_set.completed_at = None
        workout_set.is_done = data['done']


def _find_or_create_session(person, routine, data, now):
    """The session this state belongs to, reopened or created when needed. Returns (session, created)."""
    sessions = WorkoutSession.objects.filter(person_routine__person=person)
    uuid = data['uuid']
    session = sessions.filter(client_uuid=uuid).first()
    if session is None and data.get('session'):
        session = sessions.filter(pk=data['session'], person_routine=routine, client_uuid__isnull=True).first()
    if session is None:
        session = routine.sessions.filter(is_completed=False, client_uuid__isnull=True).first()   # started on this or another device
    if session is not None and session.is_completed:
        recent = session.end_time and now - session.end_time < REOPEN_WINDOW
        if session.auto_closed and recent:
            session.is_completed, session.end_time, session.duration_minutes, session.auto_closed = False, None, None, False
        elif session.client_uuid is not None:
            return session, False          # the same workout, already finished: nothing to change
        else:
            session = None                 # an unrelated finished session: this is a new workout
    created = False
    if session is None:
        session = WorkoutSession.objects.create(person_routine=routine)
        started = client_time(data.get('started_at'), now)
        if started:
            WorkoutSession.objects.filter(pk=session.pk).update(start_time=started)
            session.start_time = started
        created = True
    if session.client_uuid is None:
        session.client_uuid = uuid
    return session, created


def _apply_exercises(session, routine, exercises, now):
    prxs = {prx.pk: prx for prx in routine.exercises.all()}
    logs = {log.person_routine_exercise_id: log for log in session.exercise_logs.prefetch_related('sets')}
    for item in exercises:
        prx = prxs.get(item.get('prx'))
        if prx is None:
            raise SyncError('unknown exercise')
        log = logs.get(prx.pk) or SessionExercise.objects.create(session=session, person_routine_exercise=prx)
        sets = {s.number: s for s in log.sets.all()} if prx.pk in logs else {}
        if isinstance(item.get('notes'), str):
            log.notes = item['notes'].strip()[:1000]
            log.save(update_fields=['notes'])
        for row in item.get('sets') or []:
            number = row.get('n')
            if not isinstance(number, int) or not 1 <= number <= max(prx.sets, 1) + 20:
                raise SyncError('bad set number')
            workout_set = sets.get(number) or SessionSet(session_exercise=log, number=number, reps=prx.reps)
            values = {key: row[key] for key in ('weight', 'reps', 'done') if key in row}
            if values.get('done') is True and not workout_set.is_done:
                values['completed_at'] = client_time(row.get('at'), now, oldest=session.start_time - timedelta(minutes=5))
            apply_set_values(workout_set, values, now)
            workout_set.save()
        log.sync_from_sets()


@transaction.atomic
def sync(person, data, now=None):
    """Make the server match the device's workout. Returns {'session': WorkoutSession, 'finished': bool, 'created': bool}.

    Raises SyncError (invalid data) or PersonRoutine.DoesNotExist (a routine that is not this student's)."""
    now = now or timezone.now()
    try:
        routine = PersonRoutine.objects.get(pk=data.get('routine'), person=person)
    except (ValueError, TypeError):
        raise SyncError('bad routine')
    try:
        session, created = _find_or_create_session(person, routine, data, now)
        if session.is_completed:   # already finished (the same workout sent twice, or finished on another device)
            return {'session': session, 'finished': True, 'created': False}
        _apply_exercises(session, routine, data.get('exercises') or [], now)
    except (ValueError, InvalidOperation) as error:
        raise SyncError(str(error))
    if isinstance(data.get('notes'), str):
        session.notes = data['notes'].strip()[:2000]
    paused = data.get('paused_seconds')
    if isinstance(paused, int) and 0 <= paused <= 24 * 3600:
        session.paused_seconds = paused
    session.paused_at = client_time(data.get('paused_at'), now, oldest=session.start_time) if data.get('paused') else None
    session.save()
    finished = False
    if data.get('finish'):
        ended = client_time(data.get('ended_at'), now, oldest=session.start_time) or now
        session.end_session(at=ended)
        finished = True
    return {'session': session, 'finished': finished, 'created': created}
