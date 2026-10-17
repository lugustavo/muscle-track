from django.db import models
from django.contrib.auth.models import User
import io
import os
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

from PIL import Image, ImageOps

from .validators import validate_avatar_image, validate_photo_image


class MuscleGroup(models.Model):
    """Model representing a muscle group"""
    name = models.CharField(max_length=50, unique=True, verbose_name=_('Nome'))
    slug = models.SlugField(max_length=50, unique=True, verbose_name=_('Slug'))
    description = models.TextField(blank=True, verbose_name=_('Descrição'))
    is_active = models.BooleanField(default=True, verbose_name=_('Ativo'))
    order = models.IntegerField(default=0, verbose_name=_('Ordem de Exibição'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Grupo Muscular')
        verbose_name_plural = _('Grupos Musculares')
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Person(models.Model):
    """Model representing a student/person in the gym"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='person')
    person_name = models.CharField(max_length=200, verbose_name=_('Nome'))
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True, validators=[validate_avatar_image], verbose_name=_('Foto de Perfil'))
    # When the student closed the first-access tour ("como usar o app"); None = still to be shown
    intro_seen_at = models.DateTimeField(null=True, blank=True, editable=False)
    address = models.CharField(max_length=300, blank=True, verbose_name=_('Endereço'))
    city = models.CharField(max_length=100, blank=True, verbose_name=_('Cidade'))
    state = models.CharField(max_length=2, blank=True, verbose_name=_('Estado'))
    zip_code = models.CharField(max_length=10, blank=True, verbose_name=_('CEP'))
    phone = models.CharField(max_length=20, blank=True, verbose_name=_('Telefone'))
    birth_date = models.DateField(null=True, blank=True, verbose_name=_('Data de Nascimento'))
    notes = models.TextField(blank=True, verbose_name=_('Observações'))

    # Trainer's file on the student (not editable by the student)
    goal = models.TextField(blank=True, verbose_name=_('Objetivo'))
    health_notes = models.TextField(
        blank=True, verbose_name=_('Saúde, lesões e restrições'),
        help_text=_('Lesões, cirurgias, medicamentos, limitações médicas...')
    )
    emergency_contact_name = models.CharField(max_length=200, blank=True, verbose_name=_('Contato de emergência'))
    emergency_contact_phone = models.CharField(max_length=20, blank=True, verbose_name=_('Telefone de emergência'))
    plan_name = models.CharField(max_length=100, blank=True, verbose_name=_('Plano contratado'))
    plan_expires_on = models.DateField(null=True, blank=True, verbose_name=_('Vencimento do plano'))
    # The expiry date a "plan is expiring" reminder was already sent for (a renewal changes the date, so it re-arms)
    plan_reminder_for = models.DateField(null=True, blank=True)
    # Where the plan came from (a template, like Routine -> PersonRoutine). The values below are
    # copied to the student when the plan is assigned, so editing the template never changes them.
    plan = models.ForeignKey('Package', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='students', verbose_name=_('Pacote atual (catálogo)'))
    # Confidential commercial terms: only the trainer sees or edits them
    plan_price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                     validators=[MinValueValidator(0)], verbose_name=_('Valor negociado do período'))
    hourly_rate = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                      validators=[MinValueValidator(0)], verbose_name=_('Valor/hora negociado'))

    class Status(models.TextChoices):
        TRIAL = 'trial', _('Experimental')
        ACTIVE = 'active', _('Ativo')
        PAUSED = 'paused', _('Pausado')
        FORMER = 'former', _('Ex-aluno')

    OPEN_STATUSES = (Status.TRIAL, Status.ACTIVE)

    # Lifecycle. `is_active` stays the access switch (login, dashboard, lists); only
    # set_status() changes it, so the two never disagree.
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, verbose_name=_('Situação'))
    status_reason = models.TextField(blank=True, verbose_name=_('Motivo da situação'))
    status_changed_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Situação desde'))
    is_active = models.BooleanField(default=True, verbose_name=_('Ativo'))
    deactivated_at = models.DateTimeField(null=True, blank=True, editable=False, verbose_name=_('Inativado em'))
    # The student's own motivation settings (core/consistency.py): the ring, streak and milestones on "Hoje"
    motivation_enabled = models.BooleanField(
        default=True, verbose_name=_('Mostrar meta semanal, sequência e marcos'),
        help_text=_('Desligue se preferir ver só os treinos, sem metas.'))
    weekly_goal = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(7)],
        verbose_name=_('Minha meta de treinos por semana'),
        help_text=_('Usada quando o seu plano não define uma meta. De 1 a 7.'))
    # Bumped to revoke invite links already handed out (see core/invites.py)
    invite_version = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Aluno')
        verbose_name_plural = _('Alunos')
        ordering = ['person_name']

    def __str__(self):
        return self.person_name

    @property
    def age(self):
        """Calculate age from birth_date"""
        if not self.birth_date:
            return None
        today = timezone.now().date()
        age = today.year - self.birth_date.year
        if today.month < self.birth_date.month or (today.month == self.birth_date.month and today.day < self.birth_date.day):
            age -= 1
        return age

    def set_status(self, status, reason='', actor=None):
        """The single place that changes a student's lifecycle status.

        Keeps is_active, deactivated_at and the login account in sync and writes the history.
        Paused and former students cannot log in (same as the old "inactive")."""
        if status not in self.Status.values:
            raise ValueError(f'Unknown status: {status}')
        previous, previous_reason = self.status, self.status_reason
        reason = (reason or '').strip()
        if status == previous and reason == previous_reason:
            return False
        now = timezone.now()
        self.status = status
        self.status_reason = reason
        if status != previous:
            self.status_changed_at = now
        self.is_active = status in self.OPEN_STATUSES
        sessions_closed = 0
        if self.is_active:
            self.deactivated_at = None
        else:
            if self.deactivated_at is None:
                self.deactivated_at = now
            # A student without access no longer has upcoming classes on the calendar...
            Appointment.objects.filter(person=self, status=Appointment.Status.SCHEDULED,
                                       starts_at__gt=now).delete()
            # ...nor a workout left running (kept with what was done, or dropped when nothing was logged)
            from .workout_sessions import close_open
            sessions_closed = close_open(self, now)
        self.save(update_fields=['status', 'status_reason', 'status_changed_at', 'is_active',
                                 'deactivated_at', 'updated_at'])
        User.objects.filter(pk=self.user_id).update(is_active=self.is_active)
        labels = dict(self.Status.choices)
        StudentEvent.objects.create(
            person=self, actor=actor, kind=StudentEvent.Kind.STATUS,
            summary=gettext('Situação: %(old)s → %(new)s') % {'old': labels[previous], 'new': labels[status]}
            if status != previous else gettext('Motivo da situação atualizado'),
            changes={'status': [previous, status], 'reason': reason,
                     **({'sessions_closed': sessions_closed} if sessions_closed else {})})
        return True

    PLAN_EXPIRING_DAYS = 14

    @property
    def days_to_plan_expiry(self):
        if not self.plan_expires_on:
            return None
        return (self.plan_expires_on - timezone.localdate()).days

    @property
    def plan_status(self):
        """'none' (no expiry set), 'ok', 'expiring' (within 14 days) or 'expired'"""
        days = self.days_to_plan_expiry
        if days is None:
            return 'none'
        if days < 0:
            return 'expired'
        if days <= self.PLAN_EXPIRING_DAYS:
            return 'expiring'
        return 'ok'

    def get_avatar_url(self):
        """Get avatar URL or default image"""
        if self.avatar:
            return self.avatar.url
        return '/static/images/default-avatar.svg'

    def get_last_workout_session(self):
        """Get the most recent workout session"""
        return WorkoutSession.objects.filter(
            person_routine__person=self
        ).order_by('-start_time').first()

    def get_total_workouts(self):
        """Get total number of completed workouts"""
        return WorkoutSession.objects.filter(
            person_routine__person=self,
            is_completed=True
        ).count()


class Exercise(models.Model):
    """Model representing an exercise"""
    MEDIA_TYPE_CHOICES = [
        ('image', _('Imagem')),
        ('gif', 'GIF'),
        ('video', _('Vídeo')),
        ('youtube', 'YouTube'),
        ('url', _('Link Externo')),
    ]

    exercise_name = models.CharField(max_length=200, verbose_name=_('Nome do Exercício'))
    description = models.TextField(blank=True, verbose_name=_('Descrição'))
    muscle_group = models.ForeignKey(MuscleGroup, on_delete=models.PROTECT, related_name='exercises', verbose_name=_('Grupo Muscular'))
    default_reps = models.IntegerField(default=12, validators=[MinValueValidator(1)], verbose_name=_('Repetições Padrão'))
    default_sets = models.IntegerField(default=3, validators=[MinValueValidator(1)], verbose_name=_('Séries Padrão'))

    # Media fields
    media_type = models.CharField(max_length=20, choices=MEDIA_TYPE_CHOICES, default='image', verbose_name=_('Tipo de Mídia'))
    media_file = models.FileField(upload_to='exercises/', blank=True, null=True, verbose_name=_('Arquivo'))
    media_url = models.URLField(blank=True, verbose_name=_('URL da Mídia'))
    thumbnail = models.ImageField(upload_to='exercises/thumbnails/', blank=True, null=True, verbose_name=_('Miniatura'))

    instructions = models.TextField(blank=True, verbose_name=_('Instruções'))
    is_active = models.BooleanField(default=True, verbose_name=_('Ativo'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Exercício')
        verbose_name_plural = _('Exercícios')
        ordering = ['exercise_name']

    def __str__(self):
        return f"{self.exercise_name} ({self.muscle_group.name})"


class Routine(models.Model):
    """Model representing a workout routine template"""
    routine_name = models.CharField(max_length=200, verbose_name=_('Nome do Treino'))
    description = models.TextField(blank=True, verbose_name=_('Descrição'))
    difficulty_level = models.CharField(
        max_length=20,
        choices=[
            ('iniciante', _('Iniciante')),
            ('intermediario', _('Intermediário')),
            ('avancado', _('Avançado')),
        ],
        default='intermediario',
        verbose_name=_('Nível de Dificuldade')
    )
    estimated_duration = models.IntegerField(
        default=60,
        validators=[MinValueValidator(1)],
        help_text=_('Duração estimada em minutos'),
        verbose_name=_('Duração Estimada (min)')
    )
    is_active = models.BooleanField(default=True, verbose_name=_('Ativo'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Modelo de Treino')
        verbose_name_plural = _('Modelos de Treino')
        ordering = ['routine_name']

    def __str__(self):
        return self.routine_name


class RoutineExercise(models.Model):
    """Model linking exercises to routines with specific parameters"""
    routine = models.ForeignKey(Routine, on_delete=models.CASCADE, related_name='exercises', verbose_name=_('Treino'))
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name='routines', verbose_name=_('Exercício'))
    order = models.IntegerField(default=1, validators=[MinValueValidator(1)], verbose_name=_('Ordem'))
    sets = models.IntegerField(default=3, validators=[MinValueValidator(1)], verbose_name=_('Séries'))
    reps = models.IntegerField(default=12, validators=[MinValueValidator(1)], verbose_name=_('Repetições'))
    rest_seconds = models.IntegerField(default=60, validators=[MinValueValidator(0)], verbose_name=_('Descanso (seg)'))
    notes = models.TextField(blank=True, verbose_name=_('Observações'))

    class Meta:
        verbose_name = _('Exercício do Treino')
        verbose_name_plural = _('Exercícios do Treino')
        ordering = ['routine', 'order']
        unique_together = ['routine', 'exercise', 'order']

    def __str__(self):
        return f"{self.routine.routine_name} - {self.exercise.exercise_name}"


class PersonRoutine(models.Model):
    """Model representing a personalized routine for a specific person (copy of template)"""
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='routines', verbose_name=_('Aluno'))

    # Template reference (optional - for tracking origin)
    routine_template = models.ForeignKey(
        Routine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='person_routines',
        verbose_name=_('Template Original')
    )

    # Copied fields from template (editable per person)
    routine_name = models.CharField(max_length=200, default='Treino Personalizado', verbose_name=_('Nome do Treino'))
    description = models.TextField(blank=True, default='', verbose_name=_('Descrição'))
    difficulty_level = models.CharField(
        max_length=20,
        choices=[
            ('iniciante', _('Iniciante')),
            ('intermediario', _('Intermediário')),
            ('avancado', _('Avançado')),
        ],
        default='intermediario',
        verbose_name=_('Nível de Dificuldade')
    )
    estimated_duration = models.IntegerField(
        default=60,
        validators=[MinValueValidator(1)],
        help_text=_('Duração estimada em minutos'),
        verbose_name=_('Duração Estimada (min)')
    )

    # Person-specific fields
    routine_date = models.DateField(default=timezone.now, verbose_name=_('Data de Início'))
    start_date = models.DateField(null=True, blank=True, verbose_name=_('Data de Início Planejada'))
    end_date = models.DateField(null=True, blank=True, verbose_name=_('Data de Término'))
    is_active = models.BooleanField(default=True, verbose_name=_('Ativo'))
    notes = models.TextField(blank=True, verbose_name=_('Observações'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Treino do Aluno')
        verbose_name_plural = _('Treinos dos Alunos')
        ordering = ['-routine_date']

    def __str__(self):
        return f"{self.person.person_name} - {self.routine_name} ({self.routine_date})"


class PersonRoutineExercise(models.Model):
    """Model representing exercises in a person's routine (personalized copy)"""
    person_routine = models.ForeignKey(
        PersonRoutine,
        on_delete=models.CASCADE,
        related_name='exercises',
        verbose_name=_('Treino do Aluno')
    )
    # PROTECT: deleting an exercise must not silently wipe the students' history
    exercise = models.ForeignKey(Exercise, on_delete=models.PROTECT, verbose_name=_('Exercício'))

    # Exercise configuration (copied from template, editable per person)
    order = models.IntegerField(default=1, validators=[MinValueValidator(1)], verbose_name=_('Ordem'))
    sets = models.IntegerField(default=3, validators=[MinValueValidator(1)], verbose_name=_('Séries'))
    reps = models.IntegerField(default=12, validators=[MinValueValidator(1)], verbose_name=_('Repetições'))
    rest_seconds = models.IntegerField(default=60, validators=[MinValueValidator(0)], verbose_name=_('Descanso (seg)'))
    notes = models.TextField(blank=True, verbose_name=_('Observações'))

    class Meta:
        verbose_name = _('Exercício do Treino do Aluno')
        verbose_name_plural = _('Exercícios dos Treinos dos Alunos')
        ordering = ['person_routine', 'order']

    def __str__(self):
        return f"{self.person_routine} - {self.exercise.exercise_name}"


class WorkoutSession(models.Model):
    """Model tracking workout sessions with timer"""
    person_routine = models.ForeignKey(
        PersonRoutine,
        on_delete=models.CASCADE,
        related_name='sessions',
        verbose_name=_('Treino')
    )
    start_time = models.DateTimeField(auto_now_add=True, verbose_name=_('Início'))
    end_time = models.DateTimeField(null=True, blank=True, verbose_name=_('Término'))
    duration_minutes = models.IntegerField(null=True, blank=True, verbose_name=_('Duração (min)'))
    is_completed = models.BooleanField(default=False, verbose_name=_('Concluído'))
    notes = models.TextField(blank=True, verbose_name=_('Observações'))

    # Pause support: time spent paused does not count towards the duration
    paused_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Pausado em'))
    paused_seconds = models.PositiveIntegerField(default=0, verbose_name=_('Segundos em pausa'))

    class Meta:
        verbose_name = _('Sessão de Treino')
        verbose_name_plural = _('Sessões de Treino')
        ordering = ['-start_time']

    def __str__(self):
        return f"{self.person_routine.person.person_name} - {self.start_time.strftime('%d/%m/%Y %H:%M')}"

    @property
    def is_paused(self):
        return self.paused_at is not None and self.end_time is None

    def elapsed_seconds(self, now=None):
        """Active training time (wall-clock time minus paused time)."""
        if self.end_time:
            end = self.end_time
        elif self.paused_at:
            end = self.paused_at
        else:
            end = now or timezone.now()
        return max(0, int((end - self.start_time).total_seconds()) - self.paused_seconds)

    def pause(self):
        if not self.end_time and not self.paused_at:
            self.paused_at = timezone.now()
            self.save(update_fields=['paused_at'])

    def resume(self):
        if self.paused_at and not self.end_time:
            self.paused_seconds += max(0, int((timezone.now() - self.paused_at).total_seconds()))
            self.paused_at = None
            self.save(update_fields=['paused_at', 'paused_seconds'])

    def end_session(self, at=None):
        """End the workout session and calculate duration.

        `at` lets callers close an abandoned session at a capped time instead of "now".
        """
        if not self.end_time:
            now = at or timezone.now()
            if self.paused_at:
                self.paused_seconds += max(0, int((now - self.paused_at).total_seconds()))
                self.paused_at = None
            self.end_time = now
            self.duration_minutes = self.elapsed_seconds() // 60
            self.is_completed = True
            self.save()


class SessionExercise(models.Model):
    """Model tracking individual exercise execution within a session"""
    session = models.ForeignKey(
        WorkoutSession,
        on_delete=models.CASCADE,
        related_name='exercise_logs',
        verbose_name=_('Sessão')
    )
    person_routine_exercise = models.ForeignKey(
        PersonRoutineExercise,
        on_delete=models.CASCADE,
        related_name='session_logs',
        verbose_name=_('Exercício do Treino')
    )

    # Execution data for this specific session
    weight_used = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name=_('Carga (kg)')
    )
    is_completed = models.BooleanField(default=False, verbose_name=_('Concluído'))
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Completado em'))
    notes = models.TextField(blank=True, verbose_name=_('Observações'))

    class Meta:
        verbose_name = _('Execução de Exercício na Sessão')
        verbose_name_plural = _('Execuções de Exercícios nas Sessões')
        unique_together = ['session', 'person_routine_exercise']
        ordering = ['person_routine_exercise__order']

    def __str__(self):
        return f"{self.session} - {self.person_routine_exercise.exercise.exercise_name}"

    def mark_completed(self):
        """Mark exercise as completed in this session"""
        self.is_completed = True
        self.completed_at = timezone.now()
        self.save()

    def sync_from_sets(self):
        """Derive the exercise-level summary from its sets.

        weight_used = heaviest set actually done (what charts and records read);
        is_completed = every set done.
        """
        sets = list(self.sets.all())
        done = [s for s in sets if s.is_done]
        weights = [s.weight for s in done if s.weight is not None]
        self.weight_used = max(weights) if weights else None
        self.is_completed = bool(sets) and len(done) == len(sets)
        self.completed_at = max((s.completed_at for s in done if s.completed_at), default=None) if self.is_completed else None
        self.save(update_fields=['weight_used', 'is_completed', 'completed_at'])
        return len(done), len(sets)


class SessionSet(models.Model):
    """One set of an exercise performed in a session (weight, reps, done)"""
    session_exercise = models.ForeignKey(
        SessionExercise,
        on_delete=models.CASCADE,
        related_name='sets',
        verbose_name=_('Exercício da sessão')
    )
    number = models.PositiveIntegerField(validators=[MinValueValidator(1)], verbose_name=_('Série'))
    weight = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)], verbose_name=_('Carga (kg)')
    )
    reps = models.PositiveIntegerField(null=True, blank=True, verbose_name=_('Repetições'))
    is_done = models.BooleanField(default=False, verbose_name=_('Feita'))
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Completada em'))
    # Last time anything about the set changed: the sign of life of a running workout (core/workout_sessions.py)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        verbose_name = _('Série')
        verbose_name_plural = _('Séries')
        ordering = ['session_exercise', 'number']
        unique_together = ['session_exercise', 'number']

    def __str__(self):
        return gettext('%(exercise)s - série %(number)s') % {'exercise': self.session_exercise, 'number': self.number}


