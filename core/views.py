import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Max, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from allauth.account.models import EmailAddress

from .forms import (
    PersonForm, ExerciseForm, RoutineForm, PersonRoutineForm,
    AIWorkoutGenerationForm
)
from .invites import build_invite_url, get_user_from_invite
from .models import (
    MuscleGroup, Person, Exercise, Routine, RoutineExercise,
    PersonRoutine, PersonRoutineExercise, WorkoutSession, SessionExercise, SessionSet
)
from .permissions import is_trainer, staff_required, student_required


def home(request):
    """Home page"""
    from django.db.models import Avg

    context = {}

    # Se for admin/staff, mostra estatísticas gerais
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        context = {
            'total_students': Person.objects.filter(is_active=True).count(),
            'total_exercises': Exercise.objects.filter(is_active=True).count(),
            'total_routines': Routine.objects.filter(is_active=True).count(),
        }
    # Se for aluno, mostra estatísticas pessoais
    elif request.user.is_authenticated:
        try:
            person = request.user.person
            sessions = WorkoutSession.objects.filter(
                person_routine__person=person,
                is_completed=True
            )

            total_workouts = sessions.count()
            avg_duration = sessions.aggregate(Avg('duration_minutes'))['duration_minutes__avg'] or 0

            # Calcula assiduidade (últimos 30 dias)
            from datetime import timedelta
            thirty_days_ago = timezone.now() - timedelta(days=30)
            recent_workouts = sessions.filter(start_time__gte=thirty_days_ago).count()

            context = {
                'total_workouts': total_workouts,
                'recent_workouts': recent_workouts,
                'avg_duration': round(avg_duration, 1) if avg_duration else 0,
                'is_student': True,
            }
        except Person.DoesNotExist:
            # Usuário autenticado mas não é aluno cadastrado
            context = {
                'total_students': Person.objects.filter(is_active=True).count(),
                'total_exercises': Exercise.objects.filter(is_active=True).count(),
                'total_routines': Routine.objects.filter(is_active=True).count(),
            }
    else:
        # Usuário não autenticado
        context = {
            'total_students': Person.objects.filter(is_active=True).count(),
            'total_exercises': Exercise.objects.filter(is_active=True).count(),
            'total_routines': Routine.objects.filter(is_active=True).count(),
        }

    return render(request, 'core/home.html', context)


# ============= STUDENT VIEWS =============

@staff_required
def student_list(request):
    """List all students with filters and statistics"""
    search = request.GET.get('search', '')
    status = request.GET.get('status', '')
    view_mode = request.GET.get('view', request.COOKIES.get('student_view_mode', 'grid'))

    # Base queryset
    students = Person.objects.all().select_related('user')

    # Filtro por busca (nome, email, telefone, ID)
    if search:
        students = students.filter(
            Q(person_name__icontains=search) |
            Q(user__email__icontains=search) |
            Q(phone__icontains=search) |
            Q(id__icontains=search)
        )

    # Filtro por status
    if status == 'active':
        students = students.filter(is_active=True)
    elif status == 'inactive':
        students = students.filter(is_active=False)

    students = students.order_by('person_name')

    # Adicionar estatísticas para cada aluno
    students_with_stats = []
    for student in students:
        last_session = student.get_last_workout_session()
        students_with_stats.append({
            'student': student,
            'last_session': last_session,
            'total_workouts': student.get_total_workouts(),
            'age': student.age
        })

    response = render(request, 'core/student_list.html', {
        'students_with_stats': students_with_stats,
        'search': search,
        'selected_status': status,
        'view_mode': view_mode
    })

    # Salvar view_mode no cookie
    if 'view' in request.GET:
        response.set_cookie('student_view_mode', view_mode, max_age=31536000)

    return response


@staff_required
def student_create(request):
    """Create a new student and hand the trainer an invitation link"""
    if request.method == 'POST':
        form = PersonForm(request.POST, request.FILES)
        if form.is_valid():
            email = form.cleaned_data['email']

            with transaction.atomic():
                # Account without a password: the student chooses it through the invite link
                user = User(username=email, email=email, is_active=form.cleaned_data['is_active'])
                user.set_unusable_password()
                user.save()

                person = form.save(commit=False)
                person.user = user
                person.save()

                EmailAddress.objects.create(user=user, email=email, primary=True, verified=False)

            messages.success(request, 'Aluno cadastrado! Envie o link de convite para ele criar a senha.')
            return redirect('core:student_invite', pk=person.pk)
    else:
        form = PersonForm()
    return render(request, 'core/student_form.html', {'form': form, 'title': 'Cadastrar Aluno'})


@staff_required
def student_invite(request, pk):
    """Show the one-time link (and a WhatsApp shortcut) the student uses to set a password"""
    student = get_object_or_404(Person.objects.select_related('user'), pk=pk)
    invite_url = build_invite_url(request, student.user)

    phone_digits = ''.join(ch for ch in student.phone if ch.isdigit())
    if phone_digits and not phone_digits.startswith('55') and len(phone_digits) <= 11:
        phone_digits = '55' + phone_digits  # Brazilian number typed without country code
    whatsapp_text = (
        f'Olá, {student.person_name.split()[0]}! Seu acesso ao Muscle Track está pronto. '
        f'Crie sua senha por este link (válido por 7 dias): {invite_url}'
    )
    whatsapp_url = f'https://wa.me/{phone_digits}?text={quote(whatsapp_text)}'

    return render(request, 'core/student_invite.html', {
        'student': student,
        'invite_url': invite_url,
        'whatsapp_url': whatsapp_url,
        'has_password': student.user.has_usable_password(),
    })


