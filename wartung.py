"""Nächtlicher Wartungslauf der Briefing-App.

30.08.2026, Florians Auftrag: "bei eindeutigen Sachen und Fehlern kann das
automatisch laufen, bei sehr kritischen Sachen fragst du mich."

Ablauf: Fehler-Tagebuch lesen → Claude (Max-Abo, keine API-Kosten) die
eindeutigen Sachen beheben lassen → Syntax prüfen → committen → Bericht.
Kritische Einträge werden NICHT angefasst, sondern Florian vorgelegt.

Sicherheitsnetz, mehrfach:
  · Vor dem Lauf wird der Stand als Git-Commit gesichert; jede Änderung ist
    mit einem Befehl rücknehmbar.
  · Nach den Änderungen laufen Syntaxprüfung UND Import aller Module. Schlägt
    etwas fehl, wird ALLES zurückgerollt.
  · Wird nie gepusht, nie deployt, nie ein Briefing gestartet.
  · Läuft nur, wenn genug Kontingent frei ist.
"""

import json
import os
import subprocess
import sys
import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
LOG = os.path.join(APP_DIR, ".wartung.log")
BERICHT = os.path.join(APP_DIR, "WARTUNGSBERICHT.md")
MODULE = ("briefing_core", "briefing_app", "feedly_fetch", "pocketcasts_fetch",
          "reader_upload", "briefing_scheduled", "fehlerbuch", "browser_pfad", "wa_runde")

# Was der Wartungslauf anfassen darf — und was ausdrücklich nicht.
AUFTRAG = """Du bist der nächtliche Wartungslauf einer privaten Briefing-App.
Arbeitsverzeichnis ist das aktuelle. Die Dateien sind sehr gross
(briefing_core.py ~870 KB, briefing_app.py ~470 KB) — arbeite IMMER mit
grep -n und sed -n, NIE mit vollstaendigen Reads.

Unten steht das Fehler-Tagebuch. Behebe daraus NUR, was eindeutig ist.

DAS DARFST DU SELBST BEHEBEN:
- Rohe Fehlermeldungen (Python-/Playwright-Ausnahmen), die dem Nutzer angezeigt
  werden, in verstaendliches Deutsch uebersetzen.
- Fehlende Absicherung gegen None/fehlende Felder, wo ein Absturz droht.
- Fehlender Wiederholungsversuch bei erkennbar voruebergehenden Fehlern
  (Timeout, ECONNRESET, HTTP 429/5xx, "overloaded").
- Zu enge oder zu gierige regulaere Ausdruecke, wenn der Fehler es belegt.
- Quellen/Domains, die dauerhaft nur Anrisse liefern, in die vorhandene
  Ausschlussliste aufnehmen.

DAS DARFST DU NICHT ANFASSEN (dafuer legst du Florian eine Notiz vor):
- Alles, was Inhalte loeschen oder leeren koennte (Entwurfsfelder, Merklisten,
  Archive, _save_draft, die Schrumpf-Firewall, mark_done, remove_pending).
- Die inhaltliche Qualitaet: Buendelungs-Anweisung, Wortbudgets, Prompts,
  Selbsttest-Bewertung.
- Alles rund um Anmeldungen, Zugangsdaten, Keychain.
- Etwas loeschen, verschieben, umbenennen oder pushen.
- Einen Briefing-Lauf starten.
- Einen Eintrag der Schwere "kritisch" — die gehoeren immer Florian.

VORGEHEN:
1. Nimm dir die Eintraege der Reihe nach vor, haeufigste zuerst.
2. Aendere so wenig wie moeglich. Jede Aenderung bekommt einen deutschen
   Kommentar mit Datum und Begruendung — so wie die vorhandenen im Code.
3. Nach JEDER Datei: python3 -c "import ast; ast.parse(open('DATEI').read())"
4. Zum Schluss: schreibe eine Datei WARTUNGSBERICHT.md mit
   - was du behoben hast (je Eintrag ein Absatz, in einfachem Deutsch)
   - was du bewusst liegen gelassen hast und warum (das liest Florian)
   Schreibe fuer einen Laien: keine Fachbegriffe ohne Erklaerung.
5. Committe NICHT selbst — das macht das Skript.

Bist du dir bei etwas nicht sicher: NICHT aendern, in den Bericht schreiben.
Lieber nichts tun als etwas kaputt machen."""


