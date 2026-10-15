import csv
import json
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Exists, F, Max, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.templatetags.static import static
from django.urls import reverse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from allauth.account.models import EmailAddress

from .dashboard import INACTIVE_DAYS, INVITE_PENDING, trainer_dashboard
from .forms import (
    AIWorkoutGenerationForm, AppointmentForm, AssessmentForm, ConsentForm, ExerciseForm, PaymentForm,
    PersonForm, PersonRoutineForm, PlanForm, ScheduleSlotForm,
    ProgressPhotoForm, RoutineForm, StatusForm, StudentNoteForm,
)
from .agenda import at, attendance_stats, conflicting_ids, ensure_appointments, parse_week
from .billing import (
    charge_message, delete_payment, month_bounds, month_summary, overdue_rows, parse_month,
    register_payment, suggest_amount, suggest_period,
)
from .consistency import celebrations
from .consent import (
    ALL_KINDS, CURRENT_VERSION as CONSENT_VERSION, DESCRIPTIONS as CONSENT_DESCRIPTIONS, REQUIRED_KINDS,
    current_consents, has_current_consent, is_granted, record_consent,
)
from .exports import build_export
from .history import log_event, log_profile_edit
from .invites import build_invite_url, get_user_from_invite, revoke_invites
from .models import (
    Appointment, Assessment, ConsentRecord, Exercise, MuscleGroup, Payment, Plan, ScheduleSlot, Person, PersonRoutine, PersonRoutineExercise, ProgressPhoto,
    Routine, RoutineExercise, SessionExercise, SessionSet, StudentEvent, StudentNote, WorkoutSession,
)
from .navigation import after_save_url, back_label, safe_next_url
from .onboarding import trainer_steps
from .permissions import is_trainer, staff_required, student_required
from .search import apply_student_search, phone_digits, search_all
from .today import MAX_SESSION_MINUTES, student_today, suggest_index
from .workout_summary import session_summary
from .whatsapp import whatsapp_url

logger = logging.getLogger(__name__)


def home(request):
    """Home: trainer dashboard, student stats, or a login prompt for visitors"""
    context = {}
    user = request.user

    if is_trainer(user):
        context['dashboard'] = trainer_dashboard()
        onboarding = trainer_steps(request.get_full_path())
        context['onboarding'] = None if onboarding['complete'] else onboarding
    elif user.is_authenticated:
        person = Person.objects.filter(user=user).first()
        if person:
            context['is_student'] = True
            context['today'] = student_today(request, person)

    return render(request, 'core/home.html', context)


# ============= SEARCH =============

@staff_required
def search(request):
    """Global search results: students, exercises and routines."""
    result = search_all(request.GET.get('q', ''), limit=20)
    return render(request, 'core/search.html', {
        'result': result, 'q': result['query'],
        'found': sum(g['total'] for g in result['groups']),
    })


@staff_required
def search_suggest(request):
    """The same search for the live box: a few items per group, as JSON (the browser inserts them as text)."""
    result = search_all(request.GET.get('q', ''), limit=5)
    response = JsonResponse({'query': result['query'], 'groups': result['groups']})
    response['Cache-Control'] = 'no-store'
    return response


# ============= STUDENT VIEWS =============

STUDENT_PAGE_SIZE = 24
STUDENT_FILTERS = {
    'trial': 'Experimental',
    'paused': 'Pausado',
    'former': 'Ex-aluno',
    'consent_pending': 'Sem consentimento (LGPD)',
    'plan_expiring': 'Plano vencendo',
    'plan_expired': 'Plano vencido',
    'inactive_7d': 'Sem treinar há 7+ dias',
    'no_routine': 'Sem treino ativo',
    'invite_pending': 'Convite pendente',
}


def _students_queryset():
    """Students with the list statistics computed in the same query (no per-row queries)."""
    sessions = WorkoutSession.objects.filter(person_routine__person=OuterRef('pk'), is_completed=True)
    last_workout = sessions.order_by('-start_time').values('start_time')[:1]
    total = sessions.order_by().values('person_routine__person').annotate(c=Count('pk')).values('c')
    return Person.objects.select_related('user').annotate(
        last_workout_at=Subquery(last_workout),
        total_workouts=Coalesce(Subquery(total), 0),
        has_routine=Exists(PersonRoutine.objects.filter(person=OuterRef('pk'), is_active=True)),
        phone_digits=phone_digits(F('phone')),
    )


def _apply_student_filter(students, name):
    today = timezone.localdate()
    if name == 'plan_expiring':
        return students.filter(is_active=True, plan_expires_on__gte=today,
                               plan_expires_on__lte=today + timedelta(days=Person.PLAN_EXPIRING_DAYS))
    if name == 'plan_expired':
        return students.filter(is_active=True, plan_expires_on__lt=today)
    if name == 'inactive_7d':
        return students.filter(is_active=True, has_routine=True).exclude(INVITE_PENDING).filter(
            Q(last_workout_at__lt=timezone.now() - timedelta(days=INACTIVE_DAYS))
            | Q(last_workout_at__isnull=True))
    if name == 'no_routine':
        return students.filter(is_active=True, has_routine=False)
    if name == 'invite_pending':
        return students.filter(INVITE_PENDING)
    if name in ('trial', 'paused', 'former'):
        return students.filter(status=name)
    if name == 'consent_pending':
        def granted(kind):
            return Exists(ConsentRecord.objects.filter(
                person=OuterRef('pk'), kind=kind, granted=True, version=CONSENT_VERSION))
        return students.filter(is_active=True).exclude(granted(ConsentRecord.Kind.TERMS) & granted(ConsentRecord.Kind.HEALTH_DATA))
    return students


