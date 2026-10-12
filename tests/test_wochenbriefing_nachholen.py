"""Wochenbriefing: Wiederholen im Lauf, Nachholen in den Naechten, genau eine Push.

Komplett offline: Es laeuft KEINE Claude-CLI, kein echtes Briefing, kein
WhatsApp, kein Pushfunk, kein pmset und kein Fehlerbuch-Eintrag im echten Buch.
Alles Teure wird durch Attrappen ersetzt, alle Dateien liegen in einem
Wegwerf-Ordner. Aufruf aus dem Repo-Ordner:

    python3 -m unittest tests.test_wochenbriefing_nachholen

Der Kalender (2026): So 18.10. (erster Sonntag mit Nachholen), Mo 19., Di 20.,
Mi 21. = letzte Nacht. So 11.10. ist der ausgefallene Lauf, der NICHT nachgebaut
werden soll.
"""
import datetime
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wochenbriefing_lauf as wl  # noqa: E402

SO = datetime.datetime(2026, 10, 18, 19, 10)
MO = datetime.datetime(2026, 10, 19, 3, 20)
DI = datetime.datetime(2026, 10, 20, 3, 20)
MI = datetime.datetime(2026, 10, 21, 3, 20)
DO = datetime.datetime(2026, 10, 22, 3, 20)
ALT_SO = datetime.datetime(2026, 10, 11, 19, 10)   # der ausgefallene Sonntag
ALT_DI = datetime.datetime(2026, 10, 13, 3, 20)    # Nachholzeit NACH dem ausgefallenen Sonntag


def _fehlschlag(nr_text="boom", diag="CLI-Versuch 1 (a): FEHLER, Exit 1 | CLI-Ausgabe: kaputt"):
    return {"ok": False, "fehler": nr_text, "diagnose": diag, "wiederholen": True}


def _erfolg():
    return {"ok": True, "r": {"ok": True, "txt_path": None, "pdf_path": None, "stats": {}}, "tage": 7}


