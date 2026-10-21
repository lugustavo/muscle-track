"""Phone layout of the student file lists and the routine counters: the lists swipe like the agenda tab, the
counters are three to a row, and the file tabs keep their bottom edge."""
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from core.models import Holiday, Payment

from .base import BaseTestCase
from .test_navigation_global import RichDataMixin


def static_text(name):
    return (settings.BASE_DIR / 'static' / name).read_text()


class SwipeListsTests(RichDataMixin, BaseTestCase):
    def setUp(self):
        super().setUp()
        self.build_world()
        self.payment = Payment.objects.create(person=self.person, amount=Decimal('50'), paid_on=timezone.localdate())
        self.login(self.trainer)
        self.page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).content.decode()

    def rows(self, marker):
        """The swipe rows (with their action blocks) of the list that carries `marker`."""
        section = self.page.split(marker, 1)[1].split('</ul>', 1)[0]
        return section.count('data-swipe-row')

    def test_payments_swipe_with_refund_and_delete(self):
        self.assertEqual(self.rows('data-payments'), 1)
        self.assertContains(self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})), 'data-payment-refund')
        self.assertIn(reverse('core:payment_delete', kwargs={'pk': self.payment.pk}), self.page)

    def test_notes_swipe_with_delete(self):
        self.assertEqual(self.rows('data-notes'), 1)
        self.assertIn(reverse('core:note_delete', kwargs={'pk': self.note.pk}), self.page)

    def test_assessments_swipe_with_edit_and_delete_and_keep_one_photo_strip(self):
        self.assertEqual(self.rows('data-assessments'), 1)
        self.assertEqual(self.page.count(f'data-photo-strip="{self.assessment.pk}"'), 1)
        self.assertIn(reverse('core:assessment_edit', kwargs={'pk': self.assessment.pk}), self.page)
        self.assertIn(reverse('core:assessment_delete', kwargs={'pk': self.assessment.pk}), self.page)

    def test_only_the_measures_taken_show_on_a_phone(self):
        # the empty ones carry a class that hides them below md; the weight, taken, does not
        measures = self.page.split('assessment-measures', 1)[1].split('</dl>', 1)[0]
        self.assertRegex(measures, r'<div class="">\s*<dt>Peso</dt>')
        self.assertRegex(measures, r'<div class="is-empty">\s*<dt>Altura</dt>')

    def test_the_swipe_script_and_style_load_once_on_the_file(self):
        self.assertEqual(self.page.count('js/swipe-rows.js'), 1)
        self.assertEqual(self.page.count('css/swipe-rows.css'), 1)
        self.assertIn('Deslize um item para a esquerda', self.page)

    def test_own_holidays_swipe_with_remove(self):
        holiday = Holiday.objects.create(date=timezone.localdate() + timedelta(days=30), name='Dia do estúdio')
        page = self.client.get(reverse('core:agenda_holidays')).content.decode()
        own = page.split('data-own-holidays', 1)[1].split('</ul>', 1)[0]
        self.assertEqual(own.count('data-swipe-row'), 1)
        self.assertIn(reverse('core:agenda_holiday_delete', kwargs={'pk': holiday.pk}), own)
        self.assertIn('js/swipe-rows.js', page)
        self.assertIn('css/swipe-rows.css', page)


class PhoneLayoutTests(BaseTestCase):
    def test_the_file_tabs_do_not_pull_their_bottom_edge_out_of_the_scroller(self):
        css = static_text('css/ui.css')
        self.assertRegex(css, r'\.nav-tabs \.nav-link \{ margin-bottom: 0; \}')

    def test_the_routine_counters_are_three_to_a_row_on_a_phone(self):
        self.login(self.trainer)
        page = self.client.get(reverse('core:routine_detail', kwargs={'pk': self.template.pk})).content.decode()
        stats = page.split('routine-stats', 1)[1].split('<div class="row">', 1)[0]
        self.assertEqual(len(re.findall(r'class="col-4 col-md-2"', stats)), 6)
        self.assertNotIn('class="col-md-2"', stats)
