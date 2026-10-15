"""Breadcrumb trails, defined in one place.

The trail of a page is built from its URL name and from objects already in the
template context (``student``, ``routine``, ``exercise``, ``form.instance``...), so it
costs no extra queries and views do not have to know about it. A page that has no entry
here (home, login, invite, workout screen...) shows no breadcrumb.

The breadcrumb shows the *hierarchy* (plain links); the Back button keeps being the
*history* (``?next=``, see core/navigation.py). Labels match the main menu.
To give a new page a breadcrumb, add one function to ``TRAILS``.
"""
from django.urls import reverse
from django.utils import formats, timezone

from .permissions import is_trainer


def _home():
    return ('Início', reverse('core:home'))


def _students():
    return ('Alunos', reverse('core:student_list'))


def _student(person):
    return (person.person_name, reverse('core:student_detail', kwargs={'pk': person.pk}))


def _student_tab(person, label, tab):
    return (label, reverse('core:student_detail', kwargs={'pk': person.pk}) + f'#tab-{tab}')


def _exercises():
    return ('Exercícios', reverse('core:exercise_list'))


def _exercise(exercise):
    return (exercise.exercise_name, reverse('core:exercise_detail', kwargs={'pk': exercise.pk}))


def _routines():
    return ('Treinos', reverse('core:routine_list'))


def _routine(routine):
    return (routine.routine_name, reverse('core:routine_detail', kwargs={'pk': routine.pk}))


def _my_history():
    return ('Histórico', reverse('core:my_history'))


def _instance(ctx):
    """The object being edited in a ModelForm page, or None when creating."""
    form = ctx.get('form')
    instance = getattr(form, 'instance', None)
    return instance if instance is not None and instance.pk else None


# --- trails: each returns the full list; the last item is the current page ---------

def _student_list(request, ctx):
    return [_home(), ('Alunos', None)]


def _student_create(request, ctx):
    return [_home(), _students(), ('Novo aluno', None)]


def _student_detail(request, ctx):
    return [_home(), _students(), (ctx['student'].person_name, None)]


def _student_edit(request, ctx):
    return [_home(), _students(), _student(_instance(ctx)), ('Editar', None)]


def _student_page(label):
    def trail(request, ctx):
        return [_home(), _students(), _student(ctx['student']), (label, None)]
    return trail


def _assessment(label):
    def trail(request, ctx):
        student = ctx['student']
        return [_home(), _students(), _student(student), _student_tab(student, 'Avaliações', 'avaliacoes'),
                (label if label else ('Editar avaliação' if _instance(ctx) else 'Nova avaliação'), None)]
    return trail


def _photo_upload(request, ctx):
    student = ctx['student']
    return [_home(), _students(), _student(student), _student_tab(student, 'Avaliações', 'avaliacoes'),
            ('Adicionar foto', None)]


def _person_routine_create(request, ctx):
    trail = [_home(), _students()]
    if ctx.get('selected_person'):
        trail.append(_student(ctx['selected_person']))
    return trail + [('Atribuir treino', None)]


def _person_routine_edit(request, ctx):
    person = ctx.get('selected_person') or ctx['person_routine'].person
    return [_home(), _students(), _student(person), ('Editar treino', None)]


def _person_routine_detail(request, ctx):
    return [_home(), _students(), _student(ctx['student']), (f"Progresso: {ctx['person_routine'].routine_name}", None)]


def _session_detail(request, ctx):
    session = ctx['session']
    when = formats.date_format(timezone.localtime(session.start_time), 'd/m/Y H:i')
    label = (f'Sessão de {when}', None)
    if is_trainer(request.user):
        person_routine = ctx['person_routine']
        return [_home(), _students(), _student(ctx['student']),
                (person_routine.routine_name, reverse('core:person_routine_detail', kwargs={'pk': person_routine.pk})),
                label]
    return [_home(), _my_history(), label]


def _finance():
    return ('Financeiro', reverse('core:finance'))


def _plans():
    return ('Pacotes', reverse('core:plan_list'))


def _agenda():
    return ('Agenda', reverse('core:agenda'))


def _exercise_list(request, ctx):
    return [_home(), ('Exercícios', None)]


def _exercise_create(request, ctx):
    return [_home(), _exercises(), ('Novo exercício', None)]


def _exercise_detail(request, ctx):
    return [_home(), _exercises(), (ctx['exercise'].exercise_name, None)]


def _exercise_edit(request, ctx):
    return [_home(), _exercises(), _exercise(_instance(ctx)), ('Editar', None)]


def _routine_list(request, ctx):
    return [_home(), ('Treinos', None)]


def _routine_create(request, ctx):
    return [_home(), _routines(), ('Novo treino', None)]


def _routine_detail(request, ctx):
    return [_home(), _routines(), (ctx['routine'].routine_name, None)]


def _routine_edit(request, ctx):
    return [_home(), _routines(), _routine(ctx['routine']), ('Editar', None)]


