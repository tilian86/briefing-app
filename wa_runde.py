#!/usr/bin/env python3
"""📲 WhatsApp-Runde: das normale Briefing-PDF automatisch an Florians Freundesrunde.

Bisher hat Florian das fertige Briefing-PDF von Hand über eine WhatsApp-Broadcast-Liste
verschickt. Jetzt geht es über Chatfunk (verknüpftes WhatsApp-Gerät auf dem Funk-Server)
EINZELN an jede Person des Verteilers „Briefing-Runde“ — mit kurzem Begleittext:

    📻 *Briefing vom Sonntag, 4. Oktober*
    ☀️ 14°/6°, trocken

    *Die 3 wichtigsten Themen*
    • …
    *Außerdem drin:* Stichwort · Stichwort · …

Es geht immer das HAUPT-PDF des Briefings raus (…_briefing_claude_synthese.pdf), dasselbe,
das die App baut — kein eigenes WhatsApp-PDF mehr (04.10. ausgebaut). Der Begleittext
entsteht nach jedem fertigen Briefing-PDF mit EINEM kurzen Claude-Aufruf über die CLI
(Max-Abo, nie API, Sonnet reicht). Lange Briefings (100+ Beiträge) bekommt Claude als
Verdichtung: alle Überschriften plus der Anfang jedes Beitrags. Klappt der Aufruf nicht,
baut ein fester Notfalltext Datum + Wetter (Open-Meteo, ohne Schlüssel) + Überschriften.

Automatisch gesendet wird höchstens einmal am Tag und nur das Tagesbriefing (App-Lauf und
terminierter Lauf). Das Wochenbriefing (So 19:10) bekommt nur den Begleittext — raus geht
es ausschließlich per Knopf.

Versandzeit (seit 04.10.): per Knopf „jetzt“ oder „um HH:MM“ (schon vorbei → morgen). Die
Automatik plant ein Briefing, das nachts fertig wird (NACHT_AB bis „frühestens ab“, Standard
07:00), für den Morgen. Der Plan liegt auf dem Funk-Server (Chatfunk speichert PDF-Kopie +
Zeit), geht also auch raus, wenn der Mac schläft. Ein Plan je Briefing: ein neuer Auftrag für
dasselbe PDF ersetzt den geplanten (Chatfunk-Schlüssel „briefing:<PDF-Name>“).

Weg zum Server: Mac → https://chatfunk.46-225-133-113.sslip.io/api/* mit dem Server-Secret
(CHATFUNK_URL / CHATFUNK_SECRET in .env, wie die anderen Schlüssel der App). Der
Cloudflare-Worker mit PIN-Login wird bewusst umgangen — er ist nur das Tor fürs Handy.

Der Verteiler liegt auf dem Server (Chatfunk data/verteiler.json, id „briefing-runde“),
damit App-Knopf und Hintergrundlauf dieselbe Liste nutzen. Chatfunk bremst selbst:
Pausen zwischen den Empfängern, Tageslimit, dieselbe Datei pro Tag nur einmal je Person.

Kommandozeile:
  python3 wa_runde.py status                    Verbindung, Runde, letzte Sendung
  python3 wa_runde.py kontakte [SUCHE]          Chatfunk-Kontakte: Name · letzte 4 Ziffern · jid
  python3 wa_runde.py runde                     Mitglieder der Briefing-Runde
  python3 wa_runde.py runde --hinzu JID|ich …   Mitglieder dazu (jid aus „kontakte“)
  python3 wa_runde.py runde --weg JID …         Mitglieder raus
  python3 wa_runde.py runde --leeren            Runde leeren
  python3 wa_runde.py text [PDF]                Begleittext erzeugen und merken
  python3 wa_runde.py senden [PDF] [--an ich] [--nochmal] [--text DATEI|-] [--um HH:MM]
                                                ohne PDF: das neueste Briefing-PDF; --um plant
  python3 wa_runde.py abbrechen [ID]            geplante Sendung abbrechen (ohne ID: alle geplanten)
"""
import base64
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

APP_DIR = Path(__file__).resolve().parent
# Testumgebungen biegen die Zustandsdatei um (wie BRIEFING_DRAFT_PATH & Co.)
STATE_PATH = Path(os.environ.get("BRIEFING_WA_RUNDE_PATH") or (APP_DIR / ".briefing_wa_runde.json"))
JOB_STATUS_PATH = Path(os.environ.get("BRIEFING_JOB_STATUS_PATH") or (APP_DIR / ".briefing_job_status.json"))
ARCHIV_DIRS = [
    Path("/Users/florian/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Briefings"),
    APP_DIR / ".archive",
]
# Der launchd-Dienst kann iCloud nicht auflisten, nur per Pfad lesen — darum zusätzlich
# die Pfade, die App und Wochenlauf ohnehin mitschreiben (Tests setzen beides auf None).
WOCHEN_STATUS_PATH = APP_DIR / ".wochenbriefing_status.json"
ARCHIV_INDEX_PATH = Path.home() / ".briefing_meta_mirror" / ".archive_index.json"
VERTEILER_ID = "briefing-runde"
VERTEILER_NAME = "Briefing-Runde"
CAPTION_MAX = 1024          # WhatsApp-Bildunterschrift; länger → Chatfunk schickt den Text vorweg
DATEI_MAX_BYTES = 15 * 1024 * 1024
TUEBINGEN = (48.52, 9.06)
_WD = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"]
_TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})")
_WD_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
# Nachtruhe der Automatik: fertig ab NACHT_AB bis „frühestens ab“ → für „frühestens ab“ planen.
# 22:00, weil ein Abendlauf bis dahin noch gut ankommt, später niemand mehr eine PDF will.
NACHT_AB = "22:00"
FRUEH_STANDARD = "07:00"
FRUEH_OPTIONEN = ["%02d:%02d" % (h, m) for h in range(5, 12) for m in (0, 30)] + ["12:00"]


class WaRundeFehler(Exception):
    """Verständliche Fehlermeldung für Oberfläche und Job-Status."""


# ------------------------------------------------------------------ Einstellungen

def _konfig() -> Tuple[str, str]:
    """CHATFUNK_URL / CHATFUNK_SECRET aus der Umgebung, sonst aus .env der App."""
    url = os.environ.get("CHATFUNK_URL") or ""
    sec = os.environ.get("CHATFUNK_SECRET") or ""
    if not (url and sec):
        try:
            for zeile in (APP_DIR / ".env").read_text(encoding="utf-8").splitlines():
                zeile = zeile.strip()
                if not zeile or zeile.startswith("#") or "=" not in zeile:
                    continue
                k, v = zeile.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k.strip() == "CHATFUNK_URL" and not url:
                    url = v
                elif k.strip() == "CHATFUNK_SECRET" and not sec:
                    sec = v
        except Exception:
            pass
    return url.rstrip("/"), sec


