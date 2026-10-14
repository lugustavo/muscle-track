import json

from django.urls import reverse

from core.models import (
    PersonRoutine, Routine, RoutineExercise, SessionExercise,
    WorkoutSession,
)

from .base import BaseTestCase


class PersonRoutineEditTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.url = reverse('core:person_routine_edit', kwargs={'pk': self.alice_routine.pk})

        # Alice already trained once: there is history attached to her routine rows
        self.session = WorkoutSession.objects.create(person_routine=self.alice_routine)
        self.supino_row = self.alice_routine.exercises.get(exercise=self.supino)
        self.crucifixo_row = self.alice_routine.exercises.get(exercise=self.crucifixo)
        self.supino_log = SessionExercise.objects.create(
            session=self.session, person_routine_exercise=self.supino_row, weight_used=30)
        self.crucifixo_log = SessionExercise.objects.create(
            session=self.session, person_routine_exercise=self.crucifixo_row, weight_used=10)

    def post(self, items, **extra):
        data = {
            'person': self.alice.person.pk,
            'routine_name': 'Treino de Alice',
            'difficulty_level': 'intermediario',
            'estimated_duration': 60,
            'routine_date': '2026-01-01',
            'exercises_data': json.dumps(items) if not isinstance(items, str) else items,
        }
        data.update(extra)
        return self.client.post(self.url, data)

    def test_editing_keeps_history_of_exercises_that_stay(self):
        response = self.post([
            {'exercise_id': self.crucifixo.pk, 'sets': 4, 'reps': 8, 'rest_seconds': 90},
            {'exercise_id': self.supino.pk, 'sets': 5, 'reps': 5, 'rest_seconds': 120, 'notes': 'pesado'},
        ])
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': self.alice.person.pk}))

        # Same rows, updated in place and reordered; logs are untouched
        self.supino_row.refresh_from_db()
        self.assertEqual((self.supino_row.order, self.supino_row.sets, self.supino_row.notes), (2, 5, 'pesado'))
        self.crucifixo_row.refresh_from_db()
        self.assertEqual((self.crucifixo_row.order, self.crucifixo_row.reps), (1, 8))
        self.assertTrue(SessionExercise.objects.filter(pk=self.supino_log.pk).exists())
        self.assertTrue(SessionExercise.objects.filter(pk=self.crucifixo_log.pk).exists())

    def test_removed_exercise_is_deleted_and_new_one_added(self):
        self.post([{'exercise_id': self.supino.pk}])
        self.assertEqual(list(self.alice_routine.exercises.values_list('exercise_id', flat=True)),
                         [self.supino.pk])
        self.assertTrue(SessionExercise.objects.filter(pk=self.supino_log.pk).exists())
        self.assertFalse(SessionExercise.objects.filter(pk=self.crucifixo_log.pk).exists())

    def test_invalid_payload_changes_nothing(self):
        for payload in ('{not json', '{"a": 1}', [{'exercise_id': 'x'}], [{'exercise_id': 999999}]):
            with self.subTest(payload=payload):
                response = self.post(payload, routine_name='Nome novo')
                self.assertEqual(response.status_code, 200)  # form re-rendered with an error
                self.alice_routine.refresh_from_db()
                self.assertEqual(self.alice_routine.routine_name, 'Treino de Alice')
                self.assertEqual(self.alice_routine.exercises.count(), 2)

    def test_deactivate_requires_post(self):
        url = reverse('core:person_routine_deactivate', kwargs={'pk': self.alice_routine.pk})
        self.client.get(url)
        self.alice_routine.refresh_from_db()
        self.assertTrue(self.alice_routine.is_active)
        self.client.post(url)
        self.alice_routine.refresh_from_db()
        self.assertFalse(self.alice_routine.is_active)


class AssignRoutineTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)

    def test_assign_template_creates_personal_copy(self):
        response = self.client.post(reverse('core:person_routine_create'), {
            'person': self.alice.person.pk,
            'routine_name': 'Treino A da Alice',
            'difficulty_level': 'iniciante',
            'estimated_duration': 45,
            'routine_date': '2026-01-01',
            'template_id': self.template.pk,
            'exercises_data': json.dumps([
                {'exercise_id': self.supino.pk, 'sets': 3, 'reps': 10, 'rest_seconds': 60},
            ]),
        })
        self.assertEqual(response.status_code, 302)
        created = PersonRoutine.objects.get(routine_name='Treino A da Alice')
        self.assertEqual(created.routine_template, self.template)
        self.assertEqual(created.exercises.count(), 1)

    def test_invalid_form_rerenders_without_crashing(self):
        response = self.client.post(reverse('core:person_routine_create'), {
            'person': self.alice.person.pk, 'routine_name': '', 'exercises_data': '[]'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_person'], self.alice.person)

    def test_bad_payload_rolls_back_the_new_routine(self):
        before = PersonRoutine.objects.count()
        self.client.post(reverse('core:person_routine_create'), {
            'person': self.alice.person.pk,
            'routine_name': 'X',
            'difficulty_level': 'iniciante',
            'estimated_duration': 45,
            'routine_date': '2026-01-01',
            'template_id': 'abc',
            'exercises_data': '[{"exercise_id": 424242}]',
        })
        self.assertEqual(PersonRoutine.objects.count(), before)


class TemplateRoutineTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)

    def test_create_and_edit_template(self):
        data = {
            'routine_name': 'Treino B', 'difficulty_level': 'avancado',
            'estimated_duration': 50, 'is_active': 'on',
            'exercises_data': json.dumps([{'id': self.supino.pk, 'sets': 4, 'reps': 8, 'rest': 90},
                                           {'id': self.crucifixo.pk}]),
        }
        response = self.client.post(reverse('core:routine_create'), data)
        routine = Routine.objects.get(routine_name='Treino B')
        self.assertRedirects(response, reverse('core:routine_detail', kwargs={'pk': routine.pk}))
        self.assertEqual(
            list(routine.exercises.values_list('exercise_id', 'order', 'sets')),
            [(self.supino.pk, 1, 4), (self.crucifixo.pk, 2, self.crucifixo.default_sets)])

        data['exercises_data'] = json.dumps([{'id': self.crucifixo.pk}])
        self.client.post(reverse('core:routine_edit', kwargs={'pk': routine.pk}), data)
        self.assertEqual(RoutineExercise.objects.filter(routine=routine).count(), 1)

    def test_invalid_payload_does_not_create_routine(self):
        self.client.post(reverse('core:routine_create'), {
            'routine_name': 'Quebrado', 'difficulty_level': 'avancado', 'estimated_duration': 50,
            'exercises_data': '[{"id": 999999}]',
        })
        self.assertFalse(Routine.objects.filter(routine_name='Quebrado').exists())
