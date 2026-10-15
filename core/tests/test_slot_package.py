from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core import packages
from core.forms import ScheduleSlotForm
from core.models import Appointment, Package, ScheduleSlot

from .base import BaseTestCase

MON = date(2026, 10, 19)   # a Monday (the day the series starts in these tests)


def make_pack(sessions=8, days=60):
    return Package.objects.create(name=f'Pacote {sessions}', sessions=sessions, valid_days=days, pricing_mode='per_session',
                                  price_per_session=Decimal('50'))


class PlanTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person

    def test_none_without_a_package(self):
        self.assertIsNone(packages.schedule_plan(self.person))

    def test_counts_what_is_left_and_already_marked(self):
        packages.sell(self.person, make_pack(8))
        for n in (1, 2):
            Appointment.objects.create(person=self.person, status='scheduled',
                                       starts_at=timezone.now() + timedelta(days=n))
        plan = packages.schedule_plan(self.person)
        self.assertEqual((plan['left'], plan['scheduled'], plan['to_schedule']), (8, 2, 6))
        self.assertIsNone(plan['ends_on'])               # not started: the deadline begins at the first class

    def test_end_date_only_while_running_with_a_deadline(self):
        sold = packages.sell(self.person, make_pack(8, 30))
        Appointment.objects.create(person=self.person, status='attended', starts_at=timezone.now() - timedelta(days=3))
        packages.sync(self.person)
        sold.refresh_from_db()
        plan = packages.schedule_plan(self.person)
        self.assertEqual(plan['ends_on'], packages.ends_on(sold, timezone.localdate()))
        sold.delete()
        free = packages.sell(self.person, make_pack(5, None))
        Appointment.objects.create(person=self.person, status='attended', starts_at=timezone.now() - timedelta(days=3))
        packages.sync(self.person)
        self.assertIsNone(packages.schedule_plan(self.person)['ends_on'])   # no deadline
        self.assertIsNotNone(free)


class SlotFormTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.url = reverse('core:slot_add', kwargs={'pk': self.person.pk})
        self.client.force_login(self.trainer)

    def post(self, **extra):
        data = {'weekdays': [0, 2], 'start_time': '13:00', 'duration_minutes': 60, 'interval_weeks': 1,
                'starts_on': MON.isoformat(), 'ends': 'package', **extra}
        return self.client.post(self.url, data)

    def slots(self):
        return list(ScheduleSlot.objects.filter(person=self.person).order_by('weekday'))

    def test_form_offers_the_package_first_and_selected(self):
        packages.sell(self.person, make_pack(8))
        form = ScheduleSlotForm(plan=packages.schedule_plan(self.person))
        self.assertEqual(form.fields['ends'].choices[0][0], 'package')
        self.assertIn('8 aulas a marcar', form.fields['ends'].choices[0][1])
        self.assertEqual(form.initial['ends'], 'package')
        self.assertEqual([c[0] for c in ScheduleSlotForm().fields['ends'].choices], ['never', 'on', 'after'])

    def test_series_ends_after_the_classes_left(self):
        packages.sell(self.person, make_pack(8))
        self.post()
        slots = self.slots()
        self.assertEqual(len(slots), 2)
        # Mon+Wed from 19/10: 8 classes = 19, 21, 26, 28 Oct, 2, 4, 9, 11 Nov
        self.assertEqual({s.valid_until for s in slots}, {date(2026, 11, 11)})

    def test_already_marked_classes_shorten_the_series(self):
        packages.sell(self.person, make_pack(8))
        for n in (1, 2, 3):
            Appointment.objects.create(person=self.person, status='scheduled',
                                       starts_at=timezone.now() + timedelta(days=n))
        self.post(weekdays=[0])
        self.assertEqual(self.slots()[0].valid_until, MON + timedelta(weeks=4))      # 5 left to mark: Mondays 19/10..16/11

    def test_series_stops_at_the_package_end_date_when_that_comes_first(self):
        sold = packages.sell(self.person, make_pack(20, 30))
        Appointment.objects.create(person=self.person, status='attended', starts_at=timezone.now() - timedelta(days=20))
        packages.sync(self.person)
        sold.refresh_from_db()
        end = packages.ends_on(sold, timezone.localdate())
        self.post(weekdays=[timezone.localdate().weekday()], starts_on=timezone.localdate().isoformat())
        until = self.slots()[0].valid_until
        self.assertEqual(until, end)                      # 20 classes would not fit: the package's last day wins

    def test_nothing_left_to_mark_is_refused(self):
        packages.sell(self.person, make_pack(2))
        for n in (1, 2):
            Appointment.objects.create(person=self.person, status='scheduled',
                                       starts_at=timezone.now() + timedelta(days=n))
        response = self.post()
        self.assertEqual(self.slots(), [])
        self.assertIn('já estão marcadas', ''.join(str(m) for m in response.wsgi_request._messages))

    def test_without_a_package_the_option_does_not_exist(self):
        response = self.post()
        self.assertEqual(self.slots(), [])
        self.assertIn('Horário inválido', ''.join(str(m) for m in response.wsgi_request._messages))
        self.post(ends='never')
        self.assertEqual(len(self.slots()), 2)

    def test_other_endings_still_work_with_a_package(self):
        packages.sell(self.person, make_pack(8))
        self.post(ends='after', ends_after=2, weekdays=[0])
        self.assertEqual(self.slots()[0].valid_until, MON + timedelta(weeks=1))


class AgendaTabTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.client.force_login(self.trainer)
        self.page = lambda: self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))

    def test_without_a_package_links_to_selling_one(self):
        html = self.page().content.decode()
        self.assertIn('data-slot-no-package', html)
        self.assertIn(reverse('core:student_package_sell', kwargs={'pk': self.person.pk}), html)
        self.assertNotIn('data-slot-package', html)

    def test_with_a_package_shows_it_and_selects_following_it(self):
        packages.sell(self.person, make_pack(8))
        html = self.page().content.decode()
        self.assertIn('data-slot-package', html)
        self.assertNotIn('data-slot-no-package', html)
        self.assertIn('Pacote 8', html)
        self.assertIn('Acompanhar o pacote', html)
        box = html[html.index('value="package"'):].split('>')[0]
        self.assertIn('checked', box)
