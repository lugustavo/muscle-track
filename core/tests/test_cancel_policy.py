from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core import billing, packages, refunds
from core.models import Package, StudentEvent

from .base import BaseTestCase, page_text
from .test_refunds import klass, make_package

D = Decimal


def policy_package(free_days=0, percent='0', fixed='0', name='Pacote 10', **extra):
    package = make_package(name=name, **extra)
    package.cancel_free_days, package.cancel_fee_percent, package.cancel_fee_fixed = free_days, D(percent), D(fixed)
    package.save()
    return package


class PolicyBase(BaseTestCase):
    """Alice bought 10 classes at 50 (500, paid in full) and had 3 of them."""

    def setUp(self):
        self.person = self.alice.person

    def buy(self, package, sold_days_ago=0, classes=3, paid='500'):
        sold = packages.sell(self.person, package, sold_on=timezone.localdate() - timedelta(days=sold_days_ago))
        if D(paid):
            billing.register_payment(self.person, package=sold, amount=D(paid), paid_on=timezone.localdate(),
                                     method='pix', actor=self.trainer)
        for days in range(classes):
            klass(self.person, 9 - days)
        packages.sync(self.person)
        sold.refresh_from_db()
        return sold


class FeeTests(PolicyBase):
    def test_nothing_set_nothing_asked(self):
        sold = self.buy(policy_package())
        self.assertEqual(refunds.policy_fee(sold)['fee'], D('0'))
        self.assertFalse(refunds.has_policy(sold))
        self.assertEqual(refunds.policy_lines(sold), [])

    def test_percent_of_the_value_of_the_classes_not_used(self):
        sold = self.buy(policy_package(percent='20'))        # 7 left x 50 = 350 -> 70
        result = refunds.policy_fee(sold)
        self.assertEqual((result['fee'], result['percent_part'], result['fixed_part']), (D('70.00'), D('70.00'), D('0')))
        self.assertEqual(refunds.cancellation_quote(sold, result['fee'])['suggested'], D('280.00'))   # 500 - 150 - 70

    def test_fixed_fee_and_percent_add_up(self):
        sold = self.buy(policy_package(percent='10', fixed='15'))
        self.assertEqual(refunds.policy_fee(sold)['fee'], D('50.00'))      # 35 + 15

    def test_unused_classes_not_refundable(self):
        sold = self.buy(policy_package(percent='100'))
        result = refunds.policy_fee(sold)
        self.assertEqual(result['fee'], D('350.00'))
        self.assertEqual(refunds.cancellation_quote(sold, result['fee'])['suggested'], D('0'))

    def test_no_fee_inside_the_free_days(self):
        sold = self.buy(policy_package(free_days=7, percent='20', fixed='10'), sold_days_ago=7)
        result = refunds.policy_fee(sold)
        self.assertTrue(result['free'])
        self.assertEqual((result['fee'], result['free_until']), (D('0'), timezone.localdate()))

    def test_the_fee_starts_the_day_after_the_free_days(self):
        sold = self.buy(policy_package(free_days=7, percent='20'), sold_days_ago=8)
        result = refunds.policy_fee(sold)
        self.assertFalse(result['free'])
        self.assertEqual(result['fee'], D('70.00'))

    def test_the_fee_never_exceeds_what_is_left_of_the_payment(self):
        sold = self.buy(policy_package(percent='100', fixed='500'), paid='200')       # paid 200, 3 classes given = 150
        self.assertEqual(refunds.policy_fee(sold)['fee'], D('50.00'))
        unpaid = self.buy(policy_package(percent='50', name='Outro'), paid='0', classes=0)
        self.assertEqual(refunds.policy_fee(unpaid)['fee'], D('0'))

    def test_an_unpriced_package_has_no_percent_to_take(self):
        sold = self.buy(policy_package(percent='50', fixed='10', name='Sem valor', per='0'), paid='0')
        self.assertEqual(refunds.policy_fee(sold)['percent_part'], D('0'))

    def test_the_sale_copies_the_policy_and_later_catalog_edits_do_not_change_it(self):
        package = policy_package(free_days=5, percent='25', fixed='10')
        sold = self.buy(package)
        package.cancel_fee_percent = D('90')
        package.save()
        sold.refresh_from_db()
        self.assertEqual((sold.cancel_free_days, sold.cancel_fee_percent, sold.cancel_fee_fixed), (5, D('25.00'), D('10.00')))


