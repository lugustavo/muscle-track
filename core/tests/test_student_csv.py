"""Students in and out as a CSV file: the export (same filters as the list, nothing private) and the import with a preview."""
import csv
import io
from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import translation

from core import student_csv
from core.models import Person, StudentEvent

from .base import BaseTestCase


def csv_bytes(rows, delimiter=';', encoding='utf-8-sig'):
    out = io.StringIO()
    csv.writer(out, delimiter=delimiter, lineterminator='\r\n').writerows(rows)
    return out.getvalue().encode(encoding)


HEADER = ['nome', 'email', 'telefone', 'nascimento', 'cidade', 'estado', 'situacao', 'modalidade', 'app', 'objetivo']


class SafeCellTests(SimpleTestCase):
    def test_formulas_get_a_quote_and_phones_and_text_stay(self):
        for text in ('=1+1', '@SUM(A1)', '-cmd', '+cmd|x', '\tx'):
            self.assertTrue(student_csv.safe_cell(text).startswith("'"), text)
        for text in ('+55 11 98888-7777', '-12', 'Carla', '', '(11) 9999-0000'):
            self.assertEqual(student_csv.safe_cell(text), text)
        self.assertEqual(student_csv.safe_cell(None), '')


class ParseTests(SimpleTestCase):
    def setUp(self):
        translation.activate('pt-br')      # an earlier test may have left English on

    def test_semicolon_comma_and_tab_are_all_read(self):
        for delimiter in (';', ',', '\t'):
            rows = student_csv.parse(csv_bytes([['nome', 'email'], ['Ana', 'ana@x.com']], delimiter))
            self.assertEqual(rows, [{'nome': 'Ana', 'email': 'ana@x.com'}], repr(delimiter))

    def test_titles_may_be_in_english_or_have_accents_and_case(self):
        rows = student_csv.parse(csv_bytes([['Name', 'E-mail', 'Data de Nascimento', 'UF'], ['Ana', 'a@x.com', '01/02/1990', 'SP']]))
        self.assertEqual(rows[0], {'nome': 'Ana', 'email': 'a@x.com', 'nascimento': '01/02/1990', 'estado': 'SP'})

    def test_a_windows_file_in_cp1252_is_read(self):
        rows = student_csv.parse(csv_bytes([['nome'], ['João Lima']], encoding='cp1252'))
        self.assertEqual(rows[0]['nome'], 'João Lima')

    def test_blank_lines_are_skipped_and_short_lines_are_padded(self):
        rows = student_csv.parse(b'nome;email;telefone\r\n\r\nAna\r\n;;\r\nBia;b@x.com\r\n')
        self.assertEqual(rows, [{'nome': 'Ana', 'email': '', 'telefone': ''}, {'nome': 'Bia', 'email': 'b@x.com', 'telefone': ''}])

    def test_a_file_that_cannot_be_used_says_why(self):
        for data, part in ((b'', 'vazio'), (b'email;telefone\r\na;b\r\n', 'coluna "nome"'), (b'nome;email\r\n', 'nenhum aluno'),
                           (b'x' * (student_csv.MAX_BYTES + 1), '1 MB')):
            with self.assertRaises(student_csv.CsvError) as caught:
                student_csv.parse(data)
            self.assertIn(part, str(caught.exception))

    def test_too_many_rows_are_refused(self):
        rows = [['nome']] + [[f'Aluno {i}'] for i in range(student_csv.MAX_ROWS + 1)]
        with self.assertRaises(student_csv.CsvError) as caught:
            student_csv.parse(csv_bytes(rows))
        self.assertIn('mais de 500', str(caught.exception))

    def test_dates(self):
        self.assertEqual(student_csv.parse_date('15/03/1990'), '1990-03-15')
        self.assertEqual(student_csv.parse_date('15-03-1990'), '1990-03-15')
        self.assertEqual(student_csv.parse_date('1990-03-15'), '1990-03-15')
        self.assertEqual(student_csv.parse_date(''), '')
        self.assertIsNone(student_csv.parse_date('31/02/1990'))
        self.assertIsNone(student_csv.parse_date('ontem'))


