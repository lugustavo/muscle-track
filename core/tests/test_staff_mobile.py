from datetime import time, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.agenda import at, week_start
from core.models import Appointment

from .base import BaseTestCase


class StudentFileMobileTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.url = reverse('core:student_detail', kwargs={'pk': self.alice.person.pk})

    def test_header_has_one_main_button_and_an_actions_menu(self):
        html = self.client.get(self.url).content.decode()
        mobile = html[html.index('data-student-actions'):]
        mobile = mobile[:mobile.index('<!-- Estatísticas')]
        self.assertIn('Atribuir treino', mobile)
        self.assertIn('dropdown-menu', mobile)
        for label in ('Editar', 'Redefinir senha', 'Alterar situação', 'Excluir aluno'):
            self.assertIn(label, mobile)
        self.assertIn(reverse('core:student_status', kwargs={'pk': self.alice.person.pk}), mobile)
        # the desktop buttons are still there, only for large screens
        self.assertIn('d-none d-lg-block', html)

    def test_counters_are_four_small_tiles_and_tabs_scroll(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('stat-tiles-4', html)
        self.assertEqual(html.count('class="col-3"'), 4)
        self.assertNotIn('stat-strip', html)
        self.assertIn('tabs-sticky', html)
        tabs = html[html.index('id="studentTabs"'):html.index('class="tab-content"')]
        self.assertEqual(tabs.count('data-bs-toggle="tab"'), 8)


class AgendaDayTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.today = timezone.localdate()
        self.monday = week_start(self.today)
        self.old_monday = self.monday - timedelta(days=28)   # a past week: no fixed slots interfere

    def selected(self, **params):
        query = {'semana': self.old_monday.isoformat(), **params}
        response = self.client.get(reverse('core:agenda'), query)
        self.assertEqual(response.status_code, 200)
        return response, response.context['selected_day']

    def appointment(self, day, person=None):
        return Appointment.objects.create(person=person or self.alice.person, starts_at=at(day, time(9, 0)))

    def test_current_week_shows_today(self):
        response = self.client.get(reverse('core:agenda'))
        self.assertEqual(response.context['selected_day'], self.today)
        self.assertContains(response, f'data-day-pane="{self.today.isoformat()}"')

    def test_other_week_without_classes_starts_on_monday(self):
        self.assertEqual(self.selected()[1], self.old_monday)

    def test_other_week_starts_on_the_first_day_with_classes(self):
        wednesday = self.old_monday + timedelta(days=2)
        self.appointment(wednesday)
        self.appointment(self.old_monday + timedelta(days=4), self.bob.person)
        self.assertEqual(self.selected()[1], wednesday)

    def test_dia_param_picks_a_day_of_the_week(self):
        friday = self.old_monday + timedelta(days=4)
        self.assertEqual(self.selected(dia=friday.isoformat())[1], friday)

    def test_invalid_or_foreign_dia_falls_back(self):
        self.assertEqual(self.selected(dia='abc')[1], self.old_monday)
        outside = self.old_monday + timedelta(days=9)
        self.assertEqual(self.selected(dia=outside.isoformat())[1], self.old_monday)
        # current week: invalid dia means today
        response = self.client.get(reverse('core:agenda'), {'dia': 'zzz'})
        self.assertEqual(response.context['selected_day'], self.today)

    def test_strip_and_panes_markup(self):
        wednesday = self.old_monday + timedelta(days=2)
        self.appointment(wednesday)
        response, _ = self.selected()
        html = response.content.decode()
        self.assertEqual(html.count('data-day="'), 7)
        self.assertEqual(html.count('data-day-pane="'), 7)
        self.assertEqual(html.count('day-pane selected'), 1)
        strip = html[html.index('data-day-strip'):html.index('agenda-days')]
        self.assertEqual(strip.count('busy'), 1)

    def test_empty_day_offers_a_new_class_for_that_day(self):
        response, _ = self.selected()
        self.assertContains(response, 'Nova aula neste dia')
        self.assertContains(response, f'date={self.old_monday.isoformat()}')

    def test_appointment_create_accepts_date(self):
        day = self.today + timedelta(days=5)
        response = self.client.get(reverse('core:appointment_create'), {'date': day.isoformat(), 'person': self.bob.person.pk})
        self.assertEqual(response.context['form'].initial['date'], day)
        self.assertEqual(response.context['form'].initial['person'], self.bob.person.pk)

    def test_appointment_create_ignores_invalid_date(self):
        for value in ('', 'abc', '2026-13-45'):
            response = self.client.get(reverse('core:appointment_create'), {'date': value})
            self.assertEqual(response.context['form'].initial['date'], self.today)

    def test_saving_a_class_returns_to_that_day(self):
        day = self.today + timedelta(days=3)
        response = self.client.post(reverse('core:appointment_create'), {
            'person': self.alice.person.pk, 'date': day.isoformat(), 'time': '10:00',
            'duration_minutes': 60, 'kind': 'regular'})
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'dia={day.isoformat()}', response['Location'])