class Assessment(models.Model):
    """Physical assessment of a student on a given date (all measures optional)"""
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='assessments', verbose_name=_('Aluno'))
    date = models.DateField(default=timezone.localdate, verbose_name=_('Data'))
    weight_kg = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(1)], verbose_name=_('Peso (kg)'))
    height_cm = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(30)], verbose_name=_('Altura (cm)'))
    body_fat_pct = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)], verbose_name=_('% de gordura'))
    chest_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                                   validators=[MinValueValidator(0)], verbose_name=_('Peitoral (cm)'))
    waist_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                                   validators=[MinValueValidator(0)], verbose_name=_('Cintura (cm)'))
    hip_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                                 validators=[MinValueValidator(0)], verbose_name=_('Quadril (cm)'))
    arm_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                                 validators=[MinValueValidator(0)], verbose_name=_('Braço (cm)'))
    thigh_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                                   validators=[MinValueValidator(0)], verbose_name=_('Coxa (cm)'))
    notes = models.TextField(blank=True, verbose_name=_('Observações'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Avaliação física')
        verbose_name_plural = _('Avaliações físicas')
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.person.person_name} - {self.date:%d/%m/%Y}'

    @property
    def bmi(self):
        if self.weight_kg and self.height_cm:
            meters = float(self.height_cm) / 100
            return round(float(self.weight_kg) / (meters * meters), 1)
        return None


def progress_photo_path(instance, filename):
    """Unguessable file names: photos are private, the name is not the only protection but helps."""
    ext = os.path.splitext(filename)[1].lower()
    return f'progress/{instance.person_id}/{uuid.uuid4().hex}{ext}'


class ProgressPhoto(models.Model):
    """Private progress photo of a student; only served to the trainer and the student.

    A photo belongs to an assessment (one per position: front, left, right, back); photos without one are loose
    (the ones sent before assessments held photos, or "other" positions)."""

    class Angle(models.TextChoices):
        FRONT = 'front', _('Frente')
        LEFT = 'left', _('Lado esquerdo')
        RIGHT = 'right', _('Lado direito')
        BACK = 'back', _('Costas')
        OTHER = 'other', _('Outra')

    # The standard sequence shown in every assessment, in this order
    SEQUENCE = (Angle.FRONT, Angle.LEFT, Angle.RIGHT, Angle.BACK)

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='progress_photos', verbose_name=_('Aluno'))
    assessment = models.ForeignKey('Assessment', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='photos', verbose_name=_('Avaliação'))
    angle = models.CharField(max_length=10, choices=Angle.choices, default=Angle.OTHER, verbose_name=_('Posição'))
    date = models.DateField(default=timezone.localdate, verbose_name=_('Data'))
    image = models.ImageField(upload_to=progress_photo_path, validators=[validate_photo_image], verbose_name=_('Foto'))
    thumbnail = models.ImageField(upload_to=progress_photo_path, blank=True, editable=False, verbose_name=_('Miniatura'))
    caption = models.CharField(max_length=200, blank=True, verbose_name=_('Legenda'))
    uploaded_by_student = models.BooleanField(default=False, editable=False, verbose_name=_('Enviada pelo aluno'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Foto de progresso')
        verbose_name_plural = _('Fotos de progresso')
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.person.person_name} - {self.date:%d/%m/%Y}'

    THUMBNAIL_SIZE = (480, 480)
    MAX_SIZE = (2000, 2000)

    def save(self, *args, **kwargs):
        if self.image and not self.image._committed:   # a new upload: keep only the pixels (no EXIF, no GPS)
            self.image.save(f'{uuid.uuid4().hex}.jpg', self._clean_image(), save=False)
        if self.image and not self.thumbnail:
            self.thumbnail.save(f'{uuid.uuid4().hex}.jpg', self._make_thumbnail(), save=False)
        super().save(*args, **kwargs)

    def _clean_image(self):
        """Re-encode as a JPEG of a sensible size: drops every metadata block (location, device) and fixes the rotation."""
        self.image.seek(0)
        with Image.open(self.image) as img:
            img = ImageOps.exif_transpose(img).convert('RGB')
            img.thumbnail(self.MAX_SIZE)
            buffer = io.BytesIO()
            img.save(buffer, 'JPEG', quality=88, optimize=True)
        return ContentFile(buffer.getvalue())

    def _make_thumbnail(self):
        self.image.seek(0)
        with Image.open(self.image) as img:
            img = ImageOps.exif_transpose(img).convert('RGB')
            img.thumbnail(self.THUMBNAIL_SIZE)
            buffer = io.BytesIO()
            img.save(buffer, 'JPEG', quality=80, optimize=True)
        self.image.seek(0)
        return ContentFile(buffer.getvalue())


class StudentNote(models.Model):
    """Dated note written by the trainer about a student (never shown to the student)"""
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='trainer_notes', verbose_name=_('Aluno'))
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name=_('Autor'))
    text = models.TextField(verbose_name=_('Anotação'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Anotação da personal')
        verbose_name_plural = _('Anotações da personal')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.person.person_name} - {self.created_at:%d/%m/%Y}'


class StudentEvent(models.Model):
    """Audit trail of a student's file: who changed what, when. Deleted together with the student."""

    class Kind(models.TextChoices):
        CREATED = 'created', _('Cadastro')
        PROFILE_EDIT = 'profile_edit', _('Dados alterados')
        STATUS = 'status', _('Situação')
        INVITE_REVOKED = 'invite_revoked', _('Convite revogado')
        INVITE_ACCEPTED = 'invite_accepted', _('Convite aceito')
        CONSENT = 'consent', _('Consentimento')
        PAYMENT = 'payment', _('Pagamento')
        DATA_EXPORT = 'data_export', _('Exportação de dados')
        ERASURE_REQUEST = 'erasure_request', _('Pedido de exclusão')
        PACKAGE = 'package', _('Pacote')

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='events', verbose_name=_('Aluno'))
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name=_('Quem'))
    kind = models.CharField(max_length=20, choices=Kind.choices, verbose_name=_('Tipo'))
    summary = models.CharField(max_length=300, blank=True, verbose_name=_('Resumo'))
    # {field: [before, after]}; sensitive fields store [None, None] (only "changed")
    changes = models.JSONField(default=dict, blank=True, verbose_name=_('Alterações'))
    at = models.DateTimeField(auto_now_add=True, verbose_name=_('Quando'))

    class Meta:
        verbose_name = _('Evento do aluno')
        verbose_name_plural = _('Histórico do aluno')
        ordering = ['-at', '-pk']

    @property
    def change_rows(self):
        """[(label, before, after)] of a profile edit; before/after are None for sensitive fields."""
        from django.core.exceptions import FieldDoesNotExist
        rows = []
        for name, (before, after) in self.changes.items():
            try:
                label = str(Person._meta.get_field(name).verbose_name)
            except FieldDoesNotExist:
                label = {'email': gettext('E-mail')}.get(name, name)
            rows.append((label, before, after))
        return rows

    def __str__(self):
        return f'{self.person_id} {self.kind} {self.at:%d/%m/%Y}'


