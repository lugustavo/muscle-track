from django.test import Client
from django.urls import reverse

from core import sheet_link
from core.models import Exercise, PersonRoutine, PersonRoutineExercise, SheetLink, StudentEvent

from .base import BaseTestCase, page_text


class SheetBase(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.person.notes = 'ANOTACAO-INTERNA'
        self.person.health_notes = 'HERNIA-DE-DISCO'
        self.person.phone = '(11) 98765-4321'
        self.person.plan_price = 987
        self.person.save()
        PersonRoutineExercise.objects.filter(person_routine=self.alice_routine).update(rest_seconds=45)
        self.first = PersonRoutineExercise.objects.get(person_routine=self.alice_routine, order=1)
        self.first.notes = 'Cotovelos junto ao corpo'
        self.first.save()

    def create_link(self):
        return sheet_link.enable(self.person, self.trainer)

    def public(self, link=None, **kwargs):
        link = link or self.create_link()
        return Client().get(reverse('core:public_sheet', kwargs={'token': link.token}), **kwargs)


class PublicPageTests(SheetBase):
    def test_the_sheet_opens_without_login_and_lists_the_exercises(self):
        response = self.public()
        self.assertEqual(response.status_code, 200)
        html = page_text(response)
        self.assertIn('Ficha de treino de Alice', html)
        self.assertIn('Treino de Alice', html)
        self.assertEqual(html.count('data-sheet-exercise'), 2)
        for text in ('Supino', 'Crucifixo', 'Cotovelos junto ao corpo', 'descanso 45s'):
            self.assertIn(text, html)

    def test_nothing_private_is_there(self):
        html = page_text(self.public())
        for secret in ('ANOTACAO-INTERNA', 'HERNIA-DE-DISCO', '987', '98765', 'alice@example.com'):
            self.assertNotIn(secret, html)
        self.assertNotIn('Souza', html)

    def test_only_the_first_name_shows(self):
        self.person.person_name = 'Alice de Souza Lima'
        self.person.save()
        html = page_text(self.public())
        self.assertIn('Ficha de treino de Alice', html)
        self.assertNotIn('Lima', html)

    def test_headers_keep_it_private(self):
        response = self.public()
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertContains(response, 'name="robots"')

    def test_only_the_routines_in_use(self):
        old = PersonRoutine.objects.create(person=self.person, routine_name='Treino antigo', is_active=False)
        PersonRoutineExercise.objects.create(person_routine=old, exercise=self.supino, order=1)
        html = page_text(self.public())
        self.assertNotIn('Treino antigo', html)

    def test_no_routine_says_so(self):
        PersonRoutine.objects.filter(person=self.person).update(is_active=False)
        self.assertContains(self.public(), 'data-sheet-empty')

    def test_an_unknown_link_is_not_found(self):
        response = Client().get(reverse('core:public_sheet', kwargs={'token': 'nao-existe'}))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'data-sheet-invalid', status_code=404)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_a_paused_student_does_not_open(self):
        link = self.create_link()
        self.person.set_status('paused')
        self.assertEqual(Client().get(reverse('core:public_sheet', kwargs={'token': link.token})).status_code, 404)

    def test_the_page_only_reads(self):
        link = self.create_link()
        response = Client().post(reverse('core:public_sheet', kwargs={'token': link.token}))
        self.assertEqual(response.status_code, 405)

    def test_a_video_link_must_be_a_web_address(self):
        self.supino.media_url = 'javascript:alert(1)'
        self.supino.save()
        self.crucifixo.media_url = 'https://example.com/video'
        self.crucifixo.save()
        html = page_text(self.public())
        self.assertNotIn('javascript:', html)
        self.assertIn('https://example.com/video', html)

    def test_names_are_escaped(self):
        self.supino.exercise_name = '<script>alert(1)</script>'
        self.supino.save()
        html = page_text(self.public())
        self.assertNotIn('<script>alert(1)', html)
        self.assertIn('&lt;script&gt;', html)

    def test_view_stamp(self):
        link = self.create_link()
        self.assertIsNone(link.last_viewed_at)
        self.public(link)
        link.refresh_from_db()
        self.assertIsNotNone(link.last_viewed_at)

    def test_english(self):
        response = self.public(HTTP_ACCEPT_LANGUAGE='en')
        self.assertContains(response, 'Workout sheet for Alice')
        self.assertContains(response, 'rest 45s')

    def test_it_works_for_a_student_without_the_app(self):
        from core import app_access
        self.person.has_app = False
        self.person.save()
        app_access.apply(self.person, 'alice@example.com', True)
        self.assertEqual(self.public().status_code, 200)


