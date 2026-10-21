"""Phone layout of the student file lists and the routine counters: the lists swipe like the agenda tab, the
counters are three to a row, and the file tabs keep their bottom edge."""
import json
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

    def test_assessments_have_a_menu_not_a_swipe_and_keep_one_photo_strip(self):
        # a tall card (measures and photos) gets a menu in its header: swiping it made two giant buttons
        section = self.page.split('data-assessments', 1)[1].split('</ul>\n<p class="text-muted small mt-2">', 1)[0]
        self.assertEqual(section.count('data-assessment-menu'), 1)
        self.assertNotIn('data-swipe-row', section)
        self.assertNotIn('swipe-actions', section)
        self.assertEqual(self.page.count(f'data-photo-strip="{self.assessment.pk}"'), 1)
        self.assertIn(reverse('core:assessment_edit', kwargs={'pk': self.assessment.pk}), section)
        self.assertIn(reverse('core:assessment_delete', kwargs={'pk': self.assessment.pk}), section)
        self.assertIn('data-bs-toggle="dropdown"', section)
        self.assertIn('60,00 kg', section)                      # the header carries the weight

    def test_the_menu_becomes_inline_icons_from_md_up(self):
        css = static_text('css/ui.css')
        self.assertIn('.row-menu .dropdown-menu { display: flex; position: static;', css)

    def test_swipe_buttons_never_grow_into_slabs_on_a_tall_row(self):
        css = static_text('css/swipe-rows.css')
        self.assertRegex(css, r'max-height: 6rem;\s+align-self: center')

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


class RoutineFormListTests(BaseTestCase):
    """The exercise lists of the two routine forms: swipe for the actions, and (student routine) drag to reorder."""
    def setUp(self):
        super().setUp()
        self.login(self.trainer)

    def test_the_swipe_script_works_on_rows_added_later(self):
        # the forms draw their rows with JavaScript: the script listens on the document instead of binding rows at load
        script = static_text('js/swipe-rows.js')
        self.assertIn('capture: true', script)
        self.assertNotIn('if (!rows.length) return', script)

    def test_routine_template_rows_swipe_and_keep_the_drag_handle(self):
        page = self.client.get(reverse('core:routine_edit', kwargs={'pk': self.template.pk})).content.decode()
        self.assertIn('css/swipe-rows.css', page)
        self.assertIn('js/swipe-rows.js', page)
        self.assertIn('swipe-row', page)
        self.assertIn("handle: '.drag-handle'", page)
        self.assertIn('id="swipeHint"', page)
        self.assertIn('escapeHtml(exercise.name)', page)           # a name typed by the trainer never goes in as HTML

    def test_student_routine_rows_reorder_and_swipe(self):
        row = self.alice_routine.exercises.first()
        row.notes = 'cuidado com o ombro'
        row.save()
        page = self.client.get(reverse('core:person_routine_edit', kwargs={'pk': self.alice_routine.pk})).content.decode()
        self.assertIn('vendor/sortablejs/1.15.0/Sortable.min.js', page)
        self.assertIn('js/swipe-rows.js', page)
        self.assertIn("handle: '.drag-handle'", page)
        self.assertIn('Arraste pela alça para reordenar', page)
        self.assertIn('data-remove-exercise', page)
        self.assertIn('escapeHtml(ex.name)', page)
        # the notes of each exercise travel with the list, so saving after a reorder does not wipe them
        self.assertIn('notes: "cuidado com o ombro"', page)
        # no server-drawn rows with a dead remove button any more
        self.assertNotIn('remove-exercise">', page)

    def test_a_new_student_routine_starts_empty_and_draws_nothing_by_itself(self):
        page = self.client.get(reverse('core:person_routine_create')).content.decode()
        self.assertIn('id="exercisesList"', page)
        self.assertIn('id="noExercises"', page)


class RoutineTemplateSaveContractTests(BaseTestCase):
    """What the template form's JavaScript sends is what the server reads (the page once sent `exercise_id` and
    `rest_seconds` while the view read `id` and `rest`: every save, reorder included, was refused)."""
    def setUp(self):
        super().setUp()
        self.login(self.trainer)

    def js_keys(self, page):
        body = re.search(r'selectedExercises\.map\(ex => \(\{(.*?)\}\)\)', page, re.S).group(1)
        return re.findall(r'(\w+):', body)

    def test_the_submit_script_sends_the_keys_the_view_reads(self):
        page = self.client.get(reverse('core:routine_edit', kwargs={'pk': self.template.pk})).content.decode()
        keys = self.js_keys(page)
        self.assertEqual(sorted(keys), ['id', 'reps', 'rest', 'sets'])

    def test_a_payload_built_from_those_keys_saves_the_new_order_and_values(self):
        page = self.client.get(reverse('core:routine_edit', kwargs={'pk': self.template.pk})).content.decode()
        values = {'id': None, 'sets': 5, 'reps': 7, 'rest': 75}
        rows = [dict(values, id=self.crucifixo.pk), dict(values, id=self.supino.pk)]
        payload = [{key: row[key] for key in self.js_keys(page)} for row in rows]     # reordered: crucifixo first
        response = self.client.post(reverse('core:routine_edit', kwargs={'pk': self.template.pk}), {
            'routine_name': 'Treino A', 'difficulty_level': 'intermediario', 'estimated_duration': 45,
            'is_active': 'on', 'exercises_data': json.dumps(payload)})
        self.assertRedirects(response, reverse('core:routine_detail', kwargs={'pk': self.template.pk}), fetch_redirect_response=False)
        self.assertEqual(
            list(self.template.exercises.order_by('order').values_list('exercise_id', 'order', 'sets', 'reps', 'rest_seconds')),
            [(self.crucifixo.pk, 1, 5, 7, 75), (self.supino.pk, 2, 5, 7, 75)])
