import html
import re
from django.test import RequestFactory
from django.urls import reverse
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core import urls as core_urls
from core.breadcrumbs import NO_BREADCRUMB, TRAILS, build_breadcrumbs
from core.models import Exercise, Person

from .base import BaseTestCase
from .test_navigation_global import RichDataMixin

NAV = re.compile(r'<nav aria-label="breadcrumb".*?</nav>', re.S)
ITEM = re.compile(r'<li class="breadcrumb-item[^"]*"([^>]*)>(.*?)</li>', re.S)
LINK = re.compile(r'<a href="([^"]+)">(.*?)</a>', re.S)

# Names that are not HTML pages (APIs, POST-only actions, files, PWA, probes)
NOT_PAGES = {
    'calendar_feed', 'calendar_rotate', 'calendar_style',
    'search_suggest', 'notice_open', 'notices_read_all',
    'month_rules',
    'api_exercises_list', 'api_routine_exercises', 'api_routines_list', 'assessment_delete', 'healthz',
    'note_add', 'note_delete', 'offline', 'person_routine_deactivate', 'photo_delete', 'photo_file',
    'photo_thumb', 'pwa_manifest', 'service_worker', 'workout_end', 'workout_exercise_notes',
    'workout_pause', 'workout_resume', 'workout_set_update', 'workout_start', 'workout_sync',
    'student_export', 'students_csv_export', 'students_csv_template', 'students_csv_confirm', 'goal_status', 'goal_delete', 'student_consent_record', 'appointment_status', 'appointment_move', 'appointment_move_undo', 'appointment_counts', 'student_package_action', 'appointment_request_decide', 'push_subscribe', 'push_unsubscribe', 'notification_test', 'my_agenda_request_withdraw', 'appointment_delete', 'appointment_remove', 'whatsapp_template_save', 'whatsapp_template_preview', 'refund_void', 'photo_position_action', 'telegram_webhook', 'agenda_holiday_add', 'my_photo_delete', 'agenda_holiday_delete', 'slot_add', 'slot_preview', 'slot_fill', 'slot_topup',
    'slot_delete', 'appointment_series_end', 'payment_delete', 'finance_export', 'finance_service_export', 'workout_discard', 'my_export', 'my_consent_photos', 'my_erasure_request',
}


def trail_of(page):
    """[(label, href or None)] of the (single) breadcrumb in the page; [] when absent."""
    blocks = NAV.findall(page.content.decode())
    assert len(blocks) <= 1, f'{len(blocks)} breadcrumbs on one page'
    if not blocks:
        return []
    items = []
    for attrs, inner in ITEM.findall(blocks[0]):
        link = LINK.search(inner)
        if link:
            items.append((html.unescape(link.group(2)).strip(), link.group(1)))
        else:
            assert 'aria-current="page"' in attrs
            items.append((html.unescape(inner).strip(), None))
    return items


def labels(page):
    return [label for label, _ in trail_of(page)]


class EveryPageIsAccountedForTests(BaseTestCase):
    def test_every_url_has_a_trail_or_is_explicitly_exempt(self):
        names = {p.name for p in core_urls.urlpatterns}
        unaccounted = names - set(TRAILS) - NO_BREADCRUMB - NOT_PAGES
        self.assertEqual(unaccounted, set(), 'new pages need a breadcrumb (core/breadcrumbs.py) or an exemption')

    def test_exemptions_do_not_overlap_with_trails(self):
        self.assertEqual(set(TRAILS) & (NO_BREADCRUMB | NOT_PAGES), set())


