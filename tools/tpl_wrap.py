"""Wrap the visible Portuguese text of Django templates for translation ({% trans %} / {% blocktrans %}).

Mechanical part of the i18n work: text that is only text (plus inline <strong>/<em>... tags) is wrapped; text mixed with
{{ variables }} is wrapped with a blocktrans that binds the variables; `word{{ n|pluralize }}` becomes a counted
blocktrans. Anything it cannot do safely is left alone and reported (--report) for a person to finish.

  python tools/tpl_wrap.py templates [--write] [--report]
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tpl_scan import segments  # noqa: E402

LETTERS = re.compile(r'[A-Za-zÀ-ÿ]{2}')
INLINE = {'strong', 'b', 'em', 'small', 'code', 'u', 'mark', 'sup', 'sub', 'kbd', 'abbr'}
ATTRS = ('title', 'placeholder', 'aria-label', 'alt')
VAR = re.compile(r'^\{\{\s*(.+?)\s*\}\}$', re.S)
PLURALIZE = re.compile(r'^(.+?)\|pluralize(?::"([^"]*)")?$')


def tag_name(tok):
    m = re.match(r'</?\s*([a-zA-Z0-9]+)', tok)
    return m.group(1).lower() if m else ''


def is_icon(tok):
    return tag_name(tok) == 'i' and 'bi' in tok


def is_inline_tag(tok):
    name = tag_name(tok)
    return name in INLINE and not is_icon(tok)


def tpl_kind(tok):
    return 'var' if tok.startswith('{{') else 'tag'


class Wrapper:
    def __init__(self, text):
        self.text = text
        self.segs = list(segments(text))
        self.report = []

    # ---- runs: maximal sequences of text / {{ var }} / inline html between boundaries
    def runs(self):
        run, depth_trans = [], 0
        for i, (kind, value, s, e) in enumerate(self.segs):
            if kind == 'tpl' and re.match(r'\{%\s*(blocktrans|blocktranslate)\b', value):
                depth_trans += 1
            if kind == 'tpl' and re.match(r'\{%\s*(endblocktrans|endblocktranslate)\b', value):
                depth_trans -= 1
                run = []
                continue
            if depth_trans:
                continue
            if kind == 'text' or (kind == 'tpl' and value.startswith('{{')) or (kind == 'tag' and is_inline_tag(value)):
                run.append(i)
            else:
                if run:
                    yield run
                run = []
        if run:
            yield run

    @staticmethod
    def _matched(parts):
        stack, matched = [], set()
        for idx, (kind, value, s, e) in enumerate(parts):
            if kind != 'tag':
                continue
            name = tag_name(value)
            if value.startswith('</'):
                if stack and stack[-1][1] == name:
                    matched.add(stack.pop()[0])
                    matched.add(idx)
            else:
                stack.append((idx, name))
        return matched

    @staticmethod
    def balance(parts):
        """Drop unmatched inline tags (and blanks) at the ends, peel a pair that wraps everything; None if the middle is
        unbalanced."""
        parts = list(parts)
        while True:
            matched = Wrapper._matched(parts)

            def droppable(i):
                kind, value = parts[i][0], parts[i][1]
                return (kind == 'tag' and i not in matched) or (kind == 'text' and not value.strip())
            while parts and droppable(0):
                parts.pop(0)
                matched = Wrapper._matched(parts)
            while parts and droppable(len(parts) - 1):
                parts.pop()
                matched = Wrapper._matched(parts)
            if any(k == 'tag' and i not in matched for i, (k, *_r) in enumerate(parts)):
                return None
            if (len(parts) >= 2 and parts[0][0] == 'tag' and parts[-1][0] == 'tag'
                    and tag_name(parts[0][1]) == tag_name(parts[-1][1])):
                depth, enclosing = 0, True
                for idx, (kind, value, s, e) in enumerate(parts):
                    if kind == 'tag':
                        depth += -1 if value.startswith('</') else 1
                        if depth == 0 and idx != len(parts) - 1:
                            enclosing = False
                            break
                if enclosing:
                    parts = parts[1:-1]
                    continue
            return parts

    def process(self):
        replacements = []   # (start, end, new)
        for run in self.runs():
            sub_runs = self._split(run)
            for parts in sub_runs:
                self._process_parts(parts, replacements)
        return self._attributes(replacements)

    def _split(self, run):
        """The run as balanced pieces: unmatched inline tags in the middle become boundaries."""
        parts = [self.segs[i] for i in run]
        balanced = self.balance(parts)
        if balanced is not None:
            return [balanced] if balanced else []
        matched = self._matched(parts)
        pieces, current = [], []
        for idx, seg in enumerate(parts):
            if seg[0] == 'tag' and idx not in matched:
                pieces.append(current)
                current = []
            else:
                current.append(seg)
        pieces.append(current)
        out = []
        for piece in pieces:
            piece = self.balance(piece)
            if piece:
                out.append(piece)
        return out

    def _process_parts(self, parts, replacements):
        start, end = parts[0][2], parts[-1][3]
        raw = self.text[start:end]
        plain = re.sub(r'<[^>]+>|\{\{.*?\}\}', '', raw)
        if not LETTERS.search(plain):
            return
        in_title = re.search(r'\{%\s*block title\s*%\}\s*$', self.text[:start]) is not None
        if in_title and 'Muscle Track' in raw:   # page titles carry the brand (STUDIO_NAME or Muscle Track)
            raw2 = raw.replace('Muscle Track', '{{ brand_name }}')
            new = self._wrap_vars(raw2[:len(raw2) - len(raw2.lstrip())], raw2.strip(), raw2[len(raw2.rstrip()):])
        else:
            new = self._wrap(raw, parts)
        if new is None:
            self.report.append((self.text.count('\n', 0, start) + 1, ' '.join(raw.split())[:140]))
        elif new != raw:
            replacements.append((start, end, new))

    def _attributes(self, replacements):
        # attributes (pure text only)
        for kind, value, s, e in self.segs:
            if kind != 'tag':
                continue
            new_tag = re.sub(r"confirm\('([^'{}]*[A-Za-zÀ-ÿ]{2}[^'{}]*)'\)",
                             lambda m: "confirm('{% filter escapejs %}{% trans " + self._q(m.group(1)) + " %}{% endfilter %}')", value)
            for attr in ATTRS:
                def sub(m):
                    v = m.group(1)
                    if LETTERS.search(v) and '{%' not in v and '{{' not in v and not v.startswith('http'):
                        return f'{attr}="{{% trans {self._q(v)} %}}"'
                    return m.group(0)
                new_tag = re.sub(r'\b' + attr + r'="([^"]*)"', sub, new_tag)
            if new_tag != value:
                replacements.append((s, e, new_tag))
        return replacements

    @staticmethod
    def _q(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

    @staticmethod
    def _inside_choice_text(raw):
        return False

    # ---- wrapping one run
    def _wrap(self, raw, parts):
        lead = raw[:len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
        body = raw.strip()
        has_var = any(k == 'tpl' for k, *_ in parts)
        if not has_var:
            if not any(k == 'tag' for k, *_ in parts):
                if '\n' in body:
                    return f'{lead}{{% blocktrans trimmed %}}{body}{{% endblocktrans %}}{trail}'
                return f'{lead}{{% trans {self._q(body)} %}}{trail}'
            inner = self._only_text_inside(parts)
            if inner is not None:       # <strong>Text</strong>: translate the text, keep the tag
                return None if inner is False else inner
            if re.search(r'\{%|%\}', body):
                return None
            return f'{lead}{{% blocktrans trimmed %}}{body}{{% endblocktrans %}}{trail}'
        return self._wrap_vars(lead, body, trail)

    def _only_text_inside(self, parts):
        """For '<a ...>' + text + '</a>' style runs where the text is one piece: keep the tags, wrap the text."""
        texts = [p for p in parts if p[0] == 'text' and LETTERS.search(p[1])]
        tags = [p for p in parts if p[0] == 'tag']
        if len(texts) == 1 and len(tags) == 2 and parts[0][0] == 'tag' and parts[-1][0] == 'tag' and parts[1][0] == 'text':
            t = parts[1][1]
            lead = t[:len(t) - len(t.lstrip())]
            trail = t[len(t.rstrip()):]
            if '\n' in t.strip():
                return False
            return f'{parts[0][1]}{lead}{{% trans {self._q(t.strip())} %}}{trail}{parts[2][1]}'
        return None

    def _wrap_vars(self, lead, body, trail):
        # Rebuild the run into msgid + bindings
        pieces = []
        binds = {}
        names = {}
        count = None
        i = 0
        tokens = re.split(r'(\{\{.*?\}\})', body)
        for tok in tokens:
            m = VAR.match(tok)
            if not m:
                pieces.append(tok)
                continue
            expr = m.group(1)
            pm = PLURALIZE.match(expr)
            if pm:
                if count is None or count == pm.group(1):
                    count = pm.group(1)
                    suffix = pm.group(2)
                    pieces.append(('plural', suffix))
                    continue
                return None
            if re.search(r'[\'"].*[\'"]\s*\)?$', expr) and '|' not in expr and False:
                return None
            if re.fullmatch(r'[A-Za-z_]\w*', expr):
                pieces.append(('var', expr))
                continue
            name = self._name(expr, names)
            binds[name] = expr
            pieces.append(('var', name))
        if re.search(r'\{%', body):
            return None
        if any(isinstance(p, tuple) and p[0] == 'var' for p in pieces) or count:
            pass
        sing = ''.join(p if isinstance(p, str) else (f'{{{{ {p[1]} }}}}' if p[0] == 'var' else '') for p in pieces)
        if count is None:
            decl = ' '.join(f'{n}={e}' for n, e in binds.items())
            return f'{lead}{{% blocktrans trimmed{(" with " + decl) if decl else ""} %}}{sing}{{% endblocktrans %}}{trail}'
        # counted: singular without suffix, plural with it ("s" by default)
        plural = ''.join(p if isinstance(p, str) else (f'{{{{ {p[1]} }}}}' if p[0] == 'var' else self._plural_suffix(p[1])) for p in pieces)
        singular = ''.join(p if isinstance(p, str) else (f'{{{{ {p[1]} }}}}' if p[0] == 'var' else self._singular_suffix(p[1])) for p in pieces)
        decl = ' '.join(f'{n}={e}' for n, e in binds.items())
        counter_names = [n for n, e in binds.items() if e == count]
        counter = counter_names[0] if counter_names else self._name(count, names)
        binds2 = dict(binds)
        extra = '' if counter_names else f' {counter}={count}'
        # `count` binds its own name; remove it from the plain bindings
        decl2 = ' '.join(f'{n}={e}' for n, e in binds2.items() if n != counter)
        return (f'{lead}{{% blocktrans trimmed{(" with " + decl2) if decl2 else ""} count {counter}={count} %}}'
                f'{singular}{{% plural %}}{plural}{{% endblocktrans %}}{trail}')

    @staticmethod
    def _plural_suffix(suffix):
        if suffix is None:
            return 's'
        return suffix.split(',')[-1] if ',' in suffix else suffix

    @staticmethod
    def _singular_suffix(suffix):
        if suffix is None:
            return ''
        return suffix.split(',')[0] if ',' in suffix else ''

    @staticmethod
    def _name(expr, taken):
        base = re.sub(r'\|.*$', '', expr).strip()
        base = re.split(r'[.\s]', base)[-1] or 'value'
        base = re.sub(r'\W', '', base) or 'value'
        name, n = base, 2
        while name in taken and taken[name] != expr:
            name = f'{base}{n}'
            n += 1
        taken[name] = expr
        return name


def apply(text, replacements):
    for start, end, new in sorted(replacements, reverse=True):
        text = text[:start] + new + text[end:]
    return text


def ensure_load(text):
    if re.search(r'\{%\s*load[^%]*\bi18n\b', text):
        return text
    m = re.match(r'(\s*\{%\s*extends[^%]*%\}\s*\n)', text)
    if m:
        return text[:m.end()] + '{% load i18n %}\n' + text[m.end():]
    return '{% load i18n %}\n' + text


if __name__ == '__main__':
    root = Path(sys.argv[1] if len(sys.argv) > 1 else 'templates')
    write = '--write' in sys.argv
    report_all = []
    changed = 0
    for path in sorted(root.rglob('*.html')):
        text = path.read_text(encoding='utf-8')
        w = Wrapper(text)
        reps = w.process()
        if reps:
            new = ensure_load(apply(text, reps))
            changed += 1
            if write:
                path.write_text(new, encoding='utf-8')
        for line, run in w.report:
            report_all.append((str(path), line, run))
    print(f'files changed: {changed}; left for hand: {len(report_all)}')
    if '--report' in sys.argv:
        for p, l, r in report_all:
            print(f'{p}:{l}: {r}')
