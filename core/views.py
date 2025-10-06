from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count, Q
from .models import (
    MuscleGroup, Person, Exercise, Routine, RoutineExercise,
    PersonRoutine, PersonRoutineExercise, WorkoutSession, SessionExercise
)
from .forms import (
    PersonForm, ExerciseForm, RoutineForm, PersonRoutineForm,
    AIWorkoutGenerationForm
)
import json


def home(request):
    """Home page"""
    context = {
        'total_students': Person.objects.filter(is_active=True).count(),
        'total_exercises': Exercise.objects.filter(is_active=True).count(),
        'total_routines': Routine.objects.filter(is_active=True).count(),
    }
    return render(request, 'core/home.html', context)


# ============= STUDENT VIEWS =============

@login_required
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


@login_required
def student_create(request):
    """Create a new student"""
    if request.method == 'POST':
        form = PersonForm(request.POST, request.FILES)
        if form.is_valid():
            email = form.cleaned_data['email']

            # Criar usuário com username = email
            user = User.objects.create_user(
                username=email,
                email=email,
                is_active=False  # Inativo até confirmar o email
            )

            # Criar Person associado ao usuário
            person = form.save(commit=False)
            person.user = user
            person.save()

            # Enviar email de confirmação via allauth
            from allauth.account.models import EmailAddress
            EmailAddress.objects.create(
                user=user,
                email=email,
                primary=True,
                verified=False
            )

            # Enviar email de confirmação
            from allauth.account.utils import send_email_confirmation
            send_email_confirmation(request, user, signup=True)

            messages.success(request, f'Aluno cadastrado com sucesso! Um email de confirmação foi enviado para {email}.')
            return redirect('core:student_list')
    else:
        form = PersonForm()
    return render(request, 'core/student_form.html', {'form': form, 'title': 'Cadastrar Aluno'})


@login_required
def student_detail(request, pk):
    """View student details"""
    from django.db.models import Avg
    from datetime import datetime, timedelta
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


@login_required
def student_edit(request, pk):
    """Edit student"""
    student = get_object_or_404(Person, pk=pk)
    if request.method == 'POST':
        form = PersonForm(request.POST, request.FILES, instance=student)
        if form.is_valid():
            email = form.cleaned_data['email']

            # Verificar se o email foi alterado
            if student.user.email != email:
                # Atualizar email e username do usuário
                student.user.email = email
                student.user.username = email
                student.user.save()

                # Atualizar EmailAddress do allauth
                from allauth.account.models import EmailAddress
                email_address = EmailAddress.objects.filter(user=student.user, primary=True).first()
                if email_address:
                    email_address.email = email
                    email_address.verified = False
                    email_address.save()
                else:
                    EmailAddress.objects.create(
                        user=student.user,
                        email=email,
                        primary=True,
                        verified=False
                    )

                # Enviar novo email de confirmação
                from allauth.account.utils import send_email_confirmation
                send_email_confirmation(request, student.user, signup=False)

                messages.success(request, f'Aluno atualizado com sucesso! Um novo email de confirmação foi enviado para {email}.')
            else:
                messages.success(request, 'Aluno atualizado com sucesso!')

            form.save()
            return redirect('core:student_detail', pk=pk)
    else:
        form = PersonForm(instance=student)
    return render(request, 'core/student_form.html', {'form': form, 'title': 'Editar Aluno'})


@login_required
def my_profile(request):
    """Student edits their own profile"""
    try:
        person = Person.objects.get(user=request.user)
    except Person.DoesNotExist:
        messages.error(request, 'Perfil de aluno não encontrado para este usuário.')
        return redirect('core:home')

    if request.method == 'POST':
        from .forms import StudentProfileForm
        form = StudentProfileForm(request.POST, request.FILES, instance=person)
        if form.is_valid():
            form.save()
            messages.success(request, 'Seu perfil foi atualizado com sucesso!')
            return redirect('core:my_profile')
    else:
        from .forms import StudentProfileForm
        form = StudentProfileForm(instance=person)

    # Mostrar email atual (somente leitura)
    user_email = request.user.email

    return render(request, 'core/my_profile.html', {
        'form': form,
        'title': 'Meu Perfil',
        'user_email': user_email,
        'person': person
    })


# ============= EXERCISE VIEWS =============

@login_required
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


@login_required
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


