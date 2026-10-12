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

12.10.: NICHT MEHR EINFACH AUFGEBEN. Am 11.10. brach der Sonntagslauf mit
"Idle-Abbruch" ab und blieb tot; Florian: "Wenn das Wochen-Briefing nicht erstellt
wird, dann machst du das so lange, bis es erstellt wurde, oder klaerst das."
Seitdem:

  1. Im selben Lauf bis zu MAX_VERSUCHE (3) Baeuversuche mit PAUSE_SEKUNDEN (10 min)
     dazwischen. Jeder Fehlschlag schreibt die letzten Zeilen der CLI-Ausgabe
     ins Log UND ins Fehlerbuch (kritisch), damit die Ursache sichtbar wird.
     Ab dem 2. Versuch laeuft die CLI in einem lokalen Ordner statt im
     iCloud-Archiv (falls der Ordner der Haenger war).
  2. Klappt es am Sonntag gar nicht, holt derselbe launchd-Job es in den Naechten
     Mo/Di/Mi um 03:20 nach (plus pmset-Weckzeit 03:18, nur wenn wirklich etwas
     ansteht). Das Skript entscheidet selbst: ist die Woche schon fertig, endet
     es sofort ohne Spur.
  3. Ist die letzte Nacht (Mi) auch gescheitert: EINE Push an Florian (Pushfunk,
     bis 08:00 zurueckgehalten) + kritischer Fehlerbuch-Eintrag "bitte klaeren".
  4. Zustellung: Das Wochenbriefing geht NIE von allein an Menschen. Nach dem PDF
     passiert nur: ElevenReader-Upload (ok nachts), Aufraeumen alter Eintraege
     und ein vorbereiteter WhatsApp-BEGLEITTEXT (auto=False, nichts wird
     gesendet). Verschickt wird ausschliesslich per Knopf in der App.

Das ausgefallene Wochenbriefing vom 11.10. wird bewusst NICHT nachgebaut
(NACHHOLEN_AB_SONNTAG): Nachholen gilt erst fuer Sonntage ab dem 18.10.

Von Hand starten:  python3 wochenbriefing_lauf.py            (haelt sich an die Wochenregel)
                   BRIEFING_WOCHE_ERZWINGEN=1 python3 …      (baut sofort)
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

DRAFT_PATH = os.path.join(APP_DIR, ".briefing_draft.json")
STATUS_PATH = os.path.join(APP_DIR, ".wochenbriefing_status.json")
# Merkzettel fuer das Nachholen: welcher Sonntag, wie viele Fehlversuche, Push schon raus?
ZUSTAND_PATH = os.path.join(APP_DIR, ".wochenbriefing_nachholen.json")
LOG_PATH = os.path.expanduser("~/Library/Logs/wochenbriefing.log")
ARCHIVE = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Briefings")
SPIEGEL = os.path.expanduser("~/.briefing_meta_mirror")
# Neutraler lokaler Arbeitsordner fuer die CLI bei Wiederholungen (kein iCloud).
CLI_ORDNER = os.path.expanduser("~/.briefing_cli_cwd")
ERZWINGEN = bool(os.environ.get("BRIEFING_WOCHE_ERZWINGEN"))
# Standard: Claude schaut zu den wichtigsten Themen selbst nach, was daraus
# geworden ist. Abschaltbar, falls ein Lauf mal schnell durch soll.
RECHERCHE = not os.environ.get("BRIEFING_WOCHE_OHNE_RECHERCHE")

# --- Wiederholen und Nachholen (12.10.) ---------------------------------------
MAX_VERSUCHE = 3              # 1 Versuch + 2 weitere im selben Lauf
PAUSE_SEKUNDEN = 600          # 10 Minuten zwischen den Versuchen
NACHHOL_STUNDE = 3            # Mo/Di/Mi 03:20 (siehe launchd/de.florian.wochenbriefing.plist)
NACHHOL_MINUTE = 20
WECK_VORLAUF_MIN = 2          # pmset-Weckzeit = Start minus 2 Minuten (wie briefing_schedule)
LETZTE_NACHT_TAGE = 3         # 3 Tage nach dem Sonntag = Mittwoch = letzte Chance
# Das vom 11.10. ausgefallene Wochenbriefing soll NICHT nachgebaut werden (Florian
# 12.10.). Nachholen gilt deshalb erst fuer Sonntage ab diesem Datum.
NACHHOLEN_AB_SONNTAG = datetime.date(2026, 10, 18)
PUSH_AB_STUNDE = 8            # die eine Push kommt nie vor 08:00
PUSHFUNK = os.path.expanduser("~/bin/pushfunk")
# Das Wochenbriefing geht nie automatisch an Menschen — auch nicht beim Nachholen.
WA_AUTO = False

