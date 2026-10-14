from django import forms
from django.contrib.auth.models import User
from .models import Person, Exercise, Routine, RoutineExercise, PersonRoutine


class PersonForm(forms.ModelForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'email@exemplo.com'}),
        label='Email',
        help_text='O aluno receberá um email de confirmação neste endereço'
    )

    class Meta:
        model = Person
        fields = ['person_name', 'email', 'avatar', 'address', 'city', 'state', 'zip_code',
                  'phone', 'birth_date', 'notes', 'is_active']
        widgets = {
            'birth_date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'},
                format='%Y-%m-%d'
            ),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'person_name': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '2'}),
            'zip_code': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'avatar': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Se estiver editando, preencher o email do usuário existente
        if self.instance and self.instance.pk and self.instance.user:
            self.fields['email'].initial = self.instance.user.email

    def clean_email(self):
        email = self.cleaned_data.get('email')
        # Verificar se o email já está em uso (exceto pelo usuário atual em edição)
        user_exists = User.objects.filter(email=email)
        if self.instance and self.instance.pk and self.instance.user:
            user_exists = user_exists.exclude(pk=self.instance.user.pk)

        if user_exists.exists():
            raise forms.ValidationError('Este email já está cadastrado no sistema.')

        return email


class StudentProfileForm(forms.ModelForm):
    """Form for students to edit their own profile (restricted fields)"""
    class Meta:
        model = Person
        fields = ['person_name', 'avatar', 'address', 'city', 'state', 'zip_code',
                  'phone', 'birth_date', 'notes']
        widgets = {
            'birth_date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'},
                format='%Y-%m-%d'
            ),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'placeholder': 'Observações pessoais...'}),
            'person_name': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '2'}),
            'zip_code': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'avatar': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
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
        fields = ['person', 'routine_name', 'description', 'difficulty_level',
                  'estimated_duration', 'routine_date', 'start_date', 'end_date',
                  'notes', 'is_active']
        widgets = {
            'routine_date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'},
                format='%Y-%m-%d'
            ),
            'start_date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'},
                format='%Y-%m-%d'
            ),
            'end_date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'},
                format='%Y-%m-%d'
            ),
            'description': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'routine_name': forms.TextInput(attrs={'class': 'form-control'}),
            'difficulty_level': forms.Select(attrs={'class': 'form-select'}),
            'estimated_duration': forms.NumberInput(attrs={'class': 'form-control'}),
            'person': forms.Select(attrs={'class': 'form-select'}),
        }


class AIWorkoutGenerationForm(forms.Form):
    description = forms.CharField(
        label='Descrição do Treino',
        max_length=1000,
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
