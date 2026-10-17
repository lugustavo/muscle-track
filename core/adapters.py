from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.utils.translation import gettext as _


class AccountAdapter(DefaultAccountAdapter):
    """Students are created by the trainer (invite link); open sign-up is opt-in."""

    def is_open_for_signup(self, request):
        return settings.ALLOW_PUBLIC_SIGNUP


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Single sign-on for an invite-only app: it never creates an account.

    - an account already connected to the provider signs in;
    - someone already signed in can connect a provider (Minha conta → Contas conectadas);
    - otherwise the provider must vouch for the e-mail (verified) and an account with that e-mail must exist: then it
      is connected and signed in. Anything else is sent back to the login with an explanation."""

    def is_open_for_signup(self, request, sociallogin):
        return False

    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing or request.user.is_authenticated:
            return
        user = self._existing_user(sociallogin)
        if user is not None:
            sociallogin.connect(request, user)
            return
        messages.error(request, _('Não encontramos um acesso para esta conta. Peça um convite à sua personal, ou entre '
                                  'com e-mail e senha e conecte a conta em "Contas conectadas".'))
        raise ImmediateHttpResponse(redirect('account_login'))

    @staticmethod
    def _existing_user(sociallogin):
        """The active local user whose e-mail the provider verified (None when it did not, or nobody matches)."""
        users = get_user_model().objects
        for address in sociallogin.email_addresses:
            if not (address.verified and address.email):
                continue
            user = users.filter(email__iexact=address.email, is_active=True).first()
            if user is not None:
                return user
        return None