_DATEI_DATUM = re.compile(r"(\d{4})-(\d{2})-(\d{2}).*wochenbriefing", re.I)
_WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


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


# ── Merkzettel fuers Nachholen ─────────────────────────────────────────────

def _zustand_lesen() -> dict:
    try:
        with open(ZUSTAND_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _zustand_schreiben(z: dict) -> None:
    try:
        z["aktualisiert"] = datetime.datetime.now().isoformat(timespec="seconds")
        tmp = ZUSTAND_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(z, f, ensure_ascii=False, indent=1)
        os.replace(tmp, ZUSTAND_PATH)
    except Exception as e:
        _log("Merkzettel schreiben fehlgeschlagen: %s" % e)


def _zustand_fuer(sonntag: datetime.date, zustand=None) -> dict:
    """Der Merkzettel gilt nur fuer EINEN Sonntag; ein anderer = frisches Blatt."""
    z = _zustand_lesen() if zustand is None else zustand
    if z.get("sonntag") == sonntag.isoformat():
        return z
    return {"sonntag": sonntag.isoformat(), "status": "offen", "versuche": [], "push": False}


def _letzter_sonntag(heute: datetime.date) -> datetime.date:
    """Der Sonntag, zu dessen Woche der Tag gehoert (Sonntag selbst = heute)."""
    return heute - datetime.timedelta(days=(heute.weekday() + 1) % 7)


def _entscheiden(heute: datetime.date, letztes, zustand=None):
    """(bauen?, Begruendung, Sonntag der Woche).

    Sonntag: bauen, ausser es steht schon eins von heute da.
    Mo..Sa  = Nachholen: nur wenn das Wochenbriefing der laufenden Woche (seit dem
    letzten Sonntag) noch fehlt UND der Merkzettel nicht schon "fertig", "aufgegeben"
    oder "keine_daten" sagt. Ist die Woche fertig, passiert nichts — auch nicht,
    wenn Florian es zwischendurch von Hand in der App gebaut hat."""
    sonntag = _letzter_sonntag(heute)
    if ERZWINGEN:
        return True, "erzwungen", sonntag
    if heute.weekday() == 6:
        if letztes is not None and letztes >= heute:
            return False, "heute schon gebaut", sonntag
        if letztes is None:
            return True, "noch gar keins vorhanden", sonntag
        return True, "Sonntag, letztes vom %s" % letztes.strftime("%d.%m."), sonntag
    if sonntag < NACHHOLEN_AB_SONNTAG:
        return (False, "Nachholen gilt erst fuer Sonntage ab %s — das ausgefallene vom %s wird "
                "bewusst nicht nachgebaut" % (NACHHOLEN_AB_SONNTAG.strftime("%d.%m."),
                                              sonntag.strftime("%d.%m.")), sonntag)
    if letztes is not None and letztes >= sonntag:
        return (False, "Woche ab Sonntag %s ist schon fertig (gebaut am %s)"
                % (sonntag.strftime("%d.%m."), letztes.strftime("%d.%m.")), sonntag)
    st = _zustand_fuer(sonntag, zustand)
    if st.get("status") in ("aufgegeben", "keine_daten", "fertig"):
        return False, "Merkzettel sagt '%s' fuer die Woche ab %s" % (st["status"], sonntag.strftime("%d.%m.")), sonntag
    return (True, "Nachholen — Wochenbriefing der Woche ab Sonntag %s fehlt noch (%d Fehlversuche bisher)"
            % (sonntag.strftime("%d.%m."), len(st.get("versuche") or [])), sonntag)


def _naechster_nachholtermin(jetzt: datetime.datetime) -> datetime.datetime:
    """Naechster Termin 03:20 (heute, falls noch nicht erreicht, sonst morgen)."""
    ziel = jetzt.replace(hour=NACHHOL_STUNDE, minute=NACHHOL_MINUTE, second=0, microsecond=0)
    if ziel <= jetzt:
        ziel += datetime.timedelta(days=1)
    return ziel


# ── pmset-Weckzeit (nur wenn wirklich ein Nachholen ansteht) ───────────────

def _wecken_vormerken(start: datetime.datetime):
    """Stellt eine Weckzeit kurz vor dem Nachhol-Termin. Gibt den Weck-String
    (zum Abbestellen) zurueck oder None. Braucht die passwortlose pmset-Regel
    (/etc/sudoers.d/briefing-pmset) — fehlt sie, laeuft der Job trotzdem, sobald
    der Mac von allein wach ist (er schlaeft nachts meist nicht)."""
    try:
        import briefing_schedule as bsch
        if not bsch.wake_ready():
            _log("Weckzeit: passwortlose pmset-Regel nicht erreichbar — kein Wecken eingestellt.")
            return None
        wake = (start - datetime.timedelta(minutes=WECK_VORLAUF_MIN)).strftime("%m/%d/%y %H:%M:%S")
        r = subprocess.run(["sudo", "-n", bsch.PMSET, "schedule", "wake", wake],
                           capture_output=True, timeout=15)
        if r.returncode == 0:
            _log("Weckzeit gestellt: %s (fuer den Nachhol-Lauf um %s)." % (wake, start.strftime("%d.%m. %H:%M")))
            return wake
        _log("Weckzeit konnte nicht gestellt werden (pmset Exit %s)." % r.returncode)
    except Exception as e:
        _log("Weckzeit fehlgeschlagen: %s" % e)
    return None


def _wecken_abbestellen(wake_str) -> None:
    """Nimmt eine vorgemerkte Weckzeit zurueck (Woche ist inzwischen fertig)."""
    if not wake_str:
        return
    try:
        import briefing_schedule as bsch
        if bsch.wake_ready():
            subprocess.run(["sudo", "-n", bsch.PMSET, "schedule", "cancel", "wake", wake_str],
                           capture_output=True, timeout=15)
            _log("Weckzeit %s abbestellt." % wake_str)
    except Exception as e:
        _log("Weckzeit abbestellen fehlgeschlagen: %s" % e)


# ── Fehlerbuch und Push ────────────────────────────────────────────────────

def _fehlerbuch(bereich, meldung, schwere="normal", kontext=None):
    try:
        import fehlerbuch
        fehlerbuch.eintragen(bereich, meldung, kontext, schwere)
    except Exception:
        pass


def _fehlerbuch_abhaken(notiz: str) -> None:
    """Nach einem gelungenen Lauf: offene Wochenbriefing-Eintraege abhaken."""
    try:
        import fehlerbuch
        for x in fehlerbuch.offene():
            if x.get("bereich") == "Wochenbriefing":
                fehlerbuch.erledigen(x["kennung"], notiz)
    except Exception as e:
        _log("Fehlerbuch abhaken fehlgeschlagen: %s" % e)


def _fehler_festhalten(res: dict, nr: int, anzahl: int, sonntag: datetime.date, modus: str) -> None:
    """Fehlschlag in Log UND Fehlerbuch — samt dem, was die CLI zuletzt gesagt hat."""
    fehler = str(res.get("fehler") or "unbekannter Fehler")
    diag = str(res.get("diagnose") or "").strip()
    _log("Bauversuch %d/%d fehlgeschlagen: %s" % (nr, anzahl, fehler))
    for zeile in diag.split("\n") if diag else []:
        _log("   CLI: " + zeile)
    meldung = "Bau fehlgeschlagen: %s" % fehler[:140]
    if diag:
        meldung += " || " + diag
    kontext = {"versuch": "%d/%d" % (nr, anzahl), "modus": modus, "woche_ab": sonntag.isoformat(),
               "wann": datetime.datetime.now().isoformat(timespec="seconds"), "cli": diag[:1500]}
    _fehlerbuch(res.get("bereich") or "Wochenbriefing", meldung, "kritisch", kontext)


def _push_zeit(jetzt: datetime.datetime):
    """Die eine Push kommt nie nachts: vor 08:00 geplant auf 08:00, ab 22:00 auf morgen 08:00."""
    if jetzt.hour < PUSH_AB_STUNDE:
        return jetzt.strftime("%Y-%m-%d") + " %02d:00" % PUSH_AB_STUNDE
    if jetzt.hour >= 22:
        morgen = jetzt + datetime.timedelta(days=1)
        return morgen.strftime("%Y-%m-%d") + " %02d:00" % PUSH_AB_STUNDE
    return None


def _push_senden(titel: str, text: str, jetzt: datetime.datetime) -> bool:
    """EINE Push an Florian ueber Pushfunk (App 'pushfunk'). Bis zu 3 Anlaeufe, falls
    das Netz hakt; Rueckgabe True, wenn der Server sie angenommen hat."""
    args = [PUSHFUNK, "pushfunk", titel, text]
    um = _push_zeit(jetzt)
    if um:
        args += ["--um", um]
    for lauf in range(1, 4):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=40)
            if r.returncode == 0:
                _log("Push abgeschickt%s." % ((" (zugestellt um %s)" % um) if um else ""))
                return True
            _log("Push Anlauf %d fehlgeschlagen (Exit %s): %s"
                 % (lauf, r.returncode, (r.stderr or "").strip()[:200]))
        except Exception as e:
            _log("Push Anlauf %d fehlgeschlagen: %s" % (lauf, e))
        if lauf < 3:
            time.sleep(30)
    return False


