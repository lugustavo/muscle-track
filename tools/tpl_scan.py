"""Scan Django templates for visible text that is not wrapped for translation (a helper for the i18n work)."""
import re
import sys
from pathlib import Path

_TPL = r'(?:\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\})'
# a tag may hold template tags that contain ">" (e.g. {% if n >= 2 %}), so it is read as pieces
TAG = r'<[A-Za-z/!](?:[^>{]|' + _TPL + r'|\{)*?>'
TOKEN = re.compile(r'(\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}|<!--.*?-->|<script\b.*?</script>|<style\b.*?</style>|' + TAG + r')', re.S)
ATTRS = ('title', 'placeholder', 'aria-label', 'alt', 'label')


def segments(text):
    """Yield (kind, value, start, end): kind in tag, tpl, comment, script, text."""
    pos = 0
    for m in TOKEN.finditer(text):
        if m.start() > pos:
            yield 'text', text[pos:m.start()], pos, m.start()
        tok = m.group(0)
        if tok.startswith('{#') or tok.startswith('<!--'):
            kind = 'comment'
        elif tok.startswith('{%') or tok.startswith('{{'):
            kind = 'tpl'
        elif tok.lower().startswith(('<script', '<style')):
            kind = 'script'
        else:
            kind = 'tag'
        yield kind, tok, m.start(), m.end()
        pos = m.end()
    if pos < len(text):
        yield 'text', text[pos:], pos, len(text)


def scan(path):
    text = path.read_text(encoding='utf-8')
    out = []
    in_trans = 0
    for kind, value, start, end in segments(text):
        if kind == 'tpl':
            if re.match(r'\{%\s*blocktrans', value) or re.match(r'\{%\s*blocktranslate', value):
                in_trans += 1
            elif re.match(r'\{%\s*endblocktrans', value):
                in_trans -= 1
            continue
        if kind == 'text' and not in_trans and re.search(r'[A-Za-zÀ-ÿ]{2}', value) and value.strip():
            out.append(('text', value.strip()))
        if kind == 'tag':
            for attr in ATTRS:
                for m in re.finditer(r'\b' + attr + r'="([^"]*)"', value):
                    v = m.group(1)
                    if re.search(r'[A-Za-zÀ-ÿ]{2}', v) and '{%' not in v and '{{' not in v:
                        out.append(('attr:' + attr, v))
    return out


if __name__ == '__main__':
    root = Path(sys.argv[1] if len(sys.argv) > 1 else 'templates')
    total = 0
    for path in sorted(root.rglob('*.html')):
        items = scan(path)
        total += len(items)
        if '-v' in sys.argv:
            print(f'--- {path} ({len(items)})')
            for kind, value in items:
                print(f'  [{kind}] {value[:110]!r}')
    print('TOTAL', total)
