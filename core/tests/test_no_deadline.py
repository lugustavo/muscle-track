from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core import packages, reminders
from core.models import Appointment, Package

from .base import BaseTestCase

TODAY = date(2026, 10, 20)


def klass(person, days_ago):
    day = TODAY - timedelta(days=days_ago)
    return Appointment.objects.create(person=person, status='attended',
                                      starts_at=timezone.make_aware(datetime.combine(day, time(9))))


def form_data(**extra):
    data = {'name': 'Pacote livre', 'description': '', 'sessions': 10, 'valid_days': '60', 'pricing_mode': 'per_session',
            'price_per_session': '50', 'price_total': '', 'is_active': 'on'}
    data.update(extra)
    return data


class PackageFormTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(self.trainer)
        self.url = reverse('core:plan_create')

    def test_page_has_the_checkbox_below_the_deadline(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('name="no_deadline"', html)
        self.assertLess(html.index('name="valid_days"'), html.index('name="no_deadline"'))
        self.assertIn('Sem prazo', html)

    def test_checked_saves_a_package_without_deadline(self):
        response = self.client.post(self.url, form_data(no_deadline='on', valid_days=''))
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(Package.objects.get().valid_days)

    def test_checked_ignores_a_typed_value(self):
        self.client.post(self.url, form_data(no_deadline='on', valid_days='30'))
        self.assertIsNone(Package.objects.get().valid_days)

    def test_unchecked_needs_the_days(self):
        response = self.client.post(self.url, form_data(valid_days=''))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ou marque')
        self.assertFalse(Package.objects.exists())
        self.client.post(self.url, form_data())
        self.assertEqual(Package.objects.get().valid_days, 60)

    def test_edit_shows_the_box_checked_and_can_turn_it_off(self):
        pack = Package.objects.create(name='Livre', sessions=5, valid_days=None, pricing_mode='per_session',
                                      price_per_session=Decimal('50'))
        url = reverse('core:plan_edit', kwargs={'pk': pack.pk})
        self.assertIn('name="no_deadline" class="form-check-input" id="id_no_deadline" checked',
                      self.client.get(url).content.decode().replace('\n', ' '))
        self.client.post(url, form_data(name='Livre', sessions=5, valid_days='45'))
        pack.refresh_from_db()
        self.assertEqual(pack.valid_days, 45)

    def test_list_says_no_deadline(self):
        Package.objects.create(name='Livre', sessions=5, valid_days=None, pricing_mode='per_session', price_per_session=Decimal('50'))
        self.assertContains(self.client.get(reverse('core:plan_list')), 'sem prazo')


class NoDeadlineBehaviourTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.pack = Package.objects.create(name='Livre', sessions=8, valid_days=None, pricing_mode='per_session',
                                           price_per_session=Decimal('50'))
        self.sold = packages.sell(self.person, self.pack)

    def test_sale_copies_the_missing_deadline_and_it_never_ends(self):
        self.assertIsNone(self.sold.valid_days)
        klass(self.person, 400)                       # first class more than a year ago
        packages.sync(self.person, TODAY)
        self.sold.refresh_from_db()
        self.assertIsNone(packages.ends_on(self.sold, TODAY))
        row = packages.overview(self.person, TODAY)['rows'][0]
        self.assertEqual((row['state'], row['ends_on'], row['days_left']), ('active', None, None))
        self.person.refresh_from_db()
        self.assertIsNone(self.person.plan_expires_on)

    def test_never_listed_as_expiring_or_expired_but_still_as_running_low(self):
        klass(self.person, 400)
        packages.sync(self.person, TODAY)
        self.assertEqual(packages.renewals(TODAY), [])
        for n in range(5):
            klass(self.person, 300 - n)
        packages.sync(self.person, TODAY)             # 2 left
        rows = packages.renewals(TODAY)
        self.assertEqual([(r['kind'], r['ends_on']) for r in rows], [('low', None)])
        alert = packages.alerts(TODAY)[0]
        self.assertEqual((alert['low'], alert['expiring'], alert['expired']), (True, False, False))

    def test_extending_is_refused(self):
        klass(self.person, 3)
        packages.sync(self.person, TODAY)
        with self.assertRaises(packages.PackageError):
            packages.extend(self.sold, 10, today=TODAY)

    def test_trainer_card_and_student_pages_say_no_deadline(self):
        klass(self.person, 3)
        packages.sync(self.person)
        self.client.force_login(self.trainer)
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'sem prazo')
        self.assertNotContains(page, 'id="extend-')
        self.client.force_login(self.alice)
        for name in ('core:my_package', 'core:my_agenda'):
            self.assertContains(self.client.get(reverse(name)), 'sem prazo')

    def test_reminder_run_does_not_trip_on_it(self):
        klass(self.person, 400)
        packages.sync(self.person, TODAY)
        now = timezone.make_aware(datetime.combine(TODAY, time(9)))
        self.assertEqual(reminders.run(now)['packages'], 0)
