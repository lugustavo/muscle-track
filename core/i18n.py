"""Language handling: which language a request, a person or a notification uses.

A request's language is, in this order: the language cookie (set by the switcher: a deliberate choice), then the
browser's `Accept-Language` (what the person's browser or phone is set to), then the site default (pt-br). Django's
LocaleMiddleware does that.

Notifications, e-mails and WhatsApp texts are built outside a request, so they use the *recipient's* saved language
(`language_for`), run inside `translation.override`. It is saved when the person picks a language in the switcher and
refreshed at every sign-in with the language that device is using."""
from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import translation
from django.utils.functional import lazy
from django.views.i18n import set_language as django_set_language

SUPPORTED = [code for code, _name in settings.LANGUAGES]


def clean(code):
    """The supported language code for `code` (e.g. 'en-GB' -> 'en', 'pt' -> 'pt-br'), or '' when there is none."""
    try:
        return translation.get_supported_language_variant(code) if code else ''
    except LookupError:
        return ''


def saved_language(user):
    """The language saved for a user ('' when none)."""
    if not getattr(user, 'is_authenticated', False):
        return ''
    from .models import NotificationPreference
    return clean(NotificationPreference.objects.filter(user=user).values_list('language', flat=True).first() or '')


def language_for(user):
    """The language to write to `user` in: the saved one, else the site default."""
    return saved_language(user) or settings.LANGUAGE_CODE


def save_language(user, code):
    """Remember `code` as the user's language (it also sets the language of what is sent to them)."""
    code = clean(code)
    if not code or not getattr(user, 'is_authenticated', False):
        return
    from .models import NotificationPreference
    preference, _ = NotificationPreference.objects.get_or_create(user=user)
    if preference.language != code:
        preference.language = code
        preference.save(update_fields=['language'])


@receiver(user_logged_in)
def learn_language_at_login(sender, request, user, **kwargs):
    """Sign-in: keep the language this browser is using, so what is sent to the person later follows it."""
    if request is not None:
        save_language(user, getattr(request, 'LANGUAGE_CODE', '') or translation.get_language())


def set_language(request):
    """The switcher's endpoint: Django's view sets the cookie; a signed-in person's choice is saved too."""
    response = django_set_language(request)
    if request.method == 'POST' and request.user.is_authenticated:
        save_language(request.user, request.POST.get('language', ''))
    return response


def lazy_text(build):
    """A text that is only built when it is shown, in the language active *then* (see `translation.override`).

    `build` is a no-argument function returning the string, e.g. `lazy_text(lambda: _('Olá, %(n)s') % {'n': name})`.
    Notifications use it so one message can be written in each recipient's own language."""
    return lazy(build, str)()
