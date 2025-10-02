from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count, Q
from .models import (
    MuscleGroup, Person, Exercise, Routine, RoutineExercise,
    PersonRoutine, PersonRoutineExercise, WorkoutSession
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
        form = PersonForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Aluno cadastrado com sucesso!')
            return redirect('core:student_list')
    else:
        form = PersonForm()
    return render(request, 'core/student_form.html', {'form': form, 'title': 'Cadastrar Aluno'})


@login_required
def student_detail(request, pk):
    """View student details"""
    student = get_object_or_404(Person, pk=pk)
    active_routines = student.routines.filter(is_active=True)
    return render(request, 'core/student_detail.html', {
        'student': student,
        'active_routines': active_routines
    })


@login_required
def student_edit(request, pk):
    """Edit student"""
    student = get_object_or_404(Person, pk=pk)
    if request.method == 'POST':
        form = PersonForm(request.POST, instance=student)
        if form.is_valid():
            form.save()
            messages.success(request, 'Aluno atualizado com sucesso!')
            return redirect('core:student_detail', pk=pk)
    else:
        form = PersonForm(instance=student)
    return render(request, 'core/student_form.html', {'form': form, 'title': 'Editar Aluno'})


# ============= EXERCISE VIEWS =============

@login_required
def exercise_list(request):
    """List all exercises"""
    search = request.GET.get('search', '')
    muscle_group = request.GET.get('muscle_group', '')
    view_mode = request.GET.get('view', 'grid')  # grid ou list

    exercises = Exercise.objects.filter(is_active=True)

    # Filtro por busca
    if search:
        exercises = exercises.filter(
            Q(exercise_name__icontains=search) |
            Q(description__icontains=search)
        )

    # Filtro por grupo muscular
    if muscle_group:
        exercises = exercises.filter(muscle_group=muscle_group)

    exercises = exercises.order_by('muscle_group', 'exercise_name')

    return render(request, 'core/exercise_list.html', {
        'exercises': exercises,
        'muscle_groups': Exercise.MUSCLE_GROUP_CHOICES,
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
    return render(request, 'core/exercise_detail.html', {'exercise': exercise})


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
    total_sessions = WorkoutSession.objects.filter(person_routine__routine=routine).count()
    completed_sessions = WorkoutSession.objects.filter(
        person_routine__routine=routine,
        is_completed=True
    ).count()

    # Duração média das sessões completadas
    from django.db.models import Avg
    avg_duration = WorkoutSession.objects.filter(
        person_routine__routine=routine,
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
    """Assign a routine to a student"""
    if request.method == 'POST':
        form = PersonRoutineForm(request.POST)
        if form.is_valid():
            person_routine = form.save()
            messages.success(request, 'Treino atribuído ao aluno com sucesso!')
            return redirect('core:student_detail', pk=person_routine.person.pk)
    else:
        form = PersonRoutineForm()
    return render(request, 'core/person_routine_form.html', {'form': form})


@login_required
def person_routine_detail(request, pk):
    """View person routine details"""
    person_routine = get_object_or_404(PersonRoutine, pk=pk)
    return render(request, 'core/person_routine_detail.html', {'person_routine': person_routine})


# ============= STUDENT WORKOUT AREA =============

@login_required
def my_workouts(request):
    """Student's active workouts"""
    try:
        person = request.user.person
        active_routines = PersonRoutine.objects.filter(
            person=person,
            is_active=True
        ).select_related('routine')
        return render(request, 'core/my_workouts.html', {
            'person': person,
            'active_routines': active_routines
        })
    except Person.DoesNotExist:
        messages.warning(request, 'Você não está cadastrado como aluno.')
        return redirect('core:home')


@login_required
def workout_start(request, pk):
    """Start a workout session"""
    person_routine = get_object_or_404(PersonRoutine, pk=pk)

    # Create workout session
    session = WorkoutSession.objects.create(person_routine=person_routine)

    # Create exercise executions if they don't exist
    routine_exercises = person_routine.routine.exercises.all()
    for routine_exercise in routine_exercises:
        PersonRoutineExercise.objects.get_or_create(
            person_routine=person_routine,
            exercise=routine_exercise.exercise,
            defaults={
                'sets_completed': 0,
                'reps_completed': routine_exercise.reps
            }
        )

    messages.success(request, 'Treino iniciado! Boa sorte!')
    return redirect('core:workout_execute', pk=pk)


@login_required
def workout_execute(request, pk):
    """Execute workout with timer"""
    person_routine = get_object_or_404(PersonRoutine, pk=pk)

    # Get active session
    active_session = person_routine.sessions.filter(is_completed=False).first()

    # Get routine exercises with executions
    routine_exercises = person_routine.routine.exercises.all().order_by('order')
    exercise_executions = person_routine.exercise_executions.all()

    return render(request, 'core/workout_execute.html', {
        'person_routine': person_routine,
        'routine_exercises': routine_exercises,
        'exercise_executions': exercise_executions,
        'active_session': active_session
    })


@login_required
def workout_end(request, pk):
    """End a workout session"""
    session = get_object_or_404(WorkoutSession, pk=pk)
    session.end_session()
    messages.success(request, f'Treino concluído! Duração: {session.duration_minutes} minutos')
    return redirect('core:my_workouts')


@login_required
def exercise_toggle_completion(request, pk):
    """Toggle exercise completion status"""
    if request.method == 'POST':
        exercise_execution = get_object_or_404(PersonRoutineExercise, pk=pk)
        exercise_execution.is_completed = not exercise_execution.is_completed
        if exercise_execution.is_completed:
            exercise_execution.completed_at = timezone.now()
        else:
            exercise_execution.completed_at = None
        exercise_execution.save()

        return JsonResponse({
            'success': True,
            'is_completed': exercise_execution.is_completed
        })
    return JsonResponse({'success': False}, status=400)


@login_required
def exercise_update_weight(request, pk):
    """Update exercise weight"""
    if request.method == 'POST':
        exercise_execution = get_object_or_404(PersonRoutineExercise, pk=pk)
        data = json.loads(request.body)
        weight = data.get('weight')

        if weight:
            exercise_execution.weight_used = float(weight)
            exercise_execution.save()
            return JsonResponse({'success': True})


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


# ============= HISTORY AND PROGRESS =============

@login_required
def my_history(request):
    """Student's workout history"""
    try:
        person = request.user.person
        completed_sessions = WorkoutSession.objects.filter(
            person_routine__person=person,
            is_completed=True
        ).order_by('-start_time')[:20]

        return render(request, 'core/my_history.html', {
            'person': person,
            'completed_sessions': completed_sessions
        })
    except Person.DoesNotExist:
        messages.warning(request, 'Você não está cadastrado como aluno.')
        return redirect('core:home')


@login_required
def student_history(request, pk):
    """View student's workout history"""
    student = get_object_or_404(Person, pk=pk)
    completed_sessions = WorkoutSession.objects.filter(
        person_routine__person=student,
        is_completed=True
    ).order_by('-start_time')

    return render(request, 'core/student_history.html', {
        'student': student,
        'completed_sessions': completed_sessions
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
