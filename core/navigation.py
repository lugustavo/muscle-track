"""Helpers for "go back to where I came from" links.

Links that lead to a detail page carry ``?next=<current path>``; the detail page
turns it into its Back button. ``next`` is user input, so only local paths are
accepted - anything else falls back to the page's default.
"""
from urllib.parse import urlsplit

from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme

# url name -> label for the Back button
BACK_LABELS = {
    'home': 'Voltar ao painel',
    'person_routine_detail': 'Voltar ao progresso do treino',
    'student_detail': 'Voltar ao aluno',
    'student_history': 'Voltar ao histórico do aluno',
    'my_history': 'Voltar ao histórico',
}


def safe_next_url(request, default):
    """The local path in ``?next=`` if there is one, else ``default``."""
    target = request.GET.get('next', '')
    if (
        target.startswith('/')
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
