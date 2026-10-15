from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import packages
from core.models import Appointment, Package, StudentEvent, compute_prices

from .base import BaseTestCase

TODAY = date(2026, 10, 20)


def at(day, hour=9):
    return timezone.make_aware(datetime.combine(day, time(hour)))


def klass(person, day, status='attended', hour=9, **extra):
    return Appointment.objects.create(person=person, starts_at=at(day, hour), status=status, **extra)


class PriceTests(BaseTestCase):
    def test_per_class_gives_the_total(self):
        self.assertEqual(compute_prices('per_session', 8, Decimal('55'), None), (Decimal('55.00'), Decimal('440.00')))

    def test_total_gives_the_per_class_price(self):
        self.assertEqual(compute_prices('total', 10, None, Decimal('500')), (Decimal('50.00'), Decimal('500.00')))
        per, total = compute_prices('total', 3, None, Decimal('100'))
        self.assertEqual((per, total), (Decimal('33.33'), Decimal('100.00')))   # the total stays exactly as typed

    def test_nothing_typed(self):
        self.assertEqual(compute_prices('per_session', 8, None, None), (None, None))
        self.assertEqual(compute_prices('total', 8, None, None), (None, None))

    def test_catalog_package_computes_on_save(self):
        p = Package.objects.create(name='8 aulas', sessions=8, valid_days=60, pricing_mode='per_session', price_per_session=Decimal('55'))
        self.assertEqual(p.price_total, Decimal('440.00'))
        p.sessions = 10
        p.save()
        self.assertEqual(p.price_total, Decimal('550.00'))   # per class kept, total follows
        p.pricing_mode, p.price_total = 'total', Decimal('500')
        p.save()
        self.assertEqual((p.price_per_session, p.price_total), (Decimal('50.00'), Decimal('500.00')))


class SaleTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.pack = Package.objects.create(name='Pacote 10', sessions=10, valid_days=60, pricing_mode='per_session',
                                           price_per_session=Decimal('50'))

    def test_sale_copies_the_numbers_and_logs(self):
        sold = packages.sell(self.person, self.pack, actor=self.trainer)
        self.assertEqual((sold.name, sold.sessions, sold.valid_days, sold.price_per_session, sold.price_total),
                         ('Pacote 10', 10, 60, Decimal('50.00'), Decimal('500.00')))
        self.assertIsNone(sold.starts_on)
        self.assertTrue(StudentEvent.objects.filter(person=self.person, kind='package', summary__contains='Pacote vendido').exists())
        self.pack.price_per_session, self.pack.sessions = Decimal('99'), 20
        self.pack.save()
        sold.refresh_from_db()
        self.assertEqual((sold.sessions, sold.price_total), (10, Decimal('500.00')))   # the sold copy never changes

    def test_agreed_price_replaces_the_catalog_one(self):
        sold = packages.sell(self.person, self.pack, mode='total', price_total=Decimal('450'))
        self.assertEqual((sold.price_per_session, sold.price_total), (Decimal('45.00'), Decimal('450.00')))

    def test_cached_current_package_on_the_student(self):
        packages.sell(self.person, self.pack)
        self.person.refresh_from_db()
        self.assertEqual((self.person.plan, self.person.plan_name, self.person.plan_price, self.person.hourly_rate),
                         (self.pack, 'Pacote 10', Decimal('500.00'), Decimal('50.00')))
        self.assertIsNone(self.person.plan_expires_on)   # the clock starts on the first class


class ConsumptionTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.pack = Package.objects.create(name='Pacote 3', sessions=3, valid_days=30, pricing_mode='per_session',
                                           price_per_session=Decimal('50'))
        self.sold = packages.sell(self.person, self.pack)

    def used(self, sold=None):
        sold = sold or self.sold
        return sold.appointments.count()

    def test_attended_missed_and_late_cancel_count_but_the_rest_do_not(self):
        klass(self.person, TODAY - timedelta(days=9), 'attended')
        klass(self.person, TODAY - timedelta(days=8), 'missed')
        klass(self.person, TODAY - timedelta(days=7), 'cancelled')                       # in time: does not count
        klass(self.person, TODAY - timedelta(days=6), 'cancelled', cancelled_late=True)  # late: counts
        klass(self.person, TODAY - timedelta(days=5), 'rescheduled')
        klass(self.person, TODAY + timedelta(days=5), 'scheduled')
        packages.sync(self.person)
        self.assertEqual(self.used(), 3)
        overview = packages.overview(self.person, TODAY)
        row = overview['rows'][0]
        self.assertEqual((row['state'], row['used'], row['left']), ('done', 3, 0))
        self.assertIsNone(overview['current'])

    def test_override_forces_either_way(self):
        klass(self.person, TODAY - timedelta(days=3), 'missed', counts_override=False)   # an illness: free
        klass(self.person, TODAY - timedelta(days=2), 'cancelled', cancelled_late=True, counts_override=False)
        forced = klass(self.person, TODAY - timedelta(days=1), 'cancelled', counts_override=True)   # trainer decides
        packages.sync(self.person)
        self.assertEqual(self.used(), 1)
        self.assertEqual(list(self.sold.appointments.all()), [forced])

    def test_first_class_starts_the_deadline(self):
        packages.sync(self.person)
        self.sold.refresh_from_db()
        self.assertEqual((self.sold.starts_on, packages.ends_on(self.sold)), (None, None))
        klass(self.person, TODAY - timedelta(days=3), 'attended')
        klass(self.person, TODAY - timedelta(days=1), 'attended')
        packages.sync(self.person, TODAY)
        self.sold.refresh_from_db()
        self.assertEqual(self.sold.starts_on, TODAY - timedelta(days=3))
        self.assertEqual(packages.ends_on(self.sold, TODAY), TODAY - timedelta(days=3) + timedelta(days=30))
        self.person.refresh_from_db()
        self.assertEqual(self.person.plan_expires_on, TODAY - timedelta(days=3) + timedelta(days=30))

    def test_balance_goes_to_the_oldest_package_first_then_the_next(self):
        second = packages.sell(self.person, self.pack)
        for n in range(5):
            klass(self.person, TODAY - timedelta(days=10 - n), 'attended')
        packages.sync(self.person, TODAY)
        self.assertEqual((self.used(self.sold), self.used(second)), (3, 2))
        self.person.refresh_from_db()
        self.assertEqual(self.person.plan_name, 'Pacote 3')   # the open one is the second
        self.assertEqual(packages.overview(self.person, TODAY)['current']['package'], second)

    def test_classes_beyond_every_package_are_flagged(self):
        for n in range(4):
            klass(self.person, TODAY - timedelta(days=10 - n), 'attended')
        packages.sync(self.person, TODAY)
        self.assertEqual(packages.overview(self.person, TODAY)['without_package'], 1)

    def test_changing_a_status_back_gives_the_class_back(self):
        a = klass(self.person, TODAY - timedelta(days=2), 'attended')
        packages.sync(self.person, TODAY)
        self.assertEqual(self.used(), 1)
        a.status = 'scheduled'
        a.save()
        packages.sync(self.person, TODAY)
        self.assertEqual(self.used(), 0)
        self.sold.refresh_from_db()
        self.assertIsNone(self.sold.starts_on)   # the clock goes back to "not started"

    def test_student_without_any_package_keeps_the_old_plan_fields(self):
        bob = self.bob.person
        bob.plan_name, bob.plan_expires_on = 'Plano antigo', TODAY
        bob.save()
        packages.sync(bob, TODAY)
        bob.refresh_from_db()
        self.assertEqual((bob.plan_name, bob.plan_expires_on), ('Plano antigo', TODAY))

    def test_scheduled_classes_are_counted_apart(self):
        klass(self.person, TODAY + timedelta(days=3), 'scheduled')
        klass(self.person, TODAY + timedelta(days=5), 'scheduled')
        with override_settings():
            overview = packages.overview(self.person, TODAY)
        self.assertEqual(overview['current']['used'], 0)


class StateTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        pack = Package.objects.create(name='P', sessions=4, valid_days=30, pricing_mode='per_session', price_per_session=Decimal('50'))
        self.sold = packages.sell(self.person, pack)
        klass(self.person, TODAY - timedelta(days=10), 'attended')
        packages.sync(self.person, TODAY)
        self.sold.refresh_from_db()

    def state(self, today=TODAY):
        return packages.state(self.sold, self.sold.appointments.count(), today)

    def test_active_then_expired_with_classes_left(self):
        self.assertEqual(self.state(), 'active')
        self.assertEqual(self.state(TODAY + timedelta(days=20)), 'active')        # first class + 30 days: the last day still counts
        self.assertEqual(self.state(TODAY + timedelta(days=21)), 'expired')
        row = packages.overview(self.person, TODAY + timedelta(days=30))['current']
        self.assertEqual((row['state'], row['left']), ('expired', 3))             # never zeroed by itself
        self.person.refresh_from_db()
        packages.sync(self.person, TODAY + timedelta(days=30))
        self.person.refresh_from_db()
        self.assertEqual(self.person.plan_expires_on, TODAY - timedelta(days=10) + timedelta(days=30))   # cached as past

    def test_extend_moves_the_end_and_logs_the_reason(self):
        packages.extend(self.sold, 15, 'viagem', actor=self.trainer, today=TODAY)
        self.sold.refresh_from_db()
        self.assertEqual(packages.ends_on(self.sold, TODAY), TODAY - timedelta(days=10) + timedelta(days=45))
        self.assertTrue(StudentEvent.objects.filter(person=self.person, summary__contains='prorrogado').exists())
        self.assertIn('viagem', StudentEvent.objects.filter(kind='package').first().summary)
        for bad in (0, 366):
            with self.assertRaises(packages.PackageError):
                packages.extend(self.sold, bad)

    def test_freeze_stops_the_clock_and_unfreeze_adds_the_days(self):
        packages.freeze(self.sold, self.trainer, today=TODAY)
        self.sold.refresh_from_db()
        self.assertEqual(self.state(TODAY + timedelta(days=60)), 'frozen')        # a frozen package never expires
        self.person.refresh_from_db()
        self.assertIsNone(self.person.plan_expires_on)
        with self.assertRaises(packages.PackageError):
            packages.freeze(self.sold, today=TODAY)
        packages.unfreeze(self.sold, self.trainer, today=TODAY + timedelta(days=12))
        self.sold.refresh_from_db()
        self.assertEqual(self.sold.frozen_days, 12)
        self.assertEqual(packages.ends_on(self.sold, TODAY + timedelta(days=12)), TODAY - timedelta(days=10) + timedelta(days=42))
        with self.assertRaises(packages.PackageError):
            packages.unfreeze(self.sold)

    def test_ongoing_freeze_pushes_the_end_every_day(self):
        packages.freeze(self.sold, today=TODAY)
        self.sold.refresh_from_db()
        self.assertEqual(packages.ends_on(self.sold, TODAY + timedelta(days=5)) - packages.ends_on(self.sold, TODAY), timedelta(days=5))

    def test_cannot_freeze_before_the_first_class(self):
        pack = Package.objects.create(name='Q', sessions=2, valid_days=10, pricing_mode='per_session', price_per_session=Decimal('1'))
        bob_sold = packages.sell(self.bob.person, pack)
        with self.assertRaises(packages.PackageError):
            packages.freeze(bob_sold)

    def test_cancelled_package_frees_the_classes_and_the_cache(self):
        packages.cancel_package(self.sold, self.trainer)
        self.assertEqual(self.sold.appointments.count(), 0)
        self.person.refresh_from_db()
        self.assertEqual((self.person.plan_name, self.person.plan_expires_on), ('', None))
        with self.assertRaises(packages.PackageError):
            packages.cancel_package(self.sold)

    def test_payment_rows(self):
        from core.models import Payment
        self.assertEqual(packages.pending_amount(self.sold), Decimal('200.00'))
        Payment.objects.create(person=self.person, student_package=self.sold, amount=Decimal('200'), paid_on=TODAY)
        self.sold.refresh_from_db()
        self.assertEqual(packages.pending_amount(self.sold), Decimal('0'))
        self.assertEqual(packages.pending_payments(TODAY), [])

    def test_pending_payments_only_after_the_first_class(self):
        pack = Package.objects.create(name='R', sessions=2, valid_days=10, pricing_mode='per_session', price_per_session=Decimal('10'))
        packages.sell(self.bob.person, pack)
        rows = packages.pending_payments(TODAY)
        self.assertEqual([r['student'] for r in rows], [self.person])   # Bob's has not started: nothing to charge yet
        self.assertEqual((rows[0]['amount'], rows[0]['days']), (Decimal('200.00'), 10))