class ConsentRecord(models.Model):
    """One consent decision (LGPD). The current state of a kind is its latest record."""

    class Kind(models.TextChoices):
        TERMS = 'terms', _('Termos de uso e política de privacidade')
        HEALTH_DATA = 'health_data', _('Tratamento de dados de saúde')
        PHOTOS = 'photos', _('Fotos de evolução')

    class Source(models.TextChoices):
        STUDENT = 'student', _('Pelo próprio aluno')
        TRAINER = 'trainer', _('Registrado pela personal')

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='consents', verbose_name=_('Aluno'))
    kind = models.CharField(max_length=20, choices=Kind.choices, verbose_name=_('Tipo'))
    version = models.CharField(max_length=20, verbose_name=_('Versão do texto'))
    granted = models.BooleanField(verbose_name=_('Concedido'))
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.STUDENT)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    at = models.DateTimeField(default=timezone.now, verbose_name=_('Data'))

    class Meta:
        verbose_name = _('Consentimento')
        verbose_name_plural = _('Consentimentos')
        ordering = ['-at', '-pk']

    def __str__(self):
        return f'{self.person_id} {self.kind} v{self.version} {gettext("sim") if self.granted else gettext("não")}'


CENTS = Decimal('0.01')


def compute_prices(mode, sessions, per_session=None, total=None):
    """(price per class, total) from the one the trainer typed: `mode` is 'per_session' or 'total'.

    Typing the price per class gives total = per class x classes; typing the total gives the price per class
    (rounded to cents; the total itself is kept exactly as typed). Either may be None."""
    sessions = int(sessions or 0)
    if mode == 'total':
        if total is None:
            return None, None
        total = Decimal(total).quantize(CENTS)
        return (total / sessions).quantize(CENTS) if sessions else None, total
    if per_session is None:
        return None, None
    per_session = Decimal(per_session).quantize(CENTS)
    return per_session, per_session * sessions