class WordsTests(PolicyBase):
    def test_trainer_wording(self):
        sold = self.buy(policy_package(free_days=7, percent='20', fixed='15'))
        self.assertEqual(refunds.policy_lines(sold), [
            'Cancelando em até 7 dias depois da compra, não há taxa: só as aulas já dadas são descontadas.',
            'Depois disso, taxa de 20% do valor das aulas não usadas.', 'Taxa fixa: 15,00 €.'])

    def test_student_wording_has_no_amounts_of_money(self):
        sold = self.buy(policy_package(free_days=1, percent='12.5', fixed='15'))
        lines = refunds.policy_lines(sold, student=True)
        self.assertEqual(lines[0], 'Cancelando em até 1 dia depois da compra, não há taxa: só as aulas já dadas são descontadas.')
        self.assertEqual(lines[1], 'Depois disso, taxa de 12.5% do valor das aulas não usadas.')
        self.assertEqual(lines[2], 'Mais uma taxa fixa combinada.')
        self.assertNotIn('15', ' '.join(lines[2:]))

    def test_everything_goes_back_wording(self):
        sold = self.buy(policy_package(percent='100'))
        self.assertEqual(refunds.policy_lines(sold), ['As aulas não usadas não são devolvidas.'])
        sold.cancel_free_days = 3
        self.assertEqual(refunds.policy_lines(sold)[1], 'Depois disso, as aulas não usadas não são devolvidas.')