@login_required
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
    completed_count = PersonRoutineExercise.objects.filter(
        exercise=exercise,
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


@login_required
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

@login_required
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
    from django.db.models import Count
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


@login_required
def routine_create(request):
    """Create a new routine"""
    if request.method == 'POST':
        form = RoutineForm(request.POST)
        if form.is_valid():
            routine = form.save()

            # Processar exercícios selecionados
            import json
            exercises_data = request.POST.get('exercises_data', '[]')
            try:
                exercises_list = json.loads(exercises_data)

                # Adicionar exercícios
                for idx, exercise_data in enumerate(exercises_list, start=1):
                    exercise = Exercise.objects.get(pk=exercise_data['id'])
                    RoutineExercise.objects.create(
                        routine=routine,
                        exercise=exercise,
                        order=idx,
                        sets=exercise_data.get('sets', exercise.default_sets),
                        reps=exercise_data.get('reps', exercise.default_reps),
                        rest_seconds=exercise_data.get('rest', 60)
                    )

                messages.success(request, 'Treino criado com sucesso!')
                return redirect('core:routine_detail', pk=routine.pk)
            except json.JSONDecodeError:
                messages.error(request, 'Erro ao processar exercícios.')
    else:
        form = RoutineForm()

    # Buscar grupos musculares
    muscle_groups = MuscleGroup.objects.filter(is_active=True).order_by('order')

    return render(request, 'core/routine_form.html', {
        'form': form,
        'title': 'Criar Treino',
        'routine': None,
        'routine_exercises': [],
        'muscle_groups': muscle_groups
    })


@login_required
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


@login_required
def routine_edit(request, pk):
    """Edit routine"""
    routine = get_object_or_404(Routine, pk=pk)

    if request.method == 'POST':
        form = RoutineForm(request.POST, instance=routine)
        if form.is_valid():
            routine = form.save()

            # Processar exercícios selecionados
            import json
            exercises_data = request.POST.get('exercises_data', '[]')
            try:
                exercises_list = json.loads(exercises_data)

                # Remover exercícios existentes
                routine.exercises.all().delete()

                # Adicionar novos exercícios
                for idx, exercise_data in enumerate(exercises_list, start=1):
                    exercise = Exercise.objects.get(pk=exercise_data['id'])
                    RoutineExercise.objects.create(
                        routine=routine,
                        exercise=exercise,
                        order=idx,
                        sets=exercise_data.get('sets', exercise.default_sets),
                        reps=exercise_data.get('reps', exercise.default_reps),
                        rest_seconds=exercise_data.get('rest', 60)
                    )

                messages.success(request, 'Treino atualizado com sucesso!')
                return redirect('core:routine_detail', pk=pk)
            except json.JSONDecodeError:
                messages.error(request, 'Erro ao processar exercícios.')
    else:
        form = RoutineForm(instance=routine)

    # Buscar exercícios atuais da rotina
    routine_exercises = routine.exercises.all().select_related('exercise').order_by('order')

    # Buscar grupos musculares
    muscle_groups = MuscleGroup.objects.filter(is_active=True).order_by('order')

    return render(request, 'core/routine_form.html', {
        'form': form,
        'title': 'Editar Treino',
        'routine': routine,
        'routine_exercises': routine_exercises,
        'muscle_groups': muscle_groups
    })


# ============= PERSON ROUTINE VIEWS =============

@login_required
def person_routine_create(request):
    """Assign a routine template to a student (creates personalized copy)"""
    if request.method == 'POST':
        # Get template ID from POST data
        template_id = request.POST.get('template_id')

        # Get exercises data (JSON)
        exercises_data = request.POST.get('exercises_data', '[]')

        form = PersonRoutineForm(request.POST)
        if form.is_valid():
            person_routine = form.save(commit=False)

            # Set template reference if provided
            if template_id:
                try:
                    template = Routine.objects.get(pk=template_id)
                    person_routine.routine_template = template
                except Routine.DoesNotExist:
                    pass

            person_routine.save()

            # Process exercises
            try:
                exercises_list = json.loads(exercises_data)
                for idx, exercise_data in enumerate(exercises_list, start=1):
                    PersonRoutineExercise.objects.create(
                        person_routine=person_routine,
                        exercise_id=exercise_data['exercise_id'],
                        order=exercise_data.get('order', idx),
                        sets=exercise_data.get('sets', 3),
                        reps=exercise_data.get('reps', 12),
                        rest_seconds=exercise_data.get('rest_seconds', 60),
                        notes=exercise_data.get('notes', '')
                    )
            except json.JSONDecodeError:
                pass

            messages.success(request, f'Treino "{person_routine.routine_name}" atribuído a {person_routine.person.person_name} com sucesso!')
            return redirect('core:student_detail', pk=person_routine.person.pk)
    else:
        # Pre-fill person if provided in URL
        person_id = request.GET.get('person')
        initial_data = {}
        selected_person = None
        if person_id:
            initial_data['person'] = person_id
            try:
                selected_person = Person.objects.get(pk=person_id)
            except Person.DoesNotExist:
                pass
        form = PersonRoutineForm(initial=initial_data)

    return render(request, 'core/person_routine_form.html', {
        'form': form,
        'selected_person': selected_person
    })


@login_required
def person_routine_detail(request, pk):
    """View person routine progress - all sessions for this specific routine"""
    from django.db.models import Avg, Count
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
    recent_sessions = completed_sessions[:20]

    # Preparar dados para o gráfico (duração e carga média por sessão)
    from .models import SessionExercise

    chart_data = []
    for session in reversed(list(recent_sessions)):
        # Calcular carga média dos exercícios desta sessão específica usando SessionExercise
        session_exercises = SessionExercise.objects.filter(
            session=session,
            is_completed=True,
            weight_used__isnull=False
        )
        avg_weight = session_exercises.aggregate(avg=Avg('weight_used'))['avg']
        avg_weight = float(avg_weight) if avg_weight else 0

        chart_data.append({
            'date': session.start_time.strftime('%d/%m'),
            'duration': session.duration_minutes or 0,
            'avg_weight': round(avg_weight, 1)
        })

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


@login_required
def person_routine_edit(request, pk):
    """Edit a person's routine"""
    person_routine = get_object_or_404(PersonRoutine, pk=pk)

    if request.method == 'POST':
        # Get exercises data (JSON)
        exercises_data = request.POST.get('exercises_data', '[]')

        form = PersonRoutineForm(request.POST, instance=person_routine)
        if form.is_valid():
            person_routine = form.save()

            # Update exercises
            try:
                exercises_list = json.loads(exercises_data)

                # Delete existing exercises
                person_routine.exercises.all().delete()

                # Create new exercises
                for idx, exercise_data in enumerate(exercises_list, start=1):
                    PersonRoutineExercise.objects.create(
                        person_routine=person_routine,
                        exercise_id=exercise_data['exercise_id'],
                        order=exercise_data.get('order', idx),
                        sets=exercise_data.get('sets', 3),
                        reps=exercise_data.get('reps', 12),
                        rest_seconds=exercise_data.get('rest_seconds', 60),
                        notes=exercise_data.get('notes', '')
                    )
            except json.JSONDecodeError:
                pass

            messages.success(request, f'Treino "{person_routine.routine_name}" atualizado com sucesso!')
            return redirect('core:student_detail', pk=person_routine.person.pk)
    else:
        form = PersonRoutineForm(instance=person_routine)

    # Get current exercises
    current_exercises = person_routine.exercises.all().select_related('exercise').order_by('order')

    return render(request, 'core/person_routine_form.html', {
        'form': form,
        'person_routine': person_routine,
        'current_exercises': current_exercises,
        'is_edit': True,
        'selected_person': person_routine.person  # Always show person as read-only in edit mode
    })


@login_required
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

@login_required
def my_workouts(request):
    """Student's active workouts"""
    from datetime import timedelta
    from django.db.models import Prefetch

    try:
        person = request.user.person

        # Prefetch apenas as últimas sessões completadas de cada exercício
        last_session_prefetch = Prefetch(
            'session_logs',
            queryset=SessionExercise.objects.filter(
                session__is_completed=True,
                is_completed=True
            ).select_related('session').order_by('-completed_at')[:1],
            to_attr='last_session_log'
        )

        # Prefetch dos exercícios com suas sessões e dados do exercício
        exercises_prefetch = Prefetch(
            'exercises',
            queryset=PersonRoutineExercise.objects.select_related('exercise').prefetch_related(
                last_session_prefetch
            ).order_by('order')
        )

        active_routines = PersonRoutine.objects.filter(
            person=person,
            is_active=True
        ).select_related('routine_template').prefetch_related(
            'sessions',
            exercises_prefetch
        )

        # Check for active sessions and auto-close old ones
        routines_with_sessions = []
        for routine in active_routines:
            active_session = routine.sessions.filter(is_completed=False).first()

            # Auto-close sessions older than 120 minutes
            if active_session:
                time_elapsed = timezone.now() - active_session.start_time
                if time_elapsed > timedelta(minutes=120):
                    active_session.end_session()
                    active_session = None

            routines_with_sessions.append({
                'routine': routine,
                'active_session': active_session
            })

        return render(request, 'core/my_workouts.html', {
            'person': person,
            'routines_with_sessions': routines_with_sessions
        })
    except Person.DoesNotExist:
        messages.warning(request, 'Você não está cadastrado como aluno.')
        return redirect('core:home')


@login_required
def workout_start(request, pk):
    """Start a workout session or recover active session"""
    from datetime import timedelta

    person_routine = get_object_or_404(PersonRoutine, pk=pk)

    # Check for active session
    active_session = person_routine.sessions.filter(is_completed=False).first()

    if active_session:
        # Check if session is too old (>120 minutes) - auto close it
        time_elapsed = timezone.now() - active_session.start_time
        if time_elapsed > timedelta(minutes=120):
            active_session.end_session()
            messages.warning(request, 'Sessão anterior foi encerrada automaticamente (tempo excedido).')
            # Create new session
            session = WorkoutSession.objects.create(person_routine=person_routine)
            messages.success(request, 'Novo treino iniciado! Boa sorte!')
        else:
            # Recover active session
            messages.info(request, 'Recuperando sua sessão de treino em andamento...')
    else:
        # Create new workout session
        session = WorkoutSession.objects.create(person_routine=person_routine)
        messages.success(request, 'Treino iniciado! Boa sorte!')

    return redirect('core:workout_execute', pk=pk)


@login_required
def workout_execute(request, pk):
    """Execute workout with timer - shows previous session data for reference"""
    from datetime import timedelta
    from .models import SessionExercise

    person_routine = get_object_or_404(PersonRoutine, pk=pk)

    # Get active (current) session
    active_session = person_routine.sessions.filter(is_completed=False).first()

    # Auto-close sessions older than 120 minutes
    if active_session:
        time_elapsed = timezone.now() - active_session.start_time
        if time_elapsed > timedelta(minutes=120):
            active_session.end_session()
            messages.warning(request, 'Sessão anterior foi encerrada automaticamente (tempo excedido).')
            active_session = None

    # If no active session, redirect to start one
    if not active_session:
        messages.warning(request, 'Nenhuma sessão ativa encontrada. Inicie um novo treino.')
        return redirect('core:my_workouts')

    # Get routine exercises for current session
    routine_exercises = person_routine.exercises.select_related('exercise').order_by('order')

    # Create SessionExercise entries for active session if they don't exist
    for exercise in routine_exercises:
        SessionExercise.objects.get_or_create(
            session=active_session,
            person_routine_exercise=exercise
        )

    # Get last completed session
    last_completed_session = person_routine.sessions.filter(is_completed=True).order_by('-end_time').first()

    # Build exercises with previous and current session data
    exercises_with_previous = []
    for exercise in routine_exercises:
        # Get current session data
        current_session_exercise = SessionExercise.objects.filter(
            session=active_session,
            person_routine_exercise=exercise
        ).first()

        # Get previous session data (from last completed session for this same exercise)
        previous_session_exercise = None
        if last_completed_session:
            previous_session_exercise = SessionExercise.objects.filter(
                session=last_completed_session,
                person_routine_exercise__exercise=exercise.exercise  # Match by exercise, not by PersonRoutineExercise
            ).first()

        exercise_data = {
            'current': exercise,
            'current_session_id': current_session_exercise.id if current_session_exercise else None,
            'current_weight': current_session_exercise.weight_used if current_session_exercise else None,
            'current_is_completed': current_session_exercise.is_completed if current_session_exercise else False,
            'previous_date': previous_session_exercise.completed_at if previous_session_exercise else None,
            'previous_weight': previous_session_exercise.weight_used if previous_session_exercise else None,
        }
        exercises_with_previous.append(exercise_data)

    return render(request, 'core/workout_execute.html', {
        'person_routine': person_routine,
        'exercises_with_previous': exercises_with_previous,
        'active_session': active_session,
        'last_completed_session': last_completed_session
    })


@login_required
def workout_end(request, pk):
    """End a workout session"""
    session = get_object_or_404(WorkoutSession, pk=pk)
    session.end_session()
    messages.success(request, f'Treino concluído! Duração: {session.duration_minutes} minutos')
    return redirect('core:my_workouts')


@login_required
def session_detail(request, pk):
    """View completed workout session details (read-only for admin)"""
    from .models import SessionExercise

    session = get_object_or_404(WorkoutSession, pk=pk)
    person_routine = session.person_routine

    # Get session-specific exercise execution data
    session_exercises = SessionExercise.objects.filter(
        session=session
    ).select_related('person_routine_exercise__exercise').order_by('person_routine_exercise__order')

    # Build exercise data for this specific session
    exercises_data = []
    for session_exercise in session_exercises:
        exercises_data.append({
            'exercise': session_exercise.person_routine_exercise,
            'completed_at': session_exercise.completed_at,
            'weight_used': session_exercise.weight_used,
            'is_completed': session_exercise.is_completed
        })

    return render(request, 'core/session_detail.html', {
        'session': session,
        'person_routine': person_routine,
        'student': person_routine.person,
        'exercises_data': exercises_data
    })


@login_required
def exercise_toggle_completion(request, pk):
    """Toggle exercise completion status in active session"""
    if request.method == 'POST':
        from .models import SessionExercise

        # pk here is the SessionExercise ID, not PersonRoutineExercise
        session_exercise = get_object_or_404(SessionExercise, pk=pk)
        session_exercise.is_completed = not session_exercise.is_completed

        if session_exercise.is_completed:
            session_exercise.mark_completed()
        else:
            session_exercise.completed_at = None
            session_exercise.save()

        return JsonResponse({
            'success': True,
            'is_completed': session_exercise.is_completed
        })
    return JsonResponse({'success': False}, status=400)


@login_required
def exercise_update_weight(request, pk):
    """Update exercise weight in active session"""
    if request.method == 'POST':
        from .models import SessionExercise

        # pk here is the SessionExercise ID
        session_exercise = get_object_or_404(SessionExercise, pk=pk)
        data = json.loads(request.body)
        weight = data.get('weight')

        if weight:
            session_exercise.weight_used = float(weight)
            session_exercise.save()
            return JsonResponse({'success': True})

        return JsonResponse({'success': False, 'error': 'No weight provided'}, status=400)
    return JsonResponse({'success': False}, status=400)


@login_required
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


@login_required
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


@login_required
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

@login_required
def my_history(request):
    """Student's workout history"""
    from django.db.models import Avg
    from .models import SessionExercise
    import json

    try:
        person = request.user.person
        completed_sessions = WorkoutSession.objects.filter(
            person_routine__person=person,
            is_completed=True
        ).order_by('-start_time')[:20]

        # Preparar dados para o gráfico (incluindo carga média)
        chart_data = []
        for session in reversed(list(completed_sessions)):
            # Calcular carga média dos exercícios desta sessão
            session_exercises = SessionExercise.objects.filter(
                session=session,
                is_completed=True,
                weight_used__isnull=False
            )
            avg_weight = session_exercises.aggregate(avg=Avg('weight_used'))['avg']
            avg_weight = float(avg_weight) if avg_weight else 0

            chart_data.append({
                'date': session.start_time.strftime('%d/%m'),
                'duration': session.duration_minutes or 0,
                'avg_weight': round(avg_weight, 1),
                'workout': session.person_routine.routine_name[:15]
            })

        return render(request, 'core/my_history.html', {
            'person': person,
            'completed_sessions': completed_sessions,
            'chart_data': json.dumps(chart_data)
        })
    except Person.DoesNotExist:
        messages.warning(request, 'Você não está cadastrado como aluno.')
        return redirect('core:home')


@login_required
def student_history(request, pk):
    """View student's workout history with detailed statistics"""
    from django.db.models import Avg, Sum, Count
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
    recent_sessions = completed_sessions[:30]

    # Preparar dados para o gráfico (duração e carga média por sessão)
    from .models import SessionExercise

    chart_data = []
    for session in reversed(list(recent_sessions)):
        # Calcular carga média dos exercícios desta sessão específica usando SessionExercise
        session_exercises = SessionExercise.objects.filter(
            session=session,
            is_completed=True,
            weight_used__isnull=False
        )
        avg_weight = session_exercises.aggregate(avg=Avg('weight_used'))['avg']
        avg_weight = float(avg_weight) if avg_weight else 0

        chart_data.append({
            'date': session.start_time.strftime('%d/%m'),
            'duration': session.duration_minutes or 0,
            'avg_weight': round(avg_weight, 1)
        })

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

@login_required
def routine_generate_ai(request):
    """Generate workout routine using AI"""
    if request.method == 'POST':
        form = AIWorkoutGenerationForm(request.POST)
        if form.is_valid():
            from .ai_service import generate_workout_with_ai

            description = form.cleaned_data['description']
            difficulty = form.cleaned_data['difficulty_level']
            duration = form.cleaned_data['estimated_duration']

            try:
                routine = generate_workout_with_ai(description, difficulty, duration)
                messages.success(request, 'Treino gerado com IA com sucesso!')
                return redirect('core:routine_detail', pk=routine.pk)
            except Exception as e:
                messages.error(request, f'Erro ao gerar treino: {str(e)}')
    else:
        form = AIWorkoutGenerationForm()

    return render(request, 'core/routine_generate_ai.html', {'form': form})
