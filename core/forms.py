from decimal import Decimal

from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _, ngettext
from .recurrence import MAX_INTERVAL, MAX_OCCURRENCES, MAX_SPAN_DAYS, end_date
from .validators import normalize_phone, normalize_state, normalize_zip_code, validate_birth_date, digits_only
from .models import (
    Appointment, Holiday, Payment, Package, Refund, ScheduleSlot, StudentPackage,
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
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': _('email@exemplo.com')}),
        label=_('Email'),
        max_length=150,
        help_text=_('Usado para o login do aluno. Depois de salvar, você recebe um link de convite para enviar a ele.')
    )
    confirm_duplicate = forms.BooleanField(
        required=False, label=_('Confirmo que é outra pessoa, pode cadastrar mesmo assim'),
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
                                          'placeholder': _('Ex.: hipertrofia, emagrecer 8 kg até dezembro...')}),
            'health_notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'data-no-draft': '',
                                                  'placeholder': _('Ex.: hérnia de disco L5, joelho direito operado em 2022, usa anticoncepcional...')}),
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
            'notes': _('Anotações internas (o aluno não vê)'),
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
            raise forms.ValidationError(gettext('O e-mail pode ter no máximo 150 caracteres.'))
        # Verificar se o email já está em uso (exceto pelo usuário atual em edição)
        user_exists = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
        if self.instance and self.instance.pk and self.instance.user:
            user_exists = user_exists.exclude(pk=self.instance.user.pk)

        if user_exists.exists():
            raise forms.ValidationError(gettext('Este email já está cadastrado no sistema.'))

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
                found[p.pk] = (p, gettext('mesmo nome'))
        if phone:
            for p in others.exclude(phone='').only('pk', 'person_name', 'phone'):
                if digits_only(p.phone) == phone:
                    found[p.pk] = (p, gettext('mesmo telefone'))
        return list(found.values())

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        self.duplicates = self._find_duplicates()
        if self.duplicates and not cleaned.get('confirm_duplicate'):
            raise forms.ValidationError(gettext(
                'Já existe aluno com dados parecidos (veja abaixo). '
                'Se for outra pessoa, marque a confirmação e salve de novo.'))
        return cleaned


