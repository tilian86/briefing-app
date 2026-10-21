"""Verbaute Artikel-URLs aus dem URL-Feld nehmen (21.10.2026).

Problem: Im App-Entwurf (.briefing_draft.json, Feld "urls_text") blieb ein
Substack-Link seit dem 18.10. stehen und wäre bei JEDEM Bau wieder eingebaut
worden. Florian will: Nach einem ERFOLGREICHEN Bau sind die verbauten URLs aus
dem Feld raus.

Zwei Wege, damit es in jedem Fall greift:
  1. Die Entwurfsdatei wird direkt bereinigt. Das braucht der Zeitplan-Lauf
     (briefing_scheduled.py) und jeder Fall, in dem die App gerade nicht offen ist.
  2. Eine Merker-Datei (.briefing_urls_verbaut.json) hält die verbauten URLs fest.
     Die OFFENE App schreibt ihren Sitzungszustand bei jedem Durchlauf zurück in die
     Entwurfsdatei — reines Ändern der Datei reicht für sie nicht. Beim nächsten
     Durchlauf liest die App den Merker, nimmt die URLs aus dem Feld ihrer Sitzung
     und löscht den Merker (briefing_app.py, direkt nach _felder_wiederherstellen).

Es werden nur die URLs entfernt, die wirklich im Bau waren — Nachzügler, die
währenddessen ins Feld kamen, bleiben stehen.
"""
import json
import os
import re
import time
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
MERKER = Path(os.environ.get("BRIEFING_URLS_VERBAUT_PATH")
              or _APP_DIR / ".briefing_urls_verbaut.json")
# Der Entwurf, den die App selbst führt (gleiche Regel wie _DRAFT_PATH in briefing_app.py).
# Nur für ihn gibt es einen Merker — Nachhol-Läufe arbeiten mit eigenen Entwurfsdateien.
ENTWURF = Path(os.environ.get("BRIEFING_DRAFT_PATH") or _APP_DIR / ".briefing_draft.json")
VERFALL_S = 3 * 24 * 3600   # ein Merker, den keine App abholt, gilt nach 3 Tagen nicht mehr

_URL_RE = re.compile(r"https?://\S+")


def urls_in(text) -> list:
    """Alle URLs eines Textes (so, wie das Feld sie enthält), ohne Dubletten."""
    gesehen, aus = set(), []
    for u in _URL_RE.findall(text or ""):
        if u not in gesehen:
            gesehen.add(u)
            aus.append(u)
    return aus


def ohne_urls(text, urls) -> str:
    """Nimmt die URLs aus dem Feldtext. Eine Zeile, die nur aus verbauten URLs
    besteht (auch mit Beschriftung davor), fällt ganz weg. Steht daneben eine
    andere URL, bleibt die Zeile ohne die verbauten stehen."""
    weg = set(urls or [])
    if not weg or not text:
        return text or ""
    rest, geaendert = [], False
    for zeile in str(text).split("\n"):
        gefunden = _URL_RE.findall(zeile)
        if gefunden and all(u in weg for u in gefunden):
            geaendert = True
            continue
        if any(u in weg for u in gefunden):
            geaendert = True
            for u in gefunden:
                if u in weg:
                    zeile = zeile.replace(u, "")
            zeile = zeile.strip()
        rest.append(zeile)
    if not geaendert:
        return str(text)
    neu = "\n".join(rest).lstrip("\n")   # keine verwaiste Leerzeile oben
    return neu if neu.strip() else ""


def _schreiben(pfad: Path, daten, **kw) -> None:
    tmp = Path(str(pfad) + ".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, **kw), encoding="utf-8")
    os.replace(tmp, pfad)


def _merker_laden() -> list:
    try:
        d = json.loads(MERKER.read_text(encoding="utf-8"))
        if time.time() - float(d.get("stand", 0)) > VERFALL_S:
            return []
        return [str(u) for u in (d.get("urls") or []) if u]
    except Exception:
        return []


def merken(urls, entwurf=None) -> int:
    """Nach einem erfolgreichen Bau aufrufen. `urls`: Liste oder Feldtext der URLs,
    die im Bau waren. Gibt die Zahl der URLs zurück, die im Entwurf standen und
    entfernt wurden. Wirft nie — der Bau ist da längst fertig."""
    try:
        if isinstance(urls, str):
            urls = urls_in(urls)
        urls = [u for u in (urls or []) if u]
        if not urls:
            return 0
        pfad = Path(entwurf) if entwurf else ENTWURF
        # Merker (für die offene App) — mit dem, was noch unabgeholt darin liegt
        if pfad.resolve() == ENTWURF.resolve():
            alle = list(dict.fromkeys(_merker_laden() + urls))
            _schreiben(MERKER, {"stand": time.time(), "urls": alle})
        # Entwurfsdatei direkt bereinigen (für Zeitplan-Lauf / geschlossene App)
        try:
            d = json.loads(pfad.read_text(encoding="utf-8"))
        except Exception:
            return 0
        alt = d.get("urls_text") or ""
        neu = ohne_urls(alt, urls)
        if neu == alt:
            return 0
        d["urls_text"] = neu
        _schreiben(pfad, d, indent=2)
        return len([u for u in urls if u in set(urls_in(alt))])
    except Exception:
        return 0


def im_feld_abraeumen(feldtext):
    """Für die offene App: Merker lesen, URLs aus dem Feld der Sitzung nehmen und den
    Merker löschen. Rückgabe: der neue Feldtext — oder None, wenn nichts zu tun war."""
    urls = _merker_laden()
    if not urls:
        # leerer oder verfallener Merker: wegräumen, damit er nicht liegen bleibt
        try:
            if MERKER.exists():
                MERKER.unlink()
        except Exception:
            pass
        return None
    neu = ohne_urls(feldtext or "", urls)
    try:
        MERKER.unlink()
    except Exception:
        pass
    return neu if neu != (feldtext or "") else None