class TrainerBreadcrumbTests(RichDataMixin, BaseTestCase):
    def setUp(self):
        self.build_world()
        self.login(self.trainer)
        self.pk = self.person.pk

    def get(self, name, **kwargs):
        return self.client.get(reverse(name, kwargs=kwargs or None))

    def test_trails(self):
        student = self.person.person_name
        routine = self.alice_routine.routine_name
        cases = [
            (self.get('core:student_list'), ['Início', 'Alunos']),
            (self.get('core:student_create'), ['Início', 'Alunos', 'Novo aluno']),
            (self.get('core:student_detail', pk=self.pk), ['Início', 'Alunos', student]),
            (self.get('core:student_edit', pk=self.pk), ['Início', 'Alunos', student, 'Editar']),
            (self.get('core:student_invite', pk=self.pk), ['Início', 'Alunos', student, 'Convite']),
            (self.get('core:student_history', pk=self.pk), ['Início', 'Alunos', student, 'Histórico']),
            (self.get('core:assessment_create', pk=self.pk),
             ['Início', 'Alunos', student, 'Avaliações', 'Nova avaliação']),
            (self.get('core:assessment_edit', pk=self.assessment.pk),
             ['Início', 'Alunos', student, 'Avaliações', 'Editar avaliação']),
            (self.get('core:photo_upload', pk=self.pk), ['Início', 'Alunos', student, 'Avaliações', 'Adicionar foto']),
            (self.client.get(reverse('core:person_routine_create') + f'?person={self.pk}'),
             ['Início', 'Alunos', student, 'Atribuir treino']),
            (self.get('core:person_routine_create'), ['Início', 'Alunos', 'Atribuir treino']),
            (self.get('core:person_routine_edit', pk=self.alice_routine.pk),
             ['Início', 'Alunos', student, 'Editar treino']),
            (self.get('core:person_routine_detail', pk=self.alice_routine.pk),
             ['Início', 'Alunos', student, f'Progresso: {routine}']),
            (self.get('core:exercise_list'), ['Início', 'Exercícios']),
            (self.get('core:exercise_create'), ['Início', 'Exercícios', 'Novo exercício']),
            (self.get('core:exercise_detail', pk=self.supino.pk), ['Início', 'Exercícios', 'Supino']),
            (self.get('core:exercise_edit', pk=self.supino.pk), ['Início', 'Exercícios', 'Supino', 'Editar']),
            (self.get('core:routine_list'), ['Início', 'Treinos']),
            (self.get('core:routine_create'), ['Início', 'Treinos', 'Novo treino']),
            (self.get('core:routine_detail', pk=self.template.pk), ['Início', 'Treinos', 'Treino A']),
            (self.get('core:routine_edit', pk=self.template.pk), ['Início', 'Treinos', 'Treino A', 'Editar']),
            (self.get('core:routine_generate_ai'), ['Início', 'Treinos', 'Gerar com IA']),
        ]
        for page, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(page.status_code, 200)
                self.assertEqual(labels(page), expected)

    def test_session_trail_has_four_levels_and_a_link_to_the_routine_progress(self):
        trail = trail_of(self.get('core:session_detail', pk=self.session.pk))
        self.assertEqual([t[0] for t in trail][:4], ['Início', 'Alunos', self.person.person_name, self.alice_routine.routine_name])
        self.assertTrue(trail[-1][0].startswith('Sessão de '))
        self.assertEqual(trail[3][1], reverse('core:person_routine_detail', kwargs={'pk': self.alice_routine.pk}))

    def test_structure_of_every_trail(self):
        pages = [
            self.get('core:student_detail', pk=self.pk), self.get('core:assessment_create', pk=self.pk),
            self.get('core:routine_edit', pk=self.template.pk), self.get('core:session_detail', pk=self.session.pk),
            self.get('core:exercise_create'),
        ]
        for page in pages:
            trail = trail_of(page)
            with self.subTest(trail=trail):
                self.assertEqual(trail[0], ('Início', '/'))          # first = home
                self.assertIsNone(trail[-1][1])                     # current page is not a link
                self.assertTrue(all(href for _, href in trail[:-1]))  # all others are links
                for _, href in trail[:-1]:                          # and they all work
                    self.assertIn(self.client.get(href.split('#')[0]).status_code, (200, 302), href)

    def test_tab_links_point_to_the_right_tab(self):
        trail = trail_of(self.get('core:assessment_create', pk=self.pk))
        self.assertEqual(trail[3][1], reverse('core:student_detail', kwargs={'pk': self.pk}) + '#tab-avaliacoes')

    def test_menu_labels_are_used_at_level_one(self):
        menu = self.client.get(reverse('core:home')).content.decode()
        for label in ('Alunos', 'Exercícios', 'Treinos'):
            self.assertIn(label, menu)
        for name in ('core:student_list', 'core:exercise_list', 'core:routine_list'):
            self.assertIn(labels(self.get(name))[1], ('Alunos', 'Exercícios', 'Treinos'))
        self.assertNotIn('Rotinas', self.get('core:routine_list').content.decode().split('<main')[1])
        self.assertNotIn('Rotinas', self.get('core:routine_detail', pk=self.template.pk).content.decode().split('<main')[1])

    def test_exempt_pages_have_no_breadcrumb(self):
        self.assertEqual(trail_of(self.get('core:home')), [])
        self.client.logout()
        self.assertEqual(trail_of(self.client.get(reverse('account_login'))), [])

    def test_names_are_escaped(self):
        Person.objects.filter(pk=self.pk).update(person_name='<script>alert(1)</script>')
        Exercise.objects.filter(pk=self.supino.pk).update(exercise_name='<b>x</b>')
        for page in (self.get('core:student_detail', pk=self.pk), self.get('core:exercise_detail', pk=self.supino.pk)):
            nav = NAV.search(page.content.decode()).group(0)
            self.assertNotIn('<script>', nav)
            self.assertNotIn('<b>x</b>', nav)
            self.assertIn('&lt;', nav)

    def test_building_the_trail_costs_no_queries(self):
        # it only reads objects the view already put in the context
        request = RequestFactory().get('/x/')
        request.resolver_match = type('M', (), {'url_name': 'session_detail'})()
        request.user = self.trainer
        context = {'student': self.person, 'person_routine': self.alice_routine, 'session': self.session}
        self.person.person_name, self.alice_routine.routine_name  # load everything before counting
        with CaptureQueriesContext(connection) as queries:
            trail = build_breadcrumbs(request, context)
        self.assertEqual(len(queries.captured_queries), 0)
        self.assertEqual(len(trail), 5)

    def test_a_missing_object_never_breaks_the_page(self):
        request = RequestFactory().get('/x/')
        request.resolver_match = type('M', (), {'url_name': 'student_detail'})()
        request.user = self.trainer
        self.assertEqual(build_breadcrumbs(request, {}), [])
        request.resolver_match = None
        self.assertEqual(build_breadcrumbs(request, {}), [])