def accept_invite(request, uidb64, token):
    """Public page: the invited student chooses a password and is logged in"""
    user = get_user_from_invite(uidb64, token)
    if user is None:
        return render(request, 'core/accept_invite.html', {'invalid': True}, status=400)
    if not user.person.is_active:
        return render(request, 'core/accept_invite.html', {'invalid': True, 'inactive': True}, status=403)

    if request.method == 'POST':
        form = SetPasswordForm(user, request.POST)
        if form.is_valid():
            user = form.save()
            user.is_active = True
            user.save(update_fields=['is_active'])
            EmailAddress.objects.update_or_create(
                user=user, email=user.email, defaults={'primary': True, 'verified': True})
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, 'Senha criada! Bem-vindo(a) ao Muscle Track.')
            return redirect('core:my_workouts')
    else:
        form = SetPasswordForm(user)

    return render(request, 'core/accept_invite.html', {'form': form, 'student': user.person})


@staff_required
def student_detail(request, pk):
    """View student details"""
    from django.db.models import Avg
    from datetime import timedelta
    from django.utils import timezone

    student = get_object_or_404(Person, pk=pk)

    # Treinos ativos e inativos
    active_routines = student.routines.filter(is_active=True).select_related('routine_template')
    inactive_routines = student.routines.filter(is_active=False).select_related('routine_template').order_by('-routine_date')[:5]

    # Estatísticas
    # Data da última sessão
    last_session = WorkoutSession.objects.filter(
        person_routine__person=student,
        is_completed=True
    ).order_by('-start_time').first()

    # Total de treinos completados
    total_sessions = WorkoutSession.objects.filter(
        person_routine__person=student,
        is_completed=True
    ).count()

    # Média mensal de treinos (últimos 30 dias)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    sessions_last_month = WorkoutSession.objects.filter(
        person_routine__person=student,
        is_completed=True,
        start_time__gte=thirty_days_ago
    ).count()

    # Duração média dos treinos
    avg_duration = WorkoutSession.objects.filter(
        person_routine__person=student,
        is_completed=True,
        duration_minutes__isnull=False
    ).aggregate(avg=Avg('duration_minutes'))['avg']

    # Treinos ativos com contagem de exercícios
    active_routines_with_stats = []
    for person_routine in active_routines:
        exercise_count = person_routine.exercises.count()
        sessions_count = person_routine.sessions.filter(is_completed=True).count()
        active_routines_with_stats.append({
            'person_routine': person_routine,
            'exercise_count': exercise_count,
            'sessions_count': sessions_count,
        })

    return render(request, 'core/student_detail.html', {
        'student': student,
        'active_routines_with_stats': active_routines_with_stats,
        'inactive_routines': inactive_routines,
        'last_session': last_session,
        'total_sessions': total_sessions,
        'sessions_last_month': sessions_last_month,
        'avg_duration': avg_duration,
    })


@staff_required
def student_edit(request, pk):
    """Edit student"""
    student = get_object_or_404(Person.objects.select_related('user'), pk=pk)
    if request.method == 'POST':
        form = PersonForm(request.POST, request.FILES, instance=student)
        if form.is_valid():
            email = form.cleaned_data['email']
            user = student.user
            email_changed = user.email != email

            with transaction.atomic():
                person = form.save()
                if email_changed:
                    user.email = email
                    user.username = email
                # An inactive student loses access; reactivating restores it.
                # (A student who never used the invite has no usable password, so cannot log in anyway.)
                user.is_active = person.is_active
                user.save()
                if email_changed:
                    EmailAddress.objects.update_or_create(
                        user=user, primary=True, defaults={'email': email, 'verified': False})

            if email_changed:
                messages.success(request, 'Aluno atualizado! O e-mail de login mudou: confira o convite se o aluno ainda não criou a senha.')
            else:
                messages.success(request, 'Aluno atualizado com sucesso!')
            return redirect('core:student_detail', pk=pk)
    else:
        form = PersonForm(instance=student)
    return render(request, 'core/student_form.html', {'form': form, 'title': 'Editar Aluno'})


@student_required
def my_profile(request, person):
    """Student edits their own profile"""
    from .forms import StudentProfileForm

    if request.method == 'POST':
        form = StudentProfileForm(request.POST, request.FILES, instance=person)
        if form.is_valid():
            form.save()
            messages.success(request, 'Seu perfil foi atualizado com sucesso!')
            return redirect('core:my_profile')
    else:
        form = StudentProfileForm(instance=person)

    return render(request, 'core/my_profile.html', {
        'form': form,
        'title': 'Meu Perfil',
        'user_email': request.user.email,
        'person': person
    })


# ============= EXERCISE VIEWS =============

@staff_required
def exercise_list(request):
    """List all exercises"""
    search = request.GET.get('search', '')
    muscle_group = request.GET.get('muscle_group', '')
    view_mode = request.GET.get('view', 'grid')  # grid ou list

    exercises = Exercise.objects.filter(is_active=True).select_related('muscle_group')

    # Filtro por busca
    if search:
        exercises = exercises.filter(
            Q(exercise_name__icontains=search) |
            Q(description__icontains=search)
        )

    # Filtro por grupo muscular
    if muscle_group:
        exercises = exercises.filter(muscle_group_id=muscle_group)

    exercises = exercises.order_by('exercise_name', 'muscle_group__order')

    # Buscar grupos musculares ativos
    muscle_groups = MuscleGroup.objects.filter(is_active=True).order_by('name')

    return render(request, 'core/exercise_list.html', {
        'exercises': exercises,
        'muscle_groups': muscle_groups,
        'search': search,
        'selected_muscle_group': muscle_group,
        'view_mode': view_mode
    })


@staff_required
def exercise_create(request):
    """Create a new exercise"""
    if request.method == 'POST':
        form = ExerciseForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, 'Exercício cadastrado com sucesso!')
            return redirect('core:exercise_list')
    else:
        form = ExerciseForm()
    return render(request, 'core/exercise_form.html', {'form': form, 'title': 'Cadastrar Exercício'})


