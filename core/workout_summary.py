"""What the student did in a finished workout: volume, records and a comparison with last time."""
from decimal import Decimal

from django.db.models import Max

from .models import SessionSet, WorkoutSession


def _done_sets(session):
    return SessionSet.objects.filter(session_exercise__session=session, is_done=True)


def session_volume(session):
    """Sum of weight x reps over the finished sets (sets without a weight or reps count as zero)."""
    total = Decimal('0')
    for weight, reps in _done_sets(session).values_list('weight', 'reps'):
        if weight is not None and reps is not None:
            total += weight * reps
    return total


def _pct(now, before):
    """Change in percent, or None when there is nothing to compare with."""
    if not before:
        return None
    return round(float((now - before) * 100 / before))


def session_summary(session):
    sets = list(SessionSet.objects.filter(session_exercise__session=session).select_related(
        'session_exercise__person_routine_exercise__exercise'))
    done = [s for s in sets if s.is_done]

    per_exercise = {}
    for s in sets:
        exercise = s.session_exercise.person_routine_exercise.exercise
        entry = per_exercise.setdefault(exercise.pk, {'name': exercise.exercise_name, 'total': 0, 'done': 0, 'best': None})
        entry['total'] += 1
        if s.is_done:
            entry['done'] += 1
            if s.weight is not None and (entry['best'] is None or s.weight > entry['best']):
                entry['best'] = s.weight

    person = session.person_routine.person
    previous_best = {
        row['session_exercise__person_routine_exercise__exercise_id']: row['best']
        for row in SessionSet.objects.filter(
            session_exercise__session__person_routine__person=person,
            session_exercise__session__is_completed=True, is_done=True, weight__isnull=False,
            session_exercise__person_routine_exercise__exercise_id__in=list(per_exercise),
        ).exclude(session_exercise__session=session).values(
            'session_exercise__person_routine_exercise__exercise_id').annotate(best=Max('weight'))
    }
    # A record needs earlier history: the first time an exercise is done is not a "record"
    records = [
        {'name': e['name'], 'weight': e['best']} for pk, e in per_exercise.items()
        if e['best'] is not None and pk in previous_best and e['best'] > previous_best[pk]
    ]

    volume = session_volume(session)
    comparison = None
    last = WorkoutSession.objects.filter(
        person_routine=session.person_routine, is_completed=True, start_time__lt=session.start_time
    ).exclude(pk=session.pk).order_by('-start_time').first()
    if last is not None:
        last_volume = session_volume(last)
        comparison = {
            'date': last.start_time,
            'volume_pct': _pct(volume, last_volume),
            'duration_pct': _pct(Decimal(session.duration_minutes or 0), Decimal(last.duration_minutes or 0)),
            'last_volume': last_volume,
            'last_duration': last.duration_minutes,
        }
    return {
        'duration_minutes': session.duration_minutes or 0,
        'sets_done': len(done), 'sets_total': len(sets),
        'exercises_done': sum(1 for e in per_exercise.values() if e['total'] and e['done'] == e['total']),
        'exercises_total': len(per_exercise),
        'volume': volume, 'records': records, 'comparison': comparison,
    }
