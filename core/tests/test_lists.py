from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from core.listing import filter_tags, remember_view, view_mode
from core.models import Exercise, MuscleGroup, Package, PersonRoutineExercise, Routine

from .base import BaseTestCase, page_text


class ListingHelperTests(SimpleTestCase):
    def request(self, query='', cookies=None):
        request = RequestFactory().get('/x/' + query)
        request.COOKIES.update(cookies or {})
        return request

    def test_view_mode_prefers_the_address_then_the_cookie_then_the_default(self):
        self.assertEqual(view_mode(self.request('?view=list', {'k': 'grid'}), 'k'), 'list')
        self.assertEqual(view_mode(self.request('', {'k': 'list'}), 'k'), 'list')
        self.assertEqual(view_mode(self.request(), 'k'), 'grid')

    def test_an_unknown_mode_falls_back(self):
        self.assertEqual(view_mode(self.request('?view=<script>', {'k': 'x'}), 'k'), 'grid')
        self.assertEqual(view_mode(self.request('?view=table'), 'k', allowed=('grid', 'list', 'table')), 'table')

    def test_the_cookie_is_only_written_when_the_address_chose(self):
        from django.http import HttpResponse
        self.assertIn('k', remember_view(self.request('?view=list'), HttpResponse(), 'k', 'list').cookies)
        self.assertNotIn('k', remember_view(self.request(), HttpResponse(), 'k', 'list').cookies)

    def test_tags_skip_the_filters_that_are_off(self):
        self.assertEqual(filter_tags('abc', group='Peito', status=''), [
            {'key': 'search', 'label': '“abc”'}, {'key': 'group', 'label': 'Peito'}])
        self.assertEqual(filter_tags('', status=''), [])


class ListBase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.trainer)
        self.legs = MuscleGroup.objects.get_or_create(slug='pernas', defaults={'name': 'Pernas'})[0]
        Exercise.objects.create(exercise_name='Agachamento', muscle_group=self.legs)


