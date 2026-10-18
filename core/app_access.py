"""Students with and without the app.

A student the trainer follows in person may have no login at all: the file is only hers. The student keeps a login
account row (everything hangs from `Person.user`), but a dormant one: no usable password, `is_active=False`, so nobody
can sign in or reset the password, and every notification (they only reach active accounts) skips it. Turning the app on
gives the account its e-mail and an invite link; turning it off blocks it again. Nothing of the student's data is touched.
"""
import uuid

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils.translation import gettext as _

from .history import log_event
from .invites import revoke_invites
from .models import StudentEvent

LOCAL_PREFIX = 'local-'


def new_account(email, has_app):
    """The login account of a new student: with the app, a normal one the invite link will open (no password yet);
    without it, a dormant one (the e-mail, when given, is only kept on the file)."""
    user = User(username=email if has_app else f'{LOCAL_PREFIX}{uuid.uuid4().hex[:16]}', email=email, is_active=has_app)
    user.set_unusable_password()
    user.save()
    if has_app:
        EmailAddress.objects.create(user=user, email=email, primary=True, verified=False)
    return user


def email_in_use(email, exclude_user=None):
    found = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
    return found.exclude(pk=exclude_user.pk) if exclude_user is not None else found


def apply(person, email, was_app, actor=None):
    """Make the login account follow `person.has_app` and the e-mail after the student was saved. Returns True when
    the app was just turned on (the trainer then needs the invite link)."""
    user = person.user
    email = (email or '').strip().lower()
    if person.has_app:
        user.email = email
        user.username = email
        user.is_active = person.is_active
        user.save()
        address = EmailAddress.objects.filter(user=user, primary=True).first()
        if address is None:
            EmailAddress.objects.create(user=user, email=email, primary=True, verified=False)
        elif address.email != email:
            address.email, address.verified = email, False
            address.save()
        if not was_app:
            revoke_invites(person)
            log_event(person, StudentEvent.Kind.APP_ACCESS, actor, _('App ativado: o aluno passa a ter login'))
        return not was_app
    user.email = email
    if not user.username.startswith(LOCAL_PREFIX):
        user.username = f'{LOCAL_PREFIX}{uuid.uuid4().hex[:16]}'
    user.is_active = False
    user.save()
    EmailAddress.objects.filter(user=user).delete()
    if was_app:
        revoke_invites(person)
        log_event(person, StudentEvent.Kind.APP_ACCESS, actor, _('App desativado: o aluno só tem ficha com você'))
    return False


def access_state(person):
    """'none' (no app), 'invited' (waiting for the student to choose a password) or 'active'."""
    if not person.has_app:
        return 'none'
    return 'active' if person.user.has_usable_password() else 'invited'