class Package(models.Model):
    """Package of classes (catalog), sold to many students like Exercise and Routine are assigned.

    A package is a number of classes to be taken within a number of days counted from the first class; the price is
    typed either per class or as a total (the other one is calculated)."""
    PRICING = [('per_session', _('Valor por aula')), ('total', _('Valor total'))]

    name = models.CharField(max_length=100, unique=True, verbose_name=_('Nome'))
    description = models.TextField(blank=True, verbose_name=_('Descrição'))
    sessions = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(1), MaxValueValidator(200)], verbose_name=_('Aulas no pacote'))
    # None = no deadline (special packages); otherwise counted from the first class
    valid_days = models.PositiveSmallIntegerField(
        default=60, null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(1000)],
        verbose_name=_('Prazo (dias)'), help_text=_('Conta a partir da primeira aula.'))
    pricing_mode = models.CharField(max_length=12, choices=PRICING, default='per_session', verbose_name=_('Como o valor é informado'))
    price_per_session = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                            validators=[MinValueValidator(0)], verbose_name=_('Valor por aula'))
    price_total = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                      validators=[MinValueValidator(0)], verbose_name=_('Valor total'))
    is_active = models.BooleanField(default=True, verbose_name=_('Ativo'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Pacote')
        verbose_name_plural = _('Pacotes')
        ordering = ['name']

    def save(self, *args, **kwargs):
        self.price_per_session, self.price_total = compute_prices(
            self.pricing_mode, self.sessions, self.price_per_session, self.price_total)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class StudentPackage(models.Model):
    """A package sold to one student: a copy of the numbers at the time of the sale (changing the catalog never
    changes what was sold). The balance is not stored: it is counted from the classes linked to it (core/packages.py)."""
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='packages', verbose_name=_('Aluno'))
    package = models.ForeignKey(Package, on_delete=models.SET_NULL, null=True, blank=True, related_name='sold',
                                verbose_name=_('Pacote do catálogo'))
    name = models.CharField(max_length=100, verbose_name=_('Nome'))
    sessions = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)], verbose_name=_('Aulas'))
    valid_days = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name=_('Prazo (dias)'))   # None: no deadline
    price_per_session = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                            validators=[MinValueValidator(0)], verbose_name=_('Valor por aula'))
    price_total = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                      validators=[MinValueValidator(0)], verbose_name=_('Valor total'))
    sold_on = models.DateField(default=timezone.localdate, verbose_name=_('Vendido em'))
    # The deadline counts from the first class (set by core/packages.sync from the first class that counts)
    starts_on = models.DateField(null=True, blank=True, verbose_name=_('Primeira aula'))
    extension_days = models.PositiveSmallIntegerField(default=0, verbose_name=_('Dias de prorrogação'))
    frozen_days = models.PositiveSmallIntegerField(default=0, verbose_name=_('Dias congelado'))
    frozen_since = models.DateField(null=True, blank=True, verbose_name=_('Congelado desde'))
    cancelled_at = models.DateTimeField(null=True, blank=True)
    used_at_cancel = models.PositiveSmallIntegerField(null=True, blank=True)   # classes given when it was cancelled
    cancel_fee = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)   # fee kept when it was cancelled
    notes = models.CharField(max_length=200, blank=True, verbose_name=_('Observação'))
    created_at = models.DateTimeField(auto_now_add=True)
    # What the reminders already told about this package (core/reminders.py): once each, and a new deadline re-arms them
    low_notified_at = models.DateTimeField(null=True, blank=True)
    expiring_notified_for = models.DateField(null=True, blank=True)
    expired_notified_for = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = _('Pacote do aluno')
        verbose_name_plural = _('Pacotes dos alunos')
        ordering = ['sold_on', 'pk']

    def __str__(self):
        return f'{self.name} ({self.person_id})'