def eingerichtet() -> bool:
    url, sec = _konfig()
    return bool(url and sec)


def _api(methode: str, pfad: str, body: Optional[dict] = None, timeout: float = 30) -> dict:
    url, sec = _konfig()
    if not (url and sec):
        raise WaRundeFehler("Chatfunk ist nicht eingerichtet (CHATFUNK_URL / CHATFUNK_SECRET fehlen in .env)")
    daten = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url + pfad, data=daten, method=methode, headers={
        "x-chatfunk-secret": sec, "content-type": "application/json", "user-agent": "briefing-wa-runde"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            meldung = json.loads(e.read().decode("utf-8")).get("fehler")
        except Exception:
            meldung = None
        raise WaRundeFehler(meldung or ("Chatfunk antwortet mit Fehler %s" % e.code))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise WaRundeFehler("Chatfunk nicht erreichbar: %s" % (getattr(e, "reason", None) or e))


# ------------------------------------------------------------------ Zustand (lokal)

def _lies_state() -> dict:
    try:
        d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("texte", {})
            d.setdefault("sendungen", [])
            return d
    except Exception:
        pass
    return {"texte": {}, "sendungen": []}


def _schreib_state(d: dict) -> None:
    d["texte"] = dict(sorted(d.get("texte", {}).items())[-30:])
    d["sendungen"] = d.get("sendungen", [])[-40:]
    tmp = STATE_PATH.parent / (".tmp_" + STATE_PATH.name)   # .tmp_* steht in .gitignore
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(STATE_PATH))


def _merke_text(pdf: str, text: str, quelle: str) -> None:
    d = _lies_state()
    d["texte"][os.path.basename(pdf)] = {"text": text, "quelle": quelle,
                                         "erstellt": datetime.datetime.now().isoformat(timespec="seconds")}
    d["letzte_pdf"] = str(pdf)
    _schreib_state(d)


def gemerkter_text(pdf: str) -> Optional[dict]:
    """{'text', 'quelle', 'erstellt'} für dieses PDF, falls schon erzeugt."""
    return _lies_state()["texte"].get(os.path.basename(pdf or ""))


def merke_pdf(pdf: str) -> None:
    d = _lies_state()
    d["letzte_pdf"] = str(pdf)
    _schreib_state(d)


# ------------------------------------------------------------------ Chatfunk: Kontakte, Runde

def status() -> dict:
    return _api("GET", "/api/status", timeout=10)


def kontakte(q: Optional[str] = None, n: int = 3000) -> List[dict]:
    pfad = "/api/kontakte?n=%d" % n + ("&q=" + urllib.parse.quote(q) if q else "")
    return _api("GET", pfad, timeout=20).get("kontakte", [])


def kontakt_label(k: dict) -> str:
    """„Name · …1234“ — die letzten Ziffern unterscheiden gleichnamige Kontakte."""
    if k.get("ich"):
        return k.get("name") or "Ich (eigene Nummer)"
    ziffern = re.sub(r"\D", "", k.get("nummer") or "")
    return "%s · …%s" % (k.get("name") or k.get("jid", "?"), ziffern[-4:]) if ziffern else (k.get("name") or k.get("jid", "?"))


def runde() -> dict:
    """{'id', 'name', 'mitglieder': [{jid, name, nummer}]} — leer, wenn noch nicht angelegt."""
    for v in _api("GET", "/api/verteiler", timeout=15).get("verteiler", []):
        if v.get("id") == VERTEILER_ID:
            return v
    return {"id": VERTEILER_ID, "name": VERTEILER_NAME, "mitglieder": []}


def runde_setzen(jids: List[str]) -> dict:
    _api("POST", "/api/verteiler", {"id": VERTEILER_ID, "name": VERTEILER_NAME, "mitglieder": list(jids)}, timeout=20)
    return runde()


# ------------------------------------------------------------------ Briefing-PDFs finden

# Haupt-PDFs: „<ts>_briefing_claude_synthese.pdf“ (auch _claude, _claude_erzaehl, Längen-Suffix
# wie _int-m) und „<ts>_wochenbriefing_7d.pdf“. Nicht: Kompaktfassungen (_tagesbriefing_kompakt…)
# und alte WhatsApp-Lese-PDFs (_whatsapp.pdf, seit 04.10. ausgebaut, liegen evtl. noch im Archiv).
_PDF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(?:briefing[\w-]*|wochenbriefing_\d+d)\.pdf$")


def ist_briefing_pdf(pdf: Optional[str]) -> bool:
    n = os.path.basename(str(pdf or ""))
    return bool(_PDF_RE.match(n)) and not n.endswith("_whatsapp.pdf")


def ist_wochenbriefing(pdf: Optional[str]) -> bool:
    return "_wochenbriefing_" in os.path.basename(str(pdf or ""))


def _json_datei(pfad) -> object:
    if not pfad:
        return None
    try:
        return json.loads(Path(pfad).read_text(encoding="utf-8"))
    except Exception:
        return None


def briefing_pdfs(n: int = 8) -> List[str]:
    """Die neuesten Briefing-PDFs (Tages- und Wochenbriefing), neuestes zuerst."""
    kandidaten = []
    st = _lies_state()
    kandidaten += [st.get("letzte_pdf")] + [x.get("pdf") for x in st.get("sendungen") or []]
    job = _json_datei(JOB_STATUS_PATH)
    if isinstance(job, dict):
        kandidaten += [r.get("pdf") for r in job.get("results") or [] if isinstance(r, dict)]
    wb = _json_datei(WOCHEN_STATUS_PATH)
    if isinstance(wb, dict):
        kandidaten.append(wb.get("pdf"))
    idx = _json_datei(ARCHIV_INDEX_PATH)
    for x in idx if isinstance(idx, list) else []:
        x = str(x)
        if x.endswith("_eleven-reader.txt") and os.sep + "Texte" + os.sep in x:
            # Texte/<stamm>_eleven-reader.txt → <Archiv>/<stamm>.pdf
            ordner, name = os.path.split(x)
            x = os.path.join(os.path.dirname(ordner), name[:-len("_eleven-reader.txt")] + ".pdf")
        kandidaten.append(x)
    for d in ARCHIV_DIRS:
        for muster in ("*.pdf", "Wochen-Briefings/*.pdf"):
            try:
                kandidaten += [str(p) for p in d.glob(muster)]
            except Exception:
                pass
    je_name = {}
    for p in kandidaten:
        if p and ist_briefing_pdf(p) and os.path.basename(p) not in je_name and os.path.exists(p):
            je_name[os.path.basename(p)] = str(p)
    return [je_name[k] for k in sorted(je_name, reverse=True)][:n]


def neuestes_pdf(nur_tages: bool = False) -> Optional[str]:
    for p in briefing_pdfs(30):
        if not (nur_tages and ist_wochenbriefing(p)):
            return p
    return None