class Basis(unittest.TestCase):
    """Wegwerf-Ordner + alle Nebenwirkungen als Attrappen."""

    def setUp(self):
        self.echt_abschliessen = wl._abschliessen      # Original, bevor die Attrappe es ersetzt
        self.tmp = tempfile.mkdtemp(prefix="wbtest_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        pfad = lambda n: os.path.join(self.tmp, n)  # noqa: E731
        for name, wert in {
            "STATUS_PATH": pfad("status.json"), "ZUSTAND_PATH": pfad("zustand.json"),
            "DRAFT_PATH": pfad("draft.json"), "LOG_PATH": pfad("log.txt"),
            "SPIEGEL": pfad("spiegel"), "ARCHIVE": pfad("archiv"), "CLI_ORDNER": pfad("cli"),
            "PAUSE_SEKUNDEN": 0, "ERZWINGEN": False,
        }.items():
            p = mock.patch.object(wl, name, wert)
            p.start()
            self.addCleanup(p.stop)

        self.versuche = []          # (nr) je Bauversuch
        self.fehlerbuch = []        # (bereich, meldung, schwere)
        self.abgehakt = []
        self.pushes = []
        self.wecken = []
        self.abbestellt = []
        self.abgeschlossen = []
        self.letztes = None         # was _letztes_wochenbriefing meldet
        self.ablauf = []            # Liste von Ergebnissen fuer die Bauversuche (der Reihe nach)

        def bauversuch(nr, anzahl, grund, modus):
            self.versuche.append((nr, modus))
            return self.ablauf.pop(0) if self.ablauf else _fehlschlag()

        def abschliessen(r, heute, grund, tage, modus):
            self.abgeschlossen.append((heute, modus))
            self.letztes = heute        # wie der echte Lauf: Entwurf-Datum wird gesetzt
            return {"titel": "Wochenbriefing bis %s" % heute.strftime("%d.%m."), "upload": "ok"}

        for name, ersatz in {
            "_bauversuch": bauversuch,
            "_abschliessen": abschliessen,
            "_letztes_wochenbriefing": lambda: self.letztes,
            "_fehlerbuch": lambda bereich, meldung, schwere="normal", kontext=None:
                self.fehlerbuch.append((bereich, meldung, schwere)),
            "_fehlerbuch_abhaken": lambda notiz: self.abgehakt.append(notiz),
            "_push_senden": lambda titel, text, jetzt: (self.pushes.append((titel, text, jetzt)) or True),
            "_wecken_vormerken": lambda start: (self.wecken.append(start) or start.strftime("W:%d.%m.%H:%M")),
            "_wecken_abbestellen": lambda w: self.abbestellt.append(w),
        }.items():
            p = mock.patch.object(wl, name, ersatz)
            p.start()
            self.addCleanup(p.stop)

    def zustand(self):
        return wl._zustand_lesen()

    def status(self):
        try:
            import json
            with open(wl.STATUS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return None


class TestWiederholenImLauf(Basis):
    def test_versuch_1_scheitert_versuch_2_klappt(self):
        self.ablauf = [_fehlschlag("Idle-Abbruch"), _erfolg()]
        wl.main(jetzt=SO)
        self.assertEqual([v[0] for v in self.versuche], [1, 2])          # nicht 3: fertig ist fertig
        self.assertEqual(len(self.abgeschlossen), 1)
        self.assertEqual(self.zustand()["status"], "fertig")
        self.assertEqual(self.pushes, [], "bei Erfolg keine Push")
        self.assertEqual(self.wecken, [], "bei Erfolg keine Weckzeit")
        # Der Fehlschlag steht kritisch im Fehlerbuch — mit der CLI-Ausgabe
        fehler = [f for f in self.fehlerbuch if "fehlgeschlagen" in f[1]]
        self.assertEqual(len(fehler), 1)
        self.assertEqual(fehler[0][2], "kritisch")
        self.assertIn("CLI-Ausgabe: kaputt", fehler[0][1])
        self.assertIn("Idle-Abbruch", fehler[0][1])
        # ... und wird nach dem Erfolg abgehakt
        self.assertEqual(len(self.abgehakt), 1)
        self.assertIn("Versuch 2/3", self.abgehakt[0])

    def test_alle_drei_scheitern_am_sonntag_nachholen_wird_vorgemerkt(self):
        self.ablauf = [_fehlschlag("a"), _fehlschlag("b"), _fehlschlag("c")]
        wl.main(jetzt=SO)
        self.assertEqual([v[0] for v in self.versuche], [1, 2, 3])
        self.assertEqual(self.abgeschlossen, [])
        z = self.zustand()
        self.assertEqual(z["status"], "offen")
        self.assertEqual(z["sonntag"], "2026-10-18")
        self.assertEqual(len(z["versuche"]), 3)
        self.assertEqual(self.wecken, [datetime.datetime(2026, 10, 19, 3, 20)], "Weckzeit fuer Montag 03:20")
        self.assertEqual(z["weckzeit"], "W:19.10.03:20")
        self.assertEqual(self.pushes, [], "Sonntag allein loest KEINE Push aus")
        self.assertEqual(len([f for f in self.fehlerbuch if f[2] == "kritisch"]), 3, "jeder Fehlschlag steht im Buch")
        self.assertIn("nächster Versuch Mo 03:20", self.status()["fehler"])

    def test_keine_tagestexte_wird_nicht_wiederholt(self):
        self.ablauf = [{"ok": False, "fehler": "Keine Tagesbriefing-Texte", "diagnose": "",
                        "wiederholen": False, "keine_daten": True, "tage": 14}]
        wl.main(jetzt=SO)
        self.assertEqual(len(self.versuche), 1)
        self.assertEqual(self.zustand()["status"], "keine_daten")
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.wecken, [])


class TestNachholen(Basis):
    def test_montag_holt_nach_wenn_sonntag_offen(self):
        wl._zustand_schreiben({"sonntag": "2026-10-18", "status": "offen", "versuche": [], "push": False})
        self.ablauf = [_erfolg()]
        wl.main(jetzt=MO)
        self.assertEqual(self.versuche, [(1, "nachholen")])
        self.assertEqual(self.zustand()["status"], "fertig")
        self.assertEqual(self.pushes, [])

    def test_nachholen_mit_fertigem_wochenbriefing_tut_nichts(self):
        self.letztes = datetime.date(2026, 10, 18)         # Sonntag hat geklappt
        wl.main(jetzt=DI)
        self.assertEqual(self.versuche, [], "kein Bauversuch")
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.wecken, [])
        self.assertEqual(self.fehlerbuch, [])
        self.assertIsNone(self.status(), "Status der App bleibt unberuehrt")
        self.assertFalse(os.path.exists(wl.ZUSTAND_PATH), "keine Spur im Merkzettel")

    def test_von_hand_gebaut_schliesst_merkzettel_und_gibt_weckzeit_zurueck(self):
        wl._zustand_schreiben({"sonntag": "2026-10-18", "status": "offen", "versuche": [],
                               "push": False, "weckzeit": "W:20.10.03:18"})
        self.letztes = datetime.date(2026, 10, 19)          # Florian hat Montag selbst gebaut
        wl.main(jetzt=DI)
        self.assertEqual(self.versuche, [])
        self.assertEqual(self.zustand()["status"], "fertig")
        self.assertEqual(self.abbestellt, ["W:20.10.03:18"])

    def test_ausgefallener_sonntag_vom_11_10_wird_nicht_nachgebaut(self):
        self.letztes = datetime.date(2026, 10, 4)           # vorletzte Woche war das letzte
        for wann in (ALT_DI, datetime.datetime(2026, 10, 14, 3, 20), datetime.datetime(2026, 10, 15, 3, 20)):
            wl.main(jetzt=wann)
        self.assertEqual(self.versuche, [], "kein Neubau fuer 11.10.")
        self.assertEqual(self.pushes, [])
        # Aber der NAECHSTE Sonntag wird ganz normal gebaut:
        self.ablauf = [_erfolg()]
        wl.main(jetzt=SO)
        self.assertEqual(len(self.versuche), 1)

    def test_alter_sonntag_ist_im_entscheiden_aus(self):
        bauen, grund, so = wl._entscheiden(ALT_DI.date(), datetime.date(2026, 10, 4), {})
        self.assertFalse(bauen)
        self.assertEqual(so, datetime.date(2026, 10, 11))
        self.assertIn("11.10.", grund)


