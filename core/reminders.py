"""Automatic reminders: every student (and the trainer) who kept that kind of reminder on gets it in the app's notice
centre, and also by e-mail, push or Telegram on the channels they turned on.

`run()` is idempotent and meant to be called every few minutes (management command `send_reminders`, run by the
`scheduler` service of docker-compose): each class, each plan expiry and each package alert is told once (`Appointment.reminder_sent_at`,
`Person.plan_reminder_for`, `StudentPackage.low_notified_at` and friends)."""
from datetime import timedelta

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from . import inbox, notifications, packages, workout_sessions
from .agenda import ensure_appointments
from .models import Appointment, Person

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


def run(now=None):
    """Create the classes of the next days (fixed times are generated lazily), send what is due and end the workouts
    students left running."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    ensure_appointments(today, today + timedelta(days=3), today)
    inbox.prune(now)
    return {'classes': send_class_reminders(now), 'plans': send_plan_reminders(today),
            'packages': send_package_reminders(now, today),
            'sessions': workout_sessions.close_stale(now=now)}
