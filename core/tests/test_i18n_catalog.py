import gettext
import importlib.util
from pathlib import Path

import polib
from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
LOCALE = ROOT / 'locale' / 'en' / 'LC_MESSAGES'


def _tool():
    spec = importlib.util.spec_from_file_location('i18n_catalog', ROOT / 'tools' / 'i18n_catalog.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EnglishCatalogTests(SimpleTestCase):
    def test_both_catalogs_exist(self):
        for domain in ('django', 'djangojs'):
            self.assertTrue((LOCALE / f'{domain}.po').is_file(), domain)
            self.assertTrue((LOCALE / f'{domain}.mo').is_file(), f'{domain}.mo (run compilemessages)')

    def test_every_entry_is_translated_with_the_same_placeholders_and_tags(self):
        found = _tool().problems()
        self.assertEqual(found, [], 'Catálogo en incompleto ou inconsistente:\n' + '\n'.join(
            f'[{d}] {m[:80]!r}: {why}' for d, m, why in found[:40]))

    def test_the_compiled_files_match_the_po_files(self):
        for domain in ('django', 'djangojs'):
            po = polib.pofile(str(LOCALE / f'{domain}.po'))
            with open(LOCALE / f'{domain}.mo', 'rb') as handle:
                mo = gettext.GNUTranslations(handle)
            stale = []
            for entry in po:
                if entry.obsolete or not entry.msgid:
                    continue
                if entry.msgid_plural:
                    for index, form in entry.msgstr_plural.items():
                        if mo.ngettext(entry.msgid, entry.msgid_plural, 1 if int(index) == 0 else 2) != form:
                            stale.append(entry.msgid)
                elif mo.gettext(entry.msgid) != entry.msgstr:
                    stale.append(entry.msgid)
            self.assertEqual(stale, [], f'{domain}.mo desatualizado (rode compilemessages): {stale[:5]}')
