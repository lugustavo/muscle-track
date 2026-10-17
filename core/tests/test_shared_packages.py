from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import packages, reminders
from core.models import Appointment, NotificationPreference, Package, Person, StudentEvent

from .base import BaseTestCase

TODAY = timezone.localdate()


def klass(person, days_ago, status='attended', hour=9, **extra):
    day = TODAY - timedelta(days=days_ago)
    return Appointment.objects.create(person=person, starts_at=timezone.make_aware(datetime.combine(day, time(hour))),
                                      status=status, **extra)


class SharedBase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.alice.person
        self.second = self.bob.person
        _, _ = self._make_student('carol', 'Carol Souza')
        self.third = Person.objects.get(person_name='Carol Souza')
        self.pack = Package.objects.create(name='Pacote 4', sessions=4, valid_days=60, pricing_mode='per_session',
                                           price_per_session=Decimal('50'))

    def sell(self, *members, **extra):
        return packages.sell(self.owner, self.pack, members=members, actor=self.trainer, **extra)

    def used(self, person, package):
        return Appointment.objects.filter(person=person, package=package).count()


class SharedBalanceTests(SharedBase):
    def test_one_balance_for_everyone_and_the_earliest_class_goes_first(self):
        sold = self.sell(self.second)
        klass(self.second, 9)
        klass(self.owner, 8)
        klass(self.second, 7)
        klass(self.owner, 6)
        klass(self.second, 5)      # the fifth class finds the pool empty
        packages.sync(self.owner, TODAY)
        self.assertEqual((self.used(self.owner, sold), self.used(self.second, sold)), (2, 2))
        row = packages.overview(self.owner, TODAY)['rows'][0]
        self.assertEqual((row['used'], row['left'], row['state']), (4, 0, 'done'))
        self.assertEqual(packages.overview(self.second, TODAY)['without_package'], 1)
        self.assertEqual(sold.appointments.count(), 4)

    def test_whoever_comes_first_uses_it(self):
        sold = self.sell(self.second)
        for days in (9, 8, 7, 6):
            klass(self.second, days)
        klass(self.owner, 5)
        packages.sync(self.second, TODAY)
        self.assertEqual((self.used(self.second, sold), self.used(self.owner, sold)), (4, 0))
        self.assertEqual(packages.overview(self.owner, TODAY)['without_package'], 1)

    def test_each_class_takes_one_whoever_the_student_is(self):
        sold = self.sell(self.second, self.third)
        klass(self.owner, 9)
        klass(self.second, 8)
        klass(self.third, 7, 'missed')
        packages.sync(self.third, TODAY)
        self.assertEqual(sold.appointments.count(), 3)
        row = packages.overview(self.third, TODAY)['rows'][0]
        self.assertEqual([(p['name'], p['used']) for p in row['people']], [('Alice', 1), ('Bob', 1), ('Carol', 1)])

    def test_syncing_any_member_redoes_the_whole_group(self):
        sold = self.sell(self.second)
        klass(self.owner, 9)
        klass(self.second, 8)
        packages.sync(self.second, TODAY)
        self.assertEqual(sold.appointments.count(), 2)
        self.assertEqual(sold.appointments.filter(person=self.owner).count(), 1)

    def test_a_member_uses_their_own_older_package_first(self):
        own = packages.sell(self.second, self.pack, sold_on=TODAY - timedelta(days=30))
        shared = self.sell(self.second)
        for days in (9, 8, 7, 6, 5):
            klass(self.second, days)
        packages.sync(self.second, TODAY)
        self.assertEqual((own.appointments.count(), shared.appointments.count()), (4, 1))

    def test_first_class_starts_the_deadline_for_everybody(self):
        sold = self.sell(self.second)
        klass(self.second, 3)
        packages.sync(self.owner, TODAY)
        sold.refresh_from_db()
        self.assertEqual(sold.starts_on, TODAY - timedelta(days=3))
        self.assertEqual(packages.overview(self.owner, TODAY)['rows'][0]['ends_on'], TODAY - timedelta(days=3) + timedelta(days=60))

    def test_cached_plan_fields_follow_each_person_and_hide_the_price_from_members(self):
        self.sell(self.second)
        klass(self.second, 3)
        packages.sync(self.owner, TODAY)
        self.owner.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.owner.plan_name, self.owner.plan_price, self.owner.hourly_rate),
                         ('Pacote 4', Decimal('200.00'), Decimal('50.00')))
        self.assertEqual(self.second.plan_name, 'Pacote 4')
        self.assertEqual(self.second.plan_expires_on, self.owner.plan_expires_on)
        self.assertIsNone(self.second.plan_price)
        self.assertIsNone(self.second.hourly_rate)

    def test_scheduled_classes_of_the_others_count_for_the_plan(self):
        self.sell(self.second)
        klass(self.second, -3, 'scheduled')
        klass(self.second, -4, 'scheduled')
        plan = packages.schedule_plan(self.owner, TODAY)
        self.assertEqual((plan['left'], plan['scheduled'], plan['to_schedule']), (4, 2, 2))


