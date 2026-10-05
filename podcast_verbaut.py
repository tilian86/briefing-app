"""Podcast-Zusammenfassungen, die schon in einem fertigen Briefing standen — und
Podcasts, die nie ins Briefing sollen.

05.10.2026: Nach den Läufen vom 29.09. blieb das Podcast-Feld voll. Am 05.10.
standen die 28 alten Folgen noch drin und wären ein zweites Mal im Briefing
gelandet. Außerdem rutschte der Einschlafen-Podcast mit in die Runde. Beides
fängt dieses Modul beim Start des Baus ab, egal was gerade im Feld steht.

Wiedererkannt wird eine Zusammenfassung an ihrem fetten Titel
(„**Podcast – Folge**“). Den schreibt die Zusammenfassung immer als Erstes.
"""
import datetime
import json
import os
import re
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
PFAD = Path(os.environ.get("BRIEFING_PODCAST_VERBAUT_PATH")
            or _APP_DIR / ".briefing_podcasts_verbaut.json")
NIE_PFAD = Path(os.environ.get("BRIEFING_PODCASTS_NIE_PATH")
                or os.path.expanduser("~/.briefing_podcasts_nie.json"))
AUFBEWAHREN_TAGE = 60  # ≥ Zeitfenster in pocketcasts_fetch
# Einträge sind Präfixe: „Podcast“ sperrt den ganzen Podcast, „Podcast – Folge“
# nur diese eine Folge. Florian 05.10.2026: Einschlafen nur dieses Mal, nicht
# generell — deshalb steht dort nur die eine Folge.
NIE_STANDARD = []


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", " ", (s or "").lower()).strip()


def titel(block: str) -> str:
    """Der fette Titel vorn im Block, sonst die erste Zeile."""
    m = re.match(r"\s*\*\*(.+?)\*\*", block or "", re.S)
    if m:
        return m.group(1).strip()
    return next((z.strip() for z in (block or "").splitlines() if z.strip()), "")


def fingerabdruck(block: str) -> str:
    return _norm(titel(block))[:80]


def _lesen() -> dict:
    try:
        d = json.loads(PFAD.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def merken(bloecke, datum: str = None) -> int:
    """Hält fest, dass diese Blöcke in einem fertigen Briefing standen."""
    datum = datum or datetime.date.today().isoformat()
    d = _lesen()
    eintraege = d.get("folgen") or {}
    neu = 0
    for b in bloecke or []:
        fp = fingerabdruck(b)
        if fp and fp not in eintraege:
            eintraege[fp] = {"datum": datum, "titel": titel(b)[:120]}
            neu += 1
    grenze = (datetime.date.today() - datetime.timedelta(days=AUFBEWAHREN_TAGE)).isoformat()
    eintraege = {k: v for k, v in eintraege.items() if (v.get("datum") or "") >= grenze}
    PFAD.write_text(json.dumps({"folgen": eintraege}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return neu


def schon_verbaut(block: str):
    """Datum des Briefings, in dem der Block schon stand, sonst None."""
    e = (_lesen().get("folgen") or {}).get(fingerabdruck(block))
    return e.get("datum") if e else None


def nie_liste() -> list:
    try:
        d = json.loads(NIE_PFAD.read_text(encoding="utf-8"))
        return [str(x).strip() for x in (d.get("podcasts") or []) if str(x).strip()]
    except Exception:
        return list(NIE_STANDARD)


def gesperrt(text: str, liste=None) -> bool:
    """Gehört die Folge (Block-Titel oder Podcast-Name) zu einem gesperrten Podcast?"""
    n = _norm(text)
    return any(n.startswith(_norm(p)) for p in (nie_liste() if liste is None else liste) if _norm(p))


def bereinigen(podcast_text: str, split, combine):
    """Nimmt schon verbaute und gesperrte Blöcke heraus.

    Rückgabe (text, alt, nie): alt = [(titel, datum)], nie = [titel]. Ist nichts
    zu entfernen, kommt der Text unverändert zurück."""
    if not (podcast_text or "").strip():
        return podcast_text, [], []
    bloecke = split(podcast_text)
    verbaut = _lesen().get("folgen") or {}
    liste = nie_liste()
    bleiben, alt, nie = [], [], []
    for b in bloecke:
        if gesperrt(titel(b), liste):
            nie.append(titel(b))
        elif fingerabdruck(b) in verbaut:
            alt.append((titel(b), verbaut[fingerabdruck(b)].get("datum") or ""))
        else:
            bleiben.append(b)
    if not alt and not nie:
        return podcast_text, [], []
    return combine("", bleiben), alt, nie


def kurzmeldung(alt, nie) -> str:
    teile = []
    if alt:
        tage = sorted({d for _, d in alt if d})
        wann = ", ".join(datetime.date.fromisoformat(t).strftime("%d.%m.") for t in tage)
        teile.append(f"{len(alt)} schon im Briefing vom {wann}" if wann else f"{len(alt)} schon verbaut")
    if nie:
        teile.append(f"{len(nie)} von gesperrten Podcasts ({', '.join(sorted({t.split(' –')[0].split(' - ')[0] for t in nie}))})")
    if not teile:
        return ""
    satz = "⏭️ Nicht ins Briefing übernommen: " + "; ".join(teile)
    return satz if satz.endswith(".") else satz + "."
