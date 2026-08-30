"""Fehler-Tagebuch der Briefing-App.

30.08.2026, Florians Wunsch: Stolpersteine sollen nicht mehr in Logs
verschwinden, die niemand liest. Jeder Fehler wird strukturiert festgehalten —
mit genug Kontext, dass er ohne Rückfrage behoben werden kann.

Zwei Nutzer dieses Buchs:
  · Florian sieht offene Einträge in der App und kann sie mit einem Klick
    als fertigen Bericht kopieren.
  · Der nächtliche Wartungslauf liest sie, behebt Eindeutiges selbst und
    legt Kritisches Florian vor.

Bewusst ohne Fremdbibliotheken und absturzsicher: Ein Fehler BEIM Festhalten
eines Fehlers darf die App niemals stören.
"""

import json
import os
import datetime
import hashlib

PFAD = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".briefing_fehlerbuch.json")
MAX_EINTRAEGE = 300


def _lesen() -> list:
    try:
        with open(PFAD, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _schreiben(eintraege: list) -> None:
    try:
        tmp = PFAD + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(eintraege[-MAX_EINTRAEGE:], fh, ensure_ascii=False, indent=1)
        os.replace(tmp, PFAD)          # atomar — nie eine halbe Datei
    except Exception:
        pass


def _kennung(bereich: str, meldung: str) -> str:
    """Gleicher Fehler = gleiche Kennung, damit er nicht 50x im Buch steht."""
    roh = f"{bereich}|{(meldung or '')[:160]}"
    return hashlib.sha1(roh.encode("utf-8", "replace")).hexdigest()[:12]


def eintragen(bereich: str, meldung: str, kontext=None, schwere: str = "normal") -> None:
    """Einen Stolperstein festhalten.

    bereich:  wo es passiert ist, z.B. "Feedly-Abruf", "Podcast-Transkript"
    meldung:  die Fehlermeldung (roh reicht — der Wartungslauf übersetzt sie)
    kontext:  dict mit allem, was zum Nachvollziehen nötig ist (URL, Titel, Schritt)
    schwere:  "hinweis" | "normal" | "kritisch"
    """
    try:
        k = _kennung(bereich, meldung)
        jetzt = datetime.datetime.now().isoformat(timespec="seconds")
        eintraege = _lesen()
        for e in eintraege:
            if e.get("kennung") == k and not e.get("erledigt"):
                e["anzahl"] = int(e.get("anzahl", 1)) + 1
                e["zuletzt"] = jetzt
                if kontext:
                    e.setdefault("beispiele", [])
                    if len(e["beispiele"]) < 5:
                        e["beispiele"].append(kontext)
                _schreiben(eintraege)
                return
        eintraege.append({
            "kennung": k, "bereich": bereich, "meldung": str(meldung)[:600],
            "schwere": schwere, "anzahl": 1, "zuerst": jetzt, "zuletzt": jetzt,
            "beispiele": [kontext] if kontext else [], "erledigt": False,
        })
        _schreiben(eintraege)
    except Exception:
        pass


def offene(schwere=None) -> list:
    e = [x for x in _lesen() if not x.get("erledigt")]
    if schwere:
        e = [x for x in e if x.get("schwere") == schwere]
    return sorted(e, key=lambda x: (-int(x.get("anzahl", 1)), x.get("zuletzt") or ""))


def erledigen(kennung: str, notiz: str = "") -> None:
    e = _lesen()
    for x in e:
        if x.get("kennung") == kennung:
            x["erledigt"] = True
            x["erledigt_am"] = datetime.datetime.now().isoformat(timespec="seconds")
            if notiz:
                x["erledigt_notiz"] = notiz[:400]
    _schreiben(e)


def bericht(nur_offene: bool = True) -> str:
    """Fertiger Text zum Kopieren — enthält alles, was zum Beheben nötig ist."""
    e = offene() if nur_offene else _lesen()
    if not e:
        return "Fehler-Tagebuch: keine offenen Einträge. 🎉"
    zeilen = [f"Fehler-Tagebuch der Briefing-App ({len(e)} offene Einträge)", ""]
    for x in e:
        zeilen.append(f"[{x.get('schwere','normal').upper()}] {x.get('bereich')} — {x.get('anzahl')}x")
        zeilen.append(f"  zuerst {x.get('zuerst')}, zuletzt {x.get('zuletzt')}")
        zeilen.append(f"  Meldung: {x.get('meldung')}")
        for b in (x.get("beispiele") or [])[:3]:
            zeilen.append(f"  Beispiel: {json.dumps(b, ensure_ascii=False)[:300]}")
        zeilen.append(f"  Kennung: {x.get('kennung')}")
        zeilen.append("")
    return "\n".join(zeilen)