class ManageTests(SheetBase):
    def setUp(self):
        super().setUp()
        self.login(self.trainer)
        self.url = reverse('core:student_sheet', kwargs={'pk': self.person.pk})

    def post(self, action):
        return self.client.post(self.url, {'action': action})

    def test_off_by_default_and_the_page_offers_to_create(self):
        self.assertContains(self.client.get(self.url), 'data-sheet-off')
        self.assertFalse(SheetLink.objects.exists())

    def test_create_shows_the_link_and_a_whatsapp_message(self):
        self.assertRedirects(self.post('enable'), self.url)
        link = SheetLink.objects.get(person=self.person)
        page = self.client.get(self.url)
        self.assertContains(page, 'data-sheet-link')
        self.assertContains(page, f'/ficha/{link.token}/')
        self.assertTrue(page.context['whatsapp_url'].startswith('https://wa.me/5511987654321?text='))
        self.assertEqual(self.person.events.filter(kind=StudentEvent.Kind.SHEET_LINK).count(), 1)

    def test_creating_twice_keeps_the_same_link(self):
        self.post('enable')
        token = SheetLink.objects.get().token
        self.post('enable')
        self.assertEqual(SheetLink.objects.get().token, token)

    def test_a_new_link_kills_the_old_one(self):
        self.post('enable')
        old = SheetLink.objects.get().token
        self.post('rotate')
        new = SheetLink.objects.get().token
        self.assertNotEqual(old, new)
        self.assertEqual(Client().get(reverse('core:public_sheet', kwargs={'token': old})).status_code, 404)
        self.assertEqual(Client().get(reverse('core:public_sheet', kwargs={'token': new})).status_code, 200)

    def test_turning_it_off_closes_the_link(self):
        self.post('enable')
        token = SheetLink.objects.get().token
        self.post('disable')
        self.assertFalse(SheetLink.objects.exists())
        self.assertEqual(Client().get(reverse('core:public_sheet', kwargs={'token': token})).status_code, 404)
        self.assertEqual(self.person.events.filter(kind=StudentEvent.Kind.SHEET_LINK).count(), 2)

    def test_rotate_or_disable_without_a_link_is_refused(self):
        self.assertEqual(self.post('rotate').status_code, 400)
        self.assertEqual(self.post('disable').status_code, 400)
        self.assertEqual(self.post('whatever').status_code, 400)

    def test_warns_when_there_is_no_routine(self):
        PersonRoutine.objects.filter(person=self.person).update(is_active=False)
        self.assertContains(self.client.get(self.url), 'data-sheet-no-routine')

    def test_only_the_trainer_manages_it(self):
        anonymous = Client()
        self.assertEqual(anonymous.get(self.url).status_code, 302)
        self.login(self.alice)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'action': 'enable'}).status_code, 403)
        self.assertFalse(SheetLink.objects.exists())

    def test_the_student_file_links_to_it(self):
        html = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).content.decode()
        self.assertIn(self.url, html)
        self.assertIn('data-sheet-link-button', html)

    def test_deleting_the_student_deletes_the_link(self):
        self.post('enable')
        self.alice.delete()
        self.assertFalse(SheetLink.objects.exists())

    def test_tokens_are_long_and_unique(self):
        tokens = {sheet_link.new_token() for _ in range(20)}
        self.assertEqual(len(tokens), 20)
        self.assertTrue(all(len(t) >= 40 for t in tokens))

    def test_exercise_library_is_untouched(self):
        self.post('enable')
        self.assertEqual(Exercise.objects.count(), 2)
