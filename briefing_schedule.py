"""On-Demand-Terminierung für Briefings (einmaliger Zeitplan).

- Legt einen launchd-EINMALJOB an (StartCalendarInterval mit Monat/Tag/Std/Min),
  der zur Startzeit den Headless-Runner briefing_scheduled.py startet.
- Setzt — falls die Passwortlos-Regel für pmset installiert ist — einen passenden
  Aufweck-Zeitpunkt, damit der Mac garantiert wach ist.
- Beides räumt der Runner nach dem Lauf via disarm() selbst wieder ab.

Keine festen Rhythmen: der Nutzer stellt pro Mal Datum + Uhrzeit ein.
Python 3.9-kompatibel (CommandLineTools) — keine `X | Y`-Unions.
"""
import datetime
import json
import os
import plistlib
import subprocess
import sys
from typing import Optional

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LABEL = "local.florian.briefing-schedule"
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LABEL)
MARKER_PATH = os.path.join(APP_DIR, ".briefing_schedule_armed.json")
RUNNER_PATH = os.path.join(APP_DIR, "briefing_scheduled.py")
LOG_PATH = os.path.expanduser("~/Library/Logs/briefing-scheduled.log")
WAKE_LEAD_MIN = 2  # den Mac ein paar Minuten VOR dem Start wecken
PMSET = "/usr/bin/pmset"


def _uid() -> int:
    return os.getuid()


def wake_ready() -> bool:
    """True, wenn `sudo` den pmset-Weckbefehl OHNE Passwort erlaubt (NOPASSWD-Regel
    installiert). Prüft via `sudo -n -l` — rein lesend, kein Seiteneffekt."""
    try:
        r = subprocess.run(
            ["sudo", "-n", "-l", PMSET, "schedule", "wake", "01/01/1970 00:00:00"],
            capture_output=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return False


def _wake_str(dt: datetime.datetime) -> str:
    return dt.strftime("%m/%d/%y %H:%M:%S")


def arm(start_dt: datetime.datetime) -> dict:
    """Stellt einen Einmal-Lauf zur Startzeit scharf. Gibt Status-Dict zurück."""
    # 1) launchd-Einmaljob schreiben + laden
    py = sys.executable or "/usr/bin/python3"
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [py, RUNNER_PATH],
        "StartCalendarInterval": {
            "Month": start_dt.month, "Day": start_dt.day,
            "Hour": start_dt.hour, "Minute": start_dt.minute,
        },
        "StandardOutPath": LOG_PATH,
        "StandardErrorPath": LOG_PATH,
        "RunAtLoad": False,
        "ProcessType": "Interactive",
        "WorkingDirectory": APP_DIR,
    }
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)
    dom = "gui/%d" % _uid()
    subprocess.run(["launchctl", "bootout", "%s/%s" % (dom, LABEL)], capture_output=True, timeout=15)
    boot = subprocess.run(["launchctl", "bootstrap", dom, PLIST_PATH], capture_output=True, timeout=15)

    # 2) Aufweck-Zeitpunkt (nur wenn passwortlos möglich)
    wake_dt = start_dt - datetime.timedelta(minutes=WAKE_LEAD_MIN)
    wake_str = _wake_str(wake_dt)
    wake_set = False
    ready = wake_ready()
    if ready:
        try:
            rc = subprocess.run(["sudo", "-n", PMSET, "schedule", "wake", wake_str],
                                capture_output=True, timeout=15)
            wake_set = (rc.returncode == 0)
        except Exception:
            wake_set = False

    # 3) Merker für den Runner (zum Abbestellen der exakt gleichen Weckzeit)
    marker = {"start": start_dt.isoformat(), "wake_str": wake_str,
              "wake_set": wake_set, "armed_at": datetime.datetime.now().isoformat()}
    try:
        json.dump(marker, open(MARKER_PATH, "w", encoding="utf-8"))
    except Exception:
        pass

    return {"start": start_dt, "wake_set": wake_set, "wake_ready": ready,
            "loaded": boot.returncode == 0}


def disarm() -> None:
    """Hebt den Zeitplan auf: Weckzeit abbestellen, plist entfernen, Job entladen.
    Reihenfolge so, dass ein evtl. Selbst-Bootout nichts Halbfertiges hinterlässt."""
    # 1) exakt gesetzte Weckzeit abbestellen
    try:
        if os.path.exists(MARKER_PATH):
            m = json.loads(open(MARKER_PATH, encoding="utf-8").read())
            if m.get("wake_str") and wake_ready():
                subprocess.run(["sudo", "-n", PMSET, "schedule", "cancel", "wake", m["wake_str"]],
                               capture_output=True, timeout=15)
            os.remove(MARKER_PATH)
    except Exception:
        pass
    # 2) plist-Datei ZUERST weg (damit nichts nächstes Jahr wieder feuert)
    try:
        if os.path.exists(PLIST_PATH):
            os.remove(PLIST_PATH)
    except Exception:
        pass
    # 3) Job entladen (kann den eigenen Prozess beenden → daher zuletzt)
    try:
        subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (_uid(), LABEL)],
                       capture_output=True, timeout=15)
    except Exception:
        pass


def current() -> Optional[dict]:
    """Gibt den aktuell scharfen Zeitplan zurück (oder None)."""
    try:
        if not os.path.exists(MARKER_PATH):
            return None
        m = json.loads(open(MARKER_PATH, encoding="utf-8").read())
        return m
    except Exception:
        return None
