"""Goals with a deadline: how far each one is, when it is due and what is worth the trainer's attention.

A goal may follow a measure of the assessments (weight, waist...). Its progress is how much of the way from where the student
started to the target the latest assessment already covers; it works the same whether the goal is to lose or to gain. A goal
that is just text has no progress bar, only the deadline. Closing a goal (achieved or dropped) is always the trainer's
decision: reaching the number only makes the app suggest it."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Goal

DUE_SOON_DAYS = 7      # "due soon": this many days or less to the deadline


def latest_value(person, metric, on_or_before=None):
    """The newest recorded value of a measure (None when there is none)."""
    assessments = person.assessments.exclude(**{f'{metric}__isnull': True})
    if on_or_before is not None:
        assessments = assessments.filter(date__lte=on_or_before)
    row = assessments.order_by('-date', '-pk').values_list(metric, flat=True).first()
    return row


def first_value(person, metric):
    """The oldest recorded value of a measure (None when there is none): where a goal without a start value starts from."""
    return person.assessments.exclude(**{f'{metric}__isnull': True}).order_by('date', 'pk').values_list(metric, flat=True).first()


def starting_value(person, metric, today=None):
    """Where the student is now on that measure, to start a goal from (None without an assessment)."""
    return latest_value(person, metric, today or timezone.localdate())


def progress(goal):
    """None for a goal without a number or without enough data; otherwise {'current', 'pct', 'reached'}.

    `pct` is 0-100 of the way from the start value to the target; `reached` says the target was met or passed."""
    if not goal.metric or goal.target_value is None:
        return None
    current = latest_value(goal.person, goal.metric)
    if current is None:
        return None
    current, target = Decimal(current), Decimal(goal.target_value)
    start = goal.start_value if goal.start_value is not None else first_value(goal.person, goal.metric)
    start = Decimal(start) if start is not None else None
    reached = current >= target if (start is None or target >= start) else current <= target
    if start is None or start == target:        # no way to tell the direction (or nothing to travel)
        reached = reached if start is not None else current == target
        pct = 100 if reached else 0
    else:
        pct = max(0, min(100, round(float((current - start) / (target - start) * 100))))
    return {'current': current, 'pct': pct, 'reached': reached}


def due_label(goal, today=None):
    """(state, days): 'closed', 'overdue' (past the deadline), 'soon' (within DUE_SOON_DAYS) or 'later'; days is how many
    days to (or since) the deadline."""
    today = today or timezone.localdate()
    days = (goal.target_date - today).days
    if goal.status != Goal.Status.ACTIVE:
        return 'closed', days
    if days < 0:
        return 'overdue', -days
    return ('soon' if days <= DUE_SOON_DAYS else 'later'), days


def describe(goals, today=None):
    """The goals as rows for a page: {'goal', 'progress', 'state', 'days'}; the ones in progress first, by deadline."""
    today = today or timezone.localdate()
    rows = []
    for goal in goals:
        state, days = due_label(goal, today)
        rows.append({'goal': goal, 'progress': progress(goal) if goal.status == Goal.Status.ACTIVE else None,
                     'state': state, 'days': days})
    rows.sort(key=lambda r: (r['goal'].status != Goal.Status.ACTIVE, r['goal'].target_date))
    return rows


def attention(today=None):
    """Active goals that are overdue or due soon, of active students (what the trainer's morning summary counts)."""
    today = today or timezone.localdate()
    return Goal.objects.filter(status=Goal.Status.ACTIVE, person__is_active=True,
                               target_date__lte=today + timedelta(days=DUE_SOON_DAYS)).select_related('person')
