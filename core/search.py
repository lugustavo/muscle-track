"""Global search for the trainer: students, exercises and routine templates in one place.

Results carry only what is needed to recognise and open the item. Confidential or internal fields
(notes, health notes, hourly rate, plan price: see core/history.py) are neither searched nor shown."""
from django.db.models import F, Q, Value
from django.db.models.functions import Replace
from django.urls import reverse
from django.utils.translation import gettext as _
from urllib.parse import urlencode

from .models import Exercise, Person, Routine
from .validators import digits_only

MIN_LENGTH = 2
MAX_LENGTH = 80


def phone_digits(expr):
    """The phone number without mask characters, to match '11987654321' against '(11) 98765-4321'."""
    for ch in '()-+. ':
        expr = Replace(expr, Value(ch), Value(''))
    return expr


def apply_student_search(students, search):
    """Students matching name, e-mail, phone (with or without mask / country code) or '#id'.

    `students` must be annotated with `phone_digits`."""
    search = search.strip()
    if not search:
        return students
    if search.startswith('#') and search[1:].isdigit():
        return students.filter(pk=int(search[1:]))
    query = (Q(person_name__icontains=search) | Q(user__email__icontains=search)
             | Q(phone__icontains=search))
    digits = digits_only(search)
    if len(digits) >= 3:
        query |= Q(phone_digits__icontains=digits)
        if digits.startswith('55') and len(digits) >= 5:
            query |= Q(phone_digits__icontains=digits[2:])  # typed with the country code
    return students.filter(query)


def clean_query(raw):
    return ' '.join((raw or '').split())[:MAX_LENGTH]


def search_all(query, limit=8):
    """``{'query', 'groups': [{key, label, icon, total, more_url, items: [{title, subtitle, url, badge}]}]}``.

    One query for the total and one for the rows of each group; nothing per row."""
    query = clean_query(query)
    if len(query) < MIN_LENGTH and not (query.startswith('#') and len(query) > 1):
        return {'query': query, 'groups': [], 'too_short': bool(query)}

    students = apply_student_search(
        Person.objects.select_related('user').annotate(phone_digits=phone_digits(F('phone'))), query
    ).order_by('-is_active', 'person_name', 'pk')
    exercises = Exercise.objects.filter(is_active=True).filter(
        Q(exercise_name__icontains=query) | Q(description__icontains=query)
    ).select_related('muscle_group').order_by('exercise_name', 'pk')
    routines = Routine.objects.filter(
        Q(routine_name__icontains=query) | Q(description__icontains=query)
    ).order_by('routine_name', 'pk')

    more = '?' + urlencode({'search': query})
    groups = []
    rows = list(students[:limit])
    groups.append({
        'key': 'students', 'label': _('Alunos'), 'icon': 'bi-people', 'total': students.count() if len(rows) == limit else len(rows),
        'more_url': reverse('core:student_list') + more,
        'items': [{
            'title': s.person_name,
            'subtitle': ' · '.join(x for x in (s.phone, s.user.email) if x),
            'badge': s.get_status_display(),
            'url': reverse('core:student_detail', kwargs={'pk': s.pk}),
        } for s in rows],
    })
    rows = list(exercises[:limit])
    groups.append({
        'key': 'exercises', 'label': _('Exercícios'), 'icon': 'bi-activity', 'total': exercises.count() if len(rows) == limit else len(rows),
        'more_url': reverse('core:exercise_list') + more,
        'items': [{
            'title': e.exercise_name, 'subtitle': e.muscle_group.name, 'badge': '',
            'url': reverse('core:exercise_detail', kwargs={'pk': e.pk}),
        } for e in rows],
    })
    rows = list(routines[:limit])
    groups.append({
        'key': 'routines', 'label': _('Treinos'), 'icon': 'bi-calendar-check', 'total': routines.count() if len(rows) == limit else len(rows),
        'more_url': reverse('core:routine_list') + more,
        'items': [{
            'title': r.routine_name, 'subtitle': r.get_difficulty_level_display(),
            'badge': '' if r.is_active else _('Inativo'),
            'url': reverse('core:routine_detail', kwargs={'pk': r.pk}),
        } for r in rows],
    })
    return {'query': query, 'groups': groups, 'too_short': False}
