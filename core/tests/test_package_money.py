import io
import json
import zipfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core import billing, packages
from core.models import Appointment, Package, Payment, Person

from .base import BaseTestCase

PER = '187.35'       # distinctive confidential values, searched for in every student-facing page
TOTAL = '1873.50'    # 10 classes x 187.35


def make_package(name='Pacote 10', sessions=10, days=60, per=PER, **extra):
    return Package.objects.create(name=name, sessions=sessions, valid_days=days, pricing_mode='per_session',
                                  price_per_session=Decimal(per), **extra)


def klass(person, days_ago, status='attended'):
    day = timezone.localdate() - timedelta(days=days_ago)
    return Appointment.objects.create(person=person, starts_at=timezone.make_aware(datetime.combine(day, time(9))), status=status)


class MoneyFormatTests(BaseTestCase):
    @override_settings(CURRENCY_SYMBOL='€', CURRENCY_POSITION='after')
    def test_euro_after_the_number(self):
        self.assertEqual(billing.money(Decimal('1234.5')), '1.234,50 €')
        self.assertEqual(billing.money(None), '')

    @override_settings(CURRENCY_SYMBOL='R$', CURRENCY_POSITION='before')
    def test_real_before_the_number(self):
        self.assertEqual(billing.money(Decimal('55')), 'R$ 55,00')

    def test_default_is_euro(self):
        self.assertEqual(billing.money(Decimal('55')), '55,00 €')


class ConfidentialityTests(BaseTestCase):
    """The agreed prices are for the trainer's eyes only."""

    def setUp(self):
        self.person = self.alice.person
        self.sold = packages.sell(self.person, make_package())

    def assertHidden(self, response):
        text = response.content.decode()
        for value in (PER, TOTAL, PER.replace('.', ','), '1.873,50', '1873,50'):
            self.assertNotIn(value, text)

    def test_student_pages_never_show_the_prices(self):
        self.login(self.alice)
        for name in ('core:home', 'core:my_profile', 'core:my_privacy', 'core:my_workouts', 'core:my_history', 'core:my_agenda'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertHidden(response)

    def test_student_cannot_set_them_through_the_profile_form(self):
        self.login(self.alice)
        self.client.post(reverse('core:my_profile'), {
            'person_name': 'Alice', 'hourly_rate': '1.00', 'plan_price': '1.00', 'plan_name': 'Grátis'})
        person = Person.objects.get(pk=self.person.pk)
        self.assertEqual((person.hourly_rate, person.plan_price), (Decimal(PER), Decimal(TOTAL)))
        self.assertEqual(person.plan_name, 'Pacote 10')

    def test_another_student_cannot_see_them(self):
        self.login(self.bob)
        self.assertEqual(self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).status_code, 403)
        self.assertEqual(self.client.get(reverse('core:payment_create', kwargs={'pk': self.person.pk})).status_code, 403)
        self.assertEqual(self.client.get(reverse('core:student_package_sell', kwargs={'pk': self.person.pk})).status_code, 403)
        self.assertHidden(self.client.get(reverse('core:home')))

    def test_students_export_leaves_prices_out_and_trainers_has_them(self):
        self.login(self.alice)
        response = self.client.post(reverse('core:my_export'))
        raw = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))).read('dados.json').decode()
        self.assertNotIn(PER, raw)
        self.assertNotIn(TOTAL, raw)
        self.assertNotIn('hourly_rate', raw)
        self.assertIn('"packages"', raw)          # the package itself (classes, dates) is the student's data
        self.login(self.trainer)
        response = self.client.post(reverse('core:student_export', kwargs={'pk': self.person.pk}))
        data = json.loads(zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))).read('dados.json'))
        self.assertEqual(data['profile']['hourly_rate'], float(PER))
        self.assertEqual(data['packages'][0]['price_total'], float(TOTAL))

    def test_history_records_the_sale_without_the_values(self):
        event = self.person.events.get(kind='package')
        self.assertNotIn(PER, event.summary + json.dumps(event.changes))
        self.login(self.trainer)
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        history = page.content.decode().split('id="tab-historico"')[1]
        self.assertNotIn('187', history)

    def test_trainer_sees_them_on_the_file_behind_the_eye(self):
        self.login(self.trainer)
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, '187,35')
        self.assertContains(page, '1.873,50')
        self.assertContains(page, 'data-contract-values')


class PaymentTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.sold = packages.sell(self.person, make_package(per='50'))
        self.url = reverse('core:payment_create', kwargs={'pk': self.person.pk})
        self.login(self.trainer)

    def post(self, **extra):
        data = {'student_package': self.sold.pk, 'amount': '500.00', 'paid_on': '2026-05-10', 'method': 'cash', 'notes': ''}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_registering_links_the_payment_to_the_package(self):
        response = self.post()
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': self.person.pk}) + '#tab-pagamentos')
        payment = Payment.objects.get()
        self.assertEqual((payment.student_package, payment.plan_name, payment.amount, payment.recorded_by),
                         (self.sold, 'Pacote 10', Decimal('500.00'), self.trainer))
        self.assertEqual(packages.pending_amount(self.sold), Decimal('0'))
        event = self.person.events.get(kind='payment')
        self.assertEqual(event.actor, self.trainer)
        self.assertNotIn('500', event.summary)

    def test_part_payment_leaves_the_rest_pending(self):
        self.post(amount='200.00')
        self.assertEqual(packages.pending_amount(self.sold), Decimal('300.00'))

    def test_deleting_a_payment_makes_it_pending_again(self):
        self.post()
        self.client.post(reverse('core:payment_delete', kwargs={'pk': Payment.objects.get().pk}))
        self.assertEqual(packages.pending_amount(self.sold), Decimal('500.00'))
        self.assertTrue(self.person.events.filter(kind='payment', summary__contains='excluído').exists())

    def test_validation(self):
        for extra, field in (({'amount': '0'}, 'amount'), ({'amount': '-5'}, 'amount'), ({'amount': ''}, 'amount'),
                             ({'paid_on': ''}, 'paid_on'), ({'method': 'bitcoin'}, 'method')):
            response = self.post(**extra)
            self.assertEqual(response.status_code, 200, extra)
            self.assertIn(field, response.context['form'].errors, extra)
        self.assertFalse(Payment.objects.exists())

    def test_cannot_pay_a_package_of_another_student(self):
        other = packages.sell(self.bob.person, make_package(name='Outro', per='10'))
        response = self.post(student_package=other.pk)
        self.assertEqual(response.status_code, 200)
        self.assertIn('student_package', response.context['form'].errors)

    def test_form_suggests_what_is_still_to_pay(self):
        initial = self.client.get(self.url).context['form'].initial
        self.assertEqual((initial['student_package'], initial['amount']), (self.sold, Decimal('500.00')))
        self.post(amount='200.00')
        self.assertEqual(self.client.get(self.url).context['form'].initial['amount'], Decimal('300.00'))

    def test_only_the_trainer_registers_payments(self):
        self.login(self.alice)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post().status_code, 403)
        self.client.logout()
        self.assertEqual(self.post().status_code, 302)
        self.assertFalse(Payment.objects.exists())

    def test_payments_tab_shows_the_package_name(self):
        self.post()
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'id="tab-pagamentos"')
        self.assertContains(page, '500,00 €')
        self.assertContains(page, 'Registrar pagamento')


class FinancePageTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.today = timezone.localdate()
        self.pack = make_package(per='50')

    def started(self, person, days_ago=3):
        """A package sold to `person` whose first class (days_ago) already happened."""
        sold = packages.sell(person, self.pack)
        klass(person, days_ago)
        packages.sync(person)
        sold.refresh_from_db()
        return sold

    def pay(self, person, amount, when, method='pix', sold=None):
        return Payment.objects.create(person=person, student_package=sold, plan_name='x', amount=Decimal(amount),
                                      paid_on=when, method=method)

    def test_month_summary_by_method_and_sales(self):
        day = date(2026, 3, 15)
        self.pay(self.alice.person, '300.00', day, 'pix')
        self.pay(self.bob.person, '150.50', day, 'cash')
        self.pay(self.bob.person, '99.00', date(2026, 4, 2), 'pix')  # other month
        packages.sell(self.alice.person, self.pack, sold_on=day)
        summary = billing.month_summary(2026, 3)
        self.assertEqual(summary['received'], Decimal('450.50'))
        self.assertEqual([(m['label'], m['total']) for m in summary['by_method']],
                         [('Pix', Decimal('300.00')), ('Dinheiro', Decimal('150.50'))])
        self.assertEqual((summary['sold_count'], summary['sold_total']), (1, Decimal('500.00')))
        page = self.client.get(reverse('core:finance'), {'mes': '2026-03'})
        self.assertContains(page, '450,50 €')
        self.assertContains(page, 'Março de 2026')

    def test_invalid_month_falls_back_to_the_current_one(self):
        for bad in ('2026-13', 'abc', '', '2026', '0-0'):
            response = self.client.get(reverse('core:finance'), {'mes': bad})
            self.assertEqual(response.status_code, 200, bad)
            self.assertEqual(response.context['month_value'], f'{self.today.year}-{self.today.month:02d}')

    def test_pending_only_after_the_first_class_and_only_active_students(self):
        alice = self.started(self.alice.person, 5)
        Person.objects.filter(pk=self.alice.person.pk).update(phone='(11) 98765-4321')
        bob = self.started(self.bob.person, 9)
        Person.objects.filter(pk=self.bob.person.pk).update(is_active=False)
        carol = User.objects.create_user('carol', 'carol@example.com', 'pw')
        packages.sell(Person.objects.create(user=carol, person_name='Carol'), self.pack)   # not started: nothing to charge
        rows = billing.overdue_rows()
        self.assertEqual([(r['student'].person_name, r['days'], r['amount']) for r in rows], [('Alice', 5, Decimal('500.00'))])
        self.assertIsNotNone(bob)
        with override_settings(PIX_KEY='pix@maria.com'):
            page = self.client.get(reverse('core:finance'))
        self.assertContains(page, 'https://wa.me/5511987654321?text=')
        self.assertContains(page, 'pix%40maria.com')
        self.assertContains(page, '500,00 €')
        self.assertContains(page, f'?package={alice.pk}')

    def test_paid_package_is_not_pending(self):
        sold = self.started(self.alice.person)
        self.pay(self.alice.person, '500.00', self.today, sold=sold)
        self.assertEqual(billing.overdue_rows(), [])
        self.assertEqual(packages.pending_count(), 0)

    def test_charge_message(self):
        sold = self.started(self.alice.person)
        with override_settings(PIX_KEY='chave-pix'):
            text = billing.charge_message(self.alice.person, sold, Decimal('300'))
        self.assertIn('Alice', text)
        self.assertIn('pacote Pacote 10', text)
        self.assertIn('300,00 €', text)
        self.assertIn('Chave Pix: chave-pix', text)
        with override_settings(PIX_KEY=''):
            self.assertNotIn('Pix', billing.charge_message(self.alice.person, None, None))

    def test_csv_export_is_excel_friendly_and_safe(self):
        sold = self.started(self.alice.person)
        sold.name = '=HYPERLINK("x")'
        sold.save()
        self.pay(self.alice.person, '1234.50', date(2026, 3, 15), 'pix', sold=sold)
        response = self.client.get(reverse('core:finance_export'), {'mes': '2026-03'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('pagamentos-2026-03.csv', response['Content-Disposition'])
        text = response.content.decode('utf-8')
        self.assertTrue(text.startswith('﻿'))
        lines = text.lstrip('﻿').splitlines()
        self.assertEqual(lines[0], 'Data;Aluno;Pacote;Valor;Forma;Observação')
        self.assertIn('15/03/2026;Alice;', lines[1])
        self.assertIn(';1234,50;Pix;', lines[1])
        self.assertIn("'=HYPERLINK", lines[1])  # formulas are neutralised

    def test_finance_is_for_the_trainer_only(self):
        for name in ('core:finance', 'core:finance_export'):
            self.login(self.alice)
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
            self.client.logout()
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)

    def test_no_query_per_student(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('core:finance'))
            return len(ctx)
        for i in range(3):
            self.make_pending(i)
        few = count()
        for i in range(3, 15):
            self.make_pending(i)
        self.assertEqual(count(), few)

    def make_pending(self, i):
        user = User.objects.create_user(f'ov{i}', f'ov{i}@example.com', 'pw')
        self.started(Person.objects.create(user=user, person_name=f'Pendente {i}'), i + 1)

    def test_dashboard_card_for_pending_payments(self):
        self.assertNotContains(self.client.get(reverse('core:home')), 'Pagamentos pendentes')
        self.started(self.alice.person)
        page = self.client.get(reverse('core:home'))
        self.assertContains(page, 'Pagamentos pendentes')
        self.assertContains(page, 'wa.me')

    def test_renewals_list_on_finance_and_dashboard(self):
        sold = self.started(self.alice.person, 70)                 # first class 70 days ago, 60-day package: expired
        for n in range(2):
            klass(self.bob.person, 3 + n)                          # a second student about to finish a 3-class package
        small = packages.sell(self.bob.person, make_package(name='Pacote 3', sessions=3, per='10'))
        packages.sync(self.bob.person)
        page = self.client.get(reverse('core:finance'))
        self.assertContains(page, 'data-renewals-list')
        self.assertContains(page, 'venceu, 9 aulas sobrando')
        self.assertContains(page, 'restam 1 aula')
        home = self.client.get(reverse('core:home'))
        self.assertContains(home, 'data-renewals')
        self.assertIsNotNone(sold)
        self.assertIsNotNone(small)


class PaymentMethodTests(BaseTestCase):
    def test_mbway_and_spin_are_offered_and_recorded(self):
        from core.models import Payment
        sold = packages.sell(self.alice.person, make_package(per='50'))
        self.login(self.trainer)
        labels = [label for _, label in Payment.Method.choices]
        for label in ('MB Way', 'SPIN'):
            self.assertIn(label, labels)
        page = self.client.get(reverse('core:payment_create', kwargs={'pk': self.alice.person.pk}))
        self.assertContains(page, 'MB Way')
        self.assertContains(page, 'SPIN')
        for method, label in (('mbway', 'MB Way'), ('spin', 'SPIN')):
            self.client.post(reverse('core:payment_create', kwargs={'pk': self.alice.person.pk}), {
                'student_package': sold.pk, 'amount': '100.00', 'paid_on': '2026-05-10', 'method': method, 'notes': ''})
            self.assertEqual(Payment.objects.filter(method=method).first().get_method_display(), label)
        summary = billing.month_summary(2026, 5)
        self.assertEqual(sorted(m['label'] for m in summary['by_method']), ['MB Way', 'SPIN'])