@staff_required
def exercise_detail(request, pk):
    """View exercise details"""
    exercise = get_object_or_404(Exercise, pk=pk)

    # Estatísticas do exercício
    # Quantidade de treinos (templates) que incluem esse exercício
    routines_count = RoutineExercise.objects.filter(exercise=exercise).values('routine').distinct().count()

    # Quantidade de alunos que têm esse exercício em seus treinos ativos
    active_students = PersonRoutine.objects.filter(
        exercises__exercise=exercise,
        is_active=True
    ).values('person').distinct().count()

    # Quantidade total de sessões realizadas com esse exercício
    # Conta sessões onde o person_routine tem esse exercício
    sessions_count = WorkoutSession.objects.filter(
        person_routine__exercises__exercise=exercise,
        is_completed=True
    ).distinct().count()

    # Quantidade de vezes que o exercício foi completado
    completed_count = SessionExercise.objects.filter(
        person_routine_exercise__exercise=exercise,
        is_completed=True
    ).count()

    # Lista de treinos (templates) que incluem esse exercício
    routine_exercises = RoutineExercise.objects.filter(
        exercise=exercise
    ).select_related('routine').order_by('routine__routine_name')

    return render(request, 'core/exercise_detail.html', {
        'exercise': exercise,
        'routines_count': routines_count,
        'active_students': active_students,
        'sessions_count': sessions_count,
        'completed_count': completed_count,
        'routine_exercises': routine_exercises,
    })


@staff_required
def exercise_edit(request, pk):
    """Edit exercise"""
    exercise = get_object_or_404(Exercise, pk=pk)
    if request.method == 'POST':
        form = ExerciseForm(request.POST, request.FILES, instance=exercise)
        if form.is_valid():
            form.save()
            messages.success(request, 'Exercício atualizado com sucesso!')
            return redirect('core:exercise_detail', pk=pk)
    else:
        form = ExerciseForm(instance=exercise)
    return render(request, 'core/exercise_form.html', {'form': form, 'title': 'Editar Exercício'})


# ============= ROUTINE VIEWS =============

def _to_int(value, default, minimum=0):
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


def _load_exercise_items(raw, id_key):
    """Parse the `exercises_data` JSON sent by the routine forms.

    Returns (items, exercises_by_id). Raises ValueError with a user-facing
    message when the payload is malformed or references unknown exercises.
    """
    try:
        items = json.loads(raw or '[]')
    except json.JSONDecodeError:
        raise ValueError('Erro ao processar exercícios.')
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ValueError('Erro ao processar exercícios.')

    ids = []
    for item in items:
        ex_id = _to_int(item.get(id_key), None, 1)
        if ex_id is None:
            raise ValueError('Exercício inválido na lista.')
        item[id_key] = ex_id
        ids.append(ex_id)

    exercises = Exercise.objects.in_bulk(set(ids))
    if len(exercises) != len(set(ids)):
        raise ValueError('Um dos exercícios selecionados não existe mais.')
    return items, exercises


def _replace_routine_exercises(routine, items, exercises):
    """Rewrite the exercises of a routine *template* (nothing references them)."""
    routine.exercises.all().delete()
    for idx, item in enumerate(items, start=1):
        exercise = exercises[item['id']]
        RoutineExercise.objects.create(
            routine=routine,
            exercise=exercise,
            order=idx,
            sets=_to_int(item.get('sets'), exercise.default_sets, 1),
            reps=_to_int(item.get('reps'), exercise.default_reps, 1),
            rest_seconds=_to_int(item.get('rest'), 60, 0),
        )


def _sync_person_routine_exercises(person_routine, items, exercises):
    """Update a student's routine in place.

    Rows are matched by exercise so the SessionExercise history (cascade from
    PersonRoutineExercise) survives edits; only exercises the trainer actually
    removed are deleted.
    """
    existing = {}
    for row in person_routine.exercises.order_by('order', 'pk'):
        existing.setdefault(row.exercise_id, []).append(row)

    keep = []
    for idx, item in enumerate(items, start=1):
        values = {
            'order': idx,
            'sets': _to_int(item.get('sets'), 3, 1),
            'reps': _to_int(item.get('reps'), 12, 1),
            'rest_seconds': _to_int(item.get('rest_seconds'), 60, 0),
            'notes': str(item.get('notes') or ''),
        }
        pool = existing.get(item['exercise_id'])
        if pool:
            row = pool.pop(0)
            for field, value in values.items():
                setattr(row, field, value)
            row.save()
        else:
            row = PersonRoutineExercise.objects.create(
                person_routine=person_routine,
                exercise=exercises[item['exercise_id']],
                **values
            )
        keep.append(row.pk)
    person_routine.exercises.exclude(pk__in=keep).delete()


def _routine_form_context(form, title, routine, routine_exercises):
    return {
        'form': form,
        'title': title,
        'routine': routine,
        'routine_exercises': routine_exercises,
        'muscle_groups': MuscleGroup.objects.filter(is_active=True).order_by('order'),
    }

@staff_required
def routine_list(request):
    """List all routines"""
    search = request.GET.get('search', '')
    difficulty = request.GET.get('difficulty', '')
    status = request.GET.get('status', '')

    # Pegar view_mode do cookie ou GET, padrão é 'grid'
    view_mode = request.GET.get('view', request.COOKIES.get('routine_view_mode', 'grid'))
    #is_active=active
    routines = Routine.objects.filter().prefetch_related('exercises')

    # Filtro por busca
    if search:
        routines = routines.filter(
            Q(routine_name__icontains=search) |
            Q(description__icontains=search)
        )

    # Filtro por dificuldade
    if difficulty:
        routines = routines.filter(difficulty_level=difficulty)

    # Filtro por status
    if status == 'active':
        routines = routines.filter(is_active=True)
    elif status == 'inactive':
        routines = routines.filter(is_active=False)

    routines = routines.order_by('routine_name')

    # Anotar com contagem de alunos e exercícios
    routines = routines.annotate(
        student_count=Count('person_routines', distinct=True),
        exercise_count=Count('exercises', distinct=True)
    )

    response = render(request, 'core/routine_list.html', {
        'routines': routines,
        'search': search,
        'selected_difficulty': difficulty,
        'selected_status': status,
        'view_mode': view_mode,
        'difficulty_choices': Routine._meta.get_field('difficulty_level').choices
    })

    # Salvar view_mode no cookie se foi especificado no GET
    if 'view' in request.GET:
        response.set_cookie('routine_view_mode', view_mode, max_age=31536000)  # 1 ano

    return response


