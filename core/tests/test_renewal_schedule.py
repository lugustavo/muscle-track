from datetime import time, timedelta

from django.urls import reverse
from django.utils import timezone

from core import package_schedule, packages
from core.models import Appointment, ScheduleSlot

from .base import BaseTestCase
from .test_package_fill import make_pack

SEVEN = time(7, 0)


def next_monday():
    today = timezone.localdate()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


class RenewalBase(BaseTestCase):
    """A student with a 4-class package already on the agenda (Monday and Thursday, 07:00), renewing with 6 more."""

    def setUp(self):
        self.person = self.alice.person
        self.old = packages.sell(self.person, make_pack(4))
        package_schedule.fill_series(self.person, packages.schedule_plan(self.person), [0, 3], next_monday(), SEVEN)
        self.old_last = max(timezone.localtime(a.starts_at).date() for a in self.person.appointments.all())

    def renew(self, sessions=6):
        return packages.sell(self.person, make_pack(sessions, 90))


class PatternTests(RenewalBase):
    def test_pattern_is_the_latest_series(self):
        pattern = package_schedule.previous_pattern(self.person)
        self.assertEqual((pattern['weekdays'], pattern['start_time'], pattern['duration'], pattern['interval']),
                         ([0, 3], SEVEN, 60, 1))
        self.assertEqual(pattern['label'], 'seg e qui às 07:00')

    def test_no_pattern_without_fixed_times(self):
        self.assertIsNone(package_schedule.previous_pattern(self.bob.person))

    def test_different_times_give_no_pattern(self):
        ScheduleSlot.objects.filter(person=self.person, weekday=3).update(start_time=time(9, 0))
        self.assertIsNone(package_schedule.previous_pattern(self.person))

    def test_continues_the_day_after_the_last_marked_class(self):
        self.assertEqual(package_schedule.continue_from(self.person), self.old_last + timedelta(days=1))

    def test_nothing_marked_continues_today(self):
        self.assertEqual(package_schedule.continue_from(self.bob.person), timezone.localdate())


class RenewalPlanTests(RenewalBase):
    def test_before_renewing_everything_is_marked(self):
        self.assertEqual(packages.schedule_plan(self.person)['to_schedule'], 0)

    def test_after_renewing_the_new_package_is_still_to_schedule(self):
        new = self.renew()
        plan = packages.schedule_plan(self.person)
        self.assertEqual((plan['left'], plan['scheduled'], plan['to_schedule']), (10, 4, 6))
        self.assertEqual(plan['package'], new)
        self.assertIn('+', plan['name'])

    def test_single_package_plan_is_unchanged(self):
        plan = packages.schedule_plan(self.bob.person)
        self.assertIsNone(plan)


class ContinueFillTests(RenewalBase):
    def fill_new(self, start=None):
        plan = packages.schedule_plan(self.person)
        pattern = package_schedule.previous_pattern(self.person)
        return package_schedule.fill_series(
            self.person, plan, pattern['weekdays'], start or package_schedule.continue_from(self.person),
            pattern['start_time'], pattern['duration'], pattern['interval'])

    def test_continuing_marks_the_new_packages_classes_after_the_old_ones(self):
        new = self.renew()
        result = self.fill_new()
        self.assertEqual(result['created'], 6)
        starts = sorted(timezone.localtime(a.starts_at).date() for a in self.person.appointments.all())
        self.assertEqual(len(starts), 10)
        self.assertTrue(all(d > self.old_last for d in starts[4:]))
        self.assertEqual(starts[:4], sorted(starts[:4]))
        self.assertEqual(packages.schedule_plan(self.person)['to_schedule'], 0)
        # the weekly times now belong to the new package
        self.assertEqual({s.package_id for s in self.person.slots.filter(is_active=True)}, {new.pk})

    def test_old_classes_stay(self):
        self.renew()
        before = set(self.person.appointments.values_list('pk', flat=True))
        self.fill_new()
        self.assertTrue(before <= set(self.person.appointments.values_list('pk', flat=True)))

    def test_starting_too_early_skips_days_that_already_have_a_class(self):
        self.renew()
        result = self.fill_new(start=next_monday())      # the old classes occupy those days: they are not repeated
        self.assertEqual(result['created'], 6)
        self.assertTrue(all(timezone.localtime(a.starts_at).date() > self.old_last
                            for a in self.person.appointments.order_by('starts_at')[4:]))

    def test_agenda_generation_does_not_duplicate_classes(self):
        from core.agenda import ensure_appointments
        self.renew()
        self.fill_new()
        count = self.person.appointments.count()
        today = timezone.localdate()
        ensure_appointments(today, today + timedelta(weeks=8), today)
        self.assertEqual(self.person.appointments.count(), count)


