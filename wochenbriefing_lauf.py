#!/usr/bin/env python3
"""Headless-Runner fuer das WOCHENBRIEFING — laeuft jeden Sonntag von allein.

14.09.: Bis hierher gab es das Wochenbriefing nur als Knopf in der Streamlit-App.
`briefing_scheduled.py` kannte es gar nicht — kein einziger naechtlicher Lauf hat
also jemals eins gebaut. Ergebnis: Florians letztes Wochenbriefing war vom 30.08.
und damit zwei Wochen alt, ohne dass irgendwo sichtbar wurde, warum. Dieser
Runner schliesst die Luecke: launchd startet ihn sonntags abends, er baut das
Wochen-Meta aus den Tagestexten der letzten sieben Tage und laedt es in den
ElevenReader.

Sonntag deshalb, weil Florian das am 05.08. schon einmal so gewuenscht hat
(siehe briefing_app.py, "alle 7 Tage, am liebsten sonntags").

Laeuft komplett ueber das Max-Abo (Claude CLI) — keine API-Kosten.

Von Hand starten:  python3 wochenbriefing_lauf.py            (haelt sich an die Wochenregel)
                   BRIEFING_WOCHE_ERZWINGEN=1 python3 …      (baut sofort)
"""
import datetime
import json
import os
import re
import sys
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

DRAFT_PATH = os.path.join(APP_DIR, ".briefing_draft.json")
STATUS_PATH = os.path.join(APP_DIR, ".wochenbriefing_status.json")
LOG_PATH = os.path.expanduser("~/Library/Logs/wochenbriefing.log")
ARCHIVE = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Briefings")
SPIEGEL = os.path.expanduser("~/.briefing_meta_mirror")
ERZWINGEN = bool(os.environ.get("BRIEFING_WOCHE_ERZWINGEN"))

_DATEI_DATUM = re.compile(r"(\d{4})-(\d{2})-(\d{2}).*wochenbriefing", re.I)


def _log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def _status_schreiben(**felder):
    felder.setdefault("stand", datetime.datetime.now().isoformat())
    try:
        with open(STATUS_PATH, "w", encoding="utf-8") as f:
            json.dump(felder, f, ensure_ascii=False)
    except Exception as e:
        _log("Status schreiben fehlgeschlagen: %s" % e)


def _entwurf_lesen() -> dict:
    try:
        with open(DRAFT_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _entwurf_datum_merken(wann: datetime.datetime) -> None:
    """Traegt den Bau-Zeitpunkt in den Entwurf ein.

    Daran haengt die Erinnerung in der App ("Wochenbriefing faellig, das letzte
    war vor X Tagen"). Ohne diesen Eintrag wuerde sie Florian jeden Tag fragen,
    obwohl laengst automatisch eins gebaut wird. Lesen-Aendern-Schreiben ueber
    eine Zwischendatei, damit ein Absturz den Entwurf nicht zerreisst."""
    try:
        entwurf = _entwurf_lesen()
        if not entwurf:
            return
        entwurf["last_meta_created_iso"] = wann.isoformat()
        tmp = DRAFT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entwurf, f, ensure_ascii=False)
        os.replace(tmp, DRAFT_PATH)
    except Exception as e:
        _log("Entwurf-Datum konnte nicht gesetzt werden: %s" % e)


