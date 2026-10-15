"""First steps for a new trainer: what to set up, in order, and how far along it is."""
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.urls import reverse

from .models import Exercise, Person, PersonRoutine, Package, Routine
from .navigation import with_next


def trainer_steps(here=None):
    """The setup steps as ``{'steps': [...], 'done': n, 'total': n, 'complete': bool, 'next': step or None}``.

    `here` (the current path) becomes ``?next=`` of the links so Back returns to the dashboard."""
    def link(name, *args):
        url = reverse(name, args=args or None)
        return with_next(url, here) if here else url

    students = Person.objects.filter(user__is_staff=False, user__is_superuser=False)
    pending = students.filter(user__password__startswith=UNUSABLE_PASSWORD_PREFIX).order_by('created_at').first()
    first_student = students.order_by('created_at').first()
    has_students = first_student is not None
    has_access = students.exclude(user__password__startswith=UNUSABLE_PASSWORD_PREFIX).exists()

    steps = [
        {'key': 'exercises', 'title': 'Cadastre seus exercícios', 'hint': 'A biblioteca que você usa para montar os treinos.',
         'done': Exercise.objects.filter(is_active=True).exists(), 'url': link('core:exercise_create'), 'action': 'Novo exercício'},
        {'key': 'plan', 'title': 'Crie um plano', 'hint': 'Modelo com periodicidade e valor, para usar com vários alunos.',
         'done': Package.objects.exists(), 'url': link('core:plan_create'), 'action': 'Novo plano'},
        {'key': 'routine', 'title': 'Monte um treino', 'hint': 'Um treino modelo com os exercícios, séries e repetições.',
         'done': Routine.objects.exists(), 'url': link('core:routine_create'), 'action': 'Novo treino'},
        {'key': 'student', 'title': 'Cadastre o primeiro aluno', 'hint': 'Nome e e-mail bastam; o resto pode vir depois.',
         'done': has_students, 'url': link('core:student_create'), 'action': 'Novo aluno'},
        {'key': 'assign', 'title': 'Atribua um treino a um aluno', 'hint': 'Sem treino atribuído, o aluno não tem o que fazer no app.',
         'done': PersonRoutine.objects.filter(person__in=students).exists(), 'url': link('core:person_routine_create'),
         'action': 'Atribuir treino'},
        {'key': 'access', 'title': 'Envie o convite de acesso', 'hint': 'O aluno cria a própria senha pelo link e já pode treinar.',
         'done': has_access,
         'url': link('core:student_invite', pending.pk) if pending else (link('core:student_list')),
         'action': 'Enviar convite' if pending else 'Ver alunos'},
    ]
    done = sum(1 for s in steps if s['done'])
    next_step = next((s for s in steps if not s['done']), None)
    for step in steps:
        step['next'] = step is next_step
    return {'steps': steps, 'done': done, 'total': len(steps), 'complete': next_step is None,
            'percent': round(100 * done / len(steps)), 'next': next_step}
