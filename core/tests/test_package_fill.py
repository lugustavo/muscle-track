from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core import package_schedule, packages
from core.agenda import ensure_appointments
from core.models import Appointment, Package, ScheduleSlot

from .base import BaseTestCase

MON = date(2026, 10, 19)          # a Monday
MON_THU = [0, 3]
SEVEN = time(7, 0)


def make_pack(sessions=6, days=None):
    return Package.objects.create(name=f'Pacote {sessions} ({days})', sessions=sessions, valid_days=days, pricing_mode='per_session',
                                  price_per_session=Decimal('50'))


def when(day, hour=7):
    return timezone.make_aware(datetime.combine(day, time(hour)))


class FillBase(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.sold = packages.sell(self.person, make_pack(6))

    def plan(self):
        return packages.schedule_plan(self.person)

    def preview(self, weekdays=MON_THU, start=MON, skip=(), time_=SEVEN, duration=60, interval=1):
        return package_schedule.build_preview(self.person, self.plan(), weekdays, start, time_, duration, interval, skip)

    def fill(self, **kw):
        args = dict(weekdays=MON_THU, start=MON, start_time=SEVEN, duration=60, interval=1, skip=())
        args.update(kw)
        return package_schedule.fill_series(self.person, self.plan(), **args)

    def classes(self):
        return list(Appointment.objects.filter(person=self.person).order_by('starts_at'))


class PreviewTests(FillBase):
    def test_the_example_six_classes_monday_and_thursday_from_19_october(self):
        preview = self.preview()
        self.assertEqual([d.isoformat() for d in preview['new_dates']],
                         ['2026-10-19', '2026-10-22', '2026-10-26', '2026-10-29', '2026-11-02', '2026-11-05'])
        self.assertEqual((preview['last'], preview['count'], preview['wanted']), (date(2026, 11, 5), 6, 6))

    def test_first_day_that_is_not_a_chosen_weekday_starts_at_the_next_one(self):
        preview = self.preview(start=date(2026, 10, 21))      # a Wednesday
        self.assertEqual(preview['new_dates'][0], date(2026, 10, 22))

    def test_already_marked_classes_reduce_what_is_needed(self):
        for n in (1, 2):
            Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=n))
        self.assertEqual(self.preview()['count'], 4)

    def test_unticked_day_is_replaced_by_the_next_date(self):
        preview = self.preview(skip={date(2026, 10, 22)})
        self.assertEqual(preview['last'], date(2026, 11, 9))
        self.assertEqual([r['state'] for r in preview['rows']][:2], ['new', 'skipped'])
        self.assertEqual(preview['count'], 6)

    def test_a_day_the_student_already_has_a_class_is_not_counted(self):
        Appointment.objects.create(person=self.person, starts_at=when(date(2026, 10, 22)), status='attended')
        preview = self.preview()
        self.assertEqual([r['state'] for r in preview['rows']][:2], ['new', 'exists'])
        self.assertEqual(preview['last'], date(2026, 11, 9))

    def test_a_cancelled_class_at_that_time_does_not_block_the_day(self):
        Appointment.objects.create(person=self.person, starts_at=when(date(2026, 10, 22)), status='cancelled')
        self.assertEqual(self.preview()['last'], date(2026, 11, 5))

    def test_flags_overlap_with_another_students_class(self):
        Appointment.objects.create(person=self.bob.person, starts_at=when(date(2026, 10, 22)))
        rows = {r['date']: r for r in self.preview()['rows']}
        self.assertEqual(rows[date(2026, 10, 22)]['overlap'], ['Bob'])
        self.assertEqual(rows[date(2026, 10, 19)]['overlap'], [])

    def test_flags_dates_past_the_validity_of_a_package_that_already_started(self):
        sold = self.sold
        sold.delete()
        sold = packages.sell(self.person, make_pack(6, 20))
        Appointment.objects.create(person=self.person, status='attended', starts_at=when(date(2026, 10, 12)))
        packages.sync(self.person, date(2026, 10, 13))
        sold.refresh_from_db()
        plan = packages.schedule_plan(self.person, date(2026, 10, 13))
        self.assertEqual(plan['ends_on'], date(2026, 11, 1))
        preview = package_schedule.build_preview(self.person, plan, MON_THU, MON, SEVEN)
        self.assertEqual((preview['count'], preview['beyond']), (5, 1))      # one class was given: 5 to mark; 2/11 is after 1/11

    def test_not_started_package_uses_first_class_plus_validity(self):
        self.sold.delete()
        packages.sell(self.person, make_pack(6, 10))
        preview = self.preview()
        self.assertEqual(preview['deadline'], MON + timedelta(days=10))
        self.assertEqual(preview['beyond'], 2)       # 2/11 and 5/11 are after 29/10

    def test_without_weekdays_or_a_package_nothing_is_listed(self):
        self.assertEqual(self.preview(weekdays=[])['new_dates'], [])