class Payment(models.Model):
    """A payment received from a student for a package."""

    class Method(models.TextChoices):
        PIX = 'pix', 'Pix'
        CASH = 'cash', _('Dinheiro')
        MBWAY = 'mbway', 'MB Way'
        SPIN = 'spin', 'SPIN'
        CARD = 'card', _('Cartão')
        TRANSFER = 'transfer', _('Transferência')
        OTHER = 'other', _('Outro')

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='payments', verbose_name=_('Aluno'))
    # What was paid for: the package sold to the student (the catalog package and its name stay for old payments)
    student_package = models.ForeignKey('StudentPackage', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='payments', verbose_name=_('Pacote'))
    plan = models.ForeignKey(Package, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments',
                             verbose_name=_('Pacote do catálogo'))
    plan_name = models.CharField(max_length=100, blank=True, verbose_name=_('Pacote'))
    amount = models.DecimalField(max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))],
                                 verbose_name=_('Valor'))
    paid_on = models.DateField(default=timezone.localdate, verbose_name=_('Data do pagamento'))
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.PIX, verbose_name=_('Forma'))
    period_start = models.DateField(null=True, blank=True, verbose_name=_('Período de'))   # old plan payments only
    period_end = models.DateField(null=True, blank=True, verbose_name=_('Período até'))
    notes = models.CharField(max_length=200, blank=True, verbose_name=_('Observação'))
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Pagamento')
        verbose_name_plural = _('Pagamentos')
        ordering = ['-paid_on', '-pk']

    def __str__(self):
        return f'{self.person_id} {self.paid_on:%d/%m/%Y} {self.amount}'