# ── Ein Bauversuch ────────────────────────────────────────────────────────

def _cli_version(cli: str) -> str:
    try:
        r = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=20)
        return (r.stdout or r.stderr or "").strip().split("\n")[0][:80]
    except Exception as e:
        return "Version unbekannt (%s)" % e


def _bauversuch(nr: int, anzahl: int, grund: str, modus: str) -> dict:
    """Ein kompletter Anlauf: CLI finden, Anmeldung pruefen, Wochen-Meta bauen.

    Rueckgabe: {"ok": True, "r": <Ergebnis>, "tage": n}
           oder {"ok": False, "fehler": str, "diagnose": str, "wiederholen": bool,
                 "bereich": str|None, "keine_daten": bool}"""
    import briefing_core as core

    _log("--- Bauversuch %d/%d (%s) ---" % (nr, anzahl, modus))
    cli = core._locate_claude_cli()
    if not cli:
        return {"ok": False, "fehler": "Claude CLI nicht gefunden — der Lauf konnte nichts bauen.",
                "diagnose": "", "wiederholen": True}
    _log("Claude CLI: %s (%s)" % (cli, _cli_version(cli)))

    # Erst anklopfen: ein abgelaufener Claude-Login laesst den Lauf sonst
    # minutenlang arbeiten und dann mit leerer Antwort sterben (11.09.).
    try:
        anm = core.pruefe_claude_anmeldung(cli_path=cli)
    except Exception as e:
        anm = {"ok": True, "meldung": "Vorabpruefung uebersprungen (%s)" % e}
    if not anm.get("ok"):
        return {"ok": False, "fehler": "Claude-Anmeldung: %s" % anm.get("meldung"),
                "diagnose": "", "wiederholen": True, "bereich": "Claude-Anmeldung"}

    modell = _entwurf_lesen().get("meta_cli_model") or "opus"

    # Wie weit zurueck? Normalerweise sieben Tage. Hat Florian aber ein paar
    # Tage kein Briefing gebaut, findet ein Sieben-Tage-Fenster womoeglich nur
    # ein oder zwei Texte — und aus zwei Tagen laesst sich keine Woche deuten.
    # Genau das ist am 14.09. passiert: zwei Briefings, beide vom 11.09. Dann
    # ist es ehrlicher, das Fenster zu weiten, als einen duennen Rueckblick als
    # Wochenschau auszugeben.
    tage, gefunden = 7, 0
    try:
        for _t in (7, 10, 14):
            _n = len(core._discover_weekly_briefing_texts(str(core._LOCAL_META_MIRROR_DIR), days=_t))
            tage, gefunden = _t, _n
            if _n >= 4:
                break
        if tage != 7 and nr == 1:
            _log("Nur %d Tagesbriefings in 7 Tagen — Fenster auf %d Tage geweitet (%d Texte)."
                 % (len(core._discover_weekly_briefing_texts(str(core._LOCAL_META_MIRROR_DIR), days=7)),
                    tage, gefunden))
    except Exception as e:
        _log("Fensterwahl fehlgeschlagen (%s) — bleibe bei 7 Tagen." % e)
        tage = 7
    _status_schreiben(gebaut=False, laeuft=True, schritt="Wird gestartet… (Versuch %d/%d)" % (nr, anzahl),
                      grund=grund)

    def _cb(schritt, anteil):
        try:
            _status_schreiben(gebaut=False, laeuft=True,
                              schritt=("[%d/%d] " % (nr, anzahl) + str(schritt))[:160],
                              anteil=round(float(anteil), 3), grund=grund)
        except Exception:
            pass

    try:
        os.makedirs(ARCHIVE, exist_ok=True)
    except Exception:
        pass

    # Erster Versuch wie immer im iCloud-Archiv; Wiederholungen (und alle Nachhol-
    # Naechte) in einem neutralen LOKALEN Ordner: am 11.10. hat die CLI zweimal
    # gar nicht erst angefangen — der iCloud-Ordner als Arbeitsordner ist ein
    # Verdaechtiger, und die Wiederholung kostet nichts extra.
    cli_cwd = None
    if nr >= 2 or modus != "sonntag":
        try:
            os.makedirs(CLI_ORDNER, exist_ok=True)
            cli_cwd = CLI_ORDNER
        except Exception:
            cli_cwd = None
    try:
        r = core.run_meta_briefing_via_claude_cli(
            archive_dir=ARCHIVE, days=tage, model=modell, cli_path=cli,
            web_recherche=RECHERCHE, progress_callback=_cb, cli_cwd=cli_cwd)
    except Exception as e:
        spur = "".join(traceback.format_exc().splitlines(True)[-6:]).strip()
        return {"ok": False, "fehler": "Bau abgestuerzt: %s" % str(e)[:200], "diagnose": spur,
                "wiederholen": True}

    if r is None:
        return {"ok": False, "fehler": "Keine Tagesbriefing-Texte der letzten %d Tage gefunden." % tage,
                "diagnose": "", "wiederholen": False, "keine_daten": True, "tage": tage}
    if not r.get("ok"):
        return {"ok": False, "fehler": str(r.get("error")), "diagnose": str(r.get("diagnose") or ""),
                "wiederholen": True, "tage": tage}
    return {"ok": True, "r": r, "tage": tage}