def _letztes_wochenbriefing():
    """Datum des juengsten Wochenbriefings — oder None.

    Drei Quellen, weil keine allein verlaesslich ist: der Entwurf (den schreiben
    App und dieser Runner), der lokale Spiegel und das iCloud-Archiv. Letzteres
    kann ein launchd-Hintergrunddienst oft gar nicht auflisten, deshalb ist es
    nur die Zugabe, nie die Grundlage."""
    kandidaten = []
    iso = (_entwurf_lesen().get("last_meta_created_iso") or "").strip()
    if iso:
        try:
            kandidaten.append(datetime.datetime.fromisoformat(iso).date())
        except Exception:
            pass
    for basis in (SPIEGEL, ARCHIVE):
        ordner = os.path.join(basis, "Wochen-Briefings")
        try:
            namen = os.listdir(ordner)
        except Exception:
            continue
        for name in namen:
            m = _DATEI_DATUM.search(name)
            if not m:
                continue
            try:
                kandidaten.append(datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                continue
    return max(kandidaten) if kandidaten else None


def _faellig(heute: datetime.date, letztes):
    """(bauen?, Begruendung) — Sonntagsregel mit Nachhol-Fenster.

    Sonntags wird gebaut, ausser es steht schon eins von heute da. An allen
    anderen Tagen nur, wenn der Sonntag ausgefallen ist (Mac aus, Deckel zu) und
    das letzte laenger als eine Woche her ist — launchd holt verpasste Termine
    beim naechsten Aufwachen nach, und genau der Nachzuegler soll durchkommen."""
    if ERZWINGEN:
        return True, "erzwungen"
    if letztes is None:
        return True, "noch gar keins vorhanden"
    if heute.weekday() == 6:
        if letztes == heute:
            return False, "heute schon gebaut"
        return True, "Sonntag, letztes vom %s" % letztes.strftime("%d.%m.")
    alter = (heute - letztes).days
    if alter >= 7:
        return True, "Nachholer — letztes ist %d Tage her" % alter
    return False, "kein Sonntag und letztes erst %d Tage her" % alter


def main():
    _log("=== Wochenbriefing-Lauf gestartet ===")
    heute = datetime.date.today()
    letztes = _letztes_wochenbriefing()
    bauen, grund = _faellig(heute, letztes)
    if not bauen:
        _log("Nichts zu tun (%s)." % grund)
        _status_schreiben(gebaut=False, grund=grund,
                          letztes=(letztes.isoformat() if letztes else None))
        return

    _log("Bauen (%s)." % grund)
    import briefing_core as core

    cli = core._locate_claude_cli()
    if not cli:
        _log("Claude CLI nicht gefunden — Abbruch.")
        _status_schreiben(gebaut=False, fehler="Claude CLI nicht gefunden")
        _fehlerbuch("Wochenbriefing", "Claude CLI nicht gefunden — der Sonntagslauf konnte nichts bauen.", "kritisch")
        return

    # Erst anklopfen: ein abgelaufener Claude-Login laesst den Lauf sonst
    # minutenlang arbeiten und dann mit leerer Antwort sterben (11.09.).
    try:
        anm = core.pruefe_claude_anmeldung(cli_path=cli)
    except Exception as e:
        anm = {"ok": True, "meldung": "Vorabpruefung uebersprungen (%s)" % e}
    if not anm.get("ok"):
        _log("Claude-Anmeldung: %s" % anm.get("meldung"))
        _status_schreiben(gebaut=False, fehler=str(anm.get("meldung")))
        _fehlerbuch("Claude-Anmeldung", "Wochenbriefing abgebrochen: %s" % anm.get("meldung"), "kritisch")
        return

    modell = _entwurf_lesen().get("meta_cli_model") or "opus"
    _status_schreiben(gebaut=False, laeuft=True, schritt="Wird gestartet…", grund=grund)

    def _cb(schritt, anteil):
        try:
            _status_schreiben(gebaut=False, laeuft=True, schritt=str(schritt)[:160],
                              anteil=round(float(anteil), 3), grund=grund)
        except Exception:
            pass

    try:
        os.makedirs(ARCHIVE, exist_ok=True)
    except Exception:
        pass

    try:
        r = core.run_meta_briefing_via_claude_cli(
            archive_dir=ARCHIVE, days=7, model=modell, cli_path=cli, progress_callback=_cb)
    except Exception as e:
        _log("Bau-Ausnahme:\n" + traceback.format_exc())
        _status_schreiben(gebaut=False, fehler=str(e)[:200])
        _fehlerbuch("Wochenbriefing", "Bau abgestuerzt: %s" % str(e)[:200], "kritisch")
        return

    if r is None:
        _log("Keine Tagesbriefing-Texte der letzten 7 Tage gefunden.")
        _status_schreiben(gebaut=False, fehler="keine Tagesbriefings der letzten 7 Tage")
        _fehlerbuch("Wochenbriefing",
                    "Kein Wochenbriefing moeglich: im lokalen Spiegel liegt kein "
                    "einziger Tagestext der letzten 7 Tage.", "normal")
        return
    if not r.get("ok"):
        _log("Bau fehlgeschlagen: %s" % r.get("error"))
        _status_schreiben(gebaut=False, fehler=str(r.get("error"))[:200])
        _fehlerbuch("Wochenbriefing", "Bau fehlgeschlagen: %s" % str(r.get("error"))[:200], "kritisch")
        return

    titel = "Wochenbriefing bis %s" % heute.strftime("%d.%m.")
    hochgeladen = None
    txtp = r.get("txt_path")
    if txtp and os.path.exists(txtp):
        try:
            from reader_upload import upload_briefing_txt
            ur = upload_briefing_txt(txtp, titel)
            hochgeladen = ("ok: " + titel) if ur.get("ok") else ("fail: " + str(ur.get("error"))[:120])
        except Exception as e:
            hochgeladen = "fail: " + str(e)[:120]
        _log("Upload: %s" % hochgeladen)
        if str(hochgeladen).startswith("fail"):
            _fehlerbuch("Wochenbriefing",
                        "Gebaut, aber nicht im ElevenReader gelandet: %s" % hochgeladen[6:], "normal")
        else:
            # Aufraeumen erst NACH einem geglueckten Upload — sonst loescht ein
            # Lauf die alten Wochenbriefings weg und schiebt kein neues nach.
            # Ohne diesen Schritt waeren es nach einem Quartal dreizehn Eintraege:
            # das automatische Tages-Aufraeumen fasst Wochenbriefings mit Absicht
            # nicht an, sonst haette es sie schon frueher mitgerissen.
            try:
                from reader_upload import cleanup_old_wochenbriefings
                cl = cleanup_old_wochenbriefings(21)
                if cl.get("deleted"):
                    _log("Aufgeraeumt: %s" % ", ".join(cl["deleted"]))
                if cl.get("errors"):
                    _log("Aufraeumen unvollstaendig: %s" % cl["errors"])
            except Exception as e:
                _log("Aufraeumen fehlgeschlagen: %s" % e)

    jetzt = datetime.datetime.now()
    _entwurf_datum_merken(jetzt)
    stats = r.get("stats") or {}
    _status_schreiben(gebaut=True, titel=titel, grund=grund,
                      tage=stats.get("days"), briefings=stats.get("briefings"),
                      sekunden=int(r.get("elapsed_seconds") or 0),
                      txt=txtp, pdf=r.get("pdf_path"), upload=hochgeladen)
    _log("=== Fertig: %s aus %s Tagesbriefings in %ds, Upload=%s ==="
         % (titel, stats.get("briefings"), int(r.get("elapsed_seconds") or 0), hochgeladen))


def _fehlerbuch(bereich, meldung, schwere="normal"):
    try:
        import fehlerbuch
        fehlerbuch.eintragen(bereich, meldung, None, schwere)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as _fatal:
        _log("Fataler Fehler:\n" + traceback.format_exc())
        try:
            _status_schreiben(gebaut=False, fehler="abgestuerzt: %s" % str(_fatal)[:180])
            _fehlerbuch("Wochenbriefing", "Runner abgestuerzt: %s" % str(_fatal)[:180], "kritisch")
        except Exception:
            pass
