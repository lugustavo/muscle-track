from datetime import datetime, time, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core import billing, packages, refunds
from core.models import Appointment, Package, Payment, Refund, ScheduleSlot, StudentEvent

from .base import BaseTestCase

D = Decimal


def make_package(name='Pacote 10', sessions=10, days=60, per='50'):
    return Package.objects.create(name=name, sessions=sessions, valid_days=days, pricing_mode='per_session',
                                  price_per_session=D(per))


def klass(person, days_ago, status='attended'):
    day = timezone.localdate() - timedelta(days=days_ago)
    return Appointment.objects.create(person=person, starts_at=timezone.make_aware(datetime.combine(day, time(9))), status=status)


class RefundBase(BaseTestCase):
    """Alice bought 10 classes at 50 (total 500), paid 500 in two payments, and had 3 classes."""

    def setUp(self):
        self.person = self.alice.person
        self.sold = packages.sell(self.person, make_package())
        self.first = billing.register_payment(self.person, package=self.sold, amount=D('300'), paid_on=timezone.localdate(),
                                              method='pix', actor=self.trainer)
        self.second = billing.register_payment(self.person, package=self.sold, amount=D('200'), paid_on=timezone.localdate(),
                                               method='cash', actor=self.trainer)
        for days in (9, 6, 3):
            klass(self.person, days)
        packages.sync(self.person)
        self.sold.refresh_from_db()


class QuoteTests(RefundBase):
    def test_paid_minus_classes_given_minus_fee(self):
        quote = refunds.cancellation_quote(self.sold, D('20'))
        self.assertEqual((quote['paid'], quote['used'], quote['used_value'], quote['fee'], quote['suggested']),
                         (D('500'), 3, D('150.00'), D('20'), D('330.00')))
        self.assertEqual(quote['left'], 7)

    def test_never_below_zero_nor_above_what_was_paid(self):
        self.assertEqual(refunds.cancellation_quote(self.sold, D('9999'))['suggested'], D('0'))
        Payment.objects.all().delete()
        self.assertEqual(refunds.cancellation_quote(self.sold)['suggested'], D('0'))

    def test_without_a_price_the_classes_are_not_valued(self):
        unpriced = packages.sell(self.bob.person, Package.objects.create(name='Sem valor', sessions=4, valid_days=30, pricing_mode='per_session'))
        quote = refunds.cancellation_quote(unpriced)
        self.assertEqual((quote['per_class'], quote['used_value']), (D('0'), D('0')))

    def test_the_total_is_split_when_there_is_no_price_per_class(self):
        self.sold.price_per_session = None
        self.assertEqual(refunds.per_class_price(self.sold), D('50.00'))

    def test_text_explains_the_calculation(self):
        text = refunds.quote_text(self.sold, refunds.cancellation_quote(self.sold, D('20')), D('330'))
        for part in ('Pacote 10', 'Aulas dadas: 3 de 10', 'Taxa combinada', 'Valor devolvido'):
            self.assertIn(part, text)