class SaleForm(forms.Form):
    """"Vender pacote": which catalog package, the price agreed with this student (per class or total) and a note."""
    package = forms.ModelChoiceField(queryset=Package.objects.none(), label=_('Pacote'), empty_label=None,
                                     widget=forms.RadioSelect)
    pricing_mode = forms.ChoiceField(choices=Package.PRICING, initial='per_session', label=_('Como o valor é informado'),
                                     widget=forms.RadioSelect)
    price_per_session = forms.DecimalField(
        required=False, min_value=0, max_digits=8, decimal_places=2, label=_('Valor por aula'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'data-no-draft': ''}))
    price_total = forms.DecimalField(
        required=False, min_value=0, max_digits=8, decimal_places=2, label=_('Valor total'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'data-no-draft': ''}))
    notes = forms.CharField(required=False, max_length=200, label=_('Observação'),
                            widget=forms.TextInput(attrs={'class': 'form-control'}))
    continue_schedule = forms.BooleanField(required=False, label=_('Continuar o mesmo horário'),
                                          widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

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
        label=_('Descrição do Treino'),
        max_length=1000,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': _('Ex: Treino para hipertrofia de peito e tríceps, com foco em exercícios compostos')}),
        help_text=_('Descreva o tipo de treino que deseja gerar')
    )
    difficulty_level = forms.ChoiceField(
        label=_('Nível de Dificuldade'),
        choices=[
            ('iniciante', _('Iniciante')),
            ('intermediario', _('Intermediário')),
            ('avancado', _('Avançado')),
        ],
        initial='intermediario'
    )
    estimated_duration = forms.IntegerField(
        label=_('Duração Estimada (minutos)'),
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
            raise forms.ValidationError(gettext('Preencha pelo menos uma medida (peso, altura, % de gordura ou circunferência).'))
        return cleaned


class ProgressPhotoForm(forms.ModelForm):
    """One photo, usually for a position of an assessment; the date follows the assessment when there is one."""

    def __init__(self, *args, student=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assessment'].queryset = student.assessments.all() if student else Assessment.objects.none()
        self.fields['assessment'].empty_label = gettext('Foto avulsa (sem avaliação)')
        self.fields['assessment'].label_from_instance = lambda a: gettext('Avaliação de %(date)s') % {'date': f'{a.date:%d/%m/%Y}'}
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
            'caption': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Opcional')}),
        }


class MyPhotoForm(forms.ModelForm):
    """A photo the student sends herself: always a loose one (no assessment), with the position and the day."""
    MAX_PHOTOS = 60   # per student: keeps the disk safe from a runaway upload loop

    def __init__(self, *args, person=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.person = person
        self.fields['date'].required = False
        self.fields['angle'].required = False
        self.fields['angle'].choices = [(a.value, a.label) for a in ProgressPhoto.SEQUENCE] + [('other', _('Outra'))]

    def clean(self):
        data = super().clean()
        data['angle'] = data.get('angle') or ProgressPhoto.Angle.OTHER
        today = timezone.localdate()
        if not data.get('date'):
            data['date'] = today
        elif data['date'] > today:
            self.add_error('date', gettext('A data não pode ser no futuro.'))
        if self.person is not None and self.person.progress_photos.filter(uploaded_by_student=True).count() >= self.MAX_PHOTOS:
            raise forms.ValidationError(
                gettext('Você já enviou %(max)d fotos: remova algumas para enviar outras.') % {'max': self.MAX_PHOTOS})
        return data

    class Meta:
        model = ProgressPhoto
        fields = ['angle', 'date', 'image', 'caption']
        widgets = {
            'angle': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
            'image': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'caption': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Opcional')}),
        }


class StudentNoteForm(forms.ModelForm):
    class Meta:
        model = StudentNote
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={'rows': 3, 'class': 'form-control',
                                          'placeholder': _('Anotação da personal (o aluno não vê)...')}),
        }
        labels = {'text': ''}


class StatusForm(forms.Form):
    """Lifecycle change of a student, with the reason."""
    status = forms.ChoiceField(
        choices=Person.Status.choices, label=_('Nova situação'),
        widget=forms.Select(attrs={'class': 'form-select'}))
    reason = forms.CharField(
        required=False, label=_('Motivo'), max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control',
                                     'placeholder': _('Ex.: viagem de 2 meses, mudou de cidade, fechou o plano...')}))

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get('status')
        if status and status not in Person.OPEN_STATUSES and not cleaned.get('reason', '').strip():
            self.add_error('reason', gettext('Informe o motivo ao pausar ou encerrar o aluno.'))
        return cleaned


class ConsentForm(forms.Form):
    """First-access consent screen: the two required consents plus the optional photos one."""
    terms = forms.BooleanField(label='', error_messages={'required': _('É preciso aceitar para continuar.')})
    health_data = forms.BooleanField(label='', error_messages={'required': _('É preciso aceitar para continuar.')})
    photos = forms.BooleanField(required=False, label='')


