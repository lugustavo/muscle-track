"""Periodization: phases in order, where the student is today, the standard cycles, the screens and the trainer's reminder."""
from datetime import date, datetime, timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone, translation

from core import notifications, periodization, reminders
from core.models import Notice, NotificationPreference, TrainingPhase

from .base import BaseTestCase

TODAY = date(2026, 10, 20)
LATE_MORNING = timezone.make_aware(datetime(2026, 10, 20, 10, 30))
CONFIG = dict(NOTIFICATIONS_ASYNC=False, REMINDER_NUDGE_HOUR=10, REMINDER_DIGEST_HOUR=7, REMINDER_INACTIVE_DAYS=0,
              REMINDER_REASSESSMENT_DAYS=0)


class Base(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person

    def phase(self, start_days=0, weeks=4, name='Hipertrofia', person=None, **extra):
        return TrainingPhase.objects.create(person=person or self.person, name=name, start_date=TODAY + timedelta(days=start_days),
                                            weeks=weeks, **extra)


class LogicTests(Base):
    def test_a_phase_ends_on_the_last_day_of_its_weeks(self):
        self.assertEqual(self.phase(0, 4).end_date, TODAY + timedelta(days=27))

    def test_the_current_phase_is_the_one_that_includes_today(self):
        before, now, after = self.phase(-40, 2, 'A'), self.phase(-3, 4, 'B'), self.phase(25, 2, 'C')
        phases = [before, now, after]
        self.assertEqual(periodization.current(phases, TODAY), now)
        self.assertIsNone(periodization.current([before, after], TODAY))     # a gap has no current phase

    def test_the_first_and_last_day_belong_to_the_phase(self):
        p = self.phase(0, 1)
        self.assertEqual(periodization.current([p], TODAY), p)
        self.assertEqual(periodization.current([p], TODAY + timedelta(days=6)), p)
        self.assertIsNone(periodization.current([p], TODAY + timedelta(days=7)))

    def test_describe_marks_past_current_next_and_later_with_the_week(self):
        past, now, nxt, later = self.phase(-60, 2, 'P'), self.phase(-10, 4, 'N'), self.phase(18, 2, 'X'), self.phase(40, 2, 'L')
        rows = periodization.describe([later, nxt, now, past], TODAY)     # given out of order
        self.assertEqual([r['state'] for r in rows], ['past', 'current', 'next', 'later'])
        current = rows[1]
        self.assertEqual((current['week'], current['days_left']), (2, 17))
        self.assertEqual(current['pct'], round(100 * 11 / 28))
        self.assertEqual(rows[2]['days_to_start'], 18)

    def test_the_next_start_follows_the_last_phase_or_is_today(self):
        self.assertEqual(periodization.next_start(self.person, TODAY), TODAY)
        last = self.phase(0, 2)
        self.assertEqual(periodization.next_start(self.person, TODAY), last.end_date + timedelta(days=1))
        self.assertEqual(periodization.next_start(self.person, TODAY + timedelta(days=60)), TODAY + timedelta(days=60))

    def test_a_standard_cycle_creates_back_to_back_phases(self):
        with translation.override('pt-br'):
            created = periodization.create_preset(self.person, 'hypertrophy12', TODAY)
        self.assertEqual([(p.name, p.weeks) for p in created], [('Adaptação', 2), ('Hipertrofia', 6), ('Força', 3), ('Deload', 1)])
        for before, after in zip(created, created[1:]):
            self.assertEqual(after.start_date, before.end_date + timedelta(days=1))
        self.assertEqual(sum(p.weeks for p in created), 12)

    def test_a_standard_cycle_is_written_in_the_language_of_the_trainer(self):
        with translation.override('en'):
            created = periodization.create_preset(self.person, 'strength8', TODAY)
        self.assertEqual(created[0].name, 'Base')
        self.assertEqual(created[-1].name, 'Deload')
        self.assertNotIn('Intensificação', [p.name for p in created])

    def test_ending_lists_the_last_phase_of_an_active_student_close_to_its_end(self):
        soon = self.phase(-20, 4)                                 # ends in 7 days
        self.phase(-100, 2, 'old', person=self.bob.person)          # long over
        self.assertEqual(periodization.ending(TODAY), [soon])

    def test_a_phase_with_a_next_one_or_far_from_its_end_is_not_ending(self):
        self.phase(-20, 4)
        self.phase(8, 4, 'next')
        self.assertEqual(periodization.ending(TODAY), [])
        TrainingPhase.objects.all().delete()
        self.phase(-2, 8)
        self.assertEqual(periodization.ending(TODAY), [])

    def test_an_inactive_student_is_left_out(self):
        self.phase(-20, 4)
        self.person.is_active = False
        self.person.save(update_fields=['is_active'])
        self.assertEqual(periodization.ending(TODAY), [])


class ScreenTests(Base):
    def setUp(self):
        super().setUp()
        self.login(self.trainer)
        self.tab = reverse('core:student_detail', kwargs={'pk': self.person.pk})

    def test_the_treinos_tab_shows_the_phases_and_the_trainers_buttons(self):
        today = timezone.localdate()
        p = TrainingPhase.objects.create(person=self.person, name='Fase visível', start_date=today, weeks=4)
        page = self.client.get(self.tab).content.decode()
        self.assertIn(f'data-phase="{p.pk}"', page)
        self.assertIn('data-phase-progress', page)
        self.assertIn('data-phase-new', page)
        self.assertIn('data-phase-preset', page)
        self.assertIn(reverse('core:phase_delete', kwargs={'pk': p.pk}), page)

    def test_an_empty_student_sees_the_hint_and_the_buttons(self):
        page = self.client.get(self.tab).content.decode()
        self.assertIn('data-phases', page)
        self.assertIn('Sem fases ainda', page)

    def test_creating_a_phase_starts_after_the_last_one_by_default(self):
        TrainingPhase.objects.create(person=self.person, name='Antes', start_date=timezone.localdate(), weeks=2)
        page = self.client.get(reverse('core:phase_create', kwargs={'pk': self.person.pk})).content.decode()
        expected = (timezone.localdate() + timedelta(days=14)).isoformat()
        self.assertIn(f'value="{expected}"', page)

    def test_creating_a_phase(self):
        response = self.client.post(reverse('core:phase_create', kwargs={'pk': self.person.pk}), {
            'name': ' Força ', 'focus': 'Cargas altas', 'start_date': TODAY.isoformat(), 'weeks': '3', 'routine': ''})
        self.assertEqual(response.status_code, 302)
        p = TrainingPhase.objects.get()
        self.assertEqual((p.name, p.weeks, p.person_id, p.routine_id), ('Força', 3, self.person.pk, None))

    def test_a_phase_needs_a_name_and_between_1_and_52_weeks(self):
        url = reverse('core:phase_create', kwargs={'pk': self.person.pk})
        base = {'name': 'X', 'start_date': TODAY.isoformat(), 'weeks': '4'}
        for bad in ({'name': '   '}, {'weeks': '0'}, {'weeks': '53'}, {'start_date': ''}):
            self.assertEqual(self.client.post(url, {**base, **bad}).status_code, 200, bad)
        self.assertEqual(TrainingPhase.objects.count(), 0)

    def test_the_routine_choice_is_limited_to_the_students_active_routines(self):
        url = reverse('core:phase_create', kwargs={'pk': self.person.pk})
        mine, theirs = self.alice_routine, self.bob_routine
        ok = self.client.post(url, {'name': 'X', 'start_date': TODAY.isoformat(), 'weeks': '2', 'routine': mine.pk})
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(TrainingPhase.objects.get().routine_id, mine.pk)
        bad = self.client.post(url, {'name': 'Y', 'start_date': TODAY.isoformat(), 'weeks': '2', 'routine': theirs.pk})
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(TrainingPhase.objects.count(), 1)

    def test_editing_clears_the_reminder_so_a_moved_phase_is_announced_again(self):
        p = self.phase(0, 2, reminded_at=LATE_MORNING)
        response = self.client.post(reverse('core:phase_edit', kwargs={'pk': p.pk}), {
            'name': 'Novo', 'focus': '', 'start_date': TODAY.isoformat(), 'weeks': '3', 'routine': ''})
        self.assertEqual(response.status_code, 302)
        p.refresh_from_db()
        self.assertEqual((p.name, p.weeks, p.reminded_at), ('Novo', 3, None))

    def test_removing_a_phase_is_post_only(self):
        p = self.phase()
        url = reverse('core:phase_delete', kwargs={'pk': p.pk})
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(TrainingPhase.objects.exists())

    def test_the_preset_screen_creates_the_cycle(self):
        url = reverse('core:phase_preset', kwargs={'pk': self.person.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {'preset': 'strength8', 'start_date': TODAY.isoformat()}).status_code, 302)
        self.assertEqual(TrainingPhase.objects.filter(person=self.person).count(), 3)
        self.assertEqual(self.client.post(url, {'preset': 'nope', 'start_date': TODAY.isoformat()}).status_code, 200)
        self.assertEqual(TrainingPhase.objects.count(), 3)

    def test_a_student_cannot_use_the_trainers_screens(self):
        p = self.phase()
        self.login(self.alice)
        for name, kwargs in (('phase_create', {'pk': self.person.pk}), ('phase_preset', {'pk': self.person.pk}),
                             ('phase_edit', {'pk': p.pk})):
            self.assertEqual(self.client.get(reverse(f'core:{name}', kwargs=kwargs)).status_code, 403, name)
        self.assertEqual(self.client.post(reverse('core:phase_delete', kwargs={'pk': p.pk})).status_code, 403)
        self.assertTrue(TrainingPhase.objects.exists())


class StudentViewTests(Base):
    def test_the_student_sees_the_current_and_next_phase_read_only(self):
        today = timezone.localdate()
        TrainingPhase.objects.create(person=self.person, name='Fase de agora', focus='Volume alto', start_date=today, weeks=4)
        TrainingPhase.objects.create(person=self.person, name='Fase seguinte', start_date=today + timedelta(days=28), weeks=2)
        TrainingPhase.objects.create(person=self.person, name='Fase distante', start_date=today + timedelta(days=60), weeks=2)
        self.login(self.alice)
        page = self.client.get(reverse('core:my_workouts')).content.decode()
        self.assertIn('Fase de agora', page)
        self.assertIn('Volume alto', page)
        self.assertIn('Fase seguinte', page)
        self.assertNotIn('Fase distante', page)
        for hidden in ('data-phase-new', 'data-phase-preset', 'data-phase-menu'):
            self.assertNotIn(hidden, page)

    def test_a_student_without_phases_sees_nothing_new(self):
        self.login(self.alice)
        self.assertNotIn('data-phases', self.client.get(reverse('core:my_workouts')).content.decode())

    def test_the_student_never_sees_the_phases_of_someone_else(self):
        self.phase(0, 4, 'Só do Bob', person=self.bob.person)
        self.login(self.alice)
        self.assertNotIn('Só do Bob', self.client.get(reverse('core:my_workouts')).content.decode())


@override_settings(**CONFIG)
class ReminderTests(Base):
    def notices(self):
        return list(Notice.objects.filter(user=self.trainer, kind='phase_ending'))

    def test_the_trainer_is_told_once_with_counts_and_no_names(self):
        p = self.phase(-22, 4, 'Hipertrofia secreta')
        self.phase(-22, 4, 'Outra', person=self.bob.person)
        self.assertEqual(reminders.send_phase_reminders(LATE_MORNING, TODAY), 2)
        [notice] = self.notices()
        self.assertIn('2 alunos', notice.body)
        self.assertNotIn('Alice', f'{notice.title} {notice.body}')
        self.assertNotIn('secreta', f'{notice.title} {notice.body}')
        p.refresh_from_db()
        self.assertEqual(p.reminded_at, LATE_MORNING)
        self.assertEqual(reminders.send_phase_reminders(LATE_MORNING, TODAY), 0)
        self.assertEqual(len(self.notices()), 1)

    def test_singular_text(self):
        self.phase(-22, 4)
        reminders.send_phase_reminders(LATE_MORNING, TODAY)
        self.assertIn('1 aluno termina', self.notices()[0].body)

    def test_only_in_the_morning_window(self):
        self.phase(-22, 4)
        early = timezone.make_aware(datetime(2026, 10, 20, 5, 0))
        self.assertEqual(reminders.send_phase_reminders(early, TODAY), 0)
        self.assertEqual(self.notices(), [])

    def test_the_switch_is_respected(self):
        self.phase(-22, 4)
        NotificationPreference.objects.create(user=self.trainer, notify_phases=False)
        reminders.send_phase_reminders(LATE_MORNING, TODAY)
        self.assertEqual(self.notices(), [])

    def test_a_phase_with_a_next_one_is_not_announced(self):
        self.phase(-22, 4)
        self.phase(6, 4, 'seguinte')
        self.assertEqual(reminders.send_phase_reminders(LATE_MORNING, TODAY), 0)

    def test_the_settings_page_saves_the_switch(self):
        self.login(self.trainer)
        url = reverse('core:notification_settings')
        self.assertIn('notify_phases', self.client.get(url).content.decode())
        self.client.post(url, {'types': '1'})
        self.assertFalse(NotificationPreference.objects.get(user=self.trainer).notify_phases)
        self.client.post(url, {'types': '1', 'notify_phases': 'on'})
        self.assertTrue(NotificationPreference.objects.get(user=self.trainer).notify_phases)

    def test_the_morning_summary_counts_them(self):
        self.phase(-22, 4)
        self.assertEqual(reminders.digest_summary(LATE_MORNING, TODAY)['phases'], 1)
        body = str(notifications.daily_digest({'phases': 2}).body)
        self.assertIn('2 fases de treino terminando', body)