class StudentAreaBreadcrumbTests(RichDataMixin, BaseTestCase):
    def setUp(self):
        self.build_world()
        self.login(self.alice)

    def test_trails(self):
        self.assertEqual(labels(self.client.get(reverse('core:my_workouts'))), ['Início', 'Meus treinos'])
        self.assertEqual(labels(self.client.get(reverse('core:my_history'))), ['Início', 'Histórico'])
        self.assertEqual(labels(self.client.get(reverse('core:my_profile'))), ['Início', 'Meu perfil'])
        page = self.client.get(reverse('core:my_exercise_progress', kwargs={'exercise_pk': self.supino.pk}))
        self.assertEqual(labels(page), ['Início', 'Histórico', 'Supino'])
        self.assertEqual(trail_of(page)[1][1], reverse('core:my_history'))

    def test_session_detail_for_the_student_does_not_expose_trainer_pages(self):
        trail = trail_of(self.client.get(reverse('core:session_detail', kwargs={'pk': self.session.pk})))
        self.assertEqual([t[0] for t in trail][:2], ['Início', 'Histórico'])
        self.assertNotIn('Alunos', [t[0] for t in trail])

    def test_workout_screen_has_none(self):
        self.client.post(reverse('core:workout_start', kwargs={'pk': self.alice_routine.pk}))
        page = self.client.get(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(trail_of(page), [])
