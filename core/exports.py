"""Data export (LGPD right of access): a ZIP with dados.json and the progress photos."""
import json
import os
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from .models import WorkoutSession


def _default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _fields(obj, names):
    return {name: getattr(obj, name) for name in names}


def build_data(person, include_internal_notes):
    """Everything stored about the student, as plain data.

    The student's own export leaves out the trainer's private notes
    (Person.notes and StudentNote); the trainer's export includes them."""
    profile = _fields(person, [
        'person_name', 'address', 'city', 'state', 'zip_code', 'phone', 'birth_date', 'goal',
        'health_notes', 'emergency_contact_name', 'emergency_contact_phone', 'plan_name',
        'plan_expires_on', 'status', 'status_reason', 'status_changed_at', 'created_at'])
    profile['email'] = person.user.email
    profile['last_login'] = person.user.last_login
    if include_internal_notes:
        profile['internal_notes'] = person.notes
        # Commercial terms negotiated by the trainer: confidential, never in the student's own export
        profile['hourly_rate'] = person.hourly_rate
        profile['plan_price'] = person.plan_price

    routines = []
    for routine in person.routines.prefetch_related('exercises__exercise'):
        routines.append({
            **_fields(routine, ['routine_name', 'description', 'difficulty_level', 'estimated_duration',
                                'routine_date', 'start_date', 'end_date', 'is_active', 'notes']),
            'exercises': [
                {'exercise': e.exercise.exercise_name, **_fields(e, ['order', 'sets', 'reps', 'rest_seconds', 'notes'])}
                for e in routine.exercises.all()],
        })

    sessions = []
    qs = WorkoutSession.objects.filter(person_routine__person=person).select_related('person_routine') \
        .prefetch_related('exercise_logs__sets', 'exercise_logs__person_routine_exercise__exercise')
    for session in qs:
        sessions.append({
            'routine': session.person_routine.routine_name,
            **_fields(session, ['start_time', 'end_time', 'duration_minutes', 'is_completed', 'notes']),
            'exercises': [{
                'exercise': log.person_routine_exercise.exercise.exercise_name,
                'notes': log.notes,
                'sets': [_fields(s, ['number', 'weight', 'reps', 'is_done']) for s in log.sets.all()],
            } for log in session.exercise_logs.all()],
        })

    data = {
        'exported_at': timezone.now(),
        'profile': profile,
        'assessments': [
            _fields(a, ['date', 'weight_kg', 'height_cm', 'body_fat_pct', 'chest_cm', 'waist_cm', 'hip_cm',
                        'arm_cm', 'thigh_cm', 'notes']) for a in person.assessments.all()],
        'photos': [
            {**_fields(p, ['date', 'caption']), 'file': f'fotos/{p.pk}{os.path.splitext(p.image.name)[1]}'}
            for p in person.progress_photos.all()],
        'routines': routines,
        'sessions': sessions,
        'payments': [
            {**_fields(p, ['paid_on', 'plan_name', 'amount', 'method', 'period_start', 'period_end', 'notes'])}
            for p in person.payments.all()],
        'schedule': {
            'weekly_slots': [_fields(sl, ['weekday', 'start_time', 'duration_minutes', 'is_active'])
                             for sl in person.slots.all()],
            'appointments': [_fields(a, ['starts_at', 'duration_minutes', 'kind', 'status'])
                             for a in person.appointments.all()],
        },
        'consents': [
            {**_fields(c, ['kind', 'version', 'granted', 'source']), 'at': c.at} for c in person.consents.all()],
        'history': [
            {'at': e.at, 'kind': e.kind, 'summary': e.summary, 'by': getattr(e.actor, 'email', '')}
            for e in person.events.select_related('actor')],
    }
    if include_internal_notes:
        data['trainer_notes'] = [{'at': n.created_at, 'text': n.text} for n in person.trainer_notes.all()]
    return data


def build_export(person, include_internal_notes=False):
    """A closed temporary file holding the ZIP; the caller streams it and the file is deleted on close."""
    data = build_data(person, include_internal_notes)
    tmp = tempfile.TemporaryFile(suffix='.zip')
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('dados.json', json.dumps(data, default=_default, ensure_ascii=False, indent=2))
        for photo in person.progress_photos.all():
            try:
                with photo.image.open('rb') as handle:
                    archive.writestr(f'fotos/{photo.pk}{os.path.splitext(photo.image.name)[1]}', handle.read())
            except (FileNotFoundError, ValueError):
                continue  # file missing on disk: the metadata is still in dados.json
    tmp.seek(0)
    return tmp