def _bauen_mit_wiederholung(versuch, bei_fehler=None, schlafen=time.sleep,
                            max_versuche=None, pause_s=None):
    """Ruft `versuch(nr)` bis zu max_versuche-mal mit Pausen auf.

    Rueckgabe (erfolgreiches Ergebnis | None, Liste der Fehlschlaege). `versuch`
    liefert ein dict mit "ok"; "wiederholen": False bricht die Schleife ab (z. B.
    wenn es gar keine Tagestexte gibt — das wird beim naechsten Mal nicht besser)."""
    max_versuche = MAX_VERSUCHE if max_versuche is None else max_versuche
    pause_s = PAUSE_SEKUNDEN if pause_s is None else pause_s
    fehlschlaege = []
    for nr in range(1, max_versuche + 1):
        res = versuch(nr)
        if res.get("ok"):
            return res, fehlschlaege
        res["nr"] = nr
        fehlschlaege.append(res)
        if bei_fehler:
            bei_fehler(res, nr, max_versuche)
        if not res.get("wiederholen", True):
            break
        if nr < max_versuche:
            _log("Pause %d min, dann Bauversuch %d/%d." % (round(pause_s / 60), nr + 1, max_versuche))
            _status_schreiben(gebaut=False, laeuft=True, grund="Wiederholung",
                              schritt="Versuch %d/%d fehlgeschlagen — Pause, dann Versuch %d" % (nr, max_versuche, nr + 1))
            schlafen(pause_s)
    return None, fehlschlaege


