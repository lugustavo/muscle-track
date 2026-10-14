import io
import json
import zipfile
from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core import billing
from core.agenda import (
    BACKFILL_DAYS, at, attendance_stats, conflicting_ids, ensure_appointments, parse_week, week_start,
)
from core.forms import PersonForm
from core.models import Appointment, Payment, Person, Plan, ScheduleSlot

from .base import BaseTestCase

RATE = '187.35'    # distinctive confidential values, searched for in every student-facing page
PRICE = '91.77'


def make_plan(name='Mensal 3x', months=1, price='300.00', spw=3, **extra):
    return Plan.objects.create(name=name, months=months, price=Decimal(price), sessions_per_week=spw, **extra)


class BillingRulesTests(BaseTestCase):
    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(billing.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(billing.add_months(date(2028, 1, 31), 1), date(2028, 2, 29))   # leap year
        self.assertEqual(billing.add_months(date(2026, 11, 15), 3), date(2027, 2, 15))  # crosses the year
        self.assertEqual(billing.add_months(date(2026, 3, 10), 12), date(2027, 3, 10))

    def test_period_end_is_the_day_before_the_next_period(self):
        self.assertEqual(billing.period_end_for(date(2026, 1, 10), 1), date(2026, 2, 9))
        self.assertEqual(billing.period_end_for(date(2026, 1, 1), 12), date(2026, 12, 31))

    def test_suggest_period(self):
        person, plan = self.alice.person, make_plan(months=3)
        today = date(2026, 5, 10)
        person.plan_expires_on = None
        self.assertEqual(billing.suggest_period(person, plan, today), (today, date(2026, 8, 9)))
        person.plan_expires_on = date(2026, 5, 20)  # still valid: renewal in advance
        self.assertEqual(billing.suggest_period(person, plan, today), (date(2026, 5, 21), date(2026, 8, 20)))
        person.plan_expires_on = date(2026, 5, 10)  # expires today: still counts as valid
        self.assertEqual(billing.suggest_period(person, plan, today)[0], date(2026, 5, 11))
        person.plan_expires_on = date(2026, 4, 1)   # lapsed: starts on the payment day
        self.assertEqual(billing.suggest_period(person, plan, today)[0], today)
        self.assertEqual(billing.suggest_period(person, None, today)[1], date(2026, 6, 9))  # no plan: 1 month

    def test_suggest_amount_priority(self):
        person, plan = self.alice.person, make_plan(price='300.00', spw=3, months=1)
        person.plan = plan
        self.assertEqual(billing.suggest_amount(person), Decimal('300.00'))                  # table price
        person.hourly_rate = Decimal('50.00')
        self.assertEqual(billing.suggest_amount(person), Decimal('650.00'))                  # 50 x 3 x 52/12 weeks
        person.plan_price = Decimal('280.00')
        self.assertEqual(billing.suggest_amount(person), Decimal('280.00'))                  # negotiated wins
        other = make_plan(name='Outro', price='100.00', spw=None)
        self.assertEqual(billing.suggest_amount(person, other), Decimal('100.00'))          # negotiated is for the student's plan only
        person.plan, person.plan_price, person.hourly_rate = None, None, None
        self.assertIsNone(billing.suggest_amount(person))

    def test_hourly_amount_scales_with_period(self):
        person = self.alice.person
        person.hourly_rate = Decimal('100.00')
        quarterly = make_plan(name='Tri', months=3, spw=2, price='0')
        self.assertEqual(billing.suggest_amount(person, quarterly), Decimal('2600.00'))  # 100 x 2 x 13 weeks

    def test_money_format(self):
        self.assertEqual(billing.money(Decimal('1234.5')), 'R$ 1.234,50')
        self.assertEqual(billing.money(Decimal('0.05')), 'R$ 0,05')
        self.assertEqual(billing.money(None), '')


class PlanTemplateTests(BaseTestCase):
    def setUp(self):
        self.plan = make_plan(price='300.00')
        self.person = self.alice.person

    def form(self, **extra):
        data = {'person_name': 'Alice', 'email': self.alice.email}
        data.update(extra)
        return PersonForm(data, instance=self.person)

    def assign(self, **extra):
        form = self.form(plan=self.plan.pk, **extra)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_assigning_a_plan_copies_name_and_price(self):
        form = self.form(plan=self.plan.pk)
        self.assertTrue(form.is_valid(), form.errors)
        person = form.save()
        self.assertEqual((person.plan, person.plan_name, person.plan_price), (self.plan, 'Mensal 3x', Decimal('300.00')))

    def test_typed_values_win_over_the_template(self):
        form = self.form(plan=self.plan.pk, plan_name='Combo da Alice', plan_price='250.00')
        self.assertTrue(form.is_valid(), form.errors)
        person = form.save()
        self.assertEqual((person.plan_name, person.plan_price), ('Combo da Alice', Decimal('250.00')))

    def test_editing_the_template_does_not_change_students(self):
        self.assign()
        self.plan.price, self.plan.name = Decimal('999.00'), 'Renomeado'
        self.plan.save()
        person = Person.objects.get(pk=self.person.pk)
        self.assertEqual((person.plan_name, person.plan_price), ('Mensal 3x', Decimal('300.00')))

    def test_keeping_the_same_plan_does_not_reset_a_negotiated_price(self):
        form = self.form(plan=self.plan.pk, plan_price='250.00')
        form.is_valid()
        form.save()
        again = PersonForm({'person_name': 'Alice', 'email': self.alice.email, 'plan': self.plan.pk,
                            'plan_price': '250.00'}, instance=Person.objects.get(pk=self.person.pk))
        self.assertTrue(again.is_valid(), again.errors)
        self.assertEqual(again.save().plan_price, Decimal('250.00'))

    def test_inactive_plans_are_not_offered_but_the_current_one_stays(self):
        inactive = make_plan(name='Antigo', is_active=False)
        self.assertNotIn(inactive, PersonForm(instance=self.person).fields['plan'].queryset)
        self.person.plan = inactive
        self.assertIn(inactive, PersonForm(instance=self.person).fields['plan'].queryset)

    def test_deleting_a_template_keeps_the_student_values(self):
        self.assign()
        self.plan.delete()
        person = Person.objects.get(pk=self.person.pk)
        self.assertIsNone(person.plan)
        self.assertEqual((person.plan_name, person.plan_price), ('Mensal 3x', Decimal('300.00')))

    def test_catalog_pages_are_for_the_trainer_only(self):
        urls = [reverse('core:plan_list'), reverse('core:plan_create'), reverse('core:plan_edit', kwargs={'pk': self.plan.pk})]
        self.login(self.alice)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(urls[1], {'name': 'Hack', 'months': 1}).status_code, 403)
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_trainer_manages_the_catalog(self):
        self.login(self.trainer)
        response = self.client.post(reverse('core:plan_create'), {'name': 'Trimestral', 'months': 3, 'price': '800', 'is_active': 'on'})
        self.assertRedirects(response, reverse('core:plan_list'))
        plan = Plan.objects.get(name='Trimestral')
        self.assertContains(self.client.get(reverse('core:plan_list')), 'Trimestral')
        # duplicate names and bad prices are rejected
        bad = self.client.post(reverse('core:plan_create'), {'name': 'Trimestral', 'months': 3})
        self.assertIn('name', bad.context['form'].errors)
        bad = self.client.post(reverse('core:plan_create'), {'name': 'X', 'months': 3, 'price': '-1'})
        self.assertIn('price', bad.context['form'].errors)
        # deactivate through the edit form: hidden from the default list, kept with "inactive"
        self.client.post(reverse('core:plan_edit', kwargs={'pk': plan.pk}), {'name': 'Trimestral', 'months': 3, 'price': '800'})
        self.assertNotContains(self.client.get(reverse('core:plan_list')), 'Trimestral')
        self.assertContains(self.client.get(reverse('core:plan_list'), {'status': 'inactive'}), 'Trimestral')
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())

    def test_student_form_shows_the_plan_fields(self):
        self.login(self.trainer)
        page = self.client.get(reverse('core:student_edit', kwargs={'pk': self.person.pk}))
        for text in ('Plano (modelo)', 'Valor negociado do período', 'Valor/hora negociado', 'Mensal 3x'):
            self.assertContains(page, text)