class SharedRulesTests(SharedBase):
    def test_at_most_two_more_people(self):
        self._make_student('dora', 'Dora')
        dora = Person.objects.get(person_name='Dora')
        with self.assertRaises(packages.PackageError):
            self.sell(self.second, self.third, dora)

    def test_the_owner_cannot_be_a_member_and_inactive_students_cannot_join(self):
        with self.assertRaises(packages.PackageError):
            self.sell(self.owner)
        self.second.is_active = False
        self.second.save()
        with self.assertRaises(packages.PackageError):
            self.sell(self.second)
        self.assertFalse(Person.objects.get(pk=self.owner.pk).packages.exists())

    def test_sale_is_logged_for_everybody(self):
        self.sell(self.second)
        self.assertTrue(StudentEvent.objects.filter(person=self.second, kind='package',
                                                    summary__contains='Pacote dividido com você').exists())
        self.assertTrue(StudentEvent.objects.filter(person=self.owner, summary__contains='Pacote vendido').exists())

    def test_change_the_people_later(self):
        sold = self.sell(self.second)
        packages.set_members(sold, [self.third], self.trainer)
        self.assertEqual(list(sold.shared_with.all()), [self.third])
        self.second.refresh_from_db()
        self.assertEqual(self.second.plan_name, '')        # no longer on the package: cached fields cleared
        self.third.refresh_from_db()
        self.assertEqual(self.third.plan_name, 'Pacote 4')

    def test_someone_who_took_classes_cannot_be_removed(self):
        sold = self.sell(self.second)
        klass(self.second, 3)
        packages.sync(self.owner, TODAY)
        with self.assertRaises(packages.PackageError):
            packages.set_members(sold, [], self.trainer)
        self.assertEqual(sold.shared_with.count(), 1)
        packages.set_members(sold, [self.second, self.third], self.trainer)   # adding is fine
        self.assertEqual(sold.shared_with.count(), 2)

    def test_cancelling_releases_everybodys_classes(self):
        sold = self.sell(self.second)
        klass(self.owner, 4)
        klass(self.second, 3)
        packages.sync(self.owner, TODAY)
        packages.cancel_package(sold, self.trainer)
        self.assertEqual(Appointment.objects.filter(package__isnull=False).count(), 0)
        self.second.refresh_from_db()
        self.assertEqual(self.second.plan_name, '')

    def test_money_is_only_the_owners(self):
        self.sell(self.second)
        klass(self.second, 3)
        packages.sync(self.owner, TODAY)
        mine = packages.overview(self.owner, TODAY)['rows'][0]
        theirs = packages.overview(self.second, TODAY)['rows'][0]
        self.assertEqual((mine['is_owner'], mine['pending']), (True, Decimal('200.00')))
        self.assertEqual((theirs['is_owner'], theirs['pending'], theirs['paid'], theirs['overpaid']),
                         (False, Decimal('0'), Decimal('0'), Decimal('0')))
        self.assertEqual([r['student'] for r in packages.pending_payments(TODAY)], [self.owner])
        self.assertEqual(packages.pending_count(), 1)


