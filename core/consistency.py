"""Consistency and motivation for the student: weekly goal, streak of weeks and workout milestones.

Only about the student's own history: nothing here compares students with each other. A streak never
breaks in the middle of a week (the week in progress only counts once it reaches the goal)."""
from datetime import datetime, time, timedelta

from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from .models import WorkoutSession

MILESTONES = (10, 25, 50, 100, 200, 300, 500, 750, 1000)
STREAK_LOOKBACK_WEEKS = 104


def monday_of(day):
    return day - timedelta(days=day.weekday())


def effective_goal(person):
    """Workouts per week: the student's own goal, else None (no goal). A package has no weekly rhythm."""
    return person.weekly_goal or None


def week_counts(person, today=None, weeks=STREAK_LOOKBACK_WEEKS):
    """{monday: finished workouts that week} for the last `weeks` weeks (one query)."""
    today = today or timezone.localdate()
    first = monday_of(today) - timedelta(weeks=weeks)
    starts = WorkoutSession.objects.filter(
        person_routine__person=person, is_completed=True,
        start_time__gte=timezone.make_aware(datetime.combine(first, time.min)),
    ).values_list('start_time', flat=True)
    counts = {}
    for start in starts:
        week = monday_of(timezone.localtime(start).date())
        counts[week] = counts.get(week, 0) + 1
    return counts


def streak(counts, goal, today=None):
    """Weeks in a row that reached the goal (with no goal, a week counts when there was at least one workout).

    The week in progress adds one only once it reached the goal; until then it does not break anything."""
    today = today or timezone.localdate()
    target = goal or 1
    this_week = monday_of(today)
    met_now = counts.get(this_week, 0) >= target
    week = this_week if met_now else this_week - timedelta(weeks=1)
    weeks = 0
    for _week in range(STREAK_LOOKBACK_WEEKS):
        if counts.get(week, 0) < target:
            break
        weeks += 1
        week -= timedelta(weeks=1)
    missing = max(0, target - counts.get(this_week, 0))
    return {'weeks': weeks, 'met_this_week': met_now, 'missing_this_week': missing,
            'keeping': weeks > 0 and not met_now}


def next_milestone(total):
    """(milestone, previous milestone) around a total of finished workouts."""
    previous = max((m for m in MILESTONES if m <= total), default=0)
    upcoming = next((m for m in MILESTONES if m > total), None)
    return upcoming, previous


def ring(done, goal):
    """Numbers for the weekly ring (None without a goal)."""
    if not goal:
        return None
    circumference = 226.19   # 2 * pi * r with r = 36 (see the SVG in _today_student.html)
    fraction = min(done / goal, 1)
    return {'done': done, 'goal': goal, 'percent': round(fraction * 100), 'complete': done >= goal,
            'offset': round(circumference * (1 - fraction), 2), 'circumference': circumference}


def motivation(person, week_done, today=None):
    """What the "Hoje" screen shows when the student keeps the motivation layer on (None when it is off)."""
    if not person.motivation_enabled:
        return None
    today = today or timezone.localdate()
    goal = effective_goal(person)
    counts = week_counts(person, today)
    total = WorkoutSession.objects.filter(person_routine__person=person, is_completed=True).count()
    upcoming, previous = next_milestone(total)
    return {
        'goal': goal, 'ring': ring(week_done, goal), 'streak': streak(counts, goal, today),
        'total': total, 'next_milestone': upcoming, 'previous_milestone': previous,
        'to_next': None if upcoming is None else upcoming - total,
        'milestone_percent': None if upcoming is None else round(100 * (total - previous) / (upcoming - previous)),
    }


def celebrations(person, session):
    """Messages for the summary right after a workout: a milestone reached and/or the weekly goal met."""
    if not person.motivation_enabled:
        return []
    messages = []
    number = WorkoutSession.objects.filter(
        person_routine__person=person, is_completed=True, start_time__lte=session.start_time).count()
    if number in MILESTONES:
        messages.append({'icon': 'bi-trophy-fill', 'text': ngettext(
            'Marco: %(number)s treino concluído!', 'Marco: %(number)s treinos concluídos!', number) % {'number': number}})
    goal = effective_goal(person)
    if goal:
        week_start = monday_of(timezone.localtime(session.start_time).date())
        in_week = WorkoutSession.objects.filter(
            person_routine__person=person, is_completed=True, start_time__lte=session.start_time,
            start_time__gte=timezone.make_aware(datetime.combine(week_start, time.min))).count()
        if in_week == goal:   # this workout is the one that reached it
            messages.append({'icon': 'bi-bullseye', 'text': _('Meta da semana batida!')})
    return messages
