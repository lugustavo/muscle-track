"""Navigation conventions (see core/navigation.py): every link into a detail/form page
remembers where it came from and the page's Back/Cancel button returns there."""
import html
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.test import RequestFactory
from django.urls import resolve, reverse
from django.utils import timezone

from core.models import (
    Assessment, PersonRoutine, SessionExercise, StudentNote, WorkoutSession,
)
from core.navigation import after_save_url, back_label, safe_next_url, with_next

from .base import BaseTestCase

HREF = re.compile(r'<a\s[^>]*?href="([^"]+)"[^>]*>', re.S)
BACK = re.compile(r'<a\s[^>]*?href="([^"]+)"[^>]*data-nav-back', re.S)


def hrefs_with_next(page):
    found = []
    for raw in HREF.findall(page.content.decode()):
        url = html.unescape(raw)
        if 'next=' in url and url.startswith('/'):
            found.append(url)
    return sorted(set(found))


def back_links(page):
    return [html.unescape(h).split('#')[0] for h in BACK.findall(page.content.decode())]


class RichDataMixin:
    """A trainer-visible world with something on every page."""

    def build_world(self):
        self.person = self.alice.person
        self.session = WorkoutSession.objects.create(person_routine=self.alice_routine, notes='ok')
        row = self.alice_routine.exercises.first()
        SessionExercise.objects.create(session=self.session, person_routine_exercise=row,
                                       weight_used=40, is_completed=True, notes='leve')
        self.session.end_session()
        self.assessment = Assessment.objects.create(person=self.person, weight_kg=60)
        self.note = StudentNote.objects.create(person=self.person, text='n')
        PersonRoutine.objects.filter(pk=self.alice_routine.pk).update(routine_template=self.template)
        # an old routine makes the student file show the link to the full history
        PersonRoutine.objects.create(person=self.person, routine_name='Treino antigo', is_active=False)


class LinksRememberTheirOriginTests(RichDataMixin, BaseTestCase):
    """Generic crawler: follow every `?next=` link of a page; the target's Back button
    must lead exactly to the page we were on."""

    def setUp(self):
        self.build_world()
        self.login(self.trainer)

    def crawl(self, origin):
        page = self.client.get(origin)
        self.assertEqual(page.status_code, 200, origin)
        links = hrefs_with_next(page)
        self.assertTrue(links, f'{origin} has no links carrying next=')
        for link in links:
            self.followed.add(resolve(urlsplit(link).path).url_name)
            with self.subTest(origin=origin, link=link):
                target = self.client.get(link)
                self.assertEqual(target.status_code, 200)
                backs = back_links(target)
                self.assertTrue(backs, 'target page has no Back button')
                self.assertTrue(all(b == origin for b in backs), (backs, origin))

    def test_trainer_pages(self):
        origins = [
            reverse('core:home'),
            reverse('core:student_list') + '?search=Ali&status=active',
            reverse('core:student_detail', kwargs={'pk': self.person.pk}),
            reverse('core:routine_list') + '?difficulty=intermediario',
            reverse('core:routine_detail', kwargs={'pk': self.template.pk}),
            reverse('core:exercise_list') + '?search=Sup',
            reverse('core:exercise_detail', kwargs={'pk': self.supino.pk}),
            reverse('core:person_routine_detail', kwargs={'pk': self.alice_routine.pk}),
            reverse('core:session_detail', kwargs={'pk': self.session.pk}),
            reverse('core:student_history', kwargs={'pk': self.person.pk}),
        ]
        self.followed = set()
        for origin in origins:
            self.crawl(origin)
        # the crawl really covered the app, not just a few pages
        expected = {
            'student_detail', 'student_edit', 'student_create', 'student_history', 'student_invite',
            'person_routine_create', 'person_routine_edit', 'person_routine_detail',
            'routine_detail', 'routine_edit', 'routine_create', 'routine_generate_ai',
            'exercise_detail', 'exercise_edit', 'exercise_create', 'session_detail',
            'assessment_create', 'assessment_edit', 'photo_upload',
        }
        self.assertEqual(expected - self.followed, set())

    def test_chained_pages_keep_the_original_origin(self):
        """list -> student -> edit -> Cancel -> student -> Back -> list, filter intact."""
        list_url = reverse('core:student_list') + '?search=Ali'
        detail = [h for h in hrefs_with_next(self.client.get(list_url))
                  if urlsplit(h).path == reverse('core:student_detail', kwargs={'pk': self.person.pk})][0]
        edit = [h for h in hrefs_with_next(self.client.get(detail))
                if urlsplit(h).path == reverse('core:student_edit', kwargs={'pk': self.person.pk})][0]
        self.assertEqual(back_links(self.client.get(edit)), [detail.split('?')[0] + '?' + detail.split('?', 1)[1]] * 2)
        self.assertEqual(back_links(self.client.get(detail)), [list_url])


