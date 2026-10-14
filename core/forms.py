from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from .validators import normalize_phone, normalize_state, normalize_zip_code, validate_birth_date, digits_only
from .models import (
    Assessment, Exercise, Person, PersonRoutine, ProgressPhoto, Routine, RoutineExercise, StudentNote,
)


class ContactValidationMixin:
    """Same checks and normalization for every form that edits a student's contact data."""

    def clean_phone(self):
        return normalize_phone(self.cleaned_data.get('phone'))

    def clean_state(self):
        return normalize_state(self.cleaned_data.get('state'))

    def clean_zip_code(self):
        return normalize_zip_code(self.cleaned_data.get('zip_code'))

    def clean_birth_date(self):
        value = self.cleaned_data.get('birth_date')
        validate_birth_date(value)
        return value


class PersonForm(ContactValidationMixin, forms.ModelForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'email@exemplo.com'}),
        label='Email',
        max_length=150,
        help_text='Usado para o login do aluno. Depois de salvar, você recebe um link de convite para enviar a ele.'
    )
    confirm_duplicate = forms.BooleanField(
        required=False, label='Confirmo que é outra pessoa, pode cadastrar mesmo assim',
    )

    class Meta:
        model = Person
        fields = ['person_name', 'email', 'avatar', 'address', 'city', 'state', 'zip_code',
                  'phone', 'birth_date', 'notes', 'goal', 'health_notes', 'emergency_contact_name',
                  'emergency_contact_phone', 'plan_name', 'plan_expires_on', 'status']
        widgets = {
            'birth_date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'},
                format='%Y-%m-%d'
            ),
            'plan_expires_on': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'},
                format='%Y-%m-%d'
            ),
            'goal': forms.Textarea(attrs={'rows': 2, 'class': 'form-control',
                                          'placeholder': 'Ex.: hipertrofia, emagrecer 8 kg até dezembro...'}),
            'health_notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control',
                                                  'placeholder': 'Ex.: hérnia de disco L5, joelho direito operado em 2022, usa anticoncepcional...'}),
            'emergency_contact_name': forms.TextInput(attrs={'class': 'form-control'}),
            'emergency_contact_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'plan_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex.: Mensal 3x/semana'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'person_name': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '2'}),
            'zip_code': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'avatar': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        }
        labels = {'notes': 'Anotações internas (o aluno não vê)'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.duplicates = []
        if self.instance.pk:
            # Changing the lifecycle goes through "Alterar situação" (history + reason)
            del self.fields['status']
        else:
            self.fields['status'].choices = [
                (value, label) for value, label in Person.Status.choices if value in Person.OPEN_STATUSES]
            self.fields['status'].widget.attrs['class'] = 'form-select'
            self.fields['status'].required = False
        # Se estiver editando, preencher o email do usuário existente
        if self.instance and self.instance.pk and self.instance.user:
            self.fields['email'].initial = self.instance.user.email

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if len(email) > 150:
            raise forms.ValidationError('O e-mail pode ter no máximo 150 caracteres.')
        # Verificar se o email já está em uso (exceto pelo usuário atual em edição)
        user_exists = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
        if self.instance and self.instance.pk and self.instance.user:
            user_exists = user_exists.exclude(pk=self.instance.user.pk)

        if user_exists.exists():
            raise forms.ValidationError('Este email já está cadastrado no sistema.')

        return email

    def clean_status(self):
        return self.cleaned_data.get('status') or Person.Status.ACTIVE

    def clean_emergency_contact_phone(self):
        return normalize_phone(self.cleaned_data.get('emergency_contact_phone'))

    def _find_duplicates(self):
        """Other students with the same normalized phone or the same name."""
        name = (self.cleaned_data.get('person_name') or '').strip()
        phone = digits_only(self.cleaned_data.get('phone'))
        others = Person.objects.exclude(pk=self.instance.pk) if self.instance.pk else Person.objects.all()
        found = {}
        if name:
            for p in others.filter(person_name__iexact=name):
                found[p.pk] = (p, 'mesmo nome')
        if phone:
            for p in others.exclude(phone='').only('pk', 'person_name', 'phone'):
                if digits_only(p.phone) == phone:
                    found[p.pk] = (p, 'mesmo telefone')
        return list(found.values())

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        self.duplicates = self._find_duplicates()
        if self.duplicates and not cleaned.get('confirm_duplicate'):
            raise forms.ValidationError(
                'Já existe aluno com dados parecidos (veja abaixo). '
                'Se for outra pessoa, marque a confirmação e salve de novo.')
        return cleaned


class StudentProfileForm(ContactValidationMixin, forms.ModelForm):
    """Form for students to edit their own profile (restricted fields)"""
    class Meta:
        model = Person
        fields = ['person_name', 'avatar', 'address', 'city', 'state', 'zip_code',
                  'phone', 'birth_date']
        widgets = {
            'birth_date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'},
                format='%Y-%m-%d'
            ),
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


class AssessmentForm(forms.ModelForm):
    MEASURE_FIELDS = ['weight_kg', 'height_cm', 'body_fat_pct', 'chest_cm', 'waist_cm', 'hip_cm', 'arm_cm', 'thigh_cm']

    class Meta:
        model = Assessment
        fields = ['date'] + ['weight_kg', 'height_cm', 'body_fat_pct', 'chest_cm', 'waist_cm', 'hip_cm',
                             'arm_cm', 'thigh_cm'] + ['notes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            **{name: forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1', 'inputmode': 'decimal'})
               for name in ['weight_kg', 'height_cm', 'body_fat_pct', 'chest_cm', 'waist_cm', 'hip_cm', 'arm_cm', 'thigh_cm']},
        }

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(name) is not None for name in self.MEASURE_FIELDS):
            raise forms.ValidationError('Preencha pelo menos uma medida (peso, altura, % de gordura ou circunferência).')
        return cleaned


class ProgressPhotoForm(forms.ModelForm):
    class Meta:
        model = ProgressPhoto
        fields = ['date', 'image', 'caption']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
            'image': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'caption': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex.: frente, perfil, costas'}),
        }


class StudentNoteForm(forms.ModelForm):
    class Meta:
        model = StudentNote
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={'rows': 3, 'class': 'form-control',
                                          'placeholder': 'Anotação da personal (o aluno não vê)...'}),
        }
        labels = {'text': ''}


class StatusForm(forms.Form):
    """Lifecycle change of a student, with the reason."""
    status = forms.ChoiceField(
        choices=Person.Status.choices, label='Nova situação',
        widget=forms.Select(attrs={'class': 'form-select'}))
    reason = forms.CharField(
        required=False, label='Motivo', max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control',
                                     'placeholder': 'Ex.: viagem de 2 meses, mudou de cidade, fechou o plano...'}))

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get('status')
        if status and status not in Person.OPEN_STATUSES and not cleaned.get('reason', '').strip():
            self.add_error('reason', 'Informe o motivo ao pausar ou encerrar o aluno.')
        return cleaned


class ConsentForm(forms.Form):
    """First-access consent screen: the two required consents plus the optional photos one."""
    terms = forms.BooleanField(label='', error_messages={'required': 'É preciso aceitar para continuar.'})
    health_data = forms.BooleanField(label='', error_messages={'required': 'É preciso aceitar para continuar.'})
    photos = forms.BooleanField(required=False, label='')
