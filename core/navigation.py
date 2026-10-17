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
from django.utils.translation import gettext as _, gettext_lazy as _l

MAX_NEXT_LENGTH = 1000

# url name -> label for a Back button that leads to that page
BACK_LABELS = {
    'home': _l('Voltar ao painel'),
    'student_list': _l('Voltar aos alunos'),
    'student_detail': _l('Voltar ao aluno'),
    'student_history': _l('Voltar ao histórico do aluno'),
    'student_invite': _l('Voltar ao convite'),
    'agenda': _l('Voltar à agenda'),
    'agenda_holidays': _l('Voltar aos feriados'),
    'finance': _l('Voltar ao financeiro'),
    'finance_refunds': _l('Voltar às devoluções'),
    'plan_list': _l('Voltar aos planos'),
    'exercise_list': _l('Voltar aos exercícios'),
    'exercise_detail': _l('Voltar ao exercício'),
    'routine_list': _l('Voltar aos treinos'),
    'routine_detail': _l('Voltar ao treino'),
    'person_routine_detail': _l('Voltar ao progresso do treino'),
    'session_detail': _l('Voltar à sessão'),
    'my_workouts': _l('Voltar aos treinos'),
    'workout_execute': _l('Voltar ao treino em andamento'),
    'my_history': _l('Voltar ao histórico'),
    'my_evolution': _l('Voltar à minha evolução'),
    'exercise_progress': _l('Voltar à evolução do exercício'),
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


def back_label(url, default=None):
    if default is None:
        default = _('Voltar')
    try:
        match = resolve(urlsplit(url).path)
    except Resolver404:
        return default
    return str(BACK_LABELS.get(match.url_name, default))


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