class RegisterRefundTests(RefundBase):
    def refund(self, amount, **extra):
        args = dict(amount=D(amount), method='transfer', package=self.sold, actor=self.trainer)
        args.update(extra)
        return refunds.register_refund(self.person, **args)

    def test_net_paid_is_payments_minus_refunds(self):
        self.refund('100')
        self.sold = self.person.packages.get()
        self.assertEqual(packages.paid_amount(self.sold), D('400'))
        self.assertEqual(Payment.objects.count(), 2)            # the payments themselves are untouched

    def test_pending_grows_back_when_money_is_returned_on_an_open_package(self):
        self.refund('100')
        row = packages.overview(self.person)['rows'][0]
        self.assertEqual((row['paid'], row['pending']), (D('400'), D('100')))

    def test_voided_refund_stops_counting_but_stays(self):
        refund = self.refund('100')
        refunds.void_refund(refund, 'lançado por engano', self.trainer)
        self.assertEqual(packages.paid_amount(self.person.packages.get()), D('500'))
        refund.refresh_from_db()
        self.assertTrue(refund.is_voided)
        self.assertEqual(refund.voided_reason, 'lançado por engano')
        with self.assertRaises(refunds.RefundError):
            refunds.void_refund(refund, 'de novo', self.trainer)

    def test_void_needs_a_reason(self):
        with self.assertRaises(refunds.RefundError):
            refunds.void_refund(self.refund('10'), '  ', self.trainer)

    def test_cannot_give_back_more_than_the_payment(self):
        with self.assertRaises(refunds.RefundError):
            self.refund('250', payment=self.second)               # the payment was 200
        self.refund('150', payment=self.second)
        with self.assertRaises(refunds.RefundError):
            self.refund('60', payment=self.second)                # 50 left of it

    def test_cannot_give_back_more_than_the_package_received(self):
        self.refund('450')
        with self.assertRaises(refunds.RefundError):
            self.refund('60')

    def test_needs_a_payment_or_a_package_and_a_positive_amount(self):
        with self.assertRaises(refunds.RefundError):
            refunds.register_refund(self.person, amount=D('10'), method='cash')
        with self.assertRaises(refunds.RefundError):
            self.refund('0')

    def test_someone_elses_payment_or_package_is_refused(self):
        other = packages.sell(self.bob.person, make_package('Outro', 4))
        with self.assertRaises(refunds.RefundError):
            self.refund('10', package=other)
        theirs = billing.register_payment(self.bob.person, package=other, amount=D('50'), paid_on=timezone.localdate(), method='pix')
        with self.assertRaises(refunds.RefundError):
            self.refund('10', payment=theirs)

    def test_history_has_no_amounts(self):
        self.refund('123.45')
        text = ' '.join(StudentEvent.objects.filter(person=self.person).values_list('summary', flat=True))
        self.assertIn('Devolução registrada', text)
        self.assertNotIn('123', text)

    def test_reversal_of_a_wrong_payment_counts_too(self):
        self.refund('200', payment=self.second, kind=Refund.Kind.REVERSAL)
        self.assertEqual(packages.paid_amount(self.person.packages.get()), D('300'))


class CancelTests(RefundBase):
    def cancel(self, **extra):
        args = dict(amount=D('330'), method='mbway', fee=D('20'), reason='mudou de cidade')
        args.update(extra)
        return refunds.cancel_with_refund(self.sold, self.trainer, **args)

    def test_cancels_and_gives_money_back_with_the_calculation_kept(self):
        result = self.cancel()
        self.sold.refresh_from_db()
        self.assertIsNotNone(self.sold.cancelled_at)
        self.assertEqual(self.sold.used_at_cancel, 3)
        refund = result['refund']
        self.assertEqual((refund.amount, refund.method, refund.reason, refund.student_package), (D('330'), 'mbway', 'mudou de cidade', self.sold))
        self.assertIn('Aulas dadas: 3 de 10', refund.summary)
        self.assertEqual(packages.paid_amount(self.sold), D('170'))

    def test_classes_given_stay_in_the_history_without_a_package(self):
        self.cancel()
        self.assertEqual(Appointment.objects.filter(person=self.person).count(), 3)
        self.assertEqual(Appointment.objects.filter(person=self.person, package__isnull=False).count(), 0)

    def test_cancel_without_refund(self):
        result = self.cancel(amount=D('0'))
        self.assertIsNone(result['refund'])
        self.assertFalse(Refund.objects.exists())

    def test_refund_above_what_was_paid_cancels_nothing(self):
        with self.assertRaises(refunds.RefundError):
            self.cancel(amount=D('900'))
        self.sold.refresh_from_db()
        self.assertIsNone(self.sold.cancelled_at)

    def test_a_cancelled_package_cannot_be_cancelled_again(self):
        self.cancel()
        with self.assertRaises(refunds.RefundError):
            self.cancel()

    def test_future_classes_leave_the_agenda_and_the_weekly_times_stop(self):
        slot = ScheduleSlot.objects.create(person=self.person, weekday=1, start_time=time(7), package=self.sold)
        future = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=3), slot=slot)
        single = Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=5))
        result = self.cancel(remove_future=True)
        self.assertEqual(result['removed'], 2)
        future.refresh_from_db()
        self.assertEqual((future.status, future.counts_override), ('cancelled', False))
        self.assertFalse(Appointment.objects.filter(pk=single.pk).exists())
        slot.refresh_from_db()
        self.assertFalse(slot.is_active)

    def test_future_classes_stay_when_not_asked(self):
        Appointment.objects.create(person=self.person, starts_at=timezone.now() + timedelta(days=3))
        self.assertEqual(self.cancel()['removed'], 0)
        self.assertEqual(Appointment.objects.filter(person=self.person, status='scheduled').count(), 1)

    def test_awaiting_refund_lists_cancelled_packages_that_still_hold_money(self):
        self.cancel(amount=D('0'))
        rows = refunds.awaiting_refund()
        self.assertEqual([r['package'] for r in rows], [self.sold])
        self.assertEqual(rows[0]['quote']['suggested'], D('330.00'))      # the 20 fee of the helper is remembered
        refunds.register_refund(self.person, amount=D('330'), method='cash', package=self.sold)
        self.assertEqual(refunds.awaiting_refund(), [])

    def test_old_cancellations_are_not_listed(self):
        self.sold.cancelled_at = timezone.now()
        self.sold.save()                                          # cancelled before the feature: classes given unknown
        self.assertEqual(refunds.awaiting_refund(), [])


