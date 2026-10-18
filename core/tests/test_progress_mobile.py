from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse

from .base import BaseTestCase


class ProgressPageMobileTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.url = reverse('core:person_routine_detail', kwargs={'pk': self.alice_routine.pk})

    def test_stats_are_compact_tiles_in_a_two_column_grid(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('row-cols-2 row-cols-md-4', html)
        self.assertEqual(html.count('metric-tile'), 4)
        for label in ('Sessões concluídas', 'Último mês', 'Duração média (min)', 'Taxa de conclusão'):
            self.assertIn(label, html)
        self.assertNotIn('font-size: 2rem', html)          # sizes live in the stylesheet, not inline

    def test_back_button_is_icon_only_on_phones_but_keeps_its_name(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('btn-back-compact', html)
        self.assertIn('<span class="back-label"> Voltar ao aluno</span>', html)
        self.assertIn('title="Voltar ao aluno"', html)


class SessionDetailMobileTests(BaseTestCase):
    def setUp(self):
        from core.models import WorkoutSession
        session = WorkoutSession.objects.create(person_routine=self.alice_routine)
        session.end_session()
        self.url = reverse('core:session_detail', kwargs={'pk': session.pk})
        self.login(self.trainer)

    def test_back_button_is_icon_only_on_phones_but_keeps_its_name(self):
        html = self.client.get(self.url, {'next': '/'}).content.decode()
        self.assertIn('btn-back-compact', html)
        self.assertIn('<span class="back-label"> Voltar ao painel</span>', html)
        self.assertIn('title="Voltar ao painel"', html)

    def test_student_sees_it_too(self):
        self.login(self.alice)
        html = self.client.get(self.url).content.decode()
        self.assertIn('<span class="back-label"> Voltar ao histórico</span>', html)

    def test_titles_shrink_on_phones(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('page-title-lg', html)
        self.assertEqual(html.count('card-title-lg'), 2)


class BackButtonTagTests(BaseTestCase):
    def render(self, args):
        request = RequestFactory().get('/')
        tpl = Template('{% load navigation %}{% back_button "/x/" ' + args + ' %}')
        return tpl.render(Context({'request': request}))

    def test_derived_voltar_label_is_icon_only_on_phones(self):
        out = self.render('')
        self.assertIn('btn-back-compact', out)
        self.assertIn('class="back-label"', out)

    def test_explicit_label_such_as_cancelar_keeps_its_text(self):
        out = self.render('label="Cancelar" icon="bi-x-circle"')
        self.assertNotIn('btn-back-compact', out)
        self.assertNotIn('back-label', out)
        self.assertIn('compact', self.render('label="Cancelar" compact=True'))

    def test_compact_needs_an_icon(self):
        self.assertIn('btn-back-compact', self.render('compact=True'))
        self.assertNotIn('btn-back-compact', self.render('compact=True icon=""'))


class DropdownStackingTests(BaseTestCase):
    """The student file's "more actions" menu must open above the sticky tabs on phones."""

    def test_dropdown_menu_is_above_sticky_tabs_and_below_the_tabbar(self):
        import re
        from pathlib import Path
        css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'tabbar.css').read_text()
        z = lambda selector: int(re.search(re.escape(selector) + r'\s*\{[^}]*?z-index:\s*(\d+)', css).group(1))
        sticky = int(re.search(r'\.tabs-sticky\s*\{[^}]*?z-index:\s*(\d+)', css).group(1))
        menu = z('.dropdown-menu')
        self.assertGreater(menu, sticky)
        self.assertLess(menu, z('.fab'))


class NoHandWrittenBackLinksTests(BaseTestCase):
    """Every "Voltar" arrow button goes through _back_button.html (icon-only on phones)."""

    def test_no_template_hand_writes_a_back_arrow_button(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / 'templates'
        allowed = {'_back_button.html', 'logout.html'}   # logout: a "Cancelar" form button
        offenders = [str(f.relative_to(root)) for f in root.rglob('*.html')
                     if f.name not in allowed and 'bi-arrow-left"></i>' in f.read_text()]
        self.assertEqual(offenders, [])
