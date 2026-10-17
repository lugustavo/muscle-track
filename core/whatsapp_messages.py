"""The texts that go ready in the WhatsApp links (billing, missed class, access invitation), editable by the trainer.

A text uses variables between braces ({nome}, {valor}...). The default texts live here; the trainer's own version is
a `WhatsAppTemplate` row. A line with a variable that has no value and is optional ({valor}, {pix}) is left out, so a
student without a price or a server without a Pix key does not get an empty "Valor:" line.

The default texts are lazy: they are written in the language active when they are shown (the student's, see
`language_of`). A text the trainer saved is kept as typed and is used in every language."""
import re

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext as _, gettext_lazy as _l

from .i18n import clean
from .models import WhatsAppTemplate

Kind = WhatsAppTemplate.Kind
MAX_LENGTH = 1000

DEFAULTS = {
    Kind.CHARGE: _l('Olá, {nome}! Passando para lembrar do pagamento do pacote {pacote}.\n'
                    'Valor: {valor}.\n'
                    'Chave Pix: {pix}\n'
                    'Qualquer dúvida é só falar. 💪'),
    Kind.MISSED: _l('Oi, {nome}! Sentimos sua falta na aula de {data}. Vamos remarcar? 💪'),
    Kind.INVITE: _l('Olá, {nome}! Seu acesso ao Muscle Track está pronto. '
                    'Crie sua senha por este link (válido por 7 dias): {link}'),
}

VARIABLES = {
    Kind.CHARGE: (('nome', _l('primeiro nome do aluno')), ('pacote', _l('nome do pacote')), ('valor', _l('valor a pagar')),
                  ('pix', _l('chave Pix do servidor'))),
    Kind.MISSED: (('nome', _l('primeiro nome do aluno')), ('data', _l('dia da aula (dd/mm)'))),
    Kind.INVITE: (('nome', _l('primeiro nome do aluno')), ('link', _l('link para criar a senha'))),
}
REQUIRED = {Kind.INVITE: ('link',)}          # a text without them would be useless
OPTIONAL = {'valor', 'pix'}                  # no value: the whole line is left out

LABELS = {
    Kind.CHARGE: (_l('Cobrança de pacote'), _l('Ao tocar no WhatsApp de um pagamento pendente (painel, Financeiro e ficha).')),
    Kind.MISSED: (_l('Aula que o aluno faltou'), _l('Ao tocar no WhatsApp de uma falta recente (painel).')),
    Kind.INVITE: (_l('Convite de acesso'), _l('Ao enviar o link de acesso ao aluno (tela do convite).')),
}

# Example values for the preview: lazy, so the preview follows the language of whoever is editing the text
SAMPLE = {'nome': _l('Carla'), 'pacote': _l('Pacote 10 aulas'), 'valor': _l('500,00 €'), 'pix': _l('sua-chave-pix'),
          'data': _l('12/10'), 'link': _l('https://seu-site/convite/abc123')}

_VARIABLE = re.compile(r'\{(\w+)\}')


def language_of(person):
    """The language to write to this student in: the one saved for their account, else the site default.

    Reads the account's `notification_preference` (a list can `select_related` it to avoid one query per student)."""
    try:
        code = person.user.notification_preference.language
    except ObjectDoesNotExist:
        code = ''
    return clean(code) or settings.LANGUAGE_CODE


def current_text(kind):
    """The trainer's text, or the default one (lazy: it is written in the language active when it is rendered)."""
    saved = WhatsAppTemplate.objects.filter(kind=kind).values_list('text', flat=True).first()
    return saved if saved else DEFAULTS[kind]


def render(kind, values, text=None):
    """The message for `kind` with the variables filled (`text` tries a text that is not saved yet)."""
    text = current_text(kind) if text is None else text
    lines = []
    for line in str(text).splitlines():
        names = _VARIABLE.findall(line)
        if any(name in OPTIONAL and not values.get(name) for name in names):
            continue
        line = _VARIABLE.sub(lambda m: str(values.get(m.group(1), m.group(0))), line)
        line = ' '.join(line.split())
        lines.append(re.sub(r'\s+([.,!?;:])', r'\1', line))
    return '\n'.join(lines).strip()


def problems(kind, text):
    """Why `text` cannot be saved (a list of messages for the trainer; empty = fine)."""
    text = (text or '').strip()
    found = []
    if not text:
        return [_('O texto não pode ficar vazio (use "Restaurar o padrão").')]
    if len(text) > MAX_LENGTH:
        found.append(_('O texto passa de %(max)d caracteres.') % {'max': MAX_LENGTH})
    known = {name for name, description in VARIABLES[kind]}
    unknown = sorted({n for n in _VARIABLE.findall(text) if n not in known})
    if unknown:
        found.append(_('Variável desconhecida: %(names)s.') % {'names': ', '.join('{' + n + '}' for n in unknown)})
    for name in REQUIRED.get(kind, ()):
        if '{' + name + '}' not in text:
            found.append(_('O texto precisa ter {%(name)s}: sem ele a mensagem não leva o link.') % {'name': name})
    return found
