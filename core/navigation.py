"""Navigation conventions: "Back" always returns to where the user came from.

1. Links to a detail/form page carry ``?next=<current page, query included>``
   (template tag ``{% nav_url %}``).
2. Back/Cancel buttons use ``next`` when it is valid, otherwise the page's default
   (template tag ``{% back_button %}``); the label comes from the destination page.
3. After saving/deleting, we go to the entity; if ``next`` is that same entity we
   return to ``next`` itself, otherwise we forward ``next`` so the entity's own Back
   button still leads to the origin (``after_save_url``).

``next`` is user input, so only local paths are accepted.
"""
from urllib.parse import urlencode, urlsplit

from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme

MAX_NEXT_LENGTH = 1000

# url name -> label for a Back button that leads to that page
BACK_LABELS = {
    'home': 'Voltar ao painel',
    'student_list': 'Voltar aos alunos',
    'student_detail': 'Voltar ao aluno',
    'student_history': 'Voltar ao histórico do aluno',
    'student_invite': 'Voltar ao convite',
    'agenda': 'Voltar à agenda',
    'agenda_holidays': 'Voltar aos feriados',
    'finance': 'Voltar ao financeiro',
    'plan_list': 'Voltar aos planos',
    'exercise_list': 'Voltar aos exercícios',
    'exercise_detail': 'Voltar ao exercício',
    'routine_list': 'Voltar aos treinos',
    'routine_detail': 'Voltar ao treino',
    'person_routine_detail': 'Voltar ao progresso do treino',
    'session_detail': 'Voltar à sessão',
    'my_workouts': 'Voltar aos treinos',
    'workout_execute': 'Voltar ao treino em andamento',
    'my_history': 'Voltar ao histórico',
    'exercise_progress': 'Voltar à evolução do exercício',
}


def safe_next_url(request, default):
    """The local path in ``?next=`` if there is one, else ``default``."""
    target = request.GET.get('next', '')
    if (
        target.startswith('/')
        and len(target) <= MAX_NEXT_LENGTH
        and not target.startswith('//')
        and '\\' not in target
        and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    ):
        return target
    return default


def back_label(url, default='Voltar'):
    try:
        match = resolve(urlsplit(url).path)
    except Resolver404:
        return default
    return BACK_LABELS.get(match.url_name, default)


def with_next(url, next_url):
    """``url`` with ``?next=<next_url>`` appended (keeping an existing query string)."""
    separator = '&' if '?' in url else '?'
    return f"{url}{separator}{urlencode({'next': next_url}, safe='/')}"


def after_save_url(request, target, forward_next=True, fragment=''):
    """Where to go after saving/deleting something whose page is ``target``.

    - no valid ``next``: ``target``;
    - ``next`` is ``target`` itself (we came from there): ``next`` as it was, so the
      page we return to keeps its own origin;
    - otherwise ``target`` with ``next`` forwarded (``forward_next``) so its Back
      button still leads to the origin. Lists do not need it (``forward_next=False``).
    """
    next_url = safe_next_url(request, None)
    if not next_url:
        return target + fragment
    if urlsplit(next_url).path == urlsplit(target).path:
        return next_url + fragment
    if forward_next:
        return with_next(target, next_url) + fragment
    return target + fragment
