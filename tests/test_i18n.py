import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from a50control.i18n import STRINGS, pick_lang  # noqa: E402

PLACEHOLDER = re.compile(r"\{(\w+)\}")
UI = Path(__file__).resolve().parents[1] / "a50control"


class I18nTests(unittest.TestCase):
    def test_same_keys_and_placeholders(self):
        self.assertEqual(sorted(STRINGS["de"]), sorted(STRINGS["en"]))
        for key, text in STRINGS["en"].items():
            self.assertEqual(sorted(PLACEHOLDER.findall(STRINGS["de"][key])), sorted(PLACEHOLDER.findall(text)), key)

    def test_every_used_key_exists(self):
        used = set()
        for path in [UI / "app.py", *(UI / "ui").glob("*.py")]:
            used |= set(re.findall(r"""\bt\(\s*["']([a-z_]+)["']""", path.read_text()))
        self.assertGreater(len(used), 50)
        self.assertEqual(sorted(used - set(STRINGS["en"])), [])

    def test_language_follows_the_system_locale(self):
        self.assertEqual(pick_lang({"LANG": "de_DE.UTF-8"}), "de")
        self.assertEqual(pick_lang({"LANG": "de_AT.UTF-8"}), "de")
        self.assertEqual(pick_lang({"LANG": "en_US.UTF-8"}), "en")
        self.assertEqual(pick_lang({"LANG": "fr_FR.UTF-8"}), "en")
        self.assertEqual(pick_lang({}), "en")
        self.assertEqual(pick_lang({"LANG": "C.UTF-8"}), "en")
        # LANGUAGE overrides, but like gettext only with a real locale
        self.assertEqual(pick_lang({"LANG": "de_DE.UTF-8", "LANGUAGE": "en"}), "en")
        self.assertEqual(pick_lang({"LANG": "en_US.UTF-8", "LANGUAGE": "de:en"}), "de")
        self.assertEqual(pick_lang({"LANG": "C", "LANGUAGE": "de"}), "en")
        # LC_ALL beats LC_MESSAGES beats LANG
        self.assertEqual(pick_lang({"LANG": "de_DE.UTF-8", "LC_ALL": "en_GB.UTF-8"}), "en")
        self.assertEqual(pick_lang({"LANG": "en_US.UTF-8", "LC_MESSAGES": "de_DE.UTF-8"}), "de")


if __name__ == "__main__":
    unittest.main()
