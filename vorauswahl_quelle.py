"""Feedfunk-Vorauswahl als zweite Quelle neben der Feedly-Merkliste.

Florian hakt auf feedfunk.florian-s-thiel.workers.dev Meldungen an („Übernehmen“).
Der Worker sammelt sie in einem Korb. Dieses Modul holt den Korb im GLEICHEN
Format wie feedly_fetch.list_saved — die Briefing-App behandelt die Einträge
dann genau wie Merklisten-Artikel (Volltext laden, Paywall-Prüfung, Aufräumen).

Kennzeichen: entry_id beginnt mit "vw:". feedly_fetch.mark_done gibt diese Ids
hierher (erledigt), statt sie an Feedly zu schicken.

Zugang: ~/.feedfunk/sync.json  {"url": ..., "secret": ...}  (chmod 600)

Grundsatz: Ein Netzproblem hier darf das Briefing NIE stören — holen() liefert
dann einfach [] und merkt sich den Grund in `letzter_fehler`.
"""

import json
import os
import time
import urllib.error
import urllib.request

KONFIG = os.path.expanduser("~/.feedfunk/sync.json")
# Was sich nach dem Briefing nicht abhaken ließ (Netz weg), wird hier gemerkt und
# beim nächsten Mal nachgeholt — sonst käme derselbe Artikel ein zweites Mal.
OFFEN_ERLEDIGT = os.path.expanduser("~/.feedfunk/erledigt_offen.json")
PREFIX = "vw:"
TIMEOUT = 8

letzter_fehler = ""


def _konfig():
    try:
        with open(KONFIG, encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("url") and d.get("secret"):
            return d["url"].rstrip("/"), d["secret"]
    except (OSError, ValueError):
        pass
    return None, None


def _anfrage(methode, pfad, daten=None):
    url, secret = _konfig()
    if not url:
        raise RuntimeError("nicht eingerichtet (~/.feedfunk/sync.json fehlt)")
    body = json.dumps(daten).encode("utf-8") if daten is not None else None
    req = urllib.request.Request(url + pfad, data=body, method=methode, headers={
        "x-sync-secret": secret, "content-type": "application/json",
        "user-agent": "Briefing-App/Feedfunk"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _fehlerbuch(meldung, schwere="hinweis"):
    try:
        import fehlerbuch
        fehlerbuch.eintragen("Feedfunk-Vorauswahl", meldung[:200], None, schwere)
    except Exception:
        pass


def _offen_laden():
    try:
        with open(OFFEN_ERLEDIGT, encoding="utf-8") as fh:
            return [str(x) for x in (json.load(fh) or [])]
    except (OSError, ValueError):
        return []


def _offen_speichern(ids):
    try:
        os.makedirs(os.path.dirname(OFFEN_ERLEDIGT), exist_ok=True)
        tmp = OFFEN_ERLEDIGT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(sorted(set(ids)), fh)
        os.replace(tmp, OFFEN_ERLEDIGT)
    except OSError:
        pass


def eingerichtet() -> bool:
    return _konfig()[0] is not None


def holen() -> list:
    """Offene Feedfunk-Auswahl als Liste im Format von feedly_fetch.list_saved()["items"].
    Bei jedem Problem: [] (Grund in `letzter_fehler`)."""
    global letzter_fehler
    letzter_fehler = ""
    if not eingerichtet():
        return []
    # Nachholen, was beim letzten Mal nicht abgehakt werden konnte
    offen = _offen_laden()
    if offen:
        try:
            _anfrage("POST", "/api/sync/erledigt", {"ids": offen})
            _offen_speichern([])
            offen = []
        except Exception:
            pass
    try:
        daten = _anfrage("GET", "/api/sync/auswahl")
    except Exception as exc:
        letzter_fehler = f"Feedfunk nicht erreichbar ({exc.__class__.__name__})"
        _fehlerbuch(letzter_fehler)
        return []
    schon_erledigt = set(offen)
    items = []
    for x in daten.get("auswahl") or []:
        aid = str(x.get("id") or "")
        url = str(x.get("url") or "")
        if not aid or not url or aid in schon_erledigt:
            continue
        pub = x.get("published") or 0
        try:
            pub = int(pub)
            if 0 < pub < 10 ** 12:          # Sekunden → Millisekunden wie bei Feedly
                pub *= 1000
        except (TypeError, ValueError):
            pub = 0
        items.append({
            "entry_id": PREFIX + aid,
            "title": (x.get("title") or "").strip() or url,
            "url": url,
            "feed": (x.get("feed") or "").strip(),
            "published": pub,
            "feed_text": (x.get("teaser") or "").strip(),
            "text": "",
            "source": "",
            "problem": "",
        })
    return items


def erledigt(ids) -> int:
    """Nimmt Einträge nach einem erfolgreichen Briefing aus dem Feedfunk-Korb.
    ids mit oder ohne "vw:". Gibt die Zahl der abgehakten Ids zurück (0 bei Fehler —
    dann werden sie gemerkt und beim nächsten holen()/erledigt() nachgeholt)."""
    roh = [str(i)[len(PREFIX):] if str(i).startswith(PREFIX) else str(i) for i in (ids or []) if i]
    if not roh:
        return 0
    alle = sorted(set(roh) | set(_offen_laden()))
    for versuch in range(2):
        try:
            _anfrage("POST", "/api/sync/erledigt", {"ids": alle})
            _offen_speichern([])
            return len(roh)
        except Exception as exc:
            letzter = exc
            time.sleep(2)
    _offen_speichern(alle)
    _fehlerbuch(f"Abhaken im Feedfunk-Korb fehlgeschlagen ({letzter.__class__.__name__}) — "
                f"{len(roh)} Einträge werden beim nächsten Mal nachgeholt", "normal")
    return 0


if __name__ == "__main__":
    for it in holen():
        print(it["entry_id"], "|", it["feed"], "|", it["title"][:80])
    if letzter_fehler:
        print("Fehler:", letzter_fehler)