class PackageForm(forms.ModelForm):
    """A package of the catalog: classes, deadline (counted from the first class) and the price, typed per class or total.

    "Sem prazo" (a checkbox that is not a model field) is saved as an empty deadline: the package never expires."""
    no_deadline = forms.BooleanField(required=False, label=_('Sem prazo (o pacote não vence)'),
                                     widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.valid_days is None:
            self.initial['no_deadline'] = True

    class Meta:
        model = Package
        fields = ['name', 'description', 'sessions', 'valid_days', 'pricing_mode', 'price_per_session', 'price_total', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Ex.: Pacote 10 aulas')}),
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
            self.add_error('valid_days', gettext('Informe o prazo em dias ou marque "Sem prazo".'))
        if self.errors:
            return cleaned
        mode = cleaned.get('pricing_mode')
        typed = cleaned.get('price_per_session' if mode == 'per_session' else 'price_total')
        if typed is None:
            self.add_error('price_per_session' if mode == 'per_session' else 'price_total', gettext('Informe o valor.'))
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
        labels = {'amount': _('Valor'), 'student_package': _('Pacote')}

    def __init__(self, *args, person=None, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields['student_package']
        field.queryset = StudentPackage.objects.filter(person=person, cancelled_at__isnull=True) if person else StudentPackage.objects.none()
        field.required = False
        field.empty_label = gettext('— sem pacote —')
        field.label_from_instance = lambda obj: f'{obj.name} ({obj.sold_on:%d/%m/%Y})'


class RecurrenceMixin(forms.Form):
    """Repeat every N weeks, from a start date, ending never / on a date / after N classes."""
    ENDS = (('never', _('Nunca')), ('on', _('Em uma data')), ('after', _('Após um número de aulas')))

    interval_weeks = forms.IntegerField(
        label=_('Repete a cada (semanas)'), min_value=1, max_value=MAX_INTERVAL, initial=1, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': str(MAX_INTERVAL), 'inputmode': 'numeric'}))
    starts_on = forms.DateField(
        label=_('Começa em'), required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    ends = forms.ChoiceField(label=_('Termina'), choices=ENDS, initial='never', required=False, widget=forms.RadioSelect)
    ends_on = forms.DateField(
        label=_('Termina em'), required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    ends_after = forms.IntegerField(
        label=_('Número de aulas'), min_value=1, max_value=MAX_OCCURRENCES, required=False,
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
                self.add_error('ends_on', gettext('Informe a data final.'))
            elif cleaned['ends_on'] < start:
                self.add_error('ends_on', gettext('A data final não pode ser antes do início.'))
            elif (cleaned['ends_on'] - start).days > MAX_SPAN_DAYS:
                self.add_error('ends_on', gettext('A série pode durar no máximo 3 anos. Use "Nunca" para uma série sem fim.'))
        elif ends == 'after' and not cleaned.get('ends_after'):
            self.add_error('ends_after', gettext('Informe quantas aulas.'))
        return cleaned

    def series_end(self, weekdays, start):
        """Last day of the series for the chosen ending (None = never)."""
        cleaned = self.cleaned_data
        return end_date(start, weekdays, cleaned['interval_weeks'], cleaned['ends'], cleaned.get('ends_on'),
                        cleaned.get('ends_after'))


class ScheduleSlotForm(RecurrenceMixin):
    """New fixed time(s) for a student: one or more weekdays, repeating every N weeks.

    With `plan` (packages.schedule_plan) the series can follow the student's package: it ends on the package's last
    day or after the classes still to be marked, whichever comes first."""
    weekday = forms.TypedChoiceField(choices=ScheduleSlot.WEEKDAYS, coerce=int, required=False, label=_('Dia'),
                                     widget=forms.Select(attrs={'class': 'form-select'}))
    weekdays = forms.TypedMultipleChoiceField(choices=ScheduleSlot.WEEKDAYS, coerce=int, required=False, label=_('Dias'),
                                              widget=forms.CheckboxSelectMultiple)
    start_time = forms.TimeField(label=_('Horário'),
                                 widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'))
    duration_minutes = forms.IntegerField(
        label=_('Duração (min)'), min_value=10, max_value=480, initial=60,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '10', 'max': '480', 'step': '5'}))

    def __init__(self, *args, plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan = plan
        if plan is not None:
            parts = ngettext('%(n)d aula a marcar', '%(n)d aulas a marcar', plan['to_schedule']) % {'n': plan['to_schedule']}
            if plan['ends_on']:
                parts = gettext('até %(date)s ou %(classes)s') % {'date': f"{plan['ends_on']:%d/%m/%Y}", 'classes': parts}
            label = gettext('Acompanhar o pacote (%(parts)s, o que vier primeiro)') % {'parts': parts}
            self.fields['ends'].choices = (('package', label),) + self.ENDS
            if not self.is_bound:
                self.initial['ends'] = 'package'

    def clean(self):
        cleaned = super().clean()
        days = set(cleaned.get('weekdays') or [])
        if cleaned.get('weekday') not in (None, ''):
            days.add(cleaned['weekday'])
        if not days:
            self.add_error('weekdays', gettext('Escolha pelo menos um dia da semana.'))
        cleaned['all_weekdays'] = sorted(days)
        if cleaned.get('ends') == 'package':
            start = cleaned.get('starts_on') or timezone.localdate()
            if self.plan is None:
                self.add_error('ends', gettext('Este aluno não tem pacote em uso: escolha outra forma de terminar.'))
            elif self.plan['to_schedule'] <= 0:
                self.add_error('ends', gettext('Todas as aulas que restam do pacote já estão marcadas.'))
            elif self.plan['ends_on'] and self.plan['ends_on'] < start:
                self.add_error('ends', gettext('O pacote termina antes do início desta série.'))
        return cleaned

    def series_end(self, weekdays, start):
        if self.cleaned_data['ends'] != 'package':
            return super().series_end(weekdays, start)
        last = end_date(start, weekdays, self.cleaned_data['interval_weeks'], 'after',
                        ends_after=self.plan['to_schedule'])
        ends_on = self.plan['ends_on']
        return min(last, ends_on) if last and ends_on else (last or ends_on)


class SlotFillForm(forms.Form):
    """Fill the agenda from the student's package: weekdays, first day and time (the end date is calculated)."""
    weekdays = forms.TypedMultipleChoiceField(choices=ScheduleSlot.WEEKDAYS, coerce=int, required=True, label=_('Dias da semana'),
                                              widget=forms.CheckboxSelectMultiple,
                                              error_messages={'required': _('Escolha pelo menos um dia da semana.')})
    starts_on = forms.DateField(label=_('Primeira aula'), required=False,
                                widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    start_time = forms.TimeField(label=_('Horário'),
                                 widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'))
    duration_minutes = forms.IntegerField(
        label=_('Duração (min)'), min_value=10, max_value=480, initial=60, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '10', 'max': '480', 'step': '5'}))
    interval_weeks = forms.IntegerField(
        label=_('Repete a cada (semanas)'), min_value=1, max_value=MAX_INTERVAL, initial=1, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': str(MAX_INTERVAL)}))

    def clean_duration_minutes(self):
        return self.cleaned_data.get('duration_minutes') or 60

    def clean_interval_weeks(self):
        return self.cleaned_data.get('interval_weeks') or 1

    def clean_starts_on(self):
        return self.cleaned_data.get('starts_on') or timezone.localdate()


class SlotEditForm(forms.Form):
    """Change a fixed time from a date on (core/recurrence.edit_series)."""
    weekday = forms.TypedChoiceField(choices=ScheduleSlot.WEEKDAYS, coerce=int, label=_('Dia da semana'),
                                     widget=forms.Select(attrs={'class': 'form-select'}))
    start_time = forms.TimeField(label=_('Horário'),
                                 widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'))
    duration_minutes = forms.IntegerField(
        label=_('Duração (min)'), min_value=10, max_value=480,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '10', 'max': '480', 'step': '5'}))
    from_day = forms.DateField(label=_('A partir de'), widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    valid_until = forms.DateField(required=False, label=_('Vale até (opcional)'), widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))


class RepeatForm(RecurrenceMixin):
    """The "Repeat" part of a new class: only used when `enabled` is ticked."""
    enabled = forms.BooleanField(required=False, label=_('Repetir esta aula'),
                                 widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
    weekdays = forms.TypedMultipleChoiceField(choices=ScheduleSlot.WEEKDAYS, coerce=int, required=False, label=_('Nos dias'),
                                              widget=forms.CheckboxSelectMultiple)

    def clean(self):
        if not self.cleaned_data.get('enabled'):
            return self.cleaned_data   # nothing to validate: it is a single class
        cleaned = super().clean()
        if not cleaned.get('weekdays'):
            self.add_error('weekdays', gettext('Escolha pelo menos um dia da semana.'))
        return cleaned


class ClassRequestForm(forms.Form):
    """The student's request to move or cancel one class (core/class_requests.py applies the rules)."""
    kind = forms.ChoiceField(
        choices=[('reschedule', _('Quero remarcar para outro dia/horário')), ('cancel', _('Não vou poder ir (cancelar esta aula)'))],
        initial='reschedule', label=_('O que você precisa?'), widget=forms.RadioSelect)
    date = forms.DateField(required=False, label=_('Dia que prefere'), widget=forms.DateInput(
        attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    time = forms.TimeField(required=False, label=_('Horário que prefere'), widget=forms.TimeInput(
        attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'))
    message = forms.CharField(required=False, max_length=200, label=_('Recado para a personal (opcional)'),
                              widget=forms.TextInput(attrs={'class': 'form-control', 'maxlength': 200}))

    def clean(self):
        data = super().clean()
        if data.get('kind') == 'reschedule':
            if not data.get('date'):
                self.add_error('date', gettext('Escolha o dia.'))
            if not data.get('time'):
                self.add_error('time', gettext('Escolha o horário.'))
        return data


class AppointmentForm(forms.Form):
    """A one-off class (trial, makeup...) or the new time of a rescheduled one."""
    person = forms.ModelChoiceField(
        queryset=Person.objects.none(), label=_('Aluno'), widget=forms.Select(attrs={'class': 'form-select'}))
    date = forms.DateField(label=_('Data'), widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    time = forms.TimeField(label=_('Horário'), widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'))
    duration_minutes = forms.IntegerField(
        label=_('Duração (min)'), min_value=10, max_value=480, initial=60,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '5'}))
    kind = forms.ChoiceField(choices=Appointment.Kind.choices, label=_('Tipo'), initial=Appointment.Kind.REGULAR,
                             widget=forms.Select(attrs={'class': 'form-select'}))
    notes = forms.CharField(required=False, max_length=200, label=_('Observação'),
                            widget=forms.TextInput(attrs={'class': 'form-control'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['person'].queryset = Person.objects.filter(is_active=True).order_by('person_name')


class HolidayForm(forms.ModelForm):
    """A day with no classes the trainer adds (local holiday, studio closed)."""
    class Meta:
        model = Holiday
        fields = ['date', 'name']
        labels = {'date': _('Dia'), 'name': _('Nome')}
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
            'name': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '80', 'placeholder': _('Ex.: São João, estúdio fechado')}),
        }
        error_messages = {'date': {'unique': _('Este dia já está na lista.')}}


MONEY_ATTRS = {'class': 'form-control', 'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'data-no-draft': ''}


class PackageEditForm(forms.Form):
    """Change a package already sold to a student (the numbers of the sale, not the catalog)."""
    name = forms.CharField(max_length=100, label=_('Nome'), widget=forms.TextInput(attrs={'class': 'form-control'}))
    sessions = forms.IntegerField(min_value=1, max_value=200, label=_('Aulas'),
                                  widget=forms.NumberInput(attrs={'class': 'form-control', 'inputmode': 'numeric'}))
    valid_days = forms.IntegerField(required=False, min_value=1, max_value=1000, label=_('Prazo (dias)'),
                                    widget=forms.NumberInput(attrs={'class': 'form-control', 'inputmode': 'numeric'}))
    no_deadline = forms.BooleanField(required=False, label=_('Sem prazo (o pacote não vence)'),
                                     widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
    pricing_mode = forms.ChoiceField(choices=Package.PRICING, initial='per_session', label=_('Como o valor é informado'),
                                     widget=forms.RadioSelect)
    price_per_session = forms.DecimalField(required=False, min_value=0, max_digits=8, decimal_places=2,
                                           label=_('Valor por aula'), widget=forms.NumberInput(attrs=MONEY_ATTRS))
    price_total = forms.DecimalField(required=False, min_value=0, max_digits=8, decimal_places=2,
                                     label=_('Valor total'), widget=forms.NumberInput(attrs=MONEY_ATTRS))
    notes = forms.CharField(required=False, max_length=200, label=_('Observação'),
                            widget=forms.TextInput(attrs={'class': 'form-control'}))

    @classmethod
    def for_package(cls, package, data=None):
        initial = {'name': package.name, 'sessions': package.sessions, 'valid_days': package.valid_days,
                   'no_deadline': package.valid_days is None, 'pricing_mode': 'per_session',
                   'price_per_session': package.price_per_session, 'price_total': package.price_total,
                   'notes': package.notes}
        return cls(data, initial=initial)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('no_deadline'):
            cleaned['valid_days'] = None
            self.errors.pop('valid_days', None)
        elif cleaned.get('valid_days') is None and 'valid_days' not in self.errors:
            self.add_error('valid_days', gettext('Informe o prazo em dias ou marque "Sem prazo".'))
        return cleaned


class RefundForm(forms.Form):
    """Give money back (or undo a payment entered wrongly) against one of the student's payments."""
    payment = forms.ModelChoiceField(queryset=Payment.objects.none(), required=False, label=_('Pagamento'),
                                     empty_label=_('— escolher o pacote abaixo —'),
                                     widget=forms.Select(attrs={'class': 'form-select'}))
    student_package = forms.ModelChoiceField(queryset=StudentPackage.objects.none(), required=False, label=_('Pacote'),
                                             empty_label=_('—'), widget=forms.Select(attrs={'class': 'form-select'}))
    kind = forms.ChoiceField(choices=Refund.Kind.choices, initial=Refund.Kind.REFUND, label=_('Tipo'),
                             widget=forms.RadioSelect)
    amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=8, decimal_places=2, label=_('Valor'),
                                widget=forms.NumberInput(attrs={**MONEY_ATTRS, 'min': '0.01'}))
    method = forms.ChoiceField(choices=Payment.Method.choices, initial=Payment.Method.TRANSFER, label=_('Forma de devolução'),
                               widget=forms.Select(attrs={'class': 'form-select'}))
    refunded_on = forms.DateField(label=_('Data'), widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    reason = forms.CharField(required=False, max_length=200, label=_('Motivo'),
                             widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Ex.: pacote cancelado, valor lançado errado')}))

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.person = person
        self.fields['payment'].queryset = person.payments.select_related('student_package')
        self.fields['payment'].label_from_instance = lambda p: f'{p.paid_on:%d/%m/%Y} · {p.get_method_display()} · {p.amount}'
        self.fields['student_package'].queryset = person.packages.all()
        self.fields['student_package'].label_from_instance = lambda p: f'{p.name} ({p.sold_on:%d/%m/%Y})'

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('payment') and not cleaned.get('student_package') and not self.errors:
            self.add_error('payment', gettext('Escolha o pagamento ou o pacote a que a devolução se refere.'))
        return cleaned


class CancelPackageForm(forms.Form):
    """Cancel a package: the agreed fee, how much goes back (it starts as the calculated amount), how and why."""
    fee = forms.DecimalField(required=False, min_value=0, max_digits=8, decimal_places=2, label=_('Taxa ou multa combinada'),
                             widget=forms.NumberInput(attrs=MONEY_ATTRS))
    amount = forms.DecimalField(required=False, min_value=0, max_digits=8, decimal_places=2, label=_('Valor a devolver'),
                                widget=forms.NumberInput(attrs=MONEY_ATTRS))
    method = forms.ChoiceField(choices=Payment.Method.choices, initial=Payment.Method.TRANSFER, label=_('Forma de devolução'),
                               widget=forms.Select(attrs={'class': 'form-select'}))
    refunded_on = forms.DateField(label=_('Data da devolução'), widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))
    reason = forms.CharField(required=False, max_length=200, label=_('Motivo'),
                             widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Ex.: o aluno mudou de cidade')}))
    remove_future = forms.BooleanField(required=False, label=_('Tirar da agenda as aulas marcadas daqui para a frente'),
                                       widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