@staff_required
def routine_create(request):
    """Create a new routine"""
    if request.method == 'POST':
        form = RoutineForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    routine = form.save()
                    items, exercises = _load_exercise_items(request.POST.get('exercises_data'), 'id')
                    _replace_routine_exercises(routine, items, exercises)
            except ValueError as e:
                messages.error(request, str(e))
            else:
                messages.success(request, 'Treino criado com sucesso!')
                return redirect('core:routine_detail', pk=routine.pk)
    else:
        form = RoutineForm()

    return render(request, 'core/routine_form.html',
                  _routine_form_context(form, 'Criar Treino', None, []))


@staff_required
def routine_detail(request, pk):
    """View routine details"""
    routine = get_object_or_404(Routine, pk=pk)
    exercises = routine.exercises.all().order_by('order')

    # Estatísticas
    person_routines = routine.person_routines.all()
    students_with_routine = person_routines.values('person').distinct().count()
    active_students = person_routines.filter(is_active=True).values('person').distinct().count()
    total_sessions = WorkoutSession.objects.filter(person_routine__routine_template=routine).count()
    completed_sessions = WorkoutSession.objects.filter(
        person_routine__routine_template=routine,
        is_completed=True
    ).count()

    # Duração média das sessões completadas
    from django.db.models import Avg
    avg_duration = WorkoutSession.objects.filter(
        person_routine__routine_template=routine,
        is_completed=True,
        duration_minutes__isnull=False
    ).aggregate(avg=Avg('duration_minutes'))['avg']

    return render(request, 'core/routine_detail.html', {
        'routine': routine,
        'routine_exercises': exercises,
        'students_with_routine': students_with_routine,
        'active_students': active_students,
        'total_sessions': total_sessions,
        'completed_sessions': completed_sessions,
        'avg_duration': avg_duration
    })


@staff_required
def routine_edit(request, pk):
    """Edit routine"""
    routine = get_object_or_404(Routine, pk=pk)

    if request.method == 'POST':
        form = RoutineForm(request.POST, instance=routine)
        if form.is_valid():
            try:
                with transaction.atomic():
                    routine = form.save()
                    items, exercises = _load_exercise_items(request.POST.get('exercises_data'), 'id')
                    _replace_routine_exercises(routine, items, exercises)
            except ValueError as e:
                messages.error(request, str(e))
            else:
                messages.success(request, 'Treino atualizado com sucesso!')
                return redirect('core:routine_detail', pk=pk)
    else:
        form = RoutineForm(instance=routine)

    routine_exercises = routine.exercises.all().select_related('exercise').order_by('order')
    return render(request, 'core/routine_form.html',
                  _routine_form_context(form, 'Editar Treino', routine, routine_exercises))


# ============= PERSON ROUTINE VIEWS =============

@staff_required
def person_routine_create(request):
    """Assign a routine template to a student (creates personalized copy)"""
    selected_person = None

    if request.method == 'POST':
        form = PersonRoutineForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    person_routine = form.save(commit=False)

                    template_id = request.POST.get('template_id', '')
                    if template_id.isdigit():
                        person_routine.routine_template = Routine.objects.filter(pk=template_id).first()

                    person_routine.save()
                    items, exercises = _load_exercise_items(
                        request.POST.get('exercises_data'), 'exercise_id')
                    _sync_person_routine_exercises(person_routine, items, exercises)
            except ValueError as e:
                messages.error(request, str(e))
            else:
                messages.success(request, f'Treino "{person_routine.routine_name}" atribuído a {person_routine.person.person_name} com sucesso!')
                return redirect('core:student_detail', pk=person_routine.person.pk)
        person_id = request.POST.get('person')
    else:
        # Pre-fill person if provided in URL
        person_id = request.GET.get('person')
        form = PersonRoutineForm(initial={'person': person_id} if person_id else None)

    if person_id and str(person_id).isdigit():
        selected_person = Person.objects.filter(pk=person_id).first()

    return render(request, 'core/person_routine_form.html', {
        'form': form,
        'selected_person': selected_person
    })


@staff_required
def person_routine_detail(request, pk):
    """View person routine progress - all sessions for this specific routine"""
    from django.db.models import Avg
    from datetime import timedelta

    person_routine = get_object_or_404(PersonRoutine, pk=pk)

    # Todas as sessões deste treino específico
    all_sessions = WorkoutSession.objects.filter(
        person_routine=person_routine
    ).order_by('-start_time')

    # Sessões concluídas
    completed_sessions = all_sessions.filter(is_completed=True)

    # Estatísticas
    total_sessions = completed_sessions.count()

    # Última sessão
    last_session = completed_sessions.first()

    # Duração média
    avg_duration = completed_sessions.aggregate(avg=Avg('duration_minutes'))['avg'] or 0

    # Sessões do último mês
    one_month_ago = timezone.now() - timedelta(days=30)
    sessions_last_month = completed_sessions.filter(start_time__gte=one_month_ago).count()

    # Taxa de conclusão (sessões concluídas vs iniciadas)
    total_started = all_sessions.count()
    completion_rate = (total_sessions / total_started * 100) if total_started > 0 else 0

    # Exercícios do treino
    routine_exercises = person_routine.exercises.all().select_related('exercise').order_by('order')

    # Sessões recentes para gráfico (últimas 20)
    recent_sessions = list(completed_sessions[:20])

    chart_data = _chart_points(recent_sessions)

    return render(request, 'core/person_routine_progress.html', {
        'person_routine': person_routine,
        'student': person_routine.person,
        'all_sessions': all_sessions,
        'completed_sessions': completed_sessions,
        'recent_sessions': recent_sessions,
        'total_sessions': total_sessions,
        'last_session': last_session,
        'avg_duration': round(avg_duration, 1),
        'sessions_last_month': sessions_last_month,
        'completion_rate': round(completion_rate, 1),
        'routine_exercises': routine_exercises,
        'chart_data': json.dumps(chart_data),
    })


