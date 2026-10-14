"""Student invitation links.

The trainer registers a student (account without a password) and sends them a
one-time link. Opening it lets the student choose a password and log in. The
token is Django's password-reset token: it is bound to the user's current
password hash, so it dies as soon as it is used, and it expires after
PASSWORD_RESET_TIMEOUT. Generating a new link for the same student therefore
doubles as "reset access". The token also carries Person.invite_version, so the
trainer can revoke links already sent ("Revogar e gerar novo link").
"""
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode


class InviteTokenGenerator(PasswordResetTokenGenerator):
    key_salt = 'core.invites.InviteTokenGenerator'

    def _make_hash_value(self, user, timestamp):
        person = getattr(user, 'person', None)
        version = person.invite_version if person else 0
        return f'{super()._make_hash_value(user, timestamp)}{version}'


default_token_generator = InviteTokenGenerator()


def revoke_invites(person):
    """Invalidate every invite link already generated for this student."""
    person.invite_version += 1
    person.save(update_fields=['invite_version'])


def build_invite_path(user):
    return reverse('core:accept_invite', kwargs={
        'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
        'token': default_token_generator.make_token(user),
    })


def build_invite_url(request, user):
    path = build_invite_path(user)
    base = settings.SITE_URL.rstrip('/') if settings.SITE_URL else None
    return f'{base}{path}' if base else request.build_absolute_uri(path)


def get_user_from_invite(uidb64, token):
    """Return the invited student's User, or None if the link is invalid/expired/used."""
    try:
        user = User.objects.select_related('person').get(pk=urlsafe_base64_decode(uidb64).decode())
    except (User.DoesNotExist, ValueError, TypeError, OverflowError, UnicodeDecodeError):
        return None
    # Only students can be invited; never staff accounts
    if user.is_staff or user.is_superuser or not hasattr(user, 'person'):
        return None
    if not default_token_generator.check_token(user, token):
        return None
    return user