@staff_required
def student_list(request):
    """List students with search, filters, statistics and pagination"""
    search = request.GET.get('search', '')
    status = request.GET.get('status', '')
    filter_name = request.GET.get('filtro', '')
    if filter_name not in STUDENT_FILTERS:
        filter_name = ''
    view_mode = request.GET.get('view', request.COOKIES.get('student_view_mode', 'grid'))

    students = apply_student_search(_students_queryset(), search)
    if status == 'active':
        students = students.filter(is_active=True)
    elif status == 'inactive':
        students = students.filter(is_active=False)
    students = _apply_student_filter(students, filter_name).order_by('person_name', 'pk')

    page = Paginator(students, STUDENT_PAGE_SIZE).get_page(request.GET.get('page'))

    # Query string that keeps search/filters when changing page or view mode
    kept = {k: v for k, v in (('search', search), ('status', status), ('filtro', filter_name)) if v}
    base_qs = urlencode(kept)

    response = render(request, 'core/student_list.html', {
        'students': page,
        'page': page,
        'search': search,
        'selected_status': status,
        'selected_filter': filter_name,
        'filters': STUDENT_FILTERS,
        'base_qs': base_qs,
        'view_mode': view_mode,
        'active_filters': bool(status) + bool(filter_name),
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
                user = User(username=email, email=email, is_active=True)
                user.set_unusable_password()
                user.save()

                person = form.save(commit=False)
                person.user = user
                person.status_changed_at = timezone.now()
                person.save()

                EmailAddress.objects.create(user=user, email=email, primary=True, verified=False)
                log_event(person, StudentEvent.Kind.CREATED, request.user,
                          f'Cadastro criado ({person.get_status_display()})')

            messages.success(request, 'Aluno cadastrado! Envie o link de convite para ele criar a senha.')
            return redirect('core:student_invite', pk=person.pk)
    else:
        form = PersonForm()
    return render(request, 'core/student_form.html', {'form': form, 'title': 'Cadastrar Aluno'})


@staff_required
def student_invite(request, pk):
    """Show the one-time link (and a WhatsApp shortcut) the student uses to set a password"""
    student = get_object_or_404(Person.objects.select_related('user'), pk=pk)
    if request.method == 'POST':
        revoke_invites(student)
        log_event(student, StudentEvent.Kind.INVITE_REVOKED, request.user, 'Link de acesso revogado e gerado de novo')
        messages.success(request, 'Link anterior revogado. Este é o novo link de acesso.')
        return redirect(request.get_full_path())
    invite_url = build_invite_url(request, student.user)

    whatsapp_text = (
        f'Olá, {student.person_name.split()[0]}! Seu acesso ao Muscle Track está pronto. '
        f'Crie sua senha por este link (válido por 7 dias): {invite_url}'
    )

    return render(request, 'core/student_invite.html', {
        'student': student,
        'invite_url': invite_url,
        'whatsapp_url': whatsapp_url(student.phone, whatsapp_text),
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
            log_event(user.person, StudentEvent.Kind.INVITE_ACCEPTED, user, 'Criou a senha pelo link de acesso')
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, 'Senha criada! Bem-vindo(a) ao Muscle Track.')
            return redirect('core:my_workouts')
    else:
        form = SetPasswordForm(user)

    return render(request, 'core/accept_invite.html', {'form': form, 'student': user.person})


def _finance_agenda_context(student):
    """Plan/payments and calendar data for the student's file (trainer area only)"""
    today = timezone.localdate()
    payments = list(student.payments.all()[:50])
    amount = suggest_amount(student)
    owes = student.is_active and student.plan_expires_on and student.plan_expires_on < today
    return {
        'payments': payments,
        'last_payment': payments[0] if payments else None,
        'suggested_amount': amount,
        'charge_url': whatsapp_url(student.phone, charge_message(student, amount, student.plan_expires_on))
        if student.plan_expires_on and student.is_active and student.plan_status in ('expired', 'expiring') else '',
        'owes': owes,
        'slots': list(student.slots.filter(is_active=True)),
        'upcoming': list(student.appointments.filter(
            starts_at__gte=timezone.now(), status=Appointment.Status.SCHEDULED)[:8]),
        'attendance': attendance_stats(student, 30, today),
        'weekdays': ScheduleSlot.WEEKDAYS,
        'slot_form': ScheduleSlotForm(),
    }


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

    assessments = list(student.assessments.all())
    chart_points = [
        {
            'date': a.date.strftime('%d/%m/%y'),
            'weight': float(a.weight_kg) if a.weight_kg is not None else None,
            'body_fat': float(a.body_fat_pct) if a.body_fat_pct is not None else None,
            'waist': float(a.waist_cm) if a.waist_cm is not None else None,
        }
        for a in reversed(assessments)
    ]

    return render(request, 'core/student_detail.html', {
        'student': student,
        'assessments': assessments,
        'latest_assessment': assessments[0] if assessments else None,
        'assessment_chart': chart_points if len(chart_points) >= 2 else [],
        'photos': list(student.progress_photos.all()),
        'events': list(student.events.select_related('actor')[:100]),
        'consents': [(ConsentRecord.Kind(kind).label, record, is_granted(record), kind in REQUIRED_KINDS)
                     for kind, record in current_consents(student).items()],
        'has_consent': has_current_consent(student),
        'erasure_requested': student.events.filter(kind=StudentEvent.Kind.ERASURE_REQUEST).exists(),
        'consent_kinds': [(kind, ConsentRecord.Kind(kind).label) for kind in ALL_KINDS],
        'today': timezone.localdate(),
        **_finance_agenda_context(student),
        'notes': list(student.trainer_notes.select_related('author')),
        'note_form': StudentNoteForm(),
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
                    user.save()
                    EmailAddress.objects.update_or_create(
                        user=user, primary=True, defaults={'email': email, 'verified': False})
                log_profile_edit(person, form, request.user)

            if email_changed:
                messages.success(request, 'Aluno atualizado! O e-mail de login mudou: confira o convite se o aluno ainda não criou a senha.')
            else:
                messages.success(request, 'Aluno atualizado com sucesso!')
            return redirect(after_save_url(request, reverse('core:student_detail', kwargs={'pk': pk})))
    else:
        form = PersonForm(instance=student)
    return render(request, 'core/student_form.html', {'form': form, 'title': 'Editar Aluno'})


@staff_required
def student_delete(request, pk):
    """Permanently delete a student: account, workouts, assessments, notes and photo files (LGPD)"""
    student = get_object_or_404(Person.objects.select_related('user'), pk=pk)
    if student.user.is_staff or student.user.is_superuser:
        raise PermissionDenied  # never delete a trainer account through the student screen
    if request.method == 'POST':
        name = student.person_name
        with transaction.atomic():
            student.user.delete()  # cascades to Person and everything hanging from it
        logger.info('Student #%s deleted by user #%s', pk, request.user.pk)  # no personal data
        messages.success(request, f'{name} e todos os dados foram excluídos.')
        return redirect('core:student_list')
    counts = {
        'routines': student.routines.count(),
        'sessions': WorkoutSession.objects.filter(person_routine__person=student).count(),
        'assessments': student.assessments.count(),
        'photos': student.progress_photos.count(),
        'notes': student.trainer_notes.count(),
    }
    return render(request, 'core/student_delete.html', {'student': student, 'counts': counts})


@staff_required
def student_status(request, pk):
    """Change a student's lifecycle status (trial / active / paused / former) with the reason"""
    student = get_object_or_404(Person.objects.select_related('user'), pk=pk)
    if student.user.is_staff or student.user.is_superuser:
        raise PermissionDenied
    form = StatusForm(request.POST or None, initial={'status': student.status, 'reason': student.status_reason})
    if request.method == 'POST' and form.is_valid():
        changed = student.set_status(form.cleaned_data['status'], form.cleaned_data['reason'], actor=request.user)
        if changed:
            messages.success(request, f'Situação atualizada: {student.get_status_display()}.')
        return redirect(after_save_url(request, reverse('core:student_detail', kwargs={'pk': pk})))
    return render(request, 'core/student_status.html', {'student': student, 'form': form})


@staff_required
@require_POST
def student_export(request, pk):
    """The trainer downloads everything stored about a student (including internal notes)"""
    student = get_object_or_404(Person.objects.select_related('user'), pk=pk)
    log_event(student, StudentEvent.Kind.DATA_EXPORT, request.user, 'Exportação de dados feita pela personal')
    return _zip_response(build_export(student, include_internal_notes=True), f'aluno-{student.pk}-dados.zip')


@staff_required
@require_POST
def student_consent_record(request, pk):
    """The trainer records consents the student gave outside the app (paper, WhatsApp)"""
    student = get_object_or_404(Person, pk=pk)
    given = set(request.POST.getlist('kinds'))
    try:
        when = timezone.make_aware(datetime.combine(date.fromisoformat(request.POST.get('date', '')), time(12, 0)))
    except ValueError:
        when = timezone.now()
    count = 0
    for kind in ALL_KINDS:
        if kind in given:
            record_consent(student, kind, True, source=ConsentRecord.Source.TRAINER, actor=request.user, at=when)
            count += 1
    if count:
        messages.success(request, 'Consentimento registrado.')
    else:
        messages.warning(request, 'Marque pelo menos um consentimento para registrar.')
    return redirect(_student_tab(request, student.pk, 'historico'))


def _zip_response(handle, filename):
    return FileResponse(handle, as_attachment=True, filename=filename, content_type='application/zip')


def privacy_policy(request):
    """Public privacy policy (a template text: the trainer reviews it before relying on it)"""
    return render(request, 'core/privacy_policy.html', {
        'controller': settings.PRIVACY_CONTROLLER_NAME,
        'contact_email': settings.PRIVACY_CONTACT_EMAIL,
        'version': CONSENT_VERSION,
    })


@student_required(consent_gate=False)
def consent(request, person):
    """First access (or new text version): the student accepts the terms before using the app"""
    default = reverse('core:my_workouts')
    if has_current_consent(person):
        return redirect(safe_next_url(request, default))
    form = ConsentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        for kind in (ConsentRecord.Kind.TERMS, ConsentRecord.Kind.HEALTH_DATA):
            record_consent(person, kind, True, actor=request.user)
        if form.cleaned_data['photos']:
            record_consent(person, ConsentRecord.Kind.PHOTOS, True, actor=request.user)
        messages.success(request, 'Obrigado! Você já pode treinar.')
        return redirect(safe_next_url(request, default))
    return render(request, 'core/consent.html', {
        'form': form, 'descriptions': CONSENT_DESCRIPTIONS, 'version': CONSENT_VERSION,
        'policy_url': reverse('core:privacy_policy'),
    })


@student_required(consent_gate=False)
def my_privacy(request, person):
    """Student: consents, download my data, ask for deletion"""
    state = current_consents(person)
    return render(request, 'core/my_privacy.html', {
        'person': person,
        'consents': [(kind, ConsentRecord.Kind(kind).label, state[kind], is_granted(state[kind]),
                      kind in REQUIRED_KINDS) for kind in ALL_KINDS],
        'erasure_requested': person.events.filter(kind=StudentEvent.Kind.ERASURE_REQUEST).exists(),
        'contact_email': settings.PRIVACY_CONTACT_EMAIL,
    })


@student_required(consent_gate=False)
@require_POST
def my_export(request, person):
    log_event(person, StudentEvent.Kind.DATA_EXPORT, request.user, 'Baixou os próprios dados')
    return _zip_response(build_export(person, include_internal_notes=False), 'meus-dados.zip')


@student_required(consent_gate=False)
@require_POST
def my_consent_photos(request, person):
    """Give or withdraw the optional photo consent (the required ones end only with deletion)"""
    granted = request.POST.get('granted') == '1'
    record_consent(person, ConsentRecord.Kind.PHOTOS, granted, actor=request.user)
    messages.success(request, 'Preferência de fotos atualizada.')
    return redirect('core:my_privacy')


@student_required(consent_gate=False)
@require_POST
def my_erasure_request(request, person):
    if not person.events.filter(kind=StudentEvent.Kind.ERASURE_REQUEST).exists():
        log_event(person, StudentEvent.Kind.ERASURE_REQUEST, request.user, 'Pediu a exclusão dos próprios dados')
    messages.success(request, 'Pedido enviado. Sua personal vai apagar seus dados e avisar você.')
    return redirect('core:my_privacy')


@student_required
def my_profile(request, person):
    """Student edits their own profile"""
    from .forms import StudentProfileForm

    if request.method == 'POST':
        form = StudentProfileForm(request.POST, request.FILES, instance=person)
        if form.is_valid():
            form.save()
            log_profile_edit(person, form, request.user)
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
            return redirect(after_save_url(request, reverse('core:exercise_list'), forward_next=False))
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
            return redirect(after_save_url(request, reverse('core:exercise_detail', kwargs={'pk': pk})))
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
                return redirect(after_save_url(request, reverse('core:routine_detail', kwargs={'pk': routine.pk})))
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
                return redirect(after_save_url(request, reverse('core:routine_detail', kwargs={'pk': pk})))
    else:
        form = RoutineForm(instance=routine)

    routine_exercises = routine.exercises.all().select_related('exercise').order_by('order')
    return render(request, 'core/routine_form.html',
                  _routine_form_context(form, 'Editar Treino', routine, routine_exercises))


# ============= PERSON ROUTINE VIEWS =============

def _student_file_url(student):
    """Default Back target for pages opened from a student's file (the list if unknown)."""
    if student is None:
        return reverse('core:student_list')
    return reverse('core:student_detail', kwargs={'pk': student.pk})


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
                return redirect(after_save_url(request, reverse('core:student_detail', kwargs={'pk': person_routine.person.pk})))
        person_id = request.POST.get('person')
    else:
        # Pre-fill person if provided in URL
        person_id = request.GET.get('person')
        form = PersonRoutineForm(initial={'person': person_id} if person_id else None)

    if person_id and str(person_id).isdigit():
        selected_person = Person.objects.filter(pk=person_id).first()

    return render(request, 'core/person_routine_form.html', {
        'form': form,
        'selected_person': selected_person,
        'back_default': _student_file_url(selected_person),
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
                return redirect(after_save_url(request, reverse('core:student_detail', kwargs={'pk': person_routine.person.pk})))
    else:
        form = PersonRoutineForm(instance=person_routine)

    current_exercises = person_routine.exercises.all().select_related('exercise').order_by('order')

    return render(request, 'core/person_routine_form.html', {
        'form': form,
        'person_routine': person_routine,
        'current_exercises': current_exercises,
        'is_edit': True,
        'selected_person': person_routine.person,  # Always show person as read-only in edit mode
        'back_default': _student_file_url(person_routine.person),
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
        return redirect(after_save_url(request, reverse('core:student_detail', kwargs={'pk': person_id})))

    return redirect('core:student_list')


# ============= STUDENT WORKOUT AREA =============



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
    # (never done first) - this rotates A/B/C naturally (rule shared with the Hoje screen).
    if len(items) > 1:
        index = suggest_index([(bool(i['active_session']), i['last_done']) for i in items])
        items[index]['suggested'] = True
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

    # Focus mode: finished exercises go to the end of the queue (the trainer's order inside each group)
    for item in exercises:
        item['complete'] = bool(item['sets']) and item['done_count'] == len(item['sets'])
    exercises.sort(key=lambda item: (item['complete'], item['prx'].order, item['prx'].pk))
    first_pending = next((i for i, item in enumerate(exercises) if not item['complete']), None)
    for i, item in enumerate(exercises):
        # open the current exercise and any that is already in progress; the rest start collapsed
        item['expanded'] = not item['complete'] and (i == first_pending or item['done_count'] > 0)
    sets_total = sum(len(item['sets']) for item in exercises)
    sets_done = sum(item['done_count'] for item in exercises)

    stale_in = max(0, int((active_session.start_time + timedelta(minutes=MAX_SESSION_MINUTES)
                           - timezone.now()).total_seconds()))
    return render(request, 'core/workout_execute.html', {
        'person_routine': person_routine,
        'active_session': active_session,
        'exercises': exercises,
        'elapsed_seconds': active_session.elapsed_seconds(),
        'stale_in_seconds': stale_in,
        'all_done': all(e['complete'] for e in exercises) if exercises else False,
        'exercise_total': len(exercises),
        'exercises_done': sum(1 for e in exercises if e['complete']),
        'sets_total': sets_total,
        'sets_done': sets_done,
        'sets_percent': round(100 * sets_done / sets_total) if sets_total else 0,
    })


@student_required(consent_gate=False)
@require_POST
def workout_end(request, pk, person):
    """End a workout session, saving the student's final comment"""
    session = get_object_or_404(WorkoutSession, pk=pk, person_routine__person=person)
    if not session.is_completed:
        session.notes = request.POST.get('notes', '').strip()[:2000]
        session.save(update_fields=['notes'])
    session.end_session()
    return redirect('core:workout_summary', pk=session.pk)


@student_required(consent_gate=False)
def workout_summary(request, pk, person):
    """What the student just did: duration, volume, records and the comparison with last time"""
    session = get_object_or_404(
        WorkoutSession.objects.select_related('person_routine'),
        pk=pk, person_routine__person=person, is_completed=True)
    return render(request, 'core/workout_summary.html', {
        'session': session, 'routine': session.person_routine, 'summary': session_summary(session),
        'celebrations': celebrations(person, session)})


@student_required(consent_gate=False)
@require_POST
def workout_discard(request, pk, person):
    """Throw away a session that was started by mistake (nothing logged yet)"""
    session = _active_session_or_404(pk, person)
    has_sets = SessionSet.objects.filter(session_exercise__session=session, is_done=True).exists()
    has_notes = bool(session.notes.strip()) or session.exercise_logs.exclude(notes='').exists()
    if has_sets or has_notes:
        messages.warning(request, 'Este treino já tem séries ou comentários. Finalize-o em vez de descartar.')
        return redirect('core:workout_execute', pk=session.person_routine_id)
    session.delete()
    messages.info(request, 'Treino descartado.')
    return redirect('core:my_workouts')


def _active_session_or_404(pk, person):
    return get_object_or_404(
        WorkoutSession, pk=pk, person_routine__person=person, is_completed=False)


@student_required(consent_gate=False)
@require_POST
def workout_pause(request, pk, person):
    session = _active_session_or_404(pk, person)
    session.pause()
    return JsonResponse({'success': True, 'paused': True, 'elapsed_seconds': session.elapsed_seconds()})


@student_required(consent_gate=False)
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

    if is_trainer(request.user):
        default_back = reverse('core:person_routine_detail', kwargs={'pk': person_routine.pk})
    else:
        default_back = reverse('core:my_history')
    back_url = safe_next_url(request, default_back)

    return render(request, 'core/session_detail.html', {
        'session': session,
        'person_routine': person_routine,
        'student': person_routine.person,
        'exercises_data': exercises_data,
        'back_url': back_url,
        'back_label': back_label(back_url),
    })


def _parse_json_body(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


@student_required(consent_gate=False)
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


@student_required(consent_gate=False)
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
                return redirect(after_save_url(request, reverse('core:routine_detail', kwargs={'pk': routine.pk})))
            except AIGenerationError as e:
                messages.error(request, f'Erro ao gerar treino: {e}')
    else:
        form = AIWorkoutGenerationForm()

    return render(request, 'core/routine_generate_ai.html', {'form': form, 'title': 'Gerar Treino com IA'})


# ============= PWA =============

def _manifest_shortcuts(user):
    """Long-press shortcuts of the installed icon, by role (the manifest is fetched with the session cookie)."""
    if not user.is_authenticated:
        return []
    icon = [{'src': static('images/icon-192.png'), 'sizes': '192x192', 'type': 'image/png'}]
    if is_trainer(user):
        items = (('Agenda', reverse('core:agenda')), ('Alunos', reverse('core:student_list')),
                 ('Buscar', reverse('core:search')))
    else:
        items = (('Treinar agora', reverse('core:my_workouts')), ('Meu histórico', reverse('core:my_history')))
    return [{'name': name, 'short_name': name, 'url': url, 'icons': icon} for name, url in items]


def pwa_manifest(request):
    manifest = {
        'id': '/',
        'name': 'Muscle Track',
        'short_name': 'Muscle Track',
        'description': 'Seus treinos, séries e cargas na palma da mão',
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'orientation': 'portrait',
        'background_color': '#ffffff',
        'theme_color': '#FF6B6B',
        'lang': 'pt-BR',
        'categories': ['health', 'fitness', 'lifestyle'],
        'icons': [
            {'src': static('images/icon-192.png'), 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
            {'src': static('images/icon-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ],
        'shortcuts': _manifest_shortcuts(request.user),
    }
    response = HttpResponse(json.dumps(manifest, ensure_ascii=False), content_type='application/manifest+json')
    response['Cache-Control'] = 'private, no-cache'   # varies with who is logged in
    response['Vary'] = 'Cookie'
    return response


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


# ============= TRAINER'S FILE ON THE STUDENT =============

def _student_tab(request, student_pk, tab):
    """Back to the student's file on the given tab, keeping where the trainer came from."""
    return after_save_url(request, reverse('core:student_detail', kwargs={'pk': student_pk}), fragment=f'#tab-{tab}')


@staff_required
def assessment_create(request, pk):
    student = get_object_or_404(Person, pk=pk)
    form = AssessmentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        assessment = form.save(commit=False)
        assessment.person = student
        assessment.save()
        messages.success(request, 'Avaliação registrada.')
        return redirect(_student_tab(request, student.pk, 'avaliacoes'))
    return render(request, 'core/assessment_form.html', {'form': form, 'student': student, 'title': 'Nova avaliação'})


@staff_required
def assessment_edit(request, pk):
    assessment = get_object_or_404(Assessment.objects.select_related('person'), pk=pk)
    form = AssessmentForm(request.POST or None, instance=assessment)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Avaliação atualizada.')
        return redirect(_student_tab(request, assessment.person_id, 'avaliacoes'))
    return render(request, 'core/assessment_form.html', {
        'form': form, 'student': assessment.person, 'title': 'Editar avaliação'})


@staff_required
@require_POST
def assessment_delete(request, pk):
    assessment = get_object_or_404(Assessment, pk=pk)
    person_id = assessment.person_id
    assessment.delete()
    messages.success(request, 'Avaliação removida.')
    return redirect(_student_tab(request, person_id, 'avaliacoes'))


@staff_required
def photo_upload(request, pk):
    student = get_object_or_404(Person, pk=pk)
    form = ProgressPhotoForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        photo = form.save(commit=False)
        photo.person = student
        photo.save()
        messages.success(request, 'Foto adicionada.')
        return redirect(_student_tab(request, student.pk, 'fotos'))
    photos_consent = is_granted(current_consents(student)[ConsentRecord.Kind.PHOTOS])
    return render(request, 'core/photo_form.html', {'form': form, 'student': student, 'photos_consent': photos_consent})


@staff_required
@require_POST
def photo_delete(request, pk):
    photo = get_object_or_404(ProgressPhoto, pk=pk)
    person_id = photo.person_id
    photo.delete()
    messages.success(request, 'Foto removida.')
    return redirect(_student_tab(request, person_id, 'fotos'))


@login_required
def photo_file(request, pk, size='full'):
    """Progress photos are private: only the trainer and the student themself can open them."""
    photo = get_object_or_404(ProgressPhoto.objects.select_related('person'), pk=pk)
    if not is_trainer(request.user) and photo.person.user_id != request.user.pk:
        raise PermissionDenied
    field = photo.thumbnail if size == 'thumb' and photo.thumbnail else photo.image
    try:
        handle = field.open('rb')
    except FileNotFoundError:
        raise Http404
    response = FileResponse(handle, content_type='image/jpeg' if field is photo.thumbnail else None)
    response['Cache-Control'] = 'private, max-age=3600'
    return response


@staff_required
@require_POST
def note_add(request, pk):
    student = get_object_or_404(Person, pk=pk)
    form = StudentNoteForm(request.POST)
    if form.is_valid():
        note = form.save(commit=False)
        note.person = student
        note.author = request.user
        note.save()
        messages.success(request, 'Anotação salva.')
    else:
        messages.error(request, 'Escreva a anotação antes de salvar.')
    return redirect(_student_tab(request, student.pk, 'notas'))


@staff_required
@require_POST
def note_delete(request, pk):
    note = get_object_or_404(StudentNote, pk=pk)
    person_id = note.person_id
    note.delete()
    messages.success(request, 'Anotação removida.')
    return redirect(_student_tab(request, person_id, 'notas'))


# ============= FINANCE =============

def _csv_safe(value):
    """Spreadsheets run cells that start with = + - @ as formulas."""
    text = str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@') else text


def _month_nav(year, month):
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return f'{prev_year}-{prev_month:02d}', f'{next_year}-{next_month:02d}'


@staff_required
def finance(request):
    """Money overview: received and expected in a month, who is overdue, the month's payments"""
    year, month = parse_month(request.GET.get('mes'))
    summary = month_summary(year, month)
    overdue = overdue_rows()
    for row in overdue:
        row['whatsapp'] = whatsapp_url(row['student'].phone, charge_message(row['student'], row['amount'], row['due']))
    prev_month, next_month = _month_nav(year, month)
    return render(request, 'core/finance.html', {
        'month_value': f'{year}-{month:02d}', 'month_label': date(year, month, 1),
        'prev_month': prev_month, 'next_month': next_month,
        'is_current_month': (year, month) == (timezone.localdate().year, timezone.localdate().month),
        'summary': summary, 'overdue': overdue,
        'overdue_total': sum((r['amount'] or 0 for r in overdue), Decimal('0')),
        'overdue_unpriced': sum(1 for r in overdue if r['amount'] is None),
    })


@staff_required
def finance_export(request):
    """The month's payments as a CSV that opens directly in a Brazilian Excel"""
    year, month = parse_month(request.GET.get('mes'))
    first, last = month_bounds(year, month)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="pagamentos-{year}-{month:02d}.csv"'
    response.write('﻿')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Data', 'Aluno', 'Plano', 'Valor', 'Forma', 'Período de', 'Período até', 'Observação'])
    for p in Payment.objects.filter(paid_on__range=(first, last)).select_related('person').order_by('paid_on', 'pk'):
        writer.writerow([p.paid_on.strftime('%d/%m/%Y'), _csv_safe(p.person.person_name), _csv_safe(p.plan_name),
                         f'{p.amount:.2f}'.replace('.', ','), p.get_method_display(),
                         p.period_start.strftime('%d/%m/%Y'), p.period_end.strftime('%d/%m/%Y'), _csv_safe(p.notes)])
    return response


@staff_required
def plan_list(request):
    """Plan templates, like the exercise catalog"""
    search = request.GET.get('search', '').strip()
    status = request.GET.get('status', '')
    plans = Plan.objects.annotate(student_count=Count('students'))
    if search:
        plans = plans.filter(Q(name__icontains=search) | Q(description__icontains=search))
    if status == 'inactive':
        plans = plans.filter(is_active=False)
    elif status != 'all':
        plans = plans.filter(is_active=True)
    return render(request, 'core/plan_list.html', {
        'plans': plans.order_by('name'), 'search': search, 'status': status or 'active'})


@staff_required
def plan_create(request):
    form = PlanForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        plan = form.save()
        messages.success(request, f'Plano "{plan.name}" criado.')
        return redirect(after_save_url(request, reverse('core:plan_list'), forward_next=False))
    return render(request, 'core/plan_form.html', {'form': form, 'title': 'Novo plano'})


@staff_required
def plan_edit(request, pk):
    plan = get_object_or_404(Plan, pk=pk)
    form = PlanForm(request.POST or None, instance=plan)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Plano atualizado. Os alunos que já têm o plano mantêm os valores deles.')
        return redirect(after_save_url(request, reverse('core:plan_list'), forward_next=False))
    return render(request, 'core/plan_form.html', {
        'form': form, 'title': 'Editar plano', 'student_count': plan.students.count()})


def _payment_initial(student, plan, today):
    start, end = suggest_period(student, plan, today)
    return {'plan': plan, 'amount': suggest_amount(student, plan), 'paid_on': today,
            'period_start': start, 'period_end': end}


@staff_required
def payment_create(request, pk):
    """Register a payment: suggests the amount and the period, both editable"""
    student = get_object_or_404(Person.objects.select_related('plan'), pk=pk)
    today = timezone.localdate()
    if request.method == 'POST':
        form = PaymentForm(request.POST, person=student)
        if form.is_valid():
            data = form.cleaned_data
            register_payment(student, plan=data['plan'], amount=data['amount'], paid_on=data['paid_on'],
                             method=data['method'], period_start=data['period_start'],
                             period_end=data['period_end'], notes=data['notes'], actor=request.user)
            messages.success(request, f'Pagamento registrado. Plano até {data["period_end"]:%d/%m/%Y}.')
            return redirect(_student_tab(request, student.pk, 'pagamentos'))
    else:
        plan = student.plan
        if request.GET.get('plan', '').isdigit():
            plan = Plan.objects.filter(pk=int(request.GET['plan'])).first() or plan
        form = PaymentForm(initial=_payment_initial(student, plan, today), person=student)
    return render(request, 'core/payment_form.html', {'form': form, 'student': student})


@staff_required
@require_POST
def payment_delete(request, pk):
    payment = get_object_or_404(Payment.objects.select_related('person'), pk=pk)
    person_id = payment.person_id
    delete_payment(payment, request.user)
    messages.success(request, 'Pagamento excluído.')
    return redirect(_student_tab(request, person_id, 'pagamentos'))


# ============= AGENDA =============

def _agenda_selected_day(value, by_day, today):
    """The day shown on a phone: ?dia= if it is in the week, else today, else the first day with classes, else Monday."""
    try:
        chosen = date.fromisoformat(value)
    except (ValueError, TypeError):
        chosen = None
    if chosen in by_day:
        return chosen
    if today in by_day:
        return today
    return next((d for d in sorted(by_day) if by_day[d]), min(by_day))


@staff_required
def agenda(request):
    """The week: classes by day, with quick attendance buttons"""
    today = timezone.localdate()
    monday = parse_week(request.GET.get('semana'), today)
    sunday = monday + timedelta(days=6)
    ensure_appointments(monday, sunday, today)
    items = list(Appointment.objects.filter(
        starts_at__gte=at(monday, time.min), starts_at__lt=at(sunday + timedelta(days=1), time.min)
    ).select_related('person'))
    conflicts = conflicting_ids(items)
    by_day = {monday + timedelta(days=i): [] for i in range(7)}
    for item in items:
        by_day[timezone.localtime(item.starts_at).date()].append(item)
    selected = _agenda_selected_day(request.GET.get('dia'), by_day, today)
    days = [{'date': d, 'is_today': d == today, 'items': by_day[d], 'is_selected': d == selected,
             'weekday': ScheduleSlot.WEEKDAYS[d.weekday()][1]} for d in sorted(by_day)]
    return render(request, 'core/agenda.html', {
        'days': days, 'selected_day': selected, 'conflicts': conflicts, 'monday': monday, 'sunday': sunday,
        'prev_week': (monday - timedelta(days=7)).isoformat(), 'next_week': (monday + timedelta(days=7)).isoformat(),
        'is_current_week': monday == parse_week(None, today), 'statuses': Appointment.Status,
    })


def _warn_if_conflict(request, appointment):
    clash = Appointment.objects.filter(
        starts_at__lt=appointment.ends_at, starts_at__gte=appointment.starts_at - timedelta(minutes=480)
    ).exclude(pk=appointment.pk).exclude(
        status__in=(Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED)).select_related('person')
    others = [a for a in clash if a.ends_at > appointment.starts_at]
    if others:
        names = ', '.join(sorted({a.person.person_name for a in others}))
        messages.warning(request, f'Atenção: este horário se sobrepõe a outra aula ({names}).')


def _agenda_url_for(day):
    return f"{reverse('core:agenda')}?semana={parse_week(day.isoformat()).isoformat()}&dia={day.isoformat()}"


@staff_required
def appointment_create(request):
    """One-off class: trial, makeup..."""
    initial = {'date': timezone.localdate(), 'time': time(7, 0)}
    if request.GET.get('person', '').isdigit():
        initial['person'] = int(request.GET['person'])
    try:
        initial['date'] = date.fromisoformat(request.GET.get('date', ''))
    except ValueError:
        pass
    form = AppointmentForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        starts = at(data['date'], data['time'])
        if Appointment.objects.filter(person=data['person'], starts_at=starts).exists():
            form.add_error('time', 'Este aluno já tem uma aula neste horário.')
        else:
            appointment = Appointment.objects.create(
                person=data['person'], starts_at=starts, duration_minutes=data['duration_minutes'],
                kind=data['kind'], notes=data['notes'])
            messages.success(request, 'Aula marcada.')
            _warn_if_conflict(request, appointment)
            return redirect(after_save_url(request, _agenda_url_for(data['date']), forward_next=False))
    return render(request, 'core/appointment_form.html', {'form': form, 'title': 'Nova aula'})


@staff_required
def appointment_reschedule(request, pk):
    appointment = get_object_or_404(Appointment.objects.select_related('person'), pk=pk)
    if appointment.status == Appointment.Status.RESCHEDULED:
        raise Http404
    local = timezone.localtime(appointment.starts_at)
    form = AppointmentForm(request.POST or None, initial={
        'person': appointment.person_id, 'date': local.date(), 'time': local.time().replace(second=0, microsecond=0),
        'duration_minutes': appointment.duration_minutes, 'kind': appointment.kind})
    form.fields['person'].disabled = True
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        starts = at(data['date'], data['time'])
        if starts == appointment.starts_at or Appointment.objects.filter(person=appointment.person, starts_at=starts).exists():
            form.add_error('time', 'Escolha outro horário: este aluno já tem aula neste.')
        else:
            with transaction.atomic():
                new = Appointment.objects.create(
                    person=appointment.person, starts_at=starts, duration_minutes=data['duration_minutes'],
                    kind=data['kind'], notes=data['notes'])
                appointment.status = Appointment.Status.RESCHEDULED
                appointment.status_changed_at = timezone.now()
                appointment.save(update_fields=['status', 'status_changed_at'])
            messages.success(request, 'Aula remarcada.')
            _warn_if_conflict(request, new)
            return redirect(after_save_url(request, _agenda_url_for(data['date']), forward_next=False))
    return render(request, 'core/appointment_form.html', {
        'form': form, 'title': 'Remarcar aula', 'appointment': appointment})


@staff_required
@require_POST
def appointment_status(request, pk):
    appointment = get_object_or_404(Appointment, pk=pk)
    status = request.POST.get('status')
    allowed = (Appointment.Status.SCHEDULED, Appointment.Status.ATTENDED, Appointment.Status.MISSED,
               Appointment.Status.CANCELLED)
    if status not in allowed or appointment.status == Appointment.Status.RESCHEDULED:
        return HttpResponseBadRequest('Situação inválida')
    appointment.status = status
    appointment.status_changed_at = timezone.now()
    appointment.save(update_fields=['status', 'status_changed_at'])
    return redirect(safe_next_url(request, _agenda_url_for(timezone.localtime(appointment.starts_at).date())))


@staff_required
@require_POST
def appointment_delete(request, pk):
    """Only one-off classes are deleted; a class from a fixed slot is cancelled instead (it would come back)"""
    appointment = get_object_or_404(Appointment, pk=pk)
    if appointment.slot_id:
        messages.warning(request, 'Aula de horário fixo: use "Cancelar" (excluir faria ela voltar).')
    else:
        appointment.delete()
        messages.success(request, 'Aula removida.')
    return redirect(safe_next_url(request, _agenda_url_for(timezone.localtime(appointment.starts_at).date())))


@staff_required
@require_POST
def slot_add(request, pk):
    student = get_object_or_404(Person, pk=pk)
    form = ScheduleSlotForm(request.POST)
    if form.is_valid():
        slot, created = ScheduleSlot.objects.get_or_create(
            person=student, weekday=form.cleaned_data['weekday'], start_time=form.cleaned_data['start_time'],
            defaults={'duration_minutes': form.cleaned_data['duration_minutes']})
        if not created:
            slot.duration_minutes = form.cleaned_data['duration_minutes']
            slot.is_active = True
            slot.save()
        today = timezone.localdate()
        ensure_appointments(today, today + timedelta(weeks=4), today)
        messages.success(request, f'Horário fixo: {slot}.')
    else:
        messages.error(request, 'Horário inválido: ' + ' '.join(e for errs in form.errors.values() for e in errs))
    return redirect(_student_tab(request, student.pk, 'agenda'))


@staff_required
@require_POST
def slot_delete(request, pk):
    slot = get_object_or_404(ScheduleSlot, pk=pk)
    person_id = slot.person_id
    with transaction.atomic():
        slot.appointments.filter(status=Appointment.Status.SCHEDULED, starts_at__gt=timezone.now()).delete()
        slot.delete()  # past classes stay (their slot link is cleared)
    messages.success(request, 'Horário fixo removido. As aulas futuras dele saíram da agenda.')
    return redirect(_student_tab(request, person_id, 'agenda'))
