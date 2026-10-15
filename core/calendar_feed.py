"""Calendar link (iCalendar / .ics) for Google Agenda, Apple Calendar and Outlook.

The trainer gets every class (with the student named as she chooses); a student gets only his own classes, with
no notes and nothing about other students. The feed is plain text (RFC 5545): times in UTC, one stable UID per
class so the calendar app updates a class instead of duplicating it, and cancelled / rescheduled classes are sent
as STATUS:CANCELLED so they disappear from the calendar."""
import secrets
from datetime import time, timedelta
from datetime import timezone as dt_timezone
from urllib.parse import quote, urlencode

from django.conf import settings
from django.utils import timezone

from .agenda import at, ensure_appointments
from .models import Appointment, CalendarFeed, Person

PAST_DAYS = 14        # same window the agenda itself fills in backwards
FUTURE_DAYS = 120
UPDATE_STAMP_EVERY = timedelta(minutes=1)   # "last fetched" is written at most this often
CANCELLED = (Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED)


def new_token():
    return secrets.token_urlsafe(32)


def get_or_create_feed(user):
    feed = CalendarFeed.objects.filter(user=user).first()
    if feed is None:
        feed = CalendarFeed.objects.create(user=user, token=new_token())
    return feed


def rotate(feed):
    """A new link; the old one stops working immediately."""
    feed.token = new_token()
    feed.rotated_at = timezone.now()
    feed.last_fetched_at = None
    feed.save(update_fields=['token', 'rotated_at', 'last_fetched_at'])
    return feed


def feed_for_token(token):
    """The feed of an active account, or None (unknown token, deactivated student, trainer no longer staff)."""
    feed = CalendarFeed.objects.select_related('user').filter(token=token).first()
    if feed is None or not feed.user.is_active:
        return None
    if feed.user.is_staff or feed.user.is_superuser:
        return feed
    person = Person.objects.filter(user=feed.user).first()
    return feed if person is not None and person.is_active else None


# ---- iCalendar text ----------------------------------------------------------------------------------------

def escape(text):
    return (text.replace('\\', '\\\\').replace(';', '\;').replace(',', '\\,')
            .replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', '\\n'))


def fold(line):
    """Lines are at most 75 octets: longer ones continue on the next line, which starts with a space."""
    raw = line.encode('utf-8')
    if len(raw) <= 75:
        return line
    parts, current, size = [], '', 0
    for char in line:
        width = len(char.encode('utf-8'))
        limit = 75 if not parts else 74          # a continuation line spends one octet on the leading space
        if size + width > limit:
            parts.append(current)
            current, size = '', 0
        current += char
        size += width
    parts.append(current)
    return '\r\n '.join(parts)


def utc(moment):
    return moment.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def student_label(person, style):
    if style == 'full':
        return person.person_name
    if style == 'first':
        return (person.person_name.split() or [''])[0]
    return ''


def trainer_title(appointment, style):
    kind = appointment.get_kind_display()
    label = student_label(appointment.person, style)
    return f'{kind} · {label}' if label else f'{kind} {timezone.localtime(appointment.starts_at):%H:%M}'


def student_title(appointment):
    return 'Aula de treino' if appointment.kind == Appointment.Kind.REGULAR else f'Aula de treino ({appointment.get_kind_display().lower()})'


def event_lines(appointment, summary, description, now, host):
    stamp = appointment.status_changed_at
    lines = [
        'BEGIN:VEVENT',
        f'UID:appointment-{appointment.pk}@{host}',
        f'DTSTAMP:{utc(now)}',
        f'DTSTART:{utc(appointment.starts_at)}',
        f'DTEND:{utc(appointment.ends_at)}',
        f'SUMMARY:{escape(summary)}',
        f'SEQUENCE:{int(stamp.timestamp()) if stamp else 0}',
        'STATUS:CANCELLED' if appointment.status in CANCELLED else 'STATUS:CONFIRMED',
        'TRANSP:OPAQUE',
    ]
    if stamp:
        lines.append(f'LAST-MODIFIED:{utc(stamp)}')
    if description:
        lines.append(f'DESCRIPTION:{escape(description)}')
    if getattr(settings, 'CALENDAR_LOCATION', ''):
        lines.append(f'LOCATION:{escape(settings.CALENDAR_LOCATION)}')
    lines.append('END:VEVENT')
    return lines


def build_ics(feed, host, now=None, site_url=''):
    """The whole calendar as text (CRLF line ends, folded lines)."""
    now = now or timezone.now()
    today = timezone.localdate()
    start, end = today - timedelta(days=PAST_DAYS), today + timedelta(days=FUTURE_DAYS)
    ensure_appointments(start, end, today)   # fixed times become concrete classes, as when the agenda is opened

    queryset = Appointment.objects.filter(
        starts_at__gte=at(start, time.min), starts_at__lt=at(end + timedelta(days=1), time.min)).select_related('person')
    trainer = feed.user.is_staff or feed.user.is_superuser
    if trainer:
        name = 'Muscle Track · Agenda'
    else:
        name = 'Minhas aulas'
        queryset = queryset.filter(person__user=feed.user)

    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Muscle Track//Agenda//PT-BR', 'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH', f'X-WR-CALNAME:{escape(name)}', f'X-WR-TIMEZONE:{settings.TIME_ZONE}',
        'REFRESH-INTERVAL;VALUE=DURATION:PT1H', 'X-PUBLISHED-TTL:PT1H',
    ]
    for appointment in queryset:
        if trainer:
            summary = trainer_title(appointment, feed.name_style)
            description = f'{appointment.duration_minutes} min'
            if appointment.notes:
                description += f'\n{appointment.notes}'
            if site_url:
                description += f'\nFicha do aluno: {site_url}/students/{appointment.person_id}/'
        else:
            summary = student_title(appointment)
            description = f'{appointment.duration_minutes} min'
        lines += event_lines(appointment, summary, description, now, host)
    lines.append('END:VCALENDAR')
    return '\r\n'.join(fold(line) for line in lines) + '\r\n'


def google_template_url(appointment, summary, details=''):
    """"Add to Google Calendar" link for one class (opens Google's prefilled event form)."""
    params = {
        'action': 'TEMPLATE', 'text': summary,
        'dates': f'{utc(appointment.starts_at)}/{utc(appointment.ends_at)}',
        'details': details, 'ctz': settings.TIME_ZONE,
    }
    if getattr(settings, 'CALENDAR_LOCATION', ''):
        params['location'] = settings.CALENDAR_LOCATION
    return 'https://calendar.google.com/calendar/render?' + urlencode(params, quote_via=quote)


def mark_fetched(feed, now=None):
    now = now or timezone.now()
    if feed.last_fetched_at is None or now - feed.last_fetched_at > UPDATE_STAMP_EVERY:
        CalendarFeed.objects.filter(pk=feed.pk).update(last_fetched_at=now)