class PersonRoutineFormNavigationTests(RichDataMixin, BaseTestCase):
    """The reported bug: Add routine from the student's file -> Back must return to it."""

    def setUp(self):
        self.build_world()
        self.login(self.trainer)
        self.detail = reverse('core:student_detail', kwargs={'pk': self.person.pk})

    def add_link(self):
        links = [h for h in hrefs_with_next(self.client.get(self.detail))
                 if urlsplit(h).path == reverse('core:person_routine_create')]
        self.assertTrue(links)
        return links[0]

    def test_add_routine_back_goes_to_the_student_file(self):
        link = self.add_link()
        self.assertEqual(parse_qs(urlsplit(link).query)['person'], [str(self.person.pk)])
        page = self.client.get(link)
        self.assertEqual(back_links(page), [self.detail])
        self.assertEqual(page.context['selected_person'], self.person)

    def test_without_next_the_default_is_the_student_file_not_the_list(self):
        url = reverse('core:person_routine_create') + f'?person={self.person.pk}'
        self.assertEqual(back_links(self.client.get(url)), [self.detail])
        # unknown student -> the list is the only sensible place
        self.assertEqual(back_links(self.client.get(reverse('core:person_routine_create'))),
                         [reverse('core:student_list')])

    def test_edit_routine_back_goes_to_the_student_file(self):
        url = reverse('core:person_routine_edit', kwargs={'pk': self.alice_routine.pk})
        self.assertEqual(back_links(self.client.get(url)), [self.detail])

    def test_saving_returns_to_where_the_student_file_was_opened_from(self):
        dashboard_detail = with_next(self.detail, '/')
        form_url = with_next(reverse('core:person_routine_edit', kwargs={'pk': self.alice_routine.pk}), dashboard_detail)
        response = self.client.post(form_url, {
            'person': self.person.pk, 'routine_name': 'Novo nome', 'difficulty_level': 'iniciante',
            'estimated_duration': 30, 'routine_date': '2026-01-01', 'exercises_data': '[]'})
        self.assertRedirects(response, dashboard_detail, fetch_redirect_response=False)


