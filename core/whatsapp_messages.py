"""The texts that go ready in the WhatsApp links (billing, missed class, access invitation), editable by the trainer.

A text uses variables between braces ({nome}, {valor}...). The default texts live here; the trainer's own version is
a `WhatsAppTemplate` row. A line with a variable that has no value and is optional ({valor}, {pix}) is left out, so a
student without a price or a server without a Pix key does not get an empty "Valor:" line."""
import re

from .models import WhatsAppTemplate

Kind = WhatsAppTemplate.Kind
MAX_LENGTH = 1000

DEFAULTS = {
    Kind.CHARGE: ('Olá, {nome}! Passando para lembrar do pagamento do pacote {pacote}.\n'
                  'Valor: {valor}.\n'
                  'Chave Pix: {pix}\n'
                  'Qualquer dúvida é só falar. 💪'),
    Kind.MISSED: 'Oi, {nome}! Sentimos sua falta na aula de {data}. Vamos remarcar? 💪',
    Kind.INVITE: ('Olá, {nome}! Seu acesso ao Muscle Track está pronto. '
                  'Crie sua senha por este link (válido por 7 dias): {link}'),
}

VARIABLES = {
    Kind.CHARGE: (('nome', 'primeiro nome do aluno'), ('pacote', 'nome do pacote'), ('valor', 'valor a pagar'),
                  ('pix', 'chave Pix do servidor')),
    Kind.MISSED: (('nome', 'primeiro nome do aluno'), ('data', 'dia da aula (dd/mm)')),
    Kind.INVITE: (('nome', 'primeiro nome do aluno'), ('link', 'link para criar a senha')),
}
REQUIRED = {Kind.INVITE: ('link',)}          # a text without them would be useless
OPTIONAL = {'valor', 'pix'}                  # no value: the whole line is left out

LABELS = {
    Kind.CHARGE: ('Cobrança de pacote', 'Ao tocar no WhatsApp de um pagamento pendente (painel, Financeiro e ficha).'),
    Kind.MISSED: ('Aula que o aluno faltou', 'Ao tocar no WhatsApp de uma falta recente (painel).'),
    Kind.INVITE: ('Convite de acesso', 'Ao enviar o link de acesso ao aluno (tela do convite).'),
}

SAMPLE = {'nome': 'Carla', 'pacote': 'Pacote 10 aulas', 'valor': '500,00 €', 'pix': 'sua-chave-pix', 'data': '12/10',
          'link': 'https://seu-site/convite/abc123'}

_VARIABLE = re.compile(r'\{(\w+)\}')


def current_text(kind):
    """The trainer's text, or the default one."""
    saved = WhatsAppTemplate.objects.filter(kind=kind).values_list('text', flat=True).first()
    return saved if saved else DEFAULTS[kind]


def render(kind, values, text=None):
    """The message for `kind` with the variables filled (`text` tries a text that is not saved yet)."""
    text = current_text(kind) if text is None else text
    lines = []
    for line in text.splitlines():
        names = _VARIABLE.findall(line)
        if any(name in OPTIONAL and not values.get(name) for name in names):
            continue
        line = _VARIABLE.sub(lambda m: str(values.get(m.group(1), m.group(0))), line)
        line = ' '.join(line.split())
        lines.append(re.sub(r'\s+([.,!?;:])', r'\1', line))
    return '\n'.join(lines).strip()


def problems(kind, text):
    """Why `text` cannot be saved (a list of messages in Portuguese; empty = fine)."""
    text = (text or '').strip()
    found = []
    if not text:
        return ['O texto não pode ficar vazio (use "Restaurar o padrão").']
    if len(text) > MAX_LENGTH:
        found.append(f'O texto passa de {MAX_LENGTH} caracteres.')
    known = {name for name, _ in VARIABLES[kind]}
    unknown = sorted({n for n in _VARIABLE.findall(text) if n not in known})
    if unknown:
        found.append('Variável desconhecida: ' + ', '.join('{' + n + '}' for n in unknown) + '.')
    for name in REQUIRED.get(kind, ()):
        if '{' + name + '}' not in text:
            found.append('O texto precisa ter {' + name + '}: sem ele a mensagem não leva o link.')
    return found
