from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core.contract import contract_card
from core.models import Appointment, Payment, Person, Plan

from .base import BaseTestCase

TODAY = date(2026, 10, 14)   # a Wednesday


def when(day, hour=9):
    return timezone.make_aware(datetime.combine(day, time(hour)))


class ContractCardTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person

    def card(self, **fields):
        Person.objects.filter(pk=self.person.pk).update(**fields)
        person = Person.objects.select_related('plan').get(pk=self.person.pk)
        return contract_card(person, person.payments.first(), '', TODAY)

    def label(self, **fields):
        card = self.card(**fields)
        return card['status'], card['label'], card['css']

    def test_status_wording(self):
        self.assertEqual(self.label(), ('none', 'Sem plano', 'secondary'))
        self.assertEqual(self.label(plan_name='Mensal'), ('open', 'Sem vencimento', 'info'))
        self.assertEqual(self.label(plan_name='Mensal', plan_expires_on=TODAY + timedelta(days=40)), ('ok', 'Em dia', 'success'))
        self.assertEqual(self.label(plan_expires_on=TODAY + timedelta(days=5)), ('expiring', 'Vence em 5 dias', 'warning'))
        self.assertEqual(self.label(plan_expires_on=TODAY + timedelta(days=1)), ('expiring', 'Vence em 1 dia', 'warning'))
        self.assertEqual(self.label(plan_expires_on=TODAY), ('expiring', 'Vence hoje', 'warning'))
        self.assertEqual(self.label(plan_expires_on=TODAY - timedelta(days=1)), ('expired', 'Vencido há 1 dia', 'danger'))
        self.assertEqual(self.label(plan_expires_on=TODAY - timedelta(days=3)), ('expired', 'Vencido há 3 dias', 'danger'))

    def test_paused_and_former_win_over_the_dates(self):
        expired = TODAY - timedelta(days=9)
        self.assertEqual(self.label(plan_expires_on=expired, status='paused')[:2], ('paused', 'Pausado'))
        self.assertEqual(self.label(plan_expires_on=expired, status='former')[:2], ('former', 'Ex-aluno'))

    def test_period_progress(self):
        expires = TODAY + timedelta(days=20)
        Person.objects.filter(pk=self.person.pk).update(plan_expires_on=expires)
        self.assertIsNone(self.card()['period'])   # no payment yet
        Payment.objects.create(person=self.person, amount=Decimal('100'), paid_on=TODAY - timedelta(days=10),
                               period_start=TODAY - timedelta(days=10), period_end=expires)
        period = self.card()['period']
        self.assertEqual((period['days_used'], period['days_total'], period['percent']), (10, 30, 33))

    def test_period_is_clamped_and_ignores_inconsistent_data(self):
        Payment.objects.create(person=self.person, amount=Decimal('100'), paid_on=TODAY, period_start=TODAY - timedelta(days=60),
                               period_end=TODAY - timedelta(days=30))
        late = self.card(plan_expires_on=TODAY - timedelta(days=30))['period']
        self.assertEqual(late['percent'], 100)
        self.assertIsNone(self.card(plan_expires_on=TODAY - timedelta(days=90))['period'])   # starts after it ends

    def test_classes_this_week_only_counts_attended_ones_of_the_week(self):
        plan = Plan.objects.create(name='Mensal 3x', sessions_per_week=3)
        self.assertIsNone(self.card()['classes_done'])
        card = self.card(plan=plan)
        self.assertEqual((card['classes_per_week'], card['classes_done']), (3, 0))
        monday = TODAY - timedelta(days=TODAY.weekday())
        Appointment.objects.create(person=self.person, starts_at=when(monday), status='attended')
        Appointment.objects.create(person=self.person, starts_at=when(monday + timedelta(days=2)), status='attended')
        Appointment.objects.create(person=self.person, starts_at=when(monday + timedelta(days=1)), status='missed')
        Appointment.objects.create(person=self.person, starts_at=when(monday - timedelta(days=1)), status='attended')   # last week
        Appointment.objects.create(person=self.person, starts_at=when(monday + timedelta(days=7)), status='attended')   # next week
        Appointment.objects.create(person=self.bob.person, starts_at=when(monday), status='attended')                  # another student
        self.assertEqual(self.card()['classes_done'], 2)

    def test_value_against_the_catalog_price(self):
        plan = Plan.objects.create(name='Mensal', price=Decimal('200'))
        self.assertEqual(self.card(plan=plan, plan_price=Decimal('180'))['against_table'], {'percent': 10, 'direction': 'below'})
        self.assertEqual(self.card(plan_price=Decimal('250'))['against_table'], {'percent': 25, 'direction': 'above'})
        self.assertEqual(self.card(plan_price=Decimal('200'))['against_table'], {'percent': 0, 'direction': 'same'})
        self.assertIsNone(self.card(plan_price=None)['against_table'])
        self.assertIsNone(self.card(plan=None, plan_price=Decimal('180'))['against_table'])

    def test_main_button_follows_the_situation(self):
        person = Person.objects.get(pk=self.person.pk)
        late = contract_card(person, None, 'https://wa.me/x', TODAY)   # status none: not late
        self.assertFalse(late['charge_first'])
        Person.objects.filter(pk=person.pk).update(plan_expires_on=TODAY - timedelta(days=2))
        person.refresh_from_db()
        self.assertTrue(contract_card(person, None, 'https://wa.me/x', TODAY)['charge_first'])
        self.assertFalse(contract_card(person, None, '', TODAY)['charge_first'])   # no phone, no link
        Person.objects.filter(pk=person.pk).update(plan_expires_on=TODAY + timedelta(days=30))
        person.refresh_from_db()
        self.assertFalse(contract_card(person, None, 'https://wa.me/x', TODAY)['charge_first'])


class ContractPageTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.person = self.alice.person
        self.url = reverse('core:student_detail', kwargs={'pk': self.person.pk})

    def test_card_sits_above_the_tabs_once(self):
        html = self.client.get(self.url).content.decode()
        self.assertEqual(html.count('data-contract '), 1)
        self.assertLess(html.index('data-contract '), html.index('id="studentTabs"'))
        self.assertIn('Sem plano', html)

    def test_expired_contract_highlights_charging(self):
        Person.objects.filter(pk=self.person.pk).update(
            plan_name='Mensal', plan_expires_on=timezone.localdate() - timedelta(days=4), phone='(11) 98765-4321')
        html = self.client.get(self.url).content.decode()
        card = html[html.index('data-contract '):html.index('id="contractMore"')]
        self.assertIn('data-contract-state="expired"', card)
        self.assertIn('Vencido há 4 dias', card)
        self.assertLess(card.index('btn btn-success"><i class="bi bi-whatsapp"></i> Cobrar'), card.index('Registrar pagamento'))

    def test_secret_values_start_hidden_and_never_reach_the_student(self):
        Person.objects.filter(pk=self.person.pk).update(plan_price=Decimal('432.10'), hourly_rate=Decimal('77.70'))
        html = self.client.get(self.url).content.decode()
        block = html[html.index('data-contract-values'):]
        self.assertRegex(block, r'data-contract-values hidden')
        self.assertIn('432,10', html)
        self.login(self.alice)
        for name in ('core:home', 'core:my_profile', 'core:my_privacy', 'core:my_history'):
            page = self.client.get(reverse(name)).content.decode()
            for secret in ('432,10', '432.10', '77,70', '77.70', 'data-contract'):
                self.assertNotIn(secret, page, (name, secret))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_old_plan_card_is_gone_from_the_ficha_tab(self):
        html = self.client.get(self.url).content.decode()
        self.assertNotIn('<i class="bi bi-credit-card"></i> Plano</strong>', html)

    def test_vocabulary_in_the_form(self):
        html = self.client.get(reverse('core:student_edit', kwargs={'pk': self.person.pk})).content.decode()
        for text in ('Plano do catálogo', 'Nome do contrato', 'Vencimento do contrato'):
            self.assertIn(text, html)
        for old in ('Plano contratado', 'Plano (modelo)', 'Vencimento do plano', 'negociado'):
            self.assertNotIn(old, html)

    def test_mobile_details_are_collapsible_and_always_open_on_desktop(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-bs-target="#contractMore"', html)
        self.assertIn('class="collapse contract-more" id="contractMore"', html)
