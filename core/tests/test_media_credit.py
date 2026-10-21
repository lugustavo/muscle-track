from django.test import Client
from django.urls import reverse

from core import credits
from core.models import Exercise

from .base import BaseTestCase


class CreditLabelTests(BaseTestCase):
    def test_the_label_joins_what_is_known(self):
        exercise = Exercise(exercise_name='X', muscle_group=self.group, media_credit='Maria Silva', media_license='cc-by-sa')
        self.assertEqual(credits.label(exercise), 'Maria Silva - CC BY-SA')
        exercise.media_credit = ''
        self.assertEqual(credits.label(exercise), 'CC BY-SA')
        exercise.media_license, exercise.media_credit = '', '  Everkinetic '
        self.assertEqual(credits.label(exercise), 'Everkinetic')
        exercise.media_credit = ''
        self.assertEqual(credits.label(exercise), '')
        self.assertFalse(exercise.has_credit)

    def test_only_a_link_still_counts_as_a_credit(self):
        exercise = Exercise(exercise_name='X', muscle_group=self.group, media_source_url='https://example.org/x')
        self.assertTrue(exercise.has_credit)


class ExerciseFormCreditTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)

    def data(self, **extra):
        return {'exercise_name': 'Remada', 'muscle_group': self.group.pk, 'default_reps': 12, 'default_sets': 3,
                'media_type': 'image', 'is_active': 'on', **extra}

    def test_the_credit_is_saved_with_the_exercise(self):
        response = self.client.post(reverse('core:exercise_create'), self.data(
            media_credit='Everkinetic', media_license='cc-by-sa', media_source_url='https://example.org/remada'))
        self.assertEqual(response.status_code, 302)
        exercise = Exercise.objects.get(exercise_name='Remada')
        self.assertEqual((exercise.media_credit, exercise.media_license, exercise.media_source_url),
                         ('Everkinetic', 'cc-by-sa', 'https://example.org/remada'))

    def test_the_credit_is_optional(self):
        self.client.post(reverse('core:exercise_create'), self.data())
        self.assertFalse(Exercise.objects.get(exercise_name='Remada').has_credit)

    def test_an_unknown_licence_or_a_bad_link_is_refused(self):
        for extra in ({'media_license': 'all-rights'}, {'media_source_url': 'javascript:alert(1)'}):
            response = self.client.post(reverse('core:exercise_create'), self.data(**extra))
            self.assertEqual(response.status_code, 200, extra)
            self.assertFalse(Exercise.objects.filter(exercise_name='Remada').exists(), extra)

    def test_the_form_offers_the_credit_fields_with_the_current_values(self):
        self.supino.media_credit, self.supino.media_license = 'Ana', 'own'
        self.supino.save()
        html = self.client.get(reverse('core:exercise_edit', kwargs={'pk': self.supino.pk})).content.decode()
        self.assertIn('data-media-credit', html)
        self.assertIn('value="Ana"', html)
        self.assertIn('<option value="own" selected>Produção própria</option>', html)


class WhereTheCreditShowsTests(BaseTestCase):
    def setUp(self):
        self.supino.media_credit, self.supino.media_license = 'Maria <b>Silva</b>', 'cc-by'
        self.supino.media_source_url = 'https://example.org/supino'
        self.supino.save()

    def test_the_exercise_page_shows_it_with_the_link_and_escaped(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:exercise_detail', kwargs={'pk': self.supino.pk})).content.decode()
        self.assertIn('Mídia: Maria &lt;b&gt;Silva&lt;/b&gt; - CC BY', html)
        self.assertNotIn('<b>Silva</b>', html)
        self.assertIn('href="https://example.org/supino"', html)

    def test_a_picture_with_no_credit_is_flagged_to_the_trainer(self):
        self.login(self.trainer)
        self.crucifixo.media_url = 'https://example.org/c.jpg'
        self.crucifixo.save()
        html = self.client.get(reverse('core:exercise_detail', kwargs={'pk': self.crucifixo.pk})).content.decode()
        self.assertIn('data-media-no-credit', html)
        self.assertNotIn('data-media-no-credit', self.client.get(reverse('core:exercise_detail', kwargs={'pk': self.supino.pk})).content.decode())

    def test_the_students_workout_screen_links_to_the_credits(self):
        self.login(self.alice)
        from core.models import WorkoutSession
        WorkoutSession.objects.create(person_routine=self.alice_routine)
        html = self.client.get(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk})).content.decode()
        self.assertEqual(html.count('data-media-credit'), 1)         # only the exercise that has a credit
        self.assertIn(reverse('core:credits'), html)

    def test_the_public_sheet_credits_the_pictures(self):
        from core.models import SheetLink
        SheetLink.objects.create(person=self.alice.person, token='tok123')
        self.supino.thumbnail = 'exercises/thumbnails/supino.jpg'
        self.supino.save()
        html = Client().get(reverse('core:public_sheet', kwargs={'token': 'tok123'})).content.decode()
        self.assertIn('sheet-credit', html)
        self.assertIn('Mídia: Maria &lt;b&gt;Silva&lt;/b&gt; - CC BY', html)

    def test_the_footer_links_to_the_credits_for_everyone(self):
        self.assertContains(Client().get(reverse('account_login')), reverse('core:credits'))


class CreditsPageTests(BaseTestCase):
    def test_it_is_public_and_lists_only_active_credited_exercises(self):
        self.supino.media_credit, self.supino.media_license = 'Ana', 'own'
        self.supino.media_source_url = 'https://example.org/supino'
        self.supino.save()
        self.crucifixo.media_credit, self.crucifixo.is_active = 'Inativo', False
        self.crucifixo.save()
        response = Client().get(reverse('core:credits'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count('data-credit-row'), 1)
        self.assertIn('Supino', html)
        self.assertIn('Ana - Produção própria', html)
        self.assertIn('href="https://example.org/supino"', html)
        self.assertNotIn('Crucifixo', html)

    def test_it_says_so_when_there_are_no_credits(self):
        self.assertContains(Client().get(reverse('core:credits')), 'data-credits-empty')

    def test_it_is_read_only(self):
        self.assertEqual(Client().post(reverse('core:credits')).status_code, 405)
