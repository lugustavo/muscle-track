"""Data for the trainer's home page: who needs attention today."""
from datetime import datetime, time, timedelta

from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.db.models import Case, DateTimeField, Exists, OuterRef, Q, Subquery, When
from django.utils import dateformat, timezone, translation
from django.utils.translation import gettext as _, gettext_lazy as _l

from . import packages, refunds
from .agenda import ensure_appointments
from .billing import charge_message, overdue_rows
from .models import Appointment, Person, PersonRoutine, SessionExercise, StudentEvent, WorkoutSession
from .whatsapp import whatsapp_url
from .whatsapp_messages import Kind, current_text, language_of, render

INACTIVE_DAYS = 7
EXPIRING_DAYS = 14
COMMENT_DAYS = 7
TRIAL_DAYS = 14
LIST_LIMIT = 8

# What asks for the trainer's attention, most pressing first: (card, short label, colour of its chip). A card that has
# something to do goes up in this order; the ones with nothing to do sink to the bottom, in the same order.
PRIORITY = (
    ('erasures', _l('Exclusão de dados'), 'danger'),     # a legal deadline
    ('payments', _l('Pagamentos e devoluções'), 'warning'),
    ('plans', _l('Pacotes para renovar'), 'warning'),
    ('classes', _l('Aulas de hoje'), 'primary'),
    ('misses', _l('Faltas'), 'danger'),
    ('inactive', _l('Sem treinar'), 'secondary'),
    ('trials', _l('Experimentais'), 'secondary'),
    ('invites', _l('Convites'), 'secondary'),
    ('routines', _l('Fichas terminando'), 'secondary'),
)
INFO_CARDS = ('sessions', 'comments')   # worth reading, never "to do": ordered after the ones above, outside the strip

# Student with the app whose account never got a password, i.e. never accepted the invite (a student without the app
# has no invite to wait for)
INVITE_PENDING = Q(has_app=True, user__password__startswith=UNUSABLE_PASSWORD_PREFIX)


def last_activity(person_ref):
    """When the student last trained: the last completed workout in the app, or for a student without the app (who
    does not log workouts) the last class attended."""
    workout = WorkoutSession.objects.filter(person_routine__person=OuterRef(person_ref), is_completed=True
                                            ).order_by('-start_time').values('start_time')[:1]
    lesson = Appointment.objects.filter(person=OuterRef(person_ref), status=Appointment.Status.ATTENDED
                                        ).order_by('-starts_at').values('starts_at')[:1]
    return Case(When(has_app=False, then=Subquery(lesson)), default=Subquery(workout), output_field=DateTimeField())


def _start_of_today():
    return timezone.make_aware(datetime.combine(timezone.localdate(), time.min))


def inactive_students(now=None):
    """Active students with a running routine who have not trained for INACTIVE_DAYS (or never).

    Students who never accepted the invite are not here: they cannot train yet (see pending_invites)."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=INACTIVE_DAYS)
    students = Person.objects.filter(is_active=True).annotate(
        has_routine=Exists(PersonRoutine.objects.filter(person=OuterRef('pk'), is_active=True)),
        last_session_at=last_activity('pk'),
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


def overdue_payments():
    """Overdue students with the ready-to-send WhatsApp charge message."""
    rows = overdue_rows()
    text = current_text(Kind.CHARGE)
    for row in rows:
        row['whatsapp'] = whatsapp_url(row['student'].phone,
                                       charge_message(row['student'], row['package'], row['amount'], text))
    return rows


def todays_appointments():
    """Today's classes, earliest first (fills the calendar for today when it is still empty)."""
    today = timezone.localdate()
    ensure_appointments(today, today, today)
    start = _start_of_today()
    return list(Appointment.objects.filter(
        starts_at__gte=start, starts_at__lt=start + timedelta(days=1)
    ).exclude(status__in=(Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED)).select_related('person'))


def recent_misses(days=7, now=None):
    """Missed classes of the last `days` days, with a ready WhatsApp message."""
    now = now or timezone.now()
    rows = []
    template = current_text(Kind.MISSED)
    for a in Appointment.objects.filter(status=Appointment.Status.MISSED, starts_at__gte=now - timedelta(days=days),
                                        person__is_active=True).select_related(
            'person__user__notification_preference').order_by('-starts_at'):
        with translation.override(language_of(a.person)):   # the message goes to the student, in their language
            text = render(Kind.MISSED, {'nome': a.person.person_name.split()[0],
                                        'data': dateformat.format(timezone.localtime(a.starts_at), _('d/m'))}, template)
        rows.append({'appointment': a, 'student': a.person, 'whatsapp': whatsapp_url(a.person.phone, text)})
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
    """Active students whose current package is expired, expiring soon or running out (see packages.renewals)."""
    return packages.renewals(expiring_days=EXPIRING_DAYS)


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


def priorities(counts):
    """`counts` is {card: how many things it lists}. Returns the chips for the "Atenção agora" strip (only the cards with
    something to do, most pressing first) and the CSS `order` of every card (the ones with nothing to do sink)."""
    chips, order = [], {}
    for rank, (key, label, level) in enumerate(PRIORITY):
        n = counts.get(key, 0)
        order[key] = rank if n else 50 + rank
        if n:
            chips.append({'key': key, 'label': label, 'count': n, 'level': level})
    for offset, key in enumerate(INFO_CARDS):
        order[key] = 40 + offset if counts.get(key) else 90 + offset
    return chips, order


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
    overdue = overdue_payments()
    refunds_due = refunds.awaiting_refund()
    today_classes = todays_appointments()
    misses = recent_misses(now=now)
    erasures = erasure_requests()
    plans = expiring_plans()
    routines = ending_routines()
    sessions = todays_sessions()
    comments = recent_comments(now)
    chips, order = priorities({
        'erasures': len(erasures), 'payments': len(overdue) + len(refunds_due), 'plans': len(plans),
        'classes': len(today_classes), 'misses': len(misses), 'inactive': len(inactive), 'trials': len(trials),
        'invites': len(invites), 'routines': len(routines), 'sessions': len(sessions), 'comments': len(comments)})
    return {
        'attention': chips, 'order': order,
        'counts': counts,
        'inactive': inactive[:LIST_LIMIT], 'inactive_total': len(inactive),
        'inactive_more': max(0, len(inactive) - LIST_LIMIT),
        'overdue': overdue[:LIST_LIMIT], 'overdue_total': len(overdue),
        'overdue_more': max(0, len(overdue) - LIST_LIMIT),
        'overdue_amount': sum((r['amount'] or 0 for r in overdue), 0),
        'refunds_due': refunds_due[:LIST_LIMIT], 'refunds_due_total': len(refunds_due),
        'refunds_due_more': max(0, len(refunds_due) - LIST_LIMIT),
        'refunds_due_amount': sum((r['quote']['suggested'] for r in refunds_due), 0),
        'classes': today_classes, 'misses': misses[:LIST_LIMIT], 'misses_total': len(misses),
        'trials': trials[:LIST_LIMIT], 'trials_total': len(trials),
        'erasures': erasures, 'erasures_total': len(erasures),
        'invites': invites[:LIST_LIMIT], 'invites_total': len(invites),
        'invites_more': max(0, len(invites) - LIST_LIMIT),
        'plans': plans[:LIST_LIMIT], 'plans_total': len(plans),
        'routines': routines[:LIST_LIMIT], 'routines_total': len(routines),
        'sessions': sessions[:LIST_LIMIT], 'sessions_total': len(sessions),
        'comments': comments,
    }