class FillTests(FillBase):
    def test_creates_the_slots_and_every_class(self):
        result = self.fill()
        self.assertEqual((result['created'], result['last']), (6, date(2026, 11, 5)))
        slots = ScheduleSlot.objects.filter(person=self.person).order_by('weekday')
        self.assertEqual([(s.weekday, s.valid_until, s.package_id) for s in slots],
                         [(0, date(2026, 11, 5), self.sold.pk), (3, date(2026, 11, 5), self.sold.pk)])
        dates = [timezone.localtime(a.starts_at).date() for a in self.classes()]
        self.assertEqual(len(dates), 6)
        self.assertEqual(dates[-1], date(2026, 11, 5))
        self.assertTrue(all(a.slot_id and a.duration_minutes == 60 for a in self.classes()))
        self.assertEqual(self.plan()['to_schedule'], 0)

    def test_the_calendar_does_not_add_more_classes_later(self):
        self.fill()
        ensure_appointments(date(2026, 10, 1), date(2026, 12, 31), date(2026, 10, 20))
        self.assertEqual(len(self.classes()), 6)

    def test_skipped_days_are_never_generated_by_the_calendar(self):
        self.fill(skip={date(2026, 10, 22)})
        ensure_appointments(date(2026, 10, 1), date(2026, 12, 31), date(2026, 10, 20))
        dates = [timezone.localtime(a.starts_at).date() for a in self.classes()]
        self.assertNotIn(date(2026, 10, 22), dates)
        self.assertEqual((len(dates), dates[-1]), (6, date(2026, 11, 9)))
        self.assertEqual(ScheduleSlot.objects.get(person=self.person, weekday=3).skipped_dates, ['2026-10-22'])

    def test_a_single_weekday_creates_a_single_slot(self):
        self.fill(weekdays=[0])
        self.assertEqual(ScheduleSlot.objects.filter(person=self.person).count(), 1)
        self.assertEqual(self.fill_last(), date(2026, 11, 23))

    def fill_last(self):
        return timezone.localtime(self.classes()[-1].starts_at).date()

    def test_every_other_week_and_another_duration(self):
        result = self.fill(weekdays=[0], interval=2, duration=45)
        self.assertEqual(result['last'], MON + timedelta(weeks=10))
        self.assertEqual({a.duration_minutes for a in self.classes()}, {45})

    def test_filling_twice_is_refused_once_the_package_is_complete(self):
        self.fill()
        with self.assertRaises(package_schedule.FillError):
            self.fill()

    def test_an_active_slot_at_the_same_day_and_time_is_not_overwritten(self):
        ScheduleSlot.objects.create(person=self.person, weekday=0, start_time=SEVEN, valid_from=MON - timedelta(weeks=3),
                                    valid_until=date(2030, 1, 1))
        with self.assertRaises(package_schedule.FillError):
            self.fill()
        self.assertEqual(Appointment.objects.count(), 0)

    def test_an_ended_slot_at_the_same_day_and_time_is_reused(self):
        old = ScheduleSlot.objects.create(person=self.person, weekday=0, start_time=SEVEN, valid_from=date(2025, 1, 6),
                                          valid_until=date(2025, 3, 1))
        self.fill()
        old.refresh_from_db()
        self.assertEqual((old.valid_from, old.valid_until, old.package_id), (MON, date(2026, 11, 5), self.sold.pk))


class TopUpTests(FillBase):
    def test_a_free_cancellation_opens_one_more_class_at_the_end(self):
        self.fill()
        first = self.classes()[1]
        first.status = 'cancelled'
        first.save()
        self.assertEqual(self.plan()['to_schedule'], 1)
        result = package_schedule.top_up(self.person, self.plan())
        self.assertEqual((result['created'], result['last']), (1, date(2026, 11, 9)))      # the Monday after the 5th
        self.assertEqual(self.plan()['to_schedule'], 0)
        self.assertEqual(ScheduleSlot.objects.get(person=self.person, weekday=0).valid_until, date(2026, 11, 9))

    def test_several_missing_classes_follow_the_pattern(self):
        self.fill()
        for a in self.classes()[:2]:
            a.status = 'cancelled'
            a.save()
        result = package_schedule.top_up(self.person, self.plan())
        self.assertEqual((result['created'], result['last']), (2, date(2026, 11, 12)))

    def test_late_cancellation_uses_the_package_so_nothing_is_missing(self):
        self.fill()
        late = self.classes()[0]
        late.status, late.cancelled_late = 'cancelled', True
        late.save()
        packages.sync(self.person)
        self.assertEqual(self.plan()['to_schedule'], 0)

    def test_nothing_to_do_or_no_series(self):
        with self.assertRaises(package_schedule.FillError):
            package_schedule.top_up(self.person, self.plan())          # no series yet
        self.fill()
        with self.assertRaises(package_schedule.FillError):
            package_schedule.top_up(self.person, self.plan())          # complete


