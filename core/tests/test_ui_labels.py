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

    def test_trainer_home_and_student_pages_use_it_too(self):
        self.login(self.trainer)
        self.assertIn('class="ui-head"', self.client.get(reverse('core:home')).content.decode())
        self.login(self.alice)
        for name in ('core:my_workouts', 'core:my_history'):
            self.assertIn('class="ui-head"', self.client.get(reverse(name)).content.decode(), name)

    def test_the_language_icon_is_light_like_its_neighbours_on_the_dark_bar(self):
        css = (ROOT / 'static/css/theme.css').read_text()
        self.assertIn('.language-menu .nav-link', css)


class LeftoverScreensTests(BaseTestCase):
    def test_error_pages_have_the_app_look(self):
        self.login(self.trainer)
        missing = self.client.get('/nao-existe-mesmo/')
        self.assertContains(missing, 'data-error-page="404"', status_code=404)
        self.login(self.alice)
        denied = self.client.get(reverse('core:student_list'))
        self.assertContains(denied, 'data-error-page="403"', status_code=403)

    def test_server_error_page_is_standalone(self):
        from django.template.loader import render_to_string
        html = render_to_string('500.html')
        self.assertIn('data-error-page="500"', html)
        self.assertNotIn('csrfmiddlewaretoken', html)

    def test_student_file_and_routine_detail_share_the_header(self):
        self.login(self.trainer)
        file = self.client.get(reverse('core:student_detail', kwargs={'pk': self.alice.person.pk})).content.decode()
        self.assertIn('class="ui-head"', file)
        self.assertIn('class="ui-avatar"', file)
        self.assertIn('data-sheet-link-button', file)
        self.assertIn('data-student-actions', file)
        detail = self.client.get(reverse('core:routine_detail', kwargs={'pk': self.template.pk})).content.decode()
        self.assertIn('class="ui-head inline"', detail)

    def test_the_green_button_is_only_for_whatsapp_approve_and_start(self):
        found = []
        for path in (ROOT / 'templates').rglob('*.html'):
            for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if 'btn-success' in line and 'whatsapp' not in line.lower():
                    found.append((path.name, n))
        allowed = {'workout_execute.html', 'agenda.html', 'my_photo_form.html', 'photo_form.html', '_today_student.html'}
        self.assertEqual([f for f in found if f[0] not in allowed], [])

    def test_intro_consent_privacy_and_summary_use_the_icon_badge(self):
        self.login(self.alice)
        for name in ('core:my_intro', 'core:privacy_policy'):
            self.assertIn('class="ui-ico', self.client.get(reverse(name)).content.decode(), name)


class DashboardLookTests(BaseTestCase):
    def test_the_dashboard_uses_the_shared_pieces(self):
        self.login(self.trainer)
        html = self.client.get(reverse('core:home')).content.decode()
        for marker in ('class="ui-head"', 'ui-kpi', 'ui-sec', 'data-attention'):
            self.assertIn(marker, html)
        # no Bootstrap-colored count badges or outlined chips any more
        self.assertNotIn('badge bg-', html.split('data-attention', 1)[1].split('{% elif', 1)[0])
        self.assertNotIn('text-bg-', html)

    def test_a_workout_without_a_duration_does_not_print_none(self):
        from django.utils import timezone
        from core.models import WorkoutSession
        WorkoutSession.objects.create(person_routine=self.alice_routine, is_completed=True, end_time=timezone.now())
        self.login(self.trainer)
        self.assertNotIn('None min', self.client.get(reverse('core:home')).content.decode())


class StudentScheduleLookTests(BaseTestCase):
    def test_my_agenda_uses_the_shared_pieces(self):
        from datetime import timedelta
        from core.models import Appointment
        Appointment.objects.create(person=self.alice.person, starts_at=timezone_now() + timedelta(days=3), status='scheduled')
        self.login(self.alice)
        html = self.client.get(reverse('core:my_agenda')).content.decode()
        for marker in ('class="ui-head inline"', 'ui-date', 'ui-h2', 'data-my-class'):
            self.assertIn(marker, html)
        self.assertNotIn('class="badge', html)

    def test_the_tone_filter_maps_bootstrap_names(self):
        from core.templatetags.ui import tone
        self.assertEqual([tone(x) for x in ('success', 'warning', 'danger', 'secondary', 'nope')], ['ok', 'warn', 'bad', '', ''])

    def test_native_checks_and_radios_use_the_brand_color(self):
        css = (ROOT / 'static/css/ui.css').read_text()
        self.assertIn('accent-color: var(--primary-color)', css)


def timezone_now():
    from django.utils import timezone
    return timezone.now()
