from django.urls import reverse

from core.models import WorkoutSession

from .base import BaseTestCase


class SessionBackNavigationTests(BaseTestCase):
    def setUp(self):
        self.session = WorkoutSession.objects.create(person_routine=self.alice_routine)
        self.session.end_session()
        self.url = reverse('core:session_detail', kwargs={'pk': self.session.pk})
        self.progress = reverse('core:person_routine_detail', kwargs={'pk': self.alice_routine.pk})
        self.login(self.trainer)

    def test_from_dashboard_goes_back_to_dashboard(self):
        response = self.client.get(self.url + '?next=/')
        self.assertEqual((response.context['back_url'], response.context['back_label']), ('/', 'Voltar ao painel'))
        self.assertContains(response, 'href="/" class="btn btn-secondary btn-back-compact"')

    def test_from_routine_progress_goes_back_there(self):
        response = self.client.get(self.url, {'next': self.progress})
        self.assertEqual(response.context['back_url'], self.progress)
        self.assertEqual(response.context['back_label'], 'Voltar ao progresso do treino')

    def test_next_with_query_string_is_kept_and_labelled(self):
        target = reverse('core:student_history', kwargs={'pk': self.alice.person.pk}) + '?page=2'
        response = self.client.get(self.url, {'next': target})
        self.assertEqual(response.context['back_url'], target)
        self.assertEqual(response.context['back_label'], 'Voltar ao histórico do aluno')

    def test_unknown_local_path_gets_generic_label(self):
        response = self.client.get(self.url, {'next': '/algum/lugar/'})
        self.assertEqual((response.context['back_url'], response.context['back_label']), ('/algum/lugar/', 'Voltar'))

    def test_without_next_trainer_defaults_to_routine_progress(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['back_url'], self.progress)

    def test_student_defaults_to_history(self):
        self.login(self.alice)
        response = self.client.get(self.url)
        self.assertEqual(response.context['back_url'], reverse('core:my_history'))
        self.assertEqual(response.context['back_label'], 'Voltar ao histórico')

    def test_external_or_dangerous_next_is_ignored(self):
        for bad in ('https://evil.example/', '//evil.example/', '/\\evil.example', 'javascript:alert(1)',
                    'http://localhost/', 'evil.example/x', ''):
            with self.subTest(next=bad):
                response = self.client.get(self.url, {'next': bad})
                self.assertEqual(response.context['back_url'], self.progress)

    def test_student_cannot_use_next_to_open_trainer_pages_or_leave_the_site(self):
        # next is only a link target; the destination still enforces permissions
        self.login(self.alice)
        response = self.client.get(self.url, {'next': '//evil.example/'})
        self.assertEqual(response.context['back_url'], reverse('core:my_history'))
        self.assertEqual(self.client.get(reverse('core:student_list')).status_code, 403)


class StudentNameLinkTests(BaseTestCase):
    def setUp(self):
        self.session = WorkoutSession.objects.create(person_routine=self.alice_routine)
        self.url = reverse('core:session_detail', kwargs={'pk': self.session.pk})
        self.student_url = reverse('core:student_detail', kwargs={'pk': self.alice.person.pk})

    def test_trainer_sees_links_to_student_and_progress(self):
        self.login(self.trainer)
        page = self.client.get(self.url)
        self.assertContains(page, f'href="{self.student_url}?next=')
        self.assertContains(page, reverse('core:person_routine_detail', kwargs={'pk': self.alice_routine.pk}))

    def test_student_sees_plain_text(self):
        self.login(self.alice)
        self.assertNotContains(self.client.get(self.url), self.student_url)


class LinksCarryNextTests(BaseTestCase):
    def setUp(self):
        self.session = WorkoutSession.objects.create(person_routine=self.alice_routine, notes='comentário')
        self.session.end_session()  # the progress page lists finished sessions
        self.login(self.trainer)
        self.detail = reverse('core:session_detail', kwargs={'pk': self.session.pk})

    def test_dashboard_links_include_next(self):
        page = self.client.get(reverse('core:home'))
        # "Treinos de hoje" and "Comentários" rows
        self.assertContains(page, f'href="{self.detail}?next=/"', count=2)

    def test_progress_page_links_include_next_and_student_name(self):
        progress = reverse('core:person_routine_detail', kwargs={'pk': self.alice_routine.pk})
        page = self.client.get(progress)
        self.assertContains(page, f'{self.detail}?next={progress}')
        self.assertContains(page, reverse('core:student_detail', kwargs={'pk': self.alice.person.pk}))

    def test_round_trip_dashboard_to_detail_and_back(self):
        page = self.client.get(reverse('core:home'))
        link = f'{self.detail}?next=/'
        self.assertContains(page, link)
        detail = self.client.get(link)
        self.assertEqual(detail.context['back_url'], '/')
