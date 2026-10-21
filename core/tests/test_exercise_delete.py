import shutil
import tempfile

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse

from core.models import Exercise, PersonRoutineExercise, Routine, RoutineExercise

from .base import BaseTestCase
from .test_i18n_english_pages import leaks, visible


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='mt-media-'))
class ExerciseDeleteTests(BaseTestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.login(self.trainer)
        self.free = Exercise.objects.create(exercise_name='Prancha', muscle_group=self.group)

    def url(self, exercise):
        return reverse('core:exercise_delete', kwargs={'pk': exercise.pk})

    # ---- who ----

    def test_only_the_trainer_can_use_it(self):
        self.login(self.alice)
        self.assertEqual(self.client.get(self.url(self.free)).status_code, 403)
        self.assertEqual(self.client.post(self.url(self.free)).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url(self.free)).status_code, 302)
        self.assertTrue(Exercise.objects.filter(pk=self.free.pk).exists())

    # ---- an exercise nobody uses ----

    def test_an_unused_exercise_asks_for_confirmation_and_is_deleted(self):
        page = self.client.get(self.url(self.free))
        self.assertContains(page, 'Excluir definitivamente')
        self.assertContains(page, 'Nenhum treino usa este exercício')
        self.assertTrue(Exercise.objects.filter(pk=self.free.pk).exists())    # a GET never deletes
        response = self.client.post(self.url(self.free))
        self.assertRedirects(response, reverse('core:exercise_list'), fetch_redirect_response=False)
        self.assertFalse(Exercise.objects.filter(pk=self.free.pk).exists())
        self.assertContains(self.client.get(reverse('core:exercise_list')), 'Exercício &quot;Prancha&quot; excluído.')

    def test_its_picture_goes_with_it(self):
        self.free.media_file.save('x.gif', ContentFile(b'GIF89a'), save=True)
        path = self.free.media_file.path
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url(self.free))
        import os
        self.assertFalse(os.path.exists(path))

    # ---- in routine templates only ----

    def test_an_exercise_only_in_templates_leaves_them_and_the_templates_stay(self):
        template = Routine.objects.create(routine_name='Core')
        RoutineExercise.objects.create(routine=template, exercise=self.free, order=1)
        self.assertContains(self.client.get(self.url(self.free)), 'retirado de <strong>1</strong> modelo de treino')
        self.client.post(self.url(self.free))
        self.assertFalse(Exercise.objects.filter(pk=self.free.pk).exists())
        self.assertTrue(Routine.objects.filter(pk=template.pk).exists())
        self.assertFalse(RoutineExercise.objects.filter(routine=template).exists())

    # ---- with history: it can only be deactivated ----

    def test_an_exercise_in_a_students_workout_cannot_be_deleted(self):
        page = self.client.get(self.url(self.supino))
        self.assertContains(page, 'não pode ser excluído')
        self.assertNotContains(page, 'Excluir definitivamente')
        self.assertContains(page, 'Desativar exercício')
        self.assertContains(page, '2 alunos')
        before = PersonRoutineExercise.objects.filter(exercise=self.supino).count()
        response = self.client.post(self.url(self.supino))                  # even a forged POST is refused
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Exercise.objects.filter(pk=self.supino.pk).exists())
        self.assertEqual(PersonRoutineExercise.objects.filter(exercise=self.supino).count(), before)
        self.assertContains(self.client.get(response.url), 'já está nos treinos de alunos')

    def test_deactivating_hides_it_and_keeps_the_history(self):
        before = PersonRoutineExercise.objects.filter(exercise=self.supino).count()
        response = self.client.post(self.url(self.supino), {'action': 'deactivate'})
        self.assertRedirects(response, reverse('core:exercise_detail', kwargs={'pk': self.supino.pk}), fetch_redirect_response=False)
        self.supino.refresh_from_db()
        self.assertFalse(self.supino.is_active)
        self.assertEqual(PersonRoutineExercise.objects.filter(exercise=self.supino).count(), before)
        self.assertNotContains(self.client.get(reverse('core:exercise_list')), 'Supino')

    def test_an_exercise_already_deactivated_says_so(self):
        self.supino.is_active = False
        self.supino.save()
        page = self.client.get(self.url(self.supino))
        self.assertContains(page, 'já está desativado')
        self.assertNotContains(page, 'Desativar exercício')

    def test_after_the_students_routine_goes_the_exercise_can_be_deleted(self):
        PersonRoutineExercise.objects.filter(exercise=self.crucifixo).delete()
        self.assertContains(self.client.get(self.url(self.crucifixo)), 'Excluir definitivamente')
        self.client.post(self.url(self.crucifixo))
        self.assertFalse(Exercise.objects.filter(pk=self.crucifixo.pk).exists())

    # ---- the way in ----

    def test_the_exercise_page_links_to_it_and_the_trail_is_right(self):
        detail = reverse('core:exercise_detail', kwargs={'pk': self.free.pk})
        self.assertContains(self.client.get(detail), self.url(self.free))
        page = self.client.get(self.url(self.free)).content.decode()
        for label in ('Exercícios', 'Prancha', 'Excluir'):
            self.assertIn(label, page)

    def test_the_page_is_in_english(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'
        for exercise in (self.free, self.supino):
            page = self.client.get(self.url(exercise))
            self.assertEqual(page.status_code, 200)
            self.assertEqual(leaks(visible(page)), [], exercise.exercise_name)
        self.assertContains(self.client.get(self.url(self.free)), 'Delete permanently')
