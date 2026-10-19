"""Dados de demonstração: um estúdio com 18 alunos que cobrem os casos do sistema.

Tudo é calculado em relação a *hoje*, então a demo está sempre "viva" (aula hoje, pacote vencendo daqui a 5 dias, etc.),
sempre que for rodada. Usa as mesmas funções do app (pacotes, cobrança, devolução, pedidos, agenda), então os números
batem com o que a personal veria no uso real. NÃO use em produção:

    python populate_db.py --demo

Os casos (um aluno por situação, para testar cada tela e filtro):

  Ana        ativa, híbrida; pacote com 2 aulas sobrando (aviso de renovação), meta semanal, sequência, 3 avaliações com
             fotos, treino em andamento hoje, aula hoje, link da ficha
  Bruno      sem app (só ficha); pagamento parcial (cobrar), faltou, aula remarcada, link da ficha, restrição de saúde
  Carla      pacote vencendo em 5 dias; pedido de remarcação pendente
  Diego      experimental; convite pendente, ainda sem termos aceitos; aula experimental amanhã
  Eva        pausada (viagem); pacote concluído
  Fábio      ex-aluno; pacote cancelado com devolução
  Gabriela, Henrique e Inês   pacote compartilhado por três
  Joana      pacote vencido com aulas sobrando; cancelamento tardio (a aula conta)
  Karina     pacote congelado
  Luís       pagou a mais (devolução pendente)
  Marta      pediu exclusão dos dados; retirou o consentimento das fotos
  Nuno       pacote sem prazo; horário mensal (2ª quinta); aula de reposição
  Olívia     parcelado (falta pagar); pedido de cancelamento pendente; avaliações com fotos
  Rui        recém-cadastrado, sem pacote, ainda não viu o tour do app
  Sofia      ativa, só ficha online, sem aulas ainda
  Tiago      experimental, sem app
"""
import io
import secrets
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from . import (
    agenda, app_access, billing, class_requests, consent, packages, recurrence, refunds, sheet_link,
)
from .history import log_event
from .models import (
    Appointment, Assessment, Exercise, Holiday, MuscleGroup, NotificationPreference, Package, Payment, Person,
    PersonRoutine, PersonRoutineExercise, PhotoPosition, ProgressPhoto, Routine, RoutineExercise, ScheduleSlot,
    SessionExercise, SessionSet, StudentEvent, StudentNote, WhatsAppTemplate, WorkoutSession,
)

TRAINER_EMAIL = 'personal@example.com'
MARKER_EMAIL = 'ana.ferreira@example.com'   # present = the demo was already loaded

# --- Catálogo -------------------------------------------------------------------------------------------------------

MUSCLE_GROUPS = [
    ('Peito', 'peito', 'Músculos do peitoral'), ('Costas', 'costas', 'Músculos das costas'),
    ('Pernas', 'pernas', 'Músculos das pernas'), ('Ombros', 'ombros', 'Músculos dos ombros'),
    ('Bíceps', 'biceps', 'Músculos do bíceps'), ('Tríceps', 'triceps', 'Músculos do tríceps'),
    ('Abdômen', 'abdomen', 'Músculos abdominais'), ('Cardio', 'cardio', 'Exercícios cardiovasculares'),
]

