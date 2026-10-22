"""Students in and out as a CSV file (opens in Excel and Google Sheets).

Export: the list as the trainer sees it (the same search and filters), one student per row. It leaves out what is private
(health notes, the trainer's internal notes, the emergency contact and the commercial terms).

Import: a file with a header row. Each row goes through the same checks as the "Cadastrar aluno" form (PersonForm), the
trainer sees a preview with what is ready, what looks like a duplicate and what has an error, and only then are the
students created. Nothing is sent to anyone: a student with an e-mail gets an account waiting for the invite link, as when
registered by hand.

The file is read as text only: limited in size and in number of rows, and a cell that a spreadsheet would run as a
formula (=, @, + or - followed by something that is not a number) gets a ' in front when exported."""
import csv
import io
import re
import unicodedata
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import app_access
from .forms import PersonForm
from .history import log_event
from .models import Person, StudentEvent

MAX_BYTES = 1024 * 1024
MAX_ROWS = 500
DELIMITER = ';'            # what a pt-BR Excel opens in columns; the import also reads , and tab

COLUMNS = ('nome', 'email', 'telefone', 'nascimento', 'cidade', 'estado', 'situacao', 'modalidade', 'app', 'objetivo',
           'cadastrado_em')
IMPORT_COLUMNS = ('nome', 'email', 'telefone', 'nascimento', 'cidade', 'estado', 'situacao', 'modalidade', 'app', 'objetivo')

ALIASES = {
    'nome': ('nome', 'name', 'aluno', 'nome completo', 'person_name', 'full name'),
    'email': ('email', 'e-mail', 'mail', 'e mail'),
    'telefone': ('telefone', 'phone', 'celular', 'whatsapp', 'fone', 'tel'),
    'nascimento': ('nascimento', 'data de nascimento', 'data nascimento', 'birth_date', 'birthday', 'birth date',
                   'aniversario', 'nasc'),
    'cidade': ('cidade', 'city'),
    'estado': ('estado', 'uf', 'state'),
    'situacao': ('situacao', 'status', 'situation'),
    'modalidade': ('modalidade', 'modality'),
    'app': ('app', 'usa o app', 'usa app', 'acesso ao app'),
    'objetivo': ('objetivo', 'goal', 'meta'),
}
STATUS_WORDS = {'ativo': 'active', 'active': 'active', 'experimental': 'trial', 'trial': 'trial', 'pausado': 'paused',
                'paused': 'paused', 'ex-aluno': 'former', 'ex aluno': 'former', 'former': 'former'}
MODALITY_WORDS = {'presencial': 'in_person', 'in person': 'in_person', 'in_person': 'in_person', 'online': 'online',
                  'hibrido': 'hybrid', 'hybrid': 'hybrid'}
YES = {'sim', 's', 'yes', 'y', 'true', '1', 'x', 'usa'}
NO = {'nao', 'n', 'no', 'false', '0', ''}

FORMULA = re.compile(r'^[=@\t\r]')
NUMBERISH = re.compile(r'^[+-]?[\d\s().,-]+$')


class CsvError(Exception):
    """The file cannot be read at all (the message is shown to the trainer)."""


def plain(text):
    """Lower-case, no accents: to compare header names and words typed by hand."""
    text = unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode()
    return ' '.join(text.lower().replace('_', ' ').split()) if text else ''


def safe_cell(value):
    """A value for a CSV cell that a spreadsheet will not run as a formula."""
    text = '' if value is None else str(value)
    if FORMULA.match(text) or (text[:1] in ('+', '-') and not NUMBERISH.match(text)):
        return "'" + text
    return text


# --- export -------------------------------------------------------------------------------------------------

def export_rows(students):
    """The header and one row per student (see COLUMNS)."""
    yield COLUMNS
    for person in students:
        yield [safe_cell(v) for v in (
            person.person_name, person.user.email if person.user_id and person.has_app else '', person.phone,
            person.birth_date.strftime('%d/%m/%Y') if person.birth_date else '', person.city, person.state,
            person.get_status_display(), person.get_modality_display() if person.modality else '',
            _('sim') if person.has_app else _('não'), person.goal.replace('\r', ' ').replace('\n', ' '),
            person.created_at.strftime('%d/%m/%Y'))]


def write_csv(rows, handle):
    """Writes the rows in the format Excel opens in columns (; separated, UTF-8 with a BOM)."""
    handle.write('﻿')
    writer = csv.writer(handle, delimiter=DELIMITER, lineterminator='\r\n')
    for row in rows:
        writer.writerow(row)


def template_rows():
    yield IMPORT_COLUMNS
    yield ['Carla Souza', 'carla@exemplo.com', '11 98888-7777', '15/03/1990', 'São Paulo', 'SP', 'ativo', 'presencial', 'sim',
           'Hipertrofia']
    yield ['João Lima', '', '11 97777-6666', '02/11/1985', '', '', 'experimental', 'online', 'não', '']


# --- import: reading the file -------------------------------------------------------------------------------

def decode(data):
    if len(data) > MAX_BYTES:
        raise CsvError(_('O arquivo passa de 1 MB.'))
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CsvError(_('Não consegui ler o arquivo: salve-o como CSV (UTF-8).'))