class DefaultsWithoutNextTests(RichDataMixin, BaseTestCase):
    """Every page has a sensible Back/Cancel target when opened directly."""

    def setUp(self):
        self.build_world()
        self.login(self.trainer)
        self.pk = self.person.pk

    def test_defaults(self):
        student = reverse('core:student_detail', kwargs={'pk': self.pk})
        cases = {
            reverse('core:student_detail', kwargs={'pk': self.pk}): [reverse('core:student_list')],
            reverse('core:student_edit', kwargs={'pk': self.pk}): [student, student],
            reverse('core:student_create'): [reverse('core:student_list')] * 2,
            reverse('core:student_history', kwargs={'pk': self.pk}): [student],
            reverse('core:student_invite', kwargs={'pk': self.pk}): [student],
            reverse('core:exercise_detail', kwargs={'pk': self.supino.pk}): [reverse('core:exercise_list')],
            reverse('core:exercise_edit', kwargs={'pk': self.supino.pk}):
                [reverse('core:exercise_detail', kwargs={'pk': self.supino.pk})] * 2,
            reverse('core:exercise_create'): [reverse('core:exercise_list')] * 2,
            reverse('core:routine_detail', kwargs={'pk': self.template.pk}): [reverse('core:routine_list')],
            reverse('core:routine_edit', kwargs={'pk': self.template.pk}):
                [reverse('core:routine_detail', kwargs={'pk': self.template.pk})] * 2,
            reverse('core:routine_create'): [reverse('core:routine_list')] * 2,
            reverse('core:routine_generate_ai'): [reverse('core:routine_list')],
            reverse('core:person_routine_detail', kwargs={'pk': self.alice_routine.pk}): [student],
            reverse('core:assessment_create', kwargs={'pk': self.pk}): [student] * 2,
            reverse('core:assessment_edit', kwargs={'pk': self.assessment.pk}): [student] * 2,
            reverse('core:photo_upload', kwargs={'pk': self.pk}): [student] * 2,
            reverse('core:session_detail', kwargs={'pk': self.session.pk}):
                [reverse('core:person_routine_detail', kwargs={'pk': self.alice_routine.pk})],
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(back_links(response), expected)

    def test_no_back_button_points_to_its_own_page(self):
        for url in (reverse('core:student_detail', kwargs={'pk': self.pk}), reverse('core:routine_list')):
            for back in back_links(self.client.get(url)):
                self.assertNotEqual(back, url)

    def test_tab_is_kept_on_assessment_and_photo_forms(self):
        page = self.client.get(reverse('core:assessment_create', kwargs={'pk': self.pk}))
        self.assertIn('#tab-avaliacoes', page.content.decode())
        page = self.client.get(reverse('core:photo_upload', kwargs={'pk': self.pk}))
        self.assertIn('#tab-fotos', page.content.decode())

    def test_labels_follow_the_destination(self):
        student = reverse('core:student_detail', kwargs={'pk': self.pk})
        for next_url, label in (('/', 'Voltar ao painel'), (reverse('core:student_list'), 'Voltar aos alunos'),
                                (student, 'Voltar ao aluno'), (reverse('core:routine_list'), 'Voltar aos treinos'),
                                (reverse('core:exercise_list'), 'Voltar aos exercícios'),
                                (reverse('core:session_detail', kwargs={'pk': self.session.pk}), 'Voltar à sessão')):
            with self.subTest(label=label):
                page = self.client.get(reverse('core:student_history', kwargs={'pk': self.pk}), {'next': next_url})
                self.assertContains(page, label)


class AfterSaveRedirectTests(RichDataMixin, BaseTestCase):
    def setUp(self):
        self.build_world()
        self.login(self.trainer)
        self.detail = reverse('core:student_detail', kwargs={'pk': self.person.pk})

    def post_edit(self, next_url=None):
        url = reverse('core:student_edit', kwargs={'pk': self.person.pk})
        if next_url is not None:
            url = with_next(url, next_url)
        return self.client.post(url, {'person_name': 'Alice', 'email': 'alice@example.com', 'is_active': 'on'})

    def test_no_next_goes_to_the_entity(self):
        self.assertRedirects(self.post_edit(), self.detail, fetch_redirect_response=False)

    def test_next_pointing_to_the_entity_returns_exactly_there(self):
        origin = with_next(self.detail, '/students/?search=Ali')
        self.assertRedirects(self.post_edit(origin), origin, fetch_redirect_response=False)

    def test_other_next_is_forwarded_to_the_entity(self):
        response = self.post_edit('/')
        self.assertRedirects(response, with_next(self.detail, '/'), fetch_redirect_response=False)

    def test_malicious_next_is_ignored(self):
        for bad in ('https://evil.example/', '//evil.example/', '/\\evil.example', 'javascript:1'):
            with self.subTest(next=bad):
                self.assertRedirects(self.post_edit(bad), self.detail, fetch_redirect_response=False)

    def test_creating_in_a_list_returns_to_the_list_with_its_filters(self):
        lst = reverse('core:exercise_list') + '?search=Sup'
        response = self.client.post(with_next(reverse('core:exercise_create'), lst), {
            'exercise_name': 'Remada', 'muscle_group': self.group.pk, 'default_reps': 10, 'default_sets': 3,
            'media_type': 'image', 'is_active': 'on'})
        self.assertRedirects(response, lst, fetch_redirect_response=False)
        # from somewhere else it just lands on the plain list (nothing to forward)
        response = self.client.post(with_next(reverse('core:exercise_create'), '/'), {
            'exercise_name': 'Remada 2', 'muscle_group': self.group.pk, 'default_reps': 10, 'default_sets': 3,
            'media_type': 'image', 'is_active': 'on'})
        self.assertRedirects(response, reverse('core:exercise_list'), fetch_redirect_response=False)

    def test_student_file_tabs_keep_origin_and_tab(self):
        origin = with_next(self.detail, '/')
        for name, data, tab in (
            ('core:assessment_create', {'date': '2026-03-01', 'weight_kg': '70'}, 'avaliacoes'),
            ('core:note_add', {'text': 'oi'}, 'notas'),
        ):
            with self.subTest(name=name):
                response = self.client.post(with_next(reverse(name, kwargs={'pk': self.person.pk}), origin), data)
                self.assertRedirects(response, f'{origin}#tab-{tab}', fetch_redirect_response=False)

    def test_deleting_from_the_student_file_returns_to_it(self):
        origin = with_next(self.detail, '/')
        response = self.client.post(with_next(reverse('core:assessment_delete', kwargs={'pk': self.assessment.pk}), origin))
        self.assertRedirects(response, f'{origin}#tab-avaliacoes', fetch_redirect_response=False)
        response = self.client.post(with_next(reverse('core:note_delete', kwargs={'pk': self.note.pk}), origin))
        self.assertRedirects(response, f'{origin}#tab-notas', fetch_redirect_response=False)
        response = self.client.post(
            with_next(reverse('core:person_routine_deactivate', kwargs={'pk': self.alice_routine.pk}), origin))
        self.assertRedirects(response, origin, fetch_redirect_response=False)


class StudentAreaNavigationTests(RichDataMixin, BaseTestCase):
    def setUp(self):
        self.build_world()
        self.login(self.alice)

    def test_record_link_in_the_workout_returns_to_the_workout(self):
        self.client.post(reverse('core:workout_start', kwargs={'pk': self.alice_routine.pk}))
        workout = reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk})
        page = self.client.get(workout)
        links = [h for h in hrefs_with_next(page)
                 if urlsplit(h).path == reverse('core:my_exercise_progress', kwargs={'exercise_pk': self.supino.pk})]
        self.assertTrue(links, 'record link should carry next=')
        target = self.client.get(links[0])
        self.assertEqual(back_links(target), [workout])
        self.assertContains(target, 'Voltar ao treino em andamento')

    def test_history_defaults_and_links(self):
        history = reverse('core:my_history')
        links = hrefs_with_next(self.client.get(history))
        self.assertTrue(links)
        for link in links:
            self.assertEqual(back_links(self.client.get(link)), [history])
        direct = self.client.get(reverse('core:my_exercise_progress', kwargs={'exercise_pk': self.supino.pk}))
        self.assertEqual(back_links(direct), [history])

    def test_profile_back_defaults_to_my_workouts(self):
        self.assertEqual(back_links(self.client.get(reverse('core:my_profile'))), [reverse('core:my_workouts')] * 2)

    def test_next_cannot_be_used_to_reach_trainer_pages(self):
        for url in (reverse('core:student_list'), reverse('core:routine_list')):
            self.assertEqual(self.client.get(url, {'next': '/'}).status_code, 403)


