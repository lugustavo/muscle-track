from django.contrib.auth.models import User
from django.test import TestCase

from core.consent import ALL_KINDS, record_consent
from core.models import (
    Exercise, MuscleGroup, Person, PersonRoutine, PersonRoutineExercise, Routine,
    RoutineExercise,
)


class BaseTestCase(TestCase):
    """Shared fixtures: one trainer, two students, one routine each."""

    @classmethod
    def setUpTestData(cls):
        cls.trainer = User.objects.create_user('trainer', 'trainer@example.com', 'pw-trainer', is_staff=True)

        cls.group, _ = MuscleGroup.objects.get_or_create(slug='peito', defaults={'name': 'Peito'})
        cls.supino = Exercise.objects.create(exercise_name='Supino', muscle_group=cls.group)
        cls.crucifixo = Exercise.objects.create(exercise_name='Crucifixo', muscle_group=cls.group)
        cls.template = Routine.objects.create(routine_name='Treino A')
        RoutineExercise.objects.create(routine=cls.template, exercise=cls.supino, order=1)

        cls.alice, cls.alice_routine = cls._make_student('alice', 'Alice')
        cls.bob, cls.bob_routine = cls._make_student('bob', 'Bob')

    @classmethod
    def _make_student(cls, username, name):
        user = User.objects.create_user(username, f'{username}@example.com', f'pw-{username}')
        person = Person.objects.create(user=user, person_name=name)
        for kind in ALL_KINDS:
            record_consent(person, kind, True)
        routine = PersonRoutine.objects.create(person=person, routine_name=f'Treino de {name}')
        PersonRoutineExercise.objects.create(person_routine=routine, exercise=cls.supino, order=1)
        PersonRoutineExercise.objects.create(person_routine=routine, exercise=cls.crucifixo, order=2)
        return user, routine

    def login(self, user):
        self.client.force_login(user)