def parse(data):
    """[{column: text}] with the columns renamed to COLUMNS names (whatever the file called them); CsvError when the file
    cannot be used. Empty lines are skipped."""
    text = decode(data)
    if not text.strip():
        raise CsvError(_('O arquivo está vazio.'))
    head = text.splitlines()[0]
    delimiter = max((';', ',', '\t'), key=head.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        raise CsvError(_('O arquivo está vazio.'))
    wanted = {}
    for index, name in enumerate(header):
        key = plain(name)
        for column, names in ALIASES.items():
            if key in names and column not in wanted:
                wanted[column] = index
    if 'nome' not in wanted:
        raise CsvError(_('Não achei a coluna "nome" na primeira linha do arquivo. Baixe o modelo para ver o formato.'))
    rows = []
    try:
        for line in reader:
            if not any(cell.strip() for cell in line):
                continue
            rows.append({column: (line[index].strip() if index < len(line) else '') for column, index in wanted.items()})
            if len(rows) > MAX_ROWS:
                raise CsvError(_('O arquivo tem mais de %(max)d alunos: envie em partes.') % {'max': MAX_ROWS})
    except csv.Error:
        raise CsvError(_('O arquivo não parece um CSV válido.'))
    if not rows:
        raise CsvError(_('O arquivo só tem a linha de títulos: nenhum aluno para importar.'))
    return rows


def parse_date(text):
    """'dd/mm/yyyy', 'dd-mm-yyyy' or 'yyyy-mm-dd' -> 'yyyy-mm-dd' (what the form reads); '' when empty, None when not a date."""
    text = (text or '').strip()
    if not text:
        return ''
    for pattern in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


# --- import: checking each row ------------------------------------------------------------------------------

READY, DUPLICATE, ERROR = 'ready', 'duplicate', 'error'


def form_data(row, confirm_duplicate=False):
    """The data PersonForm reads for one row, and the problems found while translating the text of the file."""
    problems = []
    email = (row.get('email') or '').strip().lower()
    birth = parse_date(row.get('nascimento'))
    if birth is None:
        problems.append(_('Nascimento "%(value)s" não é uma data (use dd/mm/aaaa).') % {'value': row.get('nascimento')})
        birth = ''
    status = STATUS_WORDS.get(plain(row.get('situacao')), '' if not plain(row.get('situacao')) else None)
    if status is None:
        problems.append(_('Situação "%(value)s" não existe (use ativo ou experimental).') % {'value': row.get('situacao')})
        status = ''
    elif status and status not in Person.OPEN_STATUSES:
        problems.append(_('Só dá para importar alunos ativos ou experimentais (situação "%(value)s").') % {'value': row.get('situacao')})
        status = ''
    modality = MODALITY_WORDS.get(plain(row.get('modalidade')), '' if not plain(row.get('modalidade')) else None)
    if modality is None:
        problems.append(_('Modalidade "%(value)s" não existe (presencial, online ou híbrido).') % {'value': row.get('modalidade')})
        modality = ''
    app_word = plain(row.get('app'))
    if app_word in YES:
        has_app = True
    elif app_word in NO and app_word != '':
        has_app = False
    elif app_word == '':
        has_app = bool(email)           # not said: with an e-mail the student gets an invite, without it only a file
    else:
        problems.append(_('"%(value)s" na coluna app não é sim nem não.') % {'value': row.get('app')})
        has_app = bool(email)
    data = {'person_name': row.get('nome', ''), 'email': email, 'phone': row.get('telefone', ''), 'birth_date': birth,
            'city': row.get('cidade', ''), 'state': row.get('estado', ''), 'goal': row.get('objetivo', ''),
            'status': status, 'modality': modality, 'has_app': 'on' if has_app else ''}
    if confirm_duplicate:
        data['confirm_duplicate'] = 'on'
    return data, problems


def check(rows, accept_duplicates=()):
    """One result per row: {'index', 'row', 'state': ready|duplicate|error, 'messages': [...], 'duplicates': [names], 'form'}.

    `accept_duplicates` are the row indexes the trainer said to import anyway. A row that repeats an earlier one of the same
    file (same e-mail, same phone or same name) is an error: importing it would make two students."""
    results, seen = [], {}
    for index, row in enumerate(rows):
        data, problems = form_data(row, confirm_duplicate=index in accept_duplicates)
        form = PersonForm(data)
        messages, duplicates, state = list(problems), [], READY
        valid = form.is_valid()
        if not valid:
            for field, errors in form.errors.items():
                for error in errors:
                    if field == '__all__' and form.duplicates:
                        continue
                    messages.append(str(error))
            duplicates = [f'{p.person_name} ({why})' for p, why in form.duplicates]
        if duplicates and index not in accept_duplicates:
            state = DUPLICATE
        elif messages or not valid:
            state = ERROR
        keys = [k for k in (('e', data['email']), ('p', re.sub(r'\D', '', data['phone'] or '')),
                            ('n', plain(data['person_name']))) if k[1]]
        earlier = next((seen[k] for k in keys if k in seen), None)
        if earlier is not None:
            messages.append(_('Repete a linha %(line)d do arquivo.') % {'line': earlier + 2})
            state = ERROR
        else:
            for k in keys:
                seen[k] = index
        if state == READY and problems:
            state = ERROR
        results.append({'index': index, 'row': row, 'state': state, 'messages': messages, 'duplicates': duplicates,
                        'form': form if state == READY else None, 'name': row.get('nome', '')})
    return results


def create(results, actor, only=None):
    """Creates the students of the rows that are ready (and in `only`, when given), the way "Cadastrar aluno" does: an
    account without a password (waiting for the invite link when there is an e-mail), the file and a line in its history.
    Returns the new students."""
    created = []
    for result in results:
        if result['state'] != READY or (only is not None and result['index'] not in only):
            continue
        form = result['form']
        with transaction.atomic():
            user = app_access.new_account(form.cleaned_data['email'], form.cleaned_data['has_app'])
            person = form.save(commit=False)
            person.user = user
            person.status_changed_at = timezone.now()
            person.save()
            log_event(person, StudentEvent.Kind.CREATED, actor,
                      _('Cadastro importado de um arquivo CSV (%(status)s)') % {'status': person.get_status_display()})
        created.append(person)
    return created
