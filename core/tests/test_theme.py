import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

from .base import BaseTestCase

ROOT = Path(settings.BASE_DIR)

# Neutral colors must come from the theme (Bootstrap variables), or the page breaks in dark mode.
FORBIDDEN_CLASSES = re.compile(r'\b(bg-white|bg-light|table-light)\b')
FORBIDDEN_HEX = re.compile(r'#(?:fff|ffffff|f8f9fa|e9ecef|dee2e6|6c757d|495057|e9f7ef|fff8f0)\b', re.I)
# Places where a fixed color is the point: text on the brand/dark bars, and the standalone offline page
ALLOWED_HEX = {
    ('static/css/tabbar.css', '#fff'),                      # white icon/text on the brand-colored circle, FAB and selected day
    ('templates/pwa/offline.html', '#fff'),
    ('templates/core/agenda.html', '#fff'),                 # white day number on the brand-colored "today" circle                 # standalone page (no base.html), has its own dark rule
}


def _files():
    for folder, patterns in (('templates', ('*.html',)), ('static/css', ('*.css',))):
        for pattern in patterns:
            for path in (ROOT / folder).rglob(pattern):
                if path.name == 'theme.css':
                    continue
                yield path


class NoHardCodedNeutralColorsTests(SimpleTestCase):
    def test_templates_and_css_use_theme_colors(self):
        problems = []
        self.assertGreater(len(list(_files())), 30)   # the scan really sees the templates
        for path in _files():
            relative = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding='utf-8')
            for match in FORBIDDEN_CLASSES.finditer(text):
                problems.append(f'{relative}: classe "{match.group(0)}" (use bg-body / bg-body-tertiary)')
            for match in FORBIDDEN_HEX.finditer(text):
                if (relative, match.group(0).lower()) not in ALLOWED_HEX:
                    problems.append(f'{relative}: cor fixa {match.group(0)} (use var(--bs-...) do tema)')
        self.assertEqual(problems, [], 'Cores fixas quebram o tema escuro:\n' + '\n'.join(problems))

    def test_new_static_files_exist(self):
        for name in ('js/theme.js', 'js/chart-theme.js', 'css/theme.css'):
            self.assertTrue((ROOT / 'static' / name).is_file(), name)


class ThemeMarkupTests(BaseTestCase):
    def head(self, response):
        html = response.content.decode()
        return html[:html.index('</head>')]

    def check_page(self, response):
        head = self.head(response)
        # the theme script runs before any stylesheet, so the page never flashes
        self.assertIn('js/theme.js', head)
        self.assertLess(head.index('js/theme.js'), head.index('bootstrap.min.css'))
        self.assertLess(head.index('bootstrap.min.css'), head.index('css/theme.css'))
        self.assertLess(head.index('css/theme.css'), head.index('css/tabbar.css'))
        self.assertEqual(head.count('name="theme-color"'), 2)
        self.assertIn('(prefers-color-scheme: dark)', head)
        html = response.content.decode()
        self.assertEqual(html.count('data-theme-menu'), 1)
        for choice in ('light', 'dark', 'auto'):
            self.assertIn(f'data-theme-option="{choice}"', html)

    def test_visitor_sees_the_selector_on_login(self):
        self.check_page(self.client.get(reverse('account_login')))

    def test_student_and_trainer_pages(self):
        self.login(self.alice)
        self.check_page(self.client.get(reverse('core:home')))
        self.login(self.trainer)
        for name in ('core:home', 'core:student_list', 'core:agenda'):
            self.check_page(self.client.get(reverse(name)))

    def test_selector_stays_outside_the_collapsed_menu(self):
        """On a phone the hamburger is hidden: the selector must not live inside the collapsible menu."""
        html = self.client.get(reverse('account_login')).content.decode()
        self.assertLess(html.index('data-theme-menu'), html.index('id="navbarNav"'))

    def test_charts_load_the_chart_theme(self):
        self.login(self.trainer)
        response = self.client.get(reverse('core:student_detail', kwargs={'pk': self.alice.person.pk}))
        self.assertContains(response, 'js/chart-theme.js')
        html = response.content.decode()
        self.assertLess(html.index('chart.umd.js'), html.index('js/chart-theme.js'))

    def test_service_worker_cache_version_changed(self):
        self.assertNotIn("'muscle-track-v2'", self.client.get(reverse('core:service_worker')).content.decode())