class EditTests(RefundBase):
    def edit(self, **extra):
        args = dict(name='Pacote 10', sessions=10, valid_days=60, pricing_mode='per_session',
                    price_per_session=D('50'), price_total=None, notes='')
        args.update(extra)
        return refunds.edit_package(self.sold, self.trainer, **args)

    def test_more_classes_extend_the_balance_and_the_total(self):
        self.assertEqual(self.edit(sessions=12), ['sessions', 'price'])
        self.sold.refresh_from_db()
        self.assertEqual((self.sold.sessions, self.sold.price_total), (12, D('600.00')))
        row = packages.overview(self.person)['rows'][0]
        self.assertEqual((row['left'], row['pending']), (9, D('100.00')))

    def test_cannot_go_below_the_classes_already_given(self):
        with self.assertRaises(refunds.RefundError):
            self.edit(sessions=2)
        self.edit(sessions=3)

    def test_deadline_can_be_removed(self):
        self.edit(valid_days=None)
        self.sold.refresh_from_db()
        self.assertIsNone(self.sold.valid_days)
        self.assertIsNone(packages.ends_on(self.sold))

    def test_price_typed_as_a_total(self):
        self.edit(pricing_mode='total', price_per_session=None, price_total=D('450'))
        self.sold.refresh_from_db()
        self.assertEqual((self.sold.price_total, self.sold.price_per_session), (D('450.00'), D('45.00')))

    def test_nothing_changed_writes_nothing(self):
        before = StudentEvent.objects.count()
        self.assertEqual(self.edit(), [])
        self.assertEqual(StudentEvent.objects.count(), before)

    def test_history_names_the_change_but_never_the_price(self):
        self.edit(name='Pacote renovado', sessions=12, price_per_session=D('77.77'))
        text = StudentEvent.objects.filter(person=self.person).order_by('-pk').first().summary
        self.assertIn('Pacote editado', text)
        self.assertIn('aulas 10 → 12', text)
        self.assertNotIn('77', text)

    def test_a_cancelled_package_cannot_be_edited(self):
        packages.cancel_package(self.sold)
        with self.assertRaises(refunds.RefundError):
            self.edit(sessions=11)

    def test_the_catalog_package_is_not_touched(self):
        catalog = self.sold.package
        self.edit(sessions=12)
        catalog.refresh_from_db()
        self.assertEqual(catalog.sessions, 10)


class PendingCountTests(RefundBase):
    def test_bottom_bar_count_follows_the_net_paid(self):
        self.assertEqual(packages.pending_count(), 0)
        refunds.register_refund(self.person, amount=D('100'), method='cash', package=self.sold)
        self.assertEqual(packages.pending_count(), 1)