class FloatingButtonTests(BaseTestCase):
    PAGES = ('core:home', 'core:student_list', 'core:agenda', 'core:finance')

    def labels(self, response):
        return [item['label'] for item in (response.context['fab_items'] or [])]

    def test_trainer_gets_the_plus_button_on_the_main_pages(self):
        self.login(self.trainer)
        for name in self.PAGES:
            response = self.client.get(reverse(name))
            self.assertContains(response, 'id="createSheet"', msg_prefix=name)
            self.assertEqual(self.labels(response), ['Novo aluno', 'Nova aula'], name)

    def test_student_file_adds_its_own_actions_first(self):
        self.login(self.trainer)
        pk = self.alice.person.pk
        response = self.client.get(reverse('core:student_detail', kwargs={'pk': pk}))
        self.assertEqual(self.labels(response), [
            'Registrar pagamento', 'Atribuir treino', 'Nova aula para Alice', 'Nova avaliação', 'Adicionar foto',
            'Novo aluno', 'Nova aula'])
        html = response.content.decode()
        sheet = html[html.index('id="createSheet"'):]
        self.assertIn(reverse('core:payment_create', kwargs={'pk': pk}), sheet)
        self.assertIn(f'person={pk}', sheet)
        self.assertIn('next=', sheet)

    def test_no_button_on_other_pages_forms_students_or_visitors(self):
        self.login(self.trainer)
        for url in (reverse('core:exercise_list'), reverse('core:student_create'), reverse('core:plan_list'),
                    reverse('core:appointment_create')):
            response = self.client.get(url)
            self.assertNotContains(response, 'id="createSheet"', msg_prefix=url)
        self.login(self.alice)
        self.assertNotContains(self.client.get(reverse('core:home')), 'id="createSheet"')
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('core:home'), follow=True), 'id="createSheet"')

    def test_the_button_is_phone_only(self):
        self.login(self.trainer)
        self.assertContains(self.client.get(reverse('core:home')), 'class="fab d-lg-none"')


class StudentListMobileTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.url = reverse('core:student_list')

    def test_rows_and_filter_sheet(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-student-rows')
        self.assertContains(response, 'id="filterSheet"')
        html = response.content.decode()
        rows = html[html.index('data-student-rows'):html.index('<!-- Grid View -->')]
        self.assertIn('Alice', rows)
        self.assertIn('Bob', rows)
        self.assertIn(reverse('core:student_detail', kwargs={'pk': self.alice.person.pk}), rows)

    def test_active_filter_count(self):
        self.assertEqual(self.client.get(self.url).context['active_filters'], 0)
        self.assertNotContains(self.client.get(self.url), 'data-filter-count')
        response = self.client.get(self.url, {'status': 'active', 'filtro': 'trial', 'search': 'ali'})
        self.assertEqual(response.context['active_filters'], 2)   # the search box is not a "filter"
        self.assertContains(response, 'data-filter-count')

    def test_filters_still_apply_and_are_kept_in_the_sheet(self):
        response = self.client.get(self.url, {'search': 'alice'})
        self.assertEqual([s.person_name for s in response.context['students']], ['Alice'])
        self.assertContains(response, 'name="search" value="alice"')

    def test_query_count_does_not_grow_with_students(self):
        with CaptureQueriesContext(connection) as few:
            self.client.get(self.url)
        for i in range(8):
            self._make_student(f'extra{i}', f'Extra {i}')
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.url)
        self.assertLessEqual(len(many), len(few) + 1)


class ExerciseDetailMobileTests(BaseTestCase):
    def test_counters_are_compact_tiles_two_per_row_on_phones(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:exercise_detail', kwargs={'pk': self.supino.pk})).content.decode()
        self.assertEqual(html.count('stat-tile'), 4)
        self.assertEqual(html.count('class="col-6 col-md-3"'), 4)
        self.assertEqual(html.count('class="stat-top"'), 4)
        # picture first on phones, after the text on larger screens
        self.assertIn('col-md-4 order-1 order-md-2', html)
        self.assertIn('col-md-8 order-2 order-md-1', html)

    def test_empty_picture_placeholder_is_hidden_on_phones(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:exercise_detail', kwargs={'pk': self.supino.pk})).content.decode()
        self.assertIn('order-md-2 mb-3 mb-md-0 d-none d-md-block', html)   # no image: placeholder only on desktop