class ViewTests(FillBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.trainer)
        self.base = {'weekdays': ['0', '3'], 'starts_on': MON.isoformat(), 'start_time': '07:00',
                     'duration_minutes': '60', 'interval_weeks': '1'}

    def url(self, name):
        return reverse(f'core:{name}', kwargs={'pk': self.person.pk})

    def test_preview_fragment(self):
        response = self.client.get(self.url('slot_preview'), self.base)
        html = response.content.decode()
        self.assertIn('data-count="6"', html)
        self.assertEqual(html.count('data-skip-date='), 6)
        self.assertIn('05/11/2026', html)
        self.assertIn('no-store', response['Cache-Control'])

    def test_preview_with_a_skip_and_with_a_missing_field(self):
        html = self.client.get(self.url('slot_preview'), {**self.base, 'skip': '2026-10-22'}).content.decode()
        self.assertIn('sem aula', html)
        self.assertIn('09/11/2026', html)
        html = self.client.get(self.url('slot_preview'), {'weekdays': ['0']}).content.decode()
        self.assertIn('Escolha os dias', html)

    def test_fill_posts_and_reports(self):
        response = self.client.post(self.url('slot_fill'), self.base)
        self.assertIn('#tab-agenda', response['Location'])
        self.assertEqual(len(self.classes()), 6)
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any('6 aulas marcadas, até 05/11/2026' in m for m in messages))

    def test_fill_with_skips_and_bad_dates_ignored(self):
        self.client.post(self.url('slot_fill'), {**self.base, 'skip': ['2026-10-22', 'not-a-date']})
        dates = [timezone.localtime(a.starts_at).date() for a in self.classes()]
        self.assertNotIn(date(2026, 10, 22), dates)

    def test_fill_errors_are_messages(self):
        response = self.client.post(self.url('slot_fill'), {**self.base, 'weekdays': []})
        self.assertEqual(self.classes(), [])
        self.assertTrue(any('Horário inválido' in str(m) for m in response.wsgi_request._messages))
        self.sold.delete()
        response = self.client.post(self.url('slot_fill'), self.base)
        self.assertTrue(any('não tem pacote' in str(m) for m in response.wsgi_request._messages))

    def test_topup_view(self):
        self.client.post(self.url('slot_fill'), self.base)
        self.classes()[0].delete()
        response = self.client.post(self.url('slot_topup'))
        self.assertEqual(len(self.classes()), 6)
        self.assertTrue(any('1 aula a mais marcada' in str(m) for m in response.wsgi_request._messages))

    def test_only_the_trainer_and_only_post_for_changes(self):
        self.assertEqual(self.client.get(self.url('slot_fill')).status_code, 405)
        self.assertEqual(self.client.get(self.url('slot_topup')).status_code, 405)
        self.client.force_login(self.alice)
        for name in ('slot_preview', 'slot_fill', 'slot_topup'):
            self.assertEqual(self.client.get(self.url(name)).status_code, 403, name)
        self.assertEqual(self.client.post(self.url('slot_fill'), self.base).status_code, 403)
        self.assertEqual(self.classes(), [])


class TabTests(FillBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.trainer)
        self.page = lambda: self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).content.decode()

    def test_form_is_the_main_thing_with_a_package(self):
        html = self.page()
        self.assertIn('data-slot-fill', html)
        self.assertIn('Preencher agenda (6 aulas)', html)
        self.assertIn('Faltam marcar <strong>6</strong>', html)
        self.assertNotIn('data-slot-topup', html)                 # no series yet
        self.assertIn('data-slot-advanced', html)                 # the old form, tucked away
        self.assertNotIn('data-slot-advanced open', html.replace('\n', ' '))

    def test_complete_package_hides_the_form_and_says_so(self):
        self.fill()
        html = self.page()
        self.assertNotIn('data-slot-fill', html)
        self.assertIn('Todas as aulas do pacote já estão marcadas', html)

    def test_topup_button_appears_when_classes_are_missing(self):
        self.fill()
        self.classes()[0].delete()
        html = self.page()
        self.assertIn('data-slot-topup', html)
        self.assertIn('Marcar mais 1', html)

    def test_without_a_package_the_advanced_form_is_open_and_sell_is_offered(self):
        self.sold.delete()
        html = self.page().replace('\n', ' ')
        self.assertIn('data-slot-no-package', html)
        self.assertIn(reverse('core:student_package_sell', kwargs={'pk': self.person.pk}), html)
        self.assertIn('data-slot-advanced open', html)
        self.assertNotIn('data-slot-fill', html)
