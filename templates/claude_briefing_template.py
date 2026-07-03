#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vorlage für Claude Code zum Bauen eines Briefings aus einer Handoff-Datei.

VERWENDUNG (für Claude/mich):
1. Datei nach /tmp/build_briefing_<timestamp>.py kopieren
2. HANDOFF_PATH unten anpassen
3. Alle Section-Strings mit echtem Inhalt füllen
4. Skript ausführen → PDF landet im iCloud Briefings-Ordner
5. Vollständigkeitscheck am Ende prüfen, bei Bedarf nachbessern

LÄNGEN-REGELN (inhaltsgetrieben, nicht starr):
- Kurze Verbraucher-/Promi-Meldung: 200-300 Wörter
- Normaler Nachrichten-Artikel: 280-380 Wörter
- Investigative Recherche / großer Hintergrund: 400-500 Wörter
- Podcast: 600-1000 Wörter je nach Komplexität
- Wetter: 250-350 Wörter
- Recap: 600-900 Wörter
- Essenz: 600-900 Wörter
- Verabschiedung: 80-120 Wörter

Die Gesamtlänge ergibt sich aus der Anzahl und Tiefe der Beiträge.
Bei 5 Artikeln: ~8 Seiten. Bei 90 Artikeln: ~80-100 Seiten. KEINE feste Vorgabe.

VOLLSTÄNDIGKEIT IST PFLICHT: Jeder Artikel/Podcast muss als eigener Absatz
erkennbar sein. KEIN Cluster-Verschmelzen.
"""
import sys, json, datetime, re
sys.path.insert(0, "/Users/florian/Projects/apps/briefing-app")
from briefing_core import build_pdf_from_claude_json
from pathlib import Path

# ============================================================
# KONFIGURATION
# ============================================================
HANDOFF_PATH = "/Users/florian/Downloads/claude-handoff_REPLACEME.txt"
OUTPUT_NAME = "briefing_REPLACEME_claude_erzaehl.pdf"
NARRATIVE_MODE = True  # True = Erzähl-Stil, False = klassisch
GENERATED_AT = datetime.datetime.now()

# ============================================================
# HANDOFF EINLESEN (für Quick-Stats und Vollständigkeitsprüfung)
# ============================================================
with open(HANDOFF_PATH, encoding="utf-8") as f:
    handoff = f.read()

n_artikel = len(re.findall(r'^### Artikel \d+', handoff, re.MULTILINE))
n_paywall = len(re.findall(r'^### Paywall \d+', handoff, re.MULTILINE))
n_podcast = len(re.findall(r'^### Podcast \d+', handoff, re.MULTILINE))
print(f"Handoff: {n_artikel} Artikel, {n_paywall} Paywalls, {n_podcast} Podcasts")

# ============================================================
# SECTIONS — HIER INHALT FÜLLEN
# ============================================================

wetter = """### Wetter zum Auftakt

*Kurzer Einordnungssatz.*

[250-350 Wörter Wetter, basierend auf den Wetter-Rohdaten oben in der Handoff-Datei.
Aktuelle Temperatur, Tagesverlauf, nächste 2-3 Tage, ggf. Warnungen.]"""

lokales = """### Aus Tübingen und der Region

*Kurzer Einordnungssatz.*

[1.800-2.200 Wörter. Jeder Tübinger/Region-Artikel als eigener Absatz mit
~200-250 Wörtern. Quelle elegant einweben ("wie das Tagblatt schreibt").
Konkrete Namen, Daten, Zahlen, Zitate aus dem Quelltext.]"""

politik_de = """### Politik, Gesellschaft und Justiz in Deutschland

*Kurzer Einordnungssatz.*

[1.600-2.000 Wörter. Maßregelvollzug, Schwarzfahren-Debatte, Defizit, Datenhandel,
Großeinsätze etc. — jedes Thema 250-350 Wörter, mit Quotes wo möglich.]"""

wirtschaft = """### Wirtschaft und Finanzen

*Kurzer Einordnungssatz.*

[1.000-1.400 Wörter für 4-6 Wirtschaftsbeiträge.]"""

international = """### International

*Kurzer Einordnungssatz.*

[1.400-1.800 Wörter. Iran-Krieg, BBC-Berichte, internationale Justiz, etc.]"""

tech = """### Technologie, KI und Innovation

*Kurzer Einordnungssatz.*