# ── Nach dem Bau: Upload, Aufraeumen, Begleittext ─────────────────────────

def _abschliessen(r: dict, heute: datetime.date, grund: str, tage: int, modus: str) -> dict:
    """Upload in den ElevenReader, alte Eintraege aufraeumen, Begleittext vorbereiten.

    Nichts davon geht an Menschen: der WhatsApp-Begleittext wird nur VORBEREITET
    (auto=False), versendet wird das Wochenbriefing ausschliesslich per Knopf in der
    App — auch wenn es nachts im Nachhol-Lauf entsteht."""
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
    _status_schreiben(gebaut=True, titel=titel, grund=grund, fenster=tage,
                      recherche=RECHERCHE, modus=modus,
                      tage=stats.get("days"), briefings=stats.get("briefings"),
                      sekunden=int(r.get("elapsed_seconds") or 0),
                      txt=txtp, pdf=r.get("pdf_path"), upload=hochgeladen)
    _log("=== Fertig: %s aus %s Tagesbriefings in %ds, Upload=%s ==="
         % (titel, stats.get("briefings"), int(r.get("elapsed_seconds") or 0), hochgeladen))
    q = (r.get("quality") or {})
    if q.get("warnings"):
        _log("Qualitaetshinweise: %s" % "; ".join(str(w) for w in q["warnings"][:4]))

    # 📲 04.10.: WhatsApp-Runde — Begleittext fürs Wochenbriefing schon vorbereiten.
    # Gesendet wird das Wochenbriefing NIE automatisch, nur per Knopf in der App.
    if r.get("pdf_path"):
        try:
            import wa_runde
            _log("WhatsApp-Runde: %s" % wa_runde.nach_briefing(str(r["pdf_path"]), txtp, auto=WA_AUTO, log=_log))
        except Exception as e:
            _log("WhatsApp-Runde: Begleittext nicht erzeugt: %s" % e)
    return {"titel": titel, "upload": hochgeladen}


