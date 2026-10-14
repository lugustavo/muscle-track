from django.contrib import admin
from .models import (
    MuscleGroup, Person, Exercise, Routine, RoutineExercise,
    PersonRoutine, PersonRoutineExercise, WorkoutSession, SessionExercise, SessionSet,
    Assessment, ProgressPhoto, StudentNote,
)


class RoutineExerciseInline(admin.TabularInline):
    model = RoutineExercise
    extra = 1
    fields = ['exercise', 'order', 'sets', 'reps', 'rest_seconds', 'notes']


class PersonRoutineExerciseInline(admin.TabularInline):
    model = PersonRoutineExercise
    extra = 0
    fields = ['exercise', 'order', 'sets', 'reps', 'rest_seconds', 'notes']


@admin.register(MuscleGroup)
class MuscleGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'order', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ['order', 'is_active']
    ordering = ['order', 'name']


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ['person_name', 'phone', 'city', 'is_active', 'created_at']
    list_filter = ['is_active', 'city', 'state']
    search_fields = ['person_name', 'phone', 'user__email']
    fieldsets = [
        ('Informações Básicas', {
            'fields': ['user', 'person_name', 'birth_date', 'phone']
        }),
        ('Endereço', {
            'fields': ['address', 'city', 'state', 'zip_code'],
            'classes': ['collapse']
        }),
        ('Outras Informações', {
            'fields': ['notes', 'is_active']
        }),
    ]


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ['exercise_name', 'muscle_group', 'default_sets', 'default_reps', 'media_type', 'is_active']
    list_filter = ['muscle_group', 'media_type', 'is_active']
    search_fields = ['exercise_name', 'description']
    fieldsets = [
        ('Informações Básicas', {
            'fields': ['exercise_name', 'muscle_group', 'description']
        }),
        ('Configurações Padrão', {
            'fields': ['default_sets', 'default_reps']
        }),
        ('Mídia', {
            'fields': ['media_type', 'media_file', 'media_url', 'thumbnail']
        }),
        ('Instruções', {
            'fields': ['instructions', 'is_active']
        }),
    ]


@admin.register(Routine)
class RoutineAdmin(admin.ModelAdmin):
    list_display = ['routine_name', 'difficulty_level', 'estimated_duration', 'is_active', 'created_at']
    list_filter = ['difficulty_level', 'is_active']
    search_fields = ['routine_name', 'description']
    inlines = [RoutineExerciseInline]


@admin.register(RoutineExercise)
class RoutineExerciseAdmin(admin.ModelAdmin):
    list_display = ['routine', 'exercise', 'order', 'sets', 'reps', 'rest_seconds']
    list_filter = ['routine']
    search_fields = ['routine__routine_name', 'exercise__exercise_name']


@admin.register(PersonRoutine)
class PersonRoutineAdmin(admin.ModelAdmin):
    list_display = ['person', 'routine_name', 'difficulty_level', 'routine_date', 'is_active']
    list_filter = ['is_active', 'routine_date', 'difficulty_level']
    search_fields = ['person__person_name', 'routine_name', 'routine_template__routine_name']
    date_hierarchy = 'routine_date'
    inlines = [PersonRoutineExerciseInline]
    fieldsets = [
        ('Informações Básicas', {
            'fields': ['person', 'routine_template', 'routine_name', 'description', 'difficulty_level', 'estimated_duration']
        }),
        ('Datas', {
            'fields': ['routine_date', 'start_date', 'end_date', 'is_active']
        }),
        ('Observações', {
            'fields': ['notes'],
            'classes': ['collapse']
        }),
    ]


@admin.register(PersonRoutineExercise)
class PersonRoutineExerciseAdmin(admin.ModelAdmin):
    list_display = ['person_routine', 'exercise', 'order', 'sets', 'reps', 'rest_seconds']
    list_filter = ['exercise']
    search_fields = ['person_routine__person__person_name', 'exercise__exercise_name']
    ordering = ['person_routine', 'order']


class SessionExerciseInline(admin.TabularInline):
    model = SessionExercise
    extra = 0
    fields = ['person_routine_exercise', 'weight_used', 'is_completed', 'completed_at']
    readonly_fields = ['completed_at']


class SessionSetInline(admin.TabularInline):
    model = SessionSet
    extra = 0
    fields = ['number', 'weight', 'reps', 'is_done', 'completed_at']
    readonly_fields = ['completed_at']


@admin.register(WorkoutSession)
class WorkoutSessionAdmin(admin.ModelAdmin):
    list_display = ['person_routine', 'start_time', 'end_time', 'duration_minutes', 'is_completed']
    list_filter = ['is_completed', 'start_time']
    search_fields = ['person_routine__person__person_name']
    date_hierarchy = 'start_time'
    readonly_fields = ['start_time', 'paused_at', 'paused_seconds']
    inlines = [SessionExerciseInline]


@admin.register(SessionExercise)
class SessionExerciseAdmin(admin.ModelAdmin):
    list_display = ['session', 'person_routine_exercise', 'weight_used', 'is_completed', 'completed_at']
    list_filter = ['is_completed', 'session__start_time']
    search_fields = ['session__person_routine__person__person_name', 'person_routine_exercise__exercise__exercise_name']
    readonly_fields = ['completed_at']
    ordering = ['session', 'person_routine_exercise__order']
    inlines = [SessionSetInline]


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ['person', 'date', 'weight_kg', 'body_fat_pct', 'waist_cm']
    list_filter = ['date']
    search_fields = ['person__person_name']


@admin.register(ProgressPhoto)
class ProgressPhotoAdmin(admin.ModelAdmin):
    list_display = ['person', 'date', 'caption']
    search_fields = ['person__person_name']


@admin.register(StudentNote)
class StudentNoteAdmin(admin.ModelAdmin):
    list_display = ['person', 'author', 'created_at']
    search_fields = ['person__person_name', 'text']