@staff_required
def person_routine_edit(request, pk):
    """Edit a person's routine"""
    person_routine = get_object_or_404(PersonRoutine, pk=pk)

    if request.method == 'POST':
        form = PersonRoutineForm(request.POST, instance=person_routine)
        if form.is_valid():
            try:
                with transaction.atomic():
                    person_routine = form.save()
                    items, exercises = _load_exercise_items(
                        request.POST.get('exercises_data'), 'exercise_id')
                    _sync_person_routine_exercises(person_routine, items, exercises)
            except ValueError as e:
                messages.error(request, str(e))
            else:
                messages.success(request, f'Treino "{person_routine.routine_name}" atualizado com sucesso!')
                return redirect('core:student_detail', pk=person_routine.person.pk)
    else:
        form = PersonRoutineForm(instance=person_routine)

    current_exercises = person_routine.exercises.all().select_related('exercise').order_by('order')

    return render(request, 'core/person_routine_form.html', {
        'form': form,
        'person_routine': person_routine,
        'current_exercises': current_exercises,
        'is_edit': True,
        'selected_person': person_routine.person  # Always show person as read-only in edit mode
    })


@staff_required
def person_routine_deactivate(request, pk):
    """Deactivate a person routine (move to history)"""
    if request.method == 'POST':
        person_routine = get_object_or_404(PersonRoutine, pk=pk)
        person_id = person_routine.person.pk

        # Desativar o treino (move para histórico)
        person_routine.is_active = False
        person_routine.end_date = timezone.now().date()
        person_routine.save()

        messages.success(request, f'Treino "{person_routine.routine_name}" foi removido e movido para o histórico.')
        return redirect('core:student_detail', pk=person_id)

    return redirect('core:student_list')


# ============= STUDENT WORKOUT AREA =============

MAX_SESSION_MINUTES = 120


def _close_if_stale(session):
    """Close a session that has been open longer than MAX_SESSION_MINUTES.

    An abandoned session is closed at its cap, not "now", so a forgotten
    workout does not end up recorded as an 8-hour training.
    Returns True when the session was closed.
    """
    if not session:
        return False
    limit = session.start_time + timedelta(minutes=MAX_SESSION_MINUTES)
    if timezone.now() > limit:
        session.end_session(at=limit)
        return True
    return False


def _chart_points(sessions, with_workout_name=False):
    """Duration and average heaviest-set weight for each session (oldest first).

    One aggregate query for all sessions instead of one query per session.
    """
    sessions = list(reversed(list(sessions)))
    averages = {
        row['session_id']: row['avg']
        for row in SessionExercise.objects.filter(
            session__in=sessions, weight_used__isnull=False
        ).values('session_id').annotate(avg=Avg('weight_used'))
    }
    points = []
    for session in sessions:
        avg = averages.get(session.pk)
        point = {
            'date': session.start_time.strftime('%d/%m'),
            'duration': session.duration_minutes or 0,
            'avg_weight': round(float(avg), 1) if avg else 0,
        }
        if with_workout_name:
            point['workout'] = session.person_routine.routine_name[:15]
        points.append(point)
    return points


def _personal_record(person, exercise_id, exclude_session=None):
    """Heaviest weight this student ever completed in the given exercise (or None)."""
    logs = SessionExercise.objects.filter(
        session__person_routine__person=person,
        person_routine_exercise__exercise_id=exercise_id,
        weight_used__isnull=False,
    )
    if exclude_session is not None:
        logs = logs.exclude(session=exclude_session)
    return logs.aggregate(best=Max('weight_used'))['best']


@student_required
def my_workouts(request, person):
    """Student's active workouts, with a suggestion of which one is next"""
    last_log_prefetch = Prefetch(
        'session_logs',
        queryset=SessionExercise.objects.filter(
            session__is_completed=True,
            weight_used__isnull=False
        ).select_related('session').order_by('-completed_at')[:1],
        to_attr='last_session_log'
    )
    exercises_prefetch = Prefetch(
        'exercises',
        queryset=PersonRoutineExercise.objects.select_related('exercise').prefetch_related(
            last_log_prefetch
        ).order_by('order')
    )

    active_routines = PersonRoutine.objects.filter(
        person=person,
        is_active=True
    ).select_related('routine_template').prefetch_related(
        'sessions',
        exercises_prefetch
    ).order_by('routine_name')

    items = []
    for routine in active_routines:
        sessions = list(routine.sessions.all())  # prefetched
        active_session = next((x for x in sessions if not x.is_completed), None)
        if _close_if_stale(active_session):
            active_session = None
        done_dates = [x.start_time for x in sessions if x.is_completed]
        items.append({
            'routine': routine,
            'active_session': active_session,
            'last_done': max(done_dates) if done_dates else None,
            'suggested': False,
        })

    # "Treino de hoje": continue the one in progress, otherwise the one done longest ago
    # (never done first) - this rotates A/B/C naturally.
    if len(items) > 1:
        in_progress = [i for i in items if i['active_session']]
        pool = in_progress or items
        pool_sorted = sorted(pool, key=lambda i: (i['last_done'] is not None, i['last_done'] or 0))
        pool_sorted[0]['suggested'] = True
        items.sort(key=lambda i: not i['suggested'])

    return render(request, 'core/my_workouts.html', {
        'person': person,
        'routines_with_sessions': items
    })