class Refund(models.Model):
    """Money that went back to the student (`refund`) or a payment entered wrongly that is undone (`reversal`).

    Payments are never edited or deleted to fix money: a refund is a new entry, linked to the payment and the package,
    and the totals are summed from the entries. A wrong refund is voided (kept, with the reason), never removed."""

    class Kind(models.TextChoices):
        REFUND = 'refund', _('Devolução')
        REVERSAL = 'reversal', _('Estorno (lançamento errado)')

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='refunds', verbose_name=_('Aluno'))
    student_package = models.ForeignKey('StudentPackage', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='refunds', verbose_name=_('Pacote'))
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name='refunds',
                                verbose_name=_('Pagamento'))
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.REFUND, verbose_name=_('Tipo'))
    amount = models.DecimalField(max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))],
                                 verbose_name=_('Valor'))
    refunded_on = models.DateField(default=timezone.localdate, verbose_name=_('Data'))
    method = models.CharField(max_length=10, choices=Payment.Method.choices, default=Payment.Method.TRANSFER,
                              verbose_name=_('Forma de devolução'))
    reason = models.CharField(max_length=200, blank=True, verbose_name=_('Motivo'))
    summary = models.TextField(blank=True, verbose_name=_('Resumo do cálculo'))   # what was paid, given, discounted
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = _('Devolução')
        verbose_name_plural = _('Devoluções')
        ordering = ['-refunded_on', '-pk']

    @property
    def is_voided(self):
        return self.voided_at is not None

    def __str__(self):
        return f'{self.person_id} {self.refunded_on:%d/%m/%Y} -{self.amount}'