class ConfidentialityTests(BaseTestCase):
    """The negotiated hourly rate and price are for the trainer's eyes only."""

    def setUp(self):
        plan = make_plan()
        Person.objects.filter(pk=self.alice.person.pk).update(
            plan=plan, plan_name=plan.name, plan_price=Decimal(PRICE), hourly_rate=Decimal(RATE))
        self.person = Person.objects.get(pk=self.alice.person.pk)

    def assertHidden(self, response):
        text = response.content.decode()
        for value in (RATE, PRICE, RATE.replace('.', ','), PRICE.replace('.', ',')):
            self.assertNotIn(value, text)

    def test_student_pages_never_show_the_values(self):
        self.login(self.alice)
        for name in ('core:home', 'core:my_profile', 'core:my_privacy', 'core:my_workouts', 'core:my_history'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertHidden(response)

    def test_student_cannot_set_them_through_the_profile_form(self):
        self.login(self.alice)
        self.client.post(reverse('core:my_profile'), {
            'person_name': 'Alice', 'hourly_rate': '1.00', 'plan_price': '1.00', 'plan': '', 'plan_name': 'Grátis'})
        person = Person.objects.get(pk=self.person.pk)
        self.assertEqual((person.hourly_rate, person.plan_price), (Decimal(RATE), Decimal(PRICE)))
        self.assertEqual(person.plan_name, 'Mensal 3x')

    def test_another_student_cannot_see_them(self):
        self.login(self.bob)
        self.assertEqual(self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).status_code, 403)
        self.assertEqual(self.client.get(reverse('core:payment_create', kwargs={'pk': self.person.pk})).status_code, 403)
        self.assertHidden(self.client.get(reverse('core:home')))

    def test_students_export_leaves_them_out_and_trainers_has_them(self):
        self.login(self.alice)
        response = self.client.post(reverse('core:my_export'))
        raw = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))).read('dados.json').decode()
        self.assertNotIn(RATE, raw)
        self.assertNotIn(PRICE, raw)
        self.assertNotIn('hourly_rate', raw)
        self.login(self.trainer)
        response = self.client.post(reverse('core:student_export', kwargs={'pk': self.person.pk}))
        data = json.loads(zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))).read('dados.json'))
        self.assertEqual(data['profile']['hourly_rate'], float(RATE))
        self.assertEqual(data['profile']['plan_price'], float(PRICE))

    def test_history_records_that_they_changed_but_not_the_values(self):
        self.login(self.trainer)
        self.client.post(reverse('core:student_edit', kwargs={'pk': self.person.pk}), {
            'person_name': 'Alice', 'email': self.alice.email, 'hourly_rate': '200.00', 'plan_price': '150.00'})
        event = self.person.events.get(kind='profile_edit')
        self.assertEqual(event.changes['hourly_rate'], [None, None])
        self.assertEqual(event.changes['plan_price'], [None, None])
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertNotContains(page, 'id="hist-rate"')
        history = page.content.decode().split('id="tab-historico"')[1]
        self.assertNotIn('187', history)
        self.assertNotIn('200.00', history)

    def test_trainer_sees_them_on_the_file(self):
        self.login(self.trainer)
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, '187,35')
        self.assertContains(page, '91,77')
        self.assertContains(page, 'o aluno não vê')

    def test_payment_event_has_no_value(self):
        self.login(self.trainer)
        self.client.post(reverse('core:payment_create', kwargs={'pk': self.person.pk}), {
            'plan': self.person.plan_id, 'amount': PRICE, 'paid_on': '2026-05-10', 'method': 'pix',
            'period_start': '2026-05-10', 'period_end': '2026-06-09'})
        event = self.person.events.get(kind='payment')
        self.assertNotIn(PRICE, event.summary + json.dumps(event.changes))


class PaymentTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.plan = make_plan(price='300.00')
        self.url = reverse('core:payment_create', kwargs={'pk': self.person.pk})
        self.login(self.trainer)

    def post(self, **extra):
        data = {'plan': self.plan.pk, 'amount': '300.00', 'paid_on': '2026-05-10', 'method': 'pix',
                'period_start': '2026-05-10', 'period_end': '2026-06-09', 'notes': ''}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_registering_advances_the_expiry_and_sets_the_plan(self):
        response = self.post()
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': self.person.pk}) + '#tab-pagamentos')
        person = Person.objects.get(pk=self.person.pk)
        self.assertEqual(person.plan_expires_on, date(2026, 6, 9))
        self.assertEqual((person.plan, person.plan_name), (self.plan, 'Mensal 3x'))
        payment = Payment.objects.get()
        self.assertEqual((payment.amount, payment.plan_name, payment.recorded_by), (Decimal('300.00'), 'Mensal 3x', self.trainer))
        self.assertEqual(person.events.get(kind='payment').actor, self.trainer)

    def test_older_payment_never_shortens_the_plan(self):
        self.post()
        self.post(paid_on='2026-03-10', period_start='2026-03-10', period_end='2026-04-09')
        self.assertEqual(Person.objects.get(pk=self.person.pk).plan_expires_on, date(2026, 6, 9))

    def test_deleting_recomputes_the_expiry(self):
        self.post()
        self.post(paid_on='2026-06-10', period_start='2026-06-10', period_end='2026-07-09')
        newest = Payment.objects.order_by('-period_end').first()
        self.client.post(reverse('core:payment_delete', kwargs={'pk': newest.pk}))
        self.assertEqual(Person.objects.get(pk=self.person.pk).plan_expires_on, date(2026, 6, 9))
        self.assertTrue(self.person.events.filter(kind='payment', summary__contains='excluído').exists())

    def test_deleting_the_last_payment_keeps_a_manual_expiry(self):
        self.post()
        Person.objects.filter(pk=self.person.pk).update(plan_expires_on=date(2026, 8, 1))
        self.client.post(reverse('core:payment_delete', kwargs={'pk': Payment.objects.get().pk}))
        self.assertEqual(Person.objects.get(pk=self.person.pk).plan_expires_on, date(2026, 8, 1))

    def test_validation(self):
        for extra, field in (({'amount': '0'}, 'amount'), ({'amount': '-5'}, 'amount'), ({'amount': ''}, 'amount'),
                             ({'period_end': '2026-05-01'}, 'period_end'), ({'paid_on': ''}, 'paid_on'),
                             ({'method': 'bitcoin'}, 'method')):
            response = self.post(**extra)
            self.assertEqual(response.status_code, 200, extra)
            self.assertIn(field, response.context['form'].errors, extra)
        self.assertFalse(Payment.objects.exists())

    def test_form_suggests_amount_and_period_for_the_chosen_plan(self):
        Person.objects.filter(pk=self.person.pk).update(plan=self.plan, plan_price=Decimal('280.00'))
        initial = self.client.get(self.url).context['form'].initial
        self.assertEqual(initial['amount'], Decimal('280.00'))
        quarterly = make_plan(name='Tri', months=3, price='800.00', spw=None)
        initial = self.client.get(self.url, {'plan': quarterly.pk}).context['form'].initial
        self.assertEqual(initial['amount'], Decimal('800.00'))
        self.assertEqual(initial['period_end'], billing.period_end_for(initial['period_start'], 3))

    def test_only_the_trainer_registers_payments(self):
        self.login(self.alice)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post().status_code, 403)
        self.client.logout()
        self.assertEqual(self.post().status_code, 302)
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(self.client.post(reverse('core:payment_delete', kwargs={'pk': 1})).status_code, 302)

    def test_payments_tab_and_ficha_card(self):
        self.post()
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'id="tab-pagamentos"')
        self.assertContains(page, 'R$ 300,00')
        self.assertContains(page, 'Registrar pagamento')


class FinancePageTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.today = timezone.localdate()
        self.plan = make_plan(price='300.00')

    def pay(self, person, amount, when, method='pix', name='x'):
        return Payment.objects.create(person=person, plan=self.plan, plan_name=name, amount=Decimal(amount), paid_on=when,
                                      method=method, period_start=when, period_end=when + timedelta(days=29))

    def test_month_summary_by_method(self):
        day = date(2026, 3, 15)
        self.pay(self.alice.person, '300.00', day, 'pix')
        self.pay(self.bob.person, '150.50', day, 'cash')
        self.pay(self.bob.person, '99.00', date(2026, 4, 2), 'pix')  # other month
        summary = billing.month_summary(2026, 3)
        self.assertEqual(summary['received'], Decimal('450.50'))
        self.assertEqual([(m['label'], m['total']) for m in summary['by_method']],
                         [('Pix', Decimal('300.00')), ('Dinheiro', Decimal('150.50'))])
        page = self.client.get(reverse('core:finance'), {'mes': '2026-03'})
        self.assertContains(page, 'R$ 450,50')
        self.assertContains(page, 'Março de 2026')

    def test_invalid_month_falls_back_to_the_current_one(self):
        for bad in ('2026-13', 'abc', '', '2026', '0-0'):
            response = self.client.get(reverse('core:finance'), {'mes': bad})
            self.assertEqual(response.status_code, 200, bad)
            self.assertEqual(response.context['month_value'], f'{self.today.year}-{self.today.month:02d}')

    def test_expected_revenue_uses_the_suggested_amount(self):
        first = self.today.replace(day=1)
        Person.objects.filter(pk=self.alice.person.pk).update(plan=self.plan, plan_expires_on=first + timedelta(days=3))
        Person.objects.filter(pk=self.bob.person.pk).update(plan_expires_on=first + timedelta(days=4))  # no plan: no value
        summary = billing.month_summary(self.today.year, self.today.month)
        self.assertEqual((summary['expected'], summary['expected_count'], summary['unpriced']), (Decimal('300.00'), 2, 1))

    def test_overdue_lists_only_active_students_with_amount_and_whatsapp(self):
        Person.objects.filter(pk=self.alice.person.pk).update(
            plan=self.plan, plan_name='Mensal 3x', plan_expires_on=self.today - timedelta(days=5), phone='(11) 98765-4321')
        Person.objects.filter(pk=self.bob.person.pk).update(plan_expires_on=self.today - timedelta(days=9), is_active=False)
        rows = billing.overdue_rows()
        self.assertEqual([(r['student'].person_name, r['days'], r['amount']) for r in rows], [('Alice', 5, Decimal('300.00'))])
        with override_settings(PIX_KEY='pix@maria.com'):
            page = self.client.get(reverse('core:finance'))
        self.assertContains(page, 'https://wa.me/5511987654321?text=')
        self.assertContains(page, 'pix%40maria.com')
        self.assertContains(page, 'R$ 300,00')

    def test_charge_message(self):
        person = self.alice.person
        person.plan_name = 'Mensal 3x'
        with override_settings(PIX_KEY='chave-pix'):
            text = billing.charge_message(person, Decimal('300'), self.today - timedelta(days=2))
        self.assertIn('Alice', text)
        self.assertIn('venceu em', text)
        self.assertIn('R$ 300,00', text)
        self.assertIn('Chave Pix: chave-pix', text)
        with override_settings(PIX_KEY=''):
            self.assertNotIn('Pix', billing.charge_message(person, None, self.today + timedelta(days=2)))
        self.assertIn('vence em', billing.charge_message(person, None, self.today + timedelta(days=2)))

    def test_csv_export_is_excel_friendly_and_safe(self):
        self.pay(self.alice.person, '1234.50', date(2026, 3, 15), 'pix', name='=HYPERLINK("x")')
        response = self.client.get(reverse('core:finance_export'), {'mes': '2026-03'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('pagamentos-2026-03.csv', response['Content-Disposition'])
        text = response.content.decode('utf-8')
        self.assertTrue(text.startswith('﻿'))
        lines = text.lstrip('﻿').splitlines()
        self.assertEqual(lines[0], 'Data;Aluno;Plano;Valor;Forma;Período de;Período até;Observação')
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
            self.make_overdue(i)
        few = count()
        for i in range(3, 20):
            self.make_overdue(i)
        self.assertEqual(count(), few)

    def make_overdue(self, i):
        user = User.objects.create_user(f'ov{i}', f'ov{i}@example.com', 'pw')
        Person.objects.create(user=user, person_name=f'Atrasado {i}', plan=self.plan,
                              plan_expires_on=self.today - timedelta(days=i + 1))

    def test_dashboard_card_for_overdue(self):
        self.assertNotContains(self.client.get(reverse('core:home')), 'Pagamentos em atraso')
        Person.objects.filter(pk=self.alice.person.pk).update(plan=self.plan, plan_expires_on=self.today - timedelta(days=3))
        page = self.client.get(reverse('core:home'))
        self.assertContains(page, 'Pagamentos em atraso')
        self.assertContains(page, 'wa.me')


class AgendaRulesTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.today = timezone.localdate()
        self.monday = week_start(self.today)

    def slot(self, person=None, weekday=None, start=time(7, 0), **extra):
        return ScheduleSlot.objects.create(
            person=person or self.person, weekday=self.today.weekday() if weekday is None else weekday,
            start_time=start, **extra)

    def test_week_helpers(self):
        self.assertEqual(week_start(date(2026, 5, 13)), date(2026, 5, 11))  # Wednesday -> Monday
        self.assertEqual(week_start(date(2026, 5, 11)), date(2026, 5, 11))
        self.assertEqual(parse_week('2026-05-13'), date(2026, 5, 11))
        self.assertEqual(parse_week('lixo', self.today), self.monday)
        self.assertEqual(parse_week(None, self.today), self.monday)

    def test_generation_is_idempotent(self):
        self.slot()
        first = ensure_appointments(self.today, self.today + timedelta(days=13))
        self.assertEqual(first, 2)  # today + next week
        self.assertEqual(ensure_appointments(self.today, self.today + timedelta(days=13)), 0)
        self.assertEqual(Appointment.objects.count(), 2)
        self.assertTrue(all(a.slot for a in Appointment.objects.all()))

    def test_generated_class_uses_the_slot_time_in_local_time(self):
        self.slot(start=time(7, 30), duration_minutes=45)
        ensure_appointments(self.today, self.today)
        appointment = Appointment.objects.get()
        local = timezone.localtime(appointment.starts_at)
        self.assertEqual((local.date(), local.hour, local.minute), (self.today, 7, 30))
        self.assertEqual(appointment.duration_minutes, 45)

    def test_no_classes_for_students_without_access(self):
        self.slot()
        self.person.set_status('paused', 'viagem')
        self.assertEqual(ensure_appointments(self.today, self.today + timedelta(days=6)), 0)

    def test_not_before_valid_from_or_after_valid_until(self):
        self.slot(valid_from=self.today + timedelta(days=7))
        self.assertEqual(ensure_appointments(self.today, self.today + timedelta(days=6)), 0)
        self.assertEqual(ensure_appointments(self.today, self.today + timedelta(days=7)), 1)
        ScheduleSlot.objects.all().delete()
        Appointment.objects.all().delete()
        self.slot(valid_until=self.today + timedelta(days=6))
        self.assertEqual(ensure_appointments(self.today, self.today + timedelta(days=14)), 1)

    def test_backfill_is_limited(self):
        self.slot(valid_from=self.today - timedelta(days=60))
        created = ensure_appointments(self.today - timedelta(days=60), self.today)
        self.assertLessEqual(created, BACKFILL_DAYS // 7 + 1)
        oldest = min(a.starts_at for a in Appointment.objects.all())
        self.assertGreaterEqual(timezone.localtime(oldest).date(), self.today - timedelta(days=BACKFILL_DAYS))

    def test_pausing_removes_only_future_scheduled_classes(self):
        slot = self.slot()
        future = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=3), slot=slot)
        attended_future = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=4),
                                                     status='attended')
        past = Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=3), slot=slot)
        self.person.set_status('former', 'mudou de cidade')
        self.assertFalse(Appointment.objects.filter(pk=future.pk).exists())
        self.assertTrue(Appointment.objects.filter(pk=attended_future.pk).exists())
        self.assertTrue(Appointment.objects.filter(pk=past.pk).exists())

    def test_conflicts(self):
        base = timezone.now().replace(hour=9, minute=0, second=0, microsecond=0)
        a = Appointment.objects.create(person=self.alice.person, starts_at=base)
        b = Appointment.objects.create(person=self.bob.person, starts_at=base + timedelta(minutes=30))
        c = Appointment.objects.create(person=self.alice.person, starts_at=base + timedelta(hours=3))
        self.assertEqual(conflicting_ids([a, b, c]), {a.pk, b.pk})
        b.status = 'cancelled'
        self.assertEqual(conflicting_ids([a, b, c]), set())
        b.status = 'scheduled'
        b.starts_at = base + timedelta(minutes=60)  # starts exactly when the first ends
        self.assertEqual(conflicting_ids([a, b, c]), set())

    def test_attendance_stats(self):
        now = timezone.now()
        for days, status in ((2, 'attended'), (5, 'attended'), (9, 'attended'), (12, 'missed'), (40, 'missed'),
                             (3, 'cancelled'), (6, 'rescheduled')):
            Appointment.objects.create(person=self.person, starts_at=now - timedelta(days=days), status=status)
        Appointment.objects.create(person=self.person, starts_at=now + timedelta(days=2), status='attended')  # future
        stats = attendance_stats(self.person, 30)
        self.assertEqual((stats['attended'], stats['missed'], stats['rate']), (3, 1, 75))
        self.assertIsNone(attendance_stats(self.bob.person, 30)['rate'])

    def test_deleting_a_slot_keeps_the_history(self):
        slot = self.slot()
        past = Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=2),
                                          slot=slot, status='attended')
        slot.delete()
        past.refresh_from_db()
        self.assertIsNone(past.slot)
        self.assertEqual(past.status, 'attended')

    def test_unique_class_per_student_and_time(self):
        when = timezone.now().replace(microsecond=0)
        Appointment.objects.create(person=self.person, starts_at=when)
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Appointment.objects.create(person=self.person, starts_at=when)

    def test_management_command(self):
        from django.core.management import call_command
        self.slot()
        out = io.StringIO()
        call_command('generate_appointments', '--weeks', '3', stdout=out)
        self.assertEqual(Appointment.objects.count(), 4)
        self.assertIn('4 aula(s)', out.getvalue())


class AgendaViewTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.today = timezone.localdate()
        self.login(self.trainer)

    def make(self, person=None, hour=9, status='scheduled', **extra):
        start = at(self.today, time(hour, 0))
        return Appointment.objects.create(person=person or self.person, starts_at=start, status=status, **extra)

    def test_week_view_generates_and_lists_classes(self):
        ScheduleSlot.objects.create(person=self.person, weekday=self.today.weekday(), start_time=time(7, 0))
        response = self.client.get(reverse('core:agenda'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertContains(response, 'Alice')
        self.assertContains(response, '07:00')
        self.client.get(reverse('core:agenda'))  # opening again does not duplicate
        self.assertEqual(Appointment.objects.count(), 1)

    def test_week_navigation_and_bad_values(self):
        for value in ('', 'abc', '2026-99-99'):
            self.assertEqual(self.client.get(reverse('core:agenda'), {'semana': value}).status_code, 200, value)
        nxt = (week_start(self.today) + timedelta(days=7)).isoformat()
        response = self.client.get(reverse('core:agenda'), {'semana': nxt})
        self.assertEqual(response.context['monday'].isoformat(), nxt)
        self.assertContains(response, 'Hoje')  # link back to the current week

    def test_status_actions(self):
        a = self.make()
        url = reverse('core:appointment_status', kwargs={'pk': a.pk})
        for status in ('attended', 'missed', 'cancelled', 'scheduled'):
            response = self.client.post(url, {'status': status})
            self.assertEqual(response.status_code, 302)
            a.refresh_from_db()
            self.assertEqual(a.status, status)
        self.assertIsNotNone(a.status_changed_at)
        for bad in ('rescheduled', 'dancing', ''):
            self.assertEqual(self.client.post(url, {'status': bad}).status_code, 400, bad)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_status_redirect_keeps_the_week(self):
        a = self.make()
        url = reverse('core:appointment_status', kwargs={'pk': a.pk}) + '?next=/agenda/%3Fsemana%3D2026-05-11'
        self.assertRedirects(self.client.post(url, {'status': 'attended'}), '/agenda/?semana=2026-05-11',
                             fetch_redirect_response=False)
        evil = reverse('core:appointment_status', kwargs={'pk': a.pk}) + '?next=https://evil.example/'
        self.assertNotIn('evil', self.client.post(evil, {'status': 'missed'}).url)

    def test_create_one_off_class(self):
        url = reverse('core:appointment_create')
        response = self.client.post(url, {
            'person': self.person.pk, 'date': self.today.isoformat(), 'time': '18:30', 'duration_minutes': 60,
            'kind': 'trial', 'notes': 'primeira aula'})
        self.assertEqual(response.status_code, 302)
        a = Appointment.objects.get()
        self.assertEqual((a.kind, a.slot), ('trial', None))
        self.assertEqual(timezone.localtime(a.starts_at).strftime('%H:%M'), '18:30')
        again = self.client.post(url, {'person': self.person.pk, 'date': self.today.isoformat(), 'time': '18:30',
                                       'duration_minutes': 60, 'kind': 'trial'})
        self.assertEqual(again.status_code, 200)
        self.assertIn('time', again.context['form'].errors)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_create_validates_and_only_offers_active_students(self):
        self.bob.person.set_status('paused', 'x')
        form = self.client.get(reverse('core:appointment_create'), {'person': self.person.pk}).context['form']
        self.assertEqual(list(form.fields['person'].queryset), [self.person])
        self.assertEqual(form.initial['person'], self.person.pk)
        response = self.client.post(reverse('core:appointment_create'), {
            'person': self.person.pk, 'date': 'nope', 'time': '99:99', 'duration_minutes': 5, 'kind': 'x'})
        self.assertEqual(set(response.context['form'].errors), {'date', 'time', 'duration_minutes', 'kind'})

    def test_overlap_warns_but_saves(self):
        self.make(person=self.bob.person, hour=9)
        response = self.client.post(reverse('core:appointment_create'), {
            'person': self.person.pk, 'date': self.today.isoformat(), 'time': '09:30', 'duration_minutes': 60,
            'kind': 'regular'}, follow=True)
        self.assertContains(response, 'se sobrepõe a outra aula (Bob)')
        self.assertEqual(Appointment.objects.count(), 2)
        page = self.client.get(reverse('core:agenda'))
        self.assertContains(page, 'Sobreposta a outra aula')

    def test_reschedule(self):
        a = self.make(status='missed')
        url = reverse('core:appointment_reschedule', kwargs={'pk': a.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        tomorrow = self.today + timedelta(days=1)
        response = self.client.post(url, {'person': self.person.pk, 'date': tomorrow.isoformat(), 'time': '10:00',
                                          'duration_minutes': 60, 'kind': 'makeup'})
        self.assertEqual(response.status_code, 302)
        a.refresh_from_db()
        self.assertEqual(a.status, 'rescheduled')
        new = Appointment.objects.exclude(pk=a.pk).get()
        self.assertEqual((new.status, new.person, new.kind), ('scheduled', self.person, 'makeup'))
        self.assertEqual(timezone.localtime(new.starts_at).date(), tomorrow)
        # a rescheduled class cannot be rescheduled again, and the same time is refused
        self.assertEqual(self.client.get(url).status_code, 404)
        same = self.client.post(reverse('core:appointment_reschedule', kwargs={'pk': new.pk}), {
            'person': self.person.pk, 'date': tomorrow.isoformat(), 'time': '10:00', 'duration_minutes': 60,
            'kind': 'makeup'})
        self.assertEqual(same.status_code, 200)

    def test_delete_only_one_off_classes(self):
        slot = ScheduleSlot.objects.create(person=self.person, weekday=0, start_time=time(7, 0))
        fixed = self.make(slot=slot)
        one_off = self.make(hour=15)
        self.client.post(reverse('core:appointment_delete', kwargs={'pk': fixed.pk}))
        self.assertTrue(Appointment.objects.filter(pk=fixed.pk).exists())
        self.client.post(reverse('core:appointment_delete', kwargs={'pk': one_off.pk}))
        self.assertFalse(Appointment.objects.filter(pk=one_off.pk).exists())

    def test_cancelled_fixed_class_does_not_come_back(self):
        ScheduleSlot.objects.create(person=self.person, weekday=self.today.weekday(), start_time=time(7, 0))
        self.client.get(reverse('core:agenda'))
        a = Appointment.objects.get()
        self.client.post(reverse('core:appointment_status', kwargs={'pk': a.pk}), {'status': 'cancelled'})
        self.client.get(reverse('core:agenda'))
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(Appointment.objects.get().status, 'cancelled')

    def test_slots_from_the_students_file(self):
        add = reverse('core:slot_add', kwargs={'pk': self.person.pk})
        data = {'weekday': 2, 'start_time': '07:00', 'duration_minutes': 60}
        response = self.client.post(add, data)
        self.assertRedirects(response, reverse('core:student_detail', kwargs={'pk': self.person.pk}) + '#tab-agenda')
        self.assertEqual(self.person.slots.count(), 1)
        self.assertTrue(self.person.appointments.exists())  # upcoming classes are there right away
        self.client.post(add, data)  # the same slot again does not duplicate
        self.assertEqual(self.person.slots.count(), 1)
        bad = self.client.post(add, {'weekday': 9, 'start_time': 'x', 'duration_minutes': 1}, follow=True)
        self.assertContains(bad, 'Horário inválido')
        self.assertEqual(self.person.slots.count(), 1)
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'id="tab-agenda"')
        self.assertContains(page, 'Quarta')
        slot = self.person.slots.get()
        self.client.post(reverse('core:slot_delete', kwargs={'pk': slot.pk}))
        self.assertFalse(self.person.slots.exists())
        self.assertFalse(self.person.appointments.filter(status='scheduled', starts_at__gt=timezone.now()).exists())

    def test_attendance_block_on_the_file(self):
        Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=2), status='attended')
        Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=4), status='missed')
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, '50%')

    def test_dashboard_cards(self):
        self.make(hour=9)
        self.make(person=self.bob.person, hour=10, status='cancelled')
        miss = Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=2), status='missed')
        Person.objects.filter(pk=self.person.pk).update(phone='(11) 98765-4321')
        page = self.client.get(reverse('core:home'))
        self.assertContains(page, 'Aulas de hoje')
        self.assertContains(page, '09:00')
        self.assertNotContains(page, '10:00')  # cancelled classes are not listed
        self.assertContains(page, 'Faltas dos últimos 7 dias')
        self.assertContains(page, 'Sentimos%20sua%20falta')
        self.assertContains(page, reverse('core:appointment_reschedule', kwargs={'pk': miss.pk}))

    def test_missed_class_older_than_a_week_is_not_in_the_card(self):
        Appointment.objects.create(person=self.person, starts_at=timezone.now() - timedelta(days=10), status='missed')
        self.assertNotContains(self.client.get(reverse('core:home')), 'Faltas dos últimos 7 dias')

    def test_students_cannot_use_the_agenda(self):
        a = self.make()
        urls = [('get', reverse('core:agenda')), ('get', reverse('core:appointment_create')),
                ('get', reverse('core:appointment_reschedule', kwargs={'pk': a.pk})),
                ('post', reverse('core:appointment_status', kwargs={'pk': a.pk})),
                ('post', reverse('core:appointment_delete', kwargs={'pk': a.pk})),
                ('post', reverse('core:slot_add', kwargs={'pk': self.person.pk}))]
        self.login(self.alice)
        for method, url in urls:
            self.assertEqual(getattr(self.client, method)(url).status_code, 403, url)
        self.client.logout()
        for method, url in urls:
            self.assertEqual(getattr(self.client, method)(url).status_code, 302, url)
        a.refresh_from_db()
        self.assertEqual(a.status, 'scheduled')

    def test_menu_has_agenda_and_finance_for_the_trainer_only(self):
        page = self.client.get(reverse('core:home'))
        self.assertContains(page, reverse('core:agenda'))
        self.assertContains(page, reverse('core:plan_list'))
        self.login(self.alice)
        page = self.client.get(reverse('core:home'))
        self.assertNotContains(page, reverse('core:agenda'))
        self.assertNotContains(page, reverse('core:finance'))


class AgendaPrivacyTests(BaseTestCase):
    def test_exports_include_payments_and_classes_and_deletion_removes_them(self):
        person = self.alice.person
        Payment.objects.create(person=person, plan_name='Mensal', amount=Decimal('300'), paid_on=date(2026, 5, 10),
                               period_start=date(2026, 5, 10), period_end=date(2026, 6, 9))
        slot = ScheduleSlot.objects.create(person=person, weekday=0, start_time=time(7, 0))
        Appointment.objects.create(person=person, starts_at=timezone.now(), slot=slot, status='attended')
        self.login(self.alice)
        response = self.client.post(reverse('core:my_export'))
        data = json.loads(zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))).read('dados.json'))
        self.assertEqual(data['payments'][0]['amount'], 300.0)
        self.assertEqual(data['schedule']['weekly_slots'][0]['weekday'], 0)
        self.assertEqual(data['schedule']['appointments'][0]['status'], 'attended')
        self.login(self.trainer)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('core:student_delete', kwargs={'pk': person.pk}))
        for model in (Payment, ScheduleSlot, Appointment):
            self.assertFalse(model.objects.exists(), model)