def txt_zum_pdf(pdf: Optional[str]) -> Optional[str]:
    """Der saubere Text zum PDF (ElevenReader-Text bzw. Wochenbriefing-.txt), falls da."""
    if not pdf:
        return None
    ordner, name = os.path.split(str(pdf))
    stamm = name[:-4] if name.lower().endswith(".pdf") else name
    if ist_wochenbriefing(pdf):
        orte = [os.path.join(ordner, stamm + ".txt")]
    else:
        orte = [os.path.join(ordner, "Texte", stamm + "_eleven-reader.txt"),
                str(Path.home() / ".briefing_meta_mirror" / "Texte" / (stamm + "_eleven-reader.txt"))]
    return next((o for o in orte if os.path.exists(o)), None)


def pdf_label(pdf: str) -> str:
    """„📰 Tagesbriefing Di 29.9. · 14:29 Uhr“ für die Auswahl in der App."""
    n = os.path.basename(pdf)
    d, m = briefing_datum(pdf), _TS_RE.search(n)
    art = "🗓️ Wochenbriefing" if ist_wochenbriefing(pdf) else "📰 Tagesbriefing"
    out = "%s %s %d.%d." % (art, _WD[d.weekday()][:2], d.day, d.month)
    if m:
        out += " · %s:%s Uhr" % (m.group(4), m.group(5))
    variante = re.sub(r"^.*?_briefing(?:_claude(?:_synthese|_erzaehl)?)?", "", n[:-4]).strip("_")
    return out + (" · " + variante if variante and not ist_wochenbriefing(pdf) else "")


def briefing_datum(pdf: Optional[str]) -> datetime.date:
    m = _TS_RE.search(os.path.basename(pdf or ""))
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return datetime.date.today()


def anzeige_dateiname(pdf: str) -> str:
    """Freundlicher Dateiname für die Empfänger statt „2026-09-29_14-29_briefing_claude_synthese.pdf“."""
    d = briefing_datum(pdf)
    if ist_wochenbriefing(pdf):
        return "Wochenbriefing %d.%d.%d.pdf" % (d.day, d.month, d.year)
    return "Briefing %s %d.%d.%d.pdf" % (_WD[d.weekday()], d.day, d.month, d.year)


# ------------------------------------------------------------------ Wetter (Open-Meteo, ohne Schlüssel)

def wetter_open_meteo(datum: datetime.date) -> Optional[dict]:
    if os.environ.get("WA_RUNDE_OHNE_NETZ"):
        return None
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max"
           "&timezone=Europe%%2FBerlin&start_date=%s&end_date=%s" % (TUEBINGEN[0], TUEBINGEN[1], datum, datum))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"user-agent": "briefing-wa-runde"}), timeout=8) as r:
            t = json.loads(r.read().decode("utf-8")).get("daily") or {}
        w = {"max": t["temperature_2m_max"][0], "min": t["temperature_2m_min"][0],
             "regen_mm": (t.get("precipitation_sum") or [0])[0] or 0.0,
             "regen_p": (t.get("precipitation_probability_max") or [0])[0] or 0,
             "code": (t.get("weather_code") or [0])[0] or 0}
        if w["max"] is None or w["min"] is None:
            return None
        return w
    except Exception:
        return None


def _grad(x: float) -> str:
    v = int(round(float(x)))
    return "%d°" % (0 if v == 0 else v)