class ScreenTests(RefundBase):
    def setUp(self):
        super().setUp()
        self.login(self.trainer)
        self.detail = reverse('core:student_detail', kwargs={'pk': self.person.pk})

    def kw(self, **extra):
        return {'pk': self.person.pk, 'package_pk': self.sold.pk, **extra}

    def test_trainer_only(self):
        urls = [reverse('core:student_package_edit', kwargs=self.kw()), reverse('core:student_package_cancel', kwargs=self.kw()),
                reverse('core:refund_create', kwargs={'pk': self.person.pk}), reverse('core:finance_refunds')]
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302, url)
        self.login(self.alice)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_package_card_links_to_edit_and_cancel(self):
        html = self.client.get(self.detail).content.decode()
        self.assertIn('data-package-edit', html)
        self.assertIn('data-package-cancel', html)

    def test_edit_screen_and_save(self):
        url = reverse('core:student_package_edit', kwargs=self.kw())
        html = self.client.get(url).content.decode()
        self.assertIn('data-edit-context', html)
        response = self.client.post(url, {'name': 'Pacote 12', 'sessions': 12, 'valid_days': 60, 'pricing_mode': 'per_session',
                                          'price_per_session': '50', 'notes': ''})
        self.assertRedirects(response, self.detail + '#tab-ficha', fetch_redirect_response=False)
        self.sold.refresh_from_db()
        self.assertEqual((self.sold.name, self.sold.sessions), ('Pacote 12', 12))

    def test_edit_below_the_classes_given_shows_the_error(self):
        url = reverse('core:student_package_edit', kwargs=self.kw())
        response = self.client.post(url, {'name': 'X', 'sessions': 2, 'valid_days': 60, 'pricing_mode': 'per_session',
                                          'price_per_session': '50'})
        self.assertContains(response, 'não pode ter menos de 3 aulas')

    def test_edit_without_deadline(self):
        url = reverse('core:student_package_edit', kwargs=self.kw())
        self.client.post(url, {'name': 'X', 'sessions': 10, 'no_deadline': 'on', 'pricing_mode': 'per_session', 'price_per_session': '50'})
        self.sold.refresh_from_db()
        self.assertIsNone(self.sold.valid_days)

    def test_cancel_screen_shows_the_calculation_and_recalculates_with_a_fee(self):
        url = reverse('core:student_package_cancel', kwargs=self.kw())
        html = self.client.get(url).content.decode()
        self.assertIn('data-quote-suggested', html)
        self.assertIn('350,00', html)
        self.assertIn('330,00', self.client.get(url, {'fee': '20'}).content.decode())

    def test_cancel_posts_and_goes_to_the_refund_summary(self):
        url = reverse('core:student_package_cancel', kwargs=self.kw())
        response = self.client.post(url, {'fee': '20', 'amount': '330', 'method': 'mbway', 'refunded_on': timezone.localdate().isoformat(),
                                          'reason': 'mudou'})
        refund = Refund.objects.get()
        self.assertRedirects(response, reverse('core:refund_detail', kwargs={'pk': refund.pk}))
        self.sold.refresh_from_db()
        self.assertIsNotNone(self.sold.cancelled_at)
        page = self.client.get(reverse('core:refund_detail', kwargs={'pk': refund.pk})).content.decode()
        self.assertIn('data-summary-text', page)
        self.assertIn('Aulas dadas: 3 de 10', page)

    def test_cancel_too_much_is_refused_and_nothing_changes(self):
        url = reverse('core:student_package_cancel', kwargs=self.kw())
        response = self.client.post(url, {'amount': '900', 'method': 'cash', 'refunded_on': timezone.localdate().isoformat()})
        self.assertContains(response, 'Só dá para devolver até')
        self.sold.refresh_from_db()
        self.assertIsNone(self.sold.cancelled_at)

    def test_cancelled_package_redirects_away_from_edit_and_cancel(self):
        packages.cancel_package(self.sold)
        for name in ('student_package_edit', 'student_package_cancel'):
            self.assertRedirects(self.client.get(reverse(f'core:{name}', kwargs=self.kw())), self.detail + '#tab-ficha',
                                 fetch_redirect_response=False)

    def test_refund_form_from_a_payment_prefills_what_is_left(self):
        url = reverse('core:refund_create', kwargs={'pk': self.person.pk})
        response = self.client.get(url, {'payment': self.second.pk})
        self.assertEqual(response.context['form'].initial['amount'], D('200'))
        post = self.client.post(url, {'payment': self.second.pk, 'student_package': self.sold.pk, 'kind': 'refund', 'amount': '50',
                                      'method': 'cash', 'refunded_on': timezone.localdate().isoformat(), 'reason': 'teste'})
        self.assertRedirects(post, self.detail + '#tab-pagamentos', fetch_redirect_response=False)
        self.assertEqual(Refund.objects.get().payment, self.second)

    def test_refund_form_needs_a_payment_or_a_package(self):
        url = reverse('core:refund_create', kwargs={'pk': self.person.pk})
        response = self.client.post(url, {'kind': 'refund', 'amount': '50', 'method': 'cash', 'refunded_on': timezone.localdate().isoformat()})
        self.assertContains(response, 'Escolha o pagamento ou o pacote')
        self.assertFalse(Refund.objects.exists())

    def test_payments_tab_lists_refunds_with_a_negative_amount_and_the_action(self):
        refunds.register_refund(self.person, amount=D('40'), method='cash', package=self.sold, reason='teste')
        html = self.client.get(self.detail).content.decode()
        self.assertIn('data-refund-row', html)
        self.assertIn('data-payment-refund', html)
        self.assertIn('data-refund-new', html)

    def test_finance_tab_lists_and_totals(self):
        refunds.register_refund(self.person, amount=D('40'), method='cash', package=self.sold, reason='teste')
        refunds.register_refund(self.person, amount=D('10'), method='cash', payment=self.first, kind='reversal')
        response = self.client.get(reverse('core:finance_refunds'))
        self.assertEqual((response.context['refunded'], response.context['reversed'], response.context['net']),
                         (D('40'), D('10'), D('450')))
        html = response.content.decode()
        self.assertIn('data-finance-tabs', html)
        self.assertEqual(html.count('data-refund="'), 2)

    def test_finance_tab_filters_by_student_and_month(self):
        refunds.register_refund(self.person, amount=D('40'), method='cash', package=self.sold)
        self.assertEqual(self.client.get(reverse('core:finance_refunds'), {'aluno': 'zzz'}).content.decode().count('data-refund="'), 0)
        past = (timezone.localdate().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        self.assertEqual(self.client.get(reverse('core:finance_refunds'), {'mes': past}).content.decode().count('data-refund="'), 0)

    def test_voiding_from_the_tab(self):
        refund = refunds.register_refund(self.person, amount=D('40'), method='cash', package=self.sold)
        url = reverse('core:refund_void', kwargs={'pk': refund.pk})
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {'reason': ''})
        refund.refresh_from_db()
        self.assertFalse(refund.is_voided)
        self.client.post(url, {'reason': 'engano'})
        refund.refresh_from_db()
        self.assertTrue(refund.is_voided)
        self.assertContains(self.client.get(reverse('core:finance_refunds')), 'anulada')

    def test_awaiting_list_is_shown_with_a_button(self):
        packages.cancel_package(self.sold)
        html = self.client.get(reverse('core:finance_refunds')).content.decode()
        self.assertIn('data-awaiting-refund', html)
        self.assertIn('package=%d' % self.sold.pk, html)

    def test_summary_page_shows_the_net_line(self):
        refunds.register_refund(self.person, amount=D('40'), method='cash', package=self.sold)
        html = self.client.get(reverse('core:finance')).content.decode()
        self.assertIn('data-refund-line', html)
        self.assertIn('data-finance-tabs', html)

    def test_summary_page_without_refunds_has_no_extra_line(self):
        self.assertNotIn('data-refund-line', self.client.get(reverse('core:finance')).content.decode())

    def test_csv_has_the_refund_as_a_negative_row(self):
        refunds.register_refund(self.person, amount=D('40'), method='cash', package=self.sold, reason='teste')
        refund = Refund.objects.get()
        voided = refunds.register_refund(self.person, amount=D('5'), method='cash', package=self.sold)
        refunds.void_refund(voided, 'engano')
        text = self.client.get(reverse('core:finance_export')).content.decode()
        self.assertIn('-40,00', text)
        self.assertNotIn('-5,00', text)
        self.assertEqual(refund.refunded_on, timezone.localdate())


