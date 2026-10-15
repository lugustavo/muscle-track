from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Exercise, Person, PersonRoutine, Plan, Routine
from core.onboarding import trainer_steps

from .base import BaseTestCase


class FirstStepsTests(BaseTestCase):
    """BaseTestCase already has exercises, a routine and students; tests remove what they need."""

    def empty_world(self):
        PersonRoutine.objects.all().delete()
        Person.objects.all().delete()
        User.objects.filter(is_staff=False).delete()
        Routine.objects.all().delete()
        Plan.objects.all().delete()
        Exercise.objects.all().delete()

    def test_empty_system_is_zero_of_six(self):
        self.empty_world()
        steps = trainer_steps()
        self.assertEqual((steps['done'], steps['total'], steps['percent']), (0, 6, 0))
        self.assertEqual(steps['next']['key'], 'exercises')
        self.assertEqual([s['next'] for s in steps['steps']], [True] + [False] * 5)

    def test_each_step_turns_done_when_its_data_exists(self):
        self.empty_world()
        done = lambda: {s['key'] for s in trainer_steps()['steps'] if s['done']}   # noqa: E731
        group = self.group
        exercise = Exercise.objects.create(exercise_name='Agachamento', muscle_group=group)
        self.assertEqual(done(), {'exercises'})
        Plan.objects.create(name='Mensal')
        self.assertEqual(done(), {'exercises', 'plan'})
        Routine.objects.create(routine_name='Modelo')
        self.assertEqual(done(), {'exercises', 'plan', 'routine'})
        user, routine = self._make_student('nova', 'Nova')
        PersonRoutine.objects.all().delete()
        user.set_unusable_password()
        user.save()
        self.assertEqual(done(), {'exercises', 'plan', 'routine', 'student'})
        pending = trainer_steps()
        self.assertEqual(pending['next']['key'], 'assign')
        PersonRoutine.objects.create(person=user.person, routine_name='Treino da Nova')
        self.assertEqual(done(), {'exercises', 'plan', 'routine', 'student', 'assign'})
        # the last step links to the invite of the first student who has no access yet
        access = trainer_steps()['next']
        self.assertEqual(access['key'], 'access')
        self.assertIn(reverse('core:student_invite', kwargs={'pk': user.person.pk}), access['url'])
        user.set_password('nova-senha-123')
        user.save()
        final = trainer_steps()
        self.assertTrue(final['complete'])
        self.assertEqual(final['done'], 6)
        self.assertIsNone(final['next'])
        self.assertTrue(exercise.pk)

    def test_the_trainer_is_not_counted_as_a_student(self):
        self.empty_world()
        Person.objects.create(user=self.trainer, person_name='Personal')
        self.assertFalse({s['key']: s['done'] for s in trainer_steps()['steps']}['student'])

    def test_links_carry_next_to_come_back_to_the_dashboard(self):
        self.empty_world()
        first = trainer_steps('/')['steps'][0]
        self.assertIn(reverse('core:exercise_create'), first['url'])
        self.assertIn('next=/', first['url'])
        self.assertEqual(trainer_steps()['steps'][0]['url'], reverse('core:exercise_create'))


class FirstStepsCardTests(BaseTestCase):
    def test_card_is_shown_until_everything_is_done(self):
        self.login(self.trainer)
        response = self.client.get(reverse('core:home'))
        # base world: exercises, routine, students with access exist; no plan yet
        self.assertEqual(response.context['onboarding']['done'], 5)
        self.assertContains(response, 'data-onboarding')
        self.assertContains(response, 'Primeiros passos')
        self.assertContains(response, 'data-onboarding-dismiss')
        Plan.objects.create(name='Mensal')
        response = self.client.get(reverse('core:home'))
        self.assertIsNone(response.context['onboarding'])
        self.assertNotContains(response, 'data-onboarding')

    def test_students_and_visitors_do_not_see_it(self):
        self.login(self.alice)
        self.assertNotContains(self.client.get(reverse('core:home')), 'data-onboarding')
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('core:home')), 'data-onboarding')