class TestLetzteNachtUndPush(Basis):
    def test_letzte_nacht_scheitert_genau_eine_push(self):
        # Sonntag + Montag + Dienstag gescheitert, dann Mittwoch ebenfalls
        for wann in (SO, MO, DI):
            self.ablauf = [_fehlschlag()] * 3
            wl.main(jetzt=wann)
        self.assertEqual(self.pushes, [], "bis Dienstag keine Push")
        self.assertEqual(self.zustand()["status"], "offen")

        self.ablauf = [_fehlschlag("Idle-Abbruch")] * 3
        wl.main(jetzt=MI)
        self.assertEqual(len(self.pushes), 1, "genau EINE Push")
        titel, text, wann = self.pushes[0]
        self.assertIn("Wochenbriefing", titel)
        self.assertIn("Idle-Abbruch", text)
        self.assertEqual(wann, MI)                          # Zeitpunkt wird an _push_senden uebergeben (-> 08:00)
        z = self.zustand()
        self.assertEqual(z["status"], "aufgegeben")
        self.assertTrue(z["push"])
        kritisch = [f for f in self.fehlerbuch if f[2] == "kritisch" and "bitte klaeren" in f[1]]
        self.assertEqual(len(kritisch), 1, "ein kritischer Eintrag 'bitte klaeren'")
        self.assertEqual(self.wecken, [datetime.datetime(2026, 10, 19, 3, 20), datetime.datetime(2026, 10, 20, 3, 20),
                                       datetime.datetime(2026, 10, 21, 3, 20)],
                         "Weckzeiten nur nach So/Mo/Di; nach der letzten Nacht keine mehr")

        # Danach ist Schluss: Donnerstag und ein zweiter Mittwochslauf tun nichts, keine zweite Push
        self.ablauf = [_fehlschlag()] * 3
        n_versuche = len(self.versuche)
        wl.main(jetzt=DO)
        wl.main(jetzt=MI)
        self.assertEqual(len(self.versuche), n_versuche)
        self.assertEqual(len(self.pushes), 1)

    def test_push_wird_nie_doppelt_gesendet_auch_bei_erneutem_mittwochslauf(self):
        wl._zustand_schreiben({"sonntag": "2026-10-18", "status": "offen", "versuche": [], "push": True})
        self.ablauf = [_fehlschlag()] * 3
        wl.main(jetzt=MI)
        self.assertEqual(self.pushes, [])

    def test_mittwoch_klappt_keine_push(self):
        wl._zustand_schreiben({"sonntag": "2026-10-18", "status": "offen", "versuche": [], "push": False})
        self.ablauf = [_fehlschlag(), _erfolg()]
        wl.main(jetzt=MI)
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.zustand()["status"], "fertig")

    def test_push_zeit_nie_nachts(self):
        self.assertEqual(wl._push_zeit(datetime.datetime(2026, 10, 21, 3, 50)), "2026-10-21 08:00")
        self.assertEqual(wl._push_zeit(datetime.datetime(2026, 10, 21, 7, 59)), "2026-10-21 08:00")
        self.assertIsNone(wl._push_zeit(datetime.datetime(2026, 10, 21, 8, 30)))     # tagsueber sofort
        self.assertEqual(wl._push_zeit(datetime.datetime(2026, 10, 21, 22, 30)), "2026-10-22 08:00")