class StudentNeverSeesMoneyTests(RefundBase):
    def test_student_pages_do_not_carry_refund_data(self):
        refunds.register_refund(self.person, amount=D('123.45'), method='cash', package=self.sold, reason='motivo secreto')
        self.login(self.alice)
        for name in ('my_package', 'my_agenda', 'my_privacy', 'my_evolution'):
            html = self.client.get(reverse(f'core:{name}')).content.decode()
            self.assertNotIn('123,45', html, name)
            self.assertNotIn('motivo secreto', html, name)
        self.assertEqual(self.client.get(reverse('core:finance_refunds')).status_code, 403)
        refund = Refund.objects.get()
        self.assertEqual(self.client.get(reverse('core:refund_detail', kwargs={'pk': refund.pk})).status_code, 403)


class ExportTests(RefundBase):
    def test_the_students_own_data_export_lists_the_refunds(self):
        from core.exports import build_data
        refunds.register_refund(self.person, amount=D('40'), method='cash', package=self.sold, reason='teste')
        entries = build_data(self.person, False)['refunds']
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]['kind'], entries[0]['reason']), ('refund', 'teste'))


class KeptFeeTests(RefundBase):
    def test_the_fee_kept_at_cancellation_is_not_listed_as_still_to_give_back(self):
        refunds.cancel_with_refund(self.sold, self.trainer, amount=D('330'), method='cash', fee=D('20'))
        self.sold.refresh_from_db()
        self.assertEqual(self.sold.cancel_fee, D('20'))
        self.assertEqual(refunds.awaiting_refund(), [])

    def test_cancelling_without_giving_anything_back_still_lists_it(self):
        refunds.cancel_with_refund(self.sold, self.trainer, amount=D('0'), fee=D('20'))
        rows = refunds.awaiting_refund()
        self.assertEqual(rows[0]['quote']['suggested'], D('330.00'))


