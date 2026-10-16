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
        for label in ('Sessões Concluídas', 'Último Mês', 'Duração Média (min)', 'Taxa de Conclusão'):
            self.assertIn(label, html)
        self.assertNotIn('font-size: 2rem', html)          # sizes live in the stylesheet, not inline

    def test_back_button_is_icon_only_on_phones_but_keeps_its_name(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('btn-back-compact', html)
        self.assertIn('<span class="back-label"> Voltar ao aluno</span>', html)
        self.assertIn('title="Voltar ao aluno"', html)


class BackButtonTagTests(BaseTestCase):
    def render(self, args):
        request = RequestFactory().get('/')
        tpl = Template('{% load navigation %}{% back_button "/x/" ' + args + ' %}')
        return tpl.render(Context({'request': request}))

    def test_default_is_unchanged(self):
        out = self.render('')
        self.assertNotIn('btn-back-compact', out)
        self.assertNotIn('back-label', out)

    def test_compact_needs_an_icon(self):
        self.assertIn('btn-back-compact', self.render('compact=True'))
        self.assertNotIn('btn-back-compact', self.render('compact=True icon=""'))