class CheckTests(BaseTestCase):
    def check(self, *rows, accept=()):
        return student_csv.check([dict(zip(HEADER, r)) for r in rows], accept)

    def test_a_complete_row_is_ready_with_the_right_data(self):
        [r] = self.check(['Carla Souza', 'carla@x.com', '11 98888-7777', '15/03/1990', 'São Paulo', 'sp', 'experimental',
                          'online', 'sim', 'Hipertrofia'])
        self.assertEqual((r['state'], r['messages']), (student_csv.READY, []))
        data = r['form'].cleaned_data
        self.assertEqual((data['person_name'], data['email'], data['birth_date'], data['state'], data['status'], data['modality'],
                          data['has_app']), ('Carla Souza', 'carla@x.com', date(1990, 3, 15), 'SP', 'trial', 'online', True))

    def test_app_defaults_to_yes_with_an_email_and_no_without(self):
        [with_email, without] = self.check(['A Nova', 'a@x.com', '', '', '', '', '', '', '', ''],
                                           ['B Nova', '', '11 97777-0000', '', '', '', '', '', '', ''])
        self.assertTrue(with_email['form'].cleaned_data['has_app'])
        self.assertFalse(without['form'].cleaned_data['has_app'])
        self.assertEqual((with_email['state'], without['state']), (student_csv.READY, student_csv.READY))

    def test_app_yes_without_an_email_is_an_error(self):
        [r] = self.check(['Sem Email', '', '', '', '', '', '', '', 'sim', ''])
        self.assertEqual(r['state'], student_csv.ERROR)
        self.assertTrue(any('e-mail' in m for m in r['messages']))

    def test_what_is_wrong_is_said_row_by_row(self):
        results = self.check(['', '', '', '', '', '', '', '', '', ''],
                             ['Data Ruim', '', '', '31/02/1990', '', '', '', '', '', ''],
                             ['Pausado', '', '', '', '', '', 'pausado', '', '', ''],
                             ['Modo', '', '', '', '', '', '', 'galaxia', '', ''],
                             ['App', '', '', '', '', '', '', '', 'talvez', ''],
                             ['Futuro', '', '', '01/01/2999', '', '', '', '', '', ''])
        self.assertEqual([r['state'] for r in results], [student_csv.ERROR] * 6)
        text = [' '.join(r['messages']) for r in results]
        self.assertIn('não é uma data', text[1])
        self.assertIn('ativos ou experimentais', text[2])
        self.assertIn('Modalidade', text[3])
        self.assertIn('nem não', text[4])

    def test_an_email_already_in_the_system_is_an_error(self):
        [r] = self.check(['Outra', self.alice.email, '', '', '', '', '', '', '', ''])
        self.assertEqual(r['state'], student_csv.ERROR)

    def test_the_same_phone_or_name_as_an_existing_student_is_a_possible_duplicate_until_accepted(self):
        name = self.alice.person.person_name
        [r] = self.check([name, '', '', '', '', '', '', '', '', ''])
        self.assertEqual(r['state'], student_csv.DUPLICATE)
        self.assertIn('mesmo nome', r['duplicates'][0])
        self.assertEqual(r['messages'], [])
        [accepted] = self.check([name, '', '', '', '', '', '', '', '', ''], accept={0})
        self.assertEqual(accepted['state'], student_csv.READY)

    def test_a_row_repeating_an_earlier_one_of_the_file_is_an_error(self):
        first, second, third = self.check(['Dupla Nova', 'd@x.com', '11 95555-1111', '', '', '', '', '', '', ''],
                                          ['Outro Nome', 'd@x.com', '', '', '', '', '', '', '', ''],
                                          ['Dupla Nova', '', '', '', '', '', '', '', '', ''])
        self.assertEqual(first['state'], student_csv.READY)
        self.assertEqual((second['state'], third['state']), (student_csv.ERROR, student_csv.ERROR))
        self.assertIn('Repete a linha 2', second['messages'][0])


