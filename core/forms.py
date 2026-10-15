from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from .recurrence import MAX_INTERVAL, MAX_OCCURRENCES, MAX_SPAN_DAYS, end_date
from .validators import normalize_phone, normalize_state, normalize_zip_code, validate_birth_date, digits_only
from .models import (
    Appointment, Payment, Package, ScheduleSlot, StudentPackage,
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
                  'emergency_contact_phone', 'status']
        widgets = {
            'birth_date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'},
                format='%Y-%m-%d'
            ),
            'goal': forms.Textarea(attrs={'rows': 2, 'class': 'form-control',
                                          'placeholder': 'Ex.: hipertrofia, emagrecer 8 kg até dezembro...'}),
            'health_notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'data-no-draft': '',
                                                  'placeholder': 'Ex.: hérnia de disco L5, joelho direito operado em 2022, usa anticoncepcional...'}),
            'emergency_contact_name': forms.TextInput(attrs={'class': 'form-control'}),
            'emergency_contact_phone': forms.TextInput(attrs={
                'class': 'form-control', 'inputmode': 'tel', 'autocomplete': 'off', 'data-mask': 'phone'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'person_name': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '2'}),
            'zip_code': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'avatar': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        }
        labels = {
            'notes': 'Anotações internas (o aluno não vê)',
        }

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


class SaleForm(forms.Form):
    """"Vender pacote": which catalog package, the price agreed with this student (per class or total) and a note."""
    package = forms.ModelChoiceField(queryset=Package.objects.none(), label='Pacote', empty_label=None,
                                     widget=forms.RadioSelect)
    pricing_mode = forms.ChoiceField(choices=Package.PRICING, initial='per_session', label='Como o valor é informado',
                                     widget=forms.RadioSelect)
    price_per_session = forms.DecimalField(
        required=False, min_value=0, max_digits=8, decimal_places=2, label='Valor por aula',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'data-no-draft': ''}))
    price_total = forms.DecimalField(
        required=False, min_value=0, max_digits=8, decimal_places=2, label='Valor total',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'data-no-draft': ''}))
    notes = forms.CharField(required=False, max_length=200, label='Observação',
                            widget=forms.TextInput(attrs={'class': 'form-control'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['package'].queryset = Package.objects.filter(is_active=True).order_by('name')

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        field = 'price_per_session' if cleaned['pricing_mode'] == 'per_session' else 'price_total'
        if cleaned.get(field) is None:
            cleaned[field] = None   # nothing typed: the catalog price is used
        return cleaned


class StudentProfileForm(ContactValidationMixin, forms.ModelForm):
    """Form for students to edit their own profile (restricted fields)"""
    class Meta:
        model = Person
        fields = ['person_name', 'avatar', 'address', 'city', 'state', 'zip_code',
                  'phone', 'birth_date', 'motivation_enabled', 'weekly_goal']
        widgets = {
            'weekly_goal': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '7', 'inputmode': 'numeric'}),
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
    """One photo, usually for a position of an assessment; the date follows the assessment when there is one."""

    def __init__(self, *args, student=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assessment'].queryset = student.assessments.all() if student else Assessment.objects.none()
        self.fields['assessment'].empty_label = 'Foto avulsa (sem avaliação)'
        self.fields['assessment'].label_from_instance = lambda a: f'Avaliação de {a.date:%d/%m/%Y}'
        self.fields['date'].required = False
        self.fields['angle'].required = False

    def clean(self):
        data = super().clean()
        data['angle'] = data.get('angle') or ProgressPhoto.Angle.OTHER
        assessment = data.get('assessment')
        if assessment:
            data['date'] = assessment.date
        elif not data.get('date'):
            data['date'] = timezone.localdate()
        return data

    class Meta:
        model = ProgressPhoto
        fields = ['assessment', 'angle', 'date', 'image', 'caption']
        widgets = {
            'assessment': forms.Select(attrs={'class': 'form-select'}),
            'angle': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
            'image': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'caption': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Opcional'}),
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


class PackageForm(forms.ModelForm):
    """A package of the catalog: classes, deadline (counted from the first class) and the price, typed per class or total.

    "Sem prazo" (a checkbox that is not a model field) is saved as an empty deadline: the package never expires."""
    no_deadline = forms.BooleanField(required=False, label='Sem prazo (o pacote não vence)',
                                     widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.valid_days is None:
            self.initial['no_deadline'] = True

    class Meta:
        model = Package
        fields = ['name', 'description', 'sessions', 'valid_days', 'pricing_mode', 'price_per_session', 'price_total', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex.: Pacote 10 aulas'}),
            'description': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
            'sessions': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '200', 'inputmode': 'numeric'}),
            'valid_days': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '1000', 'inputmode': 'numeric'}),
            'pricing_mode': forms.RadioSelect,
            'price_per_session': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'inputmode': 'decimal'}),
            'price_total': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'inputmode': 'decimal'}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('no_deadline'):
            cleaned['valid_days'] = None
            self.errors.pop('valid_days', None)
        elif cleaned.get('valid_days') is None and 'valid_days' not in self.errors:
            self.add_error('valid_days', 'Informe o prazo em dias ou marque "Sem prazo".')
        if self.errors:
            return cleaned
        mode = cleaned.get('pricing_mode')
        typed = cleaned.get('price_per_session' if mode == 'per_session' else 'price_total')
        if typed is None:
            self.add_error('price_per_session' if mode == 'per_session' else 'price_total', 'Informe o valor.')
        return cleaned