def _routine_generate_ai(request, ctx):
    return [_home(), _routines(), ('Gerar com IA', None)]


def _my_workouts(request, ctx):
    return [_home(), ('Meus treinos', None)]


def _my_history_trail(request, ctx):
    return [_home(), ('Histórico', None)]


def _my_profile(request, ctx):
    return [_home(), ('Meu perfil', None)]


def _my_exercise_progress(request, ctx):
    return [_home(), _my_history(), (ctx['exercise'].exercise_name, None)]


TRAILS = {
    'calendar_settings': lambda request, ctx: [_home(), _agenda() if request.user.is_staff else ('Meu perfil', reverse('core:my_profile')), ('Google Agenda', None)],
    'search': lambda request, ctx: [_home(), ('Busca', None)],
    'notification_settings': lambda request, ctx: [_home(), _agenda() if request.user.is_staff else ('Meu perfil', reverse('core:my_profile')), ('Notificações', None)],
    'slot_edit': lambda request, ctx: [_home(), _students(), _student(ctx['student']), _student_tab(ctx['student'], 'Agenda', 'agenda'), ('Editar horário fixo', None)],
    'my_intro': lambda request, ctx: [_home(), ('Como usar o app', None)],
    'my_evolution': lambda request, ctx: [_home(), ('Minha evolução', None)],
    'my_evolution_compare': lambda request, ctx: [_home(), ('Minha evolução', reverse('core:my_evolution')), ('Comparar fotos', None)],
    'student_compare': lambda request, ctx: [_home(), _students(), _student(ctx['student']), _student_tab(ctx['student'], 'Avaliações', 'avaliacoes'), ('Comparar fotos', None)],
    'my_package': lambda request, ctx: [_home(), ('Meu pacote', None)],
    'my_agenda': lambda request, ctx: [_home(), ('Minha agenda', None)],
    'my_agenda_request': lambda request, ctx: [_home(), ('Minha agenda', reverse('core:my_agenda')), ('Pedir remarcação', None)],
    'finance': lambda request, ctx: [_home(), ('Financeiro', None)],
    'plan_list': lambda request, ctx: [_home(), _finance(), ('Pacotes', None)],
    'plan_create': lambda request, ctx: [_home(), _finance(), _plans(), ('Novo pacote', None)],
    'plan_edit': lambda request, ctx: [_home(), _finance(), _plans(), ('Editar pacote', None)],
    'payment_create': _student_page('Registrar pagamento'),
    'agenda': lambda request, ctx: [_home(), ('Agenda', None)],
    'appointment_create': lambda request, ctx: [_home(), _agenda(), ('Nova aula', None)],
    'appointment_reschedule': lambda request, ctx: [_home(), _agenda(), ('Remarcar aula', None)],
    'student_list': _student_list,
    'student_create': _student_create,
    'student_detail': _student_detail,
    'student_edit': _student_edit,
    'student_invite': _student_page('Convite'),
    'student_delete': _student_page('Excluir'),
    'student_status': _student_page('Situação'),
    'student_package_sell': _student_page('Vender pacote'),
    'student_history': _student_page('Histórico'),
    'assessment_create': _assessment(None),
    'assessment_edit': _assessment(None),
    'photo_upload': _photo_upload,
    'person_routine_create': _person_routine_create,
    'person_routine_edit': _person_routine_edit,
    'person_routine_detail': _person_routine_detail,
    'session_detail': _session_detail,
    'exercise_list': _exercise_list,
    'exercise_create': _exercise_create,
    'exercise_detail': _exercise_detail,
    'exercise_edit': _exercise_edit,
    'routine_list': _routine_list,
    'routine_create': _routine_create,
    'routine_detail': _routine_detail,
    'routine_edit': _routine_edit,
    'routine_generate_ai': _routine_generate_ai,
    'my_workouts': _my_workouts,
    'my_history': _my_history_trail,
    'my_profile': _my_profile,
    'my_privacy': lambda request, ctx: [_home(), ('Privacidade e meus dados', None)],
    'my_exercise_progress': _my_exercise_progress,
}

# Pages deliberately without a breadcrumb (anything else must be in TRAILS - see the tests).
NO_BREADCRUMB = {
    'home',                 # the root
    'workout_execute',      # focused workout screen (sticky header with the timer)
    'workout_summary',      # what you just did: a focused screen right after the workout
    'accept_invite',        # public invitation page
    'consent',              # first-access screen, the student must decide before using the app
    'privacy_policy',       # public text page
}


def view_name(request):
    match = getattr(request, 'resolver_match', None)
    return match.url_name if match else None


def build_breadcrumbs(request, context):
    """The trail for the current page as ``[(label, url_or_None), ...]`` (``[]`` if none)."""
    builder = TRAILS.get(view_name(request))
    if builder is None:
        return []
    try:
        trail = builder(request, context)
    except (KeyError, AttributeError, TypeError):
        return []  # a missing object must never break the page
    # The current page (last item) is never a link
    return trail[:-1] + [(trail[-1][0], None)]
