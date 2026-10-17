"""Template context: the bottom navigation bar (items per role, the active one, badges, the central workout button)
and the branding / single sign-on options every page shares."""
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from . import packages
from .class_requests import pending_for_trainer
from .models import Appointment, Person, StudentEvent
from .navigation import with_next
from .today import open_row, rows_for, start_of_day, suggested_row

# Pages without the bar (and without the top hamburger either): the student must decide first
HIDDEN_ON = {'consent'}

STAFF_SECTIONS = (
    ('busca', ('search',)),   # no bottom-bar item: none is shown as active
    ('alunos', ('student_', 'assessment_', 'photo_', 'note_', 'person_routine_', 'session_detail', 'invite')),
    ('agenda', ('agenda', 'appointment_', 'slot_')),
    ('financeiro', ('finance', 'payment_', 'plan_', 'refund')),
)
STUDENT_SECTIONS = (
    ('center', ('workout_execute',)),   # the running workout: the central button is the active one
    ('hoje', ('my_agenda',)),   # reached from "Hoje"; the bar has no slot of its own for it
    ('treinos', ('my_workouts', 'workout_')),
    ('historico', ('my_history', 'my_exercise_progress')),
    ('perfil', ('my_profile', 'my_privacy', 'notification_', 'account_', 'socialaccount_')),   # account_* = password, e-mail (allauth)
)


# Pages of the trainer area where the floating "+" button shows up (phones only)
FAB_PAGES = {'home', 'student_list', 'student_detail', 'agenda', 'finance'}


def _section(name, sections, home='hoje', fallback=''):
    if name == 'home':
        return home
    for key, prefixes in sections:
        if name.startswith(prefixes):
            return key
    return fallback


def _staff_badges():
    now = timezone.now()
    # a package that expired with classes left (to extend) + packages waiting for a payment + money to give back
    overdue = Person.objects.filter(is_active=True, plan_expires_on__lt=timezone.localdate()).count() + packages.pending_count() + packages.refund_due_count()
    erasures = StudentEvent.objects.filter(kind=StudentEvent.Kind.ERASURE_REQUEST).values('person').distinct().count()
    unmarked = Appointment.objects.filter(
        status=Appointment.Status.SCHEDULED, starts_at__gte=start_of_day(timezone.localdate()), starts_at__lt=now
    ).count()
    asked = pending_for_trainer().count()
    return {'hoje': overdue + erasures, 'agenda': unmarked + asked}


def _staff_bar(active):
    badges = _staff_badges()
    items = [
        {'key': 'hoje', 'label': _('Hoje'), 'icon': 'bi-house-door', 'url': reverse('core:home')},
        {'key': 'alunos', 'label': _('Alunos'), 'icon': 'bi-people', 'url': reverse('core:student_list')},
        {'key': 'agenda', 'label': _('Agenda'), 'icon': 'bi-calendar-week', 'url': reverse('core:agenda')},
        {'key': 'financeiro', 'label': _('Financeiro'), 'icon': 'bi-cash-coin', 'url': reverse('core:finance')},
    ]
    for item in items:
        item['active'] = item['key'] == active
        item['badge'] = badges.get(item['key'], 0)
    more = [
        {'label': _('Exercícios'), 'icon': 'bi-activity', 'url': reverse('core:exercise_list')},
        {'label': _('Treinos'), 'icon': 'bi-calendar-check', 'url': reverse('core:routine_list')},
        {'label': _('Pacotes'), 'icon': 'bi-box-seam', 'url': reverse('core:plan_list')},
        {'label': _('Feriados'), 'icon': 'bi-flag', 'url': reverse('core:agenda_holidays')},
        {'label': _('Admin'), 'icon': 'bi-gear', 'url': '/admin/'},
        {'label': _('Notificações'), 'icon': 'bi-bell', 'url': reverse('core:notification_settings')},
        {'label': _('Alterar senha'), 'icon': 'bi-key', 'url': reverse('account_change_password')},
    ]
    if settings.SSO_PROVIDERS:
        more.append({'label': _('Contas conectadas'), 'icon': 'bi-link-45deg', 'url': reverse('socialaccount_connections')})
    more += [
        {'label': _('Sair'), 'icon': 'bi-box-arrow-right', 'url': reverse('account_logout')},
    ]
    return {'role': 'staff', 'items': items, 'more': more, 'more_active': active == 'mais'}