# (nome, grupo, séries, reps, descrição, instruções)
EXERCISES = [
    ('Supino Reto', 'peito', 4, 10, 'Exercício composto para peito', 'Deite no banco, desça a barra até o peito e empurre sem tirar os glúteos do banco.'),
    ('Supino Inclinado', 'peito', 3, 12, 'Foco na parte superior do peito', 'Banco a 30 graus; cotovelos a 45 graus do tronco.'),
    ('Supino com Halteres', 'peito', 3, 12, 'Maior amplitude que a barra', 'Desça até alinhar os cotovelos ao tronco e suba juntando os halteres.'),
    ('Crucifixo', 'peito', 3, 12, 'Isolamento para peito', 'Cotovelos levemente flexionados; abra até sentir o alongamento.'),
    ('Peck Deck', 'peito', 3, 12, 'Isolamento na máquina', 'Costas apoiadas; feche sem encostar as mãos com força.'),
    ('Flexão de Braço', 'peito', 3, 15, 'Peso do corpo', 'Corpo alinhado da cabeça aos calcanhares; peito quase no chão.'),
    ('Barra Fixa', 'costas', 4, 8, 'Exercício composto para costas', 'Pegada pronada, puxe o queixo acima da barra sem balançar.'),
    ('Remada Curvada', 'costas', 4, 10, 'Largura e espessura das costas', 'Tronco inclinado a 45 graus, coluna neutra; leve a barra ao umbigo.'),
    ('Puxada Frontal', 'costas', 3, 12, 'Trabalha o latíssimo do dorso', 'Puxe a barra até a altura do queixo, peito aberto.'),
    ('Remada Baixa', 'costas', 3, 12, 'Espessura das costas', 'Tronco firme; leve o triângulo ao abdômen juntando as escápulas.'),
    ('Remada Unilateral', 'costas', 3, 12, 'Com halter, um braço de cada vez', 'Apoie a mão no banco e puxe o halter rente ao corpo.'),
    ('Levantamento Terra', 'costas', 4, 6, 'Cadeia posterior completa', 'Barra rente às pernas, coluna neutra; empurre o chão com os pés.'),
    ('Agachamento', 'pernas', 4, 12, 'Exercício fundamental para pernas', 'Pés na largura dos ombros, joelhos acompanham a ponta dos pés; desça até 90 graus.'),
    ('Leg Press', 'pernas', 4, 15, 'Quadríceps e glúteos', 'Não estique totalmente os joelhos no topo.'),
    ('Cadeira Extensora', 'pernas', 3, 15, 'Isolamento de quadríceps', 'Suba em 1 segundo e desça em 3.'),
    ('Mesa Flexora', 'pernas', 3, 15, 'Posteriores de coxa', 'Quadril colado ao banco; controle a descida.'),
    ('Afundo', 'pernas', 3, 12, 'Unilateral', 'Passo largo; joelho de trás quase toca o chão.'),
    ('Stiff', 'pernas', 3, 12, 'Posteriores e glúteos', 'Joelhos semiflexionados; leve o quadril para trás mantendo a coluna reta.'),
    ('Elevação Pélvica', 'pernas', 4, 12, 'Foco em glúteos', 'Escápulas no banco; suba o quadril contraindo os glúteos.'),
    ('Agachamento Búlgaro', 'pernas', 3, 10, 'Unilateral avançado', 'Pé de trás no banco; tronco levemente inclinado.'),
    ('Panturrilha em Pé', 'pernas', 4, 15, 'Gêmeos', 'Amplitude total, pausa de 1 segundo no topo.'),
    ('Desenvolvimento com Barra', 'ombros', 4, 10, 'Exercício composto para ombros', 'Barra à frente do rosto; não arquear a lombar.'),
    ('Elevação Lateral', 'ombros', 3, 12, 'Deltoide médio', 'Cotovelos ligeiramente flexionados; suba até a altura dos ombros.'),
    ('Elevação Frontal', 'ombros', 3, 12, 'Deltoide anterior', 'Sem balançar o tronco.'),
    ('Face Pull', 'ombros', 3, 15, 'Deltoide posterior e postura', 'Corda na altura do rosto; abra os cotovelos para fora.'),
    ('Encolhimento', 'ombros', 3, 12, 'Trapézio', 'Suba os ombros em direção às orelhas, sem girar.'),
    ('Rosca Direta', 'biceps', 3, 12, 'Exercício básico para bíceps', 'Cotovelos colados ao corpo.'),
    ('Rosca Martelo', 'biceps', 3, 12, 'Bíceps e antebraços', 'Pegada neutra, sem balançar.'),
    ('Rosca Scott', 'biceps', 3, 10, 'Isolamento no banco', 'Braços apoiados; desça até quase estender.'),
    ('Tríceps Testa', 'triceps', 3, 12, 'Isolamento de tríceps', 'Cotovelos apontando para o teto; desça até a testa.'),
    ('Tríceps Corda', 'triceps', 3, 15, 'Cabo para tríceps', 'Cotovelos fixos; abra a corda no final.'),
    ('Mergulho no Banco', 'triceps', 3, 12, 'Peso do corpo', 'Mãos no banco, quadril próximo; desça até 90 graus.'),
    ('Tríceps Francês', 'triceps', 3, 12, 'Com halter por trás da cabeça', 'Cotovelos fechados; estenda sem mover os ombros.'),
    ('Abdominal Crunch', 'abdomen', 3, 20, 'Exercício básico de abdômen', 'Queixo afastado do peito; suba só a região torácica.'),
    ('Abdominal Infra', 'abdomen', 3, 15, 'Elevação de pernas', 'Lombar colada ao chão durante todo o movimento.'),
    ('Prancha', 'abdomen', 3, 60, 'Isometria para o core (segundos)', 'Cotovelos sob os ombros; glúteos e abdômen contraídos.'),
    ('Prancha Lateral', 'abdomen', 3, 40, 'Oblíquos (segundos por lado)', 'Corpo em linha reta, quadril alto.'),
    ('Russian Twist', 'abdomen', 3, 20, 'Rotação com peso', 'Pés fora do chão, gire o tronco e não só os braços.'),
    ('Esteira', 'cardio', 1, 20, 'Corrida na esteira (minutos)', 'Comece caminhando e aumente a velocidade aos poucos.'),
    ('Bicicleta Ergométrica', 'cardio', 1, 30, 'Pedalar (minutos)', 'Selim na altura do quadril; cadência constante.'),
    ('Elíptico', 'cardio', 1, 25, 'Baixo impacto (minutos)', 'Postura ereta; empurre e puxe as alças.'),
    ('Corda', 'cardio', 5, 60, 'Pular corda (segundos por série)', 'Saltos curtos, punhos soltos.'),
    ('Remo Ergométrico', 'cardio', 1, 15, 'Corpo todo (minutos)', 'Pernas, tronco e braços nessa ordem; volte ao contrário.'),
]
RETIRED_EXERCISE = ('Máquina Smith antiga (descontinuado)', 'peito', 3, 12, 'Equipamento retirado do estúdio.', '')

# (nome, descrição, nível, minutos, ativo, [(exercício, séries, reps, descanso)])
ROUTINES = [
    ('Treino A - Peito e Tríceps', 'Treino focado em peitorais e tríceps', 'intermediario', 60, True,
     [('Supino Reto', 4, 10, 90), ('Supino Inclinado', 3, 12, 90), ('Crucifixo', 3, 12, 60),
      ('Tríceps Testa', 3, 12, 60), ('Tríceps Corda', 3, 15, 60)]),
    ('Treino B - Costas e Bíceps', 'Treino focado em costas e bíceps', 'intermediario', 60, True,
     [('Barra Fixa', 4, 8, 90), ('Remada Curvada', 4, 10, 90), ('Puxada Frontal', 3, 12, 60),
      ('Rosca Direta', 3, 12, 60), ('Rosca Martelo', 3, 12, 60)]),
    ('Treino C - Pernas', 'Treino completo de membros inferiores', 'avancado', 70, True,
     [('Agachamento', 4, 12, 120), ('Leg Press', 4, 15, 90), ('Cadeira Extensora', 3, 15, 60),
      ('Mesa Flexora', 3, 15, 60), ('Panturrilha em Pé', 4, 15, 45)]),
    ('Treino D - Ombros e Abdômen', 'Treino de ombros com abdômen', 'intermediario', 50, True,
     [('Desenvolvimento com Barra', 4, 10, 90), ('Elevação Lateral', 3, 12, 60), ('Elevação Frontal', 3, 12, 60),
      ('Abdominal Crunch', 3, 20, 45), ('Prancha', 3, 60, 60)]),
    ('Full body - iniciante', 'Corpo inteiro, 3 vezes por semana, para quem está começando', 'iniciante', 45, True,
     [('Agachamento', 3, 12, 90), ('Supino com Halteres', 3, 12, 90), ('Remada Baixa', 3, 12, 90),
      ('Desenvolvimento com Barra', 2, 12, 60), ('Prancha', 3, 30, 45)]),
    ('Treino em casa (sem equipamento)', 'Para os dias sem estúdio: só o peso do corpo', 'iniciante', 40, True,
     [('Flexão de Braço', 3, 12, 60), ('Afundo', 3, 12, 60), ('Elevação Pélvica', 3, 15, 60),
      ('Abdominal Crunch', 3, 20, 45), ('Prancha', 3, 40, 45)]),
    ('Glúteos e posteriores', 'Ênfase em glúteos e cadeia posterior', 'intermediario', 55, True,
     [('Elevação Pélvica', 4, 12, 90), ('Stiff', 3, 12, 90), ('Agachamento Búlgaro', 3, 10, 90),
      ('Mesa Flexora', 3, 15, 60), ('Prancha Lateral', 3, 40, 45)]),
    ('Treino antigo (arquivado)', 'Modelo que não uso mais', 'iniciante', 40, False,
     [('Esteira', 1, 20, 0), ('Abdominal Crunch', 3, 15, 45)]),
]