[1.000-1.400 Wörter. Hardware, Software, KI-News, Verbraucher-Tech.]"""

wissenschaft = """### Wissenschaft, Gesundheit und Lifestyle

*Kurzer Einordnungssatz.*

[800-1.200 Wörter. Forschung, Gesundheitstipps, Lifestyle.]"""

regional_sport = """### Regionales, Sport und Vermischtes

*Kurzer Einordnungssatz.*

[800-1.000 Wörter. Was sonst noch so passiert.]"""

# Podcasts: jeder eine eigene Section, 600-900 Wörter
podcast_1 = """### Aus den Podcasts: [TITEL]

*Kurzer Einordnungssatz.*

[600-900 Wörter mit Sprechernamen, Argumenten, konkreten Zitaten und Zahlen aus
der Podcast-Zusammenfassung in der Handoff-Datei.]"""

# ... weitere Podcasts: podcast_2, podcast_3, ...

recap = """### Rückblick

*Bevor wir schließen, nochmal die wichtigsten Stränge des Tages.*

[600-800 Wörter. Alle Themen kurz erwähnt, gruppiert nach Lokales/Politik/Wirtschaft/
International/Tech/Wissenschaft/Regional/Podcasts.]"""

essenz = """### Was wirklich bleibt

*Die Essenz aus dem heutigen Tag — was über den Moment hinaus zählt.*

[600-800 Wörter. 5-7 substanzielle Punkte als Fließtext-Absätze mit Kerngedanken
in **fett** und 2-3 Sätzen Erklärung. Strukturelle Verschiebungen, nicht Tagesdetails.]"""

verabschiedung = """### Bis zum nächsten Mal

„[Echtes Zitat eines weniger bekannten klugen Kopfes]" — **[Person]**

[1-2 Sätze Einordnung, was du heute damit anfangen kannst. Persönlicher Gruß
passend zur Tageszeit. Max 100 Wörter.]

Ende des Briefings."""

# ============================================================
# JSON BAUEN UND PDF GENERIEREN
# ============================================================
sections = [
    {"type": "article", "_weather": True, "source_label": "Wetter", "content": wetter},
    {"type": "article", "source_label": "Lokales und Region", "content": lokales},
    {"type": "article", "source_label": "Politik & Gesellschaft DE", "content": politik_de},
    {"type": "article", "source_label": "Wirtschaft", "content": wirtschaft},
    {"type": "article", "source_label": "International", "content": international},
    {"type": "article", "source_label": "Tech und KI", "content": tech},
    {"type": "article", "source_label": "Wissenschaft & Gesundheit", "content": wissenschaft},
    {"type": "article", "source_label": "Region, Sport und Vermischtes", "content": regional_sport},
    {"type": "article", "source_label": "Podcast 1", "content": podcast_1},
    # ... podcast_2, podcast_3, ...
    {"type": "article", "_recap": True, "source_label": "Rückblick", "content": recap},
    {"type": "article", "_essenz": True, "source_label": "Essenz", "content": essenz},
    {"type": "article", "_verabschiedung": True, "source_label": "Abschluss", "content": verabschiedung},
]

# Stats
total_chars = sum(len(s["content"]) for s in sections)
total_words = total_chars // 6
print(f"\nSections: {len(sections)}")
print(f"Gesamt-Zeichen: {total_chars:,}")
print(f"Gesamt-Wörter (grob): {total_words:,}")

if total_words < 10000:
    print(f"⚠️  WARNUNG: Nur {total_words:,} Wörter — Ziel sind 12.000-16.000.")
    print(f"   Sections aufstocken bevor du die PDF baust!")

# PDF bauen
data = {"compact_mode": False, "narrative_mode": NARRATIVE_MODE, "sections": sections}
archive_dir = Path("/Users/florian/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Briefings")
archive_dir.mkdir(parents=True, exist_ok=True)
out_path = archive_dir / OUTPUT_NAME

result = build_pdf_from_claude_json(
    json.dumps(data, ensure_ascii=False),
    str(out_path),
    generated_at=GENERATED_AT,
)

print(f"\nResult: {result}")
print(f"Output: {out_path}")

# Verifikation
if result["ok"]:
    import pdfplumber
    pdf = pdfplumber.open(str(out_path))
    text = ''.join((p.extract_text() or '') for p in pdf.pages)
    print(f"\n✅ PDF: {len(pdf.pages)} Seiten, {len(text.split()):,} Wörter")
