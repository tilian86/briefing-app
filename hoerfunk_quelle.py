"""📻 Hörfunk als einzige Podcast-Quelle fürs Audio-Briefing.

Hörfunk ist Florians eigener Podcast-Player auf dem Funk-Server (seit 07.10.2026).
Er wischt dort Folgen nach oben = „ins Briefing“. Hörfunk holt die Transkripte
selbst (Feed, nachts Whisper am Mac) und liefert sie hier mit.

Hörfunk ist hier nur die QUELLE für Podcast-Inhalte (Vormerkliste). Das fertige
Briefing hört Florian in ElevenReader, nicht in Hörfunk.

Seit 12.10.2026 die EINZIGE Quelle für Podcasts. Pocket Casts und Apple Podcasts
(TTML-Cache) sind ausgebaut; dieses Modul ersetzt `pocketcasts_fetch.py`
(was Hörfunk braucht, ist hierher gezogen).

Drei Aufrufe genügen:
  preview_hoerfunk()  – lesend: die vorgemerkten Folgen samt Transkript
  hoerfunk_verbaut()  – schreibend: „steckt im Briefing“ → 📻, raus aus der Vormerkliste
  hoerfunk_text()     – Zusammenfassung so, wie Hörfunk sie zeigt

Python 3.9-kompatibel.
"""
import json
import os
import re
import urllib.request

HOERFUNK_URL = os.environ.get("HOERFUNK_URL", "https://hoerfunk.46-225-133-113.sslip.io")
_HOERFUNK_GEHEIMNIS = os.path.expanduser("~/.config/hoerfunk/secret")


def _hoerfunk(pfad: str, daten=None, timeout: int = 60):
    with open(_HOERFUNK_GEHEIMNIS) as fh:
        geheim = fh.read().strip()
    req = urllib.request.Request(HOERFUNK_URL + pfad,
                                 data=json.dumps(daten).encode() if daten is not None else None,
                                 method="POST" if daten is not None else "GET",
                                 headers={"Authorization": "Bearer " + geheim, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def preview_hoerfunk(progress=None):
    """Hörfunks Briefing-Vormerkliste (nur lesend). Liefert (items, status).

    status: "ok" | "leer" (nichts vorgemerkt) | "hoerfunk_fehler" (nicht erreichbar).
    Jedes Item: title, podcast_title, podcast, episode, hoerfunk_id, text, has_transcript.
    Ohne Transkript in Hörfunk bleibt text leer (has_transcript False) — die App zeigt
    die Folge dann als „kein Transkript“; Hörfunk liefert es nach, sobald es eins hat."""
    try:
        folgen = _hoerfunk("/api/briefing/vorgemerkt").get("folgen") or []
    except Exception:
        return [], "hoerfunk_fehler"
    items = []
    for i, f in enumerate(folgen):
        title = f.get("titel") or "?"
        if progress:
            try:
                progress(i + 1, len(folgen), title)
            except Exception:
                pass
        text = f.get("transkript")
        items.append({"title": title, "podcast_title": f.get("podcast_titel") or "",
                      "podcast": f.get("pc_podcast"), "episode": f.get("pc_uuid") or f"hoerfunk:{f['id']}",
                      "hoerfunk_id": f["id"], "text": text, "has_transcript": bool(text)})
    return items, ("ok" if items else "leer")


# Technische Marker, die nur der Briefing-Bau braucht (eigene Zeile oder am Textende).
_HF_MARKER_ZEILEN = re.compile(
    r"^[ \t]*(?:Ende der Podcastzusammenfassung\.?(?:[ \t]*Dies war der Podcast\b[^\n]*)?"
    r"|Weiter geht['’]s\.?|(?:Nächster|Letzter) Beitrag\.[ \t]*Beitrag \d+ von \d+\.?"
    r"|Beitrag \d+ von \d+\.?|Ende des Briefings\.?|m{3,}|Artikel Ende|<<<BRIEFING_SPLIT>>>)[ \t]*$",
    re.IGNORECASE | re.MULTILINE)
_HF_MARKER_ENDE = re.compile(
    r"\s*Ende der Podcastzusammenfassung\.?(?:\s*Dies war der Podcast\b[^\n]*)?\s*$", re.IGNORECASE)


def hoerfunk_text(summary: str) -> str:
    """Podcast-Zusammenfassung so, wie Hörfunk sie zeigt: Markdown (###, fett) bleibt,
    Endmarker und andere Bau-Marker fliegen raus."""
    t = _HF_MARKER_ENDE.sub("", _HF_MARKER_ZEILEN.sub("", summary or ""))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def hoerfunk_verbaut(ids, datum: str = None, texte: dict = None) -> int:
    """Meldet Hörfunk: steckt im Briefing (📻) → raus aus der Vormerkliste.

    texte (09.10.2026): {hoerfunk_id: Zusammenfassung} — Hörfunk zeigt den Briefing-Text
    bei der Folge an. Fehlt ein Text oder ist er leer, fehlt nur diese id im Feld."""
    d = {"ids": [int(x) for x in ids]}
    if datum:
        d["datum"] = datum
    t = {}
    for k, v in (texte or {}).items():
        try:
            s = hoerfunk_text(v)
            if s:
                t[str(int(k))] = s
        except Exception:
            continue
    if t:
        d["texte"] = t
    return int((_hoerfunk("/api/briefing/verbaut", d) or {}).get("n") or 0)