@student_required
@require_POST
def workout_start(request, pk, person):
    """Start a workout session or recover active session"""
    person_routine = get_object_or_404(PersonRoutine, pk=pk, person=person, is_active=True)

    active_session = person_routine.sessions.filter(is_completed=False).first()

    if active_session and not _close_if_stale(active_session):
        messages.info(request, 'Recuperando sua sessão de treino em andamento...')
    else:
        if active_session:
            messages.warning(request, 'Sessão anterior foi encerrada automaticamente (tempo excedido).')
        WorkoutSession.objects.create(person_routine=person_routine)
        messages.success(request, 'Treino iniciado! Boa sorte!')

    return redirect('core:workout_execute', pk=pk)


def _ensure_session_sets(session, routine_exercises, previous_logs):
    """Make sure every exercise of the session has its sets.

    New sets are pre-filled from the same set number of the previous session
    (falling back to the heaviest weight of the previous session) so the
    student only changes what is different today.
    """
    logs = {
        log.person_routine_exercise_id: log
        for log in SessionExercise.objects.filter(session=session).prefetch_related('sets')
    }
    for prx in routine_exercises:
        log = logs.get(prx.pk)
        if log is None:
            log = logs[prx.pk] = SessionExercise.objects.create(session=session, person_routine_exercise=prx)
        existing = {x.number for x in log.sets.all()}
        previous = previous_logs.get(prx.exercise_id)
        previous_sets = {x.number: x for x in previous.sets.all()} if previous else {}
        new_sets = []
        for number in range(1, prx.sets + 1):
            if number in existing:
                continue
            prev_set = previous_sets.get(number)
            weight = prev_set.weight if prev_set and prev_set.weight is not None else (
                previous.weight_used if previous else None)
            new_sets.append(SessionSet(session_exercise=log, number=number, weight=weight, reps=prx.reps))
        if new_sets:
            SessionSet.objects.bulk_create(new_sets)
    return logs


@student_required
def workout_execute(request, pk, person):
    """Execute workout: timer, per-set tracking, rest timer, previous values and records"""
    person_routine = get_object_or_404(PersonRoutine, pk=pk, person=person)

    active_session = person_routine.sessions.filter(is_completed=False).first()
    if _close_if_stale(active_session):
        messages.warning(request, 'Sessão anterior foi encerrada automaticamente (tempo excedido).')
        active_session = None
    if not active_session:
        messages.warning(request, 'Nenhuma sessão ativa encontrada. Inicie um novo treino.')
        return redirect('core:my_workouts')

    routine_exercises = list(person_routine.exercises.select_related('exercise').order_by('order'))

    # Previous data: last completed session of this routine, matched by Exercise
    last_completed_session = person_routine.sessions.filter(is_completed=True).order_by('-end_time').first()
    previous_logs = {}
    if last_completed_session:
        for log in SessionExercise.objects.filter(
            session=last_completed_session
        ).select_related('person_routine_exercise', 'session').prefetch_related('sets').order_by('person_routine_exercise__order'):
            previous_logs.setdefault(log.person_routine_exercise.exercise_id, log)

    logs = _ensure_session_sets(active_session, routine_exercises, previous_logs)

    records = {
        row['person_routine_exercise__exercise_id']: row['best']
        for row in SessionExercise.objects.filter(
            session__person_routine__person=person, weight_used__isnull=False
        ).exclude(session=active_session).values(
            'person_routine_exercise__exercise_id'
        ).annotate(best=Max('weight_used'))
    }

    exercises = []
    for prx in routine_exercises:
        log = logs[prx.pk]
        sets = list(log.sets.order_by('number'))
        previous = previous_logs.get(prx.exercise_id)
        exercises.append({
            'prx': prx,
            'log': log,
            'sets': sets,
            'done_count': sum(1 for x in sets if x.is_done),
            'previous': previous,
            'previous_weight': previous.weight_used if previous else None,
            'previous_date': previous.session.start_time if previous else None,
            'record': records.get(prx.exercise_id),
        })

    stale_in = max(0, int((active_session.start_time + timedelta(minutes=MAX_SESSION_MINUTES)
                           - timezone.now()).total_seconds()))
    return render(request, 'core/workout_execute.html', {
        'person_routine': person_routine,
        'active_session': active_session,
        'exercises': exercises,
        'elapsed_seconds': active_session.elapsed_seconds(),
        'stale_in_seconds': stale_in,
        'all_done': all(e['done_count'] == len(e['sets']) for e in exercises) if exercises else False,
    })


@student_required
@require_POST
def workout_end(request, pk, person):
    """End a workout session, saving the student's final comment"""
    session = get_object_or_404(WorkoutSession, pk=pk, person_routine__person=person)
    if not session.is_completed:
        session.notes = request.POST.get('notes', '').strip()[:2000]
        session.save(update_fields=['notes'])
    session.end_session()
    messages.success(request, f'Treino concluído! Duração: {session.duration_minutes} minutos')
    return redirect('core:my_workouts')


def _active_session_or_404(pk, person):
    return get_object_or_404(
        WorkoutSession, pk=pk, person_routine__person=person, is_completed=False)


@student_required
@require_POST
def workout_pause(request, pk, person):
    session = _active_session_or_404(pk, person)
    session.pause()
    return JsonResponse({'success': True, 'paused': True, 'elapsed_seconds': session.elapsed_seconds()})


@student_required
@require_POST
def workout_resume(request, pk, person):
    session = _active_session_or_404(pk, person)
    session.resume()
    return JsonResponse({'success': True, 'paused': False, 'elapsed_seconds': session.elapsed_seconds()})


