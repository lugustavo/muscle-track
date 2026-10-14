from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Appointment, PersonRoutine, PersonRoutineExercise, Person, Plan, SessionExercise, SessionSet, StudentEvent,
    WorkoutSession,
)
from core.today import (
    MAX_SESSION_MINUTES, routine_rows, student_today, suggest_index, suggested_row, week_strip,
)

from .base import BaseTestCase


def tabbar_html(response):
    text = response.content.decode()
    start = text.find('<nav class="tabbar')
    return text[start:text.find('</nav>', start)] if start != -1 else ''


def finish(routine, when=None, minutes=45):
    """A finished workout of `routine` started at `when` (default now)."""
    session = WorkoutSession.objects.create(person_routine=routine)
    start = when or timezone.now()
    WorkoutSession.objects.filter(pk=session.pk).update(
        start_time=start, end_time=start + timedelta(minutes=minutes), duration_minutes=minutes, is_completed=True)
    return WorkoutSession.objects.get(pk=session.pk)


def open_session(routine, started_ago=timedelta(minutes=5)):
    session = WorkoutSession.objects.create(person_routine=routine)
    WorkoutSession.objects.filter(pk=session.pk).update(start_time=timezone.now() - started_ago)
    return WorkoutSession.objects.get(pk=session.pk)


class SuggestionRuleTests(BaseTestCase):
    def test_suggest_index(self):
        old, recent = timezone.now() - timedelta(days=9), timezone.now() - timedelta(days=1)
        self.assertIsNone(suggest_index([]))
        self.assertEqual(suggest_index([(False, recent)]), 0)
        self.assertEqual(suggest_index([(False, recent), (False, old)]), 1)        # done longest ago
        self.assertEqual(suggest_index([(False, recent), (False, None)]), 1)       # never done first
        self.assertEqual(suggest_index([(False, old), (True, recent)]), 1)         # in progress first
        self.assertEqual(suggest_index([(False, None), (False, None)]), 0)

    def test_rows_carry_what_the_screens_need(self):
        second = PersonRoutine.objects.create(person=self.alice.person, routine_name='Treino B')
        finish(self.alice_routine)
        rows = routine_rows(self.alice.person)
        by_name = {r['routine'].routine_name: r for r in rows}
        self.assertEqual(by_name[self.alice_routine.routine_name]['exercise_count'], 2)
        self.assertIsNotNone(by_name[self.alice_routine.routine_name]['last_done'])
        self.assertIsNone(by_name['Treino B']['last_done'])
        self.assertEqual(suggested_row(rows)['routine'], second)

    def test_abandoned_session_is_not_in_progress(self):
        open_session(self.alice_routine, started_ago=timedelta(minutes=MAX_SESSION_MINUTES + 5))
        self.assertIsNone(routine_rows(self.alice.person)[0]['open_start'])
        open_session(self.bob_routine, started_ago=timedelta(minutes=30))
        self.assertIsNotNone(routine_rows(self.bob.person)[0]['open_start'])

    def test_my_workouts_still_marks_the_suggestion(self):
        second = PersonRoutine.objects.create(person=self.alice.person, routine_name='Treino B')
        finish(self.alice_routine)
        self.login(self.alice)
        items = self.client.get(reverse('core:my_workouts')).context['routines_with_sessions']
        self.assertEqual(items[0]['routine'], second)
        self.assertTrue(items[0]['suggested'])


