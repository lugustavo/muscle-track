"""Automatic reminders, sent only to people who turned a channel on and kept that kind of reminder on.

`run()` is idempotent and meant to be called every few minutes (management command `send_reminders`, run by the
`scheduler` service of docker-compose): each class and each plan expiry is reminded once (`Appointment.reminder_sent_at`,
`Person.plan_reminder_for`)."""
from datetime import timedelta

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from . import notifications
from .agenda import ensure_appointments
from .models import Appointment, Person

# A class that starts in less than this is not reminded any more (it is too late to be useful)
MIN_LEAD = timedelta(hours=1)


def _wants(prefix, flag):
    """Filter on the person's account preferences: a channel on and the given per-type switch on."""
    base = 'user__notification_preference__' if prefix == 'person' else 'person__user__notification_preference__'
    return Q(**{f'{base}{flag}': True}) & (Q(**{f'{base}email_enabled': True}) | Q(**{f'{base}push_enabled': True}))


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
        notifications.notify([person.user], notifications.plan_expiring((person.plan_expires_on - today).days), sync=True)
        Person.objects.filter(pk=person.pk).update(plan_reminder_for=person.plan_expires_on)
        sent += 1
    return sent


def run(now=None):
    """Create the classes of the next days (fixed times are generated lazily) and send what is due."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    ensure_appointments(today, today + timedelta(days=3), today)
    return {'classes': send_class_reminders(now), 'plans': send_plan_reminders(today)}