class ScreenTests(PolicyBase):
    def setUp(self):
        super().setUp()
        self.login(self.trainer)

    def cancel_url(self, sold):
        return reverse('core:student_package_cancel', kwargs={'pk': self.person.pk, 'package_pk': sold.pk})

    def test_the_cancel_page_starts_with_the_policy_fee(self):
        sold = self.buy(policy_package(percent='20'))
        page = self.client.get(self.cancel_url(sold))
        self.assertEqual(page.context['fee'], D('70.00'))
        self.assertEqual(page.context['quote']['suggested'], D('280.00'))
        html = page_text(page)
        self.assertIn('data-policy-box', html)
        self.assertIn('Taxa pela política hoje', html)
        self.assertIn('280,00', html)

    def test_the_trainer_can_waive_or_change_the_fee(self):
        sold = self.buy(policy_package(percent='20'))
        page = self.client.get(self.cancel_url(sold), {'fee': '0'})
        self.assertEqual(page.context['fee'], D('0'))
        self.assertEqual(page.context['quote']['suggested'], D('350.00'))
        self.assertContains(page, 'Voltar à taxa da política')
        page = self.client.get(self.cancel_url(sold), {'fee': '25'})
        self.assertEqual(page.context['quote']['suggested'], D('325.00'))

    def test_cancelling_keeps_the_fee_that_was_posted(self):
        sold = self.buy(policy_package(percent='20'))
        page = self.client.get(self.cancel_url(sold))
        response = self.client.post(self.cancel_url(sold), {
            'fee': '70.00', 'amount': page.context['quote']['suggested'], 'method': 'transfer',
            'refunded_on': timezone.localdate().isoformat(), 'reason': ''})
        self.assertEqual(response.status_code, 302)
        sold.refresh_from_db()
        self.assertEqual(sold.cancel_fee, D('70.00'))
        self.assertEqual(sold.refunds.get().amount, D('280.00'))

    def test_free_period_is_announced(self):
        sold = self.buy(policy_package(free_days=10, percent='20'), sold_days_ago=2)
        page = self.client.get(self.cancel_url(sold))
        self.assertEqual(page.context['fee'], D('0'))
        self.assertContains(page, 'data-policy-free')

    def test_no_policy_no_box_and_the_old_behaviour(self):
        sold = self.buy(policy_package())
        page = self.client.get(self.cancel_url(sold))
        self.assertNotContains(page, 'data-policy-box')
        self.assertEqual(page.context['fee'], D('0'))
        self.assertEqual(page.context['quote']['suggested'], D('350.00'))

    def test_catalog_form_saves_the_policy_and_lists_it(self):
        data = {'name': 'Pacote 8', 'description': '', 'sessions': '8', 'valid_days': '60', 'pricing_mode': 'per_session',
                'price_per_session': '55', 'price_total': '', 'cancel_free_days': '7', 'cancel_fee_percent': '20',
                'cancel_fee_fixed': '', 'is_active': 'on'}
        response = self.client.post(reverse('core:plan_create'), data)
        self.assertEqual(response.status_code, 302)
        package = Package.objects.get(name='Pacote 8')
        self.assertEqual((package.cancel_free_days, package.cancel_fee_percent, package.cancel_fee_fixed), (7, D('20'), D('0')))
        listing = self.client.get(reverse('core:plan_list'))
        self.assertContains(listing, 'data-policy')
        self.assertContains(listing, 'taxa de 20% do valor das aulas não usadas')

    def test_catalog_form_refuses_a_percent_above_100(self):
        data = {'name': 'Pacote 8', 'description': '', 'sessions': '8', 'valid_days': '60', 'pricing_mode': 'per_session',
                'price_per_session': '55', 'cancel_free_days': '0', 'cancel_fee_percent': '120', 'cancel_fee_fixed': '0'}
        response = self.client.post(reverse('core:plan_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('cancel_fee_percent', response.context['form'].errors)

    def test_catalog_form_without_the_policy_fields_still_saves(self):
        data = {'name': 'Pacote 8', 'description': '', 'sessions': '8', 'valid_days': '60', 'pricing_mode': 'per_session',
                'price_per_session': '55', 'is_active': 'on'}
        self.assertEqual(self.client.post(reverse('core:plan_create'), data).status_code, 302)
        package = Package.objects.get(name='Pacote 8')
        self.assertEqual((package.cancel_free_days, package.cancel_fee_percent, package.cancel_fee_fixed), (0, D('0'), D('0')))

    def test_edit_the_policy_of_a_sold_package_and_it_is_logged(self):
        sold = self.buy(policy_package())
        url = reverse('core:student_package_edit', kwargs={'pk': self.person.pk, 'package_pk': sold.pk})
        base = {'name': sold.name, 'sessions': sold.sessions, 'valid_days': sold.valid_days, 'pricing_mode': 'per_session',
                'price_per_session': '50', 'price_total': '', 'notes': '', 'cancel_free_days': '14',
                'cancel_fee_percent': '30', 'cancel_fee_fixed': '5'}
        self.assertEqual(self.client.post(url, base).status_code, 302)
        sold.refresh_from_db()
        self.assertEqual((sold.cancel_free_days, sold.cancel_fee_percent, sold.cancel_fee_fixed), (14, D('30.00'), D('5.00')))
        event = self.person.events.filter(kind=StudentEvent.Kind.PACKAGE, summary__contains='Pacote editado').first()
        self.assertIn('política de cancelamento alterada', event.summary)
        self.assertNotIn('30', event.summary)       # values stay out of the history

    def test_editing_without_the_policy_fields_leaves_the_policy(self):
        sold = self.buy(policy_package(percent='20'))
        url = reverse('core:student_package_edit', kwargs={'pk': self.person.pk, 'package_pk': sold.pk})
        self.client.post(url, {'name': 'Novo nome', 'sessions': sold.sessions, 'valid_days': sold.valid_days,
                               'pricing_mode': 'per_session', 'price_per_session': '50', 'price_total': '', 'notes': ''})
        sold.refresh_from_db()
        self.assertEqual((sold.name, sold.cancel_fee_percent), ('Novo nome', D('20.00')))

    def test_the_policy_fields_in_the_edit_page(self):
        sold = self.buy(policy_package(percent='20'))
        page = self.client.get(reverse('core:student_package_edit', kwargs={'pk': self.person.pk, 'package_pk': sold.pk}))
        self.assertContains(page, 'data-cancel-policy')
        self.assertEqual(page.context['form'].initial['cancel_fee_percent'], D('20.00'))


class StudentSideTests(PolicyBase):
    def test_the_student_reads_the_terms_without_amounts(self):
        self.buy(policy_package(free_days=7, percent='20', fixed='15'), classes=1)
        self.login(self.alice)
        html = page_text(self.client.get(reverse('core:my_package')))
        self.assertIn('data-package-policy', html)
        self.assertIn('Se você precisar cancelar o pacote', html)
        self.assertIn('taxa de 20% do valor das aulas não usadas', html)
        self.assertIn('Mais uma taxa fixa combinada', html)
        self.assertNotIn('15,00', html)

    def test_no_policy_nothing_to_show(self):
        self.buy(policy_package(), classes=1)
        self.login(self.alice)
        self.assertNotContains(self.client.get(reverse('core:my_package')), 'data-package-policy')

    def test_a_cancelled_package_shows_no_terms(self):
        sold = self.buy(policy_package(percent='20'), classes=1)
        packages.cancel_package(sold, self.trainer)
        self.login(self.alice)
        self.assertNotContains(self.client.get(reverse('core:my_package')), 'data-package-policy')

    def test_in_english(self):
        self.buy(policy_package(free_days=7, percent='20'), classes=1)
        self.login(self.alice)
        page = self.client.get(reverse('core:my_package'), HTTP_ACCEPT_LANGUAGE='en')
        self.assertContains(page, 'If you need to cancel the package')
        self.assertContains(page, 'fee of 20% of the value of the unused classes')
