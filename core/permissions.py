from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from urllib.parse import urlencode

from .consent import has_current_consent
from .models import Person


def is_trainer(user):
    """The trainer (admin area) is any active staff/superuser account."""
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def staff_required(view_func):
    """Admin area: anonymous -> login page, logged non-staff -> 403."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_trainer(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper


def student_required(view_func=None, *, consent_gate=True):
    """Student area: injects `person` (the logged user's Person) as a kwarg.

    With `consent_gate` (default) the student is sent to the consent screen until the
    required LGPD consents of the current version are on record. JSON endpoints used
    mid-workout and the privacy/consent pages opt out so they never get redirected."""
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            try:
                person = request.user.person
            except Person.DoesNotExist:
                messages.warning(request, 'Você não está cadastrado como aluno.')
                return redirect('core:home')
            if consent_gate and not has_current_consent(person):
                return redirect(f"{reverse('core:consent')}?{urlencode({'next': request.get_full_path()}, safe='/')}")
            return func(request, *args, person=person, **kwargs)
        return wrapper
    return decorator(view_func) if view_func is not None else decorator