# (nome, descrição, aulas, prazo em dias (None = sem prazo), como informa o valor, valor, taxa %, taxa fixa, dias sem taxa, ativo)
PACKAGES = [
    ('Aula experimental', 'Uma aula para conhecer o estúdio', 1, 15, 'total', '20', '0', '0', 0, True),
    ('Pacote 4 aulas', 'Para um mês leve', 4, 45, 'per_session', '40', '20', '0', 2, True),
    ('Pacote 8 aulas', 'Duas aulas por semana', 8, 60, 'per_session', '38', '20', '0', 7, True),
    ('Pacote 12 aulas', 'O mais vendido', 12, 90, 'per_session', '35', '30', '5', 7, True),
    ('Pacote 24 aulas (semestral)', 'Melhor valor por aula', 24, 180, 'total', '780', '50', '0', 14, True),
    ('Pacote livre (sem prazo)', 'Use quando puder: sem data para acabar', 10, None, 'total', '450', '0', '0', 7, True),
    ('Pacote em grupo (12 aulas)', 'Até 3 pessoas dividem o saldo', 12, 90, 'total', '600', '25', '0', 7, True),
    ('Pacote promocional (encerrado)', 'Promoção de lançamento', 6, 30, 'total', '150', '0', '0', 0, False),
]

PALETTE = [(231, 111, 81), (42, 157, 143), (38, 70, 83), (233, 196, 106), (106, 76, 147), (69, 123, 157), (214, 40, 57)]


# --- Utilidades -----------------------------------------------------------------------------------------------------

def _money(value):
    return Decimal(str(value))


class Demo:
    """Contexto: a data de hoje, a personal e a senha dos alunos com app."""

    def __init__(self, password=None, trainer=None):
        self.today = timezone.localdate()
        self.password = password
        self.trainer = trainer
        self.people = {}
        self.catalog = {}
        self.routines = {}

    def day(self, offset):
        return self.today + timedelta(days=offset)

    def at(self, day, clock):
        """Aware datetime from a date and "HH:MM"."""
        hour, minute = (int(part) for part in clock.split(':'))
        return timezone.make_aware(datetime.combine(day, time(hour, minute)))

    def recent(self, weekdays, count, before=None):
        """The `count` most recent dates before `before` (default: today) on these weekdays, oldest first."""
        day, found = (before or self.today) - timedelta(days=1), []
        while len(found) < count and day > self.today - timedelta(days=400):
            if day.weekday() in weekdays:
                found.append(day)
            day -= timedelta(days=1)
        return sorted(found)

    def span(self, weekdays, start_ago, count):
        """`count` dates on these weekdays, starting `start_ago` days back and going forward."""
        day, found = self.today - timedelta(days=start_ago), []
        while len(found) < count:
            if day.weekday() in weekdays:
                found.append(day)
            day += timedelta(days=1)
        return found

    def upcoming(self, weekdays, count, after=None):
        day, found = (after or self.today) + timedelta(days=1), []
        while len(found) < count:
            if day.weekday() in weekdays:
                found.append(day)
            day += timedelta(days=1)
        return found


def _initials_image(name, color, size=256):
    img = Image.new('RGB', (size, size), color)
    draw = ImageDraw.Draw(img)
    initials = ''.join(part[0] for part in name.split()[:2]).upper()
    try:
        font = ImageFont.load_default(size=size // 2)
    except TypeError:   # older Pillow: bitmap font
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), initials, font=font)
    draw.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]), initials,
              fill=(255, 255, 255), font=font)
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=85)
    return ContentFile(out.getvalue(), name='avatar.jpg')