class StudentBarTests(BaseTestCase):
    def setUp(self):
        self.login(self.alice)

    def bar(self, name='core:home', **kwargs):
        return tabbar_html(self.client.get(reverse(name, kwargs=kwargs or None)))

    def test_student_items_and_destinations(self):
        html = self.bar()
        self.assertIn('aria-label="Menu principal"', html)
        for label, url in (('Hoje', reverse('core:home')), ('Treinos', reverse('core:my_workouts')),
                           ('Histórico', reverse('core:my_history')), ('Perfil', reverse('core:my_profile'))):
            self.assertIn(f'href="{url}"', html)
            self.assertIn(f'>{label}<', html)
        self.assertNotIn('Alunos', html)
        self.assertNotIn('Financeiro', html)
        self.assertNotIn('moreSheet', self.client.get(reverse('core:home')).content.decode())

    def test_active_item_follows_the_page(self):
        cases = {'core:home': 'Hoje', 'core:my_workouts': 'Treinos', 'core:my_history': 'Histórico',
                 'core:my_profile': 'Perfil', 'core:my_privacy': 'Perfil'}
        for name, label in cases.items():
            html = self.bar(name)
            self.assertEqual(html.count('aria-current="page"'), 1, name)
            active = html[:html.index('aria-current="page"')]
            self.assertIn('class="tab active"', active.split('<a ')[-1], name)
            self.assertIn(f'>{label}<', html[html.index('aria-current="page"'):][:200], name)

    def test_progress_page_keeps_history_active(self):
        html = self.bar('core:my_exercise_progress', exercise_pk=self.supino.pk)
        self.assertIn('aria-current="page"', html)
        self.assertIn('>Histórico<', html[html.index('aria-current="page"'):][:200])

    def test_center_button_starts_the_suggested_workout_with_a_post(self):
        html = self.bar()
        self.assertIn(f'action="{reverse("core:workout_start", kwargs={"pk": self.alice_routine.pk})}"', html)
        self.assertIn('method="post"', html)
        self.assertIn('csrfmiddlewaretoken', html)
        self.assertIn('>Treinar<', html)

    def test_center_button_follows_the_rotation(self):
        second = PersonRoutine.objects.create(person=self.alice.person, routine_name='Treino B')
        finish(self.alice_routine)
        self.assertIn(reverse('core:workout_start', kwargs={'pk': second.pk}), self.bar())

    def test_center_button_continues_a_running_workout_with_its_clock(self):
        session = open_session(self.alice_routine, started_ago=timedelta(minutes=12))
        html = self.bar()
        self.assertIn(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}), html)
        self.assertIn('>Continuar<', html)
        self.assertIn(f'data-tabbar-timer="{int(session.start_time.timestamp() * 1000)}"', html)
        self.assertNotIn('workout_start', html)
        self.assertNotIn('csrfmiddlewaretoken', html)

    def test_an_abandoned_session_does_not_count_as_running(self):
        open_session(self.alice_routine, started_ago=timedelta(minutes=MAX_SESSION_MINUTES + 10))
        html = self.bar()
        self.assertNotIn('data-tabbar-timer', html)
        self.assertIn('>Treinar<', html)

    def test_center_without_workouts_goes_to_today(self):
        PersonRoutine.objects.filter(person=self.alice.person).update(is_active=False)
        html = self.bar()
        self.assertIn(f'href="{reverse("core:home")}" class="btn-center"', html)
        self.assertNotIn('workout_start', html)

    def test_bar_is_absent_where_it_would_be_in_the_way(self):
        session = open_session(self.alice_routine)
        page = self.client.get(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(tabbar_html(page), '')
        self.assertNotIn('has-tabbar', page.content.decode())
        record = Person.objects.get(pk=self.alice.person.pk)
        from core.models import ConsentRecord
        ConsentRecord.objects.filter(person=record).delete()
        consent = self.client.get(reverse('core:consent'))
        self.assertEqual(consent.status_code, 200)
        self.assertEqual(tabbar_html(consent), '')
        session.delete()

    def test_extra_queries_do_not_grow_with_routines_and_sessions(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('core:my_profile'))
            return len(ctx)
        few = count()
        for i in range(8):
            routine = PersonRoutine.objects.create(person=self.alice.person, routine_name=f'T{i}')
            finish(routine)
            finish(routine)
        self.assertEqual(count(), few)

    def test_hidden_for_visitors_and_accounts_without_a_student_file(self):
        self.client.logout()
        self.assertEqual(tabbar_html(self.client.get(reverse('core:home'))), '')
        self.assertEqual(tabbar_html(self.client.get(reverse('account_login'))), '')
        orphan = User.objects.create_user('orphan', 'orphan@example.com', 'pw-orphan')
        self.client.force_login(orphan)
        self.assertEqual(tabbar_html(self.client.get(reverse('core:home'))), '')

    def test_layout_classes_for_small_screens(self):
        page = self.client.get(reverse('core:home')).content.decode()
        self.assertIn('viewport-fit=cover', page)
        self.assertIn('<body class="has-tabbar">', page)
        self.assertIn('class="navbar-toggler d-none"', page)       # the bar replaces the hamburger
        self.assertIn('<footer class="d-none d-lg-block">', page)  # no footer on phones
        self.assertIn('css/tabbar.css', page)
        self.assertIn('js/tabbar.js', page)
        trail = self.client.get(reverse('core:my_history')).content.decode()
        self.assertIn('breadcrumb-nav d-none d-lg-block', trail)
        self.assertIn('class="tabbar d-lg-none"', page)

    def test_visitors_keep_footer_and_menu(self):
        self.client.logout()
        page = self.client.get(reverse('account_login')).content.decode()
        self.assertIn('<footer>', page)
        self.assertNotIn('navbar-toggler d-none', page)
        self.assertNotIn('has-tabbar', page)

    def test_profile_has_the_account_card(self):
        page = self.client.get(reverse('core:my_profile'))
        for url in (reverse('core:my_privacy'), reverse('account_change_password'), reverse('account_logout')):
            self.assertContains(page, f'href="{url}"')


class StaffBarTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.today = timezone.localdate()

    def bar(self, name='core:home', **kwargs):
        response = self.client.get(reverse(name, kwargs=kwargs or None))
        return response, tabbar_html(response)

    def test_items_and_more_sheet(self):
        response, html = self.bar()
        for label, url in (('Hoje', reverse('core:home')), ('Alunos', reverse('core:student_list')),
                           ('Agenda', reverse('core:agenda')), ('Financeiro', reverse('core:finance'))):
            self.assertIn(f'href="{url}"', html)
            self.assertIn(f'>{label}<', html)
        self.assertIn('data-bs-target="#moreSheet"', html)
        page = response.content.decode()
        sheet = page[page.index('id="moreSheet"'):]
        for url in (reverse('core:exercise_list'), reverse('core:routine_list'), reverse('core:plan_list'), '/admin/',
                    reverse('account_change_password'), reverse('account_logout')):
            self.assertIn(f'href="{url}"', sheet)
        self.assertNotIn('Treinar', html)

    def test_active_section_by_page(self):
        person = self.alice.person.pk
        cases = [
            (('core:home', None), 'Hoje'),
            (('core:student_list', None), 'Alunos'),
            (('core:student_detail', {'pk': person}), 'Alunos'),
            (('core:student_edit', {'pk': person}), 'Alunos'),
            (('core:agenda', None), 'Agenda'),
            (('core:appointment_create', None), 'Agenda'),
            (('core:finance', None), 'Financeiro'),
            (('core:plan_list', None), 'Financeiro'),
            (('core:payment_create', {'pk': person}), 'Financeiro'),
        ]
        for (name, kwargs), label in cases:
            response, html = self.bar(name, **(kwargs or {}))
            self.assertEqual(html.count('aria-current="page"'), 1, name)
            self.assertIn(f'>{label}<', html[html.index('aria-current="page"'):][:220], name)

    def test_pages_of_the_more_sheet_light_up_more(self):
        for name in ('core:exercise_list', 'core:routine_list'):
            response, html = self.bar(name)
            self.assertNotIn('aria-current="page"', html, name)
            self.assertIn('class="tab active" data-bs-toggle="offcanvas"', html, name)

    def test_badges(self):
        response, html = self.bar()
        self.assertNotIn('tab-badge', html)
        plan = Plan.objects.create(name='Mensal', months=1, price=Decimal('100'))
        Person.objects.filter(pk=self.alice.person.pk).update(plan=plan, plan_expires_on=self.today - timedelta(days=3))
        StudentEvent.objects.create(person=self.bob.person, kind='erasure_request', summary='x')
        StudentEvent.objects.create(person=self.bob.person, kind='erasure_request', summary='again')  # one student, one badge
        past = timezone.now() - timedelta(hours=1)
        Appointment.objects.create(person=self.alice.person, starts_at=max(past, timezone.make_aware(
            datetime.combine(self.today, time.min)) + timedelta(minutes=1)))
        Appointment.objects.create(person=self.bob.person, starts_at=timezone.now() + timedelta(hours=3))  # future: not counted
        response, html = self.bar()
        hoje = html[html.index('Hoje') - 120:html.index('Alunos')]
        self.assertIn('>2<', hoje)       # one overdue + one erasure request
        agenda = html[html.index('>Agenda<'):html.index('>Financeiro<')]
        if timezone.localtime().hour or timezone.localtime().minute > 1:
            self.assertIn('>1<', agenda)
        self.assertIn('2 pendentes', html)

    def test_inactive_students_overdue_are_not_counted(self):
        Person.objects.filter(pk=self.alice.person.pk).update(is_active=False, plan_expires_on=self.today - timedelta(days=3))
        response, html = self.bar()
        self.assertNotIn('tab-badge', html)

    def test_staff_never_get_the_student_button(self):
        response, html = self.bar('core:student_list')
        self.assertNotIn('tab-center', html)
        self.assertNotIn('workout_start', html)

    def test_layout_for_the_trainer(self):
        page = self.client.get(reverse('core:finance')).content.decode()
        self.assertIn('<body class="has-tabbar">', page)
        self.assertIn('navbar-toggler d-none', page)
        self.assertIn('<footer class="d-none d-lg-block">', page)


class TodayScreenTests(BaseTestCase):
    def setUp(self):
        self.login(self.alice)
        self.person = self.alice.person

    def page(self):
        response = self.client.get(reverse('core:home'))
        self.assertEqual(response.status_code, 200)
        return response

    def test_ready_state_shows_the_workout_and_a_start_button(self):
        response = self.page()
        self.assertEqual(response.context['today']['state'], 'ready')
        self.assertContains(response, 'Oi, Alice!')
        self.assertContains(response, 'Treino de hoje')
        self.assertContains(response, self.alice_routine.routine_name)
        self.assertContains(response, '2 exercícios')
        self.assertContains(response, f'action="{reverse("core:workout_start", kwargs={"pk": self.alice_routine.pk})}"')
        self.assertContains(response, 'Iniciar treino')
        self.assertNotContains(response, 'Último:')

    def test_ready_state_shows_the_last_time(self):
        finish(self.alice_routine, when=timezone.now() - timedelta(days=3))
        response = self.page()
        self.assertContains(response, 'Último:')
        self.assertContains(response, f'{timezone.localtime(timezone.now() - timedelta(days=3)):%d/%m}')

    def test_in_progress_state(self):
        open_session(self.alice_routine, started_ago=timedelta(minutes=20))
        response = self.page()
        self.assertEqual(response.context['today']['state'], 'in_progress')
        self.assertContains(response, 'Treino em andamento')
        self.assertContains(response, 'Continuar treino')
        self.assertContains(response, reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}))
        self.assertNotContains(response, 'Iniciar treino')

    def test_done_today_state(self):
        finish(self.alice_routine, when=timezone.now() - timedelta(minutes=50), minutes=40)
        response = self.page()
        self.assertEqual(response.context['today']['state'], 'done')
        self.assertContains(response, 'Treino de hoje feito')
        self.assertContains(response, '40 min')
        self.assertContains(response, 'Fazer outro treino')

    def test_yesterdays_workout_does_not_count_as_done_today(self):
        finish(self.alice_routine, when=timezone.now() - timedelta(days=1, hours=1))
        self.assertEqual(self.page().context['today']['state'], 'ready')

    def test_empty_state(self):
        PersonRoutine.objects.filter(person=self.person).update(is_active=False)
        response = self.page()
        self.assertEqual(response.context['today']['state'], 'empty')
        self.assertContains(response, 'ainda não está pronto')
        self.assertNotContains(response, 'Iniciar treino')

    def test_old_stat_cards_are_gone_and_numbers_are_compact(self):
        finish(self.alice_routine, when=timezone.now() - timedelta(days=40), minutes=30)
        finish(self.alice_routine, when=timezone.now() - timedelta(days=2), minutes=50)
        response = self.page()
        today = response.context['today']
        self.assertEqual((today['total_workouts'], today['recent_workouts'], today['avg_duration']), (2, 1, 40))
        self.assertNotContains(response, 'Total de Treinos')
        self.assertContains(response, 'em 30 dias')

    def test_week_strip_marks_the_days_of_this_week(self):
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        finish(self.alice_routine, when=timezone.make_aware(datetime.combine(monday, time(8, 0))))
        finish(self.alice_routine, when=timezone.make_aware(datetime.combine(monday - timedelta(days=1), time(8, 0))))  # last week
        strip = week_strip(self.person, today)
        self.assertEqual([d['letter'] for d in strip], list('STQQSSD'))
        self.assertEqual([d['done'] for d in strip], [True] + [False] * 6)
        self.assertEqual(sum(d['is_today'] for d in strip), 1)
        self.assertEqual([d['future'] for d in strip], [d['date'] > today for d in strip])

    def test_week_strip_uses_local_time_at_midnight(self):
        today = date(2026, 5, 13)  # a Wednesday
        late = timezone.make_aware(datetime(2026, 5, 11, 23, 30))  # Monday 23:30 local = Tuesday in UTC
        finish(self.alice_routine, when=late)
        done = [d['done'] for d in week_strip(self.person, today)]
        self.assertEqual(done, [True, False, False, False, False, False, False])

    def test_week_goal_comes_from_the_plan(self):
        today = timezone.localdate()
        finish(self.alice_routine)
        self.assertRegex(self.page().content.decode(), r'1 treino\s*</small>')
        plan = Plan.objects.create(name='3x', months=1, sessions_per_week=3)
        Person.objects.filter(pk=self.person.pk).update(plan=plan)
        response = self.page()
        self.assertContains(response, '1 de 3 treinos')
        self.assertEqual(response.context['today']['week_goal'], 3)
        self.assertEqual(student_today(RequestFactory().get('/'), Person.objects.get(pk=self.person.pk))['today'], today)

    def test_greeting_uses_the_first_name_only(self):
        Person.objects.filter(pk=self.person.pk).update(person_name='Maria da Silva Souza')
        self.assertContains(self.page(), 'Oi, Maria!')
        self.assertNotContains(self.page(), 'Silva Souza')

    def test_start_from_the_hero_opens_the_workout(self):
        response = self.client.post(reverse('core:workout_start', kwargs={'pk': self.alice_routine.pk}))
        self.assertRedirects(response, reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}))
        self.assertEqual(self.client.get(reverse('core:home')).context['today']['state'], 'in_progress')

    def test_no_consent_still_renders_home(self):
        from core.models import ConsentRecord
        ConsentRecord.objects.filter(person=self.person).delete()
        self.assertEqual(self.client.get(reverse('core:home')).status_code, 200)

    def test_confidential_values_never_reach_the_screen(self):
        Person.objects.filter(pk=self.person.pk).update(hourly_rate=Decimal('187.35'), plan_price=Decimal('91.77'))
        html = self.page().content.decode() + tabbar_html(self.client.get(reverse('core:my_profile')))
        for value in ('187.35', '187,35', '91.77', '91,77'):
            self.assertNotIn(value, html)

    def test_trainers_home_is_unchanged(self):
        self.login(self.trainer)
        response = self.client.get(reverse('core:home'))
        self.assertContains(response, 'Painel de hoje')
        self.assertNotIn('today', response.context or {})


