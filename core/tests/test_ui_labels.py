import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

from .base import BaseTestCase

ROOT = Path(settings.BASE_DIR)
TRANS = re.compile(r'\{% (?:trans|translate) "(?P<t>[^"]+)"')
# Proper nouns and acronyms that keep their capital letter inside a label
KEEP = {'Google', 'WhatsApp', 'IA', 'Telegram', 'YouTube', 'Vimeo', 'LGPD', 'CEP', 'URL', 'ID', 'JPG', 'PNG', 'GIF',
        'MP4', 'Admin', 'Muscle', 'Track', 'Pix', 'PIX', 'Agenda', 'Calendar'}


def title_case(label):
    """True when a label is written in Title Case ("Novo Treino") instead of sentence case ("Novo treino")."""
    if not label[:1].isalpha() or '|' in label or re.search(r'[!.·&%?]|^\d|^Ex', label.replace('...', '')):
        return False
    words = label.split()[1:]
    return any(w.strip('():,;*')[:1].isupper() and w.strip('():,;*') not in KEEP and not w.strip('():,;*').isupper()
               for w in words)


class SentenceCaseTests(SimpleTestCase):
    def test_the_detector(self):
        self.assertTrue(title_case('Novo Treino'))
        self.assertTrue(title_case('Sim, Remover'))
        self.assertFalse(title_case('Novo treino'))
        self.assertFalse(title_case('Gerar com IA'))
        self.assertFalse(title_case('Ver no Google Agenda'))

    def test_templates_use_sentence_case_labels(self):
        found = []
        for path in (ROOT / 'templates').rglob('*.html'):
            if path.name in ('landing.html', 'password_reset.html'):
                continue
            for match in TRANS.finditer(path.read_text(encoding='utf-8')):
                if title_case(match.group('t')):
                    found.append(f'{path.relative_to(ROOT)}: {match.group("t")}')
        self.assertEqual(found, [])


class PageHeaderTests(BaseTestCase):
    def test_trainer_form_and_detail_pages_share_the_header(self):
        self.login(self.trainer)
        pk = self.alice.person.pk
        urls = [reverse('core:exercise_create'), reverse('core:exercise_detail', kwargs={'pk': self.supino.pk}),
                reverse('core:routine_create'), reverse('core:student_create'),
                reverse('core:student_edit', kwargs={'pk': pk}), reverse('core:plan_create'),
                reverse('core:appointment_create'), reverse('core:notification_settings'),
                reverse('core:student_status', kwargs={'pk': pk})]
        for url in urls:
            html = self.client.get(url).content.decode()
            self.assertEqual(html.count('<h1'), 1, url)
            self.assertIn('class="ui-head inline"', html, url)

    def test_form_section_headers_are_not_the_bootstrap_blue(self):
        css = (ROOT / 'static/css/ui.css').read_text()
        self.assertIn('.card-header.bg-primary', css)