class ScheduleSlot(models.Model):
    """Fixed weekly time of a student (e.g. Mondays 07:00)."""
    WEEKDAYS = [(0, _('Segunda')), (1, _('Terça')), (2, _('Quarta')), (3, _('Quinta')), (4, _('Sexta')), (5, _('Sábado')),
                (6, _('Domingo'))]

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='slots', verbose_name=_('Aluno'))
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS, verbose_name=_('Dia da semana'))
    start_time = models.TimeField(verbose_name=_('Horário'))
    duration_minutes = models.PositiveSmallIntegerField(
        default=60, validators=[MinValueValidator(10), MaxValueValidator(480)], verbose_name=_('Duração (min)'))
    is_active = models.BooleanField(default=True, verbose_name=_('Ativo'))
    valid_from = models.DateField(default=timezone.localdate, verbose_name=_('Vale a partir de'))
    valid_until = models.DateField(null=True, blank=True, verbose_name=_('Vale até'))
    # Repeats every N weeks, counted from the week of `valid_from` (1 = every week)
    interval_weeks = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(8)], verbose_name=_('Repete a cada (semanas)'))
    # The package this series fills (core/package_schedule.py): "mark more classes" and renewals follow it
    package = models.ForeignKey('StudentPackage', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='slots', verbose_name=_('Pacote'))
    # Days (ISO dates) with no class inside the series, e.g. a holiday: the calendar never generates them
    skipped_dates = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = _('Horário fixo')
        verbose_name_plural = _('Horários fixos')
        ordering = ['weekday', 'start_time']
        constraints = [models.UniqueConstraint(fields=['person', 'weekday', 'start_time'], name='unique_slot')]

    def __str__(self):
        return f'{self.get_weekday_display()} {self.start_time:%H:%M}'


class Appointment(models.Model):
    """One concrete class on the calendar (generated from a slot, or created by hand)."""

    class Kind(models.TextChoices):
        REGULAR = 'regular', _('Aula')
        TRIAL = 'trial', _('Experimental')
        MAKEUP = 'makeup', _('Reposição')
        OTHER = 'other', _('Outro')

    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', _('Marcada')
        ATTENDED = 'attended', _('Compareceu')
        MISSED = 'missed', _('Faltou')
        CANCELLED = 'cancelled', _('Cancelada')
        RESCHEDULED = 'rescheduled', _('Remarcada')

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='appointments', verbose_name=_('Aluno'))
    starts_at = models.DateTimeField(verbose_name=_('Início'))
    duration_minutes = models.PositiveSmallIntegerField(
        default=60, validators=[MinValueValidator(10), MaxValueValidator(480)], verbose_name=_('Duração (min)'))
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.REGULAR, verbose_name=_('Tipo'))
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED, verbose_name=_('Situação'))
    slot = models.ForeignKey(ScheduleSlot, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='appointments')
    notes = models.CharField(max_length=200, blank=True, verbose_name=_('Observação'))
    status_changed_at = models.DateTimeField(null=True, blank=True)
    # On the original class of a move: the class it became (lets "desfazer" put things back)
    # The package this class was taken from (linked by core/packages.sync once the class counts)
    package = models.ForeignKey('StudentPackage', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='appointments')
    cancelled_late = models.BooleanField(default=False)   # cancelled with less notice than allowed: the class counts
    counts_override = models.BooleanField(null=True, blank=True)   # trainer's choice: True/False overrides the rule
    reminder_sent_at = models.DateTimeField(null=True, blank=True)   # core/reminders.py: the class reminder went out
    rescheduled_to = models.OneToOneField('self', on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name='rescheduled_from')

    class Meta:
        verbose_name = _('Aula')
        verbose_name_plural = _('Aulas')
        ordering = ['starts_at']
        # One live class per student and time. Cancelled and rescheduled rows stay as history, so a class can be
        # moved back to a time it left, or a new one created where one was cancelled.
        constraints = [models.UniqueConstraint(
            fields=['person', 'starts_at'], condition=~models.Q(status__in=['cancelled', 'rescheduled']),
            name='unique_live_appointment')]

    @property
    def ends_at(self):
        return self.starts_at + timedelta(minutes=self.duration_minutes)

    @property
    def counts_against_package(self):
        """Does this class use up one class of the student's package? (see core/packages.py for the rules)"""
        if self.counts_override is not None:
            return self.counts_override
        if self.status in (self.Status.ATTENDED, self.Status.MISSED):
            return True
        return self.status == self.Status.CANCELLED and self.cancelled_late

    def __str__(self):
        return f'{self.person_id} {self.starts_at:%d/%m/%Y %H:%M} {self.status}'


