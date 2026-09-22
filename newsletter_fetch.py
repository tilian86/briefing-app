#!/usr/bin/env python3
"""Newsletter automatisch ins Briefing holen.

Florians Wunsch (22.09.2026): „hol mal die Hotel-Matze-Newsletter aus meinen
Mails immer direkt in mein Briefing automatisch".

Bewusst NICHT ueber IMAP/Gmail: der Newsletter liegt als Substack-Feed offen
im Netz, mit demselben Volltext wie in der Mail. Damit braucht dieser Weg
kein App-Passwort, keinen Schluesselbund-Eintrag und faellt nicht aus, wenn
Google mal wieder an den Zugaengen dreht.

Die Mail im Postfach ist davon unberuehrt. Damit sie gar nicht erst liegen
bleibt, in Gmail einen Filter anlegen:
  Suche: from:(matzehielscher@substack.com)
  Aktion: „Posteingang ueberspringen (archivieren)" + Label „Newsletter".

Jede Ausgabe wird genau EINMAL eingespeist — gemerkt wird das in
~/.briefing_newsletter_gesehen.json. Die App merkt sie sich, sobald der Link
im URL-Feld steht (das Feld liegt gesichert im Entwurf); der headless-Lauf
erst nach dem erfolgreichen Bau, weil er nichts zwischenspeichert.
"""

import json
import os
import re
import sys
import time
import urllib.request
from email.utils import parsedate_to_datetime

KONFIG_PATH = os.path.expanduser("~/.briefing_newsletter.json")
GESEHEN_PATH = os.path.expanduser("~/.briefing_newsletter_gesehen.json")

# Startbelegung. Weitere Newsletter kann Florian in der App dazulegen.
STANDARD = [
    {
        "name": "Matzes High Five (Hotel Matze)",
        "feed": "https://matzehielscher.substack.com/feed",
        "absender": "matzehielscher@substack.com",
        "aktiv": True,
    },
]

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


# ── Konfiguration ───────────────────────────────────────────────────────────
def load_config() -> list:
    """Eingerichtete Newsletter. Fehlt die Datei, gilt die Startbelegung."""
    try:
        d = json.loads(open(KONFIG_PATH, encoding="utf-8").read())
        liste = d.get("newsletter") if isinstance(d, dict) else d
        if isinstance(liste, list) and liste:
            return [x for x in liste if isinstance(x, dict) and x.get("feed")]
    except Exception:
        pass
    return [dict(x) for x in STANDARD]


def save_config(liste) -> list:
    sauber = [x for x in (liste or []) if isinstance(x, dict) and x.get("feed")]
    try:
        with open(KONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"newsletter": sauber}, fh, ensure_ascii=False, indent=1)
    except Exception as exc:
        print(f"Newsletter: Konfiguration nicht speicherbar: {exc}", file=sys.stderr)
    return sauber


# ── Merkliste: was ist schon im Briefing gelandet? ──────────────────────────
def load_gesehen() -> set:
    try:
        d = json.loads(open(GESEHEN_PATH, encoding="utf-8").read())
        return {str(x) for x in (d.get("links") or [])}
    except Exception:
        return set()


def merke_gesehen(ausgaben) -> int:
    """Merkt die Links. Haelt die Datei bei 300 Eintraegen klein."""
    alt = load_gesehen()
    neu = [a["link"] for a in (ausgaben or []) if a.get("link")]
    if not neu:
        return 0
    zusammen = list(alt) + [x for x in neu if x not in alt]
    try:
        with open(GESEHEN_PATH, "w", encoding="utf-8") as fh:
            json.dump({"links": zusammen[-300:], "stand": time.strftime("%Y-%m-%d %H:%M")},
                      fh, ensure_ascii=False)
    except Exception as exc:
        print(f"Newsletter: Merkliste nicht speicherbar: {exc}", file=sys.stderr)
    return len([x for x in neu if x not in alt])


# ── Feed lesen ──────────────────────────────────────────────────────────────
def _cdata(feld: str, block: str) -> str:
    m = re.search(r"<%s>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</%s>" % (feld, feld),
                  block, re.S)
    return (m.group(1) or "").strip() if m else ""


def _als_text(html_roh: str) -> str:
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_roh or "")
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    for roh, hin in (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'),
                     ("&#39;", "'"), ("&lt;", "<"), ("&gt;", ">"), ("&#8217;", "’")):
        t = t.replace(roh, hin)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def feed_lesen(url: str, timeout: int = 25) -> list:
    """Beitraege eines RSS-Feeds als Liste von dicts (neueste zuerst)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        roh = r.read().decode("utf-8", "replace")
    beitraege = []
    for block in re.findall(r"<item>(.*?)</item>", roh, re.S):
        link = _cdata("link", block)
        if not link:
            continue
        volltext = _als_text(_cdata("content:encoded", block)
                             or _cdata("description", block))
        datum = ""
        try:
            datum = parsedate_to_datetime(_cdata("pubDate", block)).strftime("%Y-%m-%d")
        except Exception:
            pass
        beitraege.append({"titel": _cdata("title", block) or "(ohne Titel)",
                          "link": link, "datum": datum, "text": volltext,
                          "zeichen": len(volltext)})
    return beitraege


# ── Was ist neu? ────────────────────────────────────────────────────────────
def neue_ausgaben(max_alter_tage: int = 21, pro_newsletter: int = 3,
                  progress=None, timeout: int = 25) -> dict:
    """Noch nicht eingespeiste Ausgaben aller aktiven Newsletter.

    Gibt {"neu": [...], "fehler": [...]} zurueck. Ein Feed, der nicht
    antwortet, haelt die anderen nicht auf.
    """
    gesehen = load_gesehen()
    grenze = time.time() - max_alter_tage * 86400
    neu, fehler = [], []
    for nl in load_config():
        if not nl.get("aktiv", True):
            continue
        name = nl.get("name") or nl.get("feed")
        try:
            if progress:
                progress(f"📰 {name} …")
            beitraege = feed_lesen(nl["feed"], timeout=timeout)
        except Exception as exc:
            fehler.append({"name": name, "grund": str(exc)[:140]})
            continue
        treffer = 0
        for b in beitraege:
            if b["link"] in gesehen or treffer >= pro_newsletter:
                continue
            if b["datum"]:
                try:
                    if time.mktime(time.strptime(b["datum"], "%Y-%m-%d")) < grenze:
                        continue
                except Exception:
                    pass
            b["newsletter"] = name
            neu.append(b)
            treffer += 1
    neu.sort(key=lambda x: x.get("datum") or "", reverse=True)
    return {"neu": neu, "fehler": fehler}


def als_urls(ausgaben) -> str:
    """Zeilenweise Links — genau das Format des Feldes „eigene Artikel-URLs"."""
    return "\n".join(a["link"] for a in (ausgaben or []) if a.get("link"))


if __name__ == "__main__":
    res = neue_ausgaben(progress=lambda m: print(m, flush=True))
    for a in res["neu"]:
        print("NEU  %s  %-40s %5d Zeichen  %s"
              % (a["datum"], a["titel"][:40], a["zeichen"], a["link"]))
    for f in res["fehler"]:
        print("FEHLER", f["name"], f["grund"])
    print("%d neue Ausgabe(n)" % len(res["neu"]))
