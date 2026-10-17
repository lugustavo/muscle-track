"""Helpers for the English catalog (locale/en/LC_MESSAGES/{django,djangojs}.po).

  python tools/i18n_catalog.py extract              # makemessages for Python/templates and for the JS (needs gettext)
  python tools/i18n_catalog.py todo OUT_DIR [N]     # untranslated entries, split into N JSON chunks for translators
  python tools/i18n_catalog.py merge FILE.json ...  # apply translations ({"msgid": "msgstr"} / {"msgid": ["one", "other"]})
  python tools/i18n_catalog.py check                # placeholders, HTML tags, plurals, fuzzy/empty entries
  python tools/i18n_catalog.py compile              # .po -> .mo (needs gettext)

The source language of the app is Portuguese (pt-br): the msgids are the Portuguese texts, so only `en` has a catalog.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
DOMAINS = ('django', 'djangojs')
PLACEHOLDER = re.compile(r'%\((\w+)\)[sd]|%[sd]')
TAG = re.compile(r'</?\s*([a-zA-Z0-9]+)')


def po_path(domain):
    return ROOT / 'locale' / 'en' / 'LC_MESSAGES' / f'{domain}.po'


def load(domain):
    return polib.pofile(str(po_path(domain)))


def manage(*args):
    env = {'SECRET_KEY': 'x', 'DEBUG': '1', 'PATH': '/usr/bin:/bin'}
    import os
    env = {**os.environ, 'SECRET_KEY': 'x', 'DEBUG': '1'}
    subprocess.run([sys.executable, 'manage.py', *args], cwd=ROOT, env=env, check=True)


def extract():
    ignores = ['-i', 'staticfiles', '-i', 'static/vendor', '-i', 'tools', '-i', 'venv', '-i', '.venv', '-i', 'media']
    manage('makemessages', '-l', 'en', '-d', 'django', '--no-location', '--no-obsolete', '--no-wrap', *ignores)
    manage('makemessages', '-l', 'en', '-d', 'djangojs', '--no-location', '--no-obsolete', '--no-wrap', *ignores)


def entries_todo(domain):
    po = load(domain)
    return [e for e in po if not e.obsolete and (not e.translated() or 'fuzzy' in e.flags)]


def todo(out_dir, chunks=4):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for domain in DOMAINS:
        for e in entries_todo(domain):
            items.append({'domain': domain, 'msgid': e.msgid, 'msgid_plural': e.msgid_plural, 'context': e.msgctxt or '',
                          'comment': e.comment})
    items.sort(key=lambda i: (i['domain'], i['msgid']))
    size = (len(items) + chunks - 1) // chunks
    for n in range(chunks):
        part = items[n * size:(n + 1) * size]
        (out / f'chunk{n + 1}.json').write_text(json.dumps(part, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'{len(items)} entries in {chunks} chunks -> {out}')


def merge(files):
    pos = {d: load(d) for d in DOMAINS}
    applied = missing = 0
    for f in files:
        data = json.loads(Path(f).read_text(encoding='utf-8'))
        for key, value in data.items():
            domain, _sep, msgid = key.partition('::') if '::' in key else ('django', '', key)
            if domain not in pos:
                domain, msgid = 'django', key
            entry = next((e for e in pos[domain] if e.msgid == msgid and not e.obsolete), None)
            if entry is None:
                for other in DOMAINS:       # a key without a domain may belong to either catalog
                    entry = next((e for e in pos[other] if e.msgid == msgid and not e.obsolete), None)
                    if entry is not None:
                        break
            if entry is None:
                missing += 1
                print('not in catalog:', msgid[:80])
                continue
            if entry.msgid_plural:
                forms = value if isinstance(value, list) else [value, value]
                entry.msgstr_plural = {i: str(v) for i, v in enumerate(forms)}
            else:
                entry.msgstr = str(value)
            if 'fuzzy' in entry.flags:
                entry.flags.remove('fuzzy')
            applied += 1
    for d, po in pos.items():
        po.save()
    print(f'applied {applied}, not found {missing}')


def problems():
    found = []
    for domain in DOMAINS:
        if not po_path(domain).exists():
            continue
        for e in load(domain):
            if e.obsolete:
                continue
            if 'fuzzy' in e.flags:
                found.append((domain, e.msgid, 'fuzzy'))
            forms = list(e.msgstr_plural.values()) if e.msgid_plural else [e.msgstr]
            sources = [e.msgid] + ([e.msgid_plural] if e.msgid_plural else [])
            if not all(f.strip() for f in forms):
                found.append((domain, e.msgid, 'empty'))
                continue
            for i, form in enumerate(forms):
                src = sources[min(i, len(sources) - 1)]
                if sorted(PLACEHOLDER.findall(src)) != sorted(PLACEHOLDER.findall(form)):
                    found.append((domain, e.msgid, f'placeholders differ: {PLACEHOLDER.findall(src)} vs {PLACEHOLDER.findall(form)}'))
                if sorted(TAG.findall(src)) != sorted(TAG.findall(form)):
                    found.append((domain, e.msgid, f'html tags differ: {TAG.findall(src)} vs {TAG.findall(form)}'))
    return found


def check():
    found = problems()
    for domain, msgid, why in found[:200]:
        print(f'[{domain}] {msgid[:70]!r}: {why}')
    print(f'{len(found)} problem(s)')
    return 1 if found else 0


def compile_():
    manage('compilemessages', '-l', 'en')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'extract':
        extract()
    elif cmd == 'todo':
        todo(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 4)
    elif cmd == 'merge':
        merge(sys.argv[2:])
    elif cmd == 'check':
        sys.exit(check())
    elif cmd == 'compile':
        compile_()
    else:
        print(__doc__)