def _body_photo(label, tone, width, label2=''):
    """A neutral silhouette (no real person) with the position and date written on it; `width` changes between dates."""
    img = Image.new('RGB', (600, 800), (232, 235, 238))
    draw = ImageDraw.Draw(img)
    for y in range(800):   # soft vertical gradient
        shade = 232 - y // 28
        draw.line([(0, y), (600, y)], fill=(shade, shade + 2, shade + 5))
    body = tuple(max(c - 70, 0) for c in tone)
    cx = 300
    draw.ellipse([cx - 55, 90, cx + 55, 200], fill=body)
    draw.rounded_rectangle([cx - width, 215, cx + width, 520], radius=60, fill=body)
    draw.rectangle([cx - width // 2 - 20, 500, cx - 12, 740], fill=body)
    draw.rectangle([cx + 12, 500, cx + width // 2 + 20, 740], fill=body)
    try:
        font = ImageFont.load_default(size=34)
    except TypeError:
        font = ImageFont.load_default()
    draw.text((30, 740), f'{label}  {label2}', fill=(70, 80, 90), font=font)
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=80)
    return ContentFile(out.getvalue(), name='photo.jpg')


def make_student(demo, name, email, *, has_app=True, phone='', birth=None, city='', modality='', goal='', health='',
                 emergency=None, notes='', status='active', reason='', weekly_goal=None, joined=30, terms=True,
                 photos_consent=True, avatar=True, tour_seen=True, password_set=True):
    """A student with the same account rules as the registration form (core/app_access.py)."""
    user = app_access.new_account(email, has_app)
    if has_app and password_set:
        user.set_password(demo.password)
        user.save(update_fields=['password'])
        EmailAddress.objects.filter(user=user).update(verified=True)
    created = demo.at(demo.day(-joined), '10:00')
    person = Person.objects.create(
        user=user, person_name=name, phone=phone, birth_date=birth, city=city, modality=modality, goal=goal,
        health_notes=health, notes=notes, has_app=has_app, weekly_goal=weekly_goal,
        emergency_contact_name=(emergency or ('', ''))[0], emergency_contact_phone=(emergency or ('', ''))[1],
        intro_seen_at=created + timedelta(hours=2) if (has_app and tour_seen) else None)
    Person.objects.filter(pk=person.pk).update(created_at=created)
    if avatar:
        person.avatar.save(f'{person.pk}.jpg', _initials_image(name, PALETTE[person.pk % len(PALETTE)]), save=True)
    log_event(person, StudentEvent.Kind.CREATED, demo.trainer, 'Cadastro do aluno')
    StudentEvent.objects.filter(person=person, kind=StudentEvent.Kind.CREATED).update(at=created)
    source = consent.ConsentRecord.Source.STUDENT if has_app else consent.ConsentRecord.Source.TRAINER
    actor = None if has_app else demo.trainer
    if terms:
        consent.record_consent(person, consent.ConsentRecord.Kind.TERMS, True, source=source, actor=actor, at=created)
        consent.record_consent(person, consent.ConsentRecord.Kind.HEALTH_DATA, True, source=source, actor=actor, at=created)
        if photos_consent:
            consent.record_consent(person, consent.ConsentRecord.Kind.PHOTOS, True, source=source, actor=actor, at=created)
    if status != 'active':
        person.set_status(status, reason, demo.trainer)
    demo.people[name.split()[0]] = person
    return person


def add_note(demo, person, text, days_ago):
    note = StudentNote.objects.create(person=person, author=demo.trainer, text=text)
    StudentNote.objects.filter(pk=note.pk).update(created_at=demo.at(demo.day(-days_ago), '19:30'))


def klass(demo, person, offset, clock, status='attended', *, kind='regular', late=False, notes='', slot=None, duration=60):
    """One class `offset` days from today at "HH:MM" (negative = past). Idempotent per student and time."""
    starts = demo.at(demo.day(offset), clock)
    appointment, _ = Appointment.objects.get_or_create(
        person=person, starts_at=starts,
        defaults={'status': status, 'kind': kind, 'cancelled_late': late, 'notes': notes, 'slot': slot,
                  'duration_minutes': duration, 'status_changed_at': starts if status != 'scheduled' else None})
    return appointment


def pay(demo, person, package, amount, days_ago, method='pix', notes=''):
    return billing.register_payment(person, package=package, amount=_money(amount), paid_on=demo.day(-days_ago),
                                    method=method, notes=notes, actor=demo.trainer)


def weekly_slot(demo, person, weekdays, clock, package=None, since=60, duration=60, until=None, interval=1):
    hour, minute = (int(part) for part in clock.split(':'))
    slots, _ = recurrence.create_series(person, weekdays, time(hour, minute), duration, demo.day(-since),
                                        interval_weeks=interval, valid_until=until)
    if package is not None:
        ScheduleSlot.objects.filter(pk__in=[s.pk for s in slots]).update(package=package)
    return slots


def assign(demo, person, routine_name, *, started=0, ended=None, active=True, notes='', custom=None):
    """A routine for the student: a copy of the template (or of `custom`: (name, description, level, minutes, [rows]))."""
    if custom:
        name, description, level, minutes, rows = custom
        template = None
    else:
        template = demo.routines[routine_name]
        name, description, level, minutes = template.routine_name, template.description, template.difficulty_level, template.estimated_duration
        rows = [(r.exercise.exercise_name, r.sets, r.reps, r.rest_seconds) for r in template.exercises.select_related('exercise')]
    person_routine = PersonRoutine.objects.create(
        person=person, routine_template=template, routine_name=name, description=description, difficulty_level=level,
        estimated_duration=minutes, routine_date=demo.day(-started), start_date=demo.day(-started),
        end_date=demo.day(-ended) if ended is not None else None, is_active=active, notes=notes)
    exercises = {e.exercise_name: e for e in Exercise.objects.all()}
    for order, (exercise_name, sets, reps, rest) in enumerate(rows, 1):
        PersonRoutineExercise.objects.create(person_routine=person_routine, exercise=exercises[exercise_name], order=order,
                                             sets=sets, reps=reps, rest_seconds=rest)
    PersonRoutine.objects.filter(pk=person_routine.pk).update(created_at=demo.at(demo.day(-started), '09:00'))
    return person_routine


# Carga de partida por grupo muscular (kg); None = peso do corpo / tempo
BASE_LOAD = {'peito': 40, 'costas': 35, 'pernas': 60, 'ombros': 18, 'biceps': 12, 'triceps': 15, 'abdomen': None, 'cardio': None}


def log_workout(demo, person_routine, days_ago, clock='07:00', minutes=55, strength=1.0, progress=0, only=None, notes=''):
    """A finished workout `days_ago` days back, with weights that grow with `progress` (steps of 2.5 kg)."""
    start = demo.at(demo.day(-days_ago), clock)
    session = WorkoutSession.objects.create(person_routine=person_routine, notes=notes)
    WorkoutSession.objects.filter(pk=session.pk).update(
        start_time=start, end_time=start + timedelta(minutes=minutes), duration_minutes=minutes, is_completed=True)
    rows = list(person_routine.exercises.select_related('exercise__muscle_group').order_by('order'))
    for index, row in enumerate(rows[:only] if only else rows):
        base = BASE_LOAD.get(row.exercise.muscle_group.slug)
        weight = None if base is None else round((base * strength + 2.5 * progress) * 2) / 2
        log = SessionExercise.objects.create(session=session, person_routine_exercise=row)
        when = start + timedelta(minutes=8 * (index + 1))
        for number in range(1, row.sets + 1):
            SessionSet.objects.create(session_exercise=log, number=number, weight=weight, reps=max(row.reps - (number - 1) // 2, 1),
                                      is_done=True, completed_at=when + timedelta(minutes=number))
        log.sync_from_sets()
    return session


def train(demo, routines, weekdays, weeks, *, skip=(), strength=1.0, clock='07:00', progress_per_week=0.5, minutes=55, min_ago=1):
    """Workouts on these weekdays for the last `weeks` weeks, cycling through `routines`; `skip` = days ago with no workout."""
    days = [(demo.today - d).days for d in demo.recent(weekdays, len(weekdays) * weeks)]
    count = 0
    for ago in sorted(days, reverse=True):
        if ago in skip or ago < min_ago:
            continue
        log_workout(demo, routines[count % len(routines)], ago, clock, minutes + (count * 3) % 15 - 5, strength,
                    progress=round((weeks * 7 - ago) / 7 * progress_per_week, 1))
        count += 1
    return count


def comment(person, text, nth=0):
    """The student's own comment on one of the latest finished workouts (shown to the trainer on the dashboard)."""
    session = WorkoutSession.objects.filter(person_routine__person=person, is_completed=True).order_by('-start_time')[nth]
    session.notes = text
    session.save(update_fields=['notes'])


def assessments(demo, person, rows, photos=False):
    """rows: [(days_ago, weight, fat, chest, waist, hip, arm, thigh, notes)]; with photos, the four standard positions."""
    positions = list(PhotoPosition.objects.filter(is_active=True))
    made = []
    for index, (ago, weight, fat, chest, waist, hip, arm, thigh, notes) in enumerate(rows):
        assessment = Assessment.objects.create(
            person=person, date=demo.day(-ago), weight_kg=_money(weight), height_cm=_money('168'), body_fat_pct=_money(fat),
            chest_cm=_money(chest), waist_cm=_money(waist), hip_cm=_money(hip), arm_cm=_money(arm), thigh_cm=_money(thigh), notes=notes)
        if photos:
            width = 150 - index * 8
            for position in positions:
                ProgressPhoto.objects.create(
                    person=person, assessment=assessment, angle=position.key, date=demo.day(-ago),
                    image=_body_photo(position.name, PALETTE[person.pk % len(PALETTE)], width, demo.day(-ago).strftime('%d/%m/%Y')))
        made.append(assessment)
    return made


# --- Catálogo (exercícios, treinos, pacotes) -----------------------------------------------------------------------------

def load_library(demo):
    """Muscle groups, exercises and workout templates: what a real installation also starts with."""
    for name, slug, description in MUSCLE_GROUPS:
        MuscleGroup.objects.get_or_create(slug=slug, defaults={'name': name, 'description': description})
    groups = {g.slug: g for g in MuscleGroup.objects.all()}
    for name, slug, sets, reps, description, instructions in EXERCISES + [RETIRED_EXERCISE]:
        Exercise.objects.get_or_create(
            exercise_name=name, defaults={'muscle_group': groups[slug], 'default_sets': sets, 'default_reps': reps, 'description': description,
                                          'instructions': instructions, 'media_type': 'image', 'is_active': name != RETIRED_EXERCISE[0]})
    exercises = {e.exercise_name: e for e in Exercise.objects.all()}
    for name, description, level, minutes, active, rows in ROUTINES:
        routine, created = Routine.objects.get_or_create(
            routine_name=name, defaults={'description': description, 'difficulty_level': level, 'estimated_duration': minutes, 'is_active': active})
        if created:
            for order, (exercise_name, sets, reps, rest) in enumerate(rows, 1):
                RoutineExercise.objects.create(routine=routine, exercise=exercises[exercise_name], order=order, sets=sets, reps=reps, rest_seconds=rest)
        demo.routines[name] = routine


def load_packages(demo):
    for name, description, sessions, valid, mode, price, percent, fixed, free_days, active in PACKAGES:
        values = {'description': description, 'sessions': sessions, 'valid_days': valid, 'pricing_mode': mode, 'is_active': active,
                  'cancel_fee_percent': _money(percent), 'cancel_fee_fixed': _money(fixed), 'cancel_free_days': free_days}
        values['price_per_session' if mode == 'per_session' else 'price_total'] = _money(price)
        demo.catalog[name] = Package.objects.get_or_create(name=name, defaults=values)[0]


def load_catalog(demo):
    load_library(demo)
    load_packages(demo)


# --- Os alunos ---------------------------------------------------------------------------------------------------------

def ana(demo):
    p = make_student(demo, 'Ana Ferreira', MARKER_EMAIL, phone='912 345 001', birth=date(1993, 4, 12), city='Lisboa',
                     modality='hybrid', goal='Hipertrofia e definição para o verão', weekly_goal=3, joined=75,
                     health='Sem restrições. Leve desconforto no ombro direito em exercícios acima da cabeça.',
                     emergency=('Marco Ferreira', '913 456 001'))
    sold = packages.sell(p, demo.catalog['Pacote 12 aulas'], sold_on=demo.day(-40), actor=demo.trainer)
    pay(demo, p, sold, 210, 40, 'mbway', '1ª parcela')
    pay(demo, p, sold, 210, 12, 'mbway', '2ª parcela')
    weekly_slot(demo, p, [0, 3], '07:00', package=sold, since=40)
    slot = ScheduleSlot.objects.filter(person=p).first()
    dates = demo.recent([0, 3], 11)
    for index, day in enumerate(dates):
        status = 'attended'
        if index == 2:
            status = 'missed'
        elif index == 5:
            status = 'cancelled'
        klass(demo, p, (day - demo.today).days, '07:00', status, slot=slot, notes='Faltou: trabalho' if status == 'missed' else '')
    klass(demo, p, 0, '18:30', 'scheduled', notes='Revisão da técnica do agachamento')
    a, b, c = (assign(demo, p, name, started=40) for name in ('Treino A - Peito e Tríceps', 'Treino B - Costas e Bíceps', 'Treino C - Pernas'))
    assign(demo, p, 'Full body - iniciante', started=75, ended=41, active=False)
    train(demo, [a, b, c], [0, 2, 4], 8, skip={9, 10}, strength=1.0)
    running = log_workout(demo, c, 0, '18:00', only=2)   # a workout in progress right now
    WorkoutSession.objects.filter(pk=running.pk).update(start_time=timezone.now() - timedelta(minutes=25), end_time=None,
                                                         duration_minutes=None, is_completed=False)
    comment(p, 'Ótimo treino! O ombro não incomodou nada hoje.', 1)
    assessments(demo, p, [(70, '66.0', '27.5', '92.0', '78.0', '99.0', '28.0', '56.0', 'Avaliação inicial'),
                          (35, '63.8', '25.0', '91.0', '75.5', '97.5', '28.5', '55.0', 'Boa evolução na cintura'),
                          (2, '62.4', '23.2', '90.5', '73.0', '96.5', '29.0', '54.5', 'Meta do verão a caminho')], photos=True)
    add_note(demo, p, 'Prefere treinar de manhã cedo. Pediu ajuda com alimentação: encaminhar para a nutricionista parceira.', 30)
    add_note(demo, p, 'Ombro direito melhorou; podemos voltar ao desenvolvimento com barra.', 5)
    sheet_link.enable(p, demo.trainer)
    NotificationPreference.objects.update_or_create(user=p.user, defaults={'email_enabled': True, 'email_at': timezone.now(), 'language': 'pt-br'})


def bruno(demo):
    p = make_student(demo, 'Bruno Costa', 'bruno.costa@example.com', has_app=False, phone='912 345 002', birth=date(1979, 9, 3),
                     city='Porto', modality='in_person', goal='Reduzir dores nas costas e ganhar mobilidade', joined=40,
                     health='Hérnia de disco L4-L5. Evitar carga axial e flexão de tronco com peso. Acompanhado por fisioterapeuta.',
                     emergency=('Sónia Costa', '913 456 002'), notes='Prefere conversar por WhatsApp.')
    sold = packages.sell(p, demo.catalog['Pacote 8 aulas'], sold_on=demo.day(-20), actor=demo.trainer)
    pay(demo, p, sold, 150, 20, 'cash', 'Sinal')   # 154 € em aberto: aparece em "cobrar"
    weekly_slot(demo, p, [1, 4], '18:30', package=sold, since=20)
    slot = ScheduleSlot.objects.filter(person=p).first()
    for index, day in enumerate(demo.recent([1, 4], 5)):
        klass(demo, p, (day - demo.today).days, '18:30', 'missed' if index == 3 else 'attended', slot=slot,
              notes='Avisou tarde' if index == 3 else '')
    future = demo.upcoming([1, 4], 3)
    moved = klass(demo, p, (future[0] - demo.today).days, '18:30', 'scheduled', slot=slot)
    agenda.move_appointment(moved, demo.at(future[0], '19:30'))
    assign(demo, p, '', started=20, notes='Sem carga axial. Priorizar mobilidade e estabilidade do core.',
           custom=('Treino adaptado - coluna', 'Fortalecimento do core e mobilidade, sem carga axial', 'iniciante', 50,
                   [('Prancha', 3, 30, 45), ('Elevação Pélvica', 3, 12, 60), ('Remada Baixa', 3, 12, 60),
                    ('Puxada Frontal', 3, 12, 60), ('Bicicleta Ergométrica', 1, 15, 0)]))
    sheet_link.enable(p, demo.trainer)
    link = p.sheet_link
    link.last_viewed_at = timezone.now() - timedelta(days=2)
    link.save(update_fields=['last_viewed_at'])
    add_note(demo, p, 'Fisioterapeuta liberou exercícios de remada leve. Reavaliar em 3 semanas.', 10)


def carla(demo):
    p = make_student(demo, 'Carla Mendes', 'carla.mendes@example.com', phone='912 345 003', birth=date(1996, 1, 25), city='Braga',
                     modality='hybrid', goal='Condicionamento para corrida de 10 km', weekly_goal=3, joined=52,
                     emergency=('Paulo Mendes', '913 456 003'))
    sold = packages.sell(p, demo.catalog['Pacote 8 aulas'], sold_on=demo.day(-58), actor=demo.trainer)
    pay(demo, p, sold, 304, 58, 'mbway')
    weekly_slot(demo, p, [1], '12:30', package=sold, since=58, duration=45)
    slot = ScheduleSlot.objects.filter(person=p).first()
    for day in demo.span([1], 56, 6):   # the 60-day deadline counts from the first class: it ends in a few days
        klass(demo, p, (day - demo.today).days, '12:30', 'attended', slot=slot, duration=45)
    future = klass(demo, p, 3, '12:30', 'scheduled', slot=slot, duration=45)
    class_requests.create_request(p, future, 'reschedule', proposed=demo.at(demo.day(4), '18:00'), message='Tenho consulta médica nesse horário.')
    a = assign(demo, p, 'Treino em casa (sem equipamento)', started=50)
    c = assign(demo, p, 'Treino C - Pernas', started=50, ended=-10)   # o ciclo termina em 10 dias
    train(demo, [a, c], [0, 2, 5], 6, strength=0.8, clock='06:45', minutes=40)
    NotificationPreference.objects.update_or_create(user=p.user, defaults={'email_enabled': True, 'email_at': timezone.now(), 'notify_plan_expiry': True})


def diego(demo):
    p = make_student(demo, 'Diego Rocha', 'diego.rocha@example.com', phone='912 345 004', birth=date(2000, 6, 18), city='Lisboa',
                     modality='in_person', goal='Perder peso e ganhar disposição', joined=3, terms=False, password_set=False,
                     tour_seen=False, avatar=False)
    klass(demo, p, 1, '17:00', 'scheduled', kind='trial', notes='Primeira aula: avaliação e conversa sobre objetivos')
    sold = packages.sell(p, demo.catalog['Aula experimental'], sold_on=demo.day(-1), actor=demo.trainer)
    pay(demo, p, sold, 20, 1, 'mbway')
    add_note(demo, p, 'Veio por indicação da Ana. Quer começar com 2x por semana.', 3)


def eva(demo):
    p = make_student(demo, 'Eva Santos', 'eva.santos@example.com', phone='912 345 005', birth=date(1988, 11, 2), city='Coimbra',
                     modality='online', goal='Manter o ritmo', joined=130)
    sold = packages.sell(p, demo.catalog['Pacote 12 aulas'], sold_on=demo.day(-120), actor=demo.trainer)
    pay(demo, p, sold, 420, 120, 'transfer')
    weekly_slot(demo, p, [1, 3], '08:00', package=sold, since=120, until=demo.day(-35))
    slot = ScheduleSlot.objects.filter(person=p).first()
    for day in demo.recent([1, 3], 12, before=demo.day(-33)):
        klass(demo, p, (day - demo.today).days, '08:00', 'attended', slot=slot)
    a = assign(demo, p, 'Full body - iniciante', started=120, ended=35, active=False)
    for ago in (40, 47, 54, 61, 68):
        log_workout(demo, a, ago, '08:00', 45, strength=0.7, progress=(70 - ago) / 14)
    p.set_status('paused', 'Viagem de trabalho até dezembro', demo.trainer)


def fabio(demo):
    p = make_student(demo, 'Fábio Lima', 'fabio.lima@example.com', phone='912 345 006', birth=date(1991, 3, 9), city='Faro',
                     modality='in_person', joined=75)
    sold = packages.sell(p, demo.catalog['Pacote 12 aulas'], sold_on=demo.day(-70), actor=demo.trainer)
    pay(demo, p, sold, 420, 70, 'card')
    weekly_slot(demo, p, [0, 2], '19:00', package=sold, since=70, until=demo.day(-50))
    slot = ScheduleSlot.objects.filter(person=p).first()
    for day in demo.recent([0, 2], 4, before=demo.day(-50)):
        klass(demo, p, (day - demo.today).days, '19:00', 'attended', slot=slot)
    packages.sync(p)
    quote = refunds.cancellation_quote(sold, fee=Decimal('5'))
    refunds.cancel_with_refund(sold, demo.trainer, amount=quote['suggested'], method='transfer', refunded_on=demo.day(-45),
                               reason='Mudou de cidade', fee=Decimal('5'))
    p.set_status('former', 'Mudou de cidade', demo.trainer)


def grupo(demo):
    """Gabriela paga; ela, Henrique e Inês dividem as aulas do mesmo pacote."""
    g = make_student(demo, 'Gabriela Nunes', 'gabriela.nunes@example.com', phone='912 345 007', birth=date(1990, 7, 21), city='Lisboa',
                     modality='in_person', goal='Treinar com o marido e a amiga', joined=45)
    h = make_student(demo, 'Henrique Nunes', 'henrique.nunes@example.com', phone='912 345 008', birth=date(1989, 2, 14), city='Lisboa',
                     modality='in_person', joined=45)
    i = make_student(demo, 'Inês Pinto', 'ines.pinto@example.com', phone='912 345 009', birth=date(1992, 12, 5), city='Lisboa',
                     modality='in_person', joined=44)
    sold = packages.sell(g, demo.catalog['Pacote em grupo (12 aulas)'], sold_on=demo.day(-30), members=[h, i], actor=demo.trainer)
    pay(demo, g, sold, 600, 30, 'transfer')
    for person, weekday, clock in ((g, 1, '19:00'), (h, 1, '19:00'), (i, 3, '19:00')):
        weekly_slot(demo, person, [weekday], clock, package=sold, since=30)
        slot = ScheduleSlot.objects.filter(person=person).first()
        for day in demo.recent([weekday], 3):
            klass(demo, person, (day - demo.today).days, clock, 'attended', slot=slot)
    for person in (g, h, i):
        assign(demo, person, 'Full body - iniciante', started=30)
        train(demo, [PersonRoutine.objects.get(person=person)], [1, 3], 3, strength=0.6 if person is not h else 1.0, clock='19:00')


def joana(demo):
    p = make_student(demo, 'Joana Alves', 'joana.alves@example.com', phone='912 345 010', birth=date(1985, 5, 30), city='Setúbal',
                     modality='in_person', goal='Retomar a rotina depois da gravidez', joined=115)
    sold = packages.sell(p, demo.catalog['Pacote 12 aulas'], sold_on=demo.day(-110), actor=demo.trainer)
    pay(demo, p, sold, 420, 110, 'mbway')
    weekly_slot(demo, p, [1], '10:00', package=sold, since=110, until=demo.day(-40))
    slot = ScheduleSlot.objects.filter(person=p).first()
    for index, day in enumerate(demo.span([1], 100, 8)):   # first class ~100 days ago, 90-day deadline: expired
        late = index == 5
        klass(demo, p, (day - demo.today).days, '10:00', 'cancelled' if late else 'attended', late=late, slot=slot,
              notes='Cancelou na véspera: a aula conta' if late else '')
    # prazo de 90 dias já passou com aulas sobrando: aparece em "vencidos"


def karina(demo):
    p = make_student(demo, 'Karina Dias', 'karina.dias@example.com', phone='912 345 011', birth=date(1994, 8, 8), city='Aveiro',
                     modality='hybrid', joined=45)
    sold = packages.sell(p, demo.catalog['Pacote 8 aulas'], sold_on=demo.day(-35), actor=demo.trainer)
    pay(demo, p, sold, 304, 35, 'mbway')
    for day in demo.recent([2], 3, before=demo.day(-6)):
        klass(demo, p, (day - demo.today).days, '09:00', 'attended')
    packages.sync(p)
    sold.refresh_from_db()   # the first-class date was set by sync
    packages.freeze(sold, demo.trainer, today=demo.day(-5))   # férias: o prazo não corre
    a = assign(demo, p, 'Glúteos e posteriores', started=35)
    train(demo, [a], [2, 6], 4, strength=0.75, clock='09:00', minutes=50, min_ago=7)


def luis(demo):
    p = make_student(demo, 'Luís Matos', 'luis.matos@example.com', phone='912 345 012', birth=date(1982, 10, 17), city='Lisboa',
                     modality='in_person', joined=25)
    sold = packages.sell(p, demo.catalog['Pacote 4 aulas'], sold_on=demo.day(-24), actor=demo.trainer)
    pay(demo, p, sold, 200, 24, 'cash', 'Pagou 200 por engano (o pacote é 160)')   # 40 € a devolver
    for day in demo.recent([3], 2):
        klass(demo, p, (day - demo.today).days, '20:00', 'attended')
    a = assign(demo, p, 'Treino D - Ombros e Abdômen', started=24)
    train(demo, [a], [1, 5], 3, strength=0.9, clock='20:00', minutes=50)


def marta(demo):
    p = make_student(demo, 'Marta Silva', 'marta.silva@example.com', phone='912 345 013', birth=date(1987, 1, 11), city='Lisboa',
                     modality='online', joined=60)
    sold = packages.sell(p, demo.catalog['Pacote 4 aulas'], sold_on=demo.day(-30), actor=demo.trainer)
    pay(demo, p, sold, 160, 30, 'mbway')
    klass(demo, p, -20, '20:30', 'attended')
    consent.record_consent(p, consent.ConsentRecord.Kind.PHOTOS, False, at=timezone.now() - timedelta(days=4))
    log_event(p, StudentEvent.Kind.ERASURE_REQUEST, None, 'O aluno pediu a exclusão dos próprios dados')
    a = assign(demo, p, 'Treino em casa (sem equipamento)', started=30)
    train(demo, [a], [1, 4], 3, strength=0.5, clock='20:30', minutes=35, min_ago=9)   # parada há mais de uma semana


def nuno(demo):
    p = make_student(demo, 'Nuno Pereira', 'nuno.pereira@example.com', phone='912 345 014', birth=date(1975, 12, 24), city='Sintra',
                     modality='in_person', goal='Manter a forma; trabalha viajando', joined=95)
    sold = packages.sell(p, demo.catalog['Pacote livre (sem prazo)'], sold_on=demo.day(-90), actor=demo.trainer)
    pay(demo, p, sold, 450, 90, 'transfer')
    first = demo.recent([3], 12)[0]   # uma quinta-feira passada: a série mensal é a "mesma semana do mês"
    slots, _ = recurrence.create_series(p, [], time(19, 0), 60, first, month_rule='nth')
    ScheduleSlot.objects.filter(pk__in=[s.pk for s in slots]).update(package=sold)
    day = first
    while day < demo.today:   # every date the monthly rule fell on so far
        if agenda.slot_on(slots[0], day):
            klass(demo, p, (day - demo.today).days, '19:00', 'attended', slot=slots[0])
        day += timedelta(days=1)
    klass(demo, p, -20, '08:00', 'attended', kind='makeup', notes='Reposição da aula de viagem')
    a = assign(demo, p, 'Full body - iniciante', started=90)
    train(demo, [a], [0], 8, strength=0.9, clock='19:30', minutes=45, skip={30, 37})
    Holiday.objects.get_or_create(date=demo.day(9), defaults={'name': 'Estúdio fechado (manutenção)'})


def olivia(demo):
    p = make_student(demo, 'Olívia Torres', 'olivia.torres@example.com', phone='912 345 015', birth=date(1998, 3, 3), city='Porto',
                     modality='hybrid', goal='Ganhar massa magra', weekly_goal=4, joined=32,
                     health='Asma leve: usa bombinha antes de treinos intensos.', emergency=('Teresa Torres', '913 456 015'))
    sold = packages.sell(p, demo.catalog['Pacote 24 aulas (semestral)'], sold_on=demo.day(-30), actor=demo.trainer)
    pay(demo, p, sold, 300, 30, 'mbway', '1ª parcela')
    pay(demo, p, sold, 200, 5, 'transfer', '2ª parcela')   # faltam 280 €
    weekly_slot(demo, p, [0, 2, 4], '18:00', package=sold, since=30)
    slot = ScheduleSlot.objects.filter(person=p).first()
    for day in demo.recent([0, 2, 4], 12):
        klass(demo, p, (day - demo.today).days, '18:00', 'attended', slot=slot)
    next_class = demo.upcoming([0, 2, 4], 3)[1]   # not the very next one: requests need advance notice
    pending = klass(demo, p, (next_class - demo.today).days, '18:00', 'scheduled', slot=slot)
    class_requests.create_request(p, pending, 'cancel', message='Estou com gripe, não consigo ir.')
    a, b, c = (assign(demo, p, name, started=30) for name in ('Treino A - Peito e Tríceps', 'Treino B - Costas e Bíceps', 'Treino C - Pernas'))
    train(demo, [a, b, c], [0, 1, 2, 4, 5], 4, skip={2, 3}, strength=0.85, clock='18:00', progress_per_week=0.8)
    comment(p, 'Subi a carga no stiff, mas a lombar ficou cansada. Posso manter?', 0)
    assessments(demo, p, [(28, '58.0', '26.0', '88.0', '70.0', '96.0', '27.0', '54.0', 'Início do ciclo'),
                          (1, '59.5', '24.8', '89.0', '69.5', '96.5', '28.0', '55.5', 'Ganhou massa e perdeu gordura')], photos=True)


def rui(demo):
    p = make_student(demo, 'Rui Carvalho', 'rui.carvalho@example.com', phone='912 345 016', birth=date(1999, 4, 1), city='Lisboa',
                     modality='in_person', joined=0, tour_seen=False, avatar=False)
    assign(demo, p, 'Full body - iniciante', started=0)


def sofia(demo):
    p = make_student(demo, 'Sofia Lopes', 'sofia.lopes@example.com', phone='912 345 017', birth=date(1995, 9, 9), city='Lisboa',
                     modality='online', goal='Treinar em casa com orientação à distância', joined=8, avatar=False)
    assign(demo, p, 'Treino em casa (sem equipamento)', started=7)
    log_workout(demo, PersonRoutine.objects.get(person=p), 4, '20:00', 38, strength=0.5)


def tiago(demo):
    p = make_student(demo, 'Tiago Ramos', 'tiago.ramos@example.com', has_app=False, phone='912 345 018', birth=date(2002, 2, 19),
                     city='Cascais', modality='in_person', status='trial', joined=2, avatar=False)
    klass(demo, p, 2, '09:00', 'scheduled', kind='trial', notes='Aula experimental')


STUDENTS = (ana, bruno, carla, diego, eva, fabio, grupo, joana, karina, luis, marta, nuno, olivia, rui, sofia, tiago)


# --- Entrada ---------------------------------------------------------------------------------------------------------

def already_loaded():
    return Person.objects.filter(user__email=MARKER_EMAIL).exists()


def populate(password=None):
    """Load the demo. Returns (summary dict, password used or None when nothing was done)."""
    if already_loaded():
        return None, None
    password = password or secrets.token_urlsafe(9)
    trainer = User.objects.filter(is_staff=True, is_active=True).order_by('pk').first()
    created_trainer = trainer is None
    if created_trainer:
        trainer = User.objects.create_user('personal', TRAINER_EMAIL, password, is_staff=True, first_name='Camila')
        EmailAddress.objects.create(user=trainer, email=TRAINER_EMAIL, primary=True, verified=True)
    demo = Demo(password, trainer)
    load_catalog(demo)
    for build in STUDENTS:
        build(demo)
    for person in Person.objects.all():
        packages.sync(person)
    agenda.ensure_appointments(demo.today, demo.today + timedelta(weeks=4))
    WhatsAppTemplate.objects.update_or_create(kind=WhatsAppTemplate.Kind.CHARGE, defaults={
        'text': 'Olá {nome}! Tudo bem? Passando para lembrar do pagamento do {pacote} (faltam {valor}). Qualquer dúvida, é só falar. 💪',
        'updated_by': trainer})
    for person in Person.objects.all():
        packages.sync(person)
    summary = {'alunos': Person.objects.count(), 'pacotes': Package.objects.count(), 'exercicios': Exercise.objects.count(),
               'treinos_modelo': Routine.objects.count(), 'sessoes': WorkoutSession.objects.count(), 'aulas': Appointment.objects.count(),
               'pagamentos': Payment.objects.count()}
    summary['trainer'] = TRAINER_EMAIL if created_trainer else trainer.email or trainer.username
    summary['trainer_created'] = created_trainer
    return summary, password
