import re

from django.urls import reverse

from .base import BaseTestCase


def section_open(html, name):
    """True when the collapsible section starts with the `show` class (open on page load)."""
    match = re.search(r'<div class="collapse section-collapse([^"]*)" id="sec-%s"' % name, html)
    assert match, f'section {name} not found'
    return 'show' in match.group(1).split()


def toggle_expanded(html, name):
    match = re.search(r'data-bs-target="#sec-%s"[^>]*aria-expanded="(\w+)"' % name, html)
    assert match, name
    return match.group(1) == 'true'


class StudentFormSectionsTests(BaseTestCase):
    SECTIONS = ('foto', 'pessoais', 'endereco', 'ficha', 'notas')

    def setUp(self):
        self.login(self.trainer)
        self.create_url = reverse('core:student_create')

    def valid_post(self, **extra):
        data = {'person_name': 'Nova Aluna', 'email': 'nova@example.com', 'status': 'active'}
        data.update(extra)
        return data

    def test_new_student_form_has_five_sections_and_only_the_first_is_open(self):
        html = self.client.get(self.create_url).content.decode()
        self.assertEqual(html.count('class="card mb-4 border-0 shadow-sm form-section"'), 5)
        for name in self.SECTIONS:
            self.assertEqual(section_open(html, name), name == 'pessoais', name)
            self.assertEqual(toggle_expanded(html, name), name == 'pessoais', name)

    def test_sections_with_errors_start_open(self):
        html = self.client.get(self.create_url).content.decode()
        self.assertFalse(section_open(html, 'endereco'))
        response = self.client.post(self.create_url, self.valid_post(zip_code='123'))
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(section_open(html, 'endereco'))
        self.assertTrue(toggle_expanded(html, 'endereco'))
        self.assertFalse(section_open(html, 'ficha'))

        response = self.client.post(self.create_url, self.valid_post(plan_price='-5'))
        self.assertTrue(section_open(response.content.decode(), 'ficha'))
        response = self.client.post(self.create_url, self.valid_post(emergency_contact_phone='12'))
        self.assertTrue(section_open(response.content.decode(), 'ficha'))

    def test_valid_post_still_creates_the_student(self):
        response = self.client.post(self.create_url, self.valid_post(phone='11987654321', zip_code='01310100', state='sp'))
        self.assertEqual(response.status_code, 302)

    def test_edit_form_uses_the_same_sections(self):
        pk = self.alice.person.pk
        html = self.client.get(reverse('core:student_edit', kwargs={'pk': pk})).content.decode()
        self.assertEqual(html.count('class="card mb-4 border-0 shadow-sm form-section"'), 5)
        self.assertTrue(section_open(html, 'pessoais'))
        self.assertFalse(section_open(html, 'notas'))

    def test_masks_and_keyboard_hints(self):
        html = self.client.get(self.create_url).content.decode()
        for mask in ('phone', 'cep', 'state'):
            self.assertIn(f'data-mask="{mask}"', html)
        self.assertEqual(html.count('data-mask="phone"'), 2)   # phone and emergency phone
        for hint in ('inputmode="tel"', 'inputmode="numeric"', 'inputmode="email"', 'autocapitalize="characters"',
                     'inputmode="decimal"'):
            self.assertIn(hint, html)
        self.assertNotIn('autocomplete="on"', html)

    def test_draft_never_stores_confidential_fields(self):
        html = self.client.get(self.create_url).content.decode()
        self.assertIn('<form method="post" enctype="multipart/form-data" data-draft', html)
        for name in ('health_notes', 'notes', 'plan_price', 'hourly_rate'):
            tag = re.search(r'<(?:input|textarea)[^>]*name="%s"[^>]*>' % name, html)
            self.assertIsNotNone(tag, name)
            self.assertIn('data-no-draft', tag.group(0), name)
        for name in ('person_name', 'email', 'phone'):
            tag = re.search(r'<input[^>]*name="%s"[^>]*>' % name, html)
            self.assertNotIn('data-no-draft', tag.group(0), name)

    def test_save_bar_and_script(self):
        html = self.client.get(self.create_url).content.decode()
        self.assertIn('class="form-actions d-flex gap-2 mb-4"', html)
        self.assertIn('js/forms.js', html)
        self.assertEqual(html.count('Salvar Aluno'), 1)