class SharedAlertsTests(SharedBase):
    def test_one_row_per_package_with_everybody_on_it(self):
        self.sell(self.second)
        for days in (9, 8, 7):
            klass(self.owner, days)
        packages.sync(self.owner, TODAY)
        rows = packages.renewals(TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['student'], rows[0]['people'], rows[0]['kind'], rows[0]['left']),
                         (self.owner, [self.owner, self.second], 'low', 1))
        self.assertEqual(len(packages.alerts(TODAY)), 1)

    def test_a_member_with_an_older_package_is_not_listed_twice(self):
        packages.sell(self.second, self.pack, sold_on=TODAY - timedelta(days=30))
        self.sell(self.second)
        for days in (9, 8, 7):
            klass(self.second, days)
        packages.sync(self.second, TODAY)
        current = [(p.person.person_name, [q.person_name for q in people])
                   for p, _s, _left, _end, people in packages._current_packages(TODAY)]
        self.assertEqual(current, [('Bob', ['Bob']), ('Alice', ['Alice'])])   # Bob is on his own package, not the shared one
        names = [(r['package'].person.person_name, [p.person_name for p in r['people']]) for r in packages.renewals(TODAY)]
        self.assertEqual(names, [('Bob', ['Bob'])])   # the shared one is "current" only for Alice, who has 4 left

    def test_inactive_owner_still_lists_the_active_member(self):
        self.sell(self.second)
        for days in (9, 8, 7):
            klass(self.second, days)
        packages.sync(self.owner, TODAY)
        self.owner.is_active = False
        self.owner.save()
        rows = packages.renewals(TODAY)
        self.assertEqual([(r['student'], r['people']) for r in rows], [(self.second, [self.second])])


@override_settings(NOTIFICATIONS_ASYNC=False, SITE_URL='https://treino.example.com')
class SharedReminderTests(SharedBase):
    def test_everyone_using_the_package_hears_it_is_running_low_and_the_trainer_gets_one_line(self):
        for user in (self.alice, self.bob, self.trainer):
            NotificationPreference.objects.update_or_create(user=user, defaults={'email_enabled': True})
        self.sell(self.second)
        for days in (9, 8, 7):
            klass(self.owner, days)
        packages.sync(self.owner, TODAY)
        sent = reminders.send_package_reminders(timezone.make_aware(datetime.combine(TODAY, time(9))), TODAY)
        self.assertEqual(sent, 1)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox),
                         ['alice@example.com', 'bob@example.com', 'trainer@example.com'])
        trainer = next(m for m in mail.outbox if m.to == ['trainer@example.com'])
        self.assertIn('Alice + Bob: 1 aula restante', trainer.body)
        self.assertEqual(reminders.send_package_reminders(timezone.now(), TODAY), 0)