class ExerciseListTests(ListBase):
    url = property(lambda self: reverse('core:exercise_list'))

    def test_search_filter_and_count(self):
        page = self.client.get(self.url, {'search': 'sup'})
        self.assertEqual([e.exercise_name for e in page.context['exercises']], ['Supino'])
        self.assertContains(page, '<strong>1</strong> exercício encontrado')
        page = self.client.get(self.url, {'muscle_group': self.legs.pk})
        self.assertEqual([e.exercise_name for e in page.context['exercises']], ['Agachamento'])

    def test_chips_and_active_tags(self):
        page = self.client.get(self.url, {'muscle_group': self.legs.pk, 'search': 'agach'})
        html = page_text(page)
        self.assertEqual(html.count('class="ui-chip"'), MuscleGroup.objects.filter(is_active=True).count() + 1)
        self.assertEqual(html.count('class="ui-tag"'), 2)
        self.assertIn('data-active-filters', html)
        self.assertNotIn('data-active-filters', page_text(self.client.get(self.url)))

    def test_a_bad_group_is_ignored(self):
        page = self.client.get(self.url, {'muscle_group': "1' OR '1'='1"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['selected_muscle_group'], '')
        self.assertEqual(len(page.context['exercises']), 3)

    def test_the_layout_toggle_sits_in_the_results_bar_and_is_remembered(self):
        page = self.client.get(self.url)
        html = page_text(page)
        self.assertEqual(html.count('data-view-toggle'), 1)
        self.assertLess(html.index('class="ui-toolbar"'), html.index('data-count'))
        self.assertLess(html.index('data-count'), html.index('data-view-toggle'))
        self.assertNotIn('data-view-toggle', html[html.index('class="ui-toolbar"'):html.index('</form>')])
        self.assertContains(page, 'grid-view')
        listing = self.client.get(self.url, {'view': 'list'})
        self.assertContains(listing, 'list-view')
        self.assertEqual(listing.cookies['exercise_view_mode'].value, 'list')
        self.assertContains(self.client.get(self.url), 'list-view')   # next visit opens the same way

    def test_the_toggle_keeps_the_filters(self):
        page = self.client.get(self.url, {'search': 'sup', 'muscle_group': self.group.pk})
        self.assertContains(page, f'href="?search=sup&amp;muscle_group={self.group.pk}&amp;view=list"')

    def test_empty_states(self):
        self.assertContains(self.client.get(self.url, {'search': 'zzz'}), 'Nenhum exercício com esses filtros')
        PersonRoutineExercise.objects.all().delete()
        Exercise.objects.all().delete()
        self.assertContains(self.client.get(self.url), 'Nenhum exercício cadastrado ainda')

    def test_names_are_escaped(self):
        Exercise.objects.create(exercise_name='<script>alert(1)</script>', muscle_group=self.legs)
        html = page_text(self.client.get(self.url, {'search': 'script'}))
        self.assertNotIn('<script>alert(1)', html)
        page = page_text(self.client.get(self.url, {'search': '<b>x'}))
        self.assertNotIn('<b>x', page)


class RoutineListTests(ListBase):
    url = property(lambda self: reverse('core:routine_list'))

    def setUp(self):
        super().setUp()
        Routine.objects.create(routine_name='Treino B', difficulty_level='avancado', is_active=False)

    def test_filters(self):
        names = lambda **q: [r.routine_name for r in self.client.get(self.url, q).context['routines']]
        self.assertEqual(names(), ['Treino A', 'Treino B'])
        self.assertEqual(names(difficulty='avancado'), ['Treino B'])
        self.assertEqual(names(status='inactive'), ['Treino B'])
        self.assertEqual(names(status='active'), ['Treino A'])
        self.assertEqual(names(status='<x>'), ['Treino A', 'Treino B'])
        self.assertEqual(names(difficulty='nope'), ['Treino A', 'Treino B'])

    def test_same_layout_toggle_as_exercises(self):
        html = page_text(self.client.get(self.url))
        self.assertEqual(html.count('data-view-toggle'), 1)
        self.assertLess(html.index('data-count'), html.index('data-view-toggle'))
        self.assertNotIn('data-view-toggle', html[html.index('class="ui-toolbar"'):html.index('</form>')])

    def test_the_toggle_keeps_the_difficulty(self):
        page = self.client.get(self.url, {'difficulty': 'avancado', 'status': 'inactive'})
        self.assertContains(page, 'difficulty=avancado')
        html = page_text(page)
        toggle = html[html.index('data-view-toggle'):]
        self.assertIn('difficulty=avancado', toggle[:400])

    def test_layout_cookie_and_tags(self):
        page = self.client.get(self.url, {'view': 'list', 'difficulty': 'avancado'})
        self.assertEqual(page.cookies['routine_view_mode'].value, 'list')
        self.assertContains(page, 'data-active-filters')


class PlanListTests(ListBase):
    url = property(lambda self: reverse('core:plan_list'))

    def setUp(self):
        super().setUp()
        Package.objects.create(name='Mensal', sessions=8)
        Package.objects.create(name='Antigo', sessions=4, is_active=False)

    def names(self, **query):
        return [p.name for p in self.client.get(self.url, query).context['plans']]

    def test_active_by_default(self):
        self.assertEqual(self.names(), ['Mensal'])
        self.assertEqual(self.names(status='inactive'), ['Antigo'])
        self.assertEqual(self.names(status='all'), ['Antigo', 'Mensal'])
        self.assertEqual(self.names(status='weird'), ['Mensal'])
        self.assertEqual(self.names(search='ens'), ['Mensal'])

    def test_the_default_is_not_a_tag(self):
        self.assertNotIn('data-active-filters', page_text(self.client.get(self.url)))
        self.assertIn('data-active-filters', page_text(self.client.get(self.url, {'status': 'all'})))

    def test_empty_state(self):
        self.assertContains(self.client.get(self.url, {'search': 'zzz'}), 'Nenhum pacote com esses filtros')


class ToolbarMarkupTests(ListBase):
    def test_every_list_has_the_same_pieces(self):
        for name in ('core:exercise_list', 'core:routine_list', 'core:plan_list'):
            html = page_text(self.client.get(reverse(name)))
            for marker in ('data-list-filters', 'data-list-region', 'class="ui-head"', 'name="search"', 'list-filters.js'):
                self.assertIn(marker, html, f'{name}: {marker}')


class StudentListTests(ListBase):
    url = property(lambda self: reverse('core:student_list'))

    def test_same_toolbar_and_toggle_position(self):
        html = page_text(self.client.get(self.url))
        for marker in ('data-list-filters', 'data-list-region', 'class="ui-head"', 'list-filters.js', 'name="filtro"'):
            self.assertIn(marker, html)
        self.assertEqual(html.count('data-view-toggle'), 1)
        self.assertLess(html.index('data-count'), html.index('data-view-toggle'))
        self.assertNotIn('data-view-toggle', html[html.index('class="ui-toolbar"'):html.index('</form>')])

    def test_chips_cover_every_situation(self):
        html = page_text(self.client.get(self.url))
        from core.views import STUDENT_FILTERS
        self.assertEqual(html.count('name="filtro"'), len(STUDENT_FILTERS) + 1)

    def test_status_and_filter_tags(self):
        page = self.client.get(self.url, {'status': 'active', 'filtro': 'no_app', 'view': 'list'})
        self.assertEqual(len(page.context['tags']), 2)
        self.assertEqual(page.cookies['student_view_mode'].value, 'list')
        self.assertEqual(self.client.get(self.url, {'status': 'weird'}).context['selected_status'], '')

    def test_pagination_link_keeps_filters_and_drops_nothing_else(self):
        for i in range(30):
            self._make_student(f'many{i}', f'Many {i}')
        page = self.client.get(self.url, {'search': 'Many', 'view': 'grid'})
        self.assertContains(page, '?search=Many&amp;view=grid&amp;page=2')


class FinanceAndAgendaTests(ListBase):
    def test_pages_share_the_header_and_period_navigator(self):
        for name in ('core:finance', 'core:finance_refunds'):
            html = page_text(self.client.get(reverse(name)))
            for marker in ('class="ui-head"', 'class="ui-period"', 'data-finance-tabs', 'class="ui-tabs"'):
                self.assertIn(marker, html, f'{name}: {marker}')
        html = page_text(self.client.get(reverse('core:agenda')))
        for marker in ('class="ui-head"', 'class="ui-period"', 'class="ui-seg"', 'data-agenda-views'):
            self.assertIn(marker, html)

    def test_refund_search_is_a_toolbar(self):
        html = page_text(self.client.get(reverse('core:finance_refunds'), {'aluno': 'ali'}))
        self.assertIn('data-list-filters', html)
        self.assertIn('data-search name="aluno" value="ali"', html)