# ── Hauptablauf ───────────────────────────────────────────────────────────

def main(jetzt=None):
    _log("=== Wochenbriefing-Lauf gestartet ===")
    # Nach einem langen Lauf (bis ~55 min mit Pausen) zaehlt die echte Uhr fuer
    # Weckzeit und Push; Tests geben `jetzt` fest vor.
    uhr = (lambda: jetzt) if jetzt else datetime.datetime.now
    jetzt = uhr()
    heute = jetzt.date()
    letztes = _letztes_wochenbriefing()
    zustand = _zustand_lesen()
    bauen, grund, sonntag = _entscheiden(heute, letztes, zustand)
    st = _zustand_fuer(sonntag, zustand)

    if not bauen:
        _log("Nichts zu tun (%s)." % grund)
        # Wurde die Woche zwischendurch von Hand gebaut, den Merkzettel abschliessen
        # und eine vorgemerkte Weckzeit zurueckgeben. Sonst bleibt alles unberuehrt —
        # auch der Status der App (sonst wuerde ein leerer Nachtlauf eine echte Meldung
        # wie "letzter Lauf: Bau fehlgeschlagen" ueberschreiben).
        if st.get("status") == "offen" and zustand.get("sonntag") == sonntag.isoformat() \
                and letztes is not None and letztes >= sonntag:
            _wecken_abbestellen(st.get("weckzeit"))
            st.update(status="fertig", weckzeit=None, gebaut_am=letztes.isoformat(), notiz="von Hand gebaut")
            _zustand_schreiben(st)
        return

    nacht = (heute - sonntag).days            # 0 = Sonntag selbst, 3 = Mittwoch
    modus = "sonntag" if nacht == 0 else "nachholen"
    letzte_chance = nacht >= LETZTE_NACHT_TAGE
    _log("Bauen (%s)." % grund)

    st.setdefault("versuche", [])
    st["status"] = "offen"
    _zustand_schreiben(st)
    # Eine vorgemerkte Weckzeit ist mit diesem Lauf verbraucht.
    st["weckzeit"] = None

    def _bei_fehler(res, nr, anzahl):
        st["versuche"] = (st.get("versuche") or [])[-20:] + [{
            "wann": datetime.datetime.now().isoformat(timespec="seconds"), "modus": modus,
            "nr": "%d/%d" % (nr, anzahl), "fehler": str(res.get("fehler"))[:200],
            "cli": str(res.get("diagnose") or "")[:800]}]
        _zustand_schreiben(st)
        _fehler_festhalten(res, nr, anzahl, sonntag, modus)

    ok_res, fehlschlaege = _bauen_mit_wiederholung(
        lambda nr: _bauversuch(nr, MAX_VERSUCHE, grund, modus), bei_fehler=_bei_fehler)

    # ── Gelungen ──
    if ok_res:
        info = _abschliessen(ok_res["r"], heute, grund, ok_res.get("tage"), modus)
        nr_ok = len(fehlschlaege) + 1
        st.update(status="fertig", gebaut_am=datetime.datetime.now().isoformat(timespec="seconds"),
                  notiz="Versuch %d/%d (%s)" % (nr_ok, MAX_VERSUCHE, modus))
        _zustand_schreiben(st)
        _fehlerbuch_abhaken("Spaeter gelungen am %s (Versuch %d/%d, %s); Titel: %s"
                            % (heute.strftime("%d.%m.%Y"), nr_ok, MAX_VERSUCHE, modus, info.get("titel")))
        return

    # ── Nichts zu bauen (keine Tagestexte): kein Grund zu wiederholen ──
    letzter = fehlschlaege[-1] if fehlschlaege else {}
    if letzter.get("keine_daten"):
        _status_schreiben(gebaut=False, fehler="keine Tagesbriefings der letzten %s Tage" % letzter.get("tage", 7))
        st["status"] = "keine_daten"
        _zustand_schreiben(st)
        _fehlerbuch("Wochenbriefing",
                    "Kein Wochenbriefing moeglich: im lokalen Spiegel liegt kein "
                    "einziger Tagestext der letzten %s Tage." % letzter.get("tage", 7), "normal")
        return

    # ── Alle Versuche dieses Laufs gescheitert ──
    kurzgrund = str(letzter.get("fehler") or "unbekannt")[:160]
    if not letzte_chance:
        naechster = _naechster_nachholtermin(uhr())
        st["weckzeit"] = _wecken_vormerken(naechster)
        st["status"] = "offen"
        _zustand_schreiben(st)
        wann = "%s %s" % (_WOCHENTAGE[naechster.weekday()], naechster.strftime("%H:%M"))
        _status_schreiben(gebaut=False, fehler="%s — %d Versuche gescheitert, nächster Versuch %s"
                          % (kurzgrund[:120], len(fehlschlaege), wann))
        _log("Alle %d Versuche gescheitert — naechster Versuch %s." % (len(fehlschlaege), wann))
        return

    # ── Letzte Nacht auch gescheitert: einmal Bescheid sagen, kritisch eintragen ──
    st["status"] = "aufgegeben"
    _fehlerbuch("Wochenbriefing",
                "Das Wochenbriefing der Woche ab Sonntag %s ist ausgeblieben — bitte klaeren. "
                "Sonntag und alle Nachhol-Naechte (je %d Versuche) sind gescheitert. Letzter Grund: %s"
                % (sonntag.strftime("%d.%m."), MAX_VERSUCHE, kurzgrund), "kritisch",
                {"woche_ab": sonntag.isoformat(), "versuche": len(st.get("versuche") or [])})
    if not st.get("push"):
        text = ("Das Wochenbriefing zum %s ließ sich weder am Sonntag noch in den Nächten danach "
                "bauen. Grund: %s. Bitte klären (Fehler-Tagebuch)." % (sonntag.strftime("%d.%m."), kurzgrund[:100]))
        st["push"] = bool(_push_senden("⚠️ Wochenbriefing", text, uhr()))
    _zustand_schreiben(st)
    _status_schreiben(gebaut=False, fehler="aufgegeben nach Sonntag + Nachhol-Naechten: %s" % kurzgrund[:120])
    _log("Aufgegeben — Push=%s, Fehlerbuch kritisch." % ("ja" if st.get("push") else "NEIN"))


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