class PaymentForm(forms.ModelForm):
    """A payment for one of the student's packages (the amount starts as what is still to be paid)."""

    class Meta:
        model = Payment
        fields = ['student_package', 'amount', 'paid_on', 'method', 'notes']
        widgets = {
            'student_package': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}),
            'paid_on': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
            'method': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.TextInput(attrs={'class': 'form-control'}),
        }
        labels = {'amount': 'Valor', 'student_package': 'Pacote'}

    def __init__(self, *args, person=None, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields['student_package']
        field.queryset = StudentPackage.objects.filter(person=person, cancelled_at__isnull=True) if person else StudentPackage.objects.none()
        field.required = False
        field.empty_label = '— sem pacote —'
        field.label_from_instance = lambda obj: f'{obj.name} ({obj.sold_on:%d/%m/%Y})'


class RecurrenceMixin(forms.Form):
    """Repeat every N weeks, from a start date, ending never / on a date / after N classes."""
    ENDS = (('never', 'Nunca'), ('on', 'Em uma data'), ('after', 'Após um número de aulas'))

    interval_weeks = forms.IntegerField(
        label='Repete a cada (semanas)', min_value=1, max_value=MAX_INTERVAL, initial=1, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': str(MAX_INTERVAL), 'inputmode': 'numeric'}))
    starts_on = forms.DateField(
        label='Começa em', required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    ends = forms.ChoiceField(label='Termina', choices=ENDS, initial='never', required=False, widget=forms.RadioSelect)
    ends_on = forms.DateField(
        label='Termina em', required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    ends_after = forms.IntegerField(
        label='Número de aulas', min_value=1, max_value=MAX_OCCURRENCES, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': str(MAX_OCCURRENCES), 'inputmode': 'numeric'}))

    def __init__(self, *args, start_hint=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_hint = start_hint   # the day the series starts when the form has no "starts on" of its own

    def clean_interval_weeks(self):
        return self.cleaned_data.get('interval_weeks') or 1

    def clean(self):
        cleaned = super().clean()
        ends = cleaned.get('ends') or 'never'
        cleaned['ends'] = ends
        start = cleaned.get('starts_on') or self.start_hint or timezone.localdate()
        if ends == 'on':
            if not cleaned.get('ends_on'):
                self.add_error('ends_on', 'Informe a data final.')
            elif cleaned['ends_on'] < start:
                self.add_error('ends_on', 'A data final não pode ser antes do início.')
            elif (cleaned['ends_on'] - start).days > MAX_SPAN_DAYS:
                self.add_error('ends_on', 'A série pode durar no máximo 3 anos. Use "Nunca" para uma série sem fim.')
        elif ends == 'after' and not cleaned.get('ends_after'):
            self.add_error('ends_after', 'Informe quantas aulas.')
        return cleaned

    def series_end(self, weekdays, start):
        """Last day of the series for the chosen ending (None = never)."""
        cleaned = self.cleaned_data
        return end_date(start, weekdays, cleaned['interval_weeks'], cleaned['ends'], cleaned.get('ends_on'),
                        cleaned.get('ends_after'))


class ScheduleSlotForm(RecurrenceMixin):
    """New fixed time(s) for a student: one or more weekdays, repeating every N weeks."""
    weekday = forms.TypedChoiceField(choices=ScheduleSlot.WEEKDAYS, coerce=int, required=False, label='Dia',
                                     widget=forms.Select(attrs={'class': 'form-select'}))
    weekdays = forms.TypedMultipleChoiceField(choices=ScheduleSlot.WEEKDAYS, coerce=int, required=False, label='Dias',
                                              widget=forms.CheckboxSelectMultiple)
    start_time = forms.TimeField(label='Horário',
                                 widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'))
    duration_minutes = forms.IntegerField(
        label='Duração (min)', min_value=10, max_value=480, initial=60,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '10', 'max': '480', 'step': '5'}))

    def clean(self):
        cleaned = super().clean()
        days = set(cleaned.get('weekdays') or [])
        if cleaned.get('weekday') not in (None, ''):
            days.add(cleaned['weekday'])
        if not days:
            self.add_error('weekdays', 'Escolha pelo menos um dia da semana.')
        cleaned['all_weekdays'] = sorted(days)
        return cleaned


class SlotEditForm(forms.Form):
    """Change a fixed time from a date on (core/recurrence.edit_series)."""
    weekday = forms.TypedChoiceField(choices=ScheduleSlot.WEEKDAYS, coerce=int, label='Dia da semana',
                                     widget=forms.Select(attrs={'class': 'form-select'}))
    start_time = forms.TimeField(label='Horário',
                                 widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'))
    duration_minutes = forms.IntegerField(
        label='Duração (min)', min_value=10, max_value=480,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '10', 'max': '480', 'step': '5'}))
    from_day = forms.DateField(label='A partir de', widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    valid_until = forms.DateField(required=False, label='Vale até (opcional)', widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))


class RepeatForm(RecurrenceMixin):
    """The "Repeat" part of a new class: only used when `enabled` is ticked."""
    enabled = forms.BooleanField(required=False, label='Repetir esta aula',
                                 widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
    weekdays = forms.TypedMultipleChoiceField(choices=ScheduleSlot.WEEKDAYS, coerce=int, required=False, label='Nos dias',
                                              widget=forms.CheckboxSelectMultiple)

    def clean(self):
        if not self.cleaned_data.get('enabled'):
            return self.cleaned_data   # nothing to validate: it is a single class
        cleaned = super().clean()
        if not cleaned.get('weekdays'):
            self.add_error('weekdays', 'Escolha pelo menos um dia da semana.')
        return cleaned


class ClassRequestForm(forms.Form):
    """The student's request to move or cancel one class (core/class_requests.py applies the rules)."""
    kind = forms.ChoiceField(
        choices=[('reschedule', 'Quero remarcar para outro dia/horário'), ('cancel', 'Não vou poder ir (cancelar esta aula)')],
        initial='reschedule', label='O que você precisa?', widget=forms.RadioSelect)
    date = forms.DateField(required=False, label='Dia que prefere', widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    time = forms.TimeField(required=False, label='Horário que prefere', widget=forms.TimeInput(
        attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'))
    message = forms.CharField(required=False, max_length=200, label='Recado para a personal (opcional)',
                              widget=forms.TextInput(attrs={'class': 'form-control', 'maxlength': 200}))

    def clean(self):
        data = super().clean()
        if data.get('kind') == 'reschedule':
            if not data.get('date'):
                self.add_error('date', 'Escolha o dia.')
            if not data.get('time'):
                self.add_error('time', 'Escolha o horário.')
        return data


class AppointmentForm(forms.Form):
    """A one-off class (trial, makeup...) or the new time of a rescheduled one."""
    person = forms.ModelChoiceField(
        queryset=Person.objects.none(), label='Aluno', widget=forms.Select(attrs={'class': 'form-select'}))
    date = forms.DateField(label='Data', widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    time = forms.TimeField(label='Horário', widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'))
    duration_minutes = forms.IntegerField(
        label='Duração (min)', min_value=10, max_value=480, initial=60,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '5'}))
    kind = forms.ChoiceField(choices=Appointment.Kind.choices, label='Tipo', initial=Appointment.Kind.REGULAR,
                             widget=forms.Select(attrs={'class': 'form-select'}))
    notes = forms.CharField(required=False, max_length=200, label='Observação',
                            widget=forms.TextInput(attrs={'class': 'form-control'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['person'].queryset = Person.objects.filter(is_active=True).order_by('person_name')