class SharedScreensTests(SharedBase):
    def setUp(self):
        super().setUp()
        self.sold = self.sell(self.second)
        klass(self.owner, 4)
        klass(self.second, 3)
        klass(self.second, 2)
        packages.sync(self.owner, TODAY)

    def test_sale_form_shares_the_package(self):
        self.login(self.trainer)
        other = Package.objects.create(name='Pacote 8', sessions=8, valid_days=60, pricing_mode='per_session',
                                       price_per_session=Decimal('40'))
        response = self.client.post(reverse('core:student_package_sell', args=[self.third.pk]), {
            'package': other.pk, 'pricing_mode': 'per_session', 'price_per_session': '', 'price_total': '',
            'member_1': self.owner.pk, 'member_2': self.second.pk})
        self.assertEqual(response.status_code, 302)
        sold = self.third.packages.get()
        self.assertEqual(set(sold.shared_with.all()), {self.owner, self.second})

    def test_sale_form_refuses_the_same_person_twice(self):
        self.login(self.trainer)
        response = self.client.post(reverse('core:student_package_sell', args=[self.third.pk]), {
            'package': self.pack.pk, 'pricing_mode': 'per_session', 'member_1': self.owner.pk, 'member_2': self.owner.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Escolha duas pessoas diferentes.')
        self.assertFalse(self.third.packages.exists())

    def test_owner_file_shows_the_people_and_the_money(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:student_detail', args=[self.owner.pk])).content.decode()
        self.assertIn('data-package-people', html)
        self.assertIn('Bob · 2 usadas', html)
        self.assertIn('data-package-payment', html)
        self.assertIn('data-package-cancel', html)

    def test_member_file_is_read_only_and_has_no_money(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:student_detail', args=[self.second.pk])).content.decode()
        self.assertIn('data-package-people', html)
        self.assertIn('Alice · 1 usada', html)
        self.assertIn('data-package-not-owner', html)
        for hidden in ('data-package-payment', 'data-package-cancel', 'data-package-edit', 'data-package-money'):
            self.assertNotIn(hidden, html)
        self.assertNotIn('200,00', html)
        self.assertNotIn('50,00', html)

    def test_only_the_owner_can_act_on_the_package(self):
        self.login(self.trainer)
        for name in ('student_package_edit', 'student_package_cancel'):
            response = self.client.get(reverse(f'core:{name}', args=[self.second.pk, self.sold.pk]))
            self.assertEqual(response.status_code, 404, name)
        response = self.client.post(reverse('core:student_package_action', args=[self.second.pk, self.sold.pk]),
                                    {'action': 'cancel'})
        self.assertEqual(response.status_code, 404)
        self.sold.refresh_from_db()
        self.assertIsNone(self.sold.cancelled_at)

    def test_edit_form_changes_the_people_and_refuses_removing_who_already_came(self):
        self.login(self.trainer)
        url = reverse('core:student_package_edit', args=[self.owner.pk, self.sold.pk])
        base = {'name': 'Pacote 4', 'sessions': 4, 'valid_days': 60, 'pricing_mode': 'per_session',
                'price_per_session': '50', 'price_total': '', 'notes': ''}
        response = self.client.post(url, {**base, 'member_1': '', 'member_2': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'já usou aulas deste pacote')
        self.assertEqual(self.sold.shared_with.count(), 1)
        response = self.client.post(url, {**base, 'member_1': self.second.pk, 'member_2': self.third.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.sold.shared_with.count(), 2)

    def test_edit_keeps_working_on_a_finished_shared_package(self):
        klass(self.owner, 1)
        klass(self.owner, 0)
        packages.sync(self.owner, TODAY)
        self.login(self.trainer)
        response = self.client.post(reverse('core:student_package_edit', args=[self.owner.pk, self.sold.pk]), {
            'name': 'Pacote 4+', 'sessions': 5, 'valid_days': 60, 'pricing_mode': 'per_session',
            'price_per_session': '50', 'price_total': '', 'member_1': self.second.pk, 'member_2': ''})
        self.assertEqual(response.status_code, 302)
        self.sold.refresh_from_db()
        self.assertEqual(self.sold.sessions, 5)

    def test_student_sees_first_names_and_totals_but_no_money(self):
        self.login(self.bob)
        html = self.client.get(reverse('core:my_package')).content.decode()
        self.assertIn('data-package-people', html)
        self.assertIn('Você usou 2.', html)
        self.assertIn('Alice · 1 usada', html)
        self.assertNotIn('Souza', html)
        self.assertNotIn('Carol', html)
        for money in ('200,00', '50,00', 'Pago ('):
            self.assertNotIn(money, html)

    def test_renewals_card_shows_who_shares(self):
        self.login(self.trainer)   # 3 of 4 used: running low
        html = self.client.get(reverse('core:finance')).content.decode()
        self.assertIn('data-shared-with', html)
