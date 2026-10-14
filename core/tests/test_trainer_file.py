import io
import os
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.dashboard import expiring_plans, inactive_students, recent_comments, trainer_dashboard
from core.models import (
    Assessment, Person, PersonRoutine, ProgressPhoto, SessionExercise, StudentNote, WorkoutSession,
)

from .base import BaseTestCase

MEDIA = tempfile.mkdtemp(prefix='mt-test-media-')


def photo_upload(name='front.jpg', size=(800, 1200)):
    buffer = io.BytesIO()
    Image.new('RGB', size, 'blue').save(buffer, 'JPEG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


class PlanStatusTests(BaseTestCase):
    def test_statuses(self):
        p = self.alice.person
        today = timezone.localdate()
        self.assertEqual(p.plan_status, 'none')
        p.plan_expires_on = today + timedelta(days=60)
        self.assertEqual(p.plan_status, 'ok')
        p.plan_expires_on = today + timedelta(days=14)
        self.assertEqual(p.plan_status, 'expiring')
        p.plan_expires_on = today
        self.assertEqual((p.plan_status, p.days_to_plan_expiry), ('expiring', 0))
        p.plan_expires_on = today - timedelta(days=1)
        self.assertEqual(p.plan_status, 'expired')


class FilePermissionTests(BaseTestCase):
    def urls(self):
        a = Assessment.objects.create(person=self.alice.person, weight_kg=60)
        n = StudentNote.objects.create(person=self.alice.person, text='x')
        return {
            'get': [reverse('core:assessment_create', kwargs={'pk': self.alice.person.pk}),
                    reverse('core:assessment_edit', kwargs={'pk': a.pk}),
                    reverse('core:photo_upload', kwargs={'pk': self.alice.person.pk})],
            'post': [reverse('core:assessment_delete', kwargs={'pk': a.pk}),
                     reverse('core:note_add', kwargs={'pk': self.alice.person.pk}),
                     reverse('core:note_delete', kwargs={'pk': n.pk})],
        }

    def test_students_are_forbidden_everywhere(self):
        urls = self.urls()
        self.login(self.alice)
        for url in urls['get']:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        for url in urls['post']:
            self.assertEqual(self.client.post(url, {}).status_code, 403, url)
        self.assertEqual(Assessment.objects.count(), 1)
        self.assertEqual(StudentNote.objects.count(), 1)

    def test_anonymous_goes_to_login(self):
        for url in self.urls()['get']:
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_trainer_can_open_pages(self):
        self.login(self.trainer)
        for url in self.urls()['get']:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_destructive_actions_need_post(self):
        self.login(self.trainer)
        a = Assessment.objects.create(person=self.alice.person, weight_kg=60)
        self.assertEqual(self.client.get(reverse('core:assessment_delete', kwargs={'pk': a.pk})).status_code, 405)
        self.assertTrue(Assessment.objects.filter(pk=a.pk).exists())


class StudentFilePageTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.person = self.alice.person

    def test_edit_saves_trainer_fields(self):
        response = self.client.post(reverse('core:student_edit', kwargs={'pk': self.person.pk}), {
            'person_name': 'Alice', 'email': 'alice@example.com', 'is_active': 'on',
            'goal': 'Emagrecer 8 kg', 'health_notes': 'Hérnia L5', 'emergency_contact_name': 'Mãe',
            'emergency_contact_phone': '(11) 90000-0000', 'plan_name': 'Mensal 3x', 'plan_expires_on': '2030-01-31',
        })
        self.assertEqual(response.status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual((self.person.goal, self.person.health_notes, self.person.plan_name),
                         ('Emagrecer 8 kg', 'Hérnia L5', 'Mensal 3x'))
        detail = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(detail, 'Hérnia L5')  # shown prominently

    def test_student_cannot_edit_trainer_fields_in_own_profile(self):
        self.login(self.alice)
        self.client.post(reverse('core:my_profile'), {
            'person_name': 'Alice', 'health_notes': 'hack', 'goal': 'hack', 'plan_expires_on': '2099-01-01'})
        self.person.refresh_from_db()
        self.assertEqual((self.person.health_notes, self.person.goal, self.person.plan_expires_on), ('', '', None))

    def test_detail_tabs_render_with_data(self):
        Assessment.objects.create(person=self.person, date=timezone.localdate() - timedelta(days=30), weight_kg=70, body_fat_pct=25)
        Assessment.objects.create(person=self.person, weight_kg=68, height_cm=170, body_fat_pct=23)
        StudentNote.objects.create(person=self.person, author=self.trainer, text='Voltou de viagem')
        response = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['assessment_chart']), 2)
        self.assertEqual(response.context['latest_assessment'].weight_kg, Decimal('68'))
        self.assertContains(response, 'Voltou de viagem')


class AssessmentTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.person = self.alice.person

    def test_create_edit_delete(self):
        url = reverse('core:assessment_create', kwargs={'pk': self.person.pk})
        response = self.client.post(url, {'date': '2026-03-01', 'weight_kg': '70.5', 'height_cm': '170', 'waist_cm': '80'})
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': self.person.pk}) + '#tab-avaliacoes',
                             fetch_redirect_response=False)
        a = Assessment.objects.get()
        self.assertEqual((a.person, a.weight_kg, a.bmi), (self.person, Decimal('70.50'), 24.4))

        self.client.post(reverse('core:assessment_edit', kwargs={'pk': a.pk}), {'date': '2026-03-01', 'weight_kg': '69'})
        a.refresh_from_db()
        self.assertEqual(a.weight_kg, Decimal('69.00'))
        self.assertIsNone(a.height_cm)  # cleared field

        self.client.post(reverse('core:assessment_delete', kwargs={'pk': a.pk}))
        self.assertFalse(Assessment.objects.exists())

    def test_needs_at_least_one_measure_and_sane_values(self):
        url = reverse('core:assessment_create', kwargs={'pk': self.person.pk})
        for data in ({'date': '2026-03-01'}, {'date': '2026-03-01', 'body_fat_pct': '150'},
                     {'date': '2026-03-01', 'weight_kg': '-3'}, {'date': 'xx', 'weight_kg': '60'}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(url, data).status_code, 200)
        self.assertFalse(Assessment.objects.exists())

    def test_bmi_needs_weight_and_height(self):
        self.assertIsNone(Assessment(person=self.person, weight_kg=60).bmi)


@override_settings(MEDIA_ROOT=MEDIA)
class ProgressPhotoTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.person = self.alice.person

    def upload(self, **kw):
        return self.client.post(reverse('core:photo_upload', kwargs={'pk': self.person.pk}),
                                {'date': '2026-03-01', 'image': photo_upload(), 'caption': 'frente', **kw})

    def test_upload_creates_private_file_and_thumbnail(self):
        response = self.upload()
        self.assertEqual(response.status_code, 302)
        photo = ProgressPhoto.objects.get()
        self.assertTrue(photo.image.name.startswith(f'progress/{self.person.pk}/'))
        self.assertNotIn('front', photo.image.name)  # original name is not exposed
        with Image.open(photo.thumbnail.path) as thumb:
            self.assertLessEqual(max(thumb.size), 480)

    def test_invalid_uploads_are_rejected(self):
        for bad in (SimpleUploadedFile('x.jpg', b'not an image'), photo_upload(name='x.exe'), photo_upload(size=(20, 20))):
            with self.subTest(name=bad.name):
                self.assertEqual(self.upload(image=bad).status_code, 200)
        self.assertFalse(ProgressPhoto.objects.exists())

    def test_only_trainer_and_owner_can_open_photo(self):
        self.upload()
        photo = ProgressPhoto.objects.get()
        full = reverse('core:photo_file', kwargs={'pk': photo.pk})
        thumb = reverse('core:photo_thumb', kwargs={'pk': photo.pk})

        for who, expected in ((self.trainer, 200), (self.alice, 200), (self.bob, 403)):
            self.login(who)
            for url in (full, thumb):
                self.assertEqual(self.client.get(url).status_code, expected, (who, url))
        self.client.logout()
        self.assertEqual(self.client.get(full).status_code, 302)

        self.login(self.trainer)
        response = self.client.get(thumb)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertIn('private', response['Cache-Control'])

    def test_media_url_does_not_serve_private_photos(self):
        self.upload()
        photo = ProgressPhoto.objects.get()
        self.client.logout()
        self.assertEqual(self.client.get('/media/' + photo.image.name).status_code, 404)
        self.assertEqual(self.client.get('/media/' + photo.thumbnail.name).status_code, 404)

    def test_delete_removes_files(self):
        self.upload()
        photo = ProgressPhoto.objects.get()
        paths = [photo.image.path, photo.thumbnail.path]
        self.assertTrue(all(os.path.exists(p) for p in paths))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('core:photo_delete', kwargs={'pk': photo.pk}))
        self.assertFalse(ProgressPhoto.objects.exists())
        self.assertFalse(any(os.path.exists(p) for p in paths))


class NoteTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)

    def test_add_and_delete(self):
        url = reverse('core:note_add', kwargs={'pk': self.alice.person.pk})
        self.client.post(url, {'text': 'Treina melhor de manhã'})
        note = StudentNote.objects.get()
        self.assertEqual((note.author, note.person), (self.trainer, self.alice.person))
        self.client.post(url, {'text': '   '})
        self.assertEqual(StudentNote.objects.count(), 1)
        self.client.post(reverse('core:note_delete', kwargs={'pk': note.pk}))
        self.assertFalse(StudentNote.objects.exists())

    def test_students_never_see_trainer_notes(self):
        StudentNote.objects.create(person=self.alice.person, author=self.trainer, text='segredo-da-personal')
        self.login(self.alice)
        for name in ('core:my_workouts', 'core:my_history', 'core:my_profile', 'core:home'):
            self.assertNotContains(self.client.get(reverse(name)), 'segredo-da-personal')


class DashboardTests(BaseTestCase):
    def finished_session(self, routine, days_ago):
        s = WorkoutSession.objects.create(person_routine=routine)
        WorkoutSession.objects.filter(pk=s.pk).update(start_time=timezone.now() - timedelta(days=days_ago))
        s.refresh_from_db()
        s.end_session()
        return s

    def test_inactive_students(self):
        self.finished_session(self.alice_routine, days_ago=10)
        self.finished_session(self.bob_routine, days_ago=2)
        rows = inactive_students()
        self.assertEqual([r['student'] for r in rows], [self.alice.person])
        self.assertGreaterEqual(rows[0]['days'], 10)

    def test_never_trained_counts_only_after_a_week_since_signup(self):
        Person.objects.filter(pk=self.alice.person.pk).update(created_at=timezone.now() - timedelta(days=20))
        self.finished_session(self.bob_routine, days_ago=1)
        self.assertEqual([r['student'] for r in inactive_students()], [self.alice.person])
        self.assertIsNone(inactive_students()[0]['last_session_at'])

    def test_students_without_routine_or_inactive_are_ignored(self):
        Person.objects.filter(pk=self.alice.person.pk).update(created_at=timezone.now() - timedelta(days=20))
        Person.objects.filter(pk=self.bob.person.pk).update(created_at=timezone.now() - timedelta(days=20))
        PersonRoutine.objects.filter(pk=self.alice_routine.pk).update(is_active=False)
        Person.objects.filter(pk=self.bob.person.pk).update(is_active=False)
        self.assertEqual(inactive_students(), [])

    def test_expiring_plans(self):
        today = timezone.localdate()
        Person.objects.filter(pk=self.alice.person.pk).update(plan_expires_on=today - timedelta(days=3))
        Person.objects.filter(pk=self.bob.person.pk).update(plan_expires_on=today + timedelta(days=40))
        self.assertEqual(expiring_plans(), [Person.objects.get(pk=self.alice.person.pk)])

    def test_comments_and_dashboard_page(self):
        session = WorkoutSession.objects.create(person_routine=self.alice_routine, notes='Ombro doeu')
        log = SessionExercise.objects.create(
            session=session, person_routine_exercise=self.alice_routine.exercises.first(), notes='Carga leve demais')
        texts = [c['text'] for c in recent_comments()]
        self.assertCountEqual(texts, ['Ombro doeu', 'Carga leve demais'])
        self.assertIsNotNone(log)

        data = trainer_dashboard()
        self.assertEqual(data['sessions_total'], 1)
        self.login(self.trainer)
        page = self.client.get(reverse('core:home'))
        self.assertContains(page, 'Painel de hoje')
        self.assertContains(page, 'Ombro doeu')

    def test_home_for_other_roles(self):
        anon = self.client.get(reverse('core:home'))
        self.assertContains(anon, 'Entrar')
        self.assertNotContains(anon, 'Alunos ativos')  # no numbers for visitors
        self.assertNotContains(anon, reverse('account_signup'))
        self.login(self.alice)
        self.assertContains(self.client.get(reverse('core:home')), 'Meus Treinos')