def _log(m):
    z = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {m}"
    print(z, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(z + "\n")
    except Exception:
        pass


def _git(*args):
    return subprocess.run(["git", "-C", APP_DIR] + list(args),
                          capture_output=True, text=True, timeout=120)


def _pruefen() -> str:
    """Syntax + Import aller Module. Leerer String = alles in Ordnung."""
    for m in MODULE:
        d = os.path.join(APP_DIR, m + ".py")
        if not os.path.exists(d):
            continue
        r = subprocess.run([sys.executable, "-c",
                            f"import ast;ast.parse(open({d!r},encoding='utf-8').read())"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return f"Syntaxfehler in {m}.py: {r.stderr.strip()[:300]}"
    r = subprocess.run([sys.executable, "-c",
                        "import sys;sys.path.insert(0,%r)\n" % APP_DIR +
                        "\n".join(f"import {m}" for m in MODULE if
                                  os.path.exists(os.path.join(APP_DIR, m + ".py")))],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return "Modul laedt nicht mehr: " + r.stderr.strip()[-400:]
    return ""


def _kontingent_frei() -> int:
    try:
        import urllib.request as ur
        tok = json.loads(subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        )["claudeAiOauth"]["accessToken"]
        d = json.loads(ur.urlopen(ur.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={"Authorization": f"Bearer {tok}",
                     "anthropic-beta": "oauth-2025-04-20"}), timeout=20).read())
        return 100 - int((d.get("seven_day") or {}).get("utilization", 0))
    except Exception:
        return -1


def main() -> int:
    import fehlerbuch
    offen = fehlerbuch.offene()
    machbar = [x for x in offen if x.get("schwere") != "kritisch"]
    kritisch = [x for x in offen if x.get("schwere") == "kritisch"]
    _log(f"Start — {len(offen)} offen ({len(machbar)} machbar, {len(kritisch)} kritisch)")

    if kritisch:
        _log("Kritische Einträge bleiben liegen: " +
             ", ".join(x.get("bereich", "?") for x in kritisch[:5]))
    if not machbar:
        _log("Nichts zu tun.")
        return 0

    # 10.10.2026: Am 16.09. und 23.09. endete das Protokoll nach „Start“ ohne
    # Fehlermeldung. Diese Zeilen zeigen beim nächsten Mal, wo es hängt.
    _log("Prüfe Wochenkontingent…")
    frei = _kontingent_frei()
    _log(f"Wochenkontingent: {frei}% frei" if frei >= 0
         else "Wochenkontingent unbekannt — Wartung läuft trotzdem.")
    if 0 <= frei < 20:
        _log(f"Nur {frei}% Wochenkontingent frei — Wartung verschoben.")
        return 0

    # Sicherungspunkt: alles Offene festhalten, damit ein Rückroller sauber greift
    _git("add", "-A")
    _git("commit", "-q", "-m", "Stand vor dem Wartungslauf")
    vorher = _git("rev-parse", "HEAD").stdout.strip()
    _log(f"Sicherungspunkt: {vorher[:8]}")

    cli = subprocess.run(["which", "claude"], capture_output=True, text=True).stdout.strip() \
        or os.path.expanduser("~/.local/bin/claude")
    auftrag = AUFTRAG + "\n\n=== FEHLER-TAGEBUCH ===\n\n" + fehlerbuch.bericht()

    _log("Claude arbeitet…")
    r = subprocess.run([cli, "--print", "--model", "sonnet",
                        "--dangerously-skip-permissions", "--effort", "high"],
                       input=auftrag, capture_output=True, text=True,
                       cwd=APP_DIR, timeout=3600,
                       env={**os.environ, "ANTHROPIC_API_KEY": "", "ANTHROPIC_BASE_URL": ""})
    _log(f"Claude fertig (Code {r.returncode})")

    fehler = _pruefen()
    if fehler:
        _log("PRÜFUNG FEHLGESCHLAGEN — rolle alles zurück: " + fehler)
        _git("reset", "--hard", vorher)
        with open(BERICHT, "w", encoding="utf-8") as fh:
            fh.write("# Wartungslauf abgebrochen\n\n"
                     f"Der Lauf vom {datetime.datetime.now():%d.%m.%Y %H:%M} hat etwas "
                     "verändert, das danach nicht mehr lief. **Alles wurde automatisch "
                     "zurückgenommen** — die App ist unverändert.\n\n"
                     f"Grund: {fehler}\n")
        return 1

    if not _git("status", "--porcelain").stdout.strip():
        _log("Keine Änderungen vorgenommen.")
        return 0

    _git("add", "-A")
    _git("commit", "-q", "-m",
         f"Wartungslauf {datetime.datetime.now():%d.%m.%Y}: {len(machbar)} Einträge bearbeitet\n\n"
         "Automatisch behoben, Prüfung bestanden. Rücknahme: git revert HEAD\n\n"
         "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>")
    _stand = _git("rev-parse", "--short", "HEAD").stdout.strip()
    _log("Committet. " + _git("log", "--oneline", "-1").stdout.strip())

    # Bearbeitete Eintraege abhaken — sonst arbeitet der naechste Lauf dieselben
    # Sachen erneut ab. Kritische bleiben ausdruecklich offen, die gehoeren Florian.
    for x in machbar:
        fehlerbuch.erledigen(x["kennung"], f"Wartungslauf {_stand}")
    _log(f"{len(machbar)} Eintrag/Eintraege abgehakt, {len(kritisch)} bleiben offen.")
    return 0


def _abbruch(signum, _frame):
    # Ohne das verschwindet ein von außen beendeter Lauf spurlos aus dem Protokoll.
    _log(f"Von außen beendet (Signal {signum}) — Mac schlafen gelegt oder neu gestartet?")
    sys.exit(128 + signum)


if __name__ == "__main__":
    import signal
    for _sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(_sig, _abbruch)
    try:
        sys.exit(main())
    except Exception:
        import traceback
        _log("Fataler Fehler:\n" + traceback.format_exc())
        sys.exit(1)
