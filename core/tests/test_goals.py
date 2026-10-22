"""Goals with a deadline: progress along the assessments, the deadline states, the screens, the reminders and the summary."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import goals, notifications, reminders
from core.models import Assessment, Goal, Notice, NotificationPreference, Person, StudentEvent

from .base import BaseTestCase

TODAY = date(2026, 10, 20)
LATE_MORNING = timezone.make_aware(datetime(2026, 10, 20, 10, 30))
CONFIG = dict(NOTIFICATIONS_ASYNC=False, REMINDER_NUDGE_HOUR=10, REMINDER_DIGEST_HOUR=7, REMINDER_INACTIVE_DAYS=0,
              REMINDER_REASSESSMENT_DAYS=0)


class Base(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person

    def assess(self, days_ago, weight=None, waist=None):
        return Assessment.objects.create(person=self.person, date=TODAY - timedelta(days=days_ago),
                                         weight_kg=Decimal(str(weight)) if weight is not None else None,
                                         waist_cm=Decimal(str(waist)) if waist is not None else None)

    def goal(self, days=30, **extra):
        extra.setdefault('title', 'Chegar aos 70 kg')
        return Goal.objects.create(person=self.person, target_date=TODAY + timedelta(days=days), **extra)


class ProgressTests(Base):
    def test_losing_weight_moves_from_the_start_to_the_target(self):
        goal = self.goal(metric='weight_kg', start_value=Decimal('80'), target_value=Decimal('70'))
        self.assess(0, weight=75)
        result = goals.progress(goal)
        self.assertEqual((result['pct'], result['reached'], result['current']), (50, False, Decimal('75.00')))

    def test_gaining_works_the_same_way(self):
        goal = self.goal(metric='arm_cm', start_value=Decimal('30'), target_value=Decimal('34'))
        Assessment.objects.create(person=self.person, date=TODAY, arm_cm=Decimal('33'))
        self.assertEqual(goals.progress(goal)['pct'], 75)

    def test_reaching_or_passing_the_target_is_reported_and_capped_at_100(self):
        goal = self.goal(metric='weight_kg', start_value=Decimal('80'), target_value=Decimal('70'))
        self.assess(0, weight=68)
        result = goals.progress(goal)
        self.assertEqual((result['pct'], result['reached']), (100, True))

    def test_going_the_wrong_way_is_zero_not_negative(self):
        goal = self.goal(metric='weight_kg', start_value=Decimal('80'), target_value=Decimal('70'))
        self.assess(0, weight=85)
        self.assertEqual(goals.progress(goal)['pct'], 0)

    def test_only_the_latest_assessment_that_has_the_measure_counts(self):
        goal = self.goal(metric='weight_kg', start_value=Decimal('80'), target_value=Decimal('70'))
        self.assess(10, weight=78)
        self.assess(1, waist=90)                           # newer, but without weight
        self.assertEqual(goals.progress(goal)['current'], Decimal('78.00'))

    def test_no_progress_for_a_text_goal_or_without_a_measurement(self):
        self.assertIsNone(goals.progress(self.goal()))
        self.assertIsNone(goals.progress(self.goal(metric='weight_kg', start_value=Decimal('80'), target_value=Decimal('70'))))

    def test_without_a_start_value_the_first_assessment_is_the_start(self):
        goal = self.goal(metric='weight_kg', target_value=Decimal('70'))
        self.assess(0, weight=72)
        self.assertEqual(goals.progress(goal)['pct'], 0)
        self.assess(0, weight=70)
        self.assertEqual(goals.progress(goal)['pct'], 100)

    def test_the_start_value_comes_from_the_latest_assessment_up_to_a_day(self):
        self.assess(10, weight=80)
        self.assess(-5, weight=70)                          # in the future: not the start
        self.assertEqual(goals.starting_value(self.person, 'weight_kg', TODAY), Decimal('80.00'))
        self.assertIsNone(goals.starting_value(self.person, 'hip_cm', TODAY))


class DueStateTests(Base):
    def test_the_states(self):
        for days, state in ((30, 'later'), (7, 'soon'), (0, 'soon'), (-3, 'overdue')):
            self.assertEqual(goals.due_label(self.goal(days), TODAY), (state, abs(days)), days)
        closed = self.goal(2, status='achieved')
        self.assertEqual(goals.due_label(closed, TODAY)[0], 'closed')

    def test_the_rows_put_open_goals_first_by_deadline(self):
        late = self.goal(40, title='Tarde')
        done = self.goal(1, title='Feita', status='achieved')
        soon = self.goal(5, title='Cedo')
        self.assertEqual([r['goal'].title for r in goals.describe([late, done, soon], TODAY)], ['Cedo', 'Tarde', 'Feita'])

    def test_attention_is_active_goals_overdue_or_due_within_a_week_of_active_students(self):
        near, overdue = self.goal(3), self.goal(-4)
        self.goal(30)
        self.goal(2, status='dropped')
        other = Goal.objects.create(person=self.bob.person, title='x', target_date=TODAY + timedelta(days=2))
        Person.objects.filter(pk=self.bob.person.pk).update(is_active=False)
        self.assertEqual(set(goals.attention(TODAY)), {near, overdue})
        self.assertNotIn(other, goals.attention(TODAY))


class ScreenTests(Base):
    def setUp(self):
        super().setUp()
        self.login(self.trainer)
        self.tab = reverse('core:student_detail', kwargs={'pk': self.person.pk})
        self.url = reverse('core:goal_create', kwargs={'pk': self.person.pk})

    def post(self, **fields):
        data = {'title': 'Chegar aos 70 kg', 'target_date': (timezone.localdate() + timedelta(days=60)).isoformat(),
                'metric': 'weight_kg', 'target_value': '70', 'start_value': ''}
        data.update(fields)
        return self.client.post(self.url, data)

    def test_the_tab_lists_goals_with_progress_and_the_trainers_buttons(self):
        goal = Goal.objects.create(person=self.person, title='Meta visível', target_date=timezone.localdate() + timedelta(days=10),
                                   metric='weight_kg', start_value=Decimal('80'), target_value=Decimal('70'))
        Assessment.objects.create(person=self.person, date=timezone.localdate(), weight_kg=Decimal('75'))
        page = self.client.get(self.tab).content.decode()
        self.assertIn(f'data-goal="{goal.pk}"', page)
        self.assertIn('data-goal-progress="50"', page)
        self.assertIn('data-goal-new', page)
        self.assertIn(reverse('core:goal_status', kwargs={'pk': goal.pk}), page)

    def test_creating_a_goal_fills_the_start_from_the_latest_assessment_and_logs_it(self):
        Assessment.objects.create(person=self.person, date=timezone.localdate(), weight_kg=Decimal('82'))
        response = self.post()
        self.assertEqual(response.status_code, 302)
        goal = Goal.objects.get()
        self.assertEqual((goal.start_value, goal.target_value, goal.status), (Decimal('82.0'), Decimal('70.0'), 'active'))
        self.assertTrue(StudentEvent.objects.filter(person=self.person, kind='goal').exists())

    def test_a_text_goal_needs_no_numbers_and_drops_any_that_were_sent_without_a_measure(self):
        self.post(title='Correr 5 km', metric='', target_value='', start_value='')
        self.assertEqual((Goal.objects.get().metric, Goal.objects.get().target_value), ('', None))

    def test_the_form_refuses_what_makes_no_sense(self):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        for fields, part in (({'title': '  '}, 'obrigatório'), ({'target_date': yesterday}, 'não pode ser no passado'),
                             ({'target_value': ''}, 'Informe o valor'), ({'metric': ''}, 'Escolha a medida'),
                             ({'target_value': '0'}, 'maior que zero')):
            self.assertContains(self.post(**fields), part)
        self.assertEqual(Goal.objects.count(), 0)

    def test_editing_keeps_a_deadline_that_has_already_passed(self):
        goal = Goal.objects.create(person=self.person, title='Velha', target_date=timezone.localdate() - timedelta(days=5))
        response = self.client.post(reverse('core:goal_edit', kwargs={'pk': goal.pk}), {
            'title': 'Velha renomeada', 'target_date': goal.target_date.isoformat(), 'metric': '', 'target_value': '', 'start_value': ''})
        self.assertEqual(response.status_code, 302)
        goal.refresh_from_db()
        self.assertEqual(goal.title, 'Velha renomeada')

    def test_marking_as_achieved_dropped_and_reopening(self):
        goal = Goal.objects.create(person=self.person, title='G', target_date=timezone.localdate() + timedelta(days=5))
        url = reverse('core:goal_status', kwargs={'pk': goal.pk})
        self.client.post(url, {'status': 'achieved'})
        goal.refresh_from_db()
        self.assertEqual((goal.status, goal.closed_on), ('achieved', timezone.localdate()))
        self.client.post(url, {'status': 'active'})
        goal.refresh_from_db()
        self.assertEqual((goal.status, goal.closed_on), ('active', None))
        self.assertEqual(self.client.post(url, {'status': 'weird'}).status_code, 400)

    def test_removing_a_goal(self):
        goal = Goal.objects.create(person=self.person, title='G', target_date=timezone.localdate())
        self.client.post(reverse('core:goal_delete', kwargs={'pk': goal.pk}))
        self.assertFalse(Goal.objects.exists())

    def test_the_student_sees_her_goals_read_only(self):
        Goal.objects.create(person=self.person, title='Minha meta', target_date=timezone.localdate() + timedelta(days=10))
        Goal.objects.create(person=self.bob.person, title='Meta do outro', target_date=timezone.localdate() + timedelta(days=10))
        self.login(self.alice)
        page = self.client.get(reverse('core:my_evolution')).content.decode()
        self.assertIn('Minha meta', page)
        self.assertNotIn('Meta do outro', page)
        self.assertNotIn('data-goal-new', page)
        self.assertNotIn('goal_status', page)

    def test_only_the_trainer_can_change_goals(self):
        goal = Goal.objects.create(person=self.person, title='G', target_date=timezone.localdate())
        self.login(self.alice)
        for url in (self.url, reverse('core:goal_edit', kwargs={'pk': goal.pk})):
            self.assertEqual(self.client.get(url).status_code, 403)
        for name in ('goal_status', 'goal_delete'):
            self.assertEqual(self.client.post(reverse(f'core:{name}', kwargs={'pk': goal.pk}), {'status': 'achieved'}).status_code, 403)
        self.assertEqual(Goal.objects.get().status, 'active')

    def test_the_students_own_export_includes_the_goals(self):
        Goal.objects.create(person=self.person, title='Exportada', target_date=timezone.localdate())
        self.login(self.alice)
        from core import exports
        self.assertEqual([g['title'] for g in exports.build_data(self.person, include_internal_notes=False)['goals']], ['Exportada'])


@override_settings(**CONFIG)
class ReminderTests(Base):
    def notices(self):
        return list(Notice.objects.filter(user=self.alice, kind='goal_due'))

    def run_nudges(self, now=LATE_MORNING):
        return reminders.send_student_nudges(now, timezone.localdate(now))

    def test_a_goal_close_to_its_deadline_is_told_once_without_its_words(self):
        goal = self.goal(5, title='Emagrecer 8 kg por causa da diabetes')
        self.assertEqual(self.run_nudges()['goals'], 1)
        [notice] = self.notices()
        self.assertIn('Faltam 5 dias', notice.body)
        self.assertNotIn('diabetes', f'{notice.title} {notice.body}')
        self.assertEqual(notice.path, reverse('core:my_evolution'))
        goal.refresh_from_db()
        self.assertEqual(goal.reminded_at, LATE_MORNING)
        self.assertEqual(self.run_nudges()['goals'], 0)

    def test_the_day_itself_says_today(self):
        self.goal(0)
        self.run_nudges()
        self.assertIn('é hoje', self.notices()[0].body)

    def test_far_past_closed_and_other_peoples_goals_are_left_alone(self):
        self.goal(30)
        self.goal(-2)
        self.goal(3, status='achieved')
        self.assertEqual(self.run_nudges()['goals'], 0)

    def test_the_switch_a_student_without_the_app_and_the_window_are_respected(self):
        self.goal(3)
        NotificationPreference.objects.create(user=self.alice, notify_goals=False)
        self.run_nudges()
        self.assertEqual(self.notices(), [])
        NotificationPreference.objects.all().delete()
        early = timezone.make_aware(datetime(2026, 10, 20, 6, 0))
        self.assertEqual(self.run_nudges(early)['goals'], 0)
        Person.objects.filter(pk=self.person.pk).update(has_app=False)
        self.assertEqual(self.run_nudges()['goals'], 0)

    def test_the_morning_summary_counts_goals_due_or_overdue(self):
        self.goal(3)
        self.goal(-1)
        morning = timezone.make_aware(datetime(2026, 10, 20, 7, 30))
        self.assertEqual(reminders.send_daily_digest(morning, TODAY), 1)
        self.assertIn('2 metas vencidas ou vencendo', Notice.objects.get(kind='daily_digest', user=self.trainer).body)

    def test_the_messages_and_the_switch(self):
        self.assertEqual(notifications.KIND_FLAG['goal_due'], 'notify_goals')
        self.assertEqual(str(notifications.daily_digest({'goals': 1}).body), '1 meta vencida ou vencendo')
        self.login(self.alice)
        page = self.client.get(reverse('core:notification_settings')).content.decode()
        self.assertIn('id="notify_goals"', page)
        self.client.post(reverse('core:notification_settings'), {'types': '1', 'notify_changes': 'on'})
        self.assertFalse(NotificationPreference.objects.get(user=self.alice).notify_goals)