@login_required
def session_detail(request, pk):
    """View a workout session: the trainer sees any session, a student only their own"""
    session = get_object_or_404(
        WorkoutSession.objects.select_related('person_routine__person'), pk=pk
    )
    person_routine = session.person_routine

    if not is_trainer(request.user) and person_routine.person.user_id != request.user.pk:
        raise PermissionDenied

    session_exercises = SessionExercise.objects.filter(
        session=session
    ).select_related('person_routine_exercise__exercise').prefetch_related('sets').order_by(
        'person_routine_exercise__order')

    exercises_data = [{
        'exercise': se.person_routine_exercise,
        'completed_at': se.completed_at,
        'weight_used': se.weight_used,
        'is_completed': se.is_completed,
        'notes': se.notes,
        'sets': list(se.sets.all()),
    } for se in session_exercises]

    return render(request, 'core/session_detail.html', {
        'session': session,
        'person_routine': person_routine,
        'student': person_routine.person,
        'exercises_data': exercises_data
    })


def _parse_json_body(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


@student_required
@require_POST
def workout_set_update(request, pk, person):
    """Idempotent update of one set: any of weight / reps / done.

    Sends absolute values (not toggles), so a request queued while offline can
    be replayed safely.
    """
    workout_set = get_object_or_404(
        SessionSet.objects.select_related('session_exercise'),
        pk=pk,
        session_exercise__session__person_routine__person=person,
        session_exercise__session__is_completed=False,
    )
    data = _parse_json_body(request)
    if data is None:
        return JsonResponse({'success': False, 'error': 'Dados inválidos'}, status=400)

    try:
        if 'weight' in data:
            raw = data['weight']
            if raw is None or str(raw).strip() == '':
                workout_set.weight = None
            else:
                weight = Decimal(str(raw).replace(',', '.')).quantize(Decimal('0.01'))
                if not weight.is_finite() or weight < 0 or weight > Decimal('9999.99'):
                    raise ValueError('weight')
                workout_set.weight = weight
        if 'reps' in data:
            raw = data['reps']
            if raw is None or str(raw).strip() == '':
                workout_set.reps = None
            else:
                reps = int(str(raw))
                if reps < 0 or reps > 999:
                    raise ValueError('reps')
                workout_set.reps = reps
        if 'done' in data:
            if not isinstance(data['done'], bool):
                raise ValueError('done')
            if data['done'] and not workout_set.is_done:
                workout_set.completed_at = timezone.now()
            elif not data['done']:
                workout_set.completed_at = None
            workout_set.is_done = data['done']
    except (ValueError, InvalidOperation):
        return JsonResponse({'success': False, 'error': 'Valor inválido'}, status=400)

    workout_set.save()
    log = workout_set.session_exercise
    done, total = log.sync_from_sets()
    return JsonResponse({
        'success': True,
        'done': workout_set.is_done,
        'exercise_completed': log.is_completed,
        'done_sets': done,
        'total_sets': total,
    })


@student_required
@require_POST
def workout_exercise_notes(request, pk, person):
    """Student's comment on one exercise of the running session"""
    log = get_object_or_404(
        SessionExercise,
        pk=pk,
        session__person_routine__person=person,
        session__is_completed=False,
    )
    data = _parse_json_body(request)
    if data is None or not isinstance(data.get('notes', ''), str):
        return JsonResponse({'success': False, 'error': 'Dados inválidos'}, status=400)
    log.notes = data.get('notes', '').strip()[:1000]
    log.save(update_fields=['notes'])
    return JsonResponse({'success': True})


@student_required
def my_exercise_progress(request, exercise_pk, person):
    """Evolution and personal record of one exercise"""
    exercise = get_object_or_404(Exercise, pk=exercise_pk)
    logs = list(SessionExercise.objects.filter(
        session__person_routine__person=person,
        person_routine_exercise__exercise=exercise,
        weight_used__isnull=False,
    ).select_related('session').prefetch_related('sets').order_by('session__start_time'))

    points = [{'date': log.session.start_time.strftime('%d/%m/%y'), 'weight': float(log.weight_used)}
              for log in logs]
    record_log = max(logs, key=lambda l: (l.weight_used, l.session.start_time), default=None)
    history = []
    for log in reversed(logs[-10:]):
        done_sets = [x for x in log.sets.all() if x.is_done]
        history.append({
            'log': log,
            'sets': done_sets,
        })
    return render(request, 'core/exercise_progress.html', {
        'exercise': exercise,
        'chart_points': points,
        'record_log': record_log,
        'history': history,
        'sessions_count': len(logs),
    })


@staff_required
def api_exercises_list(request):
    """API endpoint to get all active exercises for routine form"""
    exercises = Exercise.objects.filter(is_active=True).select_related('muscle_group').order_by('exercise_name')

    exercises_data = []
    for exercise in exercises:
        thumbnail_url = exercise.thumbnail.url if exercise.thumbnail else '/static/images/no-image.svg'

        exercises_data.append({
            'id': exercise.pk,
            'name': exercise.exercise_name,
            'muscle_group_id': exercise.muscle_group.id,
            'muscle_group_display': exercise.muscle_group.name,
            'default_sets': exercise.default_sets,
            'default_reps': exercise.default_reps,
            'thumbnail': thumbnail_url
        })

    return JsonResponse(exercises_data, safe=False)


@staff_required
def api_routines_list(request):
    """API endpoint to get all active routine templates"""
    routines = Routine.objects.filter(is_active=True).prefetch_related('exercises__exercise')

    routines_data = []
    for routine in routines:
        exercises_count = routine.exercises.count()

        routines_data.append({
            'id': routine.pk,
            'name': routine.routine_name,
            'description': routine.description,
            'difficulty_level': routine.difficulty_level,
            'difficulty_display': routine.get_difficulty_level_display(),
            'estimated_duration': routine.estimated_duration,
            'exercises_count': exercises_count,
        })

    return JsonResponse(routines_data, safe=False)


@staff_required
def api_routine_exercises(request, pk):
    """API endpoint to get exercises from a specific routine template"""
    routine = get_object_or_404(Routine, pk=pk)
    routine_exercises = RoutineExercise.objects.filter(routine=routine).select_related('exercise').order_by('order')

    exercises_data = []
    for routine_exercise in routine_exercises:
        exercises_data.append({
            'exercise_id': routine_exercise.exercise.id,
            'name': routine_exercise.exercise.exercise_name,
            'order': routine_exercise.order,
            'sets': routine_exercise.sets,
            'reps': routine_exercise.reps,
            'rest_seconds': routine_exercise.rest_seconds,
            'notes': routine_exercise.notes,
        })

    return JsonResponse(exercises_data, safe=False)


# ============= HISTORY AND PROGRESS =============

@student_required
def my_history(request, person):
    """Student's workout history and personal records"""
    completed_sessions = list(WorkoutSession.objects.filter(
        person_routine__person=person,
        is_completed=True
    ).select_related('person_routine').order_by('-start_time')[:20])

    records = SessionExercise.objects.filter(
        session__person_routine__person=person,
        weight_used__isnull=False,
    ).values(
        'person_routine_exercise__exercise_id',
        'person_routine_exercise__exercise__exercise_name',
    ).annotate(
        best=Max('weight_used'),
        sessions=Count('session', distinct=True),
        last=Max('session__start_time'),
    ).order_by('person_routine_exercise__exercise__exercise_name')

    return render(request, 'core/my_history.html', {
        'person': person,
        'completed_sessions': completed_sessions,
        'records': records,
        'chart_data': json.dumps(_chart_points(completed_sessions, with_workout_name=True))
    })


@staff_required
def student_history(request, pk):
    """View student's workout history with detailed statistics"""
    from django.db.models import Avg
    from datetime import timedelta

    student = get_object_or_404(Person, pk=pk)

    # Sessões concluídas
    completed_sessions = WorkoutSession.objects.filter(
        person_routine__person=student,
        is_completed=True
    ).select_related('person_routine').order_by('-start_time')

    # Estatísticas gerais
    total_sessions = completed_sessions.count()

    # Sessões do último mês
    one_month_ago = timezone.now() - timedelta(days=30)
    sessions_last_month = completed_sessions.filter(start_time__gte=one_month_ago).count()

    # Duração média
    avg_duration = completed_sessions.aggregate(avg=Avg('duration_minutes'))['avg'] or 0

    # Total de treinos atribuídos (ativos e inativos)
    total_routines_assigned = PersonRoutine.objects.filter(person=student).count()
    active_routines = PersonRoutine.objects.filter(person=student, is_active=True).count()

    # Última sessão
    last_session = completed_sessions.first()

    # Sessões por treino (para ver qual treino é mais realizado)
    sessions_by_routine = completed_sessions.values(
        'person_routine__routine_name'
    ).annotate(
        count=Count('id'),
        avg_duration=Avg('duration_minutes')
    ).order_by('-count')

    # Dados para gráfico de evolução (últimas 30 sessões)
    recent_sessions = list(completed_sessions[:30])

    chart_data = _chart_points(recent_sessions)

    # Frequência semanal (últimos 3 meses)
    three_months_ago = timezone.now() - timedelta(days=90)
    sessions_three_months = completed_sessions.filter(start_time__gte=three_months_ago).count()
    weeks_in_period = 12  # aproximadamente 3 meses
    avg_weekly_frequency = sessions_three_months / weeks_in_period if weeks_in_period > 0 else 0

    return render(request, 'core/student_history.html', {
        'student': student,
        'completed_sessions': completed_sessions,
        'recent_sessions': recent_sessions,
        'total_sessions': total_sessions,
        'sessions_last_month': sessions_last_month,
        'avg_duration': round(avg_duration, 1),
        'total_routines_assigned': total_routines_assigned,
        'active_routines': active_routines,
        'last_session': last_session,
        'sessions_by_routine': sessions_by_routine,
        'avg_weekly_frequency': round(avg_weekly_frequency, 1),
        'chart_data': json.dumps(chart_data),
    })


# ============= AI WORKOUT GENERATION =============

@staff_required
def routine_generate_ai(request):
    """Generate workout routine using AI"""
    if request.method == 'POST':
        form = AIWorkoutGenerationForm(request.POST)
        if form.is_valid():
            from .ai_service import AIGenerationError, generate_workout_with_ai

            description = form.cleaned_data['description']
            difficulty = form.cleaned_data['difficulty_level']
            duration = form.cleaned_data['estimated_duration']

            try:
                routine = generate_workout_with_ai(description, difficulty, duration)
                messages.success(request, 'Treino gerado com IA com sucesso!')
                return redirect('core:routine_detail', pk=routine.pk)
            except AIGenerationError as e:
                messages.error(request, f'Erro ao gerar treino: {e}')
    else:
        form = AIWorkoutGenerationForm()

    return render(request, 'core/routine_generate_ai.html', {'form': form, 'title': 'Gerar Treino com IA'})


# ============= PWA =============

def pwa_manifest(request):
    return render(request, 'pwa/manifest.webmanifest', content_type='application/manifest+json')


def service_worker(request):
    """Served from the site root so its scope covers the whole app"""
    response = render(request, 'pwa/sw.js', content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response


def offline_page(request):
    return render(request, 'pwa/offline.html')


def healthz(request):
    """Liveness/readiness probe for Docker: 200 only if the database answers."""
    from django.db import connection
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        return JsonResponse({'status': 'error'}, status=503)
    return JsonResponse({'status': 'ok'})
