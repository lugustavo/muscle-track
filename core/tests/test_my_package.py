from datetime import datetime, time, timedelta
from decimal import Decimal

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core import packages
from core.models import Appointment, Package

from .base import BaseTestCase, page_text


def klass(person, days_ago, status='attended', **extra):
    day = timezone.localdate() - timedelta(days=days_ago)
    return Appointment.objects.create(person=person, starts_at=timezone.make_aware(datetime.combine(day, time(9))),
                                      status=status, **extra)


class SummaryTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.pack = Package.objects.create(name='Pacote 8', sessions=8, valid_days=60, pricing_mode='per_session',
                                           price_per_session=Decimal('55'))
        self.sold = packages.sell(self.person, self.pack)

    def row(self):
        return packages.summary(self.person)['rows'][0]

    def test_counts_every_kind_of_class(self):
        klass(self.person, 30)
        klass(self.person, 28)
        klass(self.person, 26, 'missed')
        klass(self.person, 24, 'cancelled', cancelled_late=True)
        klass(self.person, 22, 'cancelled')                         # in time: free
        klass(self.person, 20, 'missed', counts_override=False)    # an illness: free
        klass(self.person, 18, 'rescheduled')
        klass(self.person, -3, 'scheduled')
        packages.sync(self.person)
        row = self.row()
        self.assertEqual((row['sessions'], row['done'], row['missed'], row['late_cancelled']), (8, 2, 1, 1))
        self.assertEqual((row['used'], row['left']), (4, 4))
        self.assertEqual((row['free_cancelled'], row['rescheduled'], row['upcoming']), (2, 1, 1))
        self.assertEqual(len(row['classes']), 4)

    def test_nothing_before_the_first_class(self):
        row = self.row()
        self.assertEqual((row['done'], row['free_cancelled'], row['rescheduled'], row['left']), (0, 0, 0, 8))

    def test_each_package_counts_only_its_own_period(self):
        small = Package.objects.create(name='Pacote 2', sessions=2, valid_days=30, pricing_mode='per_session',
                                       price_per_session=Decimal('50'))
        self.sold.delete()
        first = packages.sell(self.person, small)
        klass(self.person, 40)
        klass(self.person, 38)
        klass(self.person, 36, 'cancelled')   # free, during the first package
        packages.sync(self.person)
        second = packages.sell(self.person, self.pack)
        klass(self.person, 10)
        klass(self.person, 8, 'rescheduled')
        packages.sync(self.person)
        by_pk = {r['package'].pk: r for r in packages.summary(self.person)['rows']}
        self.assertEqual((by_pk[first.pk]['done'], by_pk[first.pk]['free_cancelled'], by_pk[first.pk]['rescheduled']), (2, 1, 0))
        self.assertEqual((by_pk[second.pk]['done'], by_pk[second.pk]['rescheduled']), (1, 1))


class PageTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.pack = Package.objects.create(name='Pacote 8', sessions=8, valid_days=60, pricing_mode='per_session',
                                           price_per_session=Decimal('187.35'))
        self.url = reverse('core:my_package')

    def test_requires_a_student(self):
        self.assertEqual(Client().get(self.url).status_code, 302)
        self.client.force_login(self.trainer)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)   # sent away from the student area

    def test_empty_state(self):
        self.client.force_login(self.alice)
        self.assertContains(self.client.get(self.url), 'ainda não tem um pacote')

    def test_shows_the_summary_and_no_prices(self):
        packages.sell(self.person, self.pack)
        klass(self.person, 5)
        klass(self.person, 3, 'cancelled', cancelled_late=True)
        packages.sync(self.person)
        self.client.force_login(self.alice)
        page = self.client.get(self.url)
        self.assertContains(page, 'Pacote 8')
        for key, value in (('bought', 8), ('done', 1), ('left', 6), ('cancelled', 1)):
            self.assertContains(page, f'data-sum="{key}">{value}<')
        self.assertContains(page, 'cancelamento em cima da hora')
        for text in ('187', '1.498', '1498'):
            self.assertNotIn(text, page_text(page))

    def test_only_my_own_package(self):
        packages.sell(self.bob.person, self.pack)
        self.client.force_login(self.alice)
        self.assertNotContains(self.client.get(self.url), 'data-package-summary')

    def test_old_packages_are_listed_below(self):
        old = packages.sell(self.person, self.pack)
        for n in range(8):
            klass(self.person, 40 - n)
        packages.sync(self.person)
        packages.sell(self.person, self.pack)
        self.client.force_login(self.alice)
        page = self.client.get(self.url)
        self.assertContains(page, 'Pacotes anteriores')
        self.assertEqual(page.content.decode().count('data-package-summary'), 2)
        self.assertContains(page, f'data-package-summary="{old.pk}"')

    def test_menu_links_for_the_student_only(self):
        self.client.force_login(self.alice)
        self.assertContains(self.client.get(reverse('core:my_workouts')), self.url)
        self.assertContains(self.client.get(reverse('core:my_profile')), self.url)
        self.client.force_login(self.trainer)
        self.assertNotContains(self.client.get(reverse('core:home')), self.url)
