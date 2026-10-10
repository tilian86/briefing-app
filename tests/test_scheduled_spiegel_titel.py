"""Terminierter Lauf: Titelzeile landet auch in der Spiegel-Kopie.

10.10.2026: briefing_scheduled.py schrieb den Reader-Titel nur in die
iCloud-TXT. Im lokalen Spiegel (~/.briefing_meta_mirror/Texte) blieb
"Audio-Briefing" stehen — Morgenfunk liest den Titel dort und fand den
Eintrag im ElevenReader nie.

Komplett offline: Bau, Upload, Anmeldeprüfungen, Newsletter, Merkliste,
WhatsApp-Runde und Podcast-Gedächtnis sind gefälscht, HOME zeigt auf einen
Wegwerf-Ordner. Es läuft kein Briefing, nichts geht ins Netz. Aufruf aus dem
Repo-Ordner:

    python3 -m unittest tests.test_scheduled_spiegel_titel
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import briefing_core as core  # noqa: E402
import briefing_scheduled as bs  # noqa: E402
import feedly_fetch  # noqa: E402
import newsletter_fetch  # noqa: E402
import podcast_verbaut  # noqa: E402
import reader_upload  # noqa: E402
import wa_runde  # noqa: E402

TXT_NAME = "2026-10-10_09-44_briefing_claude_synthese_eleven-reader.txt"
INHALT = "Audio-Briefing\n10.10.2026\nErstellt um 09:44:39 Uhr\n\nTop 3\n"


class SpiegelTitel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="briefing_test_")
        self.home = os.path.join(self.tmp, "home")
        self.archiv_texte = os.path.join(
            self.home, "Library/Mobile Documents/com~apple~CloudDocs/Downloads/Briefings/Texte")
        self.spiegel_texte = os.path.join(self.home, ".briefing_meta_mirror/Texte")
        os.makedirs(self.archiv_texte)
        os.makedirs(self.spiegel_texte)
        self.entwurf = os.path.join(self.tmp, "entwurf.json")
        with open(self.entwurf, "w", encoding="utf-8") as f:
            json.dump({"paywall_text": "### Testartikel\nText.\n"}, f)
        self.hochgeladen = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_bau(self, **_kw):
        # Der Kern schreibt dieselbe TXT in Archiv UND Spiegel (wie _mirror_txt_to_local).
        txt = os.path.join(self.archiv_texte, TXT_NAME)
        for pfad in (txt, os.path.join(self.spiegel_texte, TXT_NAME)):
            with open(pfad, "w", encoding="utf-8") as f:
                f.write(INHALT)
        return {"ok": True, "artifacts": {"eleven_txt": txt}, "beitrag_count": 1,
                "elapsed_seconds": 1}

    def _fake_upload(self, txt, titel, **_kw):
        self.hochgeladen.append(titel)
        return {"ok": True}

    def test_titel_auch_im_spiegel(self):
        verboten = mock.Mock(side_effect=AssertionError("darf im Test nicht laufen"))
        with mock.patch.dict(os.environ, {"HOME": self.home}), \
                mock.patch.object(bs, "DRAFT_PATH", self.entwurf), \
                mock.patch.object(bs, "STATUS_PATH", os.path.join(self.tmp, "status.json")), \
                mock.patch.object(bs, "LOG_PATH", os.path.join(self.tmp, "lauf.log")), \
                mock.patch.object(bs, "MANUELL", True), \
                mock.patch.object(bs, "TITEL_OVERRIDE", "Tagesbriefing Samstag 10.10."), \
                mock.patch.object(core, "pruefe_claude_anmeldung", return_value={"ok": True}), \
                mock.patch.object(core, "_read_real_5h_usage", return_value=None), \
                mock.patch.object(core, "run_briefing_via_claude_cli_chunked", side_effect=self._fake_bau), \
                mock.patch.object(reader_upload, "is_logged_in", return_value=True), \
                mock.patch.object(reader_upload, "upload_briefing_epub", side_effect=self._fake_upload), \
                mock.patch.object(reader_upload, "upload_briefing_txt", verboten), \
                mock.patch.object(newsletter_fetch, "neue_ausgaben", return_value={"neu": []}), \
                mock.patch.object(feedly_fetch, "load_pending",
                                  return_value={"entry_ids": [], "user_id": ""}), \
                mock.patch.object(feedly_fetch, "mark_done", verboten), \
                mock.patch.object(podcast_verbaut, "merken", return_value=None), \
                mock.patch.object(wa_runde, "nach_briefing", return_value="aus (Test)"):
            bs.main()

        titel = "Tagesbriefing Samstag 10.10. · Intelligent 🧵"
        self.assertEqual(self.hochgeladen, [titel])
        for ordner in (self.archiv_texte, self.spiegel_texte):
            with open(os.path.join(ordner, TXT_NAME), encoding="utf-8") as f:
                self.assertEqual(f.readline().strip(), titel, ordner)


if __name__ == "__main__":
    unittest.main()
