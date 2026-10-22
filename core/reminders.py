"""Automatic reminders: every student (and the trainer) who kept that kind of reminder on gets it in the app's notice
centre, and also by e-mail, push or Telegram on the channels they turned on.

`run()` is idempotent and meant to be called every few minutes (management command `send_reminders`, run by the
`scheduler` service of docker-compose): each class, each plan expiry and each package alert is told once (`Appointment.reminder_sent_at`,
`Person.plan_reminder_for`, `StudentPackage.low_notified_at` and friends)."""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import F, Max, Q
from django.utils import timezone

from . import backup_status, dashboard, goals, inbox, notifications, packages, workout_sessions
from .agenda import ensure_appointments
from .billing import overdue_rows
from .models import Appointment, Goal, Notice, NotificationPreference, Person

# A class that starts in less than this is not reminded any more (it is too late to be useful)
MIN_LEAD = timedelta(hours=1)


def _wants(prefix, flag):
    """Filter on the person's account preferences: the given per-type switch on (a person who never opened their
    settings has no row and gets everything, as in core/inbox.py)."""
    base = 'user__notification_preference__' if prefix == 'person' else 'person__user__notification_preference__'
    return Q(**{f'{base}{flag}': True}) | Q(**{f'{base}isnull': True})


def send_class_reminders(now):
    hours = settings.REMINDER_CLASS_HOURS
    if hours <= 0:
        return 0
    due = Appointment.objects.filter(
        _wants('appointment', 'notify_class_reminder'),
        status=Appointment.Status.SCHEDULED, reminder_sent_at__isnull=True, person__is_active=True,
        starts_at__gte=now + MIN_LEAD, starts_at__lte=now + timedelta(hours=hours)).select_related('person__user')
    sent = 0
    for appointment in due:
        notifications.notify([appointment.person.user], notifications.class_reminder(appointment, now), sync=True)
        Appointment.objects.filter(pk=appointment.pk).update(reminder_sent_at=now)
        sent += 1
    return sent


SOON_MIN_LEAD = timedelta(minutes=30)     # a class that starts in less than this is not reminded again
WINDOW_HOURS = 2                           # the daily jobs run in the first hours after their time (the command runs often)


def send_class_soon_reminders(now):
    """The short "starts soon" reminder, REMINDER_CLASS_SOON_HOURS before the class. When the day-before reminder went out
    only a moment ago (a class made for later today), this one is skipped: one message is enough."""
    hours = settings.REMINDER_CLASS_SOON_HOURS
    if hours <= 0:
        return 0
    due = Appointment.objects.filter(
        _wants('appointment', 'notify_class_reminder'),
        status=Appointment.Status.SCHEDULED, soon_reminder_sent_at__isnull=True, person__is_active=True,
        starts_at__gte=now + SOON_MIN_LEAD, starts_at__lte=now + timedelta(hours=hours)).select_related('person__user')
    sent = 0
    for appointment in due:
        just_told = appointment.reminder_sent_at and now - appointment.reminder_sent_at < timedelta(hours=hours + 1)
        if not just_told:
            notifications.notify([appointment.person.user], notifications.class_soon(appointment, now), sync=True)
            sent += 1
        Appointment.objects.filter(pk=appointment.pk).update(soon_reminder_sent_at=now)
    return sent


def _in_window(now, hour):
    """Is it the first WINDOW_HOURS hours after `hour` o'clock (local time)? A negative hour means never."""
    return hour >= 0 and hour <= timezone.localtime(now).hour < hour + WINDOW_HOURS


def reassessment_candidates(today, only_with_app=False):
    """[(student, days since the last assessment)] for the active students whose last assessment is older than
    REMINDER_REASSESSMENT_DAYS. A student who never had one is not here: there is nothing to "do again"."""
    days = settings.REMINDER_REASSESSMENT_DAYS
    if days <= 0:
        return []
    students = Person.objects.filter(is_active=True).annotate(last_assessed=Max('assessments__date')).filter(
        last_assessed__lt=today - timedelta(days=days))
    if only_with_app:
        students = students.filter(has_app=True, user__isnull=False).exclude(dashboard.INVITE_PENDING).select_related('user')
    return [(p, (today - p.last_assessed).days) for p in students]


def send_student_nudges(now, today):
    """Once a day, in the morning: the students who are overdue a new assessment, and the ones who stopped training. Each
    is told again only after a month (assessment) or a week (training), and only the ones with the switch on."""
    if not _in_window(now, settings.REMINDER_NUDGE_HOUR):
        return {'reassessment': 0, 'inactive': 0, 'birthday': 0, 'goals': 0}
    told = {'reassessment': 0, 'inactive': 0, 'birthday': 0, 'goals': 0}
    for student, days in reassessment_candidates(today, only_with_app=True):
        told['reassessment'] += notifications.notify_once(
            [student.user], notifications.reassessment_due(days), timedelta(days=30))
    told['birthday'] = send_birthday_greetings(today)
    told['goals'] = send_goal_reminders(now, today)
    inactive_days = settings.REMINDER_INACTIVE_DAYS
    if inactive_days > 0:
        for row in dashboard.inactive_students(now, days=inactive_days):
            student = row['student']
            if student.has_app and student.user is not None:
                told['inactive'] += notifications.notify_once(
                    [student.user], notifications.inactivity_nudge(row['days']), timedelta(days=inactive_days))
    return told


