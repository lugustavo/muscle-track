"""The assessments are paged (five at a time) in the student file and in "Minha evolução"; the chart and the counters
still use all of them."""
import re
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import translation

from core.models import Assessment
from core.templatetags.ui import page_url

from .base import BaseTestCase


class AssessmentPagerTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        # 12 assessments, one a month back from 2026-06-01: the newest is "01/06/2026"
        self.days = [date(2026, 6, 1) - timedelta(days=30 * n) for n in range(12)]
        self.ids = [Assessment.objects.create(person=self.person, date=day, weight_kg=Decimal('70')).pk for day in self.days]
        self.detail = reverse('core:student_detail', kwargs={'pk': self.person.pk})

    def shown(self, page):
        return re.findall(r'data-(?:assessment|my-assessment)="(\d+)"', page)

    def test_the_file_shows_the_five_newest_first(self):
        self.login(self.trainer)
        page = self.client.get(self.detail).content.decode()
        self.assertEqual(len(self.shown(page)), 5)
        self.assertEqual(self.shown(page), [str(pk) for pk in self.ids[:5]])
        self.assertIn('1–5 de 12', page)
        self.assertIn('Página 1 de 3', page)

    def test_the_next_pages_hold_the_older_ones_and_stay_on_the_tab(self):
        self.login(self.trainer)
        first = self.client.get(self.detail).content.decode()
        self.assertIn('?av=2#tab-avaliacoes', first)
        second = self.client.get(self.detail + '?av=2').content.decode()
        self.assertEqual(len(self.shown(second)), 5)
        self.assertEqual(self.shown(second), [str(pk) for pk in self.ids[5:10]])
        self.assertIn('6–10 de 12', second)
        last = self.client.get(self.detail + '?av=3').content.decode()
        self.assertEqual(len(self.shown(last)), 2)
        self.assertNotIn('rel="next"', last)
        self.assertIn('rel="prev"', last)
        # the page before the first one and one past the last fall back to a real page (no error)
        self.assertEqual(self.client.get(self.detail + '?av=99').status_code, 200)
        self.assertEqual(self.client.get(self.detail + '?av=abc').status_code, 200)

    def test_the_chart_and_the_counter_still_use_every_assessment(self):
        self.login(self.trainer)
        response = self.client.get(self.detail + '?av=3')
        self.assertEqual(len(response.context['assessment_chart']), 12)
        self.assertEqual(len(response.context['assessments']), 12)
        self.assertContains(response, '<span class="ui-badge">12</span>')

    def test_a_few_assessments_show_no_pager(self):
        Assessment.objects.filter(pk__in=list(self.person.assessments.values_list('pk', flat=True)[5:])).delete()
        self.login(self.trainer)
        page = self.client.get(self.detail).content.decode()
        self.assertEqual(len(self.shown(page)), 5)
        self.assertNotIn('data-pager', page)

    def test_the_student_pages_her_own_evolution_the_same_way(self):
        self.login(self.alice)
        page = self.client.get(reverse('core:my_evolution')).content.decode()
        self.assertEqual(len(self.shown(page)), 5)
        self.assertIn('?av=2', page)
        self.assertIn('Página 1 de 3', page)
        self.assertEqual(len(self.shown(self.client.get(reverse('core:my_evolution') + '?av=3').content.decode())), 2)


class PageUrlTests(SimpleTestCase):
    def context(self, query=''):
        from django.test import RequestFactory
        return {'request': RequestFactory().get('/x/' + query)}

    def setUp(self):
        translation.activate('pt-br')

    def test_other_filters_are_kept_and_the_first_page_has_no_parameter(self):
        context = self.context('?q=ana&av=4')
        self.assertEqual(page_url(context, 'av', 2, 'tab-avaliacoes'), '?q=ana&av=2#tab-avaliacoes')
        self.assertEqual(page_url(context, 'av', 1, 'tab-avaliacoes'), '?q=ana#tab-avaliacoes')
        self.assertEqual(page_url(self.context(), 'av', 1), '?')
