from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.utils import timezone


class MuscleGroup(models.Model):
    """Model representing a muscle group"""
    name = models.CharField(max_length=50, unique=True, verbose_name='Nome')
    slug = models.SlugField(max_length=50, unique=True, verbose_name='Slug')
    description = models.TextField(blank=True, verbose_name='Descrição')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    order = models.IntegerField(default=0, verbose_name='Ordem de Exibição')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Grupo Muscular'
        verbose_name_plural = 'Grupos Musculares'
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Person(models.Model):
    """Model representing a student/person in the gym"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='person')
    person_name = models.CharField(max_length=200, verbose_name='Nome')
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True, verbose_name='Foto de Perfil')
    address = models.CharField(max_length=300, blank=True, verbose_name='Endereço')
    city = models.CharField(max_length=100, blank=True, verbose_name='Cidade')
    state = models.CharField(max_length=2, blank=True, verbose_name='Estado')
    zip_code = models.CharField(max_length=10, blank=True, verbose_name='CEP')
    phone = models.CharField(max_length=20, blank=True, verbose_name='Telefone')
    birth_date = models.DateField(null=True, blank=True, verbose_name='Data de Nascimento')
    notes = models.TextField(blank=True, verbose_name='Observações')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Aluno'
        verbose_name_plural = 'Alunos'
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
        ('image', 'Imagem'),
        ('gif', 'GIF'),
        ('video', 'Vídeo'),
        ('youtube', 'YouTube'),
        ('url', 'Link Externo'),
    ]

    exercise_name = models.CharField(max_length=200, verbose_name='Nome do Exercício')
    description = models.TextField(blank=True, verbose_name='Descrição')
    muscle_group = models.ForeignKey(MuscleGroup, on_delete=models.PROTECT, related_name='exercises', verbose_name='Grupo Muscular')
    default_reps = models.IntegerField(default=12, validators=[MinValueValidator(1)], verbose_name='Repetições Padrão')
    default_sets = models.IntegerField(default=3, validators=[MinValueValidator(1)], verbose_name='Séries Padrão')

    # Media fields
    media_type = models.CharField(max_length=20, choices=MEDIA_TYPE_CHOICES, default='image', verbose_name='Tipo de Mídia')
    media_file = models.FileField(upload_to='exercises/', blank=True, null=True, verbose_name='Arquivo')
    media_url = models.URLField(blank=True, verbose_name='URL da Mídia')
    thumbnail = models.ImageField(upload_to='exercises/thumbnails/', blank=True, null=True, verbose_name='Miniatura')

    instructions = models.TextField(blank=True, verbose_name='Instruções')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Exercício'
        verbose_name_plural = 'Exercícios'
        ordering = ['exercise_name']

    def __str__(self):
        return f"{self.exercise_name} ({self.muscle_group.name})"


class Routine(models.Model):
    """Model representing a workout routine template"""
    routine_name = models.CharField(max_length=200, verbose_name='Nome do Treino')
    description = models.TextField(blank=True, verbose_name='Descrição')
    difficulty_level = models.CharField(
        max_length=20,
        choices=[
            ('iniciante', 'Iniciante'),
            ('intermediario', 'Intermediário'),
            ('avancado', 'Avançado'),
        ],
        default='intermediario',
        verbose_name='Nível de Dificuldade'
    )
    estimated_duration = models.IntegerField(
        default=60,
        validators=[MinValueValidator(1)],
        help_text='Duração estimada em minutos',
        verbose_name='Duração Estimada (min)'
    )
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Modelo de Treino'
        verbose_name_plural = 'Modelos de Treino'
        ordering = ['routine_name']

    def __str__(self):
        return self.routine_name


class RoutineExercise(models.Model):
    """Model linking exercises to routines with specific parameters"""
    routine = models.ForeignKey(Routine, on_delete=models.CASCADE, related_name='exercises', verbose_name='Treino')
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name='routines', verbose_name='Exercício')
    order = models.IntegerField(default=1, validators=[MinValueValidator(1)], verbose_name='Ordem')
    sets = models.IntegerField(default=3, validators=[MinValueValidator(1)], verbose_name='Séries')
    reps = models.IntegerField(default=12, validators=[MinValueValidator(1)], verbose_name='Repetições')
    rest_seconds = models.IntegerField(default=60, validators=[MinValueValidator(0)], verbose_name='Descanso (seg)')
    notes = models.TextField(blank=True, verbose_name='Observações')

    class Meta:
        verbose_name = 'Exercício do Treino'
        verbose_name_plural = 'Exercícios do Treino'
        ordering = ['routine', 'order']
        unique_together = ['routine', 'exercise', 'order']

    def __str__(self):
        return f"{self.routine.routine_name} - {self.exercise.exercise_name}"


class PersonRoutine(models.Model):
    """Model representing a personalized routine for a specific person (copy of template)"""
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='routines', verbose_name='Aluno')

    # Template reference (optional - for tracking origin)
    routine_template = models.ForeignKey(
        Routine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='person_routines',
        verbose_name='Template Original'
    )

    # Copied fields from template (editable per person)
    routine_name = models.CharField(max_length=200, default='Treino Personalizado', verbose_name='Nome do Treino')
    description = models.TextField(blank=True, default='', verbose_name='Descrição')
    difficulty_level = models.CharField(
        max_length=20,
        choices=[
            ('iniciante', 'Iniciante'),
            ('intermediario', 'Intermediário'),
            ('avancado', 'Avançado'),
        ],
        default='intermediario',
        verbose_name='Nível de Dificuldade'
    )
    estimated_duration = models.IntegerField(
        default=60,
        validators=[MinValueValidator(1)],
        help_text='Duração estimada em minutos',
        verbose_name='Duração Estimada (min)'
    )

    # Person-specific fields
    routine_date = models.DateField(default=timezone.now, verbose_name='Data de Início')
    start_date = models.DateField(null=True, blank=True, verbose_name='Data de Início Planejada')
    end_date = models.DateField(null=True, blank=True, verbose_name='Data de Término')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    notes = models.TextField(blank=True, verbose_name='Observações')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Treino do Aluno'
        verbose_name_plural = 'Treinos dos Alunos'
        ordering = ['-routine_date']

    def __str__(self):
        return f"{self.person.person_name} - {self.routine_name} ({self.routine_date})"


class PersonRoutineExercise(models.Model):
    """Model representing exercises in a person's routine (personalized copy)"""
    person_routine = models.ForeignKey(
        PersonRoutine,
        on_delete=models.CASCADE,
        related_name='exercises',
        verbose_name='Treino do Aluno'
    )
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, verbose_name='Exercício')

    # Exercise configuration (copied from template, editable per person)
    order = models.IntegerField(default=1, validators=[MinValueValidator(1)], verbose_name='Ordem')
    sets = models.IntegerField(default=3, validators=[MinValueValidator(1)], verbose_name='Séries')
    reps = models.IntegerField(default=12, validators=[MinValueValidator(1)], verbose_name='Repetições')
    rest_seconds = models.IntegerField(default=60, validators=[MinValueValidator(0)], verbose_name='Descanso (seg)')
    notes = models.TextField(blank=True, verbose_name='Observações')

    # Execution tracking
    sets_completed = models.IntegerField(default=0, validators=[MinValueValidator(0)], verbose_name='Séries Completadas')
    reps_completed = models.IntegerField(default=0, validators=[MinValueValidator(0)], verbose_name='Repetições Completadas')
    weight_used = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name='Carga (kg)'
    )
    is_completed = models.BooleanField(default=False, verbose_name='Concluído')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Completado em')

    class Meta:
        verbose_name = 'Exercício do Treino do Aluno'
        verbose_name_plural = 'Exercícios dos Treinos dos Alunos'
        ordering = ['person_routine', 'order']

    def __str__(self):
        return f"{self.person_routine} - {self.exercise.exercise_name}"

    def mark_completed(self):
        """Mark exercise as completed"""
        self.is_completed = True
        self.completed_at = timezone.now()
        self.save()