class OverpaidTests(RefundBase):
    """The price of a package already paid is lowered: the difference is money owed back."""

    def lower(self, per='45'):
        return refunds.edit_package(self.sold, self.trainer, name='Pacote 10', sessions=10, valid_days=60,
                                    pricing_mode='per_session', price_per_session=D(per), price_total=None)

    def test_nothing_is_owed_while_the_price_matches_what_was_paid(self):
        self.assertEqual(packages.overpaid_amount(self.sold), D('0'))
        self.assertEqual(refunds.awaiting_refund(), [])

    def test_lowering_the_price_makes_the_difference_owed(self):
        self.lower('45')                                           # 10 x 45 = 450, paid 500
        self.sold.refresh_from_db()
        self.assertEqual(packages.overpaid_amount(self.sold), D('50.00'))
        row = packages.overview(self.person)['rows'][0]
        self.assertEqual((row['overpaid'], row['pending']), (D('50.00'), D('0')))
        rows = refunds.awaiting_refund()
        self.assertEqual([(r['kind'], r['quote']['suggested']) for r in rows], [('overpaid', D('50.00'))])

    def test_giving_it_back_clears_the_row(self):
        self.lower('45')
        refunds.register_refund(self.person, amount=D('50'), method='cash', package=self.sold)
        self.sold.refresh_from_db()
        self.assertEqual(packages.overpaid_amount(self.sold), D('0'))
        self.assertEqual(refunds.awaiting_refund(), [])
        self.assertEqual(packages.overview(self.person)['rows'][0]['pending'], D('0'))

    def test_a_cancelled_package_is_not_listed_as_overpaid(self):
        self.lower('45')
        packages.cancel_package(self.sold)
        self.sold.refresh_from_db()
        self.assertEqual(packages.overpaid_amount(self.sold), D('0'))

    def test_the_edit_screen_warns_about_the_difference(self):
        self.login(self.trainer)
        url = reverse('core:student_package_edit', kwargs={'pk': self.person.pk, 'package_pk': self.sold.pk})
        response = self.client.post(url, {'name': 'Pacote 10', 'sessions': 10, 'valid_days': 60, 'pricing_mode': 'per_session',
                                          'price_per_session': '45'}, follow=True)
        self.assertContains(response, 'a devolver')
        self.assertContains(response, 'data-package-overpaid')

    def test_finance_tab_lists_it_with_a_button_that_prefills_the_form(self):
        self.lower('45')
        self.login(self.trainer)
        html = self.client.get(reverse('core:finance_refunds')).content.decode()
        self.assertIn('data-awaiting-refund', html)
        self.assertIn('pago a mais', html)
        form = self.client.get(reverse('core:refund_create', kwargs={'pk': self.person.pk}), {'package': self.sold.pk})
        self.assertEqual(form.context['form'].initial['amount'], D('50.00'))
        self.assertIn('pago a mais', form.context['form'].initial['reason'])

    def test_refunding_it_goes_to_the_summary_with_the_calculation(self):
        self.lower('45')
        self.login(self.trainer)
        response = self.client.post(reverse('core:refund_create', kwargs={'pk': self.person.pk}), {
            'student_package': self.sold.pk, 'kind': 'refund', 'amount': '50', 'method': 'mbway',
            'refunded_on': timezone.localdate().isoformat(), 'reason': 'ajuste'})
        refund = Refund.objects.get()
        self.assertRedirects(response, reverse('core:refund_detail', kwargs={'pk': refund.pk}))
        self.assertIn('Valor combinado', refund.summary)
        self.assertIn('diferença', refund.summary)

    def test_student_never_sees_it(self):
        self.lower('45')
        self.login(self.alice)
        html = self.client.get(reverse('core:my_package')).content.decode()
        self.assertNotIn('pago a mais', html.lower())
        self.assertNotIn('50,00', html)
