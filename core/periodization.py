"""Periodization: a student's training split into phases (blocks of weeks), and where the student is in it today.

The phases of a student are shown in order of their start day. Today's phase is the one whose days include today; a gap
between two phases or a plan that has ended simply has no current phase. Nothing here changes a routine by itself: a phase
may point to the routine used in it, and the trainer decides when to swap."""
from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext, gettext_noop as _

from .models import TrainingPhase

ENDING_SOON_DAYS = 7

# The standard cycle offered when starting a plan: (name, weeks, focus). Names and focus are plain text the trainer may edit.
PRESETS = {
    'hypertrophy12': (_('Ciclo de 12 semanas (hipertrofia)'), [
        (_('Adaptação'), 2, _('Aprender os movimentos, cargas leves')),
        (_('Hipertrofia'), 6, _('Volume moderado a alto')),
        (_('Força'), 3, _('Cargas altas, menos repetições')),
        (_('Deload'), 1, _('Semana de recuperação, volume reduzido'))]),
    'strength8': (_('Ciclo de 8 semanas (força)'), [
        (_('Base'), 3, _('Técnica e volume moderado')),
        (_('Intensificação'), 4, _('Cargas altas')),
        (_('Deload'), 1, _('Semana de recuperação'))]),
}


def current(phases, today=None):
    """The phase that includes `today` (None when none does)."""
    today = today or timezone.localdate()
    return next((p for p in phases if p.start_date <= today <= p.end_date), None)


def describe(phases, today=None):
    """Rows for a page: {'phase', 'state': past|current|next|later, 'week', 'days_left', 'pct'}; `week` and `pct` only for
    the current phase. The rows come in order of start day; the first phase that has not started is 'next'."""
    today = today or timezone.localdate()
    rows, next_seen = [], False
    for phase in sorted(phases, key=lambda p: (p.start_date, p.pk)):
        row = {'phase': phase, 'week': None, 'days_left': (phase.end_date - today).days, 'pct': None}
        if phase.end_date < today:
            row['state'] = 'past'
        elif phase.start_date <= today:
            total_days = (phase.end_date - phase.start_date).days + 1
            done = (today - phase.start_date).days
            row.update(state='current', week=done // 7 + 1, pct=round(100 * (done + 1) / total_days))
        elif not next_seen:
            row['state'] = 'next'
            next_seen = True
        else:
            row['state'] = 'later'
        row['days_to_start'] = (phase.start_date - today).days
        rows.append(row)
    return rows


def next_start(person, today=None):
    """The day a new phase of this student would naturally start: the day after the last phase ends, or today."""
    today = today or timezone.localdate()
    last = person.phases.order_by('-start_date', '-pk').first()
    return max(last.end_date + timedelta(days=1), today) if last else today


def create_preset(person, key, start):
    """Creates the phases of a standard cycle one after the other from `start`. Returns them."""
    _title, steps = PRESETS[key]
    created, day = [], start
    for name, weeks, focus in steps:
        phase = TrainingPhase.objects.create(person=person, name=gettext(name), focus=gettext(focus), start_date=day, weeks=weeks)
        created.append(phase)
        day = phase.end_date + timedelta(days=1)
    return created


def ending(today=None, within=ENDING_SOON_DAYS):
    """Phases of active students that end within `within` days (today counts) and are not followed by another phase."""
    today = today or timezone.localdate()
    rows = []
    for phase in TrainingPhase.objects.filter(person__is_active=True, start_date__lte=today).select_related('person'):
        left = (phase.end_date - today).days
        if 0 <= left <= within and not phase.person.phases.filter(start_date__gt=phase.end_date).exists():
            rows.append(phase)
    return rows
