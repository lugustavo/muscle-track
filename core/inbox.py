"""The notice centre: the bell in the top bar and the page behind it.

Every notification the app sends (core/notifications.py `notify`) is also kept here for the person, whatever channels they
turned on: the app is always a place to be told things. The per-type switches (`notify_changes`, `notify_class_reminder`...)
still apply; someone who never touched their settings gets everything."""
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone, translation

from .i18n import language_for
from .models import Notice, NotificationPreference

KEEP_DAYS = 90      # older notices are dropped
KEEP_LAST = 100     # and no one keeps more than this many
PAGE_SIZE = 50


def wants(pref, kind):
    """Does this person (their NotificationPreference or None) want notices of this kind?"""
    from .notifications import KIND_FLAG
    flag = KIND_FLAG.get(kind)
    return pref is None or not flag or getattr(pref, flag)


def add(users, message):
    """Keep `message` for each active user who wants that kind. Returns how many notices were written."""
    people = [u for u in users if u is not None and u.is_active]
    prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user__in=people)}
    written = 0
    for user in people:
        pref = prefs.get(user.pk)
        if not wants(pref, message.kind):
            continue
        # written in the person's own language, like what goes out by push or e-mail
        with translation.override((pref.language if pref and pref.language else '') or language_for(user)):
            title, body = str(message.title), str(message.body)
        Notice.objects.create(user=user, kind=message.kind, title=title[:120], body=body[:400], path=message.path or '')
        written += 1
    return written


def unread_count(user):
    return Notice.objects.filter(user=user, read_at__isnull=True).count() if user.is_authenticated else 0


def recent(user, limit=PAGE_SIZE):
    return list(Notice.objects.filter(user=user)[:limit])


def mark_read(user, pk=None, now=None):
    """Mark one notice (or every unread one when `pk` is None) as read. Returns the notice that was asked for, if any."""
    now = now or timezone.now()
    unread = Notice.objects.filter(user=user, read_at__isnull=True)
    if pk is None:
        unread.update(read_at=now)
        return None
    unread.filter(pk=pk).update(read_at=now)
    return Notice.objects.filter(user=user, pk=pk).first()


def prune(now=None):
    """Drop what is old or beyond the last KEEP_LAST of each person. Safe to run often (the reminders command does)."""
    now = now or timezone.now()
    dropped = Notice.objects.filter(created_at__lt=now - timedelta(days=KEEP_DAYS)).delete()[0]
    crowded = (Notice.objects.values('user').order_by().annotate(n=Count('pk'))
               .filter(n__gt=KEEP_LAST).values_list('user', flat=True))
    for user_id in list(crowded):
        keep = Notice.objects.filter(user_id=user_id).values_list('pk', flat=True)[:KEEP_LAST]
        dropped += Notice.objects.filter(user_id=user_id).exclude(pk__in=list(keep)).delete()[0]
    return dropped
