"""How an exercise is prescribed beyond sets x repetitions: counted in seconds (a plank) and the intensity techniques
(drop-set, rest-pause, bi-set, tri-set).

The prescription of a routine row (template or student's) carries a `measure` ('reps' or 'time') and a `technique`. A bi-set
or tri-set ties a row to the one(s) that follow it: they are done one after the other with no rest in between, which the
screens show with matching labels (A1, A2...)."""
from django.utils.translation import gettext as _, gettext_lazy as _l

MEASURES = [('reps', _l('Repetições')), ('time', _l('Tempo (segundos)'))]
TECHNIQUES = [('', _l('Nenhuma')), ('drop_set', _l('Drop-set')), ('rest_pause', _l('Rest-pause')),
              ('bi_set', _l('Bi-set (com o próximo exercício)')), ('tri_set', _l('Tri-set (com os 2 próximos exercícios)'))]
MEASURE_VALUES = {value for value, _label in MEASURES}
TECHNIQUE_VALUES = {value for value, _label in TECHNIQUES}
GROUP_SIZE = {'bi_set': 2, 'tri_set': 3}


def clean_measure(value):
    return value if value in MEASURE_VALUES else 'reps'


def clean_technique(value):
    return value if value in TECHNIQUE_VALUES else ''


def target_label(sets, reps, measure):
    """'3 × 12' for repetitions, '3 × 30 s' for time."""
    return f'{sets} × {reps} s' if measure == 'time' else f'{sets} × {reps}'


def technique_name(technique):
    return dict(TECHNIQUES).get(technique, '') if technique else ''


def hint(technique):
    """One line telling the student how to do the technique (shown in the workout screen)."""
    return {
        'drop_set': _('Drop-set: ao chegar na falha, reduza a carga uns 20% a 30% e continue, sem descanso, mais uma ou duas vezes.'),
        'rest_pause': _('Rest-pause: ao chegar na falha, descanse uns 15 segundos e faça mais repetições, mais uma ou duas vezes.'),
        'bi_set': _('Bi-set: faça este exercício e o próximo em sequência, sem descanso entre eles; descanse só depois do segundo.'),
        'tri_set': _('Tri-set: faça este exercício e os 2 próximos em sequência, sem descanso entre eles; descanse só depois do terceiro.'),
    }.get(technique, '')


def annotate(rows):
    """Marks each row (anything with `.technique`) with `group_label` ('A1', 'A2'... for the rows tied by a bi-set or tri-set,
    '' for the others), `technique_label` and `technique_hint`. A bi-set on the last row has nobody to pair with and is shown
    as a plain row. Returns what it was given (a queryset keeps its cache, so templates can still use `.count`)."""
    given = rows
    rows = list(given)
    for row in rows:
        row.group_label, row.technique_label, row.technique_hint = '', technique_name(row.technique), hint(row.technique)
    letter, index = ord('A') - 1, 0
    while index < len(rows):
        size = GROUP_SIZE.get(rows[index].technique, 0)
        if size and index + size <= len(rows):
            letter += 1
            for offset in range(size):
                member = rows[index + offset]
                member.group_label = f'{chr(letter)}{offset + 1}'
                if offset:                         # the others of the group do not carry the technique themselves
                    member.technique_label, member.technique_hint = _('Junto com o anterior'), ''
            index += size
        else:
            if size:                               # nobody to pair with
                rows[index].technique_label = rows[index].technique_hint = ''
            index += 1
    return given if hasattr(given, '_result_cache') else rows