class HelperUnitTests(BaseTestCase):
    def request(self, query=''):
        return RequestFactory().get('/x/' + query, HTTP_HOST='testserver')

    def test_safe_next_url(self):
        self.assertEqual(safe_next_url(self.request('?next=/a/?b=1'), '/d/'), '/a/?b=1')
        for bad in ('?next=//e.com', '?next=http://e.com', '?next=%5Cevil', '?next=javascript:1', '?next=',
                    '?next=/' + 'a' * 1001, ''):
            self.assertEqual(safe_next_url(self.request(bad), '/d/'), '/d/', bad)

    def test_with_next_keeps_existing_query_and_slashes_readable(self):
        self.assertEqual(with_next('/p/', '/a/?x=1&y=2'), '/p/?next=/a/%3Fx%3D1%26y%3D2')
        self.assertEqual(with_next('/p/?person=3', '/'), '/p/?person=3&next=/')

    def test_after_save_url_rules(self):
        r = self.request
        self.assertEqual(after_save_url(r(), '/e/1/'), '/e/1/')
        self.assertEqual(after_save_url(r('?next=/e/1/%3Fnext%3D/'), '/e/1/'), '/e/1/?next=/')
        self.assertEqual(after_save_url(r('?next=/'), '/e/1/'), '/e/1/?next=/')
        self.assertEqual(after_save_url(r('?next=/'), '/list/', forward_next=False), '/list/')
        self.assertEqual(after_save_url(r('?next=/'), '/e/1/', fragment='#tab-x'), '/e/1/?next=/#tab-x')
        self.assertEqual(after_save_url(r('?next=//evil.com'), '/e/1/'), '/e/1/')

    def test_back_label(self):
        self.assertEqual(back_label('/'), 'Voltar ao painel')
        self.assertEqual(back_label('/students/?search=x'), 'Voltar aos alunos')
        self.assertEqual(back_label('/no/such/page/'), 'Voltar')
        _ = (timedelta, timezone)  # imported for fixtures above