class LateCancelTests(BaseTestCase):
    @override_settings(AGENDA_NOTICE_HOURS=24)
    def test_rule(self):
        now = timezone.now()
        self.assertTrue(packages.late_cancel(now + timedelta(hours=23), now))
        self.assertFalse(packages.late_cancel(now + timedelta(hours=25), now))

    @override_settings(AGENDA_NOTICE_HOURS=0)
    def test_zero_means_only_after_the_start(self):
        now = timezone.now()
        self.assertFalse(packages.late_cancel(now + timedelta(minutes=1), now))
        self.assertTrue(packages.late_cancel(now - timedelta(minutes=1), now))


class StatusViewTests(BaseTestCase):
    """The agenda buttons: cancelling late counts the class; "Não descontar" and a status change undo that."""

    def setUp(self):
        self.person = self.alice.person
        pack = Package.objects.create(name='P', sessions=5, valid_days=60, pricing_mode='per_session', price_per_session=Decimal('50'))
        self.sold = packages.sell(self.person, pack)
        self.client.force_login(self.trainer)

    def post(self, appt, status):
        return self.client.post(reverse('core:appointment_status', kwargs={'pk': appt.pk}), {'status': status})

    def used(self):
        return self.sold.appointments.count()

    def test_late_cancel_counts_and_warns(self):
        appt = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=5))
        response = self.post(appt, 'cancelled')
        appt.refresh_from_db()
        self.assertTrue(appt.cancelled_late)
        self.assertEqual(self.used(), 1)
        follow = self.client.get(response['Location'])
        self.assertContains(follow, 'conta como aula dada')

    def test_early_cancel_is_free(self):
        appt = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=3))
        self.post(appt, 'cancelled')
        appt.refresh_from_db()
        self.assertFalse(appt.cancelled_late)
        self.assertEqual(self.used(), 0)

    def test_attended_and_missed_count_and_reopening_gives_them_back(self):
        a = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=1))
        b = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=2))
        self.post(a, 'attended')
        self.post(b, 'missed')
        self.assertEqual(self.used(), 2)
        self.post(b, 'scheduled')
        self.assertEqual(self.used(), 1)

    def test_do_not_discount_toggle(self):
        appt = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=5))
        self.post(appt, 'cancelled')
        url = reverse('core:appointment_counts', kwargs={'pk': appt.pk})
        self.client.post(url, {'counts': 'no'})
        self.assertEqual(self.used(), 0)
        self.client.post(url, {'counts': 'yes'})
        self.assertEqual(self.used(), 1)
        self.client.post(url, {'counts': 'auto'})
        self.assertEqual(self.used(), 1)   # late cancel: the rule counts it again
        self.assertEqual(self.client.post(url, {'counts': 'maybe'}).status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_changing_the_status_clears_the_override(self):
        appt = Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=1), counts_override=False)
        self.post(appt, 'attended')
        appt.refresh_from_db()
        self.assertIsNone(appt.counts_override)
        self.assertEqual(self.used(), 1)

    def test_deleting_a_one_off_class_gives_it_back(self):
        appt = Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=1), status='attended')
        packages.sync(self.person)
        self.assertEqual(self.used(), 1)
        self.client.post(reverse('core:appointment_delete', kwargs={'pk': appt.pk}))
        self.assertEqual(self.used(), 0)

    def test_student_cancel_request_approved_late_counts(self):
        from core import class_requests
        appt = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=30))
        asked = class_requests.create_request(self.person, appt, 'cancel')
        with override_settings(AGENDA_NOTICE_HOURS=48):
            appt.cancelled_late = False
            class_requests.approve(asked)
        appt.refresh_from_db()
        self.assertTrue(appt.cancelled_late)   # 30 h is inside a 48 h notice
        self.assertEqual(self.used(), 1)

    def test_only_the_trainer(self):
        appt = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(hours=5))
        self.client.force_login(self.alice)
        self.assertNotEqual(self.client.post(reverse('core:appointment_counts', kwargs={'pk': appt.pk}), {'counts': 'no'}).status_code, 200)
