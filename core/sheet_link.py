"""The student's workout sheet as a read-only page behind a private link.

For a student who does not use the app (core/app_access.py), or to hand any student a list to read on the phone or print:
the exercises of the routines in use, with sets, reps, rest, the trainer's notes and how to do each one. The page needs
no login (the long random token is the credential) and shows nothing but the sheet: no health notes, no internal notes,
no contact data, no prices, no history; the student appears by first name only."""
import secrets
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from . import credits
from .history import log_event
from .models import SheetLink, StudentEvent
from .whatsapp_messages import language_of

VIEW_STAMP_EVERY = timedelta(minutes=1)   # "last viewed" is written at most this often


def new_token():
    return secrets.token_urlsafe(32)


def enable(person, actor=None):
    """The student's link (created when there is none)."""
    link = SheetLink.objects.filter(person=person).first()
    if link is None:
        link = SheetLink.objects.create(person=person, token=new_token())
        log_event(person, StudentEvent.Kind.SHEET_LINK, actor, _('Link da ficha de treino criado'))
    return link


def rotate(link, actor=None):
    """A new link; the old one stops working immediately."""
    link.token = new_token()
    link.rotated_at = timezone.now()
    link.last_viewed_at = None
    link.save(update_fields=['token', 'rotated_at', 'last_viewed_at'])
    log_event(link.person, StudentEvent.Kind.SHEET_LINK, actor, _('Link da ficha de treino gerado de novo'))
    return link


def disable(person, actor=None):
    removed, _details = SheetLink.objects.filter(person=person).delete()
    if removed:
        log_event(person, StudentEvent.Kind.SHEET_LINK, actor, _('Link da ficha de treino desativado'))
    return bool(removed)


def link_for_token(token):
    """The link of an active student, or None (unknown token, or a paused / former student)."""
    link = SheetLink.objects.select_related('person').filter(token=token).first()
    if link is None or not link.person.is_active:
        return None
    return link


def mark_viewed(link, now=None):
    now = now or timezone.now()
    if link.last_viewed_at is None or now - link.last_viewed_at >= VIEW_STAMP_EVERY:
        SheetLink.objects.filter(pk=link.pk).update(last_viewed_at=now)


def public_path(link):
    return reverse('core:public_sheet', kwargs={'token': link.token})


def sheet(person):
    """What the page shows: `{'first_name', 'routines': [{'routine', 'exercises': [...]}]}`; the routines in use, each
    exercise with its numbers, notes and a picture / video link when it has one."""
    routines = []
    queryset = person.routines.filter(is_active=True).order_by('routine_date', 'pk').prefetch_related(
        'exercises__exercise__muscle_group')
    for routine in queryset:
        exercises = []
        for item in routine.exercises.all():
            exercise = item.exercise
            exercises.append({
                'order': item.order, 'name': exercise.exercise_name, 'group': exercise.muscle_group.label,
                'sets': item.sets, 'reps': item.reps, 'rest_seconds': item.rest_seconds, 'notes': item.notes,
                'instructions': exercise.instructions or exercise.description,
                'image': exercise.thumbnail.url if exercise.thumbnail else '',
                'credit': credits.sheet_credit(exercise),
                'video': (exercise.media_info or {}).get('url', ''),
            })
        routines.append({'routine': routine, 'exercises': exercises})
    names = (person.person_name or '').split()
    return {'first_name': names[0] if names else '', 'routines': routines}


def whatsapp_text(person, url):
    """The message that goes with the link, in the student's own language."""
    with translation.override(language_of(person)):
        first = (person.person_name or '').split()
        return _('Olá, %(name)s! Aqui está a sua ficha de treino, para ler no celular ou imprimir: %(link)s') % {
            'name': first[0] if first else '', 'link': url}