def send_goal_reminders(now, today):
    """Once per goal, when its deadline is DUE_SOON_DAYS away or less (and not past): the student is told a goal of theirs
    is close to its deadline."""
    due = Goal.objects.filter(
        _wants_goal(), status=Goal.Status.ACTIVE, reminded_at__isnull=True, person__is_active=True, person__has_app=True,
        target_date__gte=today, target_date__lte=today + timedelta(days=goals.DUE_SOON_DAYS)).select_related('person__user')
    sent = 0
    for goal in due:
        notifications.notify([goal.person.user], notifications.goal_due((goal.target_date - today).days), sync=True)
        Goal.objects.filter(pk=goal.pk).update(reminded_at=now)
        sent += 1
    return sent


def _wants_goal():
    return Q(person__user__notification_preference__notify_goals=True) | Q(person__user__notification_preference__isnull=True)


def send_birthday_greetings(today):
    """The studio's greeting to each active student (with the app) on their birthday, once a year."""
    sent = 0
    for row in dashboard.birthdays(today, ahead=0):
        student = row['student']
        if student.has_app and student.user is not None:
            sent += notifications.notify_once([student.user], notifications.birthday_greeting(student), timedelta(days=300))
    return sent


def digest_summary(now, today):
    """{item: how many} of what the trainer should look at today (counts only; see notifications.daily_digest)."""
    ensure_appointments(today, today, today)
    start_today = timezone.make_aware(datetime.combine(today, time.min))
    live = Appointment.objects.exclude(status__in=(Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED))
    classes = list(live.filter(starts_at__gte=start_today, starts_at__lt=start_today + timedelta(days=1)).order_by('starts_at'))
    unmarked = Appointment.objects.filter(
        status=Appointment.Status.SCHEDULED, person__is_active=True,
        starts_at__gte=start_today - timedelta(days=1), starts_at__lt=start_today).count()
    return {
        'classes': len(classes),
        'first_class': f'{timezone.localtime(classes[0].starts_at):%H:%M}' if classes else '',
        'unmarked': unmarked,
        'birthdays': len(dashboard.birthdays(today, ahead=0)),
        'goals': goals.attention(today).count(),
        'payments': len(overdue_rows(today)),
        'inactive': len(dashboard.inactive_students(now)) if settings.REMINDER_INACTIVE_DAYS > 0 else 0,
        'reassessment': len(reassessment_candidates(today)),
        'routines': len(dashboard.ending_routines(today)),
        'invites': len([r for r in dashboard.pending_invites(now) if r['days'] >= 3]),
        'trials': len(dashboard.long_trials(now)),
    }


DIGEST_REPEAT = timedelta(hours=20)


def send_daily_digest(now, today):
    """One notice to each trainer in the morning with the counts of what needs attention (nothing is sent on a quiet day)."""
    if not _in_window(now, settings.REMINDER_DIGEST_HOUR):
        return 0
    staff = notifications.staff_users()
    prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user__in=staff)}
    waiting = [u for u in staff if inbox.wants(prefs.get(u.pk), 'daily_digest') and not Notice.objects.filter(
        user=u, kind='daily_digest', created_at__gte=now - DIGEST_REPEAT).exists()]
    if not waiting:
        return 0
    summary = digest_summary(now, today)
    if not any(v for k, v in summary.items() if k != 'first_class'):
        return 0
    return notifications.notify_once(waiting, notifications.daily_digest(summary), DIGEST_REPEAT)


def send_plan_reminders(today):
    days = settings.REMINDER_PLAN_DAYS
    if days <= 0:
        return 0
    due = Person.objects.filter(
        _wants('person', 'notify_plan_expiry'), is_active=True,
        plan_expires_on__gte=today, plan_expires_on__lte=today + timedelta(days=days)
    ).exclude(plan_reminder_for=F('plan_expires_on')).select_related('user')
    sent = 0
    for person in due:
        current = packages.overview(person, today)['current']
        notifications.notify([person.user], notifications.plan_expiring(
            (person.plan_expires_on - today).days, current['left'] if current else None), sync=True)
        Person.objects.filter(pk=person.pk).update(plan_reminder_for=person.plan_expires_on)
        sent += 1
    return sent


def send_package_reminders(now, today):
    """Packages running low or expiring, each told once (`packages.alerts`): the student hears when classes run low,
    and the trainer gets one notice per run listing every package that needs renewing."""
    rows = packages.alerts(today)
    for row in rows:
        if row['low']:
            users = [p.user for p in row['people'] if p.user is not None]   # a shared package: everyone using it hears
            if users:
                notifications.notify(users, notifications.package_low(row['left']), sync=True)
    if rows:
        notifications.notify(notifications.staff_users(), notifications.package_attention(rows, today), sync=True)
    for row in rows:
        packages.mark_alerted(row, now)
    return len(rows)


BACKUP_REPEAT = timedelta(hours=24)


def send_backup_alert(now):
    """Tell the trainer the backups are not happening (core/backup_status.py), at most once a day while it lasts: the
    dashboard shows it too, but nobody opens the dashboard every day."""
    if backup_status.problem(now) is None:
        return 0
    return notifications.notify_once(notifications.staff_users(), notifications.backup_problem(now), BACKUP_REPEAT)


def run(now=None):
    """Create the classes of the next days (fixed times are generated lazily), send what is due and end the workouts
    students left running."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    ensure_appointments(today, today + timedelta(days=3), today)
    inbox.prune(now)
    nudges = send_student_nudges(now, today)
    return {'classes': send_class_reminders(now), 'soon': send_class_soon_reminders(now), 'plans': send_plan_reminders(today),
            'packages': send_package_reminders(now, today), 'backup': send_backup_alert(now),
            'digest': send_daily_digest(now, today), 'reassessment': nudges['reassessment'], 'inactive': nudges['inactive'],
            'birthday': nudges['birthday'], 'goals': nudges['goals'],
            'sessions': workout_sessions.close_stale(now=now)}
