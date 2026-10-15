import json
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Exercise, Person, Routine
from core.search import MAX_LENGTH, search_all

from .base import BaseTestCase


class SearchTests(BaseTestCase):
    def setUp(self):
        Person.objects.filter(pk=self.alice.person.pk).update(
            phone='(11) 98765-4321', health_notes='SEGREDO-SAUDE', notes='SEGREDO-NOTA',
            hourly_rate=Decimal('321.45'), plan_price=Decimal('555.55'))
        self.login(self.trainer)

    def groups(self, q, limit=8):
        return {g['key']: g for g in search_all(q, limit)['groups']}

    def test_visitor_goes_to_login_and_student_is_forbidden(self):
        self.client.logout()
        for name in ('core:search', 'core:search_suggest'):
            self.assertEqual(self.client.get(reverse(name), {'q': 'alice'}).status_code, 302)
        self.login(self.alice)
        for name in ('core:search', 'core:search_suggest'):
            self.assertEqual(self.client.get(reverse(name), {'q': 'alice'}).status_code, 403)

    def test_finds_students_by_name_email_phone_and_id(self):
        pk = self.alice.person.pk
        for q in ('alice', 'ALI', 'alice@example', '98765-4321', '11987654321', '5511987654321', '(11) 98765', f'#{pk}'):
            titles = [i['title'] for i in self.groups(q)['students']['items']]
            self.assertEqual(titles, ['Alice'], q)

    def test_finds_exercises_and_routines(self):
        Exercise.objects.filter(pk=self.crucifixo.pk).update(description='abre os braços')
        self.assertEqual([i['title'] for i in self.groups('supin')['exercises']['items']], ['Supino'])
        self.assertEqual([i['title'] for i in self.groups('braços')['exercises']['items']], ['Crucifixo'])
        self.assertEqual([i['title'] for i in self.groups('treino a')['routines']['items']], ['Treino A'])

    def test_inactive_exercises_are_not_listed(self):
        Exercise.objects.filter(pk=self.supino.pk).update(is_active=False)
        self.assertEqual(self.groups('supino')['exercises']['items'], [])

    def test_short_or_empty_query_returns_nothing(self):
        self.assertEqual(search_all('')['groups'], [])
        self.assertEqual(search_all('a')['groups'], [])
        self.assertTrue(search_all('a')['too_short'])
        self.assertFalse(search_all('')['too_short'])

    def test_query_is_cleaned_and_limited(self):
        self.assertEqual(len(search_all('x' * 500)['query']), MAX_LENGTH)
        self.assertEqual(search_all('  ali   ce ')['query'], 'ali ce')

    def test_limit_and_total(self):
        for i in range(12):
            self._make_student(f'aluno{i}', f'Aluno {i}')
        group = self.groups('aluno', limit=5)['students']
        self.assertEqual(len(group['items']), 5)
        self.assertEqual(group['total'], 12)
        self.assertIn('search=aluno', group['more_url'])

    def test_nothing_confidential_is_searched_or_shown(self):
        for secret in ('SEGREDO-SAUDE', 'SEGREDO-NOTA', '321.45', '555.55'):
            self.assertEqual(self.groups(secret)['students']['items'], [], secret)
        page = self.client.get(reverse('core:search'), {'q': 'alice'}).content.decode()
        data = self.client.get(reverse('core:search_suggest'), {'q': 'alice'}).content.decode()
        for text in (page, data):
            for secret in ('SEGREDO', '321.45', '321,45', '555.55', '555,55'):
                self.assertNotIn(secret, text)

    def test_html_in_a_name_is_escaped(self):
        Person.objects.filter(pk=self.bob.person.pk).update(person_name='<script>alert(1)</script> Bob')
        page = self.client.get(reverse('core:search'), {'q': 'Bob'}).content.decode()
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt; Bob', page)

    def test_suggest_json_shape_and_no_cache(self):
        response = self.client.get(reverse('core:search_suggest'), {'q': 'alice'})
        self.assertEqual(response['Cache-Control'], 'no-store')
        data = json.loads(response.content)
        self.assertEqual([g['key'] for g in data['groups']], ['students', 'exercises', 'routines'])
        item = data['groups'][0]['items'][0]
        self.assertEqual(set(item), {'title', 'subtitle', 'badge', 'url'})
        self.assertEqual(item['url'], reverse('core:student_detail', kwargs={'pk': self.alice.person.pk}))
        self.assertEqual(json.loads(self.client.get(reverse('core:search_suggest'), {'q': 'a'}).content)['groups'], [])

    def test_page_states(self):
        url = reverse('core:search')
        self.assertContains(self.client.get(url), 'Digite um nome')
        self.assertContains(self.client.get(url, {'q': 'a'}), 'pelo menos 2 letras')
        self.assertContains(self.client.get(url, {'q': 'zzzzzz'}), 'Nada encontrado')
        response = self.client.get(url, {'q': 'alice'})
        self.assertContains(response, 'Alice')
        self.assertEqual(response.context['found'], 1)

    def test_query_count_does_not_grow_with_results(self):
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse('core:search'), {'q': 'aluno'})
        for i in range(15):
            self._make_student(f'aluno{i}', f'Aluno {i}')
            Routine.objects.create(routine_name=f'Treino aluno {i}')
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse('core:search'), {'q': 'aluno'})
        self.assertLessEqual(len(many), len(few) + 4)   # a "total" count per group that overflows the page

    def test_student_list_search_still_works(self):
        response = self.client.get(reverse('core:student_list'), {'search': '11987654321'})
        self.assertEqual([s.person_name for s in response.context['students']], ['Alice'])


class SearchMarkupTests(BaseTestCase):
    def test_magnifier_and_sheet_only_for_the_trainer(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:home')).content.decode()
        for marker in ('data-search-open', 'id="searchSheet"', 'js/search.js'):
            self.assertIn(marker, html)
        # outside the collapsible menu, so it is there on a phone
        self.assertLess(html.index('data-search-open'), html.index('id="navbarNav"'))
        for user in (self.alice, None):
            if user:
                self.login(user)
            else:
                self.client.logout()
            html = self.client.get(reverse('core:home')).content.decode()
            for marker in ('data-search-open', 'searchSheet', 'js/search.js'):
                self.assertNotIn(marker, html)

    def test_no_bottom_bar_item_is_active_on_search(self):
        self.login(self.trainer)
        tabbar = self.client.get(reverse('core:search'), {'q': 'alice'}).context['tabbar']
        self.assertFalse(any(item['active'] for item in tabbar['items']))
        self.assertFalse(tabbar['more_active'])

    def test_breadcrumb(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:search'), {'q': 'alice'}).content.decode()
        self.assertIn('Busca', html[html.index('breadcrumb'):])