def wetter_zeile(w: Optional[dict]) -> str:
    """„☀️ 14°/6°, trocken“ aus den Open-Meteo-Tageswerten."""
    if not w:
        return ""
    code, mm, p = int(w.get("code") or 0), float(w.get("regen_mm") or 0), int(w.get("regen_p") or 0)
    if code >= 95:
        emoji, wort = "⛈️", "Gewitter"
    elif code in (71, 73, 75, 77, 85, 86):
        emoji, wort = "🌨️", "Schnee"
    elif mm >= 0.5 or code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        schauer = code in (80, 81, 82)
        emoji = "🌦️" if schauer or mm < 2 else "🌧️"
        wort = ("viel Regen" if mm >= 8 else ("Schauer" if schauer else "Regen")) if mm >= 0.5 else "etwas Niesel"
    else:
        emoji = {0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️", 45: "🌫️", 48: "🌫️"}.get(code, "🌤️")
        wort = "vielleicht Regen" if p >= 50 else "trocken"
    return "%s %s/%s, %s" % (emoji, _grad(w["max"]), _grad(w["min"]), wort)


def _messwerte_text(w: Optional[dict], datum: datetime.date) -> str:
    if not w:
        return "Messwerte: nicht verfügbar."
    return ("Messwerte Open-Meteo für Tübingen am %s: Höchst %.0f °C, Tiefst %.0f °C, Regen %.1f mm, "
            "Regenwahrscheinlichkeit %d %%, Wettercode %d. Vorschlag: %s" % (
                datum.strftime("%d.%m.%Y"), w["max"], w["min"], float(w["regen_mm"]), int(w["regen_p"]),
                int(w["code"]), wetter_zeile(w)))


# ------------------------------------------------------------------ Inhalt des Briefings

_ROH_MAX = 600000   # mehr liest niemand: ~220k Zeichen hat ein langes Tagesbriefing


def inhalt_lesen(pdf: Optional[str], txt: Optional[str] = None, max_zeichen: int = 40000) -> str:
    """Text des Briefings: sauberer Text (ElevenReader / Wochen-.txt), sonst aus dem PDF
    gezogen. Länger als max_zeichen → Verdichtung über ALLE Beiträge (nicht abschneiden,
    sonst sähe Claude von 120 Beiträgen nur die ersten 25)."""
    roh = ""
    txt = txt or txt_zum_pdf(pdf)
    if txt and os.path.exists(txt):
        try:
            with open(txt, encoding="utf-8") as f:
                roh = f.read(_ROH_MAX)
        except Exception:
            roh = ""
    if not roh.strip() and pdf and os.path.exists(pdf):
        try:
            import pypdf
            teile, n = [], 0
            for seite in pypdf.PdfReader(pdf).pages:
                t = seite.extract_text() or ""
                teile.append(t)
                n += len(t)
                if n > _ROH_MAX:
                    break
            roh = "\n".join(teile)
        except Exception:
            roh = ""
    return _verdichten(roh, max_zeichen)


_SEITENKOPF_RE = re.compile(r"^(Seite \d+|.*Tagesbriefing\s+-\s+.*|DIGITALES AUDIO-BRIEFING)$")
_BEITRAG_RE = re.compile(r"(?m)^[ \t]*(Beitrag \d+ von \d+\.?)[ \t]*$")


def _verdichten(inhalt: str, max_zeichen: int) -> str:
    """Kopf (mit „Die drei wichtigsten Themen“) + je Beitrag Überschrift und Anfang;
    der Wetter-Beitrag etwas länger (Zahlen fürs Wetter)."""
    if len(inhalt) <= max_zeichen:
        return inhalt
    teile = _BEITRAG_RE.split(inhalt)   # [Kopf, Marke1, Text1, Marke2, Text2, …]
    if len(teile) < 3:
        return inhalt[:max_zeichen]
    kopf = teile[0].strip()[:6000]
    bloecke = []
    for marke, text in zip(teile[1::2], teile[2::2]):
        zeilen = [z.strip() for z in text.splitlines() if z.strip() and not _SEITENKOPF_RE.match(z.strip())]
        bloecke.append((marke, zeilen[0] if zeilen else "", " ".join(zeilen[1:])))
    fest = len(kopf) + sum(len(m) + len(t) + 3 for m, t, _ in bloecke) + 1700
    je = max(0, min(800, (max_zeichen - fest) // max(1, len(bloecke))))
    out = [kopf, "", "[Verdichtet: alle %d Beiträge mit Überschrift und Anfang]" % len(bloecke)]
    for marke, titel, rumpf in bloecke:
        n = 1200 if titel.lower().startswith("wetter") else je
        out += ["", marke, titel] + ([_kuerzen(rumpf, n)] if rumpf and n >= 60 else [])
    return "\n".join(out)[:max_zeichen]


def _ueberschriften(inhalt: str) -> List[str]:
    """Titel nach „Beitrag N von M.“ (Format von PDF und ElevenReader-Text)."""
    zeilen = [z.strip() for z in inhalt.splitlines()]
    titel = []
    for i, z in enumerate(zeilen):
        if not re.match(r"^Beitrag \d+ von \d+\.?$", z):
            continue
        for j in range(i + 1, min(i + 5, len(zeilen))):
            nxt = zeilen[j]
            if not nxt or _SEITENKOPF_RE.match(nxt):
                continue
            # Im PDF bricht eine lange Überschrift um: kurze Folgezeile ohne Punkt gehört dazu
            folge = zeilen[j + 1] if j + 1 < len(zeilen) else ""
            if len(nxt) >= 30 and folge and len(folge) <= 40 and not folge.endswith((".", "!", "?")):
                nxt = nxt + " " + folge
            if not nxt.lower().startswith("wetter"):
                titel.append(nxt)
            break
    return titel


# Gliederungs-Überschriften des Wochenbriefings, die kein Thema sind
_WOCHEN_GERUEST = ("die lage in", "die großen stränge", "lokales aus", "querverbindungen",
                   "unterschätzte signale", "was daraus geworden", "der coach-blick", "recap der woche",
                   "was wirklich bleibt", "bis zum nächsten", "wochenrückblick")


def _wochen_ueberschriften(inhalt: str) -> List[str]:
    """Themen-Überschriften des Wochenbriefings („#### Pflege: Gestritten wird …“)."""
    out = []
    for z in re.findall(r"(?m)^#{2,4}\s+(.+?)\s*$", inhalt):
        z = _saeubern(z)
        if z and not z.lower().startswith(_WOCHEN_GERUEST):
            out.append(z)
    return out


def _top3(inhalt: str) -> List[str]:
    """Die drei wichtigsten Themen, wenn das Briefing selbst eine Top-3 hat (erster Satz je Punkt)."""
    m = re.search(r"Die drei wichtigsten Themen[^\n]*\n(.*?)(?:\nBeitrag \d+ von|\Z)", inhalt, re.S)
    if not m:
        return []
    punkte = re.split(r"(?m)^\s*[123]\.\s+", m.group(1))
    out = []
    for p in punkte[1:4]:
        p = re.sub(r"\s+", " ", p).strip()
        satz = re.split(r"(?<=[.!?])\s", p, maxsplit=1)[0]
        out.append(_kuerzen(satz, 150))
    return out


def _kuerzen(t: str, n: int) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",;:–-") + " …"


def _stichwort(titel: str) -> str:
    """„Keine Käufer, kein Weiterbetrieb: BioNTech schließt …“ → Teil nach dem Doppelpunkt, kurz."""
    teil = titel.split(":", 1)[1] if ":" in titel and len(titel.split(":", 1)[1].strip()) > 8 else titel
    return _kuerzen(teil.strip(" .„“\""), 42)


def _saeubern(t: str) -> str:
    """Kein Markdown, keine Links, keine Aufzählungszeichen — WhatsApp-tauglich."""
    t = re.sub(r"https?://\S+", "", str(t or ""))
    t = re.sub(r"[*_#`>]+", "", t)
    t = re.sub(r"^\s*(?:[-•–]|\d+[.)])\s*", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _zusammensetzen(datum: datetime.date, wetter: str, themen: List[str], ausserdem: List[str],
                    woche: bool = False) -> str:
    zeilen = ["📻 *%s vom %s, %d. %s*" % ("Wochenbriefing" if woche else "Briefing",
                                          _WD[datum.weekday()], datum.day, _MONATE[datum.month - 1])]
    if wetter and not woche:
        zeilen.append(wetter)
    if themen:
        zeilen += ["", "*Die 3 wichtigsten Themen%s*" % (" der Woche" if woche else "")] + ["• " + t for t in themen[:3]]
    if ausserdem:
        stich, laenge = [], 0
        for s in ausserdem:
            if laenge + len(s) + 3 > 190:   # höchstens ~2 Zeilen am Handy
                break
            stich.append(s)
            laenge += len(s) + 3
        if stich:
            zeilen += ["", "*Außerdem drin:* " + " · ".join(stich)]
    return "\n".join(zeilen)


def notfall_text(inhalt: str, datum: datetime.date, wetter: str, woche: bool = False) -> str:
    """Ohne Claude: Datum + Wetter + Top-3 bzw. Überschriften der ersten Beiträge."""
    titel = _wochen_ueberschriften(inhalt) if woche else _ueberschriften(inhalt)
    themen = [] if woche else _top3(inhalt)
    rest = titel
    if len(themen) < 3:
        themen = [_kuerzen(t, 150) for t in titel[:3]]
        rest = titel[3:]
    gesehen, ausserdem = set(), []
    schon = " ".join(themen).lower()
    for t in rest:
        s = _stichwort(t)
        # Was schon unter den 3 Themen steht (gleiches markantes Wort), nicht nochmal nennen
        markant = [w for w in re.findall(r"\w{5,}", s) if w[0].isupper()]
        if not s or s.lower() in gesehen or (markant and markant[0].lower() in schon):
            continue
        gesehen.add(s.lower())
        ausserdem.append(s)
    return _zusammensetzen(datum, wetter, themen, ausserdem[:6], woche=woche)


_PROMPT = """Du schreibst den Begleittext für ein PDF, das Florian per WhatsApp an Freunde schickt: sein Nachrichten-Briefing.
Lies das Briefing unten und antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Vorrede, ohne Markdown:
{"wetter": "...", "themen": ["...", "...", "..."], "ausserdem": ["...", "..."]}

Regeln:
- themen: die 3 wichtigsten Themen des Tages, je EIN kurzer Satz (höchstens 14 Wörter), sachlich und konkret, ohne Links, ohne Emojis, ohne Markdown. Nennt das Briefing selbst „Die drei wichtigsten Themen“, nimm genau diese.
- ausserdem: 5 bis 8 weitere Themen aus dem Briefing als knappe Stichworte (je 1 bis 3 Wörter, z. B. „Bahnstreik“, „Tübinger Gemeinderat“, „Bundesliga“), nicht doppelt zu den themen, wichtigste zuerst.
- wetter: EINE Zeile für Tübingen am Briefing-Tag im Format „<Emoji> <Höchst>°/<Tiefst>°, <2 bis 4 Wörter>“, z. B. „☀️ 14°/6°, trocken“ oder „🌦️ 11°/7°, nachmittags Schauer“. Zahlen nur aus dem Wetterabschnitt des Briefings oder aus den Messwerten unten, nichts erfinden; fehlt im Briefing ein Wert, nimm die Messwerte. Leerer String, wenn es gar keine Zahlen gibt.
- Ist das Briefing sehr lang, bekommst du es verdichtet (jeder Beitrag mit Überschrift und Anfang) — wähle trotzdem aus dem GANZEN Briefing.
"""
_PROMPT_WOCHE = """Achtung, das ist das WOCHENBRIEFING (Rückblick auf die Woche): themen = die 3 wichtigsten Linien der Woche, ausserdem = weitere Themen der Woche, wetter = leerer String.
"""


def _claude_text(inhalt: str, datum: datetime.date, w: Optional[dict], woche: bool = False) -> Optional[dict]:
    """EIN kurzer CLI-Aufruf (Max-Abo, Sonnet). None, wenn es nicht klappt."""
    if os.environ.get("WA_RUNDE_OHNE_CLAUDE"):
        return None
    try:
        import briefing_core as core
    except Exception:
        return None
    cli = core._locate_claude_cli()
    if not cli or not inhalt.strip():
        return None
    eingabe = (_PROMPT + (_PROMPT_WOCHE if woche else "")
               + "\nBriefing-Tag: %s, %s\n%s\n\n=== BRIEFING ===\n%s"
               % (_WD[datum.weekday()], datum.strftime("%d.%m.%Y"),
                  "" if woche else _messwerte_text(w, datum), inhalt))
    cmd = [cli, "--print", "--output-format", "text", "--model", core.cli_modell("sonnet"),
           "--dangerously-skip-permissions", "--effort", core.cli_effort("mechanik"),
           "--append-system-prompt", "Antworte ausschließlich mit dem JSON-Objekt, ohne Vorrede oder Erklärung."]
    try:
        sr = core._run_claude_cli_subprocess_streaming(
            cmd, eingabe, timeout_seconds=180, expected_duration_s=30.0,
            label="WhatsApp-Runde: Begleittext (Claude)", use_caffeinate=False)
    except Exception:
        return None
    if not sr.get("ok") or sr.get("returncode") not in (0, None):
        return None
    m = re.search(r"\{.*\}", sr.get("stdout") or "", re.S)
    d = core._loads_llm_json(m.group(0)) if m else None
    return d if isinstance(d, dict) else None


def begleittext(pdf: Optional[str], txt: Optional[str] = None) -> Tuple[str, str]:
    """(Text, Quelle) — Quelle „claude“ oder „notfall“. Wirft nie."""
    datum = briefing_datum(pdf)
    woche = ist_wochenbriefing(pdf)
    inhalt = inhalt_lesen(pdf, txt)
    w = None if woche else wetter_open_meteo(datum)
    om_zeile = wetter_zeile(w)
    d = None
    try:
        d = _claude_text(inhalt, datum, w, woche=woche)
    except Exception:
        d = None
    if d:
        themen = [_kuerzen(_saeubern(t), 160) for t in (d.get("themen") or []) if _saeubern(t)]
        ausserdem, gesehen = [], set()
        for s in d.get("ausserdem") or []:
            s = _kuerzen(_saeubern(s), 40)
            if s and s.lower() not in gesehen:
                gesehen.add(s.lower())
                ausserdem.append(s)
        wetter = _saeubern(d.get("wetter") or "")
        if not re.search(r"-?\d+°\s*/\s*-?\d+°", wetter) or len(wetter) > 60:
            wetter = om_zeile
        if len(themen) >= 3:
            return _zusammensetzen(datum, wetter, themen[:3], ausserdem, woche=woche), "claude"
    return notfall_text(inhalt, datum, om_zeile, woche=woche), "notfall"


# ------------------------------------------------------------------ Versandzeit

def _hhmm(uhrzeit) -> Tuple[int, int]:
    if isinstance(uhrzeit, (datetime.time, datetime.datetime)):
        return uhrzeit.hour, uhrzeit.minute
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", str(uhrzeit or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise WaRundeFehler("Uhrzeit nicht verstanden: %s (so: 7:00)" % uhrzeit)
    return int(m.group(1)), int(m.group(2))


def naechster_zeitpunkt(uhrzeit, jetzt: Optional[datetime.datetime] = None) -> datetime.datetime:
    """Heute um diese Uhrzeit — oder morgen, wenn sie heute schon vorbei ist."""
    jetzt = jetzt or datetime.datetime.now()
    h, m = _hhmm(uhrzeit)
    z = jetzt.replace(hour=h, minute=m, second=0, microsecond=0)
    return z if z > jetzt else z + datetime.timedelta(days=1)


def nachtplan(frueh: Optional[str], jetzt: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
    """Für die Automatik: fertig zwischen NACHT_AB und „frühestens ab“ → dieser Zeitpunkt
    (heute bzw. morgen früh); tagsüber oder ohne Nachtruhe None = sofort."""
    if not frueh:
        return None
    jetzt = jetzt or datetime.datetime.now()
    t = (jetzt.hour, jetzt.minute)
    if t >= _hhmm(NACHT_AB) or t < _hhmm(frueh):
        return naechster_zeitpunkt(frueh, jetzt)
    return None


def zeit_text(zeit) -> str:
    """ms/datetime → „Mo 5.10. 07:00“."""
    d = zeit if isinstance(zeit, datetime.datetime) else datetime.datetime.fromtimestamp(float(zeit) / 1000)
    return "%s %d.%d. %02d:%02d" % (_WD_KURZ[d.weekday()], d.day, d.month, d.hour, d.minute)


def tag_text(zeit: datetime.datetime, jetzt: Optional[datetime.datetime] = None) -> str:
    """„heute“ / „morgen“ / „übermorgen“ / „“."""
    diff = (zeit.date() - (jetzt or datetime.datetime.now()).date()).days
    return {0: "heute", 1: "morgen", 2: "übermorgen"}.get(diff, "")


# ------------------------------------------------------------------ Senden

def _sendung_aus(a: dict, alt: Optional[dict] = None) -> dict:
    s = dict(alt or {})
    s.update({
        "id": a.get("id"), "status": a.get("status"), "gesendet": a.get("gesendet", 0),
        "fehler": a.get("fehler", 0), "uebersprungen": a.get("uebersprungen", 0), "offen": a.get("offen", 0),
        "empfaenger": [{"name": x.get("name"), "status": x.get("status"), "fehler": x.get("fehler"),
                        "ich": bool(x.get("ich")), "um": x.get("um")} for x in a.get("empfaenger") or []],
        "fertig": a.get("fertigUm"),
    })
    for k in ("zeit", "grund"):      # nur geplante Aufträge haben das
        if a.get(k):
            s[k] = a[k]
    return s


def _speichere_sendung(s: dict) -> None:
    d = _lies_state()
    d["sendungen"] = [x for x in d["sendungen"] if x.get("id") != s.get("id")] + [s]
    _schreib_state(d)


def senden(pdf: str, text: str, an: Optional[List[str]] = None, nochmal: bool = False,
           auto: bool = False, warten_s: int = 0, zeit: Optional[datetime.datetime] = None) -> dict:
    """Schickt das PDF über Chatfunk. an=None → an die Briefing-Runde. Gibt die Sendung zurück
    (Chatfunk arbeitet im Hintergrund; Stand später mit aktualisieren()). Mit zeit plant
    Chatfunk die Sendung auf dem Server. Ein neuer Auftrag für dasselbe PDF (Runde bzw. Test)
    ersetzt dort einen noch geplanten."""
    p = Path(pdf)
    if not p.exists():
        raise WaRundeFehler("PDF nicht gefunden: %s" % p.name)
    roh = p.read_bytes()
    if len(roh) > DATEI_MAX_BYTES:
        raise WaRundeFehler("Das PDF ist zu groß (%.1f MB, höchstens 15 MB)" % (len(roh) / 1048576))
    body = {"datei": base64.b64encode(roh).decode("ascii"), "dateiName": anzeige_dateiname(pdf),
            "text": (text or "").strip() or None, "nochmal": bool(nochmal),
            "quelle": "briefing-auto" if auto else "briefing", "warten": int(warten_s or 0),
            "schluessel": ("briefing-test:" if an else "briefing:") + p.name}
    if zeit is not None:
        if zeit <= datetime.datetime.now():
            raise WaRundeFehler("Die Versandzeit liegt in der Vergangenheit")
        body["zeit"] = int(zeit.timestamp() * 1000)
        body["warten"] = 0
    if an:
        body["an"] = list(an)
    else:
        body["verteiler"] = VERTEILER_ID
    a = _api("POST", "/api/datei", body, timeout=60 + int(body["warten"]))["auftrag"]
    s = _sendung_aus(a, {"pdf": str(pdf), "datei": body["dateiName"], "auto": bool(auto),
                         "runde": not an, "test": bool(an) and list(an) == ["ich"],
                         "schluessel": body["schluessel"],
                         "erstellt": datetime.datetime.now().isoformat(timespec="seconds"),
                         "text": (text or "")[:1500]})
    # Chatfunk hat einen älteren Plan mit demselben Schlüssel ersetzt — lokal nachziehen
    d = _lies_state()
    for x in d["sendungen"]:
        if x.get("status") == "geplant" and x.get("schluessel") == body["schluessel"] and x.get("id") != s["id"]:
            x["status"], x["grund"] = "abgebrochen", "ersetzt"
    d["sendungen"] = [x for x in d["sendungen"] if x.get("id") != s["id"]] + [s]
    _schreib_state(d)
    return s


def aktualisieren(sendung_id: str) -> dict:
    """Holt den aktuellen Stand eines Auftrags von Chatfunk und merkt ihn lokal."""
    alt = next((x for x in _lies_state()["sendungen"] if x.get("id") == sendung_id), None)
    try:
        a = _api("GET", "/api/datei/%s" % sendung_id, timeout=15)["auftrag"]
    except WaRundeFehler as e:
        if "Nicht gefunden" not in str(e) or not alt:
            raise
        a = dict(alt, status="fertig" if alt.get("status") != "geplant" else "abgebrochen",
                 grund="auf dem Server nicht mehr da", fertigUm=alt.get("fertig"))
    s = _sendung_aus(a, alt)
    _speichere_sendung(s)
    return s


def _plan_aktion(sendung_id: str, methode: str, pfad: str) -> dict:
    alt = next((x for x in _lies_state()["sendungen"] if x.get("id") == sendung_id), None)
    a = _api(methode, pfad, timeout=20)["auftrag"]
    s = _sendung_aus(a, alt)
    _speichere_sendung(s)
    return s


def abbrechen(sendung_id: str) -> dict:
    """Geplante Sendung auf dem Server abbrechen (PDF-Kopie wird dort gelöscht)."""
    return _plan_aktion(sendung_id, "DELETE", "/api/datei/%s" % sendung_id)


def jetzt_senden(sendung_id: str) -> dict:
    """Geplante Sendung sofort starten (mit dem Text vom Planen)."""
    return _plan_aktion(sendung_id, "POST", "/api/datei/%s/jetzt" % sendung_id)


_OFFEN = ("geplant", "wartet", "laeuft")
_zuletzt_aufgefrischt = [0.0]


def offene_auffrischen(min_abstand: float = 20.0) -> None:
    """Geplante/laufende Sendungen mit dem Server abgleichen (ausgeführt, abgebrochen in der
    Chatfunk-App, ersetzt). Höchstens alle min_abstand Sekunden; Fehler still."""
    if time.time() - _zuletzt_aufgefrischt[0] < min_abstand:
        return
    _zuletzt_aufgefrischt[0] = time.time()
    for x in [x for x in _lies_state()["sendungen"] if x.get("status") in _OFFEN][-6:]:
        try:
            aktualisieren(x["id"])
        except Exception:
            pass


def geplante_sendungen() -> List[dict]:
    """Lokal bekannte, noch geplante Sendungen (früheste zuerst)."""
    return sorted([x for x in _lies_state()["sendungen"] if x.get("status") == "geplant"],
                  key=lambda x: x.get("zeit") or 0)


def plan_text(s: dict) -> str:
    """„📅 geplant: Mo 5.10. 07:00 an 7 Leute“."""
    emp = s.get("empfaenger") or []
    andere = len([x for x in emp if not x.get("ich")])
    wen = ("%d %s" % (andere, "Person" if andere == 1 else "Leute")) if andere else "dich"
    return "📅 geplant: %s an %s" % (zeit_text(s["zeit"]) if s.get("zeit") else "?", wen)


def warte_auf(sendung_id: str, max_s: int = 900, takt: float = 5.0) -> dict:
    bis = time.time() + max_s
    s = aktualisieren(sendung_id)
    while s.get("status") != "fertig" and time.time() < bis:
        time.sleep(takt)
        try:
            s = aktualisieren(sendung_id)
        except WaRundeFehler:
            pass   # kurzer Aussetzer — weiter warten
    return s


def letzte_sendung(auch_geplant: bool = False) -> Optional[dict]:
    """Die jüngste Sendung — geplante nur mit auch_geplant (die zeigt die Oberfläche extra),
    abgebrochene Pläne nie (sonst verdeckt „✕ abgebrochen“ die letzte echte Quittung)."""
    for s in reversed(_lies_state()["sendungen"]):
        if s.get("status") == "abgebrochen":
            continue
        if auch_geplant or s.get("status") != "geplant":
            return s
    return None


def _start_ts(s: dict) -> float:
    if s.get("zeit"):
        return float(s["zeit"]) / 1000
    try:
        return datetime.datetime.fromisoformat(s.get("erstellt")).timestamp()
    except Exception:
        return 0.0


def laufende_sendung() -> Optional[dict]:
    """Eine Sendung, die gerade läuft (gestartet vor < 1 h) — geplante zählen nicht."""
    for s in reversed(_lies_state()["sendungen"]):
        if s.get("status") in ("wartet", "laeuft") and time.time() - _start_ts(s) < 3600:
            return s
    return None


def liefertag(s: dict) -> datetime.date:
    """An welchem Tag die Sendung rausging bzw. rausgeht (geplant: Plan-Zeit)."""
    return datetime.date.fromtimestamp(_start_ts(s))


def runde_am_tag(tag: datetime.date, pdf: Optional[str] = None, nur_tages: bool = False,
                 mit_geplant: bool = True) -> Optional[dict]:
    """Letzte Runden-Sendung für diesen Tag, bei der jemand etwas bekam, die noch läuft oder
    (mit_geplant) geplant ist — optional nur dieses PDF bzw. nur Tagesbriefings."""
    for s in reversed(_lies_state()["sendungen"]):
        if not s.get("runde") or s.get("status") == "abgebrochen" or liefertag(s) != tag:
            continue
        if pdf and os.path.basename(s.get("pdf") or "") != os.path.basename(pdf):
            continue
        if nur_tages and ist_wochenbriefing(s.get("pdf")):
            continue
        if s.get("status") == "geplant" and not mit_geplant:
            continue
        if s.get("gesendet") or s.get("status") != "fertig":
            return s
    return None


def heute_an_runde(pdf: Optional[str] = None, nur_tages: bool = False, mit_geplant: bool = True) -> Optional[dict]:
    return runde_am_tag(datetime.date.today(), pdf=pdf, nur_tages=nur_tages, mit_geplant=mit_geplant)


def _uhr(s: dict) -> str:
    try:
        if s.get("fertig"):
            return datetime.datetime.fromtimestamp(s["fertig"] / 1000).strftime("%H:%M")
        return datetime.datetime.fromisoformat(s.get("erstellt")).strftime("%H:%M")
    except Exception:
        return ""


def quittung(s: Optional[dict]) -> Tuple[str, List[str]]:
    """(„✅ an 7 gesendet, 14:32“, [Fehlerzeilen je Person]) für die Oberfläche."""
    if not s:
        return "", []
    if s.get("status") == "geplant":
        return plan_text(s), []
    if s.get("status") == "abgebrochen":
        wann = " für %s" % zeit_text(s["zeit"]) if s.get("zeit") else ""
        warum = " (durch neuen Auftrag ersetzt)" if any(w in str(s.get("grund")) for w in ("ersetzt", "sofort")) else ""
        return "✕ Plan%s abgebrochen%s — nichts gesendet" % (wann, warum), []
    emp = s.get("empfaenger") or []
    an_andere = [x for x in emp if x.get("status") == "gesendet" and not x.get("ich")]
    fehler = ["%s: %s" % (x.get("name"), x.get("fehler") or "Fehler") for x in emp if x.get("status") == "fehler"]
    if s.get("status") != "fertig":
        fertig = len([x for x in emp if x.get("status") != "wartet"])
        return "⏳ Wird gesendet … %d von %d (mit Pausen dazwischen)" % (fertig, len(emp)), fehler
    teile = []
    if an_andere:
        teile.append("✅ an %d gesendet" % len(an_andere))
    if any(x.get("status") == "gesendet" and x.get("ich") for x in emp):
        teile.append("🧪 an dich gesendet" if not an_andere else "+ an dich")
    if s.get("uebersprungen"):
        teile.append("%d übersprungen (heute schon bekommen)" % s["uebersprungen"])
    if not teile:
        teile.append("❌ an niemanden gesendet")
    return ", ".join(teile) + ", " + _uhr(s), fehler


def job_eintrag(s: dict) -> str:
    """Kurzform für results[].wa_runde im Job-Status (wie results[].upload)."""
    text, fehler = quittung(s)
    if fehler:
        return "fail: %s — %s" % (text, "; ".join(fehler)[:300])
    if s.get("status") == "fertig" and not s.get("gesendet") and not s.get("uebersprungen"):
        return "fail: " + text
    return "ok: " + text


# ------------------------------------------------------------------ Für den Briefing-Lauf

def nach_briefing(pdf: str, txt: Optional[str] = None, auto: bool = False, log=None,
                  nachts_bis: Optional[str] = None) -> str:
    """Nach jedem fertigen Briefing-PDF: Begleittext erzeugen + merken. Mit auto geht ein
    TAGESbriefing an die Runde — höchstens einmal je Liefertag; ein Wochenbriefing nie
    automatisch. nachts_bis="07:00": fertig zwischen NACHT_AB und 07:00 → auf dem Server für
    07:00 geplant (ein neueres Briefing ersetzt einen automatischen Plan für denselben Morgen).
    Wirft nie — der Briefing-Lauf darf hieran nicht scheitern. Rückgabe für results[].wa_runde."""
    def _log(m):
        try:
            (log or (lambda x: print(x, file=sys.stderr)))("📲 WhatsApp-Runde: " + m)
        except Exception:
            pass
    text, quelle = "", "notfall"
    try:
        text, quelle = begleittext(pdf, txt)
        _merke_text(pdf, text, quelle)
        _log("Begleittext bereit (%s, %d Zeichen)" % (quelle, len(text)))
    except Exception as e:
        _log("Begleittext fehlgeschlagen: %s" % e)
        try:
            merke_pdf(pdf)
        except Exception:
            pass
    bereit = "Begleittext bereit (%s)" % ("Claude" if quelle == "claude" else "Notfalltext")
    if not auto:
        return "aus: %s, Auto-Senden ist aus" % bereit
    if ist_wochenbriefing(pdf):
        return "aus: %s — das Wochenbriefing geht nur per Knopf raus" % bereit
    try:
        if not eingerichtet():
            raise WaRundeFehler("Chatfunk ist nicht eingerichtet (CHATFUNK_URL / CHATFUNK_SECRET in .env)")
        offene_auffrischen(min_abstand=0)
        plan_zeit = nachtplan(nachts_bis)
        schon = runde_am_tag((plan_zeit or datetime.datetime.now()).date(), nur_tages=True)
        if schon and schon.get("status") == "geplant":
            if not schon.get("auto"):
                return "aus: für %s schon von Hand geplant — die Automatik lässt es dabei" % zeit_text(schon["zeit"])
            if os.path.basename(schon.get("pdf") or "") != os.path.basename(pdf):
                abbrechen(schon["id"])      # neueres Briefing ersetzt den automatischen Plan
                _log("automatischen Plan für %s (%s) durch das neue Briefing ersetzt"
                     % (zeit_text(schon["zeit"]), os.path.basename(schon.get("pdf") or "")))
            schon = None
        if schon:
            return "aus: heute schon an die Runde gesendet (%s) — nochmal nur von Hand" % _uhr(schon)
        if not runde().get("mitglieder"):
            raise WaRundeFehler("Die Briefing-Runde ist leer — keine Empfänger eingetragen")
        if plan_zeit:
            s = senden(pdf, text, auto=True, zeit=plan_zeit)
            e = "ok: %s (nachts fertig — Funk-Server sendet morgens)" % plan_text(s)
            _log(e)
            return e
        s = senden(pdf, text, auto=True)
        _log("Auftrag %s an Chatfunk übergeben, warte auf Abschluss…" % s.get("id"))
        s = warte_auf(s["id"], max_s=900)
        e = job_eintrag(s)
        _log(e)
        if e.startswith("fail"):
            _fehlerbuch(e[6:])
        return e
    except Exception as e:
        _log("Senden fehlgeschlagen: %s" % e)
        _fehlerbuch(str(e))
        return "fail: %s" % str(e)[:200]


def _fehlerbuch(meldung: str) -> None:
    try:
        import fehlerbuch
        fehlerbuch.eintragen("WhatsApp-Runde", meldung[:300], None, "normal")
    except Exception:
        pass


# ------------------------------------------------------------------ Kommandozeile

def _cli(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="wa_runde.py", description="📲 Briefing-PDF über Chatfunk an die Runde")
    sub = ap.add_subparsers(dest="befehl")
    sub.add_parser("status")
    k = sub.add_parser("kontakte")
    k.add_argument("suche", nargs="?")
    r = sub.add_parser("runde")
    r.add_argument("--hinzu", nargs="+", default=[])
    r.add_argument("--weg", nargs="+", default=[])
    r.add_argument("--leeren", action="store_true")
    t = sub.add_parser("text")
    t.add_argument("pdf", nargs="?")
    s = sub.add_parser("senden")
    s.add_argument("pdf", nargs="?")
    s.add_argument("--an", nargs="+", help="statt der Runde, z. B. --an ich")
    s.add_argument("--nochmal", action="store_true", help="auch wer die Datei heute schon bekam")
    s.add_argument("--text", help="Begleittext aus Datei (- = stdin); sonst der gemerkte bzw. neu erzeugte")
    s.add_argument("--um", help="planen statt sofort, z. B. --um 7:00 (schon vorbei → morgen)")
    ab = sub.add_parser("abbrechen")
    ab.add_argument("id", nargs="?")
    a = ap.parse_args(argv)

    try:
        if a.befehl == "status":
            st = status()
            print("Chatfunk: %s · heute gesendet %s/%s" % (st.get("verbindung"), st.get("heuteGesendet"), st.get("limit")))
            rd = runde()
            print("Briefing-Runde (%d): %s" % (len(rd["mitglieder"]), ", ".join(kontakt_label(m) for m in rd["mitglieder"]) or "—"))
            print("Neuestes Briefing-PDF: %s" % (neuestes_pdf() or "—"))
            offene_auffrischen(min_abstand=0)
            for gp in geplante_sendungen():
                print("%s · %s · %s" % (plan_text(gp), gp.get("datei"), gp.get("id")))
            q, f = quittung(letzte_sendung())
            if q:
                print("Letzte Sendung: %s" % q)
                for z in f:
                    print("  ❌ " + z)
        elif a.befehl == "kontakte":
            for kk in kontakte(a.suche, n=200 if a.suche else 3000):
                print("%-45s %s" % (kontakt_label(kk), kk.get("jid")))
        elif a.befehl == "runde":
            rd = runde()
            jids = [m["jid"] for m in rd["mitglieder"]]
            if a.leeren or a.hinzu or a.weg:
                jids = [] if a.leeren else jids
                jids += [j for j in a.hinzu if j not in jids]
                jids = [j for j in jids if j not in a.weg]
                rd = runde_setzen(jids)
            print("Briefing-Runde (%d):" % len(rd["mitglieder"]))
            for m in rd["mitglieder"]:
                print("  %-45s %s" % (kontakt_label(m), m["jid"]))
        elif a.befehl == "text":
            pdf = a.pdf or neuestes_pdf()
            if not pdf:
                print("Kein Briefing-PDF gefunden.", file=sys.stderr)
                return 1
            text, quelle = begleittext(pdf)
            _merke_text(pdf, text, quelle)
            print("(%s, %d Zeichen)\n%s" % (quelle, len(text), text))
        elif a.befehl == "senden":
            pdf = a.pdf or neuestes_pdf()
            if not pdf:
                print("Kein Briefing-PDF gefunden.", file=sys.stderr)
                return 1
            if a.text:
                text = sys.stdin.read() if a.text == "-" else open(a.text, encoding="utf-8").read()
            else:
                g = gemerkter_text(pdf)
                text = g["text"] if g else None
                if not text:
                    text, quelle = begleittext(pdf)
                    _merke_text(pdf, text, quelle)
            zeit = naechster_zeitpunkt(a.um) if a.um else None
            s = senden(pdf, text, an=a.an, nochmal=a.nochmal, zeit=zeit)
            if zeit:
                print("%s (%s) — Auftrag %s liegt auf dem Funk-Server" % (plan_text(s), os.path.basename(pdf), s["id"]))
                return 0
            print("Auftrag %s an Chatfunk übergeben (%d Empfänger) — warte…" % (s["id"], len(s["empfaenger"])))
            s = warte_auf(s["id"], max_s=900)
            q, f = quittung(s)
            print(q)
            for z in f:
                print("  ❌ " + z)
            return 1 if f else 0
        elif a.befehl == "abbrechen":
            offene_auffrischen(min_abstand=0)
            ids = [a.id] if a.id else [x["id"] for x in geplante_sendungen()]
            if not ids:
                print("Nichts geplant.")
            for i in ids:
                print(quittung(abbrechen(i))[0])
        else:
            ap.print_help()
    except WaRundeFehler as e:
        print("❌ %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