class TestPushAufruf(unittest.TestCase):
    def test_push_wird_an_pushfunk_mit_uhrzeit_uebergeben(self):
        aufrufe = []

        def fake_run(args, **kw):
            aufrufe.append(args)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(wl.subprocess, "run", fake_run), mock.patch.object(wl, "_log", lambda *_: None):
            ok = wl._push_senden("⚠️ Wochenbriefing", "Text", datetime.datetime(2026, 10, 21, 4, 0))
        self.assertTrue(ok)
        self.assertEqual(len(aufrufe), 1)
        self.assertEqual(aufrufe[0][1:3], ["pushfunk", "⚠️ Wochenbriefing"])
        self.assertEqual(aufrufe[0][-2:], ["--um", "2026-10-21 08:00"])


class TestZustellung(Basis):
    """Nichts geht von allein an Menschen — auch nicht im Nachhol-Lauf."""

    def test_begleittext_wird_nur_vorbereitet_nie_automatisch_gesendet(self):
        aufrufe = []
        fake_wa = types.ModuleType("wa_runde")
        fake_wa.nach_briefing = lambda pdf, txt, auto=False, log=None, nachts_bis=None: (
            aufrufe.append({"pdf": pdf, "auto": auto}) or "vorbereitet")
        fake_up = types.ModuleType("reader_upload")
        fake_up.upload_briefing_txt = lambda pfad, titel: {"ok": True}
        fake_up.cleanup_old_wochenbriefings = lambda tage: {"deleted": [], "errors": []}
        txt = os.path.join(self.tmp, "w.txt")
        with open(txt, "w") as f:
            f.write("x")
        r = {"txt_path": txt, "pdf_path": os.path.join(self.tmp, "w.pdf"), "stats": {}, "elapsed_seconds": 5}
        with mock.patch.dict(sys.modules, {"wa_runde": fake_wa, "reader_upload": fake_up}):
            self.echt_abschliessen(r, datetime.date(2026, 10, 20), "Nachholen", 7, "nachholen")
        self.assertEqual(len(aufrufe), 1)
        self.assertIs(aufrufe[0]["auto"], False)
        self.assertIs(wl.WA_AUTO, False)


class TestKernDiagnose(unittest.TestCase):
    def test_cli_ausgabe_ende_kuerzt_auf_die_letzten_zeilen(self):
        import briefing_core as bc
        text = "\n".join("Zeile %d" % i for i in range(1, 40)) + "\n\n"
        ende = bc.cli_ausgabe_ende(text, zeilen=3)
        self.assertEqual(ende, "Zeile 37\nZeile 38\nZeile 39")
        self.assertEqual(bc.cli_ausgabe_ende(""), "")
        self.assertEqual(bc.cli_ausgabe_ende(None), "")
        lang = bc.cli_ausgabe_ende("x" * 5000, zeichen=100)
        self.assertLessEqual(len(lang), 101)
        self.assertTrue(lang.startswith("…"))


if __name__ == "__main__":
    unittest.main()
