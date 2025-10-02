from django import forms
from django.contrib.auth.models import User
from .models import Person, Exercise, Routine, RoutineExercise, PersonRoutine


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ['user', 'person_name', 'address', 'city', 'state', 'zip_code',
                  'phone', 'birth_date', 'notes', 'is_active']
        widgets = {
            'birth_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class ExerciseForm(forms.ModelForm):
    class Meta:
        model = Exercise
        fields = ['exercise_name', 'description', 'muscle_group', 'default_reps',
                  'default_sets', 'media_type', 'media_file', 'media_url',
                  'thumbnail', 'instructions', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'instructions': forms.Textarea(attrs={'rows': 4}),
        }


class RoutineForm(forms.ModelForm):
    class Meta:
        model = Routine
        fields = ['routine_name', 'description', 'difficulty_level',
                  'estimated_duration', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class RoutineExerciseForm(forms.ModelForm):
    class Meta:
        model = RoutineExercise
        fields = ['routine', 'exercise', 'order', 'sets', 'reps', 'rest_seconds', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 2}),
        }


class PersonRoutineForm(forms.ModelForm):
    class Meta:
        model = PersonRoutine
        fields = ['person', 'routine', 'routine_date', 'start_date', 'end_date',
                  'notes', 'is_active']
        widgets = {
            'routine_date': forms.DateInput(attrs={'type': 'date'}),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class AIWorkoutGenerationForm(forms.Form):
    description = forms.CharField(
        label='Descrição do Treino',
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Ex: Treino para hipertrofia de peito e tríceps, com foco em exercícios compostos'}),
        help_text='Descreva o tipo de treino que deseja gerar'
    )
    difficulty_level = forms.ChoiceField(
        label='Nível de Dificuldade',
        choices=[
            ('iniciante', 'Iniciante'),
            ('intermediario', 'Intermediário'),
            ('avancado', 'Avançado'),
        ],
        initial='intermediario'
    )
    estimated_duration = forms.IntegerField(
        label='Duração Estimada (minutos)',
        min_value=15,
        max_value=180,
        initial=60
    )
