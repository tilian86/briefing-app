"""Auto-Repair trifft denselben Beitrag, den der Plausi-Check bemängelt.

Offline: `_run_claude_cli_subprocess_streaming` ist gefälscht, es läuft keine
Claude-CLI und kein Briefing. Aufruf aus dem Repo-Ordner:

    python3 -m unittest tests.test_plausi_repair_index
"""
import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import briefing_core as bc  # noqa: E402

FAKE_CLI = "/nicht/vorhanden/claude"


def _beitrag(titel: str, label: str, typ: str = "article", **extra) -> dict:
    s = {"type": typ, "source_label": label,
         "content": f"### {titel}\n*Einordnung.*\nText zu {titel}.\n#### Was bleibt:\n- Punkt\nWeiter geht's."}
    s.update(extra)
    return s


def _briefing():
    """Verbatim-Podcast VOR den geprüften Beiträgen — genau der Fall aus dem Bug."""
    return [
        {"type": "transition", "content": "Willkommen."},
        _beitrag("Podcast im Original", "Lage der Nation", typ="podcast", _verbatim=True),
        _beitrag("Bahnstreik", "Tagesschau"),
        _beitrag("Zinsentscheid", "Handelsblatt"),
        _beitrag("Rückblick", "Briefing", _recap=True),
    ]


def _ergebnis(stdout: str) -> dict:
    return {"ok": True, "stdout": stdout, "stderr": "", "elapsed": 0.1, "returncode": 0}


def _fake_check(_cmd, stdin_text, **_kw):
    # Claude nummeriert so, wie der Check die Beiträge zeigt: Tagesschau bemängeln.
    nr = int(re.search(r"=== BEITRAG (\d+) \(Quelle: Tagesschau\) ===", stdin_text).group(1))
    return _ergebnis(json.dumps({"items": [
        {"section_index": nr, "label": "Bahnstreik", "level": "warn",
         "summary": "Zahl falsch", "hard_issues": ["Streik dauert 3 statt 5 Tage"]},
    ], "typos": []}))


class _FakeRepair:
    def __init__(self):
        self.payload = None

    def __call__(self, _cmd, stdin_text, **_kw):
        start = stdin_text.index("[", stdin_text.index("ZU REPARIERENDE BEITRÄGE"))
        self.payload = json.loads(stdin_text[start:])
        return _ergebnis(json.dumps({"repaired": [
            {"section_index": p["section_index"],
             "content": f"### REPARIERT {p['label']}\n*Einordnung.*\nNeu.\n#### Was bleibt:\n- x\nWeiter geht's."}
            for p in self.payload
        ]}))


class PlausiRepairIndexTest(unittest.TestCase):
    def test_filter_gleich(self):
        sections = _briefing()
        pruefbar = bc._plausi_pruefbare_sections(sections)
        self.assertEqual([s["source_label"] for s in pruefbar], ["Tagesschau", "Handelsblatt"])

    def test_repair_trifft_bemaengelten_beitrag(self):
        sections = _briefing()
        with mock.patch.object(bc, "_run_claude_cli_subprocess_streaming", side_effect=_fake_check):
            check = bc.run_content_check_via_claude_cli("QUELLEN", sections, cli_path=FAKE_CLI)
        self.assertTrue(check["ok"], check.get("error"))
        self.assertEqual(check["warnings"], 1)

        fake = _FakeRepair()
        with mock.patch.object(bc, "_run_claude_cli_subprocess_streaming", side_effect=fake):
            rep = bc.run_briefing_repair_via_claude_cli(
                "QUELLEN", sections, output_lint=None, content_check=check, cli_path=FAKE_CLI)
        self.assertTrue(rep["ok"], rep.get("error"))
        self.assertEqual([p["label"] for p in fake.payload], ["Tagesschau"])
        self.assertEqual(fake.payload[0]["content_issues"], ["Zahl falsch — Streik dauert 3 statt 5 Tage"])

        neu = rep["sections"]
        self.assertEqual(rep["repaired_count"], 1)
        self.assertIn("REPARIERT Tagesschau", neu[2]["content"])
        self.assertEqual(neu[1]["content"], sections[1]["content"])  # Verbatim-Podcast unangetastet
        self.assertEqual(neu[3]["content"], sections[3]["content"])

    def test_lint_index_wird_umgerechnet(self):
        # output_lint zählt 0-basiert über ALLE Sections.
        sections = _briefing()
        lint = {"items": [
            {"severity": "warning", "section_index": 3, "label": "Zinsentscheid", "message": "Titel fehlt"},
            {"severity": "warning", "section_index": 1, "label": "Podcast", "message": "verbatim"},
            {"severity": "warning", "section_index": 4, "label": "Rückblick", "message": "recap"},
            {"severity": "warning", "section_index": -1, "label": "Vollständigkeit", "message": "leer"},
        ]}
        fake = _FakeRepair()
        with mock.patch.object(bc, "_run_claude_cli_subprocess_streaming", side_effect=fake):
            rep = bc.run_briefing_repair_via_claude_cli(
                "QUELLEN", sections, output_lint=lint, content_check=None, cli_path=FAKE_CLI)
        self.assertTrue(rep["ok"], rep.get("error"))
        self.assertEqual([(p["section_index"], p["label"]) for p in fake.payload], [(2, "Handelsblatt")])
        self.assertEqual(fake.payload[0]["lint_issues"], ["Zinsentscheid: Titel fehlt"])
        self.assertIn("REPARIERT Handelsblatt", rep["sections"][3]["content"])
        self.assertEqual(rep["sections"][1]["content"], sections[1]["content"])


if __name__ == "__main__":
    unittest.main()