class DiscardWorkoutTests(BaseTestCase):
    def setUp(self):
        self.login(self.alice)
        self.session = open_session(self.alice_routine)
        self.url = reverse('core:workout_discard', kwargs={'pk': self.session.pk})

    def test_discards_an_untouched_session(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse('core:my_workouts'))
        self.assertFalse(WorkoutSession.objects.filter(pk=self.session.pk).exists())

    def test_discarding_removes_the_empty_logs_too(self):
        prx = PersonRoutineExercise.objects.filter(person_routine=self.alice_routine).first()
        log = SessionExercise.objects.create(session=self.session, person_routine_exercise=prx)
        SessionSet.objects.create(session_exercise=log, number=1)  # pre-filled but not done
        self.client.post(self.url)
        self.assertFalse(SessionExercise.objects.filter(pk=log.pk).exists())

    def test_refuses_when_a_set_was_done(self):
        prx = PersonRoutineExercise.objects.filter(person_routine=self.alice_routine).first()
        log = SessionExercise.objects.create(session=self.session, person_routine_exercise=prx)
        SessionSet.objects.create(session_exercise=log, number=1, is_done=True, weight=Decimal('20'), reps=10)
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}))
        self.assertTrue(WorkoutSession.objects.filter(pk=self.session.pk).exists())

    def test_refuses_when_there_are_notes(self):
        WorkoutSession.objects.filter(pk=self.session.pk).update(notes='dor no ombro')
        self.client.post(self.url)
        self.assertTrue(WorkoutSession.objects.filter(pk=self.session.pk).exists())
        prx = PersonRoutineExercise.objects.filter(person_routine=self.alice_routine).first()
        WorkoutSession.objects.filter(pk=self.session.pk).update(notes='')
        SessionExercise.objects.create(session=self.session, person_routine_exercise=prx, notes='ajuste')
        self.client.post(self.url)
        self.assertTrue(WorkoutSession.objects.filter(pk=self.session.pk).exists())

    def test_only_the_owner_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.login(self.bob)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.login(self.trainer)
        self.assertEqual(self.client.post(self.url).status_code, 302)  # no student file: sent home
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertTrue(WorkoutSession.objects.filter(pk=self.session.pk).exists())

    def test_finished_sessions_cannot_be_discarded(self):
        done = finish(self.alice_routine)
        self.assertEqual(self.client.post(reverse('core:workout_discard', kwargs={'pk': done.pk})).status_code, 404)
        self.assertTrue(WorkoutSession.objects.filter(pk=done.pk).exists())

    def test_the_workout_screen_offers_it(self):
        page = self.client.get(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk}))
        self.assertContains(page, self.url)
        self.assertContains(page, 'Descartar treino')


