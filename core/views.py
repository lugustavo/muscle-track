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
from django.views.decorators.http import require_POST, require_safe

from allauth.account.models import EmailAddress

from .dashboard import INACTIVE_DAYS, INVITE_PENDING, trainer_dashboard
from .forms import (
    AIWorkoutGenerationForm, AppointmentForm, AssessmentForm, ClassRequestForm, ConsentForm, SaleForm, ExerciseForm, PaymentForm,
    PersonForm, PersonRoutineForm, PackageForm, RepeatForm, ScheduleSlotForm, SlotEditForm, SlotFillForm,
    ProgressPhotoForm, RoutineForm, StatusForm, StudentNoteForm,
)
from .agenda import (
    DEFAULT_VIEW as DEFAULT_AGENDA_VIEW, LIST_DAYS, VIEWS as AGENDA_VIEWS, add_months, at, attendance_stats,
    MoveError, annotate_history, conflicting_ids, live_class_at, ensure_appointments, month_cells, move_appointment, overlap_names, parse_week,
    undo_move, view_range,
)
from .billing import (
    charge_message, delete_payment, month_bounds, month_summary, overdue_rows, parse_month, register_payment, service_report,
)
from . import class_requests, evolution, intro, notifications, packages
from .consistency import celebrations
from .calendar_feed import build_ics, feed_for_token, get_or_create_feed, mark_fetched, rotate
from .consent import (
    ALL_KINDS, CURRENT_VERSION as CONSENT_VERSION, DESCRIPTIONS as CONSENT_DESCRIPTIONS, REQUIRED_KINDS,
    current_consents, has_current_consent, is_granted, record_consent,
)
from .exports import build_export
from .history import log_event, log_profile_edit
from .invites import build_invite_url, get_user_from_invite, revoke_invites
from .models import (
    Appointment, AppointmentRequest, Assessment, CalendarFeed, NotificationPreference, PushSubscription, ConsentRecord, Exercise, MuscleGroup, Payment, Package, ScheduleSlot, Person, PersonRoutine, PersonRoutineExercise, ProgressPhoto,
    Routine, RoutineExercise, SessionExercise, SessionSet, StudentEvent, StudentNote, WorkoutSession,
)
from .navigation import after_save_url, back_label, safe_next_url, with_next
from .onboarding import trainer_steps
from .package_schedule import MAX_SKIPS, FillError, build_preview, fill_series, top_up
from .recurrence import SeriesError, create_series, describe, edit_series, end_series_from
from .permissions import is_trainer, staff_required, student_required
from .search import apply_student_search, phone_digits, search_all
from .today import MAX_SESSION_MINUTES, student_today, suggest_index
from .workout_sessions import close_if_stale
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
            context['show_intro'] = person.intro_seen_at is None
            context['intro_steps'] = intro.STEPS

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
    """Package/payments and calendar data for the student's file (trainer area only)"""
    today = timezone.localdate()
    payments = list(student.payments.select_related('student_package')[:50])
    pkg = packages.overview(student, today)
    plan = packages.schedule_plan(student, today)
    pending = next((r for r in pkg['rows'] if r['pending'] > 0 and r['starts_on']), None)
    charge_url = whatsapp_url(student.phone, charge_message(student, pending['package'], pending['pending'])) \
        if pending and student.is_active else ''
    return {
        'payments': payments,
        'pkg': pkg,
        'charge_url': charge_url,
        'slots': list(student.slots.filter(is_active=True).filter(Q(valid_until__isnull=True) | Q(valid_until__gte=timezone.localdate()))),
        'upcoming': list(student.appointments.filter(
            starts_at__gte=timezone.now(), status=Appointment.Status.SCHEDULED)[:8]),
        'attendance': attendance_stats(student, 30, today),
        'weekdays': ScheduleSlot.WEEKDAYS,
        'slot_form': ScheduleSlotForm(plan=plan),
        'fill_form': SlotFillForm(initial={'starts_on': today, 'duration_minutes': 60, 'interval_weeks': 1}),
        'has_package_series': bool(plan and student.slots.filter(package=plan['package'], is_active=True).exists()),
        'slot_plan': plan,
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

    assessments = evolution.assessments_with_photos(student)
    chart_points = evolution.chart_points(assessments)

    return render(request, 'core/student_detail.html', {
        'student': student,
        'assessments': assessments,
        'latest_assessment': assessments[0] if assessments else None,
        'assessment_chart': chart_points,
        'can_compare': bool(evolution.comparable_angles(assessments)),
        'loose_photos': list(student.progress_photos.filter(assessment__isnull=True)),
        'photo_count': student.progress_photos.count(),
        'events': list(student.events.select_related('actor')[:100]),
        'consents': [(ConsentRecord.Kind(kind).label, record, is_granted(record), kind in REQUIRED_KINDS)
                     for kind, record in current_consents(student).items()],
        'has_consent': has_current_consent(student),
        'erasure_requested': student.events.filter(kind=StudentEvent.Kind.ERASURE_REQUEST).exists(),
        'consent_kinds': [(kind, ConsentRecord.Kind(kind).label, is_granted(record))
                          for kind, record in current_consents(student).items()],
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
def student_package_sell(request, pk):
    """"Vender pacote": sell a catalog package to the student, with the price agreed for them."""
    student = get_object_or_404(Person, pk=pk)
    form = SaleForm(request.POST or None, initial={'pricing_mode': 'per_session'})
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        sold = packages.sell(student, data['package'], mode=data['pricing_mode'], price_per_session=data['price_per_session'],
                             price_total=data['price_total'], notes=data['notes'], actor=request.user)
        messages.success(request, f'Pacote vendido: {sold.name}. O prazo conta a partir da primeira aula.')
        return redirect(after_save_url(request, reverse('core:student_detail', kwargs={'pk': pk}), fragment='#tab-ficha'))
    catalog = list(form.fields['package'].queryset)
    return render(request, 'core/student_package_sell.html', {
        'form': form, 'student': student, 'catalog': catalog, 'title': 'Vender pacote',
        'overview': packages.overview(student)})


@staff_required
@require_POST
def student_package_action(request, pk, package_pk):
    """Freeze / unfreeze / extend / cancel one package of the student."""
    student = get_object_or_404(Person, pk=pk)
    package = get_object_or_404(student.packages, pk=package_pk)
    action = request.POST.get('action')
    try:
        if action == 'freeze':
            packages.freeze(package, request.user)
            messages.success(request, 'Pacote congelado: o prazo para de contar até você descongelar.')
        elif action == 'unfreeze':
            packages.unfreeze(package, request.user)
            messages.success(request, 'Pacote descongelado: os dias parados foram somados ao prazo.')
        elif action == 'extend':
            try:
                days = int(request.POST.get('days', ''))
            except ValueError:
                raise packages.PackageError('Informe quantos dias de prorrogação.')
            packages.extend(package, days, request.POST.get('note', ''), request.user)
            messages.success(request, f'Pacote prorrogado em {days} dias.')
        elif action == 'cancel':
            packages.cancel_package(package, request.user)
            messages.success(request, 'Pacote cancelado.')
        else:
            return HttpResponseBadRequest('Ação inválida')
    except packages.PackageError as error:
        messages.error(request, str(error))
    return redirect(after_save_url(request, reverse('core:student_detail', kwargs={'pk': pk}), fragment='#tab-ficha'))


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


@student_required
def my_agenda(request, person):
    """The student's own classes (next 60 days and the last 30), with the option to ask the trainer to move or cancel one."""
    now = timezone.now()
    today = timezone.localdate()
    ensure_appointments(today, today + timedelta(days=60), today)
    # rescheduled classes stay in the list (greyed, with where they went), so the student can see what happened
    mine = person.appointments.select_related('rescheduled_to', 'rescheduled_from').prefetch_related(
        'requests', 'rescheduled_from__requests')
    upcoming = list(mine.filter(starts_at__gte=now, starts_at__lt=at(today + timedelta(days=61), time.min)))
    recent = list(mine.filter(starts_at__lt=now, starts_at__gte=now - timedelta(days=30)).order_by('-starts_at'))
    annotate_history(upcoming + recent)
    for appointment in upcoming + recent:
        asked = list(appointment.requests.all())
        appointment.block = class_requests.request_block(appointment, now)
        appointment.open_request = next((r for r in asked if r.status == AppointmentRequest.Status.PENDING), None)
        decided = [r for r in asked if r.status in (AppointmentRequest.Status.APPROVED, AppointmentRequest.Status.DECLINED)]
        appointment.last_answer = decided[-1] if decided else None
    return render(request, 'core/my_agenda.html', {
        'title': 'Minha agenda', 'upcoming': upcoming, 'recent': recent, 'notice_hours': class_requests.notice_hours(),
        'package': packages.overview(person, today)['current']})


@student_required
def my_intro(request, person):
    """"Como usar o app": the three first-access screens. A POST records that the student has seen them (the Hoje
    screen sends it with fetch and wants no page back; the plain form goes to Hoje)."""
    if request.method == 'POST':
        if person.intro_seen_at is None:
            person.intro_seen_at = timezone.now()
            person.save(update_fields=['intro_seen_at'])
        if request.headers.get('X-Requested-With') == 'fetch':
            return HttpResponse(status=204)
        return redirect('core:home')
    return render(request, 'core/my_intro.html', {'title': 'Como usar o app', 'steps': intro.STEPS})


@student_required
def my_package(request, person):
    """The student's package summary: bought, done, cancelled, moved and the balance (no prices)."""
    data = packages.summary(person)
    rows = data['rows']
    current = data['current']
    others = [r for r in reversed(rows) if r is not current]
    return render(request, 'core/my_package.html', {
        'title': 'Meu pacote', 'current': current, 'others': others, 'without_package': data['without_package'],
        'has_packages': bool(rows)})


@student_required
def my_evolution(request, person):
    """The student's own assessments, chart and photos (read-only; the trainer's private notes are never shown)."""
    assessments = evolution.assessments_with_photos(person)
    return render(request, 'core/my_evolution.html', {
        'title': 'Minha evolução', 'assessments': assessments, 'assessment_chart': evolution.chart_points(assessments),
        'can_compare': bool(evolution.comparable_angles(assessments)),
        'loose_photos': list(person.progress_photos.filter(assessment__isnull=True))})


@student_required
def my_evolution_compare(request, person):
    data = evolution.compare(person, request.GET.get('angle'), request.GET.get('before'), request.GET.get('after'))
    return render(request, 'core/photo_compare.html', {
        'title': 'Comparar fotos', 'data': data, 'mine': True,
        'form_url': reverse('core:my_evolution_compare'), 'back_url': reverse('core:my_evolution')})


@staff_required
def student_compare(request, pk):
    student = get_object_or_404(Person, pk=pk)
    data = evolution.compare(student, request.GET.get('angle'), request.GET.get('before'), request.GET.get('after'))
    return render(request, 'core/photo_compare.html', {
        'title': 'Comparar fotos', 'data': data, 'mine': False, 'student': student,
        'form_url': reverse('core:student_compare', kwargs={'pk': student.pk}),
        'back_url': reverse('core:student_detail', kwargs={'pk': student.pk}) + '#tab-avaliacoes'})


@student_required
def my_agenda_request(request, person, pk):
    """Ask the trainer to move or cancel one class."""
    appointment = get_object_or_404(person.appointments.prefetch_related('requests'), pk=pk)
    block = class_requests.request_block(appointment)
    form = ClassRequestForm(request.POST or None)
    if block and request.method == 'POST':
        messages.error(request, block)
        return redirect('core:my_agenda')
    if request.method == 'POST' and not block and form.is_valid():
        data = form.cleaned_data
        proposed = at(data['date'], data['time']) if data['kind'] == 'reschedule' else None
        try:
            asked = class_requests.create_request(person, appointment, data['kind'], proposed, data['message'])
        except class_requests.RequestError as error:
            form.add_error(None, str(error))
        else:
            notifications.notify(notifications.staff_users(), notifications.request_created(asked))
            messages.success(request, 'Pedido enviado. Assim que sua personal responder, a resposta aparece aqui.')
            return redirect('core:my_agenda')
    return render(request, 'core/my_agenda_request.html', {
        'form': form, 'appointment': appointment, 'blocked': block, 'title': 'Pedir remarcação ou cancelamento'})


@student_required
@require_POST
def my_agenda_request_withdraw(request, person, pk):
    asked = get_object_or_404(AppointmentRequest.objects.select_related('appointment'), pk=pk, appointment__person=person)
    try:
        class_requests.withdraw(person, asked)
        messages.success(request, 'Pedido retirado.')
    except class_requests.RequestError as error:
        messages.error(request, str(error))
    return redirect('core:my_agenda')


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
    """End a running session that is idle or has run too long (core/workout_sessions.py: closed at its last sign of
    life, or dropped when nothing was logged). Returns True when it is gone from the running ones."""
    return close_if_stale(session)


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


# ============= CALENDAR LINK (Google Agenda, Apple Calendar...) =============

def _public_base(request):
    return (settings.SITE_URL or f'{request.scheme}://{request.get_host()}').rstrip('/')


@require_safe
def calendar_feed(request, token):
    """The private .ics link: no login, the long random token is the credential."""
    feed = feed_for_token(token)
    if feed is None:
        raise Http404
    mark_fetched(feed)
    host = request.get_host().split(':')[0]
    response = HttpResponse(build_ics(feed, host, site_url=_public_base(request)), content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'inline; filename="muscle-track.ics"'
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _calendar_owner(request):
    """('trainer' | 'student', person or None) for the signed-in account; a student without consent goes to /consent/."""
    if is_trainer(request.user):
        return 'trainer', None
    person = Person.objects.filter(user=request.user).first()
    if person is None:
        raise PermissionDenied
    return 'student', person


def _set_channel(user, person, pref, channel, enabled):
    """Turn one channel on/off, keeping when (the consent) and, for a student, a line in the file's history."""
    field = f'{channel}_enabled'
    if getattr(pref, field) == enabled:
        return
    setattr(pref, field, enabled)
    setattr(pref, f'{channel}_at', timezone.now() if enabled else None)
    pref.save()
    if channel == 'push' and not enabled:
        user.push_subscriptions.all().delete()   # withdrawing also forgets the devices
    if person is not None:
        label = 'e-mail' if channel == 'email' else 'push (aparelho)'
        StudentEvent.objects.create(
            person=person, kind=StudentEvent.Kind.CONSENT, actor=user,
            summary=f'{"Ativou" if enabled else "Desativou"} avisos por {label}',
            changes={'channel': channel, 'granted': enabled})


@login_required
def notification_settings(request):
    """Each person chooses whether to get notifications (e-mail, push on this device); everything starts off."""
    person = getattr(request.user, 'person', None)   # None for the trainer; students go through the consent gate
    if person is not None and not has_current_consent(person):
        return redirect('core:consent')
    pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        wants_email = 'email_enabled' in request.POST
        wants_push = 'push_enabled' in request.POST
        error = ''
        if wants_email and not request.user.email:
            error = 'Cadastre um e-mail na sua conta para receber avisos por e-mail.'
        elif wants_email and not pref.email_enabled and not notifications.email_configured():
            error = 'O envio de e-mail ainda não foi configurado no servidor.'
        elif wants_push and not pref.push_enabled and not notifications.push_configured():
            error = 'O aviso no aparelho ainda não foi configurado no servidor.'
        if error:
            messages.error(request, error)
        else:
            _set_channel(request.user, person, pref, 'email', wants_email)
            _set_channel(request.user, person, pref, 'push', wants_push)
            if 'types' in request.POST:   # the kinds-of-notice switches (a marker, so a partial post never resets them)
                pref.notify_changes = 'notify_changes' in request.POST
                if request.user.is_staff:
                    pref.notify_renewals = 'notify_renewals' in request.POST
                if person is not None:
                    pref.notify_class_reminder = 'notify_class_reminder' in request.POST
                    pref.notify_plan_expiry = 'notify_plan_expiry' in request.POST
                pref.save(update_fields=['notify_changes', 'notify_class_reminder', 'notify_plan_expiry',
                                                 'notify_renewals'])
            messages.success(request, 'Preferências de notificação salvas.')
        return redirect('core:notification_settings')
    recent = list(request.user.notification_logs.all()[:5])
    devices = list(request.user.push_subscriptions.all())
    for d in devices:   # rows saved before the friendly names hold the raw User-Agent
        d.label = notifications.device_name(d.device) if 'Mozilla/' in d.device else (d.device or 'Aparelho')
    return render(request, 'core/notification_settings.html', {
        'title': 'Notificações', 'pref': pref, 'person': person, 'devices': devices,
        'push_ready': notifications.push_configured(), 'email_ready': notifications.email_configured(),
        'reminder_hours': settings.REMINDER_CLASS_HOURS, 'plan_days': settings.REMINDER_PLAN_DAYS,
        'vapid_public_key': settings.VAPID_PUBLIC_KEY, 'recent': recent, 'is_trainer': request.user.is_staff,
    })


@login_required
@require_POST
def push_subscribe(request):
    """The page hands over this browser's push subscription (JSON); it also means "yes, notify me here"."""
    if not notifications.push_configured():
        return JsonResponse({'ok': False, 'error': 'Aviso no aparelho não configurado no servidor.'}, status=400)
    try:
        body = json.loads(request.body.decode())
        endpoint, keys = body['endpoint'], body['keys']
        p256dh, auth = keys['p256dh'], keys['auth']
        if not (isinstance(endpoint, str) and endpoint.startswith('https://') and len(endpoint) <= 2000
                and isinstance(p256dh, str) and isinstance(auth, str) and len(p256dh) <= 200 and len(auth) <= 100):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Assinatura inválida.'}, status=400)
    PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={
        'user': request.user, 'p256dh': p256dh, 'auth': auth,
        'device': notifications.device_name(request.META.get('HTTP_USER_AGENT', ''))})
    pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
    _set_channel(request.user, getattr(request.user, 'person', None), pref, 'push', True)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def push_unsubscribe(request, pk):
    get_object_or_404(PushSubscription, pk=pk, user=request.user).delete()
    messages.success(request, 'Aparelho removido.')
    return redirect('core:notification_settings')


@login_required
@require_POST
def notification_test(request):
    """Send a test on one channel so the person sees it works (only to themselves, only on a channel they enabled)."""
    channel = request.POST.get('channel')
    pref = NotificationPreference.objects.filter(user=request.user).first()
    if channel not in ('email', 'push') or pref is None or not getattr(pref, f'{channel}_enabled'):
        messages.error(request, 'Ative este canal antes de testar.')
        return redirect('core:notification_settings')
    notifications.deliver(request.user, notifications.test_message(), only=channel)
    last = request.user.notification_logs.filter(channel=channel).first()
    if last and last.status == 'sent':
        messages.success(request, 'Teste enviado. Confira o e-mail.' if channel == 'email' else 'Teste enviado. Deve aparecer no aparelho em instantes.')
    else:
        messages.error(request, f'Não foi possível enviar: {last.detail if last else "sem detalhes"}.')
    return redirect('core:notification_settings')


@login_required
def calendar_settings(request):
    """Where each person finds their own calendar link, how to add it to Google Agenda, and renews it."""
    role, person = _calendar_owner(request)
    if person is not None and not has_current_consent(person):
        return redirect('core:consent')
    feed = get_or_create_feed(request.user)
    url = f"{_public_base(request)}{reverse('core:calendar_feed', kwargs={'token': feed.token})}"
    return render(request, 'core/calendar_settings.html', {
        'role': role, 'feed': feed, 'feed_url': url, 'webcal_url': 'webcal://' + url.split('://', 1)[1],
        'name_styles': CalendarFeed.NAME_STYLES, 'person': person,
    })


@login_required
@require_POST
def calendar_rotate(request):
    _calendar_owner(request)
    rotate(get_or_create_feed(request.user))
    messages.success(request, 'Novo link gerado. O anterior deixou de funcionar: adicione o novo no Google Agenda.')
    return redirect('core:calendar_settings')


@staff_required
@require_POST
def calendar_style(request):
    feed = get_or_create_feed(request.user)
    value = request.POST.get('name_style')
    if value in dict(CalendarFeed.NAME_STYLES):
        feed.name_style = value
        feed.save(update_fields=['name_style'])
        messages.success(request, 'Preferência salva. O Google Agenda atualiza o calendário aos poucos.')
    else:
        messages.error(request, 'Escolha uma das opções.')
    return redirect('core:calendar_settings')


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
    """Add a photo, normally to one position of an assessment (`?assessment=<id>&angle=front`); "save and next"
    walks through the positions that are still empty."""
    student = get_object_or_404(Person, pk=pk)
    initial = {}
    wanted = student.assessments.filter(pk=request.GET.get('assessment') or 0).first()
    if wanted:
        initial['assessment'] = wanted
    if request.GET.get('angle') in ProgressPhoto.Angle.values:
        initial['angle'] = request.GET['angle']
    form = ProgressPhotoForm(request.POST or None, request.FILES or None, student=student, initial=initial)
    if request.method == 'POST' and form.is_valid():
        photo = form.save(commit=False)
        photo.person = student
        photo.save()
        if photo.assessment_id and photo.angle != ProgressPhoto.Angle.OTHER:   # a new photo replaces the old one of that position
            photo.assessment.photos.filter(angle=photo.angle).exclude(pk=photo.pk).delete()
        messages.success(request, 'Foto adicionada.')
        if 'save_next' in request.POST and photo.assessment_id:
            taken = set(photo.assessment.photos.values_list('angle', flat=True))
            missing = [a for a in ProgressPhoto.SEQUENCE if a not in taken]
            if missing:
                target = f"{reverse('core:photo_upload', kwargs={'pk': student.pk})}?assessment={photo.assessment_id}&angle={missing[0]}"
                came_from = request.GET.get('next')
                return redirect(with_next(target, came_from) if came_from else target)
        return redirect(_student_tab(request, student.pk, 'avaliacoes'))
    photos_consent = is_granted(current_consents(student)[ConsentRecord.Kind.PHOTOS])
    chosen = form['assessment'].value()
    return render(request, 'core/photo_form.html', {
        'form': form, 'student': student, 'photos_consent': photos_consent,
        'sequence': [(a.value, a.label) for a in ProgressPhoto.SEQUENCE], 'has_assessment': bool(chosen)})


@staff_required
@require_POST
def photo_delete(request, pk):
    photo = get_object_or_404(ProgressPhoto, pk=pk)
    person_id = photo.person_id
    photo.delete()
    messages.success(request, 'Foto removida.')
    return redirect(_student_tab(request, person_id, 'avaliacoes'))


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
    """Money overview: received and sold in a month, what is still to be paid, packages to renew, the month's payments"""
    year, month = parse_month(request.GET.get('mes'))
    summary = month_summary(year, month)
    pending = overdue_rows()
    for row in pending:
        row['whatsapp'] = whatsapp_url(row['student'].phone, charge_message(row['student'], row['package'], row['amount']))
    prev_month, next_month = _month_nav(year, month)
    return render(request, 'core/finance.html', {
        'month_value': f'{year}-{month:02d}', 'month_label': date(year, month, 1),
        'prev_month': prev_month, 'next_month': next_month,
        'is_current_month': (year, month) == (timezone.localdate().year, timezone.localdate().month),
        'summary': summary, 'pending': pending,
        'pending_total': sum((r['amount'] for r in pending), Decimal('0')),
        'renewals': packages.renewals(), 'service': service_report(year, month),
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
    writer.writerow(['Data', 'Aluno', 'Pacote', 'Valor', 'Forma', 'Observação'])
    for p in Payment.objects.filter(paid_on__range=(first, last)).select_related('person', 'student_package').order_by('paid_on', 'pk'):
        writer.writerow([p.paid_on.strftime('%d/%m/%Y'), _csv_safe(p.person.person_name),
                         _csv_safe(p.student_package.name if p.student_package else p.plan_name),
                         f'{p.amount:.2f}'.replace('.', ','), p.get_method_display(), _csv_safe(p.notes)])
    return response


@staff_required
def plan_list(request):
    """Package templates, like the exercise catalog"""
    search = request.GET.get('search', '').strip()
    status = request.GET.get('status', '')
    plans = Package.objects.annotate(sold_count=Count('sold'))
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
    form = PackageForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        plan = form.save()
        messages.success(request, f'Pacote "{plan.name}" criado.')
        return redirect(after_save_url(request, reverse('core:plan_list'), forward_next=False))
    return render(request, 'core/plan_form.html', {'form': form, 'title': 'Novo pacote'})


@staff_required
def plan_edit(request, pk):
    plan = get_object_or_404(Package, pk=pk)
    form = PackageForm(request.POST or None, instance=plan)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Pacote atualizado. Quem já comprou mantém os números da venda.')
        return redirect(after_save_url(request, reverse('core:plan_list'), forward_next=False))
    return render(request, 'core/plan_form.html', {
        'form': form, 'title': 'Editar pacote', 'sold_count': plan.sold.count()})


@staff_required
def payment_create(request, pk):
    """Register a payment for one of the student's packages; the amount starts as what is still to be paid."""
    student = get_object_or_404(Person, pk=pk)
    today = timezone.localdate()
    if request.method == 'POST':
        form = PaymentForm(request.POST, person=student)
        if form.is_valid():
            data = form.cleaned_data
            register_payment(student, package=data['student_package'], amount=data['amount'], paid_on=data['paid_on'],
                             method=data['method'], notes=data['notes'], actor=request.user)
            messages.success(request, 'Pagamento registrado.')
            return redirect(_student_tab(request, student.pk, 'pagamentos'))
    else:
        overview = packages.overview(student, today)
        target = next((r for r in overview['rows'] if r['pending'] > 0 and r['state'] != 'cancelled'), overview['current'])
        initial = {'paid_on': today}
        if request.GET.get('package', '').isdigit():
            target = next((r for r in overview['rows'] if r['package'].pk == int(request.GET['package'])), target)
        if target:
            initial.update(student_package=target['package'], amount=target['pending'] or target['package'].price_total)
        form = PaymentForm(initial=initial, person=student)
    return render(request, 'core/payment_form.html', {'form': form, 'student': student,
                                                       'overview': packages.overview(student, today)})


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


AGENDA_VIEW_LABELS = (('semana', 'Semana'), ('dia', 'Dia'), ('mes', 'Mês'), ('lista', 'Lista'))


def _agenda_url(**params):
    return f"{reverse('core:agenda')}?{urlencode(params)}"


def _agenda_nav(view, pivot, today):
    """(previous url, next url, "today" url, is the current period) of a view."""
    if view == 'mes':
        return (_agenda_url(vista='mes', mes=add_months(pivot, -1).strftime('%Y-%m')),
                _agenda_url(vista='mes', mes=add_months(pivot, 1).strftime('%Y-%m')),
                _agenda_url(vista='mes'), pivot == today.replace(day=1))
    if view == 'dia':
        return (_agenda_url(vista='dia', dia=(pivot - timedelta(days=1)).isoformat()),
                _agenda_url(vista='dia', dia=(pivot + timedelta(days=1)).isoformat()),
                _agenda_url(vista='dia'), pivot == today)
    if view == 'lista':
        return (_agenda_url(vista='lista', inicio=(pivot - timedelta(days=LIST_DAYS)).isoformat()),
                _agenda_url(vista='lista', inicio=(pivot + timedelta(days=LIST_DAYS)).isoformat()),
                _agenda_url(vista='lista'), pivot == today)
    return (_agenda_url(vista='semana', semana=(pivot - timedelta(days=7)).isoformat()),
            _agenda_url(vista='semana', semana=(pivot + timedelta(days=7)).isoformat()),
            _agenda_url(vista='semana'), pivot == parse_week(None, today))


@staff_required
def agenda(request):
    """The calendar in four views (week, day, month, list); the choice is remembered in a cookie.

    Classes are shown with quick attendance buttons; the week is the default."""
    today = timezone.localdate()
    requested = request.GET.get('vista')
    remembered = request.COOKIES.get('agenda_vista')
    view = requested if requested in AGENDA_VIEWS else (remembered if remembered in AGENDA_VIEWS else DEFAULT_AGENDA_VIEW)
    view, start, end, pivot = view_range(view, request.GET, today)
    ensure_appointments(start, end, today)
    items = list(Appointment.objects.filter(
        starts_at__gte=at(start, time.min), starts_at__lt=at(end + timedelta(days=1), time.min)
    ).select_related('person', 'rescheduled_to', 'rescheduled_from'))
    conflicts = conflicting_ids(items)
    by_day = {}
    for item in items:
        by_day.setdefault(timezone.localtime(item.starts_at).date(), []).append(item)

    prev_url, next_url, today_url, is_current = _agenda_nav(view, pivot, today)
    context = {
        'view': view, 'conflicts': conflicts, 'statuses': Appointment.Status,
        'calendar_style': CalendarFeed.objects.filter(user=request.user).values_list('name_style', flat=True).first() or 'first',
        'range_start': start, 'range_end': end, 'pivot': pivot,
        'prev_url': prev_url, 'next_url': next_url, 'today_url': today_url, 'is_current': is_current,
        'view_links': [(key, label, _agenda_url(vista=key), key == view) for key, label in AGENDA_VIEW_LABELS],
        'pending_requests': list(class_requests.pending_for_trainer()),
    }
    if view == 'semana':
        days_map = {start + timedelta(days=i): by_day.get(start + timedelta(days=i), []) for i in range(7)}
        selected = _agenda_selected_day(request.GET.get('dia'), days_map, today)
        context.update({
            'days': [{'date': d, 'is_today': d == today, 'items': days_map[d], 'is_selected': d == selected,
                      'weekday': ScheduleSlot.WEEKDAYS[d.weekday()][1]} for d in sorted(days_map)],
            'selected_day': selected, 'monday': start, 'sunday': end,
            'prev_week': (start - timedelta(days=7)).isoformat(), 'next_week': (start + timedelta(days=7)).isoformat(),
            'is_current_week': is_current,
        })
    elif view == 'mes':
        context.update({'weeks': month_cells(pivot, by_day, today, conflicts), 'month': pivot,
                        'weekday_letters': [label[:3] for _, label in ScheduleSlot.WEEKDAYS],
                        # the month's classes below the grid, day by day: today and the days ahead only (days outside
                        # the month and past days are left out)
                        'month_days': [{'date': d, 'is_today': d == today, 'items': by_day[d],
                                        'weekday': ScheduleSlot.WEEKDAYS[d.weekday()][1]}
                                       for d in sorted(by_day) if d >= today and (d.year, d.month) == (pivot.year, pivot.month)]})
    else:
        # day and list: the same cards, day by day (the list skips days without classes)
        day = start
        days = []
        while day <= end:
            if view == 'dia' or by_day.get(day):
                days.append({'date': day, 'is_today': day == today, 'items': by_day.get(day, []),
                             'weekday': ScheduleSlot.WEEKDAYS[day.weekday()][1]})
            day += timedelta(days=1)
        context['days'] = days
    response = render(request, 'core/agenda.html', context)
    if requested in AGENDA_VIEWS:
        response.set_cookie('agenda_vista', requested, max_age=31536000, samesite='Lax')
    return response


def _warn_if_conflict(request, appointment):
    names = overlap_names(appointment)
    if names:
        messages.warning(request, f'Atenção: este horário se sobrepõe a outra aula ({", ".join(names)}).')


def _agenda_url_for(day):
    return f"{reverse('core:agenda')}?semana={parse_week(day.isoformat()).isoformat()}&dia={day.isoformat()}"


@staff_required
def appointment_create(request):
    """A class: one-off (trial, makeup...) or, with "Repetir", a repeating fixed time (every N weeks, several days)."""
    initial = {'date': timezone.localdate(), 'time': time(7, 0)}
    if request.GET.get('person', '').isdigit():
        initial['person'] = int(request.GET['person'])
    try:
        initial['date'] = date.fromisoformat(request.GET.get('date', ''))
    except ValueError:
        pass
    form = AppointmentForm(request.POST or None, initial=initial)
    repeat = RepeatForm(request.POST or None, prefix='rep')
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        repeat.start_hint = data['date']
        if repeat.is_valid():
            if repeat.cleaned_data['enabled']:
                if data['kind'] != Appointment.Kind.REGULAR:
                    form.add_error('kind', 'Só aulas do tipo "Aula" podem se repetir.')
                else:
                    return _create_series_from_form(request, data, repeat)
            else:
                starts = at(data['date'], data['time'])
                if live_class_at(data['person'], starts) is not None:
                    form.add_error('time', 'Este aluno já tem uma aula neste horário.')
                else:
                    appointment = Appointment.objects.create(
                        person=data['person'], starts_at=starts, duration_minutes=data['duration_minutes'],
                        kind=data['kind'], notes=data['notes'])
                    messages.success(request, 'Aula marcada.')
                    _warn_if_conflict(request, appointment)
                    return redirect(after_save_url(request, _agenda_url_for(data['date']), forward_next=False))
    return render(request, 'core/appointment_form.html', {'form': form, 'repeat': repeat, 'title': 'Nova aula'})


def _create_series_from_form(request, data, repeat):
    start = data['date']
    cleaned = repeat.cleaned_data
    weekdays = cleaned['weekdays']
    until = repeat.series_end(weekdays, start)
    create_series(data['person'], weekdays, data['time'], data['duration_minutes'], start, cleaned['interval_weeks'], until)
    today = timezone.localdate()
    ensure_appointments(max(start, today), max(start, today) + timedelta(weeks=4), today)
    messages.success(request, 'Aulas repetidas criadas: ' + describe(weekdays, data['time'], cleaned['interval_weeks'], until) + '.')
    return redirect(after_save_url(request, _agenda_url_for(start), forward_next=False))


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
        try:
            new = move_appointment(appointment, starts, data['duration_minutes'], data['kind'], data['notes'])
        except MoveError:
            form.add_error('time', 'Escolha outro horário: este aluno já tem aula neste.')
        else:
            notifications.notify([appointment.person.user], notifications.class_moved(appointment, new))
            messages.success(request, 'Aula remarcada.')
            _warn_if_conflict(request, new)
            return redirect(after_save_url(request, _agenda_url_for(data['date']), forward_next=False))
    return render(request, 'core/appointment_form.html', {
        'form': form, 'title': 'Remarcar aula', 'appointment': appointment})


def _move_response(new):
    local = timezone.localtime(new.starts_at)
    return JsonResponse({
        'ok': True, 'id': new.pk, 'person': new.person.person_name,
        'when': f'{local:%d/%m} às {local:%H:%M}', 'date': local.date().isoformat(),
        'overlap': overlap_names(new),
        'undo_url': reverse('core:appointment_move_undo', args=[new.pk]),
    })


@staff_required
@require_POST
def appointment_move(request, pk):
    """Drag & drop / "Mover": reschedule a marked class to another day (and optionally time). JSON in, JSON out."""
    appointment = get_object_or_404(Appointment.objects.select_related('person'), pk=pk)
    if appointment.status != Appointment.Status.SCHEDULED:
        return JsonResponse({'ok': False, 'error': 'Só aulas marcadas podem ser movidas.'}, status=400)
    local = timezone.localtime(appointment.starts_at)
    try:
        day = date.fromisoformat(request.POST.get('date', ''))
        clock = time.fromisoformat(request.POST['time']) if request.POST.get('time') else local.time().replace(
            second=0, microsecond=0)
        duration = int(request.POST['duration']) if request.POST.get('duration') else None
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Data ou horário inválido.'}, status=400)
    if duration is not None and not 10 <= duration <= 480:
        return JsonResponse({'ok': False, 'error': 'A duração deve ficar entre 10 e 480 minutos.'}, status=400)
    try:
        new = move_appointment(appointment, at(day, clock), duration)
    except MoveError as error:
        return JsonResponse({'ok': False, 'error': str(error)}, status=400)
    notifications.notify([appointment.person.user], notifications.class_moved(appointment, new))
    return _move_response(new)


@staff_required
@require_POST
def appointment_move_undo(request, pk):
    """Undo a move: `pk` is the class created by it."""
    new = get_object_or_404(Appointment.objects.select_related('person'), pk=pk)
    try:
        original = undo_move(new)
    except MoveError as error:
        return JsonResponse({'ok': False, 'error': str(error)}, status=400)
    notifications.notify([new.person.user], notifications.move_undone(original))
    return JsonResponse({'ok': True, 'id': original.pk})


@staff_required
@require_POST
def appointment_request_decide(request, pk):
    """The trainer answers a student's request: approve (does what was asked) or decline (with an optional reason)."""
    asked = get_object_or_404(AppointmentRequest.objects.select_related('appointment__person'), pk=pk)
    decision = request.POST.get('decision')
    if decision not in ('approve', 'decline'):
        return HttpResponseBadRequest('Decisão inválida')
    name = asked.appointment.person.person_name
    try:
        note = request.POST.get('note', '').strip()[:200]
        if decision == 'approve':
            class_requests.approve(asked)
            messages.success(request, f'Pedido de {name} aprovado.')
        else:
            class_requests.decline(asked, note)
            messages.success(request, f'Pedido de {name} recusado.')
        notifications.notify([asked.appointment.person.user], notifications.request_answered(asked, note))
    except class_requests.RequestError as error:
        messages.error(request, str(error))
    return redirect(safe_next_url(request, reverse('core:agenda')))


@staff_required
@require_POST
def appointment_status(request, pk):
    appointment = get_object_or_404(Appointment.objects.select_related('person'), pk=pk)
    status = request.POST.get('status')
    allowed = (Appointment.Status.SCHEDULED, Appointment.Status.ATTENDED, Appointment.Status.MISSED,
               Appointment.Status.CANCELLED)
    if status not in allowed or appointment.status == Appointment.Status.RESCHEDULED:
        return HttpResponseBadRequest('Situação inválida')
    was_cancelled = appointment.status == Appointment.Status.CANCELLED
    if status != Appointment.Status.CANCELLED and live_class_at(appointment.person_id, appointment.starts_at, exclude=appointment):
        messages.error(request, 'Este aluno já tem outra aula neste horário; remova ou cancele uma delas antes.')
        return redirect(safe_next_url(request, _agenda_url_for(timezone.localtime(appointment.starts_at).date())))
    appointment.status = status
    appointment.status_changed_at = timezone.now()
    # cancelling with less notice than allowed counts the class against the package (the trainer can undo that)
    appointment.cancelled_late = status == Appointment.Status.CANCELLED and packages.late_cancel(appointment.starts_at)
    appointment.counts_override = None
    appointment.save(update_fields=['status', 'status_changed_at', 'cancelled_late', 'counts_override'])
    packages.sync(appointment.person)
    if appointment.cancelled_late:
        messages.warning(request, f'Cancelada com menos de {settings.AGENDA_NOTICE_HOURS} h de antecedência: conta como aula dada '
                                  'no pacote. Se não for para descontar, use "Não descontar".')
    if status == Appointment.Status.CANCELLED and not was_cancelled and appointment.starts_at > timezone.now():
        notifications.notify([appointment.person.user], notifications.class_cancelled(appointment))
    return redirect(safe_next_url(request, _agenda_url_for(timezone.localtime(appointment.starts_at).date())))


@staff_required
@require_POST
def appointment_counts(request, pk):
    """"Descontar / Não descontar do pacote" for one class (an illness, a class the trainer cancelled...)."""
    appointment = get_object_or_404(Appointment.objects.select_related('person'), pk=pk)
    choice = request.POST.get('counts')
    if choice not in ('yes', 'no', 'auto'):
        return HttpResponseBadRequest('Opção inválida')
    appointment.counts_override = {'yes': True, 'no': False, 'auto': None}[choice]
    appointment.save(update_fields=['counts_override'])
    packages.sync(appointment.person)
    messages.success(request, {'yes': 'Esta aula vai ser descontada do pacote.', 'no': 'Esta aula não será descontada do pacote.',
                               'auto': 'Esta aula segue a regra do pacote.'}[choice])
    return redirect(safe_next_url(request, _agenda_url_for(timezone.localtime(appointment.starts_at).date())))


@staff_required
@require_POST
def appointment_delete(request, pk):
    """Only one-off classes are deleted; a class from a fixed slot is cancelled instead (it would come back)"""
    appointment = get_object_or_404(Appointment, pk=pk)
    if appointment.slot_id:
        messages.warning(request, 'Aula de horário fixo: use "Cancelar" (excluir faria ela voltar).')
    else:
        person = appointment.person
        appointment.delete()
        packages.sync(person)
        messages.success(request, 'Aula removida.')
    return redirect(safe_next_url(request, _agenda_url_for(timezone.localtime(appointment.starts_at).date())))


@staff_required
@require_POST
def slot_add(request, pk):
    student = get_object_or_404(Person, pk=pk)
    form = ScheduleSlotForm(request.POST, plan=packages.schedule_plan(student))
    if form.is_valid():
        data = form.cleaned_data
        today = timezone.localdate()
        start = data.get('starts_on') or today
        weekdays = data['all_weekdays']
        until = form.series_end(weekdays, start)
        create_series(student, weekdays, data['start_time'], data['duration_minutes'], start, data['interval_weeks'], until)
        ensure_appointments(today, today + timedelta(weeks=4), today)
        messages.success(request, 'Horário fixo: ' + describe(weekdays, data['start_time'], data['interval_weeks'], until) + '.')
    else:
        messages.error(request, 'Horário inválido: ' + ' '.join(e for errs in form.errors.values() for e in errs))
    return redirect(_student_tab(request, student.pk, 'agenda'))


@staff_required
@require_POST
def appointment_series_end(request, pk):
    """"This class and the following ones": stop the fixed time from this class on."""
    appointment = get_object_or_404(Appointment.objects.select_related('slot', 'person'), pk=pk)
    if appointment.slot_id is None:
        messages.warning(request, 'Esta aula não faz parte de um horário fixo.')
    else:
        slot, removed = end_series_from(appointment)
        gone = '1 aula marcada saiu da agenda' if removed == 1 else f'{removed} aulas marcadas saíram da agenda'
        messages.success(request, f'Horário fixo encerrado: {slot.get_weekday_display()} às {slot.start_time:%H:%M} '
                                  f'deixa de repetir a partir desta aula ({gone}).')
    return redirect(safe_next_url(request, _agenda_url_for(timezone.localtime(appointment.starts_at).date())))


def _skip_dates(values):
    """ISO dates the trainer unticked in the preview (bad values are ignored)."""
    found = set()
    for value in list(values)[:MAX_SKIPS]:
        try:
            found.add(date.fromisoformat(value))
        except ValueError:
            continue
    return found


@staff_required
def slot_preview(request, pk):
    """The dates "Preencher a agenda" would create, as an HTML fragment for the form (every change refreshes it)."""
    student = get_object_or_404(Person, pk=pk)
    plan = packages.schedule_plan(student)
    form = SlotFillForm(request.GET)
    preview = None
    if plan is not None and form.is_valid():
        data = form.cleaned_data
        preview = build_preview(student, plan, data['weekdays'], data['starts_on'], data['start_time'],
                                data['duration_minutes'], data['interval_weeks'], _skip_dates(request.GET.getlist('skip')))
    response = render(request, 'core/student/_slot_preview.html', {
        'preview': preview, 'plan': plan, 'start_time': form.cleaned_data.get('start_time') if preview else None})
    response['Cache-Control'] = 'no-store'
    return response


@staff_required
@require_POST
def slot_fill(request, pk):
    """Create the fixed times and every class the package still needs (the end date is calculated)."""
    student = get_object_or_404(Person, pk=pk)
    plan = packages.schedule_plan(student)
    form = SlotFillForm(request.POST)
    if plan is None:
        messages.error(request, 'Este aluno não tem pacote em uso. Venda um pacote primeiro.')
    elif not form.is_valid():
        messages.error(request, 'Horário inválido: ' + ' '.join(e for errs in form.errors.values() for e in errs))
    else:
        data = form.cleaned_data
        try:
            result = fill_series(student, plan, data['weekdays'], data['starts_on'], data['start_time'],
                                 data['duration_minutes'], data['interval_weeks'], _skip_dates(request.POST.getlist('skip')))
        except FillError as error:
            messages.error(request, str(error))
        else:
            n = result['created']
            text = f"{n} aula{'' if n == 1 else 's'} marcada{'' if n == 1 else 's'}, até {result['last']:%d/%m/%Y}."
            if result['preview']['beyond']:
                text += ' Atenção: parte das aulas passa da validade do pacote; prorrogue-o se for preciso.'
            if result['preview']['overlaps']:
                text += ' Atenção: alguma aula se sobrepõe à de outro aluno (veja na agenda).'
            messages.success(request, text)
    return redirect(_student_tab(request, student.pk, 'agenda'))


@staff_required
@require_POST
def slot_topup(request, pk):
    """Mark the classes the package is missing at the end of its series."""
    student = get_object_or_404(Person, pk=pk)
    plan = packages.schedule_plan(student)
    if plan is None:
        messages.error(request, 'Este aluno não tem pacote em uso.')
    else:
        try:
            result = top_up(student, plan)
        except FillError as error:
            messages.error(request, str(error))
        else:
            n = result['created']
            messages.success(request, f"{n} aula{'' if n == 1 else 's'} a mais marcada{'' if n == 1 else 's'}, "
                                      f"até {result['last']:%d/%m/%Y}.")
    return redirect(_student_tab(request, student.pk, 'agenda'))


@staff_required
def slot_edit(request, pk):
    """Change a fixed time (day, hour, length, end) from a date on; past classes keep the old time."""
    slot = get_object_or_404(ScheduleSlot.objects.select_related('person'), pk=pk)
    today = timezone.localdate()
    form = SlotEditForm(request.POST or None, initial={
        'weekday': slot.weekday, 'start_time': slot.start_time, 'duration_minutes': slot.duration_minutes,
        'from_day': today, 'valid_until': slot.valid_until})
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            current, removed = edit_series(slot, data['weekday'], data['start_time'], data['duration_minutes'],
                                           data['valid_until'], data['from_day'])
        except SeriesError as error:
            form.add_error(None, str(error))
        else:
            ensure_appointments(today, today + timedelta(weeks=4), today)
            messages.success(request, 'Horário fixo atualizado: ' + describe(
                [current.weekday], current.start_time, current.interval_weeks, current.valid_until) + '.'
                + (f' {removed} aula(s) marcada(s) foram recriadas no novo horário.' if removed else ''))
            return redirect(_student_tab(request, slot.person_id, 'agenda'))
    return render(request, 'core/slot_form.html', {'form': form, 'slot': slot, 'student': slot.person, 'title': 'Editar horário fixo'})


@staff_required
@require_POST
def slot_delete(request, pk):
    slot = get_object_or_404(ScheduleSlot, pk=pk)
    person_id = slot.person_id
    with transaction.atomic():
        slot.appointments.filter(status=Appointment.Status.SCHEDULED, starts_at__gt=timezone.now()).delete()
        slot.delete()  # past classes stay (their slot link is cleared)
    messages.success(request, 'Horário fixo removido. As aulas futuras dele saíram da agenda.')
    back = safe_next_url(request, '')
    if back.startswith(reverse('core:agenda')):   # removed from a calendar card: stay on the calendar
        return redirect(back)
    return redirect(_student_tab(request, person_id, 'agenda'))