class WorkoutSession(models.Model):
    """Model tracking workout sessions with timer"""
    person_routine = models.ForeignKey(
        PersonRoutine,
        on_delete=models.CASCADE,
        related_name='sessions',
        verbose_name='Treino'
    )
    start_time = models.DateTimeField(auto_now_add=True, verbose_name='Início')
    end_time = models.DateTimeField(null=True, blank=True, verbose_name='Término')
    duration_minutes = models.IntegerField(null=True, blank=True, verbose_name='Duração (min)')
    is_completed = models.BooleanField(default=False, verbose_name='Concluído')
    notes = models.TextField(blank=True, verbose_name='Observações')

    class Meta:
        verbose_name = 'Sessão de Treino'
        verbose_name_plural = 'Sessões de Treino'
        ordering = ['-start_time']

    def __str__(self):
        return f"{self.person_routine.person.person_name} - {self.start_time.strftime('%d/%m/%Y %H:%M')}"

    def end_session(self):
        """End the workout session and calculate duration"""
        if not self.end_time:
            self.end_time = timezone.now()
            duration = self.end_time - self.start_time
            self.duration_minutes = int(duration.total_seconds() / 60)
            self.is_completed = True
            self.save()


class SessionExercise(models.Model):
    """Model tracking individual exercise execution within a session"""
    session = models.ForeignKey(
        WorkoutSession,
        on_delete=models.CASCADE,
        related_name='exercise_logs',
        verbose_name='Sessão'
    )
    person_routine_exercise = models.ForeignKey(
        PersonRoutineExercise,
        on_delete=models.CASCADE,
        related_name='session_logs',
        verbose_name='Exercício do Treino'
    )

    # Execution data for this specific session
    weight_used = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name='Carga (kg)'
    )
    is_completed = models.BooleanField(default=False, verbose_name='Concluído')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Completado em')
    notes = models.TextField(blank=True, verbose_name='Observações')

    class Meta:
        verbose_name = 'Execução de Exercício na Sessão'
        verbose_name_plural = 'Execuções de Exercícios nas Sessões'
        unique_together = ['session', 'person_routine_exercise']
        ordering = ['person_routine_exercise__order']

    def __str__(self):
        return f"{self.session} - {self.person_routine_exercise.exercise.exercise_name}"

    def mark_completed(self):
        """Mark exercise as completed in this session"""
        self.is_completed = True
        self.completed_at = timezone.now()
        self.save()
