from datetime import datetime, time, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core import packages
from core.models import Appointment, Package, StudentPackage

from .base import BaseTestCase


def make_package(name='Pacote 10', sessions=10, days=60, per='50', **extra):
    return Package.objects.create(name=name, sessions=sessions, valid_days=days, pricing_mode='per_session',
                                  price_per_session=Decimal(per), **extra)


def klass(person, days_ago, status='attended'):
    day = timezone.localdate() - timedelta(days=days_ago)
    return Appointment.objects.create(person=person, starts_at=timezone.make_aware(datetime.combine(day, time(9))), status=status)


class CatalogTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(self.trainer)

    def test_list_shows_sessions_days_and_prices(self):
        make_package('Pacote 8', 8, 60, '55')
        page = self.client.get(reverse('core:plan_list'))
        for text in ('Pacotes', 'Pacote 8', '8 aulas', '60 dias', '440,00 €', '55,00 € por aula'):
            self.assertContains(page, text)

    def test_create_by_price_per_class(self):
        response = self.client.post(reverse('core:plan_create'), {
            'name': 'Pacote 8', 'sessions': 8, 'valid_days': 60, 'pricing_mode': 'per_session',
            'price_per_session': '55.00', 'price_total': '', 'is_active': 'on'})
        self.assertEqual(response.status_code, 302)
        pack = Package.objects.get()
        self.assertEqual((pack.sessions, pack.valid_days, pack.price_per_session, pack.price_total),
                         (8, 60, Decimal('55.00'), Decimal('440.00')))

    def test_create_by_total(self):
        self.client.post(reverse('core:plan_create'), {
            'name': 'Pacote 10', 'sessions': 10, 'valid_days': 90, 'pricing_mode': 'total',
            'price_per_session': '', 'price_total': '500', 'is_active': 'on'})
        pack = Package.objects.get()
        self.assertEqual((pack.price_per_session, pack.price_total), (Decimal('50.00'), Decimal('500.00')))

    def test_validation(self):
        base = {'name': 'X', 'sessions': 8, 'valid_days': 60, 'pricing_mode': 'per_session', 'price_per_session': '55'}
        for extra, field in (({'sessions': 0}, 'sessions'), ({'sessions': 201}, 'sessions'), ({'valid_days': 0}, 'valid_days'),
                             ({'price_per_session': ''}, 'price_per_session'), ({'pricing_mode': 'total', 'price_total': ''}, 'price_total'),
                             ({'price_per_session': '-1'}, 'price_per_session'), ({'name': ''}, 'name')):
            response = self.client.post(reverse('core:plan_create'), {**base, **extra})
            self.assertEqual(response.status_code, 200, extra)
            self.assertIn(field, response.context['form'].errors, extra)
        self.assertFalse(Package.objects.exists())

    def test_edit_keeps_what_was_already_sold(self):
        pack = make_package()
        sold = packages.sell(self.alice.person, pack)
        self.client.post(reverse('core:plan_edit', kwargs={'pk': pack.pk}), {
            'name': 'Pacote 10', 'sessions': 12, 'valid_days': 90, 'pricing_mode': 'per_session', 'price_per_session': '99', 'is_active': 'on'})
        sold.refresh_from_db()
        self.assertEqual((sold.sessions, sold.valid_days, sold.price_total), (10, 60, Decimal('500.00')))
        page = self.client.get(reverse('core:plan_edit', kwargs={'pk': pack.pk}))
        self.assertContains(page, '1 pacote já vendido')

    def test_trainer_only(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse('core:plan_list')).status_code, 403)

    def test_onboarding_step_is_about_packages(self):
        page = self.client.get(reverse('core:home'))
        self.assertContains(page, 'Crie um pacote')


class SaleTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.pack = make_package('Pacote 8', 8, 60, '55')
        self.url = reverse('core:student_package_sell', kwargs={'pk': self.person.pk})
        self.client.force_login(self.trainer)

    def test_page_offers_the_catalog_and_hides_inactive_ones(self):
        make_package('Antigo', 5, 30, '10', is_active=False)
        page = self.client.get(self.url)
        self.assertContains(page, 'Pacote 8')
        self.assertNotContains(page, 'Antigo')
        self.assertContains(page, 'js/package-sale.js')

    def test_sale_with_the_catalog_price(self):
        response = self.client.post(self.url, {'package': self.pack.pk, 'pricing_mode': 'per_session', 'price_per_session': '55.00', 'price_total': '440.00'})
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': self.person.pk}) + '#tab-ficha', fetch_redirect_response=False)
        sold = StudentPackage.objects.get()
        self.assertEqual((sold.person, sold.sessions, sold.price_per_session, sold.price_total), (self.person, 8, Decimal('55.00'), Decimal('440.00')))

    def test_sale_with_an_agreed_total(self):
        self.client.post(self.url, {'package': self.pack.pk, 'pricing_mode': 'total', 'price_total': '400', 'price_per_session': ''})
        sold = StudentPackage.objects.get()
        self.assertEqual((sold.price_per_session, sold.price_total), (Decimal('50.00'), Decimal('400.00')))

    def test_nothing_typed_uses_the_catalog_price(self):
        self.client.post(self.url, {'package': self.pack.pk, 'pricing_mode': 'per_session'})
        self.assertEqual(StudentPackage.objects.get().price_total, Decimal('440.00'))

    def test_validation(self):
        for data in ({}, {'package': 999}, {'package': self.pack.pk, 'pricing_mode': 'xx'},
                     {'package': self.pack.pk, 'pricing_mode': 'total', 'price_total': '-3'}):
            self.assertEqual(self.client.post(self.url, data).status_code, 200, data)
        self.assertFalse(StudentPackage.objects.exists())

    def test_warns_when_the_student_still_has_classes(self):
        packages.sell(self.person, self.pack)
        page = self.client.get(self.url)
        self.assertContains(page, 'data-has-open')

    def test_trainer_only(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'package': self.pack.pk}).status_code, 403)
        self.assertFalse(StudentPackage.objects.exists())

    def test_student_form_points_to_it(self):
        html = self.client.get(reverse('core:student_edit', kwargs={'pk': self.person.pk})).content.decode()
        for name in ('plan', 'plan_name', 'plan_price', 'hourly_rate', 'plan_expires_on'):
            self.assertNotIn(f'name="{name}"', html)
        self.assertIn(self.url, html)

    def test_new_student_can_be_created_without_a_package(self):
        from .test_student_corrections import new_student_data
        self.assertEqual(self.client.post(reverse('core:student_create'), new_student_data()).status_code, 302)


class CardTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.url = reverse('core:student_detail', kwargs={'pk': self.person.pk})
        self.client.force_login(self.trainer)

    def card(self):
        html = self.client.get(self.url).content.decode()
        return html[html.index('data-package-card'):]

    def test_without_a_package_the_primary_action_is_to_sell_one(self):
        card = self.card()
        self.assertIn('data-package-state="none"', card)
        self.assertIn('Vender pacote', card)

    def test_not_started_shows_the_rule_and_no_payment_yet(self):
        packages.sell(self.person, make_package())
        card = self.card()
        self.assertIn('data-package-state="not_started"', card)
        self.assertIn('começa a contar na primeira aula', card)
        self.assertIn('0</strong> de 10 aulas usadas', card)

    def test_balance_dates_and_pending_payment_after_the_first_class(self):
        packages.sell(self.person, make_package())
        for n in range(3):
            klass(self.person, 5 - n)
        packages.sync(self.person)
        card = self.card()
        self.assertIn('data-package-state="active"', card)
        self.assertIn('3</strong> de 10 aulas usadas', card)
        self.assertIn('7 sobrando', card)
        self.assertIn('Pagamento em aberto: 500,00 €', card)
        self.assertIn('Registrar pagamento', card)

    def test_paid_package_says_so(self):
        sold = packages.sell(self.person, make_package())
        klass(self.person, 2)
        packages.sync(self.person)
        from core.models import Payment
        Payment.objects.create(person=self.person, student_package=sold, amount=Decimal('500'), paid_on=timezone.localdate())
        self.assertIn('Pago (500,00 €)', self.card())

    def test_expired_with_classes_left_asks_to_extend(self):
        packages.sell(self.person, make_package())
        klass(self.person, 70)
        packages.sync(self.person)
        card = self.card()
        self.assertIn('data-package-alert="expired"', card)
        self.assertIn('Prorrogue o pacote', card)
        page = self.client.get(self.url)
        self.assertContains(page, 'Pacote vencido em')

    def test_running_out_suggests_a_new_package(self):
        packages.sell(self.person, make_package(sessions=3))
        for n in range(2):
            klass(self.person, 5 - n)
        packages.sync(self.person)
        self.assertIn('data-package-alert="low"', self.card())

    def test_classes_beyond_the_package_are_flagged(self):
        packages.sell(self.person, make_package(sessions=1))
        klass(self.person, 3)
        klass(self.person, 2)
        packages.sync(self.person)
        self.assertIn('data-without-package', self.card())

    def test_more_scheduled_than_left_is_flagged(self):
        packages.sell(self.person, make_package(sessions=2))
        klass(self.person, 3)
        packages.sync(self.person)
        for n in range(1, 4):
            Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=n))
        self.assertIn('data-over-scheduled', self.card())

    def test_previous_packages_are_listed(self):
        first = packages.sell(self.person, make_package(sessions=1))
        klass(self.person, 9)
        packages.sync(self.person)
        packages.sell(self.person, make_package('Pacote 8', 8, 60, '55'))
        card = self.card()
        self.assertIn('data-package-history', card)
        self.assertIn('Concluído', card)
        self.assertIn('Pacote 8', card)
        self.assertIsNotNone(first)

    def test_prices_sit_behind_the_eye(self):
        packages.sell(self.person, make_package())
        card = self.card()
        self.assertIn('data-contract-values hidden', card)
        self.assertIn('500,00 €', card)


class ActionTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.sold = packages.sell(self.person, make_package())
        klass(self.person, 10)
        packages.sync(self.person)
        self.sold.refresh_from_db()
        self.client.force_login(self.trainer)

    def act(self, action, **extra):
        url = reverse('core:student_package_action', kwargs={'pk': self.person.pk, 'package_pk': self.sold.pk})
        return self.client.post(url, {'action': action, **extra}, follow=True)

    def test_extend(self):
        response = self.act('extend', days='15', note='viagem')
        self.sold.refresh_from_db()
        self.assertEqual(self.sold.extension_days, 15)
        self.assertContains(response, 'Pacote prorrogado em 15 dias')
        self.assertTrue(self.person.events.filter(kind='package', summary__contains='viagem').exists())

    def test_extend_needs_a_sensible_number_of_days(self):
        for days in ('', 'x', '0', '400'):
            response = self.act('extend', days=days)
            self.assertEqual(response.status_code, 200)
        self.sold.refresh_from_db()
        self.assertEqual(self.sold.extension_days, 0)

    def test_freeze_and_unfreeze(self):
        self.act('freeze')
        self.sold.refresh_from_db()
        self.assertIsNotNone(self.sold.frozen_since)
        self.assertContains(self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})), 'Descongelar')
        self.act('unfreeze')
        self.sold.refresh_from_db()
        self.assertIsNone(self.sold.frozen_since)

    def test_cancel(self):
        self.act('cancel')
        self.sold.refresh_from_db()
        self.assertIsNotNone(self.sold.cancelled_at)

    def test_unknown_action_and_methods(self):
        url = reverse('core:student_package_action', kwargs={'pk': self.person.pk, 'package_pk': self.sold.pk})
        self.assertEqual(self.client.post(url, {'action': 'explode'}).status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_package_of_another_student_is_not_reachable(self):
        url = reverse('core:student_package_action', kwargs={'pk': self.bob.person.pk, 'package_pk': self.sold.pk})
        self.assertEqual(self.client.post(url, {'action': 'cancel'}).status_code, 404)

    def test_trainer_only(self):
        self.client.force_login(self.alice)
        url = reverse('core:student_package_action', kwargs={'pk': self.person.pk, 'package_pk': self.sold.pk})
        self.assertNotEqual(self.client.post(url, {'action': 'cancel'}).status_code, 200)
        self.sold.refresh_from_db()
        self.assertIsNone(self.sold.cancelled_at)


class AgendaCardTests(BaseTestCase):
    def test_cancelled_and_missed_classes_show_the_discount_choice(self):
        packages.sell(self.alice.person, make_package())
        late = Appointment.objects.create(person=self.alice.person, starts_at=timezone.now() + timedelta(hours=2),
                                          status='cancelled', cancelled_late=True)
        packages.sync(self.alice.person)
        self.client.force_login(self.trainer)
        day = timezone.localtime(late.starts_at).date().isoformat()
        html = self.client.get(reverse('core:agenda'), {'vista': 'lista', 'dia': day}).content.decode()
        self.assertIn('data-package-count', html)
        self.assertIn('desconta do pacote', html)
        self.assertIn('Não descontar', html)


class StudentSideTests(BaseTestCase):
    def test_student_sees_the_package_without_prices(self):
        packages.sell(self.alice.person, make_package(per='187.35'))
        klass(self.alice.person, 4)
        packages.sync(self.alice.person)
        self.client.force_login(self.alice)
        page = self.client.get(reverse('core:my_agenda'))
        self.assertContains(page, 'data-my-package')
        self.assertContains(page, 'Meu pacote')
        self.assertContains(page, '1</strong> de 10 aulas usadas')
        self.assertContains(page, 'conta como aula dada')
        self.assertNotContains(page, '187')

    def test_no_card_without_a_package(self):
        self.client.force_login(self.alice)
        self.assertNotContains(self.client.get(reverse('core:my_agenda')), 'data-my-package')