def _student_bar(request, person, active):
    rows = rows_for(request, person)
    running = open_row(rows)
    suggested = suggested_row(rows)
    if running:
        center = {'state': 'continue', 'label': _('Continuar'),
                  'url': reverse('core:workout_execute', kwargs={'pk': running['routine'].pk}),
                  'started_ms': int(running['open_start'].timestamp() * 1000)}
    elif suggested:
        center = {'state': 'start', 'label': _('Treinar'),
                  'url': reverse('core:workout_start', kwargs={'pk': suggested['routine'].pk})}
    else:
        center = {'state': 'idle', 'label': _('Treinar'), 'url': reverse('core:home')}
    items = [
        {'key': 'hoje', 'label': _('Hoje'), 'icon': 'bi-house-door', 'url': reverse('core:home')},
        {'key': 'treinos', 'label': _('Treinos'), 'icon': 'bi-fire', 'url': reverse('core:my_workouts')},
        {'key': 'center'},
        {'key': 'historico', 'label': _('Histórico'), 'icon': 'bi-graph-up', 'url': reverse('core:my_history')},
        {'key': 'perfil', 'label': _('Perfil'), 'icon': 'bi-person-circle', 'url': reverse('core:my_profile')},
    ]
    for item in items:
        item['active'] = item['key'] == active
        item['badge'] = 0
    center['active'] = active == 'center'
    return {'role': 'student', 'items': items, 'center': center}


def _staff_fab(request, name):
    """Items of the "Criar" sheet behind the floating "+": the page's own actions first, then the global ones."""
    if name not in FAB_PAGES:
        return None
    here = request.get_full_path()

    def item(label, icon, url_name, *args, extra=''):
        return {'label': label, 'icon': icon, 'url': with_next(reverse(url_name, args=args or None), here) + extra}

    items = []
    pk = request.resolver_match.kwargs.get('pk')
    if name == 'student_detail' and pk:
        student = Person.objects.filter(pk=pk).values_list('person_name', flat=True).first()
        if student is not None:
            items += [
                item(_('Registrar pagamento'), 'bi-cash-coin', 'core:payment_create', pk),
                item(_('Atribuir treino'), 'bi-lightning-charge', 'core:person_routine_create', extra=f'&person={pk}'),
                item(_('Nova aula para %(name)s') % {'name': student.split()[0]}, 'bi-calendar-plus', 'core:appointment_create', extra=f'&person={pk}'),
                item(_('Nova avaliação'), 'bi-rulers', 'core:assessment_create', pk),
                item(_('Adicionar foto'), 'bi-camera', 'core:photo_upload', pk),
            ]
    items += [
        item(_('Novo aluno'), 'bi-person-plus', 'core:student_create'),
        item(_('Nova aula'), 'bi-calendar-plus', 'core:appointment_create'),
    ]
    return items


def app_nav(request):
    """`tabbar` is None when there is no bar (visitors, accounts without a student file, hidden pages)."""
    user = request.user
    if not user.is_authenticated:
        return {'tabbar': None}
    match = getattr(request, 'resolver_match', None)
    name = match.url_name if match and match.url_name else ''
    if name in HIDDEN_ON:
        return {'tabbar': None, 'hide_topmenu': True}
    if user.is_staff or user.is_superuser:
        active = _section(name, STAFF_SECTIONS, fallback='mais' if name else '')
        return {'tabbar': _staff_bar(active), 'fab_items': _staff_fab(request, name)}
    try:
        person = user.person
    except Person.DoesNotExist:
        return {'tabbar': None}
    return {'tabbar': _student_bar(request, person, _section(name, STUDENT_SECTIONS))}


APP_NAME = 'Muscle Track'


def branding(request):
    """The name shown in the header, footer and landing page (STUDIO_NAME or "Muscle Track"), and the single sign-on
    providers that are configured (none: no button, no divider)."""
    providers = []
    for provider in settings.SSO_PROVIDERS:
        try:
            providers.append({**provider, 'url': reverse(provider['login_url_name'])})
        except NoReverseMatch:
            continue
    return {'brand_name': settings.STUDIO_NAME or APP_NAME, 'studio_name': settings.STUDIO_NAME,
            'app_name': APP_NAME, 'sso_providers': providers}