class CreateTests(BaseTestCase):
    def build(self, *rows, accept=()):
        return student_csv.check([dict(zip(HEADER, r)) for r in rows], accept)

    def test_it_creates_the_students_like_the_registration_form_does(self):
        before = Person.objects.count()
        results = self.build(['Nova Com App', 'nova@x.com', '11 94444-0000', '', '', '', 'ativo', '', 'sim', ''],
                             ['Nova Sem App', '', '11 93333-0000', '', '', '', '', '', 'não', ''])
        created = student_csv.create(results, self.trainer)
        self.assertEqual(Person.objects.count(), before + 2)
        with_app, without = created
        self.assertTrue(with_app.has_app and not with_app.user.has_usable_password())
        self.assertEqual(with_app.user.email, 'nova@x.com')
        self.assertFalse(without.has_app)
        self.assertFalse(without.user.is_active)
        event = StudentEvent.objects.get(person=with_app, kind=StudentEvent.Kind.CREATED)
        self.assertIn('importado', event.summary)
        self.assertEqual(event.actor, self.trainer)

    def test_only_what_is_ready_and_chosen_is_created(self):
        results = self.build(['Pronta', '', '', '', '', '', '', '', '', ''], ['', '', '', '', '', '', '', '', '', ''],
                             [self.alice.person.person_name, '', '', '', '', '', '', '', '', ''])
        self.assertEqual([p.person_name for p in student_csv.create(results, self.trainer)], ['Pronta'])
        results = self.build(['Segunda', '', '', '', '', '', '', '', '', ''], ['Terceira', '', '', '', '', '', '', '', '', ''])
        self.assertEqual([p.person_name for p in student_csv.create(results, self.trainer, only={1})], ['Terceira'])


class ExportTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        Person.objects.filter(pk=self.alice.person.pk).update(
            phone='+5511988887777', birth_date=date(1990, 3, 15), city='Santos', goal='=cmd|calc', health_notes='SEGREDO-SAUDE',
            notes='SEGREDO-NOTA', emergency_contact_name='SEGREDO-CONTATO')

    def get(self, **params):
        return self.client.get(reverse('core:students_csv_export'), params)

    def rows(self, response):
        text = response.content.decode('utf-8-sig')
        return list(csv.reader(io.StringIO(text), delimiter=';'))

    def test_it_is_a_csv_download_with_a_bom_and_the_header(self):
        response = self.get()
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="alunos-', response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertTrue(response.content.startswith('﻿'.encode()))
        self.assertEqual(self.rows(response)[0], list(student_csv.COLUMNS))

    def test_each_student_is_a_row_with_their_data(self):
        rows = {r[0]: r for r in self.rows(self.get())[1:]}
        alice = rows[self.alice.person.person_name]
        self.assertEqual((alice[2], alice[3], alice[4]), ('+5511988887777', '15/03/1990', 'Santos'))
        self.assertEqual(len(rows), Person.objects.count())

    def test_nothing_private_is_in_the_file(self):
        body = self.get().content.decode()
        for secret in ('SEGREDO-SAUDE', 'SEGREDO-NOTA', 'SEGREDO-CONTATO'):
            self.assertNotIn(secret, body)

    def test_a_formula_in_a_cell_is_neutralised(self):
        alice = next(r for r in self.rows(self.get())[1:] if r[0] == self.alice.person.person_name)
        self.assertEqual(alice[9], "'=cmd|calc")

    def test_the_search_and_the_filters_of_the_list_apply(self):
        only = self.rows(self.get(search=self.alice.person.person_name))[1:]
        self.assertEqual([r[0] for r in only], [self.alice.person.person_name])
        Person.objects.filter(pk=self.bob.person.pk).update(status='trial')
        trial = self.rows(self.get(filtro='trial'))[1:]
        self.assertEqual([r[0] for r in trial], [self.bob.person.person_name])

    def test_only_the_trainer_can_export(self):
        self.client.logout()
        self.assertEqual(self.get().status_code, 302)
        self.login(self.alice)
        self.assertEqual(self.get().status_code, 403)

    def test_the_list_offers_both_actions_with_the_current_filters(self):
        page = self.client.get(reverse('core:student_list'), {'filtro': 'trial'}).content.decode()
        self.assertIn('data-csv-import', page)
        self.assertIn('export.csv?filtro=trial', page)

    def test_the_round_trip_reads_its_own_export(self):
        rows = student_csv.parse(self.get().content)
        self.assertEqual(len(rows), Person.objects.count())
        self.assertIn('nome', rows[0])


class ImportViewTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.url = reverse('core:students_csv_import')
        self.confirm = reverse('core:students_csv_confirm')

    def upload(self, rows):
        return self.client.post(self.url, {'file': SimpleUploadedFile('alunos.csv', csv_bytes([HEADER] + rows), 'text/csv')})

    def test_the_page_offers_the_template_and_explains_the_columns(self):
        page = self.client.get(self.url).content.decode()
        self.assertIn('data-csv-upload', page)
        self.assertIn(reverse('core:students_csv_template'), page)
        self.assertIn('<code>nascimento</code>', page)

    def test_the_template_is_a_valid_file_of_its_own(self):
        response = self.client.get(reverse('core:students_csv_template'))
        rows = student_csv.parse(response.content)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['state'] != student_csv.ERROR for r in student_csv.check(rows)))

    def test_uploading_shows_the_preview_and_creates_nothing(self):
        before = Person.objects.count()
        response = self.upload([['Nova Pessoa', 'np@x.com', '', '', '', '', '', '', '', ''],
                               [self.alice.person.person_name, '', '', '', '', '', '', '', '', ''],
                               ['', 'sem-nome@x.com', '', '', '', '', '', '', '', '']])
        page = response.content.decode()
        self.assertEqual(Person.objects.count(), before)
        self.assertIn('data-csv-preview', page)
        self.assertEqual([r['state'] for r in response.context['results']],
                         [student_csv.READY, student_csv.DUPLICATE, student_csv.ERROR])
        self.assertIn('Já existe', page)

    def test_confirming_creates_the_ticked_rows_only(self):
        self.upload([['Primeira', '', '', '', '', '', '', '', '', ''], ['Segunda', '', '', '', '', '', '', '', '', ''],
                     ['Terceira', 'ter@x.com', '', '', '', '', '', '', '', '']])
        response = self.client.post(self.confirm, {'row': ['0', '2']})
        self.assertEqual(sorted(Person.objects.filter(person_name__in=['Primeira', 'Segunda', 'Terceira']).values_list('person_name', flat=True)),
                         ['Primeira', 'Terceira'])
        self.assertRedirects(response, reverse('core:student_list') + '?filtro=invite_pending', fetch_redirect_response=False)
        follow = self.client.get(response['Location'])
        self.assertContains(follow, '2 alunos importados')
        self.assertContains(follow, 'convite')

    def test_a_possible_duplicate_enters_only_when_ticked(self):
        name = self.alice.person.person_name
        self.upload([[name, '', '', '', '', '', '', '', '', '']])
        self.client.post(self.confirm, {})
        self.assertEqual(Person.objects.filter(person_name=name).count(), 1)
        self.upload([[name, '', '', '', '', '', '', '', '', '']])
        self.client.post(self.confirm, {'row': ['0']})
        self.assertEqual(Person.objects.filter(person_name=name).count(), 2)

    def test_a_row_with_an_error_cannot_be_forced_in(self):
        self.upload([['Sem Email', '', '', '', '', '', '', '', 'sim', '']])
        self.client.post(self.confirm, {'row': ['0']})
        self.assertFalse(Person.objects.filter(person_name='Sem Email').exists())

    def test_the_state_is_checked_again_at_the_confirmation(self):
        self.upload([['Corrida', 'corrida@x.com', '', '', '', '', '', '', '', '']])
        User.objects.create_user('corrida@x.com', 'corrida@x.com')       # someone took the e-mail in the meantime
        self.client.post(self.confirm, {'row': ['0']})
        self.assertFalse(Person.objects.filter(person_name='Corrida').exists())

    def test_without_a_file_or_with_a_bad_one_nothing_happens(self):
        self.assertContains(self.client.post(self.url, {}), 'Escolha o arquivo CSV')
        bad = self.client.post(self.url, {'file': SimpleUploadedFile('x.csv', b'email;telefone\r\n1;2\r\n')})
        self.assertContains(bad, 'Não achei a coluna')

    def test_confirming_without_a_file_in_the_session_asks_to_send_it_again(self):
        response = self.client.post(self.confirm, {'row': ['0']}, follow=True)
        self.assertContains(response, 'expirou')

    def test_confirm_is_post_only_and_the_trainer_only(self):
        self.assertEqual(self.client.get(self.confirm).status_code, 405)
        self.login(self.alice)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.confirm, {}).status_code, 403)
