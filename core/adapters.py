from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings


class AccountAdapter(DefaultAccountAdapter):
    """Students are created by the trainer (invite link); open sign-up is opt-in."""

    def is_open_for_signup(self, request):
        return settings.ALLOW_PUBLIC_SIGNUP