class RenewalScreensTests(RenewalBase):
    def setUp(self):
        super().setUp()
        self.login(self.trainer)
        self.sell_url = reverse('core:student_package_sell', kwargs={'pk': self.person.pk})
        self.detail = reverse('core:student_detail', kwargs={'pk': self.person.pk})

    def post_sale(self, package, **extra):
        data = {'package': package.pk, 'pricing_mode': 'per_session', 'price_per_session': '50', **extra}
        return self.client.post(self.sell_url, data)

    def test_sale_page_offers_the_same_schedule(self):
        html = self.client.get(self.sell_url).content.decode()
        self.assertIn('data-continue-schedule', html)
        self.assertIn('seg e qui às 07:00', html)

    def test_sale_page_has_no_offer_without_a_previous_schedule(self):
        url = reverse('core:student_package_sell', kwargs={'pk': self.bob.person.pk})
        self.assertNotIn('data-continue-schedule', self.client.get(url).content.decode())

    def test_checked_goes_to_the_agenda_tab(self):
        pack = make_pack(6, 90)
        response = self.post_sale(pack, continue_schedule='on')
        self.assertRedirects(response, self.detail + '#tab-agenda', fetch_redirect_response=False)

    def test_unchecked_goes_to_the_file_as_before(self):
        response = self.post_sale(make_pack(6, 90))
        self.assertRedirects(response, self.detail + '#tab-ficha', fetch_redirect_response=False)

    def test_checked_without_a_previous_schedule_is_ignored(self):
        bob = self.bob.person
        response = self.client.post(reverse('core:student_package_sell', kwargs={'pk': bob.pk}),
                                    {'package': make_pack(6, 90).pk, 'pricing_mode': 'per_session',
                                     'price_per_session': '50', 'continue_schedule': 'on'})
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': bob.pk}) + '#tab-ficha',
                             fetch_redirect_response=False)

    def test_the_agenda_form_comes_prefilled_after_renewing(self):
        self.renew()
        response = self.client.get(self.detail)
        form = response.context['fill_form']
        self.assertEqual(form.initial['weekdays'], [0, 3])
        self.assertEqual(form.initial['start_time'], SEVEN)
        self.assertEqual(form.initial['starts_on'], self.old_last + timedelta(days=1))
        html = response.content.decode()
        self.assertIn('data-fill-continue', html)
        self.assertIn('seg e qui às 07:00', html)

    def test_no_prefill_when_everything_is_already_marked(self):
        response = self.client.get(self.detail)
        self.assertIsNone(response.context['fill_pattern'])

    def test_preview_follows_the_renewal_plan(self):
        self.renew()
        url = reverse('core:slot_preview', kwargs={'pk': self.person.pk})
        html = self.client.get(url, {'weekdays': [0, 3], 'start_time': '07:00',
                                     'starts_on': (self.old_last + timedelta(days=1)).isoformat()}).content.decode()
        self.assertIn('data-count="6"', html)

    def test_confirming_fills_the_agenda_through_the_view(self):
        self.renew()
        url = reverse('core:slot_fill', kwargs={'pk': self.person.pk})
        self.client.post(url, {'weekdays': [0, 3], 'start_time': '07:00', 'duration_minutes': 60, 'interval_weeks': 1,
                               'starts_on': (self.old_last + timedelta(days=1)).isoformat()})
        self.assertEqual(Appointment.objects.filter(person=self.person).count(), 10)