class AppointmentRequest(models.Model):
    """A student asking to move or cancel one of their classes; the trainer approves or declines (core/class_requests.py)."""

    class Kind(models.TextChoices):
        RESCHEDULE = 'reschedule', _('Remarcar')
        CANCEL = 'cancel', _('Cancelar')

    class Status(models.TextChoices):
        PENDING = 'pending', _('Aguardando a personal')
        APPROVED = 'approved', _('Aprovado')
        DECLINED = 'declined', _('Recusado')
        WITHDRAWN = 'withdrawn', _('Retirado')

    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name='requests')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    proposed_starts_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Novo horário sugerido'))
    message = models.CharField(max_length=200, blank=True, verbose_name=_('Recado'))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [models.UniqueConstraint(
            fields=['appointment'], condition=models.Q(status='pending'), name='one_pending_request_per_appointment')]

    def __str__(self):
        return f'{self.appointment_id} {self.kind} {self.status}'


class NotificationPreference(models.Model):
    """Which channels a person (student or trainer) allows for notifications. Everything starts off; turning a channel
    on is the consent for it (`*_at` keeps when), turning it off withdraws it."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_preference')
    email_enabled = models.BooleanField(default=False)
    email_at = models.DateTimeField(null=True, blank=True)
    push_enabled = models.BooleanField(default=False)
    push_at = models.DateTimeField(null=True, blank=True)
    # What to be told about, once a channel is on (applies to both channels)
    notify_changes = models.BooleanField(default=True)          # class moved/cancelled, answers and requests
    notify_class_reminder = models.BooleanField(default=True)   # "your class is tomorrow"
    notify_plan_expiry = models.BooleanField(default=True)      # student: "your plan expires in 3 days", "2 classes left"
    notify_renewals = models.BooleanField(default=True)         # trainer: packages running out or expiring
    # Language the person uses the app in (set by the language switcher, or learned at login); the notifications, e-mails
    # and WhatsApp texts meant for them are written in it. Empty = the site default.
    language = models.CharField(max_length=10, blank=True, default='')


class WhatsAppTemplate(models.Model):
    """The trainer's own text for one of the WhatsApp messages (core/whatsapp_messages.py); no row = the default text."""

    class Kind(models.TextChoices):
        CHARGE = 'charge', _('Cobrança de pacote')
        MISSED = 'missed', _('Aula que o aluno faltou')
        INVITE = 'invite', _('Convite de acesso')

    kind = models.CharField(max_length=10, choices=Kind.choices, unique=True)
    text = models.TextField(max_length=1000)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    def __str__(self):
        return self.get_kind_display()


class Holiday(models.Model):
    """A day with no classes that the trainer added (a local holiday, a day the studio is closed). The national holidays
    are computed in core/holidays.py."""
    date = models.DateField(unique=True)
    name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f'{self.date:%d/%m/%Y} {self.name}'


class PushSubscription(models.Model):
    """One browser/phone that agreed to receive web push (the subscription the browser handed to the page)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.TextField(unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    device = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_success_at = models.DateTimeField(null=True, blank=True)


class NotificationLog(models.Model):
    """What was sent (or not) and how it went. Never holds the message text, only its kind."""

    class Status(models.TextChoices):
        SENT = 'sent', _('Enviada')
        FAILED = 'failed', _('Falhou')
        SKIPPED = 'skipped', _('Não enviada')

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notification_logs')
    kind = models.CharField(max_length=30)
    channel = models.CharField(max_length=10)
    status = models.CharField(max_length=10, choices=Status.choices)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']


class CalendarFeed(models.Model):
    """A private calendar link (iCalendar, .ics) for one account: the trainer's whole agenda, or a student's own classes.

    Whoever has the link can read the classes it shows, so it is treated like a password: long random token,
    one per account, and "generate a new link" invalidates the old one (see core/calendar_feed.py)."""
    NAME_STYLES = [('first', _('Primeiro nome do aluno')), ('full', _('Nome completo do aluno')),
                   ('none', _('Sem o nome do aluno'))]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='calendar_feed')
    token = models.CharField(max_length=64, unique=True, editable=False)
    # Trainer's feed only: how a student appears in the title of the event
    name_style = models.CharField(max_length=5, choices=NAME_STYLES, default='first', verbose_name=_('Nome do aluno no evento'))
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    last_fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Link de calendário')
        verbose_name_plural = _('Links de calendário')

    def __str__(self):
        return gettext('Calendário de %(user)s') % {'user': self.user_id}
