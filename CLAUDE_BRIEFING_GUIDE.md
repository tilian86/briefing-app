# Briefing via Claude Code — Anleitung für mich (Claude)

Wenn der User mir eine Handoff-Datei (`claude-handoff_*.txt`) gibt und sagt „verarbeite das und bau die PDF", dann ist hier der Standard-Workflow.

## Schritt 1: Datei lesen und Struktur verstehen

```python
# Im Bash:
grep -c "^### Artikel" <handoff-datei>
grep -c "^### Podcast" <handoff-datei>
grep -c "^### Paywall" <handoff-datei>
```

Daraus weiß ich: Wie viele Artikel, Paywalls, Podcasts. Plus den Modus aus dem Header (`STIL: ERZÄHL-MODUS` oder `KLASSISCH`).

## Schritt 2: Skript bauen

Nutze immer `/Users/florian/Projects/apps/briefing-app/templates/claude_briefing_template.py` als Vorlage. Kopiere sie nach `/tmp/build_briefing_<timestamp>.py` und fülle die Section-Strings.

## Schritt 3: LÄNGE — inhaltsgetrieben, nicht starr

Die Gesamtlänge ergibt sich aus der Anzahl und Tiefe der Beiträge. KEINE feste Vorgabe.
Bei 5 Artikeln: ~8 Seiten. Bei 90 Artikeln: ~80-100 Seiten.

**Pro einzelnem Beitrag** (das ist die einzige feste Regel):

| Beitragstyp | Wörter |
|---|---|
| Kurze Verbraucher-/Promi-Meldung | 200-300 |
| Normaler Nachrichten-Artikel | 280-380 |
| Investigative Recherche / großer Hintergrund | 400-500 |
| Podcast | 600-1000 (je nach Komplexität) |
| Wetter | 250-350 |
| Recap | 600-900 |
| Essenz | 600-900 (4-7 Punkte) |
| Verabschiedung | 80-120 |

**Faustregel:** Lieber tiefer als kürzer. Wenn ein Artikel klar wichtiger ist (große Recherche, internationale Geschichte mit vielen Akteuren), geh hoch auf 500. Wenn ein Artikel knapper Verbraucher-Tipp ist, sind 250 ok.

**Vollständigkeitsprüfung:** Jeder nummerierte Artikel/Podcast aus der Handoff-Datei muss als EIGENER Absatz im finalen Text erkennbar sein. KEIN Cluster-Verschmelzen. Lieber 2-3 Sätze Übergang als zwei Beiträge in einem Sammelabsatz.

## Schritt 4: PDF bauen

```python
from briefing_core import build_pdf_from_claude_json
import json, datetime
from pathlib import Path

result = build_pdf_from_claude_json(
    json.dumps({"narrative_mode": True, "sections": sections}, ensure_ascii=False),
    "/Users/florian/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Briefings/briefing_<timestamp>_claude_erzaehl.pdf",
    generated_at=datetime.datetime.now(),
)
```

## Schritt 5: Verifikation

```python
import pdfplumber
pdf = pdfplumber.open(out_path)
text = ''.join((p.extract_text() or '') for p in pdf.pages)
print(f"Seiten: {len(pdf.pages)}, Wörter: {len(text.split())}")

# Vollständigkeitscheck: alle Beiträge im Text?
required_keywords = [...]  # aus den Artikel-Titeln extrahieren
missing = [k for k in required_keywords if k.lower() not in text.lower()]
print(f"Fehlt: {missing}")
```

Wenn was fehlt → Section ergänzen, JSON neu schreiben, PDF neu bauen.

## Häufige Fehler die ICH (Claude) vermeiden muss

1. **Cluster-Verschmelzung** — wenn ich mehrere Beiträge in einem Sammelabsatz quetsche. Jeder Beitrag braucht einen eigenen Absatz.
2. **Zu kompakt schreiben** — der Erzähl-Modus verleitet zur Kürze. Immer auf 280-350 Wörter pro Beitrag zielen.
3. **Quotes weglassen** — wenn im Quelltext Zitate stehen, gehören die in den Erzähltext.
4. **Konkrete Zahlen weglassen** — Beträge, Daten, Personen-Anzahl usw. müssen rein.
5. **Verfrüht abbrechen** — bei 6.000 Wörtern zu glauben „das reicht". Es reicht nicht. 12.000+.

## Nach erfolgreicher PDF-Generierung

Dem User kurz melden:
- Pfad zur PDF
- Seiten + Wörter
- Vollständigkeitscheck-Ergebnis
- Was er noch optimieren könnte
