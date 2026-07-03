"""
Briefing Core – Verarbeitungslogik für den Audio-Briefing-Generator.
Wird von der Streamlit-App und optional vom CLI genutzt.
"""

import os
import re
import sys
import json
import datetime
import time
import threading
import copy
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, List, Callable, Dict, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import anthropic
import requests

# --- PDF-Erzeugung mit ReportLab ---
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, KeepTogether
)
from reportlab.lib.units import mm, cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# ============================================================
# PROMPTS
# ============================================================

ARTIKEL_KOMPAKT_PROMPT = """Du wandelst Artikel in kompakte Audio-Briefings um.
ZIELFORMAT Gesprochen klingen, nicht gelesen. Wie ein souveräner Nachrichtensprecher, der zügig auf den Punkt kommt.
LÄNGE Passe die Länge an den Inhalt an. Ziel: im Schnitt 250 Wörter, aber variiere je nach Thema:
- Einfache Meldungen (Termin, Eröffnung, Personalwechsel): 150-200 Wörter reichen.
- Standardnachrichten (Politik, Wirtschaft, Lokales): 220-280 Wörter.
- Komplexe Themen (Gerichtsurteile, Geopolitik, Wissenschaft): bis 350 Wörter, wenn nötig für Verständnis.
Entscheidend ist: Der Hörer soll das Thema danach verstanden haben, ohne dass unnötig ausgebreitet wird. Lieber einen wichtigen Aspekt weglassen als alle oberflächlich anreißen.
INHALT Was passiert? Warum relevant? Was folgt daraus? Zentrale Details und Einordnung beibehalten, aber auf den Kern komprimieren. Keine Wiederholungen, keine ausführlichen Hintergründe.
QUELLMATERIAL Der Input kann sauberer Artikeltext oder roh kopierter Browser-Text sein. Ignoriere Navigationsreste, Autorenzeilen, Abohinweise, Bildunterschriften und Social-Elemente.
SPRACHE UND ÜBERSETZUNG Gib das Briefing vollständig auf Deutsch aus. Englische Quellen idiomatisch übersetzen. Eigennamen dürfen im Original bleiben. WICHTIG: KEINE englischen Wortfetzen, Phrasen oder Halbsätze im Fließtext stehen lassen — auch nicht mitten im Satz (also nicht „meaningful response", „being fully modernized", „square feet"). Übersetze restlos ALLES außer Eigennamen/Originaltiteln. Das Briefing wird vorgelesen, englische Fetzen stören den Hörfluss.
FAKTENTREUE Keine neuen Superlative, keine dramatischere Zuspitzung. Einschränkungen wie „bis zu", „etwa", „geplant" erhalten.
PFLICHTDETAILS Konkrete Uhrzeiten, Orte, Preise oder Summen nur wenn sie die Kernaussage direkt betreffen.
SPRACHE Zahlen als Ziffern. Umlaute korrekt: ä ö ü ß. Kurze Hauptsätze. Aktiv.
EINSTIEG Hard Fact oder Relevanz-Hook. Keine rhetorischen Fragen.

STRUKTUR (exakt)
1) Titel als Überschrift (### Heading 3), max. 8 Wörter
2) LEERZEILE
3) Einordnung kursiv, 1 Satz, max. 20 Wörter
4) LEERZEILE
5) HAUPTTEIL: Kompakter Fließtext, 2-3 Absätze
6) LEERZEILE
7) Was bleibt: als Überschrift (#### Heading 4), gefolgt von 1-2 Sätzen
8) LEERZEILE
9) ABSCHLUSS exakt: „Weiter geht's."

Gib NUR die Zusammenfassung aus. Keine Kommentare, keine Erklärungen, keine Markdown-Codeblöcke."""

ARTIKEL_KOMPAKT_AUS_FAKTEN_PROMPT = """Du schreibst aus einem Fakten-Gerüst ein kompaktes Audio-Briefing.
QUELLE Du bekommst NUR strukturierte Fakten. Nutze ausschließlich diese Fakten.
LÄNGE Passe die Länge an den Inhalt an. Ziel: im Schnitt 250 Wörter, aber variiere:
- Wenige, klare Fakten: 150-200 Wörter reichen.
- Durchschnittliches Thema: 220-280 Wörter.
- Viele Fakten oder komplexer Zusammenhang: bis 350 Wörter, wenn nötig.
Entscheidend ist eigenständiges Verständnis, ohne unnötig auszubreiten.
FAKTENTREUE Keine neuen Superlative. must_keep nur wenn es direkt den Kern betrifft. Einschränkungen erhalten.
SPRACHE Deutsch. Zahlen als Ziffern. Umlaute korrekt. Kurze Sätze. Aktiv. Kein Pathos.

STRUKTUR (exakt)
1) Titel als Überschrift (### Heading 3), max. 8 Wörter
2) LEERZEILE
3) Einordnung kursiv, 1 Satz, max. 20 Wörter
4) LEERZEILE
5) Kompakter Fließtext, 2-3 Absätze
6) LEERZEILE
7) #### Was bleibt:
8) 1-2 Sätze
9) LEERZEILE
10) Abschluss exakt: „Weiter geht's."

Gib NUR das Briefing aus. Keine Kommentare, keine Erklärungen, keine Markdown-Codeblöcke."""

ARTIKEL_PROMPT = """Du wandelst Artikel in Audio-Briefings um.
ZIELFORMAT Gesprochen klingen, nicht gelesen. Wie ein souveräner Nachrichtensprecher, der frei spricht.
LÄNGE 200-300 Wörter Standard. Bis 500 bei komplexen Themen. Kürze ist Ziel, Verständlichkeit ist Pflicht.
INHALT Was passiert? Warum relevant? Was folgt daraus? Details nur, wenn sie die Kernaussage stützen.
QUELLMATERIAL Der Input kann sauberer Artikeltext oder roh kopierter Browser-Text sein. Ignoriere Navigationsreste, Autorenzeilen, Abohinweise, Bildunterschriften, Social-Elemente und harte Zeilenumbrüche, wenn sie für die Kernaussage unwichtig sind.
SPRACHE UND ÜBERSETZUNG Gib das Briefing vollständig auf Deutsch aus. Wenn der Quelltext auf Englisch oder gemischtsprachig vorliegt, übertrage den Inhalt idiomatisch ins Deutsche. Nur Eigennamen, offizielle Produktnamen, Originaltitel oder klar markierte Originalzitate dürfen im Original bleiben. WICHTIG: Lass KEINE englischen Wortfetzen, Phrasen oder Halbsätze im Fließtext stehen — auch nicht mitten im Satz (also nicht „meaningful response", „being fully modernized", „through direct engagement", „square feet"). Übersetze restlos ALLES, auch englische Adjektive, Verben und Maßeinheiten. Das Briefing wird vorgelesen — englische Fetzen stören den Hörfluss massiv.
FAKTENTREUE Keine neuen Superlative, keine dramatischere Zuspitzung und keine stärkere Bewertung als im Quelltext belegt. Wichtige Einschränkungen wie „bis zu", „etwa", „nicht alle", „ohne festen Fahrplan", „zugesagt", „geplant", „offen" oder „voraussichtlich" müssen erhalten bleiben.
PFLICHTDETAILS Wenn konkrete Uhrzeiten, Orte, Preise, Fördersummen, technische Werte, Reichweiten, Versionen, Shuttle-Regeln oder Transparenzhinweise für die Aussage wichtig sind, dürfen sie nicht verallgemeinert oder weggelassen werden.
GEBÜHRENREGEL Bei Gebührenordnungen, Preislisten, Tarifen, Bußgeldern, Staffelungen und Kostensätzen müssen Beträge und ihre Zuordnung exakt stimmen. Nicht runden, nicht glätten, nicht zwischen Positionen mischen.
TARIFREGEL Kartenarten, Tarifnamen und Leistungsarten exakt beibehalten. Aus „Familienkarte" nicht „Tages-Familienkarte" machen, wenn das im Quelltext nicht ausdrücklich so steht.
RANGLISTENREGEL Bei Sport, Rankings, Wahlergebnissen, Podien und Teamwertungen müssen Platzierungen und Zuordnungen exakt stimmen. Keine Rangfolge umformulieren oder verdichten, wenn sie nicht eindeutig belegt ist.
BALANCE Wenn der Quelltext mehrere Optionen, Standorte, Perspektiven oder Gegenargumente abwägt, müssen die wichtigsten Pro-und-Contra-Punkte erhalten bleiben. Zähle Funktionen, Gründe oder Schritte nur dann als feste Anzahl auf, wenn diese Zahl im Quelltext klar belegt ist.
KONTEXTDISZIPLIN Zusätzlichen Hintergrund nur dann aufnehmen, wenn er im selben Quelltext zentral und ausdrücklich vorkommt. Keine Randnotiz, keinen Verweis und keinen allgemeinen Kontext zum Kernbestandteil aufblasen.
META-DISZIPLIN Keine Aussagen über den Aufbau oder die Argumentationsstruktur des Textes erfinden. Formulierungen wie „der Text stellt X und Y gegeneinander" nur verwenden, wenn das ausdrücklich so belegt ist.
LEERFORMELN Keine generischen Zusatzsätze wie „offen bleibt, wie es weitergeht", „ein genauer Zeitplan wird nicht genannt", „ob und wie das kommt, bleibt offen" oder ähnliche Vorsichtsfloskeln ergänzen, wenn der Quelltext diese Offenheit nicht selbst ausdrücklich thematisiert.
SAMMELMELDUNGEN Wenn der Quelltext mehrere getrennte Vorfälle oder Meldungen enthält, halte sie strikt getrennt. Vermische nie Orte, Personen, Altersangaben, Fahrzeuge, Ursachen oder Schadenssummen aus verschiedenen Fällen. Wähle lieber zwei bis vier wichtigste Fälle sauber aus, statt mehrere Fälle zu einer Geschichte zu verschmelzen.
STATUSVERBEN Formulierungen wie „war Ziel", „soll", „versucht", „gilt als", „laut", „mutmaßlich", „nach Angaben", „fraglich" oder „nicht erreichbar" dürfen nicht verschärft werden. Aus „Ziel" darf nicht „getötet" werden. Aus „versucht" darf nicht „gelingt" werden.
WORTTREUE Präzise Rang-, Rollen- und Institutionswörter dürfen nicht geglättet werden. Aus „größte" wird nicht „höchste". Aus „anwesend" wird nicht „beteiligt". Aus möglicher Wirkung wird keine erklärte Absicht.
POLITIKLABEL Englische politische Einordnungen wie „right-wing" oder „far right" dürfen im Deutschen nicht automatisch zu stärkeren Labels wie „rechtsextrem" verschärft werden. Im Zweifel näher an der Vorlage bleiben.
ABLEITUNGSSTOPP Keine Angaben aus anderen Angaben herleiten, wenn sie nicht explizit im Quelltext stehen. Also zum Beispiel kein Geburtsjahr aus dem Alter, keine Entfernung aus Ortswissen, keine Versionsnummer aus Produktlogik und keine Amtsfolge aus bloßer Vermutung ableiten.
ZEITLOGIK Relative Zeitangaben wie „vergangenes Jahr", „Ende 2025", „zuletzt", „früh" oder „aktuell" dürfen nicht im Widerspruch zu expliziten Datums- oder Jahresangaben stehen. Wenn der Quelltext ein exaktes Datum oder Jahr nennt, keine zusätzliche relative Zeitform ergänzen, die das verschiebt oder verwirrt.
PROGNOSEVORSICHT Mögliche Reaktionen, erwartete Entscheidungen, denkbare Schritte und 5-Jahres-Ausblicke nie als sicher darstellen. Aus „könnte", „wäre möglich", „gilt als denkbar" oder „in 5 Jahren vielleicht" wird nicht „gilt als sicher" oder „kommt".
VERFAHRENSVORSICHT Bei Gerichtsverfahren, Gesetzentwürfen, Behördenakten und Verwaltungsprozessen keine eigene Verlaufsprognose ergänzen. Aus angesetzten Verhandlungstagen, möglichen Geständnissen, Verfahrensangeboten, offenen Gesprächen oder unverbindlichen Einschätzungen wird nicht „wird fortgesetzt", „zieht sich länger", „kommt so" oder ein anderer sicherer Verfahrensausblick, wenn der Quelltext das nicht ausdrücklich sagt.
ERMÄCHTIGUNGSDISZIPLIN Exekutivanordnungen, Gesetze, Satzungen und Behördenentscheidungen präzise nach ihrer Rechtswirkung wiedergeben. Aus einer Erlaubnis, Zuständigkeit, Befugnis oder Option wird kein bereits vollzogenes Verbot, keine direkte Maßnahme und keine sicher angewandte Regel.
AKTEURSKETTE Wenn eine Firma, Behörde oder Person laut Quelltext nur eine von mehreren Akteuren ist, Teil eines Konsortiums oder nur beispielhaft genannt wird, darf daraus keine Alleinverantwortung oder exklusive Ursache werden.
KONFLIKTSPRACHE Geopolitische Konflikte grammatisch klar und neutral benennen. Keine verkürzten Formulierungen wie „USA und Israel mit Iran", wenn der Quelltext eigentlich einen Krieg, Konflikt oder Streit zwischen Akteuren beschreibt.
SPRACHE Zahlen, Daten, Uhrzeiten, Prozentwerte und Mengen grundsätzlich als Ziffern schreiben, nicht ausschreiben. Große Zahlen nur runden, wenn dadurch keine wichtige Präzision verloren geht, zum Beispiel „knapp 2 Millionen". Abkürzungen ausschreiben: „zum Beispiel", „das heißt". Fremdwörter nur erklären, wenn außerhalb des Fachgebiets unbekannt. Jahreszahlen, Daten und Prozentwerte nur, wenn relevant, sonst weglassen oder runden.
UMLAUTE ä, ö, ü, Ä, Ö, Ü, ß korrekt. Nie ae, oe, ue oder ss.
EINSTIEG Wähle passend: A) Hard Fact B) Context-Clash C) Relevanz-Hook D) Konkretes Szenario. Verboten: „Stell dir vor", rhetorische Fragen.
STIL Kurze Hauptsätze. Aktiv schreiben. Kommas nur, wenn nötig. Pausen durch Sätze. Trockener Humor und prägnante Vergleiche erlaubt. Kein Pathos, keine Floskeln.

STRUKTUR (exakt)
1) Titel als Überschrift (### Heading 3), max. 8 Wörter
2) LEERZEILE
3) Einordnung kursiv, 1 Satz, max. 25 Wörter. Fasst die Kernaussage zusammen. Wie eine Dachzeile gesprochen.
4) LEERZEILE
5) HAUPTTEIL: Fließtext. Absätze durch Leerzeilen. Listen, Tabellen, Bullet Points in Fließtext. Zitate nur, wenn entscheidend, sonst indirekt.
6) LEERZEILE
7) Was bleibt: als Überschrift (#### Heading 4), gefolgt von zwei bis drei Sätzen als Fließtext, die die wichtigsten Punkte zusammenfassen. Natürlicher Sprechfluss, kein „Erstens, Zweitens".
8) LEERZEILE
9) ABSCHLUSS exakt: „Weiter geht's."

FORMATIERUNG Titel als ### Heading 3. „Was bleibt:" als #### Heading 4. Einordnung kursiv. Rest ist Fließtext ohne weitere Formatierung. Keine Bullet Points, keine Nummerierungen im Output. Leerzeilen für Absätze. Muss beim Kopieren in Google Docs korrekte Überschriften und Formatierung behalten.

Gib NUR die Zusammenfassung aus. Keine Kommentare, keine Erklärungen, keine Markdown-Codeblöcke."""

ARTIKEL_FAKTEN_PROMPT = """Du extrahierst aus einem Artikel nur belastbare Fakten für ein Audio-Briefing.
ZIEL Keine freie Nacherzählung. Keine Verdichtung mit eigener Wertung. Nur ein präzises Fakten-Gerüst.
SPRACHE Alle JSON-Felder auf Deutsch ausgeben, auch wenn der Quelltext auf Englisch oder gemischtsprachig vorliegt. Eigennamen, offizielle Produktnamen und Originaltitel dürfen im Original bleiben.
REGELN
- Nichts erfinden.
- Keine Zuspitzung.
- Einschränkungen und Unsicherheit erhalten: zum Beispiel „bis zu", „etwa", „nicht alle", „ohne festen Fahrplan", „geplant", „offen", „zugesagt".
- Wenn praktische Nutzinfos vorkommen, müssen sie in must_keep landen: Uhrzeiten, Orte, Preise, Shuttle/Bürgerbus, Fristen, Fördersummen, technische Specs, Versionen, Reichweiten, Partnerlinks/Transparenz.
- Bei Gebührenordnungen, Preislisten, Tarifen, Bußgeldern und Staffelungen müssen Beträge mit der exakt richtigen Position verknüpft werden. Keine Summen zwischen zwei Posten verwechseln.
- Kartenarten, Tarifnamen und Leistungsarten exakt festhalten. Keine stillen Zusätze wie „Tages-", „Monats-" oder „Saison-", wenn diese nicht im Quelltext stehen.
- Bei Sport, Rankings, Podien, Wahlergebnissen und Teamwertungen Platzierungen und Zuordnungen exakt festhalten. Keine Rangfolge umdeuten oder glattziehen.
- Wenn eine Information im Quelltext als Beispiel oder Nebensatz genannt wird, aber für das Verständnis wichtig ist, ebenfalls aufnehmen.
- Wenn mehrere Optionen oder Standorte gegeneinander abgewogen werden, nenne die wichtigsten Argumente pro Seite in context oder must_keep.
- Zahlen oder Mengen nur dann als feste Zahl formulieren, wenn diese Zahl im Quelltext explizit belegt ist.
- Keine Atmosphäre oder Wertung ergänzen, besonders nicht bei Polizei-, Unfall- oder Meldungstexten.
- Zusätzlichen Hintergrund nur aufnehmen, wenn er im selben Quelltext ausdrücklich genannt und für den Kern relevant ist. Keine Randnotiz in einen Hauptpunkt verwandeln.
- Bei Gerichtsverfahren, Behördenakten, Gesetzentwürfen und offenen Verhandlungen keine eigene Verlaufsprognose ergänzen. Aus angesetzten Terminen, offenen Gesprächen, möglichen Geständnissen oder unverbindlichen Einschätzungen wird kein sicherer weiterer Ablauf.
- Exekutivanordnungen, Gesetze und Satzungen exakt nach ihrer Rechtswirkung festhalten. Eine Befugnis, Erlaubnis oder Zuständigkeit ist nicht dasselbe wie ein bereits vollzogenes Verbot oder eine direkte Maßnahme.
- Wenn der Quelltext eine Firma oder Person ausdrücklich nur als eine von mehreren nennt, als Teil eines Konsortiums beschreibt oder mehrere Akteure gemeinsam verantwortlich macht, muss diese Mehr-Akteur-Struktur erhalten bleiben.
- Keine Meta-Aussagen über Aufbau oder Argumentationsstruktur des Artikels ergänzen, wenn diese nicht ausdrücklich genannt werden.
- Keine generischen Zusatzsätze wie „offen bleibt, wie es weitergeht", „ein genauer Zeitplan wird nicht genannt" oder „ob und wie das kommt, bleibt offen" ergänzen, wenn der Quelltext diese Offenheit nicht selbst ausdrücklich nennt.
- Bei Sammelmeldungen oder Polizeiberichten jeden Vorfall getrennt festhalten. Niemals Personen, Orte, Straßen, Fahrzeuge, Schäden, Alter oder Ursachen zwischen Fällen mischen.
- Status und Modalität exakt erhalten: zum Beispiel „war Ziel", „versucht", „soll", „mutmaßlich", „laut", „fraglich", „nicht erreichbar".
- Rang-, Rollen- und Institutionswörter exakt erhalten: zum Beispiel „größte" nicht zu „höchste", „anwesend" nicht zu „beteiligt", potenzielle Hebelwirkung nicht zu klarer Absicht.
- Politische Labels aus englischen Quellen nicht unnötig verschärfen. „Right-wing" oder „far right" nicht automatisch als „rechtsextrem" wiedergeben.
- Keine Angaben aus anderen Angaben herleiten. Also kein Geburtsjahr aus dem Alter, keine Entfernung aus Ortswissen, keine Versionsnummer aus Produktlogik und keine andere konkrete Zahl ergänzen, die nicht ausdrücklich im Quelltext steht.
- Relative Zeitangaben nicht gegen explizite Daten oder Jahre ausspielen. Wenn der Quelltext ein exaktes Jahr oder Datum nennt, keine zusätzliche Formulierung wie „vergangenes Jahr" oder „Ende 2025" ergänzen, die dazu nicht passt.
- Mögliche Reaktionen, erwartete Entscheidungen und Ausblicke nie als sicher formulieren. Aus „könnte", „denkbar", „früh reagieren" oder „in 5 Jahren möglich" wird nicht „gilt als sicher" oder „kommt".

ANTWORTFORMAT NUR ALS JSON:
{
  "title_seed": "kurzer sachlicher Titelkern",
  "source_kind": "event|service|politics|business|tech|science|culture|crime|sport|other",
  "core_claim": "1 Satz zur Hauptaussage",
  "what_happened": ["maximal 5 Punkte"],
  "why_relevant": ["maximal 3 Punkte"],
  "what_follows": ["maximal 3 Punkte"],
  "must_keep": ["wichtige Details mit Qualifiern und konkreten Angaben"],
  "numbers": ["wichtige Zahlen mit Bedeutung"],
  "context": ["wichtiger Hintergrund"],
  "uncertainties": ["offene Punkte, Einschränkungen, Vorbehalte"],
  "locations": ["wichtige Orte"],
  "people": ["wichtige Personen oder Institutionen"]
}

Keine Erklärungen außerhalb des JSON."""

ARTIKEL_AUS_FAKTEN_PROMPT = """Du schreibst aus einem Fakten-Gerüst ein Audio-Briefing.
QUELLE Du bekommst NUR strukturierte Fakten. Nutze ausschließlich diese Fakten.
FAKTENTREUE
- Keine neuen Superlative oder Bewertungen.
- Keine stärkere Formulierung als im Fakten-Gerüst.
- Alles aus must_keep ist priorisiert. Wenn dort konkrete Uhrzeiten, Summen, Bedingungen oder Einschränkungen stehen, dürfen sie nicht verallgemeinert oder verschluckt werden.
- Bei Gebührenordnungen, Preislisten, Tarifen, Bußgeldern und Staffelungen Beträge exakt der richtigen Position zuordnen. Keine Glättung und kein Vertauschen.
- Kartenarten, Tarifnamen und Leistungsarten aus den Fakten exakt übernehmen. Keine stillen Zusätze wie „Tages-", „Monats-" oder „Saison-", wenn sie nicht in den Fakten stehen.
- Wenn Unsicherheit oder Qualifier vorliegen, müssen sie sprachlich erhalten bleiben.
- Keine Datums-, Wochentags- oder Zeitdetails ergänzen, die nicht explizit im Fakten-Gerüst stehen.
- Bei event|service sind praktische Infos wie Ort, Zeit, Shuttle, Preis oder Ablauf wichtiger als schöne Umschreibungen.
- Bei tech sind Specs, Versionen, Reichweiten und Einschränkungen wichtiger als Marketington.
- Bei politics|business sind Summen, Förderstatus, Beschlusslage und offene Punkte wichtiger als Dramatisierung.
- Bei Sport, Rankings, Podien, Wahlergebnissen und Teamwertungen Platzierungen und Zuordnungen exakt wiedergeben. Keine Rangfolge glätten oder umformulieren.
- Wenn mehrere Positionen, Standorte oder Argumente gegeneinander abgewogen werden, darf keine Seite still verschwinden.
- Zähle Funktionen, Gründe oder Schritte nur dann als feste Anzahl auf, wenn diese Zahl in den Fakten explizit vorkommt.
- Bei Polizei-, Unfall- und Meldungstexten keine zusätzliche Szene, Dramatik oder Ortsdeutung ergänzen.
- Zusätzlichen Hintergrund nur aufnehmen, wenn er im Fakten-Gerüst ausdrücklich als relevant auftaucht. Keine Randnotiz zum Kern aufblasen.
- Keine Meta-Aussagen über Aufbau oder Argumentationsstruktur ergänzen, wenn sie nicht ausdrücklich in den Fakten stehen.
- Keine generischen Zusatzsätze wie „offen bleibt, wie es weitergeht", „ein genauer Zeitplan wird nicht genannt" oder „ob und wie das kommt, bleibt offen" ergänzen, wenn diese Offenheit nicht ausdrücklich in den Fakten steht.
- Bei Sammelmeldungen oder Polizeiberichten mehrere Fälle nacheinander und sauber getrennt erzählen. Keine Fakten zwischen den Fällen mischen.
- Statusverben und Unsicherheitsmarker aus den Fakten wörtlich im Sinn erhalten. Aus „Ziel" wird nicht „getötet". Aus „versucht" wird nicht „geschafft". Aus „fraglich" wird nicht „klar".
- Rang-, Rollen- und Institutionswörter aus den Fakten exakt erhalten. Aus „größte" wird nicht „höchste". Aus „anwesend" wird nicht „beteiligt". Aus möglicher Hebelwirkung wird keine erklärte Absicht.
- Politische Labels aus englischen Fakten nicht unnötig verschärfen. „Right-wing" oder „far right" nicht automatisch als „rechtsextrem" formulieren.
- Keine konkreten Angaben aus anderen Fakten ableiten. Also kein Geburtsjahr aus dem Alter, keine Entfernung aus Ortswissen, keine Versionsnummer aus Produktlogik und keine zusätzliche Zahl ergänzen, die in den Fakten nicht ausdrücklich vorkommt.
- Relative Zeitangaben nicht gegen explizite Daten oder Jahre ausspielen. Wenn Fakten ein exaktes Jahr oder Datum nennen, keine zusätzliche Formulierung wie „vergangenes Jahr" oder „Ende 2025" ergänzen, die dazu nicht passt.
- Mögliche Reaktionen, erwartete Entscheidungen und Ausblicke nie als sicher formulieren. Aus „könnte", „denkbar", „früh reagieren" oder „in 5 Jahren möglich" wird nicht „gilt als sicher" oder „kommt".

SPRACHE Gib das Briefing vollständig auf Deutsch aus. Englische oder gemischtsprachige Formulierungen aus dem Fakten-Gerüst ins Deutsche übertragen, außer bei Eigennamen, offiziellen Produktnamen oder Originaltiteln. Zahlen, Daten, Uhrzeiten, Prozentwerte und Mengen grundsätzlich als Ziffern schreiben, nicht ausschreiben. Große Zahlen nur runden, wenn dadurch keine wichtige Präzision verloren geht. Abkürzungen ausschreiben, aber Markennamen und Firmennamen nicht verfälschen.
STIL Gesprochen, klar, knapp. Kurze Hauptsätze. Aktiv. Kein Pathos.

STRUKTUR (exakt)
1) Titel als Überschrift (### Heading 3), max. 8 Wörter
2) LEERZEILE
3) Einordnung kursiv, 1 Satz, max. 25 Wörter
4) LEERZEILE
5) Hauptteil als Fließtext mit Absätzen
6) LEERZEILE
7) #### Was bleibt:
8) Zwei bis drei Sätze als Zusammenfassung
9) LEERZEILE
10) Abschluss exakt: „Weiter geht's."

Gib NUR das Briefing aus. Keine Kommentare, keine Erklärungen, keine Markdown-Codeblöcke."""

PODCAST_PROMPT = """Du wandelst Podcast-Transkripte in Audio-Briefings um. Gesprochen klingen, nicht gelesen.
PODCASTNAME Der Name des Podcasts muss dreimal vorkommen: im Titel, im ersten Satz des Hauptteils und im Abschluss.
LÄNGE Nach Inhaltsdichte skalieren. Wenig Substanz: 250-400 Wörter. Mittlere Dichte: 400-700 Wörter. Hohe Dichte: 700-1500 Wörter. Lieber vollständig als zu kurz. Kein Fülltext, aber alle relevanten Punkte.
INHALT Alle Thesen, Erkenntnisse, Beispiele, Kontroversen, Schlüsse, Wendepunkte. Kein Smalltalk, keine Werbung, keine Wiederholungen. Sprecher nur bei Relevanz oder Widerspruch. Nichts erfinden, Unklares weglassen. Mehrere Themenblöcke durch Absätze trennen.
SPRACHE Zahlen, Daten, Uhrzeiten, Prozentwerte und Mengen grundsätzlich als Ziffern schreiben, nicht ausschreiben. Große Zahlen nur bei Bedarf behutsam runden. Abkürzungen ausschreiben. Umlaute korrekt: ä ö ü ß. Nie ae, oe, ue, ss.
EINSTIEG A) Hard Fact B) Context-Clash C) Relevanz-Hook D) Konkretes Szenario. Verboten: „Stell dir vor", „Wusstest du", rhetorische Fragen, „In dieser Folge geht es um".
STIL Kurze Hauptsätze, maximal zwei pro Gedanke. Aktiv. Trockener Humor erlaubt. Kein Pathos, keine Floskeln.

STRUKTUR UND FORMATIERUNG (exakt so ausgeben)
### Podcastname: Thema, max 8 Wörter

*Einordnung hier, 1 Satz, max 25 Wörter.*

Hauptteil als Fließtext. Podcast im ersten Satz namentlich nennen. Absätze durch Leerzeilen. Keine Listen, keine Bullet Points. Zitate nur wenn entscheidend.

KEINE Zwischenüberschriften innerhalb des Hauptteils verwenden — weder mit ### noch mit **fett**. Thematische Wechsel werden ausschließlich durch einen neuen Absatz (Leerzeile) und einen klaren Einleitungssatz markiert, z.B. „Zur Wirtschaftlichkeit sagen die Schwestern, dass …". Nie einen Titel oder Header als separate Zeile mitten im Hauptteil.

#### Was bleibt:
Drei bis fünf Sätze Fließtext mit allen Kernpunkten. Podcastname erneut nennen.

Ende der Podcastzusammenfassung. Dies war der Podcast [Podcastname].

Gib NUR die Zusammenfassung aus. Keine Kommentare, keine Erklärungen, keine Markdown-Codeblöcke. Im Hauptteil KEINE Zwischentitel, keine Markdown-Marker außer den im Schema angegebenen."""

FORMAT_PROMPT = """Du formatierst eine fertige Zusammenfassung ins Audio-Briefing-Format.

REGEL 1: KÜRZE NICHTS. Jeder Satz, jedes Detail muss erhalten bleiben. Output-Länge = Input-Länge.
REGEL 2: ERFINDE NICHTS NEU. Der Text hat bereits Titel, Einordnung und Struktur. Setze nur Markdown-Marker davor. Keinen zweiten Titel, keine zweite Einordnung erfinden.
REGEL 3: Sprache so wenig wie möglich anfassen. Zahlen, Daten, Uhrzeiten und Prozentwerte exakt in der vorhandenen Ziffernform belassen. Nur offensichtliche Formatmarker setzen. Umlaute korrekt: ä ö ü ß.

DEINE AUFGABE: Setze Markdown-Marker vor die vorhandenen Elemente:
- ### vor den vorhandenen Titel
- *Sternchen* um die vorhandene Einordnung
- #### vor das vorhandene „Was bleibt:"
- Vorhandenen Abschluss beibehalten
- Listen/Bullets in Fließtext umwandeln
- Alles andere: Fließtext, nichts weglassen

Gib NUR den formatierten Text aus. Keine Kommentare, keine Erklärungen."""

WETTER_PROMPT = """Du erstellst einen Wetterbericht für Tübingen-Hirschau als Audio-Briefing.
ZIELFORMAT Gesprochen klingen, nicht gelesen. Duze den Hörer. Persönlich und direkt.
LÄNGE 200-350 Wörter.
INHALT In dieser Reihenfolge: 1) Aktuelle Lage (Temperatur, gefühlt, Wind, Himmel). 2) Nächste drei bis vier Stunden (stündliche Daten nutzen, praktische Tipps). 3) Nächste drei Tage (Trends und markante Wechsel, aber keinen „Sieger-Tag" küren, wenn das nur eine weiche Tendenz ist).
MODELLABGLEICH Wenn die Modellvalidierung uneinheitlich ist, formuliere vorsichtig. Sage dann eher „Schauer möglich" oder „noch unsicher" statt sicheren Regen zu behaupten.
REGIONALCHECK Wenn ein regionaler DWD-Text für Baden-Württemberg mitgeliefert wird, nutze ihn als Zusatz für Großwetterlage, Warncharakter, Trend und Nord-Süd-Unterschiede. Verwende ihn nicht, um exakte lokale Hirschau-Zahlen zu überschreiben. Regionale Warnungen oder Landestrends dürfen nur dann auf Hirschau heruntergebrochen werden, wenn sie zu den lokalen Daten passen oder ausdrücklich als regionaler Hinweis formuliert werden.
DATENTREUE Nutze Bewölkung und Sonnenstunden direkt. Wenn Sonnenstunden hoch und Niederschlag niedrig sind, beschreibe den Tag als sonnig oder freundlich, nicht als bewölkt. Erfinde keine Wolkenlage über die Daten hinaus.
VORSICHT Wenn Daten „Sonne mit Wolkenfeldern" oder ähnlich nahe Mischlagen zeigen, formuliere konservativ: lieber „freundlich mit Wolken" als „noch mehr Sonne". Unsichere Trends nicht glätten.
WARNSPRACHE Praktische Hinweise nur dann zuspitzen, wenn die Daten sie klar tragen. Keine erfundene Glätte-, Rutsch- oder Gefahrensprache. Keine dramatischen Verben wie „kippt deutlich", wenn der Wechsel auch nüchtern beschrieben werden kann.
REGENSPRACHE Wenn für einen Tag konkreter Niederschlag mit Millimeterwerten vorliegt oder die Niederschlagswahrscheinlichkeit klar hoch ist, formuliere Regen als wahrscheinlich oder eingeplant, nicht nur als vage Möglichkeit.
NIEDERSCHLAGSTREUE Wenn die Tagesdaten eine Niederschlagswahrscheinlichkeit ≥ 70% zeigen, darf der Tag im Briefing NICHT als überwiegend trocken oder „eher kein Regenschirm nötig" dargestellt werden. Auch wenn einzelne Stunden sonnig sind, bleibt der Gesamtcharakter des Tages unbeständig. Nenne klar, dass Niederschlag wahrscheinlich ist, auch wenn es zwischendurch auflockert.
TAGESBEZUG Für den aktuellen Tag lieber „heute" verwenden statt den Wochentag auszuschreiben. Wochentage nur nennen, wenn sie aus den gelieferten Tagesdaten klar ableitbar sind.
WINDSPRACHE Exakte Windzahlen aus Tageswerten nicht als sichere „Böen" formulieren, wenn die Daten nur den allgemeinen Tageswind zeigen. Dann lieber den Trend nennen: windiger, auffrischend, kräftigerer Wind.
STUNDENVORSICHT Die nächsten 3 bis 4 Stunden nicht pauschal dramatisieren. Wenn Niederschlag schwächer wird, Wahrscheinlichkeiten sinken oder sich das Bild innerhalb dieses Fensters beruhigt, diesen Übergang ausdrücklich nennen statt die ganze Phase als gleichbleibend unruhig zusammenzufassen.
SPRACHE Zahlen, Daten, Uhrzeiten und Temperaturen grundsätzlich als Ziffern schreiben, nicht ausschreiben. Temperatur als Grad. Tage nur dann als Wochentagnamen, wenn das sicher passt. Umlaute korrekt: ä ö ü ß.
STIL Kurze Hauptsätze. Aktiv. Trockener Humor sparsam. Kein Pathos. Unterhaltsam, aber nüchtern.

STRUKTUR UND FORMATIERUNG (exakt so ausgeben)
### Wetter-Titel mit Tübingen-Bezug, max 8 Wörter

*Einordnung hier, 1 Satz, max 25 Wörter.*

Fließtext: Erst aktuelle Lage, dann nächste Stunden, dann Tagesübersicht. Absätze durch Leerzeilen.

#### Was bleibt:
Zwei bis drei Sätze Zusammenfassung.

Weiter geht's.

Gib NUR den Wetterbericht aus. Keine Kommentare, keine Erklärungen."""

RECAP_PROMPT = """Du erstellst eine thematisch sortierte Gesamtübersicht über alle Beiträge eines Audio-Briefings.
AUFGABE Ordne alle Beiträge in Kategorien ein und fasse jeden in einem Satz zusammen. Kategorien: Wetter, Lokale Nachrichten, Innenpolitik/Gesellschaft, Internationale Politik, Kriminalität/Unglücke, Technologie, Medien/Kultur, Wissenschaft, Podcast-Zusammenfassungen. Nur vorkommende Kategorien. Keine Beiträge erfinden.
WETTER-KATEGORIE Unter „Wetter" gehört NUR der eigentliche Wetterbericht des Tages. Nachrichtenartikel über Klimaprojektionen, DWD-Studien oder Hitzewellen-Forschung sind keine Wetterberichte — ordne sie unter Wissenschaft oder einer passenden Kategorie ein.
LOKAL-REGEL Die Kategorie „Lokale Nachrichten" ist nur für Beiträge aus Stadt oder Kreis Tübingen und Stadt oder Kreis Reutlingen gedacht. Andere Regionen wie Bodensee, Stuttgart, Ulm oder Freiburg sind nicht lokal, sondern thematisch anders einzuordnen.
ABDECKUNG Jeder vorhandene Beitrag muss genau 1 Mal vorkommen. Nichts weglassen, nichts doppeln, keine Kategorie leer anlegen.
SPRACHE Schreibe durchgehend idiomatisches Deutsch. Englische oder gemischtsprachige Formulierungen aus den Einzelbeiträgen ins Deutsche übertragen, außer bei Eigennamen, offiziellen Produktnamen, Podcastnamen oder Originaltiteln.
VERDICHTUNG Bleibe strikt bei den bereits vorliegenden Einzelzusammenfassungen. Keine neuen Trendwörter, keine zusätzliche Synthese, keine neue Gewichtung und keine Details ergänzen, die im jeweiligen Einzelbeitrag nicht schon angelegt sind.
TRENNSCHÄRFE Jeder Satz muss sich ausschließlich auf den einen Beitrag beziehen, dessen ID voransteht. Keine Inhalte, Zahlen oder Fakten aus anderen Beiträgen einmischen. Wenn zwei Beiträge ein ähnliches Thema behandeln, trotzdem getrennt zusammenfassen — nicht verschmelzen.
IDENTITÄT Jede Beitragszeile muss mit der zugehörigen ID in eckigen Klammern beginnen, genau so: [12] Satz ... Verwende jede ID genau 1 Mal. ZWINGEND: Halte dieses [N]-Format bei JEDEM Beitrag durch — auch bei 80 oder mehr Beiträgen. Wechsle NIEMALS zu reinem Fließtext ohne [N]-Marker und fasse NIEMALS mehrere Beiträge unter einer Nummer zusammen. Keine ID auslassen, keine doppeln.
PODCASTS Bei Podcast-Zusammenfassungen klar als Wiedergabe des Beitrags formulieren, zum Beispiel „Der Podcast beschreibt ...", „Im Podcast geht es um ...", „Der Podcast betont ...". Nicht so schreiben, als seien diese Aussagen unmarkierte Außenfakten.
VORSICHT Keine zusätzlichen Zuspitzungen wie „total", „klar", „komplett", „massiv", „eindeutig" oder „dramatisch", wenn sie nicht schon im Einzelbeitrag angelegt sind.
STIL Gesprochen klingen. Kurze Sätze. Kein Pathos. Duze den Hörer. Umlaute korrekt: ä ö ü ß.

STRUKTUR UND FORMATIERUNG (exakt so ausgeben)
### Das war's für heute

*Hier nochmal alles sortiert im Schnelldurchlauf.*

#### Kategoriename
[1] Ein Satz pro Beitrag.
[2] Ein Satz pro Beitrag.

#### Nächste Kategorie
[3] Ein Satz pro Beitrag.

Ende des Briefings.

Gib NUR die Übersicht aus. Keine Kommentare, keine Erklärungen."""

ESSENZ_PROMPT = """Du destillierst aus einem kompletten Tagesbriefing die Essenz: Was davon ist in einer Woche, einem Monat, einem Jahr noch relevant?
AUFGABE Lies alle Beiträge durch und extrahiere die 3 bis 7 Punkte, die über den Tag hinaus wirken. Nicht die lautesten Schlagzeilen, sondern das, was Strukturen verändert, Weichen stellt oder ein Muster sichtbar macht.
PERSPEKTIVE Denke wie ein kluger Freund, der sagt: „Vergiss den Rest, aber DAS solltest du dir merken." Sei ehrlich — wenn an einem Tag nichts wirklich langfristig relevant ist, sag das.
EBENEN Mögliche Essenz-Typen (nicht alle müssen vorkommen):
- Strukturelle Verschiebungen (Machtbalance, Markt, Regulierung, Technologie)
- Muster und Trends, die sich verdichten (z.B. drittes Mal in Folge X)
- Entscheidungen mit Langzeitwirkung (Gesetze, Urteile, Investitionen)
- Unterschätzte Signale, die der Mainstream übersieht
- Verbindungen zwischen Themen, die einzeln harmlos wirken
VERBOTEN Tagesaktuelle Kleinstmeldungen aufblasen. Alles wiederholen, was im Recap schon steht. Allgemeinplätze wie „Die Welt verändert sich". Moralisieren.
QUELLTREUE Beziehe dich nur auf Inhalte, die tatsächlich im Briefing vorkommen. Keine externen Fakten oder Hintergrundwissen ergänzen.
SPRACHE Gesprochen klingen. Duze den Hörer. Kurze Sätze. Klar und direkt. Kein Pathos. Umlaute korrekt: ä ö ü ß.

STRUKTUR UND FORMATIERUNG (exakt so ausgeben)
### Was wirklich bleibt

*Die Essenz aus dem heutigen Briefing — was über den Tag hinaus zählt.*

Fließtext, ein Absatz pro Essenzpunkt. Jeder Absatz beginnt mit dem Kerngedanken in Fettdruck, dann 2-3 Sätze Erklärung. Keine Nummerierung, keine Bullets.

Gib NUR die Essenz aus. Keine Kommentare, keine Erklärungen. KEIN „Ende des Briefings." anhängen."""


VERABSCHIEDUNG_PROMPT = """Du schreibst eine kurze, warme Verabschiedung für ein tägliches Audio-Briefing.

AUFGABE Beende das Briefing mit einem klugen Zitat und einem kurzen Gedanken dazu — was man damit heute konkret anfangen kann.

ZITAT-REGELN
- NIEMALS Glückskekssprüche, Kalenderweisheiten oder abgedroschene Zitate (kein „Der Weg ist das Ziel", kein „Carpe diem", kein Gandhi, kein „Sei du selbst die Veränderung").
- Bevorzuge: weniger bekannte Zitate von klugen Köpfen — Wissenschaftler, Philosophen, Schriftsteller, Filmemacher, Musiker. Oder ein unbekanntes Zitat einer bekannten Person.
- Das Zitat soll zum Nachdenken einladen, hoffnungsvoll sein, aber Substanz haben. Es darf gerne überraschen.
- Nenne immer den Urheber.
- WICHTIG: Verwende ausschließlich echte, belegbare Zitate. Erfinde niemals ein Zitat und schreibe es einer Person zu.
- SPRACHE DES ZITATS: Das Zitat MUSS auf Deutsch sein. Wenn das Original auf Englisch oder einer anderen Sprache ist, übersetze es ins Deutsche und nenne den Urheber. Das Briefing wird vorgelesen — englische Zitate stören den Hörfluss.

EINORDNUNG
- Nach dem Zitat: 1-2 Sätze, die erklären was man damit heute anfangen kann. Kein Nacherzählen des Zitats, sondern eine eigene Wendung — ein konkreter Impuls für den Tag.
- Der Ton: wie ein kluger Freund, der dir zum Abschied noch einen Gedanken mitgibt. Persönlich, warm, nicht belehrend.
- Duze den Hörer.

VARIANZ Nicht immer das gleiche Muster. Mal mit dem Zitat starten, mal hinführen. Mal eine Beobachtung voranstellen, mal direkt einsteigen.

ABSCHLUSS Beende mit einem kurzen, persönlichen Gruß — passend zur Tageszeit. Morgens z.B. ein Wunsch für den Tag, abends etwas Ruhiges für den Feierabend, nachts etwas Warmes. Nicht immer „Schönen Tag" — variiere kreativ. Kein „Tschüss", kein „Bis bald". Eher wie ein guter Freund, der sich kurz verabschiedet.

VERBOTEN Bezug auf einzelne Briefing-Inhalte. Kitsch. Pathos. Phrasen wie „In diesem Sinne". Mehr als 90 Wörter.

SPRACHE Gesprochen klingen. Umlaute korrekt: ä ö ü ß. KEIN „Ende des Briefings." anhängen.

FORMATIERUNG (exakt so):
### Bis zum nächsten Mal

[Zitat + Einordnung + persönlicher Gruß, max. 90 Wörter]

Gib NUR die Verabschiedung aus."""

META_BRIEFING_PROMPT = """Du erstellst ein deutsches Wochen-Meta-Briefing aus mehreren fertigen Tagesbriefings.

ZIEL Nicht Tagesmeldungen nacherzählen, sondern die Woche verstehen: Was hat sich verschoben, was verdichtet sich, was ist nur Lärm, und was sollte der Hörer für die nächste Woche im Kopf behalten?

RADIKAL FILTERN — DAS WICHTIGSTE: Stapele NICHT alles aufeinander, was passiert ist. Der Hörer kann sich ohnehin nicht alles merken und will das auch nicht. Er will den VIBE der Woche: die wenigen Dinge, die wirklich zählen, klug herausgefiltert — plus deine Einordnung dazu. Lieber drei, vier Themen mit Tiefe, Haltung und einem klaren Gedanken als zwanzig brav abgehakte Meldungen. Was nur Rauschen war, lässt du bewusst weg.

ROLLE Du bist Analyst, ruhiger Coach UND kluger Kommentator zugleich. Analytisch bei Fakten und Mustern. Du darfst und sollst eine eigene, klar erkennbare journalistische Einordnung und Meinung einbringen — pointiert, mit Haltung, aber fundiert auf den Wochenfakten. Kein neutrales Referat, sondern ein kluger Kopf, der sagt, was er von der Woche hält und warum. WICHTIG: Meinung heißt Einordnung/Bewertung der vorliegenden Fakten — NIEMALS neue Fakten, Zahlen oder Namen erfinden. Kein Motivationssprech, kein Lebenshilfe-Kitsch, keine Psychologisierung, keine Effekthascherei.

INPUT Der Nutzer liefert mehrere Tagesbriefings mit Datum, Uhrzeit, Datei und Text. Nutze nur diese Inhalte. Wenn ein Thema nur an einem Tag vorkommt, darfst du es erwähnen, aber nicht künstlich zur Wochenlinie aufblasen.

QUELLTREUE
- Keine externen Fakten ergänzen.
- Keine Zahlen, Namen, Orte, Entscheidungen oder Zeitpunkte erfinden.
- Unsicherheiten klar markieren: „Das wirkt eher wie ein Signal als wie ein Trend", „Dafür gibt es in den Briefings nur einen Hinweis".
- Wenn ein Thema mehrfach vorkommt, nenne die Entwicklung, nicht jedes einzelne Vorkommen.
- Wetterberichte, reine Service-Meldungen und Kleinstmeldungen nur erwähnen, wenn sie über mehrere Tage ein größeres Muster zeigen.

ANALYSEAUFGABEN
- Wichtigste Entwicklungen: Was wurde entschieden, blockiert, verschoben oder sichtbar?
- Muster und Trends: Welche Themen tauchen wiederholt auf oder passen überraschend zusammen?
- Unterschätzte Signale: Was ging im Tagesrauschen unter, könnte aber länger wirken?
- Lokales: Was bewegt Tübingen, Reutlingen und die Region wirklich?
- Querverbindungen: Welche getrennten Themen ergeben zusammen ein größeres Bild?
- Coach-Ebene: Was heißt das für die kommende Woche? Worauf lohnt es sich zu achten? Was kann man getrost rausfiltern?

STIL Gesprochen, klar, direkt. Duze den Hörer. Kurze bis mittlere Sätze. Substanziell, warm, trocken-humorig wenn passend. Keine Floskeln wie „spannend bleibt", „nur die Zeit wird zeigen", „in einer immer komplexeren Welt". Zahlen als Ziffern. Umlaute korrekt: ä ö ü ß.

LÄNGE 2200-3500 Wörter. Substanz und Haltung schlagen Vollständigkeit — lieber prägnant, gefiltert und meinungsstark als eine lange, brave Aufzählung. Nicht künstlich strecken.

STRUKTUR UND FORMATIERUNG exakt so ausgeben:

### Wochenrückblick: [Kalenderwoche oder Zeitraum] — [knackiger Untertitel, max. 8 Wörter]

*Die Woche in der Metaebene: was bleibt, was kippt, was nächste Woche Aufmerksamkeit verdient.*

#### Die Lage in 5 Minuten
[5-8 Absätze Fließtext. Die wichtigsten Linien der Woche, thematisch sortiert. Keine Tageschronik.]

#### Die großen Stränge
[3-6 thematische Stränge. Jeder Strang hat eine kurze #### Unterüberschrift und 2-4 Absätze. Erzähle Entwicklung, Ursache/Wirkung, offene Frage.]

#### Lokales aus Tübingen und Region
[Was bewegte die Region diese Woche? Wenn nichts Substanzielles dabei war, sag das ehrlich und knapp.]

#### Querverbindungen
[Welche getrennten Themen ergeben zusammen ein größeres Bild? 3-5 konkrete Verbindungen, jeweils als Fließtextabsatz.]

#### Unterschätzte Signale
[3-6 Punkte. Jeder Punkt beginnt mit einem fett gesetzten Kerngedanken und erklärt in 2-3 Sätzen, warum er länger relevant sein könnte.]

#### Der Coach-Blick auf nächste Woche
[Persönlich und konkret: 5-7 Absätze. Worauf achten? Was nachverfolgen? Welche Themen nicht überbewerten? Welche Gespräche/Entscheidungen könnten davon profitieren? Keine To-do-Liste, sondern kluge Priorisierung.]

#### Recap der Woche
[KEINE vollständige Liste, kein Aufstapeln. Nur die ~8-12 Dinge, die wirklich hängenbleiben sollten, in wenigen knappen Zeilen — bewusst weglassen, was nur Rauschen war. Max. ~250 Wörter.]

#### Was wirklich bleibt
[5-7 substanzielle Punkte als Fließtext-Absätze. Jeder beginnt mit dem Kerngedanken in **fett** und erklärt in 2-3 Sätzen die Langzeitwirkung.]

#### Bis zum nächsten Wochenrückblick
[Kurzer, persönlicher Abschluss in 2-3 Sätzen. Optional ein echtes, belegbares Zitat. Wenn du dir beim Zitat nicht absolut sicher bist, lass es weg.]

Ende des Wochen-Briefings.

Gib NUR das Wochen-Briefing aus. Keine Vorrede, keine Erklärungen, kein JSON."""

GENIUS_SUMMARY_PROMPT_STANDARD = """Du erstellst aus einem fertigen Audio-Briefing eine ausführliche, aber kompakte Hörfassung (Standard-Variante).
ZIEL Diese Standardfassung soll beim Hören Spaß machen, gut kuratiert wirken und den ganzen Tag in einer stimmigen Erzähllogik zusammenhalten. Sie ist klar kürzer als das komplette Briefing, aber deutlich ausführlicher als eine reine Kurzfassung. Für Hörer gedacht, die die Einzelartikel nicht kennen und trotzdem alles gut einordnen wollen. Es darf kein Beitrag fehlen.
ABDECKUNG Jeder vorhandene Beitrag muss genau 1 Mal vorkommen. Nichts weglassen, nichts doppeln.
IDENTITÄT Jede Beitragszeile muss mit der zugehörigen ID in eckigen Klammern beginnen, genau so: [12] Satz ... Verwende jede ID genau 1 Mal. ZWINGEND: Halte dieses [N]-Format bei JEDEM Beitrag durch — auch bei 80 oder mehr Beiträgen. Wechsle NIEMALS zu reinem Fließtext ohne [N]-Marker und fasse NIEMALS mehrere Beiträge unter einer Nummer zusammen.
LÄNGE Pro Beitrag in der Regel 4 bis 6 dichte, gut verständliche Sätze mit Namen, Zahlen und Zusammenhang. Nur bei sehr einfachen Meldungen reichen 3 Sätze. PODCAST-Beiträge sind die Ausnahme: Bei ihnen lasse den Text unverändert lang stehen, wie er vorliegt — nicht kürzen.
TREUE Bleibe strikt bei den bereits vorliegenden Einzelzusammenfassungen. Keine neue Wertung, keine neue Gewichtung, keine zusätzlichen Fakten und keine dramatischere Sprache.
SELBSTSTÄNDIGKEIT Schreibe so, dass die Fassung auch ohne das vorherige Vollbriefing verständlich ist. Jede Zeile muss den Sachverhalt eigenständig tragen. Keine bloßen Verweise wie „dort", „dabei", „das" oder „dieser Fall", wenn der Bezug nicht im selben Satz klar benannt ist.
KONTEXT Pro Beitrag mindestens den handelnden Akteur, das Thema oder den Ort so nennen, dass man den Punkt ohne Vorwissen einordnen kann. Nicht voraussetzen, dass der Hörer die Einzelartikel schon kennt.
STIL Hörbar, klar, lebendig aber ohne Pathos. Keine Bullet Points. Deutsch, außer bei Eigennamen oder offiziellen Titeln. Darf einen Tick wärmer klingen als die Einzelzusammenfassungen — guter Radio-Journalismus, nicht steif.
PODCASTS Podcast-Beiträge werden separat vom Code in voller Länge übernommen. Für diese ID trotzdem eine ID-Zeile mit [N] und 1-2 Stichworten ausgeben, damit die Abdeckung stimmt. Kennzeichne sie als Podcast.

FORMAT (exakt)
### Kompakte Vollzusammenfassung

*Hier ist das ganze Briefing in ausführlicher, aber kompakter Form.*

[1] 4 bis 6 klare Sätze zu Beitrag 1.
[2] 4 bis 6 klare Sätze zu Beitrag 2.

#### Was du mitnehmen kannst
8 bis 12 Sätze INHALTLICHER Fließtext. Nenne die konkreten Themen und Entwicklungen des Tages mit Namen, Orten, Zahlen. Schreibe NICHT über das Briefing selbst. Sage stattdessen, was heute wirklich passiert ist: welche Entscheidungen, welche Konflikte, welche Zahlen, welche neuen Entwicklungen. Baue klare Cluster: regional, politisch/international, Wirtschaft, Tech, Gerichtliches, Podcasts.

Ende der Kurzfassung.

Gib NUR die Kurzfassung aus. Keine Kommentare, keine Erklärungen."""

GENIUS_SUMMARY_PROMPT_LONG = """Du erstellst aus einem fertigen Audio-Briefing eine sehr ausführliche Hörfassung (Lang-Variante) — nahe am Vollbriefing, aber noch verdichtet.
ZIEL Diese Langfassung ist die tiefste Hörfassung unterhalb des Voll-Briefings. Sie soll beim Hören Spaß machen, gut kuratiert wirken, klare Zusammenhänge herstellen und JEDEN Beitrag so tief behandeln, dass der Hörer den Sachverhalt wirklich versteht — inklusive Namen, Zahlen, Kontext, Hintergrund und Entwicklung. Nur redundante Nebensätze und Wiederholungen werden gegenüber dem Vollbriefing gekürzt. Es darf kein Beitrag fehlen.
ABDECKUNG Jeder vorhandene Beitrag muss genau 1 Mal vorkommen. Nichts weglassen, nichts doppeln.
IDENTITÄT Jede Beitragszeile muss mit der zugehörigen ID in eckigen Klammern beginnen, genau so: [12] Satz ... Verwende jede ID genau 1 Mal. ZWINGEND: Halte dieses [N]-Format bei JEDEM Beitrag durch — auch bei 80 oder mehr Beiträgen. Wechsle NIEMALS zu reinem Fließtext ohne [N]-Marker und fasse NIEMALS mehrere Beiträge unter einer Nummer zusammen.
LÄNGE Pro Beitrag in der Regel 7 bis 10 klare, inhaltlich dichte Sätze. Bei komplexen Themen (Gerichtsverfahren, Politik-Entscheidungen, Konfliktlagen) gerne auch 10-14 Sätze. Nur bei sehr kurzen Meldungen (Termine, Kleinstmeldungen) reichen 4-5 Sätze. Nenne immer alle relevanten Namen, Zahlen, Daten, Orte und Einordnungen aus dem Vollbriefing. PODCAST-Beiträge werden komplett unverändert übernommen.
TREUE Bleibe strikt bei den bereits vorliegenden Einzelzusammenfassungen. Keine neue Wertung, keine neue Gewichtung, keine zusätzlichen Fakten und keine dramatischere Sprache.
SELBSTSTÄNDIGKEIT Schreibe so, dass die Fassung auch ohne das vorherige Vollbriefing verständlich ist. Jede Zeile muss den Sachverhalt eigenständig tragen. Keine bloßen Verweise wie „dort", „dabei", „das" oder „dieser Fall", wenn der Bezug nicht im selben Satz klar benannt ist.
KONTEXT Pro Beitrag mindestens den handelnden Akteur, das Thema, den Ort, die relevanten Zahlen und die Entwicklungsrichtung so nennen, dass man den Punkt ohne Vorwissen vollständig einordnen kann. Lieber einen Satz zuviel erklären als den Hörer ratlos lassen.
STIL Hörbar, klar, lebendig aber ohne Pathos. Keine Bullet Points. Deutsch, außer bei Eigennamen oder offiziellen Titeln. Darf wärmer klingen als die Einzelzusammenfassungen — guter NDR-Info-Podcast, nicht steif.
PODCASTS Podcast-Beiträge werden separat vom Code in voller Länge übernommen. Für diese ID trotzdem eine ID-Zeile mit [N] und 1-2 Stichworten ausgeben, damit die Abdeckung stimmt. Kennzeichne sie als Podcast.

FORMAT (exakt)
### Kompakte Vollzusammenfassung

*Hier ist das ganze Briefing in sehr ausführlicher Form, nahe am Vollbriefing.*

[1] 7 bis 10 klare Sätze zu Beitrag 1, mit allen Namen und Zahlen.
[2] 7 bis 10 klare Sätze zu Beitrag 2.

#### Was du mitnehmen kannst
12 bis 16 Sätze INHALTLICHER Fließtext. Nenne die konkreten Themen und Entwicklungen des Tages mit Namen, Orten, Zahlen. Schreibe NICHT über das Briefing selbst. Sage stattdessen, was heute wirklich passiert ist: welche Entscheidungen, welche Konflikte, welche Zahlen, welche neuen Entwicklungen. Baue klare Cluster: regional, politisch/international, Wirtschaft, Tech, Gerichtliches, Podcasts.

Ende der Kurzfassung.

Gib NUR die Kurzfassung aus. Keine Kommentare, keine Erklärungen."""

GENIUS_SUMMARY_PROMPT_SHORT = """Du erstellst aus einem fertigen Audio-Briefing eine zweite, sehr knappe Hörfassung.
ZIEL Diese Kurzfassung ist die kurze Variante: kürzer als die Standardfassung, aber trotzdem vollständig. Es darf kein Beitrag fehlen.
ABDECKUNG Jeder vorhandene Beitrag muss genau 1 Mal vorkommen. Nichts weglassen, nichts doppeln.
IDENTITÄT Jede Beitragszeile muss mit der zugehörigen ID in eckigen Klammern beginnen, genau so: [12] Satz ... Verwende jede ID genau 1 Mal. ZWINGEND: Halte dieses [N]-Format bei JEDEM Beitrag durch — auch bei 80 oder mehr Beiträgen. Wechsle NIEMALS zu reinem Fließtext ohne [N]-Marker und fasse NIEMALS mehrere Beiträge unter einer Nummer zusammen.
LÄNGE Pro Beitrag 1 kompakter Satz, nur wenn nötig 2 kurze Sätze.
TREUE Bleibe strikt bei den bereits vorliegenden Einzelzusammenfassungen. Keine neue Wertung, keine neue Gewichtung, keine zusätzlichen Fakten und keine dramatischere Sprache.
SELBSTSTÄNDIGKEIT Schreibe so, dass die Kurzfassung auch ohne das vorherige Vollbriefing verständlich ist. Jede Zeile muss den Sachverhalt eigenständig tragen. Keine bloßen Verweise wie „dort", „dabei", „das" oder „dieser Fall", wenn der Bezug nicht im selben Satz klar benannt ist.
KONTEXT Pro Beitrag mindestens den handelnden Akteur, das Thema oder den Ort so nennen, dass man den Punkt ohne Vorwissen einordnen kann. Nicht voraussetzen, dass der Hörer die Einzelartikel schon kennt.
STIL Hörbar, dicht, klar. Kurze Sätze. Kein Pathos. Keine Bullet Points. Deutsch, außer bei Eigennamen oder offiziellen Titeln.
PODCASTS Bei Podcast-Beiträgen klar markieren, dass es sich um die Wiedergabe eines Podcasts handelt.

FORMAT (exakt)
### Kompakte Vollzusammenfassung

*Hier ist das ganze Briefing in sehr knapper, aber vollständiger Form.*

[1] 1 kurzer Satz zu Beitrag 1.
[2] 1 kurzer Satz zu Beitrag 2.

#### Was du mitnehmen kannst
2 bis 4 Sätze INHALTLICHER Fließtext. Nenne die wichtigsten Themen und Entwicklungen des Tages mit Namen, Orten, Zahlen. Schreibe NICHT über das Briefing selbst (keine Sätze wie „Die Kurzfassung deckt X Beiträge ab").

Ende der Kurzfassung.

Gib NUR die Kurzfassung aus. Keine Kommentare, keine Erklärungen."""

CONTENT_CHECK_WARN_PROMPT = """Du prüfst einen Abschnitt eines Audio-Briefings gegen seinen Quelltext.
ZIEL Finde nur materielle inhaltliche Fehler. Sei sparsam mit Warnungen. Ein gutes Briefing kürzt, paraphrasiert und rundet — das ist erwünscht, nicht fehlerhaft.

HART WARNEN NUR BEI:
1) Aussage im Briefing widerspricht dem Quelltext faktisch (Zahl falsch, Person verwechselt, Ergebnis verdreht)
2) Kernaussage fehlt komplett und dadurch entsteht ein falsches Gesamtbild
3) Briefing behauptet etwas Sicheres, das im Quelltext nur als Möglichkeit, Plan oder Vermutung steht
4) Fälle oder Personen aus einer Sammelmeldung werden vermischt

KEIN HART-WARN FÜR:
- Ein Detail fehlt, aber die Kernaussage stimmt noch → maximal notice
- Leicht andere Wortwahl ohne Bedeutungsänderung → ignorieren
- Zahlen korrekt gerundet (13,6 → „knapp 14”, 2,4 Mio → „gut 2 Millionen”) → ignorieren
- Briefing wählt 3 von 7 Beispielen aus → ignorieren, solange Aussage stimmt
- Übersetzung aus Englisch mit leicht anderem Wortlaut → ignorieren
- Formatierung, Markdown, Struktur → ignorieren

NUR ALS HINWEIS (notice):
- Möglicherweise nützliches Detail fehlt, Kern aber intakt
- Leichte Überzeichnung ohne echte Bedeutungsverschiebung
- Leichte Fremdtextreste (Cookie, Newsletter, Abo), die nicht den Inhalt verfälschen

KOMPLETT IGNORIEREN:
- Stilfragen, Tonalität, Satzlänge
- Audio-Rundungen bei Zahlen (Tendenz muss stimmen, nicht die Dezimalstelle)
- Orts- oder Personenwiederholungen
- Fehlende Nebendetails bei langen Artikeln
- Wetter-Dezimaldifferenzen wenn Tagescharakter gleich bleibt
- „[gekürzt: Mittelteil ausgelassen]” im Quelltext → im Zweifel NICHT warnen
- Briefing-Strukturelemente (###-Titel, kursive Einordnung, „Was bleibt:”, „Weiter geht's.”, Beitragszähler, „Ende der Podcastzusammenfassung.”, „Ende des Briefings.”) → kein Befund
- Markdown-Formatierung → kein Befund

ENTSCHEIDUNGSREGEL: Wenn du unsicher bist, ob etwas „warn” oder „notice” ist, wähle „notice”. Wenn du unsicher bist, ob etwas „notice” oder „ok” ist, wähle „ok”. Lieber einen echten Fehler übersehen als zehn Fehlalarme produzieren.

ANTWORTFORMAT NUR ALS JSON:
{"level":"ok"|"notice"|"warn","summary":"kurzer Ein-Satz-Befund","hard_issues":["maximal 3 kurze materielle Probleme"],"soft_issues":["maximal 3 kurze Hinweise"],"source_support":["maximal 2 kurze Quelltextstellen"]}
Keine Erklärungen außerhalb des JSON."""

CONTENT_CHECK_FULL_PROMPT = """Du prüfst einen Abschnitt eines Audio-Briefings gründlich gegen seinen Quelltext.
BEWERTE:
1) Faithfulness: stimmt der Briefing-Abschnitt faktisch mit dem Quelltext überein?
2) Coverage: fehlen Kernpunkte, die das Gesamtbild verändern würden?
3) Overclaiming: wird etwas Unsicheres als sicher dargestellt?
4) Locality: ist die regionale Einordnung plausibel?

SCORING-MASSSTAB
- Faithfulness 5: Alles faktisch korrekt. 4: Minimale Unschärfe ohne Bedeutungsänderung. 3: Ein spürbarer Fehler. 2: Mehrere Fehler. 1: Grob falsch.
- Coverage 5: Alle Kernpunkte da. 4: Ein Nebenaspekt fehlt. 3: Ein wichtiger Punkt fehlt. 2: Mehrere fehlen. 1: Thema verfehlt.
- Audio-Briefings kürzen und paraphrasieren absichtlich. Das allein senkt weder Faithfulness noch Coverage. Nur echte Fehler oder fehlende Kernaussagen senken den Score.

HART WARNEN NUR BEI:
- Faktischer Widerspruch zum Quelltext
- Kernaussage fehlt und Gesamtbild kippt
- Aus Möglichkeit wird Sicherheit
- Fälle/Personen aus Sammelmeldung vermischt

KEIN HART-WARN FÜR:
- Fehlende Nebendetails → maximal notice
- Audio-Rundungen (13,6 → „knapp 14”) → ignorieren
- Paraphrase mit gleichem Sinn → ignorieren
- Auswahlkürzung bei langen Artikeln → ignorieren wenn Kern stimmt
- Formatierung, Markdown, Struktur → ignorieren

RICHTLINIEN
- Statusverben und Modalität bei Sicherheits-/Diplomatie-Themen besonders prüfen.
- „[gekürzt: Mittelteil ausgelassen]” → vorsichtig bewerten, nicht aus Fehlen im Ausschnitt auf Fehler schließen.
- Briefing-Strukturelemente (###-Titel, kursive Einordnung, „Was bleibt:”, „Weiter geht's.”, Beitragszähler) sind kein Fremdtext.
- Nur echte Portal-Boilerplate (Cookie-Banner, Newsletter-Aufforderung, Abo-Shop) im Briefing-Text ist ein Befund.

ENTSCHEIDUNGSREGEL: Im Zweifel lieber eine Stufe milder bewerten. Fehlalarme sind schlimmer als ein übersehener Hinweis.

ANTWORTFORMAT NUR ALS JSON:
{"level":"ok"|"notice"|"warn","summary":"kurzer Ein-Satz-Befund","faithfulness":1-5,"coverage":1-5,"hard_issues":["maximal 4 materielle Probleme"],"soft_issues":["maximal 4 weichere Hinweise"],"strengths":["maximal 3 kurze Punkte"],"source_support":["maximal 2 kurze Quelltextstellen"]}
Setze level auf "warn" nur bei mindestens einem echten materiellen Problem oder faithfulness/coverage ≤ 3. Setze level auf "notice" nur bei weicheren Hinweisen.
Keine Erklärungen außerhalb des JSON."""


# ============================================================
# WMO-Wettercodes
# ============================================================

WMO_CODES = {
    0: "Klar", 1: "Überwiegend klar", 2: "Teilweise bewölkt",
    3: "Bewölkt", 45: "Nebel", 48: "Reifnebel",
    51: "Leichter Nieselregen", 53: "Nieselregen", 55: "Starker Nieselregen",
    61: "Leichter Regen", 63: "Regen", 65: "Starker Regen",
    66: "Gefrierender Regen leicht", 67: "Gefrierender Regen stark",
    71: "Leichter Schneefall", 73: "Schneefall", 75: "Starker Schneefall",
    77: "Schneekörner", 80: "Leichte Regenschauer", 81: "Regenschauer",
    82: "Starke Regenschauer", 85: "Leichte Schneeschauer",
    86: "Starke Schneeschauer", 95: "Gewitter",
    96: "Gewitter mit leichtem Hagel", 99: "Gewitter mit starkem Hagel"
}

WETTER_LAT = 48.4965
WETTER_LON = 9.0775
WETTER_TIMEZONE = "Europe/Berlin"


OPENAI_PRICING_USD_PER_MTOKEN = {
    "gpt-5.5": {"input": 5.00, "cached_input": 0.50, "output": 30.0},
    "gpt-5.5-pro": {"input": 30.00, "cached_input": 0.0, "output": 180.0},
    "gpt-5.4": {"input": 2.50, "cached_input": 0.25, "output": 15.0},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "cached_input": 0.02, "output": 1.25},
    "gpt-5.2": {"input": 1.75, "cached_input": 0.175, "output": 14.0},
    "gpt-5.2-chat-latest": {"input": 1.75, "cached_input": 0.175, "output": 14.0},
    "gpt-5": {"input": 1.25, "cached_input": 0.125, "output": 10.0},
    "gpt-5-chat-latest": {"input": 1.25, "cached_input": 0.125, "output": 10.0},
    "gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.0},
    "gpt-4.1-mini": {"input": 0.40, "cached_input": 0.10, "output": 1.60},
}

ANTHROPIC_PRICING_USD_PER_MTOKEN = {
    "claude-opus-4-8": {"input": 5.0, "cache_write": 6.25, "cache_read": 0.50, "output": 25.0},
    "claude-sonnet-5": {"input": 3.0, "cache_write": 3.75, "cache_read": 0.30, "output": 15.0},
    "claude-sonnet-4": {"input": 3.0, "cache_write": 3.75, "cache_read": 0.30, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "cache_write": 1.25, "cache_read": 0.10, "output": 5.0},
    "claude-haiku-3.5": {"input": 0.80, "cache_write": 1.0, "cache_read": 0.08, "output": 4.0},
}


def _resolve_openai_pricing(model: str) -> tuple:
    exact = OPENAI_PRICING_USD_PER_MTOKEN.get(model)
    if exact:
        return exact, model, None

    lowered = model.lower()
    if lowered.startswith("gpt-5.5-pro"):
        return OPENAI_PRICING_USD_PER_MTOKEN["gpt-5.5-pro"], "gpt-5.5-pro", None
    if lowered.startswith("gpt-5.5"):
        return OPENAI_PRICING_USD_PER_MTOKEN["gpt-5.5"], "gpt-5.5", None
    if lowered.startswith("gpt-5.4-mini"):
        return OPENAI_PRICING_USD_PER_MTOKEN["gpt-5.4-mini"], "gpt-5.4-mini", None
    if lowered.startswith("gpt-5.4-nano"):
        return OPENAI_PRICING_USD_PER_MTOKEN["gpt-5.4-nano"], "gpt-5.4-nano", None
    if lowered.startswith("gpt-5.4"):
        return OPENAI_PRICING_USD_PER_MTOKEN["gpt-5.4"], "gpt-5.4", None
    if lowered.startswith("gpt-5.3-chat-latest"):
        return OPENAI_PRICING_USD_PER_MTOKEN["gpt-5.2-chat-latest"], "gpt-5.2-chat-latest", "Preis für gpt-5.3-chat-latest wird näherungsweise wie gpt-5.2-chat-latest geschätzt."
    if lowered.startswith("gpt-5.3"):
        return OPENAI_PRICING_USD_PER_MTOKEN["gpt-5.2"], "gpt-5.2", "Preis für gpt-5.3 wird näherungsweise wie gpt-5.2 geschätzt."
    if "mini" in lowered and lowered.startswith("gpt-5"):
        return OPENAI_PRICING_USD_PER_MTOKEN["gpt-5-mini"], "gpt-5-mini", f"Preis für {model} wird näherungsweise wie gpt-5-mini geschätzt."
    if lowered.startswith("gpt-5"):
        return OPENAI_PRICING_USD_PER_MTOKEN["gpt-5"], "gpt-5", f"Preis für {model} wird näherungsweise wie gpt-5 geschätzt."
    return None, None, f"Für {model} ist kein Preis hinterlegt."


def _resolve_anthropic_pricing(model: str) -> tuple:
    lowered = model.lower()
    if lowered.startswith("claude-opus"):
        return ANTHROPIC_PRICING_USD_PER_MTOKEN["claude-opus-4-8"], "claude-opus-4-8", None
    if lowered.startswith("claude-sonnet-5"):
        return ANTHROPIC_PRICING_USD_PER_MTOKEN["claude-sonnet-5"], "claude-sonnet-5", "Konservativ mit Listenpreis 3/15 gerechnet — bis 31.08.2026 gilt der günstigere Einführungspreis (2/10). Achtung: Sonnet 5 zählt ~30% mehr Tokens für denselben Text."
    if lowered.startswith("claude-sonnet-4-6") or lowered.startswith("claude-sonnet-4"):
        return ANTHROPIC_PRICING_USD_PER_MTOKEN["claude-sonnet-4"], "claude-sonnet-4", None
    if lowered.startswith("claude-haiku-4-5") or lowered.startswith("claude-haiku"):
        return ANTHROPIC_PRICING_USD_PER_MTOKEN["claude-haiku-4-5"], "claude-haiku-4-5", None
    return None, None, f"Für {model} ist kein Preis hinterlegt."


class _BriefingCostTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self.total_usd = 0.0
        self.calls = 0
        self.billable_input_tokens = 0
        self.cached_input_tokens = 0
        self.output_tokens = 0
        self.by_model = defaultdict(lambda: {
            "calls": 0,
            "usd": 0.0,
            "billable_input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "provider": None,
        })
        self.notes = []

    def _add_note(self, note: Optional[str]):
        if note and note not in self.notes:
            self.notes.append(note)

    def record_openai(self, model: str, usage) -> None:
        if not usage:
            return
        rates, pricing_key, note = _resolve_openai_pricing(model)
        self._add_note(note)
        if not rates:
            return

        input_tokens = getattr(usage, "input_tokens", None)
        cached_tokens = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)

        if input_tokens is None:
            input_tokens = getattr(usage, "prompt_tokens", 0)
            cached_tokens = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
            output_tokens = getattr(usage, "completion_tokens", 0)

        input_tokens = int(input_tokens or 0)
        cached_tokens = int(cached_tokens or 0)
        output_tokens = int(output_tokens or 0)
        billable_input = max(input_tokens - cached_tokens, 0)
        usd = (
            billable_input * rates["input"]
            + cached_tokens * rates.get("cached_input", rates["input"])
            + output_tokens * rates["output"]
        ) / 1_000_000
        self._record("OpenAI", model, pricing_key, usd, billable_input, cached_tokens, output_tokens)

    def record_anthropic(self, model: str, usage) -> None:
        if not usage:
            return
        rates, pricing_key, note = _resolve_anthropic_pricing(model)
        self._add_note(note)
        if not rates:
            return

        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        cache_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        usd = (
            input_tokens * rates["input"]
            + cache_creation * rates.get("cache_write", rates["input"])
            + cache_read * rates.get("cache_read", rates["input"])
            + output_tokens * rates["output"]
        ) / 1_000_000
        self._record("Anthropic", model, pricing_key, usd, input_tokens + cache_creation, cache_read, output_tokens)

    def _record(self, provider: str, model: str, pricing_key: str, usd: float, billable_input: int, cached_input: int, output_tokens: int):
        with self._lock:
            self.total_usd += usd
            self.calls += 1
            self.billable_input_tokens += billable_input
            self.cached_input_tokens += cached_input
            self.output_tokens += output_tokens
            model_bucket = self.by_model[model]
            model_bucket["calls"] += 1
            model_bucket["usd"] += usd
            model_bucket["billable_input_tokens"] += billable_input
            model_bucket["cached_input_tokens"] += cached_input
            model_bucket["output_tokens"] += output_tokens
            model_bucket["provider"] = provider
            model_bucket["pricing_key"] = pricing_key

    def merge(self, other: "_BriefingCostTracker") -> None:
        """Übernimmt die Aufzeichnungen eines anderen Trackers (z.B. Fallback-Provider)."""
        if other is None or other is self:
            return
        with self._lock:
            with other._lock:
                self.total_usd += other.total_usd
                self.calls += other.calls
                self.billable_input_tokens += other.billable_input_tokens
                self.cached_input_tokens += other.cached_input_tokens
                self.output_tokens += other.output_tokens
                for model, values in other.by_model.items():
                    bucket = self.by_model[model]
                    bucket["calls"] += values["calls"]
                    bucket["usd"] += values["usd"]
                    bucket["billable_input_tokens"] += values["billable_input_tokens"]
                    bucket["cached_input_tokens"] += values["cached_input_tokens"]
                    bucket["output_tokens"] += values["output_tokens"]
                    if values.get("provider") and not bucket.get("provider"):
                        bucket["provider"] = values["provider"]
                    if values.get("pricing_key") and not bucket.get("pricing_key"):
                        bucket["pricing_key"] = values["pricing_key"]
                for note in other.notes:
                    if note not in self.notes:
                        self.notes.append(note)

    def as_dict(self) -> dict:
        with self._lock:
            return {
                "estimated": True,
                "currency": "USD",
                "total_usd": round(self.total_usd, 6),
                "calls": self.calls,
                "billable_input_tokens": self.billable_input_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "output_tokens": self.output_tokens,
                "models": [
                    {
                        "model": model,
                        "provider": values["provider"],
                        "pricing_key": values.get("pricing_key"),
                        "calls": values["calls"],
                        "usd": round(values["usd"], 6),
                        "billable_input_tokens": values["billable_input_tokens"],
                        "cached_input_tokens": values["cached_input_tokens"],
                        "output_tokens": values["output_tokens"],
                    }
                    for model, values in sorted(self.by_model.items())
                ],
                "notes": list(self.notes),
            }


def get_berlin_now() -> datetime.datetime:
    """Liefert die aktuelle Zeit in Europe/Berlin."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Europe/Berlin"))
    except ImportError:
        return datetime.datetime.now()


def _parse_created_at(value: Optional[str]) -> datetime.datetime:
    """Parst den gespeicherten Dateistempel oder nutzt notfalls jetzt."""
    if value:
        try:
            return datetime.datetime.strptime(value, "%Y-%m-%d_%H-%M-%S")
        except Exception:
            pass
    return get_berlin_now()


SEPARATOR_LINE_PATTERN = r"^\s*(?:-{2,}|={2,}|artikel ende)\s*$"
SEPARATOR_ANYWHERE_PATTERN = r"(?:-{2,}|={2,}|artikel ende|m{3,})"
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "si",
}


def _split_on_manual_separators(text: str) -> List[str]:
    """Teilt manuelle Blöcke anhand erlaubter Trenner."""
    normalized = re.sub(
        SEPARATOR_LINE_PATTERN,
        "\n<<<BRIEFING_SPLIT>>>\n",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?i)\bartikel ende\b|m{3,}",
        "\n<<<BRIEFING_SPLIT>>>\n",
        normalized,
    )
    return [
        chunk.strip()
        for chunk in re.split(r"\n?<<<BRIEFING_SPLIT>>>\n?", normalized)
        if chunk.strip() and len(chunk.strip()) > 30
    ]


def _canonical_url_for_dedup(url: str) -> str:
    """Normalisiert URLs für sichere Duplikat-Erkennung."""
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith(TRACKING_QUERY_PREFIXES) or lowered in TRACKING_QUERY_KEYS:
            continue
        filtered_query.append((key, value))

    query = urlencode(filtered_query, doseq=True)
    return urlunparse((scheme, host, path, "", query, ""))


def _normalize_text_for_dedup(text: str) -> str:
    """Bringt Texte in eine stabile Form für sichere Duplikat-Erkennung."""
    text = normalize_unicode(text)
    text = re.sub(r"https?://[^\s)]+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^#{1,4}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`>#]", " ", text)
    text = re.sub(r"Weiter geht[\'\u2019]s\.?", " ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"Ende der Podcastzusammenfassung\.\s*Dies war der Podcast\s+[^\n.]+\.?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bartikel ende\b", " ", text, flags=re.IGNORECASE)
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _deduplicate_items(items: List[str], key_fn: Callable[[str], str]) -> tuple:
    """Entfernt sichere Duplikate bei Eingabeblöcken, Reihenfolge bleibt erhalten."""
    unique_items = []
    seen = set()
    duplicates = 0
    for item in items:
        key = key_fn(item)
        if not key:
            key = _normalize_text_for_dedup(item)
        if key and key in seen:
            duplicates += 1
            continue
        if key:
            seen.add(key)
        unique_items.append(item)
    return unique_items, duplicates


def _paywall_chunk_dedup_key(chunk: str) -> str:
    if _looks_like_briefing_summary(chunk):
        base = chunk
    else:
        base = _normalize_copied_article(chunk)
    return _normalize_text_for_dedup(base)


def _podcast_chunk_dedup_key(chunk: str) -> str:
    return _normalize_text_for_dedup(chunk)


# ============================================================
# TEXT SPLITTING
# ============================================================

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_HTTP_THREAD_LOCAL = threading.local()


def _get_http_session() -> requests.Session:
    """Liefert eine Thread-lokale Session mit Connection-Pooling."""
    session = getattr(_HTTP_THREAD_LOCAL, "session", None)
    if session is not None:
        return session

    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=0)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": _DEFAULT_USER_AGENT})
    _HTTP_THREAD_LOCAL.session = session
    return session

_PAYWALL_MARKERS = [
    "Sie haben bereits ein Abo",
    "Jetzt weiterlesen mit",
    "Kennenlernabo",
    "Jahresabo",
    "Kostenlos Digitalzugang freischalten",
    "Unbegrenzt lesen auf",
    "Weitere Angebote",
]


def detect_truncated_paywall_blocks(blocks: List[str]) -> List[dict]:
    """Erkennt abgeschnittene Paywall-Blöcke (Login-Promo statt Volltext).

    Returns Liste von {"index": int, "url": str|None, "reason": str} für jeden
    Block, der offensichtlich abgeschnitten ist.
    """
    truncated = []
    for idx, block in enumerate(blocks):
        # Paywall-Marker gefunden?
        found_marker = None
        for marker in _PAYWALL_MARKERS:
            if marker in block:
                found_marker = marker
                break
        if not found_marker:
            continue
        # Text vor dem Paywall-Marker extrahieren (das ist der tatsächliche Artikel)
        marker_pos = block.find(found_marker)
        actual_content = block[:marker_pos].strip()
        # Kurz genug um als "abgeschnitten" zu gelten? (< 1500 Zeichen Nutzinhalt)
        if len(actual_content) > 1500:
            continue
        # URL aus dem Block extrahieren falls vorhanden
        url_match = re.search(r"https?://[^\s)]+", block)
        url = url_match.group(0) if url_match else None
        truncated.append({
            "index": idx,
            "url": url,
            "content_length": len(actual_content),
            "marker": found_marker,
        })
    return truncated


def split_paywall_articles(text: str) -> List[str]:
    """Trennt Paywall-Inhalte.

    Unterstützt:
    - fertige Briefing-Summaries mit Endmarker "Weiter geht's."
    - roh kopierte Artikel, getrennt durch Zeilen mit ---, ----, ==, === oder "Artikel Ende"
    - einen einzelnen rohen Artikel ohne Trenner
    """
    if not text.strip():
        return []

    manual_chunks = [text]
    if re.search(SEPARATOR_ANYWHERE_PATTERN, text, flags=re.IGNORECASE):
        manual_chunks = _split_on_manual_separators(text)

    chunks = []
    for manual_chunk in manual_chunks:
        if re.search(r"Weiter geht[\'\u2019]s\.?", manual_chunk):
            parts = re.split(r"Weiter geht[\'\u2019]s\.?", manual_chunk)
            for part in parts:
                cleaned = part.strip()
                if cleaned and len(cleaned) > 30:
                    chunks.append(cleaned + "\n\nWeiter geht's.")
        else:
            cleaned = manual_chunk.strip()
            if cleaned and len(cleaned) > 30:
                chunks.append(cleaned)

    if chunks:
        return chunks

    cleaned = text.strip()
    return [cleaned] if len(cleaned) > 30 else []


def combine_podcast_field(existing: str, new_blocks: List[str]) -> str:
    """Fügt neue Zusammenfassungen ans Podcast-Feld — IMMER mit sauberem mmm-Trenner
    davor, dazwischen und am Ende (der kurze Endmarker allein trennt nicht zuverlässig;
    der End-Trenner schützt spätere manuelle Einfügungen)."""
    blocks = [b.strip() for b in (new_blocks or []) if b and b.strip()]
    ex = (existing or "").rstrip()
    if not blocks:
        return ex
    joined = "\n\nmmm\n\n".join(blocks)
    if not ex:
        combined = joined
    elif re.search(r"(?:^|\n)\s*m{3,}\s*$", ex, flags=re.IGNORECASE):
        combined = f"{ex}\n\n{joined}"
    else:
        combined = f"{ex}\n\nmmm\n\n{joined}"
    return combined + "\n\nmmm\n"


def split_podcast_summaries(text: str) -> List[str]:
    """Trennt Podcast-Zusammenfassungen anhand der Endmarker."""
    if not text.strip():
        return []

    standard_marker = r'Ende der Podcastzusammenfassung\.\s*Dies war der Podcast\s+[^\n.]+\.?'
    chunks = []
    manual_chunks = [text]
    # Podcast-spezifische Trenner: nur mmm und "Artikel Ende", NICHT --- (kommt in Podcast-Texten vor)
    _podcast_sep_pattern = r"(?:m{3,}|artikel ende)"
    if re.search(_podcast_sep_pattern, text, flags=re.IGNORECASE):
        normalized = re.sub(
            r"(?i)\bartikel ende\b|m{3,}",
            "\n<<<BRIEFING_SPLIT>>>\n",
            text,
        )
        manual_chunks = [chunk.strip() for chunk in normalized.split("<<<BRIEFING_SPLIT>>>") if chunk.strip()]

    for manual_chunk in manual_chunks:
        if not re.search(standard_marker, manual_chunk):
            cleaned = manual_chunk.strip()
            if cleaned and len(cleaned) > 30:
                chunks.append(cleaned)
            continue

        parts = re.split(f'({standard_marker})', manual_chunk)
        i = 0
        while i < len(parts):
            content = parts[i].strip()
            if i + 1 < len(parts) and re.match(r'Ende der Podcastzusammenfassung', parts[i + 1]):
                full = content + "\n\n" + parts[i + 1].strip()
                if len(content) > 30:
                    chunks.append(full)
                i += 2
            else:
                if content and len(content) > 30:
                    chunks.append(content)
                i += 1

    return chunks


def _extract_article_urls_internal(text: str) -> tuple:
    """Extrahiert Artikel-URLs robust, entfernt Duplikate und filtert offensichtliche Nicht-Artikel."""
    if not text.strip():
        return [], 0, []

    cleaned_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cleaned_lines.append(line)

    normalized = " ".join(cleaned_lines)
    matches = re.findall(r"https?://.*?(?=(?:https?://)|\s|$)", normalized, flags=re.IGNORECASE)

    def _reject_article_url(url: str) -> Optional[str]:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "/").lower()

        if host in {"localhost", "127.0.0.1"} or host.endswith(".local"):
            return "lokale App/kein öffentlicher Artikel"

        blocked_hosts = {
            "gemini.google.com": "Chat-/App-Seite",
            "chatgpt.com": "Chat-/App-Seite",
            "platform.openai.com": "Plattform-/Billing-Seite",
            "platform.claude.com": "Plattform-/Billing-Seite",
            "portal.azure.com": "Portal-/Billing-Seite",
            "chromewebstore.google.com": "Webstore-Seite",
        }
        if host in blocked_hosts:
            return blocked_hosts[host]

        if host == "news.google.com" and (path in {"", "/", "/home"} or path.startswith("/home")):
            return "News-Startseite statt Artikel"

        if host == "feedly.com" and path.startswith("/i/subscription/"):
            return "Feed-/Abo-Link statt Artikel"

        if host in {"google.com", "www.google.com"} and path.startswith("/maps"):
            return "Karten-Link statt Artikel"

        if path in {"", "/"} and host not in {"www.tagesschau.de", "tagesschau.de"}:
            return "Startseite statt Artikel"

        blocked_path_parts = (
            "/settings/",
            "/billing",
            "/app/",
            "/login",
            "/signin",
            "/account",
            "/accounts/",
            "/resource/",
        )
        if any(part in path for part in blocked_path_parts):
            return "Portal-/Konto-Seite statt Artikel"

        ad_path_parts = (
            "/anzeige/", "/anzeigen/",
            "/sonderveroeffentlichung", "/sonderver%C3%B6ffentlichung",
            "/sponsored/", "/sponsored-post",
            "/advertorial", "/promotion/",
            "/native-ads/", "/native-advertising",
            "/verlagspartner",
        )
        if any(part in path for part in ad_path_parts):
            return "Werbung/Anzeige statt Artikel"
        if path.rstrip("/") == "/app":
            return "Portal-/Konto-Seite statt Artikel"

        return None

    urls = []
    seen = set()
    duplicates = 0
    rejected = []
    for match in matches:
        url = match.rstrip('.,;:!?)"]\'')
        dedup_key = _canonical_url_for_dedup(url) if url else ""
        reject_reason = _reject_article_url(url) if url else "ungültige URL"
        if reject_reason:
            rejected.append({"url": url, "reason": reject_reason})
            continue
        if dedup_key and dedup_key not in seen:
            urls.append(url)
            seen.add(dedup_key)
        elif dedup_key:
            duplicates += 1
    return urls, duplicates, rejected


def extract_article_urls(text: str) -> List[str]:
    """Extrahiert Artikel-URLs robust, auch wenn mehrere Links verklebt sind."""
    urls, _, _ = _extract_article_urls_internal(text)
    return urls


def inspect_article_urls(text: str) -> dict:
    """Liefert validierte URL-Infos für die UI-Vorschau."""
    urls, duplicates, rejected = _extract_article_urls_internal(text)
    return {
        "urls": urls,
        "duplicates": duplicates,
        "rejected": rejected,
    }


def assess_article_fetch_quality(urls: List[str], max_workers: int = 6) -> List[dict]:
    """Pre-Flight-Check: Fetcht alle URLs parallel und bewertet die Text-Qualität.

    Pro URL wird ein Score-Dict zurückgegeben:
        {
            "url": str,
            "source_label": str,
            "ok": bool,        # True wenn brauchbarer Quelltext
            "level": "ok" | "thin" | "boilerplate" | "failed",
            "word_count": int,
            "char_count": int,
            "warning": Optional[str],  # menschlich lesbarer Hinweis
        }

    Heuristik:
    - failed: Kein Text geholt (Network/HTTP-Fehler)
    - boilerplate: <100 Wörter ODER >40% Cookie/Newsletter/Anmelde-Boilerplate
    - thin: 100-250 Wörter (geht, aber dünn)
    - ok: >250 Wörter, ohne dominante Boilerplate
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not urls:
        return []

    boilerplate_markers = (
        "cookie", "datenschutz", "newsletter", "anmelden", "abonnent",
        "registrieren", "einloggen", "javascript", "browser", "werbung",
        "akzeptieren", "tracking", "consent",
    )

    def _assess_single(url: str) -> dict:
        try:
            payload = _fetch_article_payload(url)
        except Exception:
            payload = None
        if not payload or not payload.get("source_text"):
            return {
                "url": url,
                "source_label": source_label_from_url(url),
                "ok": False,
                "level": "failed",
                "word_count": 0,
                "char_count": 0,
                "warning": "HTTP-Fetch fehlgeschlagen oder kein Inhalt",
                "excerpt": "",
            }
        text = payload["source_text"]
        char_count = len(text)
        words = re.findall(r'\b\w+\b', text)
        word_count = len(words)
        # Excerpt für Topic-Cluster-Vorschläge (erste ~500 Zeichen — meist Headline + Lead)
        excerpt = text[:600].strip()
        # Boilerplate-Anteil grob schätzen
        text_lower = text.lower()
        boilerplate_hits = sum(text_lower.count(m) for m in boilerplate_markers)
        boilerplate_ratio = (boilerplate_hits * 8) / max(word_count, 1)  # ~8 Wörter pro Boilerplate-Marker

        if word_count < 50:
            return {
                "url": url, "source_label": payload.get("source_label", "?"),
                "ok": False, "level": "boilerplate",
                "word_count": word_count, "char_count": char_count,
                "warning": f"Nur {word_count} Wörter — fast leer, vermutlich Cookie-Wall oder JS-rendered.",
                "excerpt": excerpt,
            }
        if word_count < 100 or boilerplate_ratio > 0.5:
            return {
                "url": url, "source_label": payload.get("source_label", "?"),
                "ok": False, "level": "boilerplate",
                "word_count": word_count, "char_count": char_count,
                "warning": f"{word_count} Wörter, viel Boilerplate ({int(boilerplate_ratio*100)}%) — vermutlich nur Cookie-Banner/Pop-ups.",
                "excerpt": excerpt,
            }
        if word_count < 250:
            return {
                "url": url, "source_label": payload.get("source_label", "?"),
                "ok": True, "level": "thin",
                "word_count": word_count, "char_count": char_count,
                "warning": f"Nur {word_count} Wörter — geht, aber dünn. Briefing wird kurz.",
                "excerpt": excerpt,
            }
        return {
            "url": url, "source_label": payload.get("source_label", "?"),
            "ok": True, "level": "ok",
            "word_count": word_count, "char_count": char_count,
            "warning": None,
            "excerpt": excerpt,
        }

    results: List[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_url = {pool.submit(_assess_single, url): url for url in urls}
        for future in as_completed(future_to_url):
            try:
                results.append(future.result())
            except Exception as exc:
                url = future_to_url[future]
                results.append({
                    "url": url, "source_label": source_label_from_url(url),
                    "ok": False, "level": "failed",
                    "word_count": 0, "char_count": 0,
                    "warning": f"Unerwarteter Fehler: {exc}",
                    "excerpt": "",
                })

    # Reihenfolge wie ursprünglich (urls)
    by_url = {r["url"]: r for r in results}
    return [by_url[u] for u in urls if u in by_url]


def _stem_for_cluster(token: str) -> str:
    """Macht Tokens vergleichbar trotz Genitiv-S, Plural-Endungen etc.

    'Epsteins' und 'Epstein' werden beide zu 'epstei' (6-Char-Prefix).
    """
    return token[:6]


def find_potential_topic_duplicates(quality_results: List[dict]) -> List[dict]:
    """Schlägt URL-Cluster vor, die thematisch ähnlich aussehen.

    Greift NICHT in den Briefing-Lauf ein — nur Vorschläge zur Anzeige in der
    UI. Florian entscheidet selbst, ob er URLs aus dem Eingabefeld löscht.

    Algorithmus: 6-Zeichen-Stem-Match auf Body-Excerpts (erste 600 Zeichen).
    Konservativ aber tolerant — fängt 'Epstein' vs. 'Epsteins' vs. 'Epstein-Brief'
    durch Stem-Vergleich, ohne harte Schwellen für seltene Fälle.

    Schwelle: 4+ überlappende Stems UND Jaccard >= 0.14 (siehe Code unten).

    Returns: Liste von Clustern, jeder Cluster ist ein dict mit:
        {"members": [{"url": str, "source_label": str, "excerpt": str, "index": int}, ...]}
    Index ist die ursprüngliche Position in `quality_results` (für UI-Anzeige
    "URL 4 + URL 12").
    """
    eligible = []
    for idx, r in enumerate(quality_results):
        excerpt = (r.get("excerpt") or "").strip()
        if not excerpt or r.get("level") == "failed":
            continue
        stems = {_stem_for_cluster(t) for t in _topic_similarity_tokens(excerpt) if len(t) >= 5}
        if len(stems) < 5:
            continue
        eligible.append({
            "index": idx,
            "url": r["url"],
            "source_label": r.get("source_label", "?"),
            "excerpt": excerpt,
            "stems": stems,
        })

    if len(eligible) < 2:
        return []

    # Union-Find für Cluster-Bildung
    parent = list(range(len(eligible)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(eligible)):
        for j in range(i + 1, len(eligible)):
            stems_i = eligible[i]["stems"]
            stems_j = eligible[j]["stems"]
            overlap = len(stems_i & stems_j)
            if overlap < 4:
                continue
            union_size = len(stems_i | stems_j)
            jaccard = overlap / union_size if union_size else 0.0
            if jaccard < 0.14:
                continue
            union(i, j)

    # Gruppen sammeln
    groups: dict = {}
    for i in range(len(eligible)):
        root = find(i)
        groups.setdefault(root, []).append(eligible[i])

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        clusters.append({
            "members": [
                {
                    "index": m["index"],
                    "url": m["url"],
                    "source_label": m["source_label"],
                    "excerpt": m["excerpt"][:200],
                }
                for m in members
            ]
        })
    # Stabile Sortierung: nach kleinstem Index im Cluster
    clusters.sort(key=lambda c: min(m["index"] for m in c["members"]))
    return clusters


_DEDUP_CONFIRM_PROMPT = """Du bekommst mehrere CLUSTER von Nachrichtenbeiträgen, die thematisch ähnlich aussehen. Entscheide pro Cluster, ob die Beiträge WIRKLICH DIESELBE einzelne Story / dasselbe konkrete Ereignis behandeln (= echte Dublette) — oder ob es nur dasselbe THEMA aus verschiedenen Blickwinkeln, Aspekten oder Folgeentwicklungen ist (= KEINE Dublette, alle behalten).

STRENG SEIN: Nur als Dublette werten, wenn es sicher derselbe konkrete Vorgang ist (z.B. zweimal Bericht über dieselbe Obduktion, dieselbe Pressekonferenz, denselben Gerichtstermin, denselben Anschlag, dasselbe Unglück). Wenn ZWEI Quellen über DASSELBE konkrete Ereignis berichten (gleicher Prozess, gleicher Anschlag, gleicher Unfall — erkennbar an gleicher Person/gleichem Ort/gleichem Tag/gleicher Tat), ist es eine Dublette — AUCH wenn Schlagzeile, Wortwahl oder Schwerpunkt verschieden sind. KEINE Dublette dagegen: verschiedene Ereignisse zum gleichen Thema, andere Personen/Orte/Zahlen, Hintergrund vs. Aktuelles, eigenständiger Folgebericht. Im Zweifel: KEINE Dublette.

Bei echter Dublette: behalte den VOLLSTÄNDIGSTEN/informativsten Beitrag (keep), markiere die anderen zum Entfernen (drop).

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, keine Vorrede, kein Markdown:
{"clusters": [{"id": 1, "is_duplicate": true, "keep": 3, "drop": [7], "reason": "zweimal dieselbe Obduktion"}, {"id": 2, "is_duplicate": false, "keep": null, "drop": [], "reason": "verschiedene Blickwinkel"}]}
Die Zahlen in keep/drop sind die ITEM-INDIZES aus dem Input (die Zahl in eckigen Klammern)."""


def llm_confirm_duplicate_clusters(clusters, model="sonnet", cli_path=None, timeout_seconds=180):
    """Lässt Claude entscheiden, welche Kandidaten-Cluster ECHTE Dubletten sind
    (gleiche Story) und welche nur gleiches Thema / verschiedene Blickwinkel.

    Returns dict {cluster_1based_id: {"is_duplicate": bool, "keep": item_index|None,
    "drop": [item_index, ...], "reason": str}}. Bei Fehler/keinem CLI: {} (die UI
    fällt dann auf 'nichts vorausgewählt' zurück — sicher).
    Läuft launchd-sicher über --append-system-prompt (NICHT --system-prompt)."""
    cli = cli_path or _locate_claude_cli()
    if not cli or not clusters:
        return {}
    lines = []
    for ci, cl in enumerate(clusters, 1):
        lines.append(f"=== CLUSTER {ci} ===")
        for m in cl.get("members", []):
            excerpt = (m.get("excerpt") or "").replace("\n", " ").strip()[:900]
            lines.append(f"[{m['index']}] Quelle: {m.get('source_label', '?')} — {excerpt}")
        lines.append("")
    payload = _DEDUP_CONFIRM_PROMPT + "\n\n=== KANDIDATEN ===\n\n" + "\n".join(lines)
    cmd = [
        cli, "--print", "--output-format", "text", "--model", model,
        "--dangerously-skip-permissions", "--effort", "low",
        "--append-system-prompt", "Antworte ausschließlich mit dem JSON-Objekt, ohne Vorrede oder Erklärung.",
    ]
    try:
        sr = _run_claude_cli_subprocess_streaming(
            cmd, payload, timeout_seconds=timeout_seconds,
            expected_duration_s=30.0, label="Dubletten-Prüfung (Claude)",
        )
    except Exception:
        return {}
    if not sr.get("ok") or sr.get("returncode") != 0:
        return {}
    raw = (sr.get("stdout") or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    block = match.group(0)
    data = None
    for candidate in (block, _repair_llm_json_quotes(block)):
        try:
            data = json.loads(candidate)
            break
        except Exception:
            continue
    if not isinstance(data, dict):
        return {}
    out = {}
    for c in data.get("clusters", []):
        try:
            cid = int(c.get("id"))
        except (TypeError, ValueError):
            continue
        keep = c.get("keep")
        try:
            keep = int(keep) if keep is not None else None
        except (TypeError, ValueError):
            keep = None
        drop = []
        for x in (c.get("drop") or []):
            try:
                drop.append(int(x))
            except (TypeError, ValueError):
                continue
        out[cid] = {
            "is_duplicate": bool(c.get("is_duplicate")),
            "keep": keep,
            "drop": drop,
            "reason": str(c.get("reason", ""))[:200],
        }
    return out


_TOPIC_SIMILARITY_STOPWORDS = {
    "aber", "alle", "allen", "aller", "alles", "also", "auch", "beim", "bereits",
    "dabei", "damit", "dann", "dass", "dem", "den", "der", "des", "die", "dieser",
    "dieses", "doch", "eine", "einem", "einen", "einer", "eines", "einfach", "einmal",
    "erklärt", "erst", "etwa", "fuer", "für", "gibt", "habe", "haben", "hat", "heute",
    "hier", "ihre", "ihren", "ihrer", "ihres", "immer", "jetzt", "kann", "könnte",
    "laut", "mehr", "milliarden", "millionen", "nach", "noch", "oder", "rund", "schon",
    "sich", "sie", "sind", "soll", "sollen", "sowie", "teil", "the", "their", "there",
    "this", "über", "ueber", "und", "unter", "viele", "vom", "von", "war", "waren",
    "weil", "weiter", "werden", "wird", "with", "zeit", "zum", "zur",
}


def _topic_similarity_tokens(text: str) -> List[str]:
    normalized = _normalize_text_for_dedup(text)
    tokens = []
    for token in normalized.split():
        if len(token) < 4:
            continue
        if token.isdigit():
            continue
        if token in _TOPIC_SIMILARITY_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _token_jaccard(tokens_a: List[str], tokens_b: List[str]) -> float:
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a or not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _sequence_ratio(text_a: str, text_b: str) -> float:
    if not text_a or not text_b:
        return 0.0
    return SequenceMatcher(None, text_a, text_b).ratio()


def _topic_preview_from_payload(payload: dict) -> dict:
    text = payload.get("source_text", "") or ""
    cleaned = _normalize_copied_article(text) if text else ""

    lines = []
    for raw_line in (cleaned or text).splitlines():
        line = _normalize_copy_line(raw_line)
        if not line:
            continue
        if _is_non_content_line(line) or _is_credit_line(line) or _looks_like_meta_or_promo_line(line):
            continue
        if _is_byline_line(line) or _is_date_line(line):
            continue
        lines.append(line)
        if len(lines) >= 14:
            break

    lines = _merge_header_lines(lines)
    fallback = re.sub(r"\s+", " ", cleaned or text).strip()

    title = ""
    title_idx = 0
    for idx, line in enumerate(lines[:6]):
        words = line.split()
        if 3 <= len(words) <= 20 and 15 <= len(line) <= 140 and not line.endswith((".", "!", "?")):
            title = line
            title_idx = idx
            break
    if not title and lines:
        title = lines[0][:140]
        title_idx = 0
    if not title:
        title = fallback[:140]

    lead_parts = []
    for line in lines[title_idx + 1:]:
        if line == title:
            continue
        if not _looks_like_content_line(line) and len(line.split()) < 6:
            continue
        lead_parts.append(line)
        if len(" ".join(lead_parts)) >= 420 or len(lead_parts) >= 4:
            break

    lead = " ".join(lead_parts).strip()
    if not lead and fallback:
        if title and fallback.lower().startswith(title.lower()):
            lead = fallback[len(title):].strip(" .:-")
        else:
            lead = fallback
        lead = lead[:420]

    preview = (lead or fallback or title)[:280].strip()
    title_norm = _normalize_text_for_dedup(title)
    lead_norm = _normalize_text_for_dedup(lead)
    combined_norm = _normalize_text_for_dedup(f"{title} {lead}".strip())

    return {
        "url": payload.get("url", ""),
        "source_label": payload.get("source_label", "Quelle unbekannt"),
        "title": title or payload.get("source_label", "Artikel"),
        "preview": preview,
        "title_norm": title_norm,
        "lead_norm": lead_norm,
        "combined_norm": combined_norm,
        "title_tokens": _topic_similarity_tokens(title),
        "lead_tokens": _topic_similarity_tokens(lead),
        "combined_tokens": _topic_similarity_tokens(f"{title} {lead}".strip()),
        "content_chars": len(text),
    }


def _topic_similarity_bucket(item_a: dict, item_b: dict) -> Optional[str]:
    title_overlap = len(set(item_a["title_tokens"]) & set(item_b["title_tokens"]))
    lead_overlap = len(set(item_a["lead_tokens"]) & set(item_b["lead_tokens"]))
    combined_overlap = len(set(item_a["combined_tokens"]) & set(item_b["combined_tokens"]))

    title_ratio = _sequence_ratio(item_a["title_norm"], item_b["title_norm"])
    title_jaccard = _token_jaccard(item_a["title_tokens"], item_b["title_tokens"])
    lead_ratio = _sequence_ratio(item_a["lead_norm"][:500], item_b["lead_norm"][:500])
    lead_jaccard = _token_jaccard(item_a["lead_tokens"], item_b["lead_tokens"])
    combined_ratio = _sequence_ratio(item_a["combined_norm"][:700], item_b["combined_norm"][:700])
    combined_jaccard = _token_jaccard(item_a["combined_tokens"], item_b["combined_tokens"])

    if (
        title_ratio >= 0.88
        or (title_overlap >= 4 and title_jaccard >= 0.60)
        or (title_overlap >= 3 and lead_overlap >= 6 and lead_jaccard >= 0.26)
        or (combined_overlap >= 8 and combined_ratio >= 0.82)
        or (combined_overlap >= 10 and combined_jaccard >= 0.34)
    ):
        return "certain"

    if (
        title_ratio >= 0.76
        or (title_overlap >= 3 and title_jaccard >= 0.42)
        or (title_overlap >= 2 and lead_overlap >= 6 and lead_jaccard >= 0.22)
        or (combined_overlap >= 7 and combined_ratio >= 0.72)
        or (combined_overlap >= 8 and combined_jaccard >= 0.26)
    ):
        return "possible"

    return None


def _topic_similarity_groups(items: List[dict], edges: List[tuple], bucket: str) -> List[dict]:
    if not items or not edges:
        return []

    index_map = {item["url"]: idx for idx, item in enumerate(items)}
    adjacency = {item["url"]: set() for item in items}
    for url_a, url_b in edges:
        if url_a in adjacency and url_b in adjacency:
            adjacency[url_a].add(url_b)
            adjacency[url_b].add(url_a)

    groups = []
    visited = set()
    group_number = 0
    for item in items:
        url = item["url"]
        if url in visited or not adjacency[url]:
            continue
        stack = [url]
        component = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            stack.extend(sorted(adjacency[current] - visited))

        if len(component) < 2:
            continue

        ordered_urls = sorted(component, key=lambda current_url: index_map[current_url])
        group_items = [copy.deepcopy(items[index_map[current_url]]) for current_url in ordered_urls]
        recommended_item = max(
            group_items,
            key=lambda current: (
                min(current.get("content_chars", 0), 5000),
                1 if current.get("source_label") and current.get("source_label") != "Quelle unbekannt" else 0,
                len(current.get("title", "")),
                -len(current.get("url", "")),
            ),
        )

        reasons = []
        longest_chars = max(entry.get("content_chars", 0) for entry in group_items)
        if recommended_item.get("content_chars", 0) >= max(1800, int(longest_chars * 0.95)):
            reasons.append("mehr Nutzinhalt")
        if recommended_item.get("source_label") and recommended_item.get("source_label") != "Quelle unbekannt":
            reasons.append("klarere Quelle")
        if 20 <= len(recommended_item.get("title", "")) <= 120:
            reasons.append("sauberere Artikelseite")
        if not reasons:
            reasons.append("vollständigere Artikelseite")

        for entry in group_items:
            entry["recommended"] = entry["url"] == recommended_item["url"]

        group_number += 1
        groups.append({
            "group_id": f"{bucket}_{group_number}",
            "recommended_url": recommended_item["url"],
            "recommendation_reason": ", ".join(reasons[:2]),
            "items": group_items,
        })

    return groups


def _best_model_for_provider(model: str) -> str:
    """Gibt das stärkste Modell für den gleichen Provider zurück."""
    if model.startswith("gpt-"):
        return "gpt-5.5"
    return "claude-opus-4-8"


def inspect_similar_article_topics(
    urls: List[str],
    progress_callback: Optional[Callable] = None,
    relevance_filter: bool = False,
    api_key: str = "",
    model: str = "claude-sonnet-5",
) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    valid_urls = [url for url in urls if url]
    if len(valid_urls) < 2:
        return {
            "all_urls": valid_urls,
            "certain": [],
            "possible": [],
            "prefetched_payloads": {},
            "unchecked_urls": [],
            "relevance_scores": {},
        }

    def report(step: str, progress: float):
        if progress_callback:
            progress_callback(step, progress)

    report("Artikeltexte für Themen-Check werden geladen...", 0.02)
    payload_map = {}
    with ThreadPoolExecutor(max_workers=_article_fetch_workers(len(valid_urls))) as pool:
        future_to_url = {pool.submit(_fetch_article_payload, url): url for url in valid_urls}
        for completed, future in enumerate(as_completed(future_to_url), start=1):
            url = future_to_url[future]
            try:
                payload = future.result()
            except Exception as exc:
                payload = None
                print(f"[topic-check] Fetch fehlgeschlagen: {url} ({type(exc).__name__}: {exc})", file=sys.stderr)
            if payload:
                payload_map[url] = payload
            report(
                f"Artikeltexte für Themen-Check werden geladen ({completed}/{len(valid_urls)})...",
                0.02 + (completed / max(len(valid_urls), 1)) * 0.58,
            )

    missing_urls = [url for url in valid_urls if url not in payload_map]
    if missing_urls:
        report(f"{len(missing_urls)} Artikeltexte werden für Themen-Check nachgeholt...", 0.65)
        for idx, url in enumerate(missing_urls, start=1):
            payload = _fetch_article_payload(url)
            if payload:
                payload_map[url] = payload
            report(
                f"Artikeltexte für Themen-Check werden nachgeholt ({idx}/{len(missing_urls)})...",
                0.65 + (idx / max(len(missing_urls), 1)) * 0.10,
            )

    previews = [_topic_preview_from_payload(payload_map[url]) for url in valid_urls if url in payload_map]
    report("Ähnliche Themen werden verglichen...", 0.80)

    certain_edges = []
    possible_edges = []
    for i in range(len(previews)):
        for j in range(i + 1, len(previews)):
            bucket = _topic_similarity_bucket(previews[i], previews[j])
            if bucket == "certain":
                certain_edges.append((previews[i]["url"], previews[j]["url"]))
            elif bucket == "possible":
                possible_edges.append((previews[i]["url"], previews[j]["url"]))

    certain_groups = _topic_similarity_groups(previews, certain_edges, "certain")
    certain_urls = {
        item["url"]
        for group in certain_groups
        for item in group.get("items", [])
    }

    remaining_previews = [item for item in previews if item["url"] not in certain_urls]
    filtered_possible_edges = [
        (url_a, url_b)
        for url_a, url_b in possible_edges
        if url_a not in certain_urls and url_b not in certain_urls
    ]
    possible_groups = _topic_similarity_groups(remaining_previews, filtered_possible_edges, "possible")

    # Optionaler Relevanz-Filter
    relevance_scores = {}
    if relevance_filter and api_key and previews:
        report("Relevanz wird bewertet...", 0.85)
        # Relevanz nutzt immer das stärkste verfügbare Modell desselben Providers
        relevance_model = _best_model_for_provider(model)
        print(f"[relevance] Starte Bewertung: {len(previews)} Artikel, Modell={relevance_model}", file=sys.stderr)
        relevance_client = _build_client(api_key, relevance_model)
        relevance_scores = rate_article_relevance(previews, relevance_client, relevance_model)
        print(f"[relevance] Ergebnis: {len(relevance_scores)} Scores zurück", file=sys.stderr)
    elif relevance_filter:
        print(f"[relevance] Übersprungen: api_key={'JA' if api_key else 'NEIN'}, previews={len(previews) if previews else 0}", file=sys.stderr)

    report("Themen-Check fertig.", 1.0)
    return {
        "all_urls": valid_urls,
        "certain": certain_groups,
        "possible": possible_groups,
        "prefetched_payloads": payload_map,
        "unchecked_urls": [url for url in valid_urls if url not in payload_map],
        "relevance_scores": relevance_scores,
    }


RELEVANCE_PROMPT = """Du bewertest Nachrichtenartikel für ein tägliches Audio-Briefing nach Relevanz.
Der Hörer ist ein gut informierter Mensch aus Süddeutschland, der sich für Lokales, Politik, Wirtschaft, Technologie und Gesellschaft interessiert.

Bewerte jeden Artikel auf einer Skala von 1-5:
5 = sehr relevant (wichtige Nachricht, breites öffentliches Interesse, direkte Auswirkungen)
4 = relevant (solide Nachricht, informativ)
3 = mittelmäßig (nett zu wissen, aber verzichtbar wenn es viel gibt)
2 = weniger relevant (Nischenthema, geringer Nachrichtenwert, Listicle, Produktwerbung)
1 = kaum relevant (reine Werbung, Clickbait, extrem spezifisch)

WICHTIG:
- Lokalnachrichten aus Tübingen/Region sind tendenziell relevanter.
- Lieber ein paar zu viele behalten als zu wenige. Im Zweifel Score 3.
- Doppelte/sehr ähnliche Themen erkennst du NICHT — das macht ein anderes System.

Antworte NUR als JSON:
{"ratings": [{"index": 1, "score": 4, "reason": "kurzer Grund auf Deutsch"}, ...]}"""


def rate_article_relevance(
    previews: List[dict],
    client,
    model: str = "claude-haiku-4-5-20251001",
) -> dict:
    """Bewertet Artikel nach Relevanz. Gibt {url: {"score": int, "reason": str}} zurück."""
    if not previews:
        return {}

    numbered_lines = []
    for i, p in enumerate(previews, start=1):
        source = p.get("source_label", "")
        title = p.get("title", "Artikel")
        lead = p.get("preview", "")[:200]
        line = f"{i}. [{source}] {title}"
        if lead:
            line += f" — {lead}"
        numbered_lines.append(line)

    user_input = f"{len(previews)} Artikel zur Bewertung:\n\n" + "\n".join(numbered_lines)

    try:
        result = summarize(client, user_input, RELEVANCE_PROMPT, model, max_tokens=2000, json_mode=True)
        if not result:
            return {}

        data = json.loads(result)
        ratings = data.get("ratings", [])

        scores = {}
        for entry in ratings:
            idx = int(entry.get("index", 0)) - 1
            if 0 <= idx < len(previews):
                scores[previews[idx]["url"]] = {
                    "score": max(1, min(5, int(entry.get("score", 3)))),
                    "reason": str(entry.get("reason", ""))[:120],
                    "title": previews[idx].get("title", "Artikel"),
                }

        # Fehlende Artikel bekommen Score 3 (neutral)
        for p in previews:
            if p["url"] not in scores:
                scores[p["url"]] = {"score": 3, "reason": "Keine Bewertung erhalten", "title": p.get("title", "Artikel")}

        return scores
    except Exception as e:
        import traceback
        print(f"[relevance] Bewertung fehlgeschlagen: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        # Fehler auch im Return transportieren damit die UI ihn anzeigen kann
        return {"_error": f"{type(e).__name__}: {e}"}


SOURCE_DOMAIN_MAP = {
    "news.google.com": "Google News",
    "stadt-bremerhaven.de": "Caschys Blog",
    "cashysblog.de": "Caschys Blog",
    "arstechnica.com": "Ars Technica",
    "swp.de": "Schwäbisches Tagblatt",
    "tagblatt.de": "Schwäbisches Tagblatt",
    "gea.de": "GEA",
    "ntv.de": "n-tv",
    "n-tv.de": "n-tv",
    "spiegel.de": "SPIEGEL",
    "zeit.de": "ZEIT",
    "faz.net": "FAZ",
    "welt.de": "WELT",
    "heise.de": "heise",
    "tagesschau.de": "tagesschau",
    "zdf.de": "ZDF",
    "sz.de": "SZ",
    "sueddeutsche.de": "SZ",
    "handelsblatt.com": "Handelsblatt",
    "wiwo.de": "WiWo",
    "focus.de": "FOCUS",
    "stern.de": "stern",
    "bild.de": "BILD",
    "t-online.de": "t-online",
}

SOURCE_TEXT_MARKERS = (
    ("cashys blog", "Cashys Blog"),
    ("cashysblog.de", "Cashys Blog"),
    ("schwäbisches tagblatt", "Schwäbisches Tagblatt"),
    ("schwaebisches tagblatt", "Schwäbisches Tagblatt"),
    ("swp.de", "Schwäbisches Tagblatt"),
    ("gea.de", "GEA"),
    ("dem gea folgen", "GEA"),
    ("meine nachrichten reutlingen", "GEA"),
    ("reutlinger general-anzeiger", "GEA"),
    ("ntv.de", "n-tv"),
    ("n-tv", "n-tv"),
    ("spiegel.de", "SPIEGEL"),
    ("zeit.de", "ZEIT"),
    ("faz.net", "FAZ"),
    ("welt.de", "WELT"),
    ("heise.de", "heise"),
    ("tagesschau.de", "tagesschau"),
    ("zdf.de", "ZDF"),
    ("sueddeutsche.de", "SZ"),
    ("süddeutsche", "SZ"),
    ("handelsblatt.com", "Handelsblatt"),
    ("wiwo.de", "WiWo"),
    ("focus.de", "FOCUS"),
    ("stern.de", "stern"),
    ("bild.de", "BILD"),
    ("t-online.de", "t-online"),
)


def _fallback_source_label(host: str) -> str:
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org"}:
        base = parts[-3]
    elif len(parts) >= 2:
        base = parts[-2]
    elif parts:
        base = parts[0]
    else:
        return "Quelle unbekannt"

    if base.isalpha() and len(base) <= 4:
        return base.upper()
    return base.replace("-", " ")


def source_label_from_url(url: str) -> str:
    """Leitet eine kurze Quellenbezeichnung aus einer URL ab."""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]

    for suffix, label in SOURCE_DOMAIN_MAP.items():
        if host == suffix or host.endswith(f".{suffix}"):
            return label
    return _fallback_source_label(host)


def source_label_from_text(text: str) -> Optional[str]:
    """Errät eine Quellenbezeichnung aus roh kopiertem Text."""
    lowered = text.lower()
    compact = re.sub(r"[^a-z0-9]+", "", lowered)
    for marker, label in SOURCE_TEXT_MARKERS:
        marker_compact = re.sub(r"[^a-z0-9]+", "", marker.lower())
        if marker_compact and len(marker_compact) <= 4 and "." not in marker and " " not in marker:
            if re.search(rf"(?<![a-z0-9]){re.escape(marker.lower())}(?![a-z0-9])", lowered):
                return label
        elif marker in lowered:
            return label
        if marker_compact and len(marker_compact) >= 5 and marker_compact in compact:
            return label

    url_match = re.search(r"https?://[^\s)]+", text, flags=re.IGNORECASE)
    if url_match:
        return source_label_from_url(url_match.group(0))
    return "Quelle unbekannt"


def _infer_paywall_chunk_source_hints(chunks: List[str]) -> List[Optional[str]]:
    """Leitet für Paywall-Blöcke vorsichtige Quellen-Hinweise aus Nachbarblöcken ab.

    Nur für Blöcke ohne explizite Quellenmarker. Gemischte Mittelbereiche bleiben
    absichtlich ungelöst, damit keine falschen Labels aggressiv propagiert werden.
    """
    hints: List[Optional[str]] = [None] * len(chunks)
    explicit_labels: List[Optional[str]] = []
    for chunk in chunks:
        label = source_label_from_text(chunk)
        explicit_labels.append(None if not label or label == "Quelle unbekannt" else label)

    known_indices = [idx for idx, label in enumerate(explicit_labels) if label]
    if not known_indices:
        return hints

    for idx, label in enumerate(explicit_labels):
        if label:
            hints[idx] = label
            continue

        prev_known = None
        next_known = None
        for known_idx in reversed(known_indices):
            if known_idx < idx:
                prev_known = known_idx
                break
        for known_idx in known_indices:
            if known_idx > idx:
                next_known = known_idx
                break

        if prev_known is not None and next_known is not None:
            prev_label = explicit_labels[prev_known]
            next_label = explicit_labels[next_known]
            if prev_label == next_label:
                hints[idx] = prev_label
            continue

        if prev_known is None and next_known is not None:
            if all(explicit_labels[j] is None for j in range(0, next_known)):
                hints[idx] = explicit_labels[next_known]
            continue

        if next_known is None and prev_known is not None:
            if all(explicit_labels[j] is None for j in range(prev_known + 1, len(chunks))):
                hints[idx] = explicit_labels[prev_known]

    return hints


def _strip_source_specific_noise(text: str, source_label: Optional[str]) -> str:
    """Entfernt bekannte, nicht inhaltliche Textbausteine einzelner Quellen."""
    cleaned = normalize_unicode(text)
    if source_label == "Cashys Blog":
        cleaned = re.sub(
            r"Transparenzhinweis im Text:\s*Es sind Partnerlinks enthalten\..*?keinen Einfluss auf die Berichterstattung\.",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(
            r"Es sind Partnerlinks enthalten\..*?keinen Einfluss auf die Berichterstattung\.",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(
            r"Transparenzhinweis im Text:.*?(?:\n{2,}|$)",
            "\n\n",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        paragraphs = re.split(r"\n{2,}", cleaned)
        filtered = []
        for paragraph in paragraphs:
            lowered = paragraph.strip().lower()
            if not lowered:
                continue
            if "partnerlinks" in lowered and (
                "provision" in lowered
                or "berichterstattung" in lowered
                or "für käufer ändert sich der preis nicht" in lowered
                or "fur kaufer andert sich der preis nicht" in lowered
            ):
                continue
            filtered.append(paragraph.strip())
        cleaned = "\n\n".join(filtered)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    elif source_label == "GEA":
        gea_noise_patterns = (
            r"^\s*Meine Nachrichten(?:\s*Reutlingen.*)?$",
            r"^\s*MeineNachrichten(?:Reutlingen.*)?$",
            r"^\s*gea\.de\s*$",
            r"^\s*Service\s*Abo-Shop\s*Anzeigen\s*Vorteilswelt\s*E-Paper\s*$",
            r"^\s*ServiceAbo-ShopAnzeigenVorteilsweltE-Paper\s*$",
            r"^\s*Mein Profil\s*$",
            r"^\s*Startseite.*$",
            r"^\s*Dem GEA folgen\s*&\s*informiert bleiben\s*$",
            r"^\s*DemGEAfolgen.*informiertbleiben\s*$",
        )
        filtered_lines = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            compact = re.sub(r"[^a-z0-9]+", "", line.lower())
            if any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in gea_noise_patterns):
                continue
            if compact.startswith("meinenachrichten") and "reutlingen" in compact:
                continue
            if compact.startswith("startseite"):
                continue
            if "demgeafolgen" in compact and "informiertbleiben" in compact:
                continue
            if compact.startswith("serviceaboshopanzeigenvorteilsweltepaper"):
                continue
            filtered_lines.append(raw_line)
        cleaned = "\n".join(filtered_lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _strip_general_boilerplate(text: str) -> str:
    """Entfernt allgemeine Boilerplate-Absätze aus Quelltexten aller Quellen.

    Läuft nach _strip_source_specific_noise auf ALLEN Quelltexten, damit
    Cookie-Banner, Consent-Dialoge, Newsletter-Prompts und Portal-Reste
    nicht beim LLM landen.
    """
    if not text:
        return text

    _BOILERPLATE_PARAGRAPH_PATTERNS = (
        # Cookie/Consent
        r"(?i)wir verwenden cookies",
        r"(?i)diese (?:website|seite|webseite) (?:verwendet|nutzt|benutzt) cookies",
        r"(?i)wir (?:und unsere partner )?nutzen cookies",
        r"(?i)mit (?:dem )?klick auf .{0,40}akzeptieren",
        r"(?i)(?:alle )?cookies (?:akzeptieren|ablehnen|zulassen)",
        r"(?i)cookie[- ]?einstellungen",
        r"(?i)einwilligung .{0,40}cookie",
        r"(?i)zur datenschutzerkl.rung",
        r"(?i)privacy (?:policy|settings|notice)",
        r"(?i)we use cookies",
        r"(?i)accept (?:all )?cookies",
        r"(?i)manage (?:cookie|consent|privacy)",
        r"(?i)consent .{0,30}(?:manage|setting|choice)",
        r"(?i)einwilligungseinstellungen",
        # Newsletter/Push
        r"(?i)(?:jetzt|hier) (?:den )?newsletter (?:abonnieren|bestellen|anmelden)",
        r"(?i)(?:melden sie sich|melde dich) (?:für|zu) (?:unserem|unseren|dem) newsletter",
        r"(?i)push[- ]?nachrichten (?:aktivieren|erhalten|abonnieren)",
        r"(?i)sign up for (?:our|the) newsletter",
        r"(?i)subscribe to (?:our|the) newsletter",
        # Abo/Paywall-Prompts (nicht der Artikelinhalt)
        r"(?i)(?:jetzt|hier) (?:ein )?(?:digital[- ]?)?abo (?:abschließen|bestellen|sichern)",
        r"(?i)(?:dieser|diesen) artikel (?:lesen sie|gibt es) (?:nur )?(?:im|mit) (?:abo|digital)",
        r"(?i)(?:jetzt|hier) (?:30 tage )?(?:kostenlos )?testen",
        r"(?i)(?:weiter|artikel) lesen mit .{0,20}abo",
        # Portal-/Navigationsreste (nur kurze Zeilen)
        r"(?i)^(?:startseite|home|menü|navigation|hauptmenü)\s*$",
        r"(?i)^(?:anmelden|registrieren|mein konto|abmelden|einloggen)\s*$",
        r"(?i)nutzungsbedingungen\s+datenschutz\s+impressum",
        r"(?i)^service\s+abo[- ]?shop\s+anzeigen",
    )

    paragraphs = re.split(r"\n{2,}", text)
    cleaned = []
    for paragraph in paragraphs:
        stripped = paragraph.strip()
        if not stripped:
            continue
        # Lange Absätze sind fast immer echter Inhalt
        if len(stripped) > 600:
            cleaned.append(paragraph)
            continue
        if any(re.search(pattern, stripped) for pattern in _BOILERPLATE_PARAGRAPH_PATTERNS):
            continue
        cleaned.append(paragraph)

    result = "\n\n".join(cleaned).strip()
    return result if result else text


# Navigations-Marker, die in roh kopierten Paywall-/Webseiten-Texten als eigene
# Zeilen auftauchen (GEA, SWP, Tagblatt etc.). Konservativ genutzt: eine Zeile
# fliegt nur raus, wenn sie KURZ ist UND einen Marker enthält, oder ein ALLCAPS-
# Klumpen ohne Satzstruktur ist. Echter Artikeltext bleibt unangetastet.
_PAYWALL_NAV_MARKERS = (
    "e-paper", "alle themen", "menü schließen", "mein konto", "abmelden",
    "meine swp", "meine gea", "merkliste", "newsletter", "podcasts", "push",
    "meine nachrichten", "abo-shop", "vorteilswelt", "mein profil",
    "startseite", "dem gea folgen", "informiert bleiben", "nutzungsbedingungen",
    "services & portale", "bluum", "jobs & arbeitgeber", "todesanzeigen",
    "immobilienangebote", "kfz-angebote", "kleinanzeigen", "werben mit",
    "mediadaten", "online-anzeigenaufgabe", "sonderthemen", "swp shop",
    "unternehmen der region", "erscheinungsbild", "mehr von swp", "aboshop",
    "trauerportal", "wochenblätter", "impressum", "datenschutz",
)


def _strip_paywall_navigation(text: str) -> str:
    """Entfernt Webseiten-Navigations-Zeilen aus roh kopiertem Paywall-Text.

    Fängt Müll wie „MEINE NACHRICHTENREUTLINGENNECKAR-ALBLANDWELT…" oder
    Menüleisten (E-Paper / Alle Themen / Mein Konto …), der sonst in den
    Briefing-Text durchrutscht. Konservativ — entfernt nur klare Navi-Zeilen,
    behält echten Artikeltext. Wird vor dem Handoff auf Paywall-Texte angewandt.
    """
    if not text:
        return text
    kept = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            kept.append(line)
            continue
        low = s.lower()
        # 1) ALLCAPS-Klumpen ohne Satzstruktur (z.B. MEINE NACHRICHTENREUTLINGEN…)
        letters = [c for c in s if c.isalpha()]
        if letters and len(s) > 20:
            caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
            if caps_ratio > 0.6 and s.count(" ") < 4:
                continue
        # 2) Kurze Zeile mit eindeutigem Navi-Marker
        if len(s) < 55 and any(m in low for m in _PAYWALL_NAV_MARKERS):
            continue
        # 3) Service-Listing „Begriff - Beschreibung" (kurz, kein Satz)
        if " - " in s and len(s) < 55 and s.count(".") == 0 and s.count(",") == 0:
            if any(m in low for m in _PAYWALL_NAV_MARKERS):
                continue
        kept.append(line)
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned if cleaned else text


def _looks_like_non_article_html(url: str, raw_html: str) -> bool:
    """Erkennt bekannte Consent-/Interstitital-Seiten statt echter Artikel."""
    lowered_url = (url or "").lower()
    lowered_html = raw_html.lower()

    google_consent_markers = (
        "<title>bevor sie fortfahren",
        "consent.google.com/save",
        "policies.google.com/technologies/cookies",
        "accounts.google.com/servicelogin",
        "google zu cookies und daten",
    )
    if ("news.google.com" in lowered_url or "google.com" in lowered_url) and any(
        marker in lowered_html for marker in google_consent_markers
    ):
        return True

    return False


# ============================================================
# ARTIKEL FETCHEN
# ============================================================

def clean_text(raw: str) -> str:
    """Entfernt NULL-Bytes und Steuerzeichen aus Text."""
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)


def fetch_article(url: str) -> Optional[str]:
    """Holt den Artikeltext einer URL mit dreistufigem Fallback."""
    try:
        response = _get_http_session().get(url, timeout=30)
        response.raise_for_status()
        raw_html = clean_text(response.text)
    except Exception as e:
        return None

    if _looks_like_non_article_html(url, raw_html):
        print(f"[fetch] Nicht-Artikel/Consent-Seite übersprungen: {url[:120]}",
              file=sys.stderr)
        return None

    # Versuch 1: trafilatura
    try:
        import trafilatura
        text = trafilatura.extract(raw_html, include_comments=False,
                                   include_tables=False, no_fallback=False)
        if text and len(text) > 200:
            return text
    except Exception:
        pass

    # Versuch 2: readability
    try:
        from readability import Document
        import html
        doc = Document(raw_html)
        clean = re.sub(r'<[^>]+>', ' ', doc.summary())
        clean = html.unescape(clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        if len(clean) > 200:
            return clean
    except Exception:
        pass

    # Versuch 3: Einfacher HTML-zu-Text Fallback
    try:
        import html
        clean = re.sub(r'<script[^>]*>.*?</script>', '', raw_html,
                       flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<style[^>]*>.*?</style>', '', clean,
                       flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<[^>]+>', ' ', clean)
        clean = html.unescape(clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        if len(clean) > 200:
            return clean
    except Exception:
        pass

    return None


# ============================================================
# API-AUFRUF (Anthropic + OpenAI)
# ============================================================

def _is_reasoning_model(model: str) -> bool:
    """Prüft, ob ein OpenAI-Modell ein Reasoning-Modell ist (GPT-5.x, nicht Instant)."""
    if not model.startswith("gpt-5"):
        return False
    # "chat-latest" / "instant" Varianten sind NICHT Reasoning
    return "chat" not in model and "instant" not in model


class APIQuotaExhaustedError(Exception):
    """Wird geworfen wenn die API explizit „Guthaben aufgebraucht" / „insufficient_quota" /
    „rate limit" / 401 unauthorized meldet. Soll vom Watchdog sofort behandelt werden,
    nicht als Hänger interpretiert werden."""
    def __init__(self, provider: str, message: str = ""):
        self.provider = provider
        super().__init__(f"{provider}: {message or 'Guthaben aufgebraucht oder Rate Limit'}")


def _is_quota_or_auth_error(exc: Exception) -> bool:
    """Erkennt typische Provider-Fehler die NICHT durch Retry behoben werden können:
    insufficient_quota, rate_limit, invalid_api_key, 401, 402, 429."""
    msg = str(exc).lower()
    if any(kw in msg for kw in (
        "insufficient_quota",
        "insufficient quota",
        "rate_limit",
        "rate limit",
        "invalid_api_key",
        "invalid api key",
        "authentication_error",
        "incorrect api key",
        "credit balance is too low",
    )):
        return True
    # HTTP-Status-Code-Hinweise
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in (401, 402, 429):
        return True
    return False


def _call_openai(client, model: str, prompt: str, text: str,
                 max_tokens: int = 16384,
                 json_mode: bool = False,
                 timeout_seconds: Optional[float] = None) -> Optional[str]:
    """Ruft die OpenAI-API auf: Responses API bevorzugt, Chat Completions als Fallback.
    
    Responses API ist besser für GPT-5.x (reasoning-aware, saubere Token-Limits).
    Falls die Library zu alt ist (kein client.responses), wird automatisch
    Chat Completions mit max_completion_tokens verwendet.
    """


    if timeout_seconds is None:
        timeout_seconds = DEFAULT_MODEL_TIMEOUT_SECONDS

    # --- Versuch 1: Responses API (empfohlen für GPT-5.x) ---
    if model.startswith("gpt-5") and hasattr(client, "responses"):
        try:
            kwargs = {
                "model": model,
                "instructions": prompt,
                "input": f"Hier ist der Text:\n\n{text}",
                "max_output_tokens": max_tokens,
                "timeout": timeout_seconds,
            }
            if json_mode:
                kwargs["text"] = {
                    "format": {"type": "json_object"},
                    "verbosity": "low",
                }
            if _is_reasoning_model(model):
                kwargs["reasoning"] = {"effort": "none"}
            response = client.responses.create(**kwargs)
            tracker = getattr(client, "_briefing_cost_tracker", None)
            if tracker:
                tracker.record_openai(model, getattr(response, "usage", None))
            return response.output_text
        except Exception as e:
            print(f"[openai] Responses API fehlgeschlagen ({model}): "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            if _is_quota_or_auth_error(e):
                raise APIQuotaExhaustedError("OpenAI", str(e)) from e
            print(f"[openai] Fallback auf Chat Completions...", file=sys.stderr)

    # --- Versuch 2 / Fallback: Chat Completions ---
    try:
        user_content = f"{prompt}\n\nHier ist der Text:\n\n{text}"
        kwargs = {
            "model": model,
            "max_completion_tokens": max_tokens,
            "messages": [{"role": "user", "content": user_content}],
            "timeout": timeout_seconds,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        message = client.chat.completions.create(
            **kwargs
        )
        tracker = getattr(client, "_briefing_cost_tracker", None)
        if tracker:
            tracker.record_openai(model, getattr(message, "usage", None))
        return message.choices[0].message.content
    except Exception as e:
        print(f"[openai] Chat Completions fehlgeschlagen ({model}): "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        if _is_quota_or_auth_error(e):
            raise APIQuotaExhaustedError("OpenAI", str(e)) from e

        # Bei Rate-Limit: warten und nochmal versuchen
        if "rate" in str(e).lower() or "429" in str(e):
            try:
                print(f"[openai] Rate-Limit, warte 15s...", file=sys.stderr)
                time.sleep(15)
                retry_kwargs = {
                    "model": model,
                    "max_completion_tokens": max_tokens,
                    "messages": [{"role": "user", "content": user_content}],
                    "timeout": timeout_seconds,
                }
                if json_mode:
                    retry_kwargs["response_format"] = {"type": "json_object"}
                message = client.chat.completions.create(**retry_kwargs)
                tracker = getattr(client, "_briefing_cost_tracker", None)
                if tracker:
                    tracker.record_openai(model, getattr(message, "usage", None))
                return message.choices[0].message.content
            except Exception as e2:
                print(f"[openai] Retry fehlgeschlagen: {e2}", file=sys.stderr)
                if _is_quota_or_auth_error(e2):
                    raise APIQuotaExhaustedError("OpenAI", str(e2)) from e2
        return None


def summarize(client, text: str, prompt: str,
              model: str = "claude-sonnet-5",
              max_tokens: int = 8192,
              json_mode: bool = False,
              timeout_seconds: Optional[float] = None) -> Optional[str]:
    """Fasst einen Text über die Anthropic- oder OpenAI-API zusammen.
    
    Erkennt anhand des Modellnamens automatisch, welche API genutzt wird:
    - claude-*  → Anthropic Messages API
    - gpt-*     → OpenAI (Responses API bevorzugt, Chat Completions als Fallback)
    """


    try:
        if timeout_seconds is None:
            timeout_seconds = DEFAULT_MODEL_TIMEOUT_SECONDS
        if len(text) > 100_000:
            text = text[:100_000] + "\n\n[Text gekürzt]"

        if model.startswith("gpt-"):
            return _call_openai(
                client,
                model,
                prompt,
                text,
                max_tokens=max_tokens,
                json_mode=json_mode,
                timeout_seconds=timeout_seconds,
            )
        else:
            # Anthropic API (default)
            user_content = f"{prompt}\n\nHier ist der Text:\n\n{text}"
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": user_content}],
                timeout=timeout_seconds,
            )
            tracker = getattr(client, "_briefing_cost_tracker", None)
            if tracker:
                tracker.record_anthropic(model, getattr(message, "usage", None))
            return message.content[0].text
    except APIQuotaExhaustedError:
        raise
    except Exception as e:
        # Quota / Auth-Fehler explizit als Exception weiterwerfen, damit der
        # Watchdog sofort zum anderen Provider switchen kann (statt zu hängen).
        if _is_quota_or_auth_error(e):
            provider = "OpenAI" if model.startswith("gpt-") else "Anthropic"
            raise APIQuotaExhaustedError(provider, str(e)) from e
        print(f"[summarize] FEHLER ({model}): {type(e).__name__}: {e}",
              file=sys.stderr)
        return None


# ============================================================
# WETTERBERICHT
# ============================================================

def _fetch_open_meteo_dataset(base_url: str,
                              current_vars: Optional[List[str]] = None,
                              hourly_vars: Optional[List[str]] = None,
                              daily_vars: Optional[List[str]] = None,
                              forecast_days: int = 4) -> dict:
    """Holt Wetterdaten von einem Open-Meteo-Endpunkt."""
    params = {
        "latitude": WETTER_LAT,
        "longitude": WETTER_LON,
        "timezone": WETTER_TIMEZONE,
        "forecast_days": forecast_days,
    }
    if current_vars:
        params["current"] = ",".join(current_vars)
    if hourly_vars:
        params["hourly"] = ",".join(hourly_vars)
    if daily_vars:
        params["daily"] = ",".join(daily_vars)

    resp = _get_http_session().get(base_url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _is_precipitation_code(code: Optional[int]) -> bool:
    return code in {
        51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
        71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99,
    }


def _hourly_model_signal(hourly: dict, idx: int) -> Optional[bool]:
    if idx >= len(hourly.get("time", [])):
        return None

    precip_prob = hourly.get("precipitation_probability", [])
    precip = hourly.get("precipitation", [])
    code = hourly.get("weather_code", [])

    precip_prob_value = precip_prob[idx] if idx < len(precip_prob) else None
    precip_value = precip[idx] if idx < len(precip) else None
    code_value = code[idx] if idx < len(code) else None

    if precip_prob_value is not None and precip_prob_value >= 55:
        return True
    if precip_value is not None and precip_value >= 0.2:
        return True
    if _is_precipitation_code(code_value):
        return True
    return False


def _daily_model_signal(daily: dict, idx: int) -> Optional[bool]:
    if idx >= len(daily.get("time", [])):
        return None

    precip_prob_max = daily.get("precipitation_probability_max", [])
    precip_sum = daily.get("precipitation_sum", [])
    code = daily.get("weather_code", [])

    precip_prob_value = precip_prob_max[idx] if idx < len(precip_prob_max) else None
    precip_sum_value = precip_sum[idx] if idx < len(precip_sum) else None
    code_value = code[idx] if idx < len(code) else None

    if precip_prob_value is not None and precip_prob_value >= 55:
        return True
    if precip_sum_value is not None and precip_sum_value >= 1.0:
        return True
    if _is_precipitation_code(code_value):
        return True
    return False


def _validation_label(primary_signal: Optional[bool],
                      secondary_signal: Optional[bool]) -> str:
    if secondary_signal is None:
        return "ohne Gegencheck"
    if primary_signal == secondary_signal:
        return "bestätigt"
    return "uneinheitlich"


def _hours_from_seconds(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value) / 3600.0, 1)


def _extract_dwd_regional_forecast_from_html(raw_html: str) -> Optional[str]:
    """Extrahiert den relevanten Baden-Württemberg-Text aus der DWD-Seite."""
    import html as html_lib

    if not raw_html:
        return None

    cleaned = re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<style[^>]*>.*?</style>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"</?(?:p|div|section|article|main|aside|nav|header|footer|h[1-6]|li|ul|ol|br|tr|td|th)[^>]*>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html_lib.unescape(clean_text(cleaned)).replace("\xa0", " ")

    lines = []
    for raw_line in cleaned.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" \t\r-")
        if line:
            lines.append(line)

    if not lines:
        return None

    start_idx = None
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if "vorhersage für" in lowered and "baden" in lowered and "württemberg" in lowered:
            start_idx = idx
            break

    if start_idx is None:
        return None

    stop_markers = (
        "Trend bis 10. Tag",
        "Mehr Wetterinfos",
        "Interessante Links",
        "Fachinformationen für",
        "Diese Seite teilen",
        "Service-Navigations- Menü",
        "Sub-Navigations- Menü",
        "DWD-Services",
    )

    collected = []
    for line in lines[start_idx + 1:]:
        if any(marker.lower() in line.lower() for marker in stop_markers):
            break
        if line in {"DownloadNasBild", "Zoom"}:
            continue
        if line.startswith("Image:"):
            continue
        collected.append(line)

    if not collected:
        return None

    deduped = []
    seen_recent = []
    for line in collected:
        if seen_recent and line == seen_recent[-1]:
            continue
        deduped.append(line)
        seen_recent.append(line)
        if len(seen_recent) > 3:
            seen_recent.pop(0)

    trimmed = []
    for line in deduped:
        trimmed.append(line)
        if len(trimmed) >= 18:
            break

    result = "\n".join(trimmed).strip()
    return result or None


def _fetch_dwd_regional_forecast() -> Optional[str]:
    """Holt den DWD-Landesbericht für Baden-Württemberg als Regional-Check."""
    urls = [
        "https://www.dwd.de/DE/wetter/vorhersage_aktuell/baden-wuerttemberg/vhs_bawue_node.html",
        "https://www.dwd.de/DE/wetter/vorhersage_aktuell/baden-wuerttemberg/vorhersage_bawue.html/1000",
    ]
    for url in urls:
        try:
            resp = _get_http_session().get(url, timeout=20)
            resp.raise_for_status()
            extracted = _extract_dwd_regional_forecast_from_html(resp.text)
            if extracted:
                return extracted
        except Exception:
            continue
    return None


def _daily_sky_hint(weather_code: Optional[int],
                    sunshine_hours: Optional[float],
                    precip_prob: Optional[float],
                    precip_sum: Optional[float]) -> str:
    if precip_prob is not None and precip_prob >= 55:
        return "eher unbeständig"
    if precip_sum is not None and precip_sum >= 1.0:
        return "mit Schauerrisiko"
    if sunshine_hours is not None:
        if sunshine_hours >= 8:
            return "viel Sonne"
        if sunshine_hours >= 5:
            return "freundlich"
        if sunshine_hours >= 2:
            return "teils wolkig"
    if weather_code == 0:
        return "klar bis sonnig"
    if weather_code in {1, 2}:
        return "freundlich"
    if weather_code == 3:
        return "eher wolkig"
    return "wechselhaft"


def _find_hour_index(hourly_times: List[str], now: datetime.datetime) -> int:
    current_iso = now.strftime("%Y-%m-%dT%H:00")
    for i, t in enumerate(hourly_times):
        if t >= current_iso:
            return i
    return 0


def _build_weather_text(primary: dict,
                        now: datetime.datetime,
                        secondary: Optional[dict] = None,
                        regional_text: Optional[str] = None,
                        primary_name: str = "DWD ICON",
                        secondary_name: str = "ECMWF IFS") -> str:
    """Baut den Rohtext für das Wetterbriefing mit Modell-Gegencheck."""
    current = primary.get("current", {})
    daily = primary.get("daily", {})
    hourly = primary.get("hourly", {})
    secondary_hourly = secondary.get("hourly", {}) if secondary else {}
    secondary_daily = secondary.get("daily", {}) if secondary else {}
    secondary_hour_index = {
        t: i for i, t in enumerate(secondary_hourly.get("time", []))
    } if secondary else {}
    secondary_day_index = {
        t: i for i, t in enumerate(secondary_daily.get("time", []))
    } if secondary else {}

    current_hour = now.strftime("%H:%M")
    weather_text = f"Stand: {current_hour} Uhr.\n"
    weather_text += f"Primärmodell: {primary_name}.\n"
    if secondary:
        weather_text += f"Gegencheck: {secondary_name}.\n"
    weather_text += (
        f"Aktuell in Tübingen-Hirschau: {current.get('temperature_2m', '?')}°C "
        f"(gefühlt {current.get('apparent_temperature', '?')}°C), "
        f"Luftfeuchtigkeit {current.get('relative_humidity_2m', '?')}%, "
        f"Wind {current.get('wind_speed_10m', '?')} km/h, "
        f"Bewölkung {current.get('cloud_cover', '?')}%, "
        f"{WMO_CODES.get(current.get('weather_code', -1), 'Unbekannt')}.\n\n"
    )

    if regional_text:
        weather_text += "Regionaler DWD-Check Baden-Württemberg:\n"
        weather_text += f"{regional_text}\n\n"

    weather_text += "Stündliche Vorhersage:\n"
    hourly_times = hourly.get("time", [])
    start_idx = _find_hour_index(hourly_times, now) if hourly_times else 0

    if hourly_times:
        start_label = hourly_times[start_idx][-5:]
        weather_text += f"Ab {start_label} Uhr:\n"
    else:
        weather_text += "Ab jetzt:\n"

    for i in range(start_idx, min(start_idx + 5, len(hourly_times))):
        time_label = hourly_times[i]
        hour_label = time_label[-5:]
        temp = hourly.get("temperature_2m", [None])[i]
        precip_prob = hourly.get("precipitation_probability", [None])[i]
        code = hourly.get("weather_code", [None])[i]
        wind = hourly.get("wind_speed_10m", [None])[i]
        cloud_cover = hourly.get("cloud_cover", [None])[i]
        sunshine_hours = _hours_from_seconds(hourly.get("sunshine_duration", [None])[i])
        desc = WMO_CODES.get(code, "?") if code is not None else "?"

        validation = ""
        if secondary and time_label in secondary_hour_index:
            secondary_idx = secondary_hour_index[time_label]
            secondary_signal = _hourly_model_signal(secondary_hourly, secondary_idx)
            primary_signal = _hourly_model_signal(hourly, i)
            validation = f", Modellabgleich { _validation_label(primary_signal, secondary_signal) }"

        weather_text += (
            f"  {hour_label}: {temp}°C, {desc}, "
            f"Bewölkung {cloud_cover}%, "
            f"Regenwahrscheinlichkeit {precip_prob}%, "
            f"Sonne {sunshine_hours} h, Wind {wind} km/h{validation}.\n"
        )

    weather_text += "\nTagesübersicht:\n"
    for i in range(min(3, len(daily.get("time", [])))):
        date = daily["time"][i]
        sunshine_hours = _hours_from_seconds(daily.get("sunshine_duration", [None])[i])
        precip_prob = daily.get("precipitation_probability_max", [None])[i]
        precip_sum = daily.get("precipitation_sum", [None])[i]
        sky_hint = _daily_sky_hint(
            daily.get("weather_code", [None])[i],
            sunshine_hours,
            precip_prob,
            precip_sum,
        )
        validation = ""
        if secondary and date in secondary_day_index:
            secondary_idx = secondary_day_index[date]
            secondary_signal = _daily_model_signal(secondary_daily, secondary_idx)
            primary_signal = _daily_model_signal(daily, i)
            validation = f", Modellabgleich { _validation_label(primary_signal, secondary_signal) }"

        weather_text += f"Tag {date}: "
        weather_text += f"Min {daily['temperature_2m_min'][i]}°C, Max {daily['temperature_2m_max'][i]}°C, "
        weather_text += f"{WMO_CODES.get(daily['weather_code'][i], 'Unbekannt')}, "
        weather_text += f"Sonnenstunden {sunshine_hours} h, Tendenz {sky_hint}, "
        weather_text += f"Niederschlag {daily['precipitation_sum'][i]} mm "
        weather_text += f"(Wahrscheinlichkeit {daily['precipitation_probability_max'][i]}%), "
        weather_text += f"Wind bis {daily['wind_speed_10m_max'][i]} km/h, "
        weather_text += f"UV-Index {daily['uv_index_max'][i]}, "
        weather_text += f"Sonnenaufgang {daily['sunrise'][i][-5:]}, "
        weather_text += f"Sonnenuntergang {daily['sunset'][i][-5:]}{validation}.\n"

    return weather_text

def _condition_to_wmo(condition: Optional[str], cloud_cover: Optional[float] = None) -> int:
    """Mappt Bright-Sky-Conditions auf WMO-Codes (für _build_weather_text Kompatibilität)."""
    if not condition:
        if cloud_cover is None:
            return 0
        if cloud_cover < 25:
            return 0  # klar
        if cloud_cover < 50:
            return 1  # heiter
        if cloud_cover < 87:
            return 2  # bewölkt
        return 3  # bedeckt
    cond = condition.lower()
    mapping = {
        "dry": 1, "fog": 45, "rain": 61, "sleet": 67, "snow": 71,
        "hail": 77, "thunderstorm": 95,
    }
    return mapping.get(cond, 1)


def _fetch_brightsky_dataset() -> Optional[dict]:
    """Holt Wetterdaten von Bright Sky (DWD-Daten direkt, kein API-Key, sehr zuverlässig).

    Bright Sky liefert keine Niederschlagswahrscheinlichkeit pro Stunde — wir leiten
    sie aus dem 'precipitation'-Wert ab (>0mm = 80%, sonst 10%).
    """
    try:
        session = _get_http_session()
        now = get_berlin_now()
        today = now.strftime("%Y-%m-%d")
        end_date = (now + datetime.timedelta(days=4)).strftime("%Y-%m-%d")

        # Aktuelle Bedingungen
        cur_resp = session.get(
            "https://api.brightsky.dev/current_weather",
            params={"lat": WETTER_LAT, "lon": WETTER_LON, "tz": "Europe/Berlin"},
            timeout=15,
        )
        cur_resp.raise_for_status()
        cur_data = cur_resp.json()
        cur_w = cur_data.get("weather", {})

        # Stündliche Vorhersage
        fc_resp = session.get(
            "https://api.brightsky.dev/weather",
            params={
                "lat": WETTER_LAT, "lon": WETTER_LON,
                "date": today, "last_date": end_date, "tz": "Europe/Berlin",
            },
            timeout=15,
        )
        fc_resp.raise_for_status()
        fc_data = fc_resp.json()
        hours = fc_data.get("weather", [])

        if not hours:
            return None

        # Hourly-Struktur aufbauen (Open-Meteo-kompatibel)
        hourly = {
            "time": [],
            "temperature_2m": [],
            "precipitation_probability": [],
            "weather_code": [],
            "wind_speed_10m": [],
            "cloud_cover": [],
            "sunshine_duration": [],
        }
        for h in hours:
            ts = h.get("timestamp", "")[:16]  # "2026-04-07T00:00"
            hourly["time"].append(ts)
            hourly["temperature_2m"].append(h.get("temperature"))
            precip = h.get("precipitation") or 0
            hourly["precipitation_probability"].append(80 if precip > 0.1 else 10)
            hourly["weather_code"].append(_condition_to_wmo(h.get("condition"), h.get("cloud_cover")))
            hourly["wind_speed_10m"].append(h.get("wind_speed"))
            hourly["cloud_cover"].append(h.get("cloud_cover"))
            sun = h.get("sunshine") or 0
            hourly["sunshine_duration"].append(sun * 60)  # min → sek

        # Daily aus Hourly aggregieren (3-4 Tage)
        from collections import defaultdict
        by_day = defaultdict(list)
        for i, ts in enumerate(hourly["time"]):
            day = ts[:10]
            by_day[day].append(i)

        daily = {
            "time": [],
            "weather_code": [],
            "temperature_2m_max": [],
            "temperature_2m_min": [],
            "precipitation_sum": [],
            "precipitation_probability_max": [],
            "wind_speed_10m_max": [],
            "uv_index_max": [],
            "sunrise": [],
            "sunset": [],
            "sunshine_duration": [],
        }
        for day, indices in sorted(by_day.items())[:4]:
            temps = [hourly["temperature_2m"][i] for i in indices if hourly["temperature_2m"][i] is not None]
            winds = [hourly["wind_speed_10m"][i] for i in indices if hourly["wind_speed_10m"][i] is not None]
            precs = [hours[i].get("precipitation") or 0 for i in indices]
            sun_total = sum((hours[i].get("sunshine") or 0) for i in indices)
            codes = [hourly["weather_code"][i] for i in indices]
            # Häufigster Code des Tages (gewichtet zu schlechterem Wetter)
            day_code = max(set(codes), key=codes.count) if codes else 1

            daily["time"].append(day)
            daily["weather_code"].append(day_code)
            daily["temperature_2m_max"].append(round(max(temps), 1) if temps else None)
            daily["temperature_2m_min"].append(round(min(temps), 1) if temps else None)
            daily["precipitation_sum"].append(round(sum(precs), 1))
            daily["precipitation_probability_max"].append(80 if sum(precs) > 0.5 else 10)
            daily["wind_speed_10m_max"].append(round(max(winds), 1) if winds else None)
            daily["uv_index_max"].append(None)
            daily["sunrise"].append(f"{day}T06:30")  # Platzhalter
            daily["sunset"].append(f"{day}T20:00")
            daily["sunshine_duration"].append(sun_total * 60)

        # Current
        current = {
            "temperature_2m": cur_w.get("temperature"),
            "apparent_temperature": cur_w.get("temperature"),  # Bright Sky liefert keinen "feels like"
            "relative_humidity_2m": cur_w.get("relative_humidity"),
            "wind_speed_10m": cur_w.get("wind_speed_30") or cur_w.get("wind_speed_60"),
            "cloud_cover": cur_w.get("cloud_cover"),
            "weather_code": _condition_to_wmo(cur_w.get("condition"), cur_w.get("cloud_cover")),
        }

        return {"current": current, "hourly": hourly, "daily": daily}
    except Exception as exc:
        print(f"[weather] Bright Sky fehlgeschlagen: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def _fetch_met_norway_dataset() -> Optional[dict]:
    """Holt Wetterdaten von MET Norway (global, frei, kein Key, sehr zuverlässig).

    MET Norway erfordert User-Agent. Daten kommen als 'timeseries' im ISO-Format.
    """
    try:
        session = _get_http_session()
        resp = session.get(
            "https://api.met.no/weatherapi/locationforecast/2.0/compact",
            params={"lat": WETTER_LAT, "lon": WETTER_LON},
            headers={"User-Agent": "AudioBriefing/1.0 (florian.s.thiel@gmail.com)"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        ts_list = data.get("properties", {}).get("timeseries", [])
        if not ts_list:
            return None

        now = get_berlin_now()
        # Filter: nur die nächsten ~4 Tage
        end = now + datetime.timedelta(days=4)

        hourly = {
            "time": [],
            "temperature_2m": [],
            "precipitation_probability": [],
            "weather_code": [],
            "wind_speed_10m": [],
            "cloud_cover": [],
            "sunshine_duration": [],
        }

        for entry in ts_list:
            time_str = entry.get("time", "")
            try:
                t = datetime.datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except Exception:
                continue
            if t > end:
                break
            local = t.astimezone(now.tzinfo)
            instant = entry.get("data", {}).get("instant", {}).get("details", {})
            next_1h = entry.get("data", {}).get("next_1_hours", {})
            symbol = next_1h.get("summary", {}).get("symbol_code", "")
            precip = next_1h.get("details", {}).get("precipitation_amount", 0) or 0

            hourly["time"].append(local.strftime("%Y-%m-%dT%H:%M"))
            hourly["temperature_2m"].append(instant.get("air_temperature"))
            hourly["precipitation_probability"].append(80 if precip > 0.1 else 10)
            # WMO grobe Mapping
            code = 1
            if "rain" in symbol or "shower" in symbol: code = 61
            elif "snow" in symbol: code = 71
            elif "fog" in symbol: code = 45
            elif "thunder" in symbol: code = 95
            elif "cloudy" in symbol: code = 3
            elif "partlycloudy" in symbol: code = 2
            elif "fair" in symbol: code = 1
            elif "clear" in symbol: code = 0
            hourly["weather_code"].append(code)
            hourly["wind_speed_10m"].append(round((instant.get("wind_speed") or 0) * 3.6, 1))  # m/s → km/h
            hourly["cloud_cover"].append(instant.get("cloud_area_fraction"))
            hourly["sunshine_duration"].append(0)

        if not hourly["time"]:
            return None

        # Daily aggregieren
        from collections import defaultdict
        by_day = defaultdict(list)
        for i, ts in enumerate(hourly["time"]):
            by_day[ts[:10]].append(i)

        daily = {
            "time": [], "weather_code": [], "temperature_2m_max": [],
            "temperature_2m_min": [], "precipitation_sum": [],
            "precipitation_probability_max": [], "wind_speed_10m_max": [],
            "uv_index_max": [], "sunrise": [], "sunset": [], "sunshine_duration": [],
        }
        for day, indices in sorted(by_day.items())[:4]:
            temps = [hourly["temperature_2m"][i] for i in indices if hourly["temperature_2m"][i] is not None]
            winds = [hourly["wind_speed_10m"][i] for i in indices if hourly["wind_speed_10m"][i] is not None]
            codes = [hourly["weather_code"][i] for i in indices]
            day_code = max(set(codes), key=codes.count) if codes else 1
            daily["time"].append(day)
            daily["weather_code"].append(day_code)
            daily["temperature_2m_max"].append(round(max(temps), 1) if temps else None)
            daily["temperature_2m_min"].append(round(min(temps), 1) if temps else None)
            daily["precipitation_sum"].append(0)
            daily["precipitation_probability_max"].append(0)
            daily["wind_speed_10m_max"].append(round(max(winds), 1) if winds else None)
            daily["uv_index_max"].append(None)
            daily["sunrise"].append(f"{day}T06:30")
            daily["sunset"].append(f"{day}T20:00")
            daily["sunshine_duration"].append(0)

        # Current = erster Eintrag
        first = ts_list[0].get("data", {}).get("instant", {}).get("details", {})
        current = {
            "temperature_2m": first.get("air_temperature"),
            "apparent_temperature": first.get("air_temperature"),
            "relative_humidity_2m": first.get("relative_humidity"),
            "wind_speed_10m": round((first.get("wind_speed") or 0) * 3.6, 1),
            "cloud_cover": first.get("cloud_area_fraction"),
            "weather_code": hourly["weather_code"][0] if hourly["weather_code"] else 1,
        }

        return {"current": current, "hourly": hourly, "daily": daily}
    except Exception as exc:
        print(f"[weather] MET Norway fehlgeschlagen: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def fetch_weather() -> Optional[str]:
    """Holt Wetterdaten mit mehrstufiger Fallback-Kette:
    1. Open-Meteo DWD-ICON (Hauptquelle)
    2. Open-Meteo ECMWF (Gegencheck)
    3. Open-Meteo Forecast (Fallback)
    4. Bright Sky (DWD direkt — best fallback für DE)
    5. MET Norway (global)
    """
    try:
        now = get_berlin_now()
        regional_text = None
        try:
            regional_text = _fetch_dwd_regional_forecast()
        except Exception:
            regional_text = None

        primary = _fetch_open_meteo_dataset(
            "https://api.open-meteo.com/v1/dwd-icon",
            current_vars=[
                "temperature_2m", "relative_humidity_2m", "weather_code",
                "wind_speed_10m", "apparent_temperature", "cloud_cover",
            ],
            hourly_vars=[
                "temperature_2m", "precipitation_probability",
                "weather_code", "wind_speed_10m", "cloud_cover",
                "sunshine_duration",
            ],
            daily_vars=[
                "weather_code", "temperature_2m_max", "temperature_2m_min",
                "precipitation_sum", "precipitation_probability_max",
                "wind_speed_10m_max", "uv_index_max", "sunrise", "sunset",
                "sunshine_duration",
            ],
        )

        secondary = None
        try:
            secondary = _fetch_open_meteo_dataset(
                "https://api.open-meteo.com/v1/ecmwf",
                hourly_vars=[
                    "temperature_2m", "precipitation", "weather_code",
                    "wind_speed_10m", "cloud_cover", "sunshine_duration",
                ],
                daily_vars=[
                    "weather_code", "temperature_2m_max", "temperature_2m_min",
                    "precipitation_sum", "wind_speed_10m_max",
                ],
            )
        except Exception:
            secondary = None

        return _build_weather_text(
            primary,
            now=now,
            secondary=secondary,
            regional_text=regional_text,
            primary_name="DWD ICON",
            secondary_name="ECMWF IFS",
        )
    except Exception:
        # Open-Meteo komplett down — Fallback-Kette starten
        regional_text = None
        try:
            regional_text = _fetch_dwd_regional_forecast()
        except Exception:
            regional_text = None
        now = get_berlin_now()

        # Fallback 1: Open-Meteo Forecast (gleicher Anbieter, anderer Endpoint)
        try:
            fallback = _fetch_open_meteo_dataset(
                "https://api.open-meteo.com/v1/forecast",
                current_vars=[
                    "temperature_2m", "relative_humidity_2m", "weather_code",
                    "wind_speed_10m", "apparent_temperature", "cloud_cover",
                ],
                hourly_vars=[
                    "temperature_2m", "precipitation_probability",
                    "weather_code", "wind_speed_10m", "cloud_cover",
                    "sunshine_duration",
                ],
                daily_vars=[
                    "weather_code", "temperature_2m_max", "temperature_2m_min",
                    "precipitation_sum", "precipitation_probability_max",
                    "wind_speed_10m_max", "uv_index_max", "sunrise", "sunset",
                    "sunshine_duration",
                ],
            )
            return _build_weather_text(
                fallback, now=now, secondary=None, regional_text=regional_text,
                primary_name="Open-Meteo Forecast",
            )
        except Exception as exc:
            print(f"[weather] Open-Meteo Forecast fehlgeschlagen: {exc}", file=sys.stderr)

        # Fallback 2: Bright Sky (DWD direkt — beste Quelle für Süddeutschland)
        brightsky = _fetch_brightsky_dataset()
        if brightsky:
            print("[weather] Bright Sky als Fallback verwendet", file=sys.stderr)
            return _build_weather_text(
                brightsky, now=now, secondary=None, regional_text=regional_text,
                primary_name="Bright Sky (DWD direkt)",
            )

        # Fallback 3: MET Norway (global, sehr zuverlässig)
        met = _fetch_met_norway_dataset()
        if met:
            print("[weather] MET Norway als Fallback verwendet", file=sys.stderr)
            return _build_weather_text(
                met, now=now, secondary=None, regional_text=regional_text,
                primary_name="MET Norway",
            )

        # Letzter Strohhalm: nur regionalen DWD-Text liefern
        if regional_text:
            print("[weather] Nur DWD-Regionaltext verfügbar", file=sys.stderr)
            return f"Stand: {now.strftime('%H:%M')} Uhr.\nQuelle: DWD-Regionaltext (alle anderen Wetterquellen nicht erreichbar).\n\n{regional_text}\n"

        return None


# ============================================================
# PDF-ERZEUGUNG
# ============================================================

def register_fonts():
    """Registriert Schriften für die PDF-Erzeugung.
    
    Sucht zuerst im fonts/-Ordner neben diesem Script (= mitgelieferte Fonts),
    dann in System-Pfaden. Falls nirgends gefunden, werden DejaVu-Fonts
    automatisch heruntergeladen.
    
    Eingebettete TrueType-Fonts sind KRITISCH für TTS, weil sie
    Unicode-cmap-Tabellen enthalten, die ElevenReader braucht.
    Helvetica (Type 1) hat das NICHT und verursacht Sprachenwechsel/Wiederholungen.
    """

    
    # Pfad relativ zum Script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    bundled_dir = os.path.join(script_dir, "fonts")

    # Suchpfade: Bundled zuerst, dann System (Linux, macOS Homebrew, macOS System)
    search_dirs = [
        bundled_dir,
        "/usr/share/fonts/truetype/dejavu",
        "/opt/homebrew/share/fonts/dejavu",
        "/System/Library/Fonts/Supplemental",
        "/Library/Fonts",
    ]

    font_files = {
        "Regular": ["DejaVuSans.ttf", "BundledFont-Regular.ttf"],
        "Bold": ["DejaVuSans-Bold.ttf", "BundledFont-Bold.ttf"],
        "Italic": ["DejaVuSans-Oblique.ttf", "BundledFont-Italic.ttf"],
        "BoldItalic": ["DejaVuSans-BoldOblique.ttf", "BundledFont-BoldItalic.ttf"],
    }

    found = {}
    for style, filenames in font_files.items():
        for search_dir in search_dirs:
            for filename in filenames:
                path = os.path.join(search_dir, filename)
                if os.path.exists(path):
                    found[style] = path
                    break
            if style in found:
                break

    # Falls nicht alle 4 Styles gefunden → automatisch herunterladen
    if len(found) < 4:
        print("[fonts] DejaVu nicht gefunden, lade herunter...", file=sys.stderr)
        found = _download_dejavu_fonts(bundled_dir)

    if len(found) >= 4:
        names = {
            "Regular": "DejaVu",
            "Bold": "DejaVu-Bold",
            "Italic": "DejaVu-Italic",
            "BoldItalic": "DejaVu-BoldItalic",
        }
        for style, path in found.items():
            pdfmetrics.registerFont(TTFont(names[style], path))

        from reportlab.pdfbase.pdfmetrics import registerFontFamily
        registerFontFamily(
            "DejaVu",
            normal="DejaVu",
            bold="DejaVu-Bold",
            italic="DejaVu-Italic",
            boldItalic="DejaVu-BoldItalic"
        )
        print(f"[fonts] DejaVu registriert (TTS-kompatibel)", file=sys.stderr)
        return "DejaVu"

    # Absoluter Fallback: Helvetica (NICHT empfohlen für TTS!)
    print("[fonts] WARNUNG: Helvetica-Fallback! TTS wird Probleme haben.", file=sys.stderr)
    return "Helvetica"


def _download_dejavu_fonts(target_dir: str) -> dict:
    """Lädt DejaVu-Fonts von GitHub herunter und speichert sie im fonts/-Ordner."""

    import zipfile
    import io

    os.makedirs(target_dir, exist_ok=True)
    
    url = "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.zip"
    
    mapping = {
        "DejaVuSans.ttf": "Regular",
        "DejaVuSans-Bold.ttf": "Bold",
        "DejaVuSans-Oblique.ttf": "Italic",
        "DejaVuSans-BoldOblique.ttf": "BoldItalic",
    }
    
    found = {}
    try:
        print(f"[fonts] Lade DejaVu von {url}...", file=sys.stderr)
        resp = _get_http_session().get(url, timeout=30)
        resp.raise_for_status()
        
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            for name in zf.namelist():
                basename = os.path.basename(name)
                if basename in mapping:
                    target_path = os.path.join(target_dir, basename)
                    with open(target_path, "wb") as f:
                        f.write(zf.read(name))
                    found[mapping[basename]] = target_path
                    print(f"[fonts]   {basename} → {target_path}", file=sys.stderr)
        
        print(f"[fonts] {len(found)}/4 Fonts heruntergeladen.", file=sys.stderr)
    except Exception as e:
        print(f"[fonts] Download fehlgeschlagen: {e}", file=sys.stderr)
    
    return found


def build_styles(font_name: str) -> dict:
    """Erstellt die Paragraph-Styles für die PDF."""
    base_size = 11.2
    line_height = 16.6

    styles = {
        "eyebrow": ParagraphStyle(
            "BriefingEyebrow",
            fontName=f"{font_name}-Bold" if font_name != "Helvetica" else "Helvetica-Bold",
            fontSize=8.5, leading=11,
            spaceAfter=2.5 * mm,
            textColor=HexColor("#12823b"), alignment=TA_LEFT,
        ),
        "title": ParagraphStyle(
            "BriefingTitle",
            fontName=f"{font_name}-Bold" if font_name != "Helvetica" else "Helvetica-Bold",
            fontSize=24, leading=30, spaceAfter=3.5 * mm,
            textColor=HexColor("#0c1110"), alignment=TA_LEFT,
        ),
        "h3": ParagraphStyle(
            "Heading3",
            fontName=f"{font_name}-Bold" if font_name != "Helvetica" else "Helvetica-Bold",
            fontSize=15, leading=20, spaceBefore=4.2 * mm, spaceAfter=2.2 * mm,
            textColor=HexColor("#0f5132"), alignment=TA_LEFT,
        ),
        "h4": ParagraphStyle(
            "Heading4",
            fontName=f"{font_name}-Bold" if font_name != "Helvetica" else "Helvetica-Bold",
            fontSize=11.4, leading=16, spaceBefore=3 * mm, spaceAfter=1.5 * mm,
            textColor=HexColor("#157347"), alignment=TA_LEFT,
        ),
        "body": ParagraphStyle(
            "Body", fontName=font_name,
            fontSize=base_size, leading=line_height, spaceAfter=2.5 * mm,
            textColor=HexColor("#101614"), alignment=TA_LEFT,
        ),
        "italic": ParagraphStyle(
            "Italic",
            fontName=f"{font_name}-Italic" if font_name != "Helvetica" else "Helvetica-Oblique",
            fontSize=10.6, leading=15.4, spaceAfter=2.5 * mm,
            textColor=HexColor("#4f6358"), alignment=TA_LEFT,
        ),
        "transition": ParagraphStyle(
            "Transition",
            fontName=f"{font_name}-Bold" if font_name != "Helvetica" else "Helvetica-Bold",
            fontSize=15.5, leading=22, spaceBefore=10 * mm, spaceAfter=6 * mm,
            textColor=HexColor("#12823b"), alignment=TA_LEFT,
        ),
        "date": ParagraphStyle(
            "Date", fontName=font_name,
            fontSize=9.4, leading=13, spaceAfter=4.5 * mm,
            textColor=HexColor("#61756b"), alignment=TA_LEFT,
        ),
    }
    return styles


def normalize_unicode(text: str) -> str:
    """Normalisiert problematische Unicode-Zeichen für TTS-kompatible PDFs.
    
    GPT-5.x gibt gelegentlich Unicode-Bindestriche, -Anführungszeichen und
    andere Sonderzeichen aus, die DejaVu/ReportLab als ■ rendern und
    ElevenReader als Artefakte vorliest.
    """
    # Unicode-Bindestriche → ASCII-Bindestrich
    text = text.replace('\u2010', '-')   # HYPHEN
    text = text.replace('\u2011', '-')   # NON-BREAKING HYPHEN
    text = text.replace('\u2012', '-')   # FIGURE DASH
    text = text.replace('\u2013', '-')   # EN DASH (–)
    text = text.replace('\u2014', ' - ') # EM DASH (—) → mit Leerzeichen
    text = text.replace('\u2015', ' - ') # HORIZONTAL BAR
    text = text.replace('\u00AD', '')    # SOFT HYPHEN (unsichtbar)
    text = text.replace('\uFFFD', '-')   # REPLACEMENT CHARACTER (�)
    # Unicode-Anführungszeichen → korrekte deutsche Typografie
    # GPT-5.x nutzt oft „ (U+201E) auf BEIDEN Seiten: „Wort„ statt „Wort"
    # Schritt 1: Englische Quotes normalisieren
    text = text.replace('\u201C', '\u201E')  # LEFT DOUBLE QUOTATION → „ (öffnend)
    text = text.replace('\u201D', '\u201C')  # RIGHT DOUBLE QUOTATION → " (schließend)
    # Schritt 2: Doppelte „...„ fixen → „..." (zweites „ wird zum schließenden ")
    text = re.sub(
        r'\u201E([^\u201E\u201C]{1,500}?)\u201E',
        lambda m: '\u201E' + m.group(1) + '\u201C',
        text
    )
    text = text.replace('\u2018', '\u201A')  # LEFT SINGLE QUOTATION → ‚
    text = text.replace('\u2019', "'")       # RIGHT SINGLE QUOTATION → Apostroph
    # Sonstige Problemzeichen
    text = text.replace('\u2026', '...')  # HORIZONTAL ELLIPSIS
    text = text.replace('\u2022', '-')    # BULLET
    text = text.replace('\u2023', '-')    # TRIANGULAR BULLET
    text = text.replace('\u2043', '-')    # HYPHEN BULLET
    text = text.replace('\u2219', '-')    # BULLET OPERATOR
    text = text.replace('\u00A0', ' ')   # NON-BREAKING SPACE
    text = text.replace('\u202F', ' ')   # NARROW NO-BREAK SPACE
    text = text.replace('\u200B', '')    # ZERO-WIDTH SPACE
    text = text.replace('\u200C', '')    # ZERO-WIDTH NON-JOINER
    text = text.replace('\u200D', '')    # ZERO-WIDTH JOINER
    text = text.replace('\u2060', '')    # WORD JOINER
    text = text.replace('\u25A0', '-')   # BLACK SQUARE (■) → Fallback
    return text


def tts_safe(text: str) -> str:
    """Macht Text TTS-sicher: Abkürzungen ausschreiben, Sonderzeichen normalisieren.
    
    Läuft als LETZTER Schritt vor der PDF-Erzeugung auf ALLEN Sections.
    Greift nur bei eindeutigen Patterns — kein aggressives Rewriting.
    """
    # --- Einheiten mit Slash → ausgeschrieben ---
    # Erst Varianten mit Zahl, dann losgelöste Einheiten (z.B. "Wind in km/h")
    text = re.sub(r'(\d+)\s*km/h\b', r'\1 Kilometer pro Stunde', text)
    text = re.sub(r'(\d+)\s*m/s\b', r'\1 Meter pro Sekunde', text)
    text = re.sub(r'(\d+)\s*kWh\b', r'\1 Kilowattstunden', text)
    text = re.sub(r'(\d+)\s*MW\b', r'\1 Megawatt', text)
    text = re.sub(r'\bkm/h\b', 'Kilometer pro Stunde', text)
    text = re.sub(r'\bm/s\b', 'Meter pro Sekunde', text)
    text = re.sub(r'(?<!\w)zum Bsp\.', 'zum Beispiel', text)
    text = re.sub(r'(?<!\w)z\.B\.', 'zum Beispiel', text)
    text = re.sub(r'(?<!\w)z\. B\.', 'zum Beispiel', text)
    text = re.sub(r'(?<!\w)d\.h\.', 'das heißt', text)
    text = re.sub(r'(?<!\w)d\. h\.', 'das heißt', text)
    text = re.sub(r'(?<!\w)u\.a\.', 'unter anderem', text)
    text = re.sub(r'(?<!\w)u\. a\.', 'unter anderem', text)
    text = re.sub(r'(?<!\w)bzw\.', 'beziehungsweise', text)
    text = re.sub(r'(?<!\w)ca\.', 'circa', text)
    text = re.sub(r'(?<!\w)evtl\.', 'eventuell', text)
    text = re.sub(r'(?<!\w)ggf\.', 'gegebenenfalls', text)
    text = re.sub(r'(?<!\w)i\.d\.R\.', 'in der Regel', text)
    text = re.sub(r'(?<!\w)Mio\.', 'Millionen', text)
    text = re.sub(r'(?<!\w)Mrd\.', 'Milliarden', text)
    
    # --- Prozentzeichen → ausgeschrieben ---
    text = re.sub(r'(\d+)\s*%', r'\1 Prozent', text)
    
    # --- Grad-Zeichen → ausgeschrieben ---
    text = re.sub(r'(\d+)\s*°C\b', r'\1 Grad Celsius', text)
    text = re.sub(r'(\d+)\s*°\b', r'\1 Grad', text)
    
    # --- Euro-Zeichen → ausgeschrieben ---
    text = re.sub(r'(\d[\d.,]*)\s*€', r'\1 Euro', text)
    text = re.sub(r'€\s*(\d[\d.,]*)', r'\1 Euro', text)
    
    # --- Schrägstriche in Komposita → Bindestrich (TTS liest "/" als Pause) ---
    # Nur bei Wort/Wort-Mustern, nicht bei URLs
    text = re.sub(r'(?<=[a-zäöü])\/(?=[A-ZÄÖÜ])', '-', text)
    
    return text


def escape_xml(text: str) -> str:
    """Escaped XML-Sonderzeichen für ReportLab Paragraphs."""
    text = normalize_unicode(text)
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    return text


def _content_with_source_label(section: dict) -> str:
    """Ergänzt bei Artikeln optional eine kurze Quellenzeile vor dem Inhalt."""
    label = section.get("source_label")
    if (
        not label
        or label == "Quelle unbekannt"
        or section.get("_weather")
        or section.get("_recap")
        or section.get("type") not in {"article", "manual"}
    ):
        return section["content"]
    return f"{normalize_unicode(label)}\n\n{section['content']}"


def _content_with_source_label_pdf(section: dict) -> str:
    """Wie _content_with_source_label, aber mit EYEBROW-Marker für PDF-Rendering."""
    label = section.get("source_label")
    if (
        not label
        or label == "Quelle unbekannt"
        or section.get("_weather")
        or section.get("_recap")
        or section.get("type") not in {"article", "manual"}
    ):
        return section["content"]
    return f"EYEBROW:{normalize_unicode(label)}\n\n{section['content']}"


def _join_plain_paragraphs(lines: List[str]) -> List[str]:
    paragraphs: List[str] = []
    current: List[str] = []
    for raw_line in lines:
        line = normalize_unicode(raw_line).strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current).strip())
    return [paragraph for paragraph in paragraphs if paragraph]


def _normalize_existing_briefing_markdown(text: str) -> str:
    """Bringt bereits fertige Briefings ohne Markdown-Markierungen ins Standardformat."""
    text = _dedup_header(normalize_unicode(text).strip())
    if not text:
        return text
    if text.startswith("### "):
        return text

    lines = text.splitlines()
    nonempty = [line.strip() for line in lines if line.strip()]
    if len(nonempty) < 3:
        return text

    title = re.sub(r"^#{1,4}\s*", "", nonempty[0]).strip()
    intro = nonempty[1].strip("* ").strip()

    marker_idx = None
    for idx, raw_line in enumerate(lines):
        if re.match(r"^#{0,4}\s*Was bleibt:?\s*$", raw_line.strip(), flags=re.IGNORECASE):
            marker_idx = idx
            break
    if marker_idx is None:
        return text

    before = lines[2:marker_idx]
    after = lines[marker_idx + 1:]
    end_marker_idx = None
    for idx, raw_line in enumerate(after):
        stripped = raw_line.strip()
        if _contains_regular_end_marker(stripped):
            end_marker_idx = idx
            break
        if _contains_briefing_end_marker(stripped) or _contains_podcast_end_marker(stripped):
            end_marker_idx = idx
            break
    if end_marker_idx is not None:
        was_bleibt_lines = after[:end_marker_idx]
        end_marker = after[end_marker_idx].strip()
    else:
        was_bleibt_lines = after
        end_marker = "Weiter geht's."

    body_paragraphs = _join_plain_paragraphs(before)
    was_bleibt_paragraphs = _join_plain_paragraphs(was_bleibt_lines)
    if not body_paragraphs or not was_bleibt_paragraphs:
        return text

    out: List[str] = [f"### {title}", "", f"*{intro}*", ""]
    for paragraph in body_paragraphs:
        out.extend([paragraph, ""])
    out.append("#### Was bleibt:")
    out.append("")
    for paragraph in was_bleibt_paragraphs:
        out.extend([paragraph, ""])
    out.append("Weiter geht's." if _contains_regular_end_marker(end_marker) else end_marker)
    return "\n".join(out).strip()


def _is_structural_briefing_block(block: str) -> bool:
    stripped = normalize_unicode(block or "").strip()
    if not stripped:
        return False
    if stripped.startswith("### ") or stripped.startswith("#### "):
        return True
    if stripped.startswith("*") and stripped.endswith("*") and "\n" not in stripped:
        return True
    return (
        _contains_regular_end_marker(stripped)
        or _contains_podcast_end_marker(stripped)
        or _contains_briefing_end_marker(stripped)
    )


def _is_briefing_noise_line(line: str) -> bool:
    lowered = normalize_unicode(line or "").strip().lower()
    if not lowered:
        return False
    if "[gekürzt:" in lowered:
        return True
    if lowered.startswith("transparenz:"):
        return True
    if "partnerlink" in lowered or "partnerlinks" in lowered:
        return True
    if "kein einfluss auf unsere berichterstattung" in lowered or "keinerlei einfluss auf unsere berichterstattung" in lowered:
        return True
    if "fur kaufer andert sich der preis nicht" in lowered or "für käufer ändert sich der preis nicht" in lowered:
        return True
    if lowered.startswith("sign up for our") or ("newsletter" in lowered and "sign up" in lowered):
        return True
    if any(marker in lowered for marker in ("abo-shop", "e-paper", "epaper", "mein profil", "vorteilswelt", "push")):
        return True
    # Cookie/Consent-Reste im Output
    if any(marker in lowered for marker in (
        "wir verwenden cookies", "diese website verwendet cookies",
        "diese seite verwendet cookies", "cookies akzeptieren",
        "alle akzeptieren", "cookie-einstellungen",
        "einstellungen verwalten", "zur datenschutzerklärung",
        "we use cookies", "accept cookies", "manage cookies",
        "einwilligung", "cookie-richtlinie", "consent",
    )):
        return True
    # Abo/Paywall-Prompts im Output
    if any(marker in lowered for marker in (
        "jetzt abo", "digital-abo", "weiter lesen mit",
        "artikel lesen mit", "jetzt testen", "kostenlos testen",
        "jetzt abonnieren", "30 tage kostenlos",
    )):
        return True
    if re.match(r"^\s*[-•]\s*", line):
        upper_tokens = re.findall(r"\b[A-ZÄÖÜ0-9][A-ZÄÖÜ0-9\"'.:-]{2,}\b", line)
        if len(upper_tokens) >= 2:
            return True
        if any(marker in lowered for marker in ("display", "promotion", "warum ipad", "macbook", "airpods")):
            return True
    return False


def _strip_duplicate_heading_echo(text: str) -> str:
    """Entfernt 'Echo'-Zeilen direkt nach Markdown-Headings.

    GPT-5.x neigt dazu, eine `### Titel`-Zeile zu schreiben und direkt
    danach denselben Titel nochmal als nackte Plain-Text-Zeile zu
    wiederholen. Das landet im PDF zweimal sichtbar (einmal kursiv mit
    Hashes, einmal als großer H3-Heading). Diese Funktion findet das
    Pattern und entfernt die Echo-Zeile.
    """
    if not text:
        return text
    lines = text.split("\n")
    out: List[str] = []
    skip_next_echo_of: Optional[str] = None
    for line in lines:
        if skip_next_echo_of is not None:
            stripped = line.strip()
            if not stripped:
                # Leerzeile zwischen Heading und Echo erlaubt — durchreichen
                out.append(line)
                continue
            normalized = re.sub(r"[\s\W_]+$", "", stripped).strip().lower()
            if normalized == skip_next_echo_of:
                # Echo gefunden — überspringen
                skip_next_echo_of = None
                continue
            # Keine Echo-Übereinstimmung — Modus aus, Zeile normal verarbeiten
            skip_next_echo_of = None

        m = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if m:
            heading_text = m.group(2)
            # Trailing punct + sterne weg fürs Vergleichen
            heading_normalized = re.sub(r"[\s\W_]+$", "", heading_text.strip("* ").strip()).strip().lower()
            if heading_normalized:
                skip_next_echo_of = heading_normalized
        out.append(line)
    return "\n".join(out)


def _sanitize_briefing_output(text: str) -> str:
    normalized = normalize_unicode(text or "").strip()
    if not normalized:
        return normalized
    normalized = _strip_duplicate_heading_echo(normalized)

    blocks = re.split(r"\n{2,}", normalized)
    cleaned_blocks: List[str] = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if _is_structural_briefing_block(stripped):
            cleaned_blocks.append(stripped)
            continue

        # Ganze Absätze entfernen, die klar Boilerplate sind
        if len(stripped) <= 500:
            block_lowered = stripped.lower()
            if any(marker in block_lowered for marker in _BRIEFING_BOILERPLATE_MARKERS):
                if not any(word in block_lowered for word in ("briefing", "zusammenfassung", "nachricht")):
                    continue

        kept_lines = []
        for raw_line in stripped.splitlines():
            line = raw_line.strip()
            if not line or _is_briefing_noise_line(line):
                continue
            kept_lines.append(line)

        candidate = "\n".join(kept_lines).strip()
        if not candidate:
            continue
        if all(_is_briefing_noise_line(line) for line in candidate.splitlines() if line.strip()):
            continue
        cleaned_blocks.append(candidate)

    cleaned = "\n\n".join(cleaned_blocks).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _contains_regular_end_marker(text: str) -> bool:
    lowered = normalize_unicode(text or "").lower()
    return "weiter geht" in lowered


def _contains_podcast_end_marker(text: str) -> bool:
    lowered = normalize_unicode(text or "").lower()
    return "ende der podcastzusammenfassung" in lowered


def _contains_briefing_end_marker(text: str) -> bool:
    lowered = normalize_unicode(text or "").lower()
    return "ende des briefings" in lowered


def _current_section_audio_marker(current_index: int, total: int) -> str:
    if current_index == 1:
        return f"Beitrag {current_index} von {total}."
    if current_index == total:
        return f"Letzter Beitrag, {current_index} von {total}."
    return f"Beitrag {current_index} von {total}."


def _replace_last_matching_line(text: str, predicate: Callable[[str], bool], replacement: str) -> str:
    lines = text.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if predicate(lines[idx].strip()):
            lines[idx] = replacement
            return "\n".join(lines).strip()
    if text.strip():
        return (text.rstrip() + "\n" + replacement).strip()
    return replacement


def _strip_premature_briefing_end_markers(sections: List[dict]) -> None:
    """Entfernt 'Ende des Briefings.' aus ALLEN Sections außer der allerletzten.

    Manche Podcast-Zusammenfassungen oder LLM-Outputs enthalten 'Ende des Briefings.'
    obwohl noch weitere Beiträge folgen. Dieser Pass räumt das auf.
    """
    # Finde den Index der allerletzten Section, die "Ende des Briefings." enthalten DARF
    last_allowed_idx = -1
    for i, s in reversed(list(enumerate(sections))):
        if s.get("_verabschiedung") or s.get("_essenz") or s.get("_recap"):
            last_allowed_idx = i
            break
    if last_allowed_idx < 0:
        # Falls keiner der Spezial-Sections gefunden: letzter Index insgesamt
        last_allowed_idx = len(sections) - 1

    for i, s in enumerate(sections):
        if i >= last_allowed_idx:
            continue
        content = s.get("content", "")
        if "Ende des Briefings" in content:
            s["content"] = re.sub(r"\s*Ende des Briefings\.?\s*", " ", content).strip()


# Eingebettete Audio-Marker (ganze Zeile), optional mit Connector-Präfix wie
# "Nächster Beitrag." / "Letzter Beitrag." / "Weiterer Beitrag." — fängt sowohl
# saubere Marker als auch LLM-Artefakte, die der Annotator sonst stehen lässt.
_EMBEDDED_AUDIO_MARKER_RE = re.compile(
    r"^\s*"
    r"(?:(?:Nächster|Letzter|Weiterer|Vorheriger|Erster|Nächstes)\s+"
    r"(?:Beitrag|Artikel)\s*[.:]\s*)?"          # optionaler Connector ("Nächster Beitrag.")
    r"(?:Letzter\s+)?Beitrag,?\s+\d+\s+von\s+\d+\s*\.?\s*$",
    re.IGNORECASE,
)


def _strip_embedded_audio_markers(content: str) -> str:
    """Entfernt eingebettete Audio-Marker-Zeilen aus dem Section-Body.

    Greift Voll-Zeilen wie „Beitrag 5 von 83.", „Nächster Beitrag. Beitrag 61 von 62."
    oder „Letzter Beitrag, 82 von 83." — egal an welcher Position. Echter Fließtext,
    der „Beitrag X von Y" nur eingebettet in einen längeren Satz enthält, bleibt
    unangetastet (das Muster verlangt, dass die GANZE Zeile der Marker ist).
    Danach entstehende Leerzeilen-Häufungen werden zusammengefasst.
    """
    if not content:
        return content
    kept = [ln for ln in content.splitlines() if not _EMBEDDED_AUDIO_MARKER_RE.match(ln)]
    result = "\n".join(kept)
    # Mehr als eine Leerzeile am Stück auf eine reduzieren, führende Leerzeilen weg
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _annotate_section_progress_markers(sections: List[dict]) -> None:
    # Konsistenter Filter: exakt wie in der Genialen Zusammenfassung.
    # Recap/Essenz/Verabschiedung/Preview sind Meta-Blöcke, kein eigener „Artikel".
    content_indices = [idx for idx, section in enumerate(sections)
                       if section.get("type") != "transition"
                       and not section.get("_preview")
                       and not section.get("_essenz")
                       and not section.get("_verabschiedung")
                       and not section.get("_ressort_header")
                       and not section.get("_recap")]
    total = len(content_indices)
    if total <= 0:
        return

    for position, section_index in enumerate(content_indices, start=1):
        section = sections[section_index]
        content = normalize_unicode(section.get("content", "")).strip()
        if not content:
            continue
        is_last = position == total

        # Eingebettete Audio-Marker entfernen, bevor der neue gesetzt wird.
        # Fängt LLM-Artefakte ("Nächster Beitrag. Beitrag 61 von 62.") UND macht
        # den Annotator idempotent (verhindert Marker-Stapeln bei Mehrfach-Aufruf).
        content = _strip_embedded_audio_markers(content)

        # Zähler an den ANFANG setzen (vor dem ### Titel)
        if not section.get("_recap"):
            marker = _current_section_audio_marker(position, total)
            if content.startswith("### "):
                # Marker vor die Überschrift als eigene Zeile
                content = f"{marker}\n\n{content}"
            else:
                content = f"{marker}\n\n{content}"

        # Ende-Marker: "Ende des Briefings." kommt NUR beim allerletzten Beitrag.
        # Alle anderen bekommen "Weiter geht's." / "Ende der Podcastzusammenfassung."
        if section.get("_recap"):
            # Recap: "Ende des Briefings." nur wenn wirklich letzter Beitrag
            target = "Ende des Briefings." if is_last else "Weiter geht's."
            content = _replace_last_matching_line(
                content, _contains_briefing_end_marker, target,
            )
        elif section.get("type") == "podcast":
            podcast_end_re = re.compile(r"(?i)^.*ende der podcastzusammenfassung\.[^\n]*$")
            match = None
            for raw_line in reversed(content.splitlines()):
                stripped = raw_line.strip()
                if podcast_end_re.match(stripped):
                    match = stripped
                    break
            base_end = match or "Ende der Podcastzusammenfassung."
            if is_last:
                content = _replace_last_matching_line(
                    content, _contains_podcast_end_marker,
                    f"{base_end} Ende des Briefings.",
                )
            else:
                # Nicht-letzter Podcast: "Ende des Briefings." entfernen falls vorhanden
                content = content.replace("Ende des Briefings.", "").strip()
        elif is_last:
            content = _replace_last_matching_line(
                content, _contains_regular_end_marker,
                "Weiter geht's. Ende des Briefings.",
            )

        section["content"] = content


def _ensure_briefing_shape(client, text: str, model: str) -> Optional[str]:
    """Sichert grob das erwartete Briefing-Format ab."""
    if not text:
        return None
    text = _dedup_header(text)
    if _looks_like_briefing_summary(text):
        return _sanitize_briefing_output(_normalize_existing_briefing_markdown(text))
    formatted = summarize(client, text, FORMAT_PROMPT, model=model, max_tokens=4096)
    if formatted:
        return _sanitize_briefing_output(_normalize_existing_briefing_markdown(_dedup_header(formatted)))
    return text


_ENGLISH_FRAGMENT_PATTERNS = (
    r"\bthe\b",
    r"\band\b",
    r"\bwith\b",
    r"\bfrom\b",
    r"\bmore\b",
    r"\bless\b",
    r"\babout\b",
    r"\broughly\b",
    r"\baround\b",
    r"\bnearly\b",
    r"\bearlier this month\b",
    r"\boccupancy rate\b",
    r"\bvacancy rate\b",
    r"\bper square foot\b",
    r"\bdollars?\b",
    r"\bpercent\b",
    r"\bshoppers?\b",
    r"\btenant sales\b",
    r"\bproperty\b",
    r"\bproperties\b",
    r"\blaunched\b",
    r"\bstarted\b",
    r"\bhovering\b",
    # Häufige englische Restfetzen mitten im Satz (gesehen am 05.06.2026)
    r"\bbeing\b",
    r"\bthrough\b",
    r"\bbetween\b",
    r"\bwilling\b",
    r"\bprepared\b",
    r"\bresponse\b",
    r"\bmeaningful\b",
    r"\bdeeply\b",
    r"\bfully\b",
    r"\bwould\b",
    r"\bcould\b",
    r"\bshould\b",
    r"\bwithin\b",
    r"\btoward(?:s)?\b",
    r"\bsquare (?:feet|foot)\b",
    r"\bof the\b",
    r"\bin the\b",
    r"\bto the\b",
)


GERMANIZE_BRIEFING_PROMPT = """Du glättest einen fertigen Audio-Briefing-Abschnitt sprachlich ins Deutsche.

ZIEL
- Den bestehenden Abschnitt vollständig auf idiomatisches Deutsch übertragen.
- Fakten, Zahlen, Namen, Reihenfolgen, Struktur und Länge so stabil wie möglich behalten.

REGELN
- Nichts hinzufügen, nichts weglassen, nichts neu gewichten.
- Übersetze englische oder gemischtsprachige Formulierungen ins Deutsche.
- Eigennamen, offizielle Produktnamen, Originaltitel und klar gekennzeichnete Originalzitate dürfen im Original bleiben.
- Überschriften, Einordnung, „Was bleibt:“ und den Abschluss beibehalten.
- Zahlen, Prozentwerte, Daten und Uhrzeiten in Ziffernform belassen.

Gib NUR den geglätteten Abschnitt aus. Keine Kommentare, keine Erklärungen."""


# Starke Signale: Begriffe/Phrasen, die in deutschen Eigennamen/Titeln praktisch nie
# vorkommen → schon EIN Treffer löst die Germanize-Nachbearbeitung aus.
_ENGLISH_STRONG_PATTERNS = (
    r"\bearlier this month\b",
    r"\boccupancy rate\b",
    r"\bvacancy rate\b",
    r"\bper square foot\b",
    r"\bsquare (?:feet|foot)\b",
    r"\btenant sales\b",
    r"\bpercent\b",
    r"\bdollars?\b",
    r"\bshoppers?\b",
    r"\bhovering\b",
    r"\bmeaningful\b",
    r"\bwilling\b",
    r"\bprepared\b",
    r"\bresponse\b",
    r"\btoward(?:s)?\b",
)


def _looks_english_mixed(text: str) -> bool:
    """True, wenn der Abschnitt wahrscheinlich unübersetzte englische Reste enthält.
    Ein STARKES Signal genügt; sonst braucht es >=3 generische Treffer. So lösen
    englische Eigennamen/Titel (z.B. „The Voice of ...") NICHT unnötig die
    Germanize-Nachbearbeitung aus (vermeidet überflüssige API-Calls im API-Pfad)."""
    lowered = normalize_unicode(text).lower()
    if any(re.search(p, lowered) for p in _ENGLISH_STRONG_PATTERNS):
        return True
    total = sum(1 for p in _ENGLISH_FRAGMENT_PATTERNS if re.search(p, lowered))
    return total >= 3


def _germanize_briefing_if_needed(client, text: str, model: str) -> Optional[str]:
    if not text:
        return text
    if not _looks_english_mixed(text):
        return text

    germanized = summarize(
        client,
        text,
        GERMANIZE_BRIEFING_PROMPT,
        model=model,
        max_tokens=4096,
    )
    germanized = _ensure_briefing_shape(client, germanized, model)
    return germanized or text


def _soften_political_labels_if_needed(text: str, source_text: str) -> str:
    """Verhindert unnötig verschärfte politische Labels bei englischen Quellen."""
    if not text or not source_text:
        return text

    source_lower = normalize_unicode(source_text).lower()
    text_lower = normalize_unicode(text).lower()
    if (
        "stérin" not in text_lower
        and "sterin" not in text_lower
        and "stérin" not in source_lower
        and "sterin" not in source_lower
    ):
        return text
    if "far right" not in source_lower and "right-wing" not in source_lower:
        return text

    softened = text
    replacements = (
        (r"\bdie viele mit der extremen Rechten verbinden\b", "die viele dem weit rechten Spektrum zuordnen"),
        (r"\brechtsextreme beziehungsweise rechte\b", "rechte"),
        (r"\brechtsextreme oder rechte\b", "rechte"),
        (r"\bmit der extremen Rechten\b", "dem weit rechten Spektrum"),
        (r"\bder extremen Rechten\b", "dem weit rechten Spektrum"),
        (r"\brechtsextrem\b", "weit rechts"),
    )
    for pattern, repl in replacements:
        softened = re.sub(pattern, repl, softened, flags=re.IGNORECASE)
    return softened


_SPORT_SOURCE_MARKERS = (
    "halbmarathon",
    "meisterschaft",
    "vize",
    "silber",
    "bronze",
    "gold",
    "teamwertung",
    "platz",
    "platzierung",
    "ziel",
    "stunden",
    "minuten",
    "sekunden",
    "siegte",
)

SPORT_RESULT_REPAIR_PROMPT = """Du prüfst einen fertigen Sport-Briefing-Abschnitt gegen den Quelltext.

KORRIGIERE NUR:
- Platzierungen
- Medaillen
- Siegerstatus
- Teamwertungen
- Zeiten
- Titelzeile, falls sie einen falschen Sieger- oder Medaillenstatus behauptet

REGELN
- Nur auf Basis des Quelltexts korrigieren.
- Keine neuen Details hinzufügen.
- Wenn eine konkrete Wertung oder Zahl im Entwurf nicht sauber belegt ist, lieber neutraler formulieren.
- Struktur und Format exakt beibehalten: Titel, Einordnung, Fließtext, „Was bleibt:“ und Abschluss.

Gib NUR den korrigierten Abschnitt aus. Keine Kommentare, keine Erklärungen."""


SECTION_REPAIR_PROMPT = """Du reparierst einen bestehenden Audio-Briefing-Abschnitt auf Basis des Quelltexts und konkreter Plausibilitäts-Hinweise.

ZIEL
- Nur die beanstandeten Stellen minimal korrigieren.
- Den übrigen Abschnitt so stabil wie möglich lassen.

REGELN
- Nutze ausschließlich den Quelltext und die genannten Probleme.
- Nichts hinzufügen, was nicht klar im Quelltext steht.
- Keine generischen Zusätze wie „offen bleibt" oder „ein genauer Zeitplan wird nicht genannt", wenn die Quelle das nicht selbst so sagt.
- Wenn ein beanstandetes Detail im Quelltext nicht sauber belegt ist, streiche es lieber klar oder formuliere es enger, statt nur weich darum herumzureden.
- Wenn ein beanstandeter Punkt eine falsche Zuordnung, Reichweite, Verfügbarkeit, Beschlusslage, Zuständigkeit, Versionsaussage oder Geografie betrifft, korrigiere genau diesen Kern ausdrücklich.
- Randhinweise wie Partnerlinks, Transparenzhinweise, Shop-Hinweise oder Mediathek-/Freeform-Details nur dann nennen, wenn sie für den eigentlichen Artikelkern wirklich zentral sind.
- Transparenz-, Partnerlink-, Newsletter-, Shop-, Cookie-, Portal- oder Promo-Reste ausdrücklich entfernen, wenn sie nicht Kern des Artikels sind.
- Wenn etwas im bisherigen Abschnitt zu sicher oder zu stark klingt, formuliere nur so weit zurück, wie es der Quelltext trägt.
- Gültige Briefing-Struktur ist erwünscht und muss erhalten bleiben: `###`-Titel, kursiver Einordnungssatz, `#### Was bleibt:`, `Weiter geht's.`, Beitragsmarker wie `Nächster Beitrag. Beitrag X von Y.`, `Letzter Beitrag. Beitrag X von Y.`, `Ende der Podcastzusammenfassung.` und `Ende des Briefings.`.
- Struktur exakt beibehalten: Titel, Einordnung, Fließtext, „Was bleibt:“ und Abschluss.
- Format exakt beibehalten. Keine Kommentare, keine Listen außerhalb der bestehenden Struktur.

Gib NUR den korrigierten Abschnitt aus. Keine Kommentare, keine Erklärungen."""


def _looks_like_sport_source(text: str) -> bool:
    lowered = normalize_unicode(text).lower()
    hits = sum(1 for marker in _SPORT_SOURCE_MARKERS if marker in lowered)
    return hits >= 3


def _repair_sport_summary_if_needed(client, summary: str, source_text: str, model: str) -> Optional[str]:
    if not summary or not source_text or not _looks_like_sport_source(source_text):
        return summary

    repaired = summarize(
        client,
        f"SPORT-BRIEFING-ENTWURF:\n{summary}\n\nQUELLTEXT:\n{source_text}",
        SPORT_RESULT_REPAIR_PROMPT,
        model=model,
        max_tokens=3500,
    )
    repaired = _ensure_briefing_shape(client, repaired, model)
    return repaired or summary


def _extract_article_facts(client, source_text: str, model: str) -> Optional[dict]:
    raw = summarize(
        client,
        source_text,
        ARTIKEL_FAKTEN_PROMPT,
        model=model,
        max_tokens=3500,
        json_mode=True,
    )
    if not raw:
        return None
    parsed = _parse_json_object(raw)
    if not isinstance(parsed, dict):
        parsed = _repair_json_object(
            client,
            raw,
            model,
            """{
  "title_seed": "kurzer sachlicher Titelkern",
  "source_kind": "event|service|politics|business|tech|science|culture|crime|sport|other",
  "core_claim": "1 Satz zur Hauptaussage",
  "what_happened": ["maximal 5 Punkte"],
  "why_relevant": ["maximal 3 Punkte"],
  "what_follows": ["maximal 3 Punkte"],
  "must_keep": ["wichtige Details mit Qualifiern und konkreten Angaben"],
  "numbers": ["wichtige Zahlen mit Bedeutung"],
  "context": ["wichtiger Hintergrund"],
  "uncertainties": ["offene Punkte, Einschränkungen, Vorbehalte"],
  "locations": ["wichtige Orte"],
  "people": ["wichtige Personen oder Institutionen"]
}""",
        )
    if not isinstance(parsed, dict):
        return None
    return parsed


def _summarize_article_from_source(client, source_text: str, model: str, compact: bool = False) -> Optional[str]:
    """Erzeugt ein Artikel-Briefing bevorzugt über einen Fakten-Zwischenschritt."""
    import json

    article_prompt = ARTIKEL_KOMPAKT_PROMPT if compact else ARTIKEL_PROMPT
    facts_prompt = ARTIKEL_KOMPAKT_AUS_FAKTEN_PROMPT if compact else ARTIKEL_AUS_FAKTEN_PROMPT
    max_tok = 2400 if compact else 3500

    facts = _extract_article_facts(client, source_text, model)
    if facts:
        facts_json = json.dumps(facts, ensure_ascii=False, indent=2)
        summary = summarize(
            client,
            facts_json,
            facts_prompt,
            model=model,
            max_tokens=max_tok,
        )
        summary = _ensure_briefing_shape(client, summary, model)
        summary = _germanize_briefing_if_needed(client, summary, model)
        summary = _soften_political_labels_if_needed(summary, source_text)
        summary = _repair_sport_summary_if_needed(client, summary, source_text, model)
        if summary:
            return summary

    fallback = summarize(client, source_text, article_prompt, model=model, max_tokens=max_tok)
    fallback = _ensure_briefing_shape(client, fallback, model)
    fallback = _germanize_briefing_if_needed(client, fallback, model)
    fallback = _soften_political_labels_if_needed(fallback, source_text)
    return _repair_sport_summary_if_needed(client, fallback, source_text, model)


def _content_check_models(main_model: str) -> List[str]:
    """Wählt Prüfmodelle: Hauptmodell bevorzugt, günstigeres als Fallback."""
    candidates: List[str]
    if main_model.startswith("gpt-"):
        candidates = [main_model, "gpt-5.4-mini", "gpt-4.1-mini"]
    else:
        candidates = [main_model, "claude-haiku-4-5-20251001"]

    unique = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json_object(text: str) -> Optional[dict]:
    import json

    cleaned = _strip_json_fences(text)
    try:
        return json.loads(cleaned)
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except Exception:
                return None
    return None


def _repair_json_object(client, raw_text: str, model: str, schema_example: str) -> Optional[dict]:
    repair_prompt = (
        "Du bekommst eine fehlerhafte Modellantwort. "
        "Wandle nur das bereits Enthaltene in valides JSON um. "
        "Erfinde nichts neu, glätte nichts inhaltlich und gib nur ein JSON-Objekt zurück.\n\n"
        f"Erwartetes Schema-Beispiel:\n{schema_example}"
    )
    repaired = summarize(
        client,
        raw_text,
        repair_prompt,
        model=model,
        max_tokens=1200,
        json_mode=model.startswith("gpt-"),
    )
    if not repaired:
        return None
    return _parse_json_object(repaired)


def _coerce_score(value) -> Optional[int]:
    try:
        score = int(value)
    except Exception:
        return None
    if 1 <= score <= 5:
        return score
    return None


def _is_non_issue_content_check_text(text: str) -> bool:
    lowered = normalize_unicode(str(text or "")).lower()
    collapsed = re.sub(r"\s+", " ", lowered).strip(" .:;,-")
    if collapsed in {"keine", "kein", "none", "nichts"}:
        return True
    expected_marker_issue = (
        any(
            marker in lowered
            for marker in (
                "weiter geht",
                "was bleibt",
                "ende der podcastzusammenfassung",
                "ende des briefings",
                "nächster beitrag",
                "letzter beitrag",
            )
        )
        and any(
            context in lowered
            for context in (
                "boilerplate",
                "fremdtext",
                "redaktionell",
                "redaktioneller rest",
                "portalrest",
                "resttext",
                "gehört nicht",
                "nicht zum eigentlichen briefingkern",
                "nicht zum nachrichtenkern",
                "meta",
            )
        )
    )
    if expected_marker_issue:
        return True
    if "markdown" in lowered and any(term in lowered for term in ("formatierung", "überschrift", "kursiv")):
        return True
    return any(
        phrase in lowered for phrase in (
            "keine materiellen widersprüche",
            "keine klaren materiellen widersprüche",
            "keine klar falschen kernaussagen",
            "keine wesentlichen inhaltlichen konflikte",
            "keine materiellen konflikte",
            "keine relevanten konflikte erkennbar",
            "keine klaren materiellen widerspruche",
            "keine materielle abweichung",
        )
    )


def _trim_for_check(text: str, max_chars: int, keep_ends: bool = False) -> str:
    text = normalize_unicode(text).strip()
    if len(text) <= max_chars:
        return text
    if not keep_ends or max_chars < 1200:
        return text[:max_chars] + "\n\n[gekürzt]"

    segment_count = 5 if max_chars >= 4000 else 3
    gap_label = "\n\n[gekürzt: Mittelteil ausgelassen]\n\n"
    usable_chars = max_chars - ((segment_count - 1) * len(gap_label))
    if usable_chars < (segment_count * 220):
        segment_count = 3
        usable_chars = max_chars - ((segment_count - 1) * len(gap_label))
    if usable_chars < 800:
        return text[:max_chars] + "\n\n[gekürzt]"

    if segment_count == 5:
        weights = [1.35, 1.0, 1.0, 1.0, 1.35]
        anchors = [0.0, 0.25, 0.5, 0.75, 1.0]
    else:
        weights = [1.35, 1.0, 1.35]
        anchors = [0.0, 0.5, 1.0]

    weight_sum = sum(weights)
    lengths = [max(220, int(usable_chars * (weight / weight_sum))) for weight in weights]
    overflow = sum(lengths) - usable_chars
    idx = len(lengths) - 1
    while overflow > 0 and idx >= 0:
        reducible = lengths[idx] - 220
        if reducible > 0:
            delta = min(reducible, overflow)
            lengths[idx] -= delta
            overflow -= delta
        idx -= 1
        if idx < 0 and overflow > 0:
            idx = len(lengths) - 1

    segments = []
    text_len = len(text)
    for idx, (anchor, seg_len) in enumerate(zip(anchors, lengths)):
        if idx == 0:
            start = 0
            end = min(text_len, seg_len)
        elif idx == len(anchors) - 1:
            end = text_len
            start = max(0, end - seg_len)
        else:
            center = int(text_len * anchor)
            start = max(0, center - (seg_len // 2))
            end = min(text_len, start + seg_len)
            start = max(0, end - seg_len)
        segments.append((start, end))

    merged = []
    for start, end in segments:
        if not merged:
            merged.append((start, end))
            continue
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            if end > prev_end:
                merged[-1] = (prev_start, end)
            continue
        merged.append((start, end))

    parts = [text[start:end].strip() for start, end in merged if text[start:end].strip()]
    return gap_label.join(parts)


def _run_single_content_check(client, payload: dict, model: str, mode: str) -> dict:
    prompt = CONTENT_CHECK_FULL_PROMPT if mode == "full" else CONTENT_CHECK_WARN_PROMPT
    max_tokens = 900 if mode == "full" else 450
    is_recap = payload.get("item_type") == "recap"
    # Recap fasst ALLE Beiträge zusammen → braucht mehr Platz für den Quelltext
    source_limit = (28000 if is_recap else 18000) if mode == "full" else (20000 if is_recap else 12000)
    summary_limit = 6500 if mode == "full" else 4500
    source_text = _trim_for_check(payload["source_text"], source_limit, keep_ends=True)
    summary_text = _trim_for_check(payload["summary_text"], summary_limit, keep_ends=True)
    recap_note = ""
    if is_recap:
        recap_note = (
            "\nHINWEIS: Dies ist ein Recap, das ALLE Beiträge des Briefings in einem Satz pro Beitrag "
            "zusammenfasst. Der Quelltext enthält die Zusammenfassungen aller Beiträge — die genannten Themen "
            "können weit verstreut im Quelltext stehen. Bewerte nur als Warnung, wenn ein Punkt nachweislich "
            "NICHT im Quelltext vorkommt oder inhaltlich verdreht ist. Fehlende Details sind bei einem Recap normal.\n"
        )
    check_text = (
        f"TYP: {payload['item_type']}\n"
        f"LABEL: {payload['label']}\n"
        f"{recap_note}\n"
        f"QUELLTEXT:\n{source_text}\n\n"
        f"BRIEFING-ABSCHNITT:\n{summary_text}"
    )

    raw = summarize(
        client,
        check_text,
        prompt,
        model=model,
        max_tokens=max_tokens,
        json_mode=True,
        timeout_seconds=CONTENT_CHECK_REQUEST_TIMEOUT_SECONDS,
    )
    if not raw:
        return {
            "_check_key": payload.get("_check_key"),
            "label": payload["label"],
            "item_type": payload["item_type"],
            "level": "warn",
            "summary": "Prüfung konnte nicht sauber ausgewertet werden.",
            "hard_issues": ["JSON-Antwort der Plausibilitätsprüfung fehlgeschlagen."],
            "soft_issues": [],
            "issues": ["JSON-Antwort der Plausibilitätsprüfung fehlgeschlagen."],
            "failed": True,
        }

    parsed = _parse_json_object(raw)
    if not parsed:
        parsed = _repair_json_object(
            client,
            raw,
            model,
            """{
  "level": "ok|notice|warn",
  "summary": "kurzer Ein-Satz-Befund",
  "faithfulness": 1,
  "coverage": 1,
  "hard_issues": ["materielle Probleme"],
  "soft_issues": ["weichere Hinweise"],
  "strengths": ["kurze Punkte"],
  "source_support": ["kurze Quelltextstellen"]
}""",
        )
    if not parsed:
        return {
            "_check_key": payload.get("_check_key"),
            "label": payload["label"],
            "item_type": payload["item_type"],
            "level": "warn",
            "summary": "Prüfung lieferte kein auswertbares JSON.",
            "hard_issues": ["Antwortformat der Plausibilitätsprüfung war unbrauchbar."],
            "soft_issues": [],
            "issues": ["Antwortformat der Plausibilitätsprüfung war unbrauchbar."],
            "failed": True,
        }

    parsed_level = str(parsed.get("level", "")).strip().lower()
    hard_issues = [str(item).strip() for item in parsed.get("hard_issues", []) if str(item).strip()]
    soft_issues = [str(item).strip() for item in parsed.get("soft_issues", []) if str(item).strip()]
    source_support = [str(item).strip() for item in parsed.get("source_support", []) if str(item).strip()]
    legacy_issues = [str(item).strip() for item in parsed.get("issues", []) if str(item).strip()]
    if legacy_issues and not hard_issues and not soft_issues:
        if parsed_level == "warn":
            hard_issues = legacy_issues
        else:
            soft_issues = legacy_issues

    def _is_soft_not_hard(issue_text: str, source_text: str, summary_text: str) -> bool:
        """Generische Erkennung: Issue klingt nach warn, ist aber eher notice."""
        low = normalize_unicode(issue_text).lower()
        src = normalize_unicode(source_text).lower()
        summ = normalize_unicode(summary_text).lower()

        # Detail im Quelltext vorhanden, im Briefing weggelassen — meist Coverage, nicht Faithfulness
        if any(p in low for p in (
            "im quelltext genannt", "im quelltext erwähnt", "im quelltext vorhanden",
            "wird im quelltext", "steht im quelltext", "fehlt im briefing",
            "nicht im briefing erwähnt", "nicht übernommen", "wird nicht erwähnt",
            "im original erwähnt", "in the source", "mentioned in source",
            "not mentioned in briefing", "omitted from briefing",
        )):
            # Nur downgraden wenn kein Kern-Keyword dabei
            if not any(k in low for k in ("kernaussage", "kernpunkt", "zentral", "wesentlich", "hauptaussage", "core")):
                return True

        # Audio-Rundung (Zahlen nahe beieinander)
        if any(p in low for p in (
            "rundet", "rundung", "gerundet", "knapp", "gut ", "etwa", "circa",
            "rounded", "approximat",
        )):
            return True

        # Leichte Wortwahlunterschiede
        if any(p in low for p in (
            "formulierung", "wortwahl", "wording", "paraphras", "umformulier",
            "anders formuliert", "slightly different", "leicht anders",
        )):
            if not any(k in low for k in ("verschärft", "dramatisiert", "verfälscht", "widerspricht", "contradicts")):
                return True

        # Reihenfolge/Auswahl bei langen Artikeln
        if any(p in low for p in (
            "reihenfolge", "auswahl", "beispielauswahl", "nicht alle beispiele",
            "selection", "order of", "some examples",
        )):
            return True

        # Stilkritik als harte Warnung verkleidet
        if any(p in low for p in (
            "könnte klarer", "etwas unpräzise", "nicht ganz sauber", "leicht ungenau",
            "etwas verkürzt", "slightly imprecise", "could be clearer",
        )):
            return True

        return False

    hard_issues = [item for item in hard_issues if not _is_non_issue_content_check_text(item)]
    soft_issues = [item for item in soft_issues if not _is_non_issue_content_check_text(item)]

    # Generisches Downgrading: harte Issues die eigentlich soft sind
    still_hard = []
    for issue in hard_issues:
        if _is_soft_not_hard(issue, payload.get("source_text", ""), payload.get("summary_text", "")):
            soft_issues.append(issue)
        else:
            still_hard.append(issue)
    hard_issues = still_hard

    contamination_issues = _detect_briefing_boilerplate_contamination(payload.get("summary_text", ""))
    for issue in contamination_issues:
        if issue not in hard_issues:
            hard_issues.append(issue)

    result = {
        "_check_key": payload.get("_check_key"),
        "label": payload["label"],
        "item_type": payload["item_type"],
        "level": parsed_level if parsed_level in {"ok", "notice", "warn"} else "ok",
        "summary": parsed.get("summary", "").strip() or "Keine Zusammenfassung der Prüfung.",
        "hard_issues": hard_issues,
        "soft_issues": soft_issues,
        "issues": hard_issues + soft_issues,
        "source_support": source_support,
        "failed": False,
    }
    if mode == "full":
        result["faithfulness"] = _coerce_score(parsed.get("faithfulness"))
        result["coverage"] = _coerce_score(parsed.get("coverage"))
        result["strengths"] = [str(item).strip() for item in parsed.get("strengths", []) if str(item).strip()]
        if result.get("faithfulness") is not None and result["faithfulness"] <= 3:
            result["level"] = "warn"
        if result.get("coverage") is not None and result["coverage"] <= 3:
            result["level"] = "warn"
    summary_lower = normalize_unicode(result["summary"]).lower()
    # Level strikt aus tatsaechlich vorhandenen Issues ableiten
    if result["hard_issues"]:
        result["level"] = "warn"
    elif result["soft_issues"]:
        result["level"] = "notice"
    elif not result["hard_issues"] and not result["soft_issues"]:
        # LLM sagte warn/notice, aber nach Filterung keine Issues mehr uebrig
        if result["level"] in {"warn", "notice"}:
            result["level"] = "ok"
            if "keine" not in summary_lower and "kein" not in summary_lower:
                result["summary"] = "Nach Filterung keine materiellen Befunde."

    # Zusaetzlich: Wenn LLM-Summary selbst sagt "alles ok" aber level noch warn
    if result["level"] == "warn" and not result["hard_issues"] and any(
        phrase in summary_lower for phrase in (
            "keine materiellen widersprüche",
            "keine klar falschen kernaussagen",
            "ohne kernbruch",
            "stimmt überein",
            "faktisch korrekt",
            "keine wesentlichen fehler",
            "no material issues",
            "faithfully reflects",
        )
    ):
        result["level"] = "notice" if result["soft_issues"] else "ok"
    if not result.get("source_support"):
        result["source_support"] = _extract_source_support_snippets(
            payload.get("source_text", ""),
            payload.get("summary_text", ""),
            result["hard_issues"] + result["soft_issues"] + [result["summary"]],
            limit=2,
        )
    return result


def _run_content_check(client,
                       payloads: List[dict],
                       model,
                       mode: str,
                       progress_callback: Optional[Callable[[str, float], None]] = None,
                       progress_start: Optional[float] = None,
                       progress_end: Optional[float] = None) -> dict:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed

    prepared_payloads = []
    for idx, payload in enumerate(payloads):
        payload_copy = dict(payload)
        payload_copy["_check_key"] = str(idx)
        prepared_payloads.append(payload_copy)

    model_candidates = [model] if isinstance(model, str) else [m for m in model if m]
    if progress_start is None:
        progress_start = CONTENT_CHECK_PROGRESS_START
    if progress_end is None:
        progress_end = CONTENT_CHECK_PROGRESS_END

    if not prepared_payloads:
        return {
            "enabled": True,
            "mode": mode,
            "model": model_candidates[0] if model_candidates else None,
            "models_attempted": list(model_candidates),
            "model_display": model_candidates[0] if model_candidates else None,
            "checked": 0,
            "warnings": 0,
            "notices": 0,
            "ok": 0,
            "failed": 0,
            "items": [],
            "all_items": [],
            "status": "ok",
        }

    last_report = None
    if progress_callback:
        progress_callback(
            f"Inhaltlicher Plausibilitäts-Check 0/{len(prepared_payloads)}",
            progress_start,
        )
    total_payloads = len(prepared_payloads)
    pending_payloads = list(prepared_payloads)
    resolved_results = {}
    attempted_models: List[str] = []

    for candidate in model_candidates:
        attempted_models.append(candidate)
        if not pending_payloads:
            break
        results = []
        batch_timeout = _content_check_batch_timeout(candidate, len(pending_payloads))
        timed_out = False
        pool = ThreadPoolExecutor(max_workers=_max_workers(candidate))
        try:
            future_to_payload = {
                pool.submit(_run_single_content_check, client, payload, candidate, mode): payload
                for payload in pending_payloads
            }
            completed = 0
            try:
                for future in as_completed(future_to_payload, timeout=batch_timeout):
                    payload = future_to_payload[future]
                    try:
                        results.append(future.result())
                    except Exception as e:
                        results.append({
                            "label": payload["label"],
                            "item_type": payload["item_type"],
                            "level": "warn",
                            "summary": f"Prüfung abgebrochen: {type(e).__name__}",
                            "hard_issues": [str(e)],
                            "soft_issues": [],
                            "issues": [str(e)],
                            "failed": True,
                        })
                    completed += 1
                    if progress_callback:
                        fraction = (len(resolved_results) + completed) / max(total_payloads, 1)
                        progress = progress_start + (progress_end - progress_start) * fraction
                        progress_callback(
                            f"Inhaltlicher Plausibilitäts-Check {min(len(resolved_results) + completed, total_payloads)}/{total_payloads}",
                            progress,
                        )
            except TimeoutError:
                timed_out = True
                for future, payload in future_to_payload.items():
                    if future.done():
                        continue
                    future.cancel()
                    results.append({
                        "_check_key": payload.get("_check_key"),
                        "label": payload["label"],
                        "item_type": payload["item_type"],
                        "level": "warn",
                        "summary": "Prüfung brach wegen Zeitlimit ab.",
                        "hard_issues": [
                            f"Plausibilitätsprüfung überschritt das Zeitlimit von {int(batch_timeout)} Sekunden."
                        ],
                        "soft_issues": [],
                        "issues": [
                            f"Plausibilitätsprüfung überschritt das Zeitlimit von {int(batch_timeout)} Sekunden."
                        ],
                        "failed": True,
                    })
        finally:
            pool.shutdown(wait=not timed_out, cancel_futures=timed_out)

        next_pending = []
        last_candidate = candidate == model_candidates[-1]
        payload_by_key = {payload["_check_key"]: payload for payload in pending_payloads}
        for item in results:
            check_key = item.get("_check_key")
            if item.get("failed") and not last_candidate:
                if check_key in payload_by_key:
                    next_pending.append(payload_by_key[check_key])
            else:
                resolved_results[check_key] = item

        for payload in pending_payloads:
            if payload["_check_key"] in resolved_results:
                continue
            if any(p["_check_key"] == payload["_check_key"] for p in next_pending):
                continue
            if not last_candidate:
                next_pending.append(payload)
            else:
                resolved_results[payload["_check_key"]] = {
                    "_check_key": payload["_check_key"],
                    "label": payload["label"],
                    "item_type": payload["item_type"],
                    "level": "warn",
                    "summary": "Prüfung konnte nicht abgeschlossen werden.",
                    "hard_issues": ["Plausibilitätsprüfung lieferte kein verwertbares Ergebnis."],
                    "soft_issues": [],
                    "issues": ["Plausibilitätsprüfung lieferte kein verwertbares Ergebnis."],
                    "failed": True,
                }

        pending_payloads = next_pending

        ordered_results = [
            resolved_results[payload["_check_key"]]
            for payload in prepared_payloads
            if payload["_check_key"] in resolved_results
        ]
        warning_items = [item for item in ordered_results if item.get("level") == "warn"]
        notice_items = [item for item in ordered_results if item.get("level") == "notice"]
        failed_items = [item for item in ordered_results if item.get("failed")]
        display_items = warning_items + notice_items
        report = {
            "enabled": True,
            "mode": mode,
            "model": candidate,
            "models_attempted": list(attempted_models),
            "model_display": (
                f"{attempted_models[0]} mit Fallback auf {', '.join(attempted_models[1:])}"
                if len(attempted_models) > 1 else candidate
            ),
            "checked": len(ordered_results),
            "warnings": len(warning_items),
            "notices": len(notice_items),
            "ok": len(ordered_results) - len(warning_items) - len(notice_items),
            "failed": len(failed_items),
            "items": display_items,
            "all_items": ordered_results,
            "status": "warn" if warning_items else ("notice" if notice_items else "ok"),
        }
        last_report = report
        if not pending_payloads:
            return report

    return last_report or {
        "enabled": True,
        "mode": mode,
        "model": model_candidates[0] if model_candidates else None,
        "models_attempted": list(model_candidates),
        "model_display": (
            f"{model_candidates[0]} mit Fallback auf {', '.join(model_candidates[1:])}"
            if len(model_candidates) > 1 else (model_candidates[0] if model_candidates else None)
        ),
        "checked": 0,
        "warnings": 0,
        "notices": 0,
        "ok": 0,
        "failed": 0,
        "items": [],
        "all_items": [],
        "status": "ok",
    }


def _markdown_line_to_plain(line: str) -> str:
    """Entfernt Markdown-Marker, behält aber eine gut lesbare Klartextstruktur."""
    line = line.strip()
    if not line:
        return ""

    for prefix in ("#### ", "### ", "## ", "# "):
        if line.startswith(prefix):
            line = line[len(prefix):].strip()
            break

    bold_match = re.match(r'^\*\*(.+?)\*\*\s*$', line)
    if bold_match:
        line = bold_match.group(1).strip()
    elif line.startswith("*") and line.endswith("*") and not line.startswith("**"):
        line = line.strip("*").strip()

    line = re.sub(r'^\s*[•\-]\s+', '', line)
    line = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
    line = re.sub(r'\*([^*]+)\*', r'\1', line)
    line = normalize_unicode(line)
    line = re.sub(r'[ \t]+', ' ', line).strip()
    return line


CURRENT_ELEVEN_TXT_NAME = "_AKTUELL_eleven-reader.txt"


def _write_current_eleven_copy(texte_dir: str, eleven_text: str) -> None:
    """Schreibt/überschreibt zusätzlich eine Kopie mit FESTEM Namen (sortiert oben,
    stabiler Pfad) — dafür da, dass ein iPhone-Kurzbefehl das jeweils neueste
    Briefing mit EINEM Tipp an ElevenReader übergeben kann."""
    try:
        p = os.path.join(texte_dir, CURRENT_ELEVEN_TXT_NAME)
        with open(p, "w", encoding="utf-8") as fp:
            fp.write(eleven_text)
    except Exception:
        pass


def create_eleven_reader_text(sections: List[dict], generated_at: datetime.datetime) -> str:
    """Erzeugt eine markdownfreie Klartext-Version für Eleven Reader."""
    lines = [
        "Audio-Briefing",
        generated_at.strftime("%d.%m.%Y"),
        f"Erstellt um {generated_at.strftime('%H:%M:%S')} Uhr",
        "",
    ]

    for section in sections:
        if section["type"] == "transition":
            heading = _markdown_line_to_plain(section["content"])
            if heading:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(heading)
                lines.append("")
            continue

        for raw_line in _content_with_source_label(section).splitlines():
            plain = _markdown_line_to_plain(raw_line)
            if plain:
                lines.append(plain)
            elif lines and lines[-1] != "":
                lines.append("")

        if lines and lines[-1] != "":
            lines.append("")

    text = "\n".join(lines)
    text = normalize_unicode(text)
    text = text.replace("*", "")
    text = text.replace("#", "")
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip() + "\n"


def _markdown_line_to_epub_block(line: str):
    """Ordnet eine Markdown-Zeile einem einfachen EPUB-Blocktyp zu."""
    line = line.strip()
    if not line:
        return None

    if line.startswith("#### "):
        return ("h4", line[5:].strip())
    if line.startswith("### "):
        return ("h3", line[4:].strip())
    if line.startswith("## "):
        return ("h3", line[3:].strip())
    if line.startswith("# "):
        return ("h2", line[2:].strip())

    bold_match = re.match(r'^\*\*(.+?)\*\*\s*$', line)
    if bold_match:
        return ("h4", bold_match.group(1).strip())

    if line.startswith("*") and line.endswith("*") and not line.startswith("**"):
        return ("intro", line.strip("*").strip())

    line = re.sub(r'^\s*[•\-]\s+', '', line)
    line = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
    line = re.sub(r'\*([^*]+)\*', r'\1', line)
    return ("p", line.strip())


def create_epub(sections: List[dict], generated_at: datetime.datetime) -> bytes:
    """Erzeugt ein einfaches, strukturiertes EPUB fuer Reader/TTS."""
    import html
    import io
    import uuid
    import zipfile

    today = generated_at.date()
    modified = generated_at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    book_id = f"urn:uuid:{uuid.uuid4()}"
    _WEEKDAYS_DE_EPUB = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    _MONTHS_DE_EPUB = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    _wd = _WEEKDAYS_DE_EPUB[generated_at.weekday()]
    _mn = _MONTHS_DE_EPUB[generated_at.month]
    title = f"{_wd}, {generated_at.day}. {_mn} {generated_at.year} — Audio-Briefing"

    body = [
        '<section class="cover">',
        f"<h1>{html.escape(title)}</h1>",
        f"<p class=\"date\">{today.strftime('%d.%m.%Y')}</p>",
        f"<p class=\"date\">Erstellt um {generated_at.strftime('%H:%M:%S')} Uhr</p>",
        "</section>",
    ]

    for section in sections:
        if section["type"] == "transition":
            heading = normalize_unicode(section["content"]).strip()
            if heading:
                body.append("<section class=\"transition\">")
                body.append(f"<h2>{html.escape(heading, quote=True)}</h2>")
                body.append("</section>")
            continue

        body.append("<section class=\"entry\">")
        for raw_line in _content_with_source_label(section).splitlines():
            block = _markdown_line_to_epub_block(raw_line)
            if not block:
                continue

            kind, text = block
            text = normalize_unicode(text)
            text = html.escape(text, quote=True)

            if kind == "intro":
                body.append(f"<p class=\"intro\">{text}</p>")
            elif kind == "h2":
                body.append(f"<h2>{text}</h2>")
            elif kind == "h3":
                body.append(f"<h3>{text}</h3>")
            elif kind == "h4":
                body.append(f"<h4>{text}</h4>")
            else:
                body.append(f"<p>{text}</p>")
        body.append("</section>")

    briefing_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="de" lang="de">
  <head>
    <title>{html.escape(title)}</title>
    <link rel="stylesheet" type="text/css" href="styles.css"/>
  </head>
  <body>
    <main>
      {' '.join(body)}
    </main>
  </body>
</html>
"""

    nav_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="de" lang="de">
  <head>
    <title>Inhalt</title>
  </head>
  <body>
    <nav epub:type="toc" id="toc">
      <h1>Inhalt</h1>
      <ol>
        <li><a href="briefing.xhtml">{html.escape(title)}</a></li>
      </ol>
    </nav>
  </body>
</html>
"""

    package_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="de">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:language>de</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="briefing" href="briefing.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="styles.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="briefing"/>
  </spine>
</package>
"""

    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

    css = """body { font-family: serif; line-height: 1.5; margin: 5%; }
h1, h2, h3, h4 { line-height: 1.2; margin-top: 1.4em; margin-bottom: 0.5em; }
h1 { font-size: 1.8em; }
h2 { font-size: 1.5em; }
h3 { font-size: 1.25em; }
h4 { font-size: 1.1em; }
p { margin: 0 0 0.9em 0; }
.cover { margin-bottom: 2em; }
.date, .intro { color: #555; }
.entry, .transition { margin-bottom: 1.4em; }
"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("META-INF/container.xml", container_xml, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", package_opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/briefing.xhtml", briefing_xhtml, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/styles.css", css, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def markdown_to_flowables(text: str, styles: dict, font_name: str) -> list:
    """Wandelt eine Zusammenfassung in ReportLab-Flowables um."""
    text = normalize_unicode(text)
    # Defense-in-depth: Doppelte Heading-Echos final entfernen, falls sie
    # vorher nicht gestripped wurden.
    text = _strip_duplicate_heading_echo(text)
    flowables = []
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if not line:
            flowables.append(Spacer(1, 2 * mm))
            i += 1
            continue

        if line.startswith("EYEBROW:"):
            eyebrow_text = escape_xml(line[8:].strip())
            flowables.append(Paragraph(eyebrow_text, styles["eyebrow"]))
            i += 1
            continue

        if line.startswith("#### "):
            heading_text = escape_xml(line[5:].strip())
            flowables.append(Paragraph(heading_text, styles["h4"]))
            i += 1
            continue

        if line.startswith("### "):
            heading_text = escape_xml(line[4:].strip())
            flowables.append(Paragraph(heading_text, styles["h3"]))
            i += 1
            continue

        if line.startswith("## "):
            heading_text = escape_xml(line[3:].strip())
            flowables.append(Paragraph(heading_text, styles["h3"]))
            i += 1
            continue

        if line.startswith("# "):
            heading_text = escape_xml(line[2:].strip())
            flowables.append(Paragraph(heading_text, styles["h3"]))
            i += 1
            continue

        bold_match = re.match(r'^\*\*(.+?)\*\*\s*$', line)
        if bold_match:
            heading_text = escape_xml(bold_match.group(1))
            flowables.append(Paragraph(heading_text, styles["h4"]))
            i += 1
            continue

        if line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            italic_text = escape_xml(line.strip("*").strip())
            flowables.append(Paragraph(italic_text, styles["italic"]))
            i += 1
            continue

        # Inline-Kursiv (*text*) im Fließtext: Sterne entfernen, aber KEIN
        # <i>-Tag setzen. Inline-Tags fragmentieren den PDF-Textlayer und
        # verursachen TTS-Artefakte (Wortwiederholungen, Sprachenwechsel).
        # Ganze Zeilen in Kursiv (z.B. Einordnung) werden oben separat
        # als eigener Paragraph mit italic-Style gerendert — das ist safe.
        para_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)   # **bold** → bold
        para_text = re.sub(r'\*([^*]+)\*', r'\1', para_text)  # *italic* → italic
        # Safety net: Einzelne übrig gebliebene * entfernen (z.B. bei Edge Cases)
        para_text = para_text.replace('*', '')
        para_text = escape_xml(para_text)
        flowables.append(Paragraph(para_text, styles["body"]))
        i += 1

    return flowables


class TaggedDocTemplate(SimpleDocTemplate):
    """SimpleDocTemplate-Erweiterung mit /Lang und /MarkInfo für Tagged PDF.
    
    Setzt PDF-Metadaten, die TTS-Engines (ElevenReader etc.) brauchen:
    - /Lang (de-DE): Verhindert Sprachenwechsel (z.B. plötzlich Chinesisch)
    - /MarkInfo: Signalisiert logische Dokumentstruktur
    """

    def _endBuild(self):
        """Setzt PDF-Metadaten vor dem finalen Schreiben."""
        from reportlab.pdfbase.pdfdoc import PDFDictionary
        try:
            cat = self.canv._doc._catalog
            # /Lang als PDFString – wird von format() aus __Defaults__ gelesen
            cat.Lang = '(de-DE)'
            # /MarkInfo als PDFDictionary mit /Marked true
            cat.MarkInfo = PDFDictionary({"Marked": "true"})
        except Exception:
            pass  # Worst case: PDF ohne Tags, aber immerhin lesbar
        super()._endBuild()


def create_pdf(
    sections: List[dict],
    output_path: str,
    generated_at: datetime.datetime,
    document_title: str = "Audio-Briefing",
):
    """Erzeugt die finale PDF aus einer Liste von Sections."""
    font_name = register_fonts()
    styles = build_styles(font_name)

    # PDF-Titel mit Datum vorne, damit ElevenReader & Co. die Briefings sofort zuordnen können
    _WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    _MONTHS_DE = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    weekday = _WEEKDAYS_DE[generated_at.weekday()]
    month_name = _MONTHS_DE[generated_at.month]
    date_spoken = f"{weekday}, {generated_at.day}. {month_name} {generated_at.year}"
    # Titel-Mapping: Alte interne Bezeichnungen → neue benutzerfreundliche Anzeige
    _TITLE_MAP = {
        "Audio-Briefing": "Tagesbriefing — Vollversion",
        "Audio-Briefing (Erzählmodus)": "Tagesbriefing — Erzählmodus",
        "Geniale Zusammenfassung": "Tagesbriefing — Kompaktfassung (Standard)",
        "Geniale Zusammenfassung (Lang)": "Tagesbriefing — Kompaktfassung (Lang)",
        "Geniale Zusammenfassung (Kurz)": "Tagesbriefing — Kompaktfassung (Kurz)",
    }
    user_facing_title = _TITLE_MAP.get(document_title, document_title)
    display_title = f"{date_spoken} — {user_facing_title}"

    doc = TaggedDocTemplate(
        output_path, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2.05 * cm, bottomMargin=2.1 * cm,
        title=display_title,
        author="Audio-Briefing",
        subject=user_facing_title,
    )

    story = []

    today = generated_at.strftime("%d.%m.%Y")
    created_at = f"Erstellt um {generated_at.strftime('%H:%M:%S')} Uhr"
    story.append(Paragraph("DIGITALES AUDIO-BRIEFING", styles["eyebrow"]))
    story.append(Paragraph(escape_xml(display_title), styles["title"]))
    story.append(Paragraph(f"{today} · {created_at}", styles["date"]))
    story.append(HRFlowable(
        width="100%", thickness=1.1, color=HexColor("#9ad9b3"),
        spaceBefore=1.5 * mm, spaceAfter=6.5 * mm, lineCap='round'
    ))

    def draw_page_chrome(canvas, pdf_doc):
        canvas.saveState()
        width, height = A4
        # Cremefarbener Hintergrund (Vektor-Rechteck, kein Bild → 0 Bytes extra)
        canvas.setFillColor(HexColor("#FDFBF7"))
        canvas.rect(0, 0, width, height, stroke=0, fill=1)
        left_x = pdf_doc.leftMargin
        right_x = width - pdf_doc.rightMargin
        line_y = pdf_doc.bottomMargin - 2.5 * mm
        text_y = pdf_doc.bottomMargin - 7.5 * mm
        font_regular = font_name if font_name != "Helvetica" else "Helvetica"
        canvas.setStrokeColor(HexColor("#d6e9dd"))
        canvas.setLineWidth(0.6)
        canvas.line(left_x, line_y, right_x, line_y)
        canvas.setFont(font_regular, 8)
        canvas.setFillColor(HexColor("#60756b"))
        canvas.drawString(left_x, text_y, normalize_unicode(display_title))
        canvas.drawRightString(right_x, text_y, f"Seite {canvas.getPageNumber()}")
        canvas.restoreState()

    for section in sections:
        if section["type"] == "transition":
            story.append(Spacer(1, 4 * mm))
            story.append(HRFlowable(
                width="100%", thickness=0.7, color=HexColor("#d7eade"),
                spaceBefore=4 * mm, spaceAfter=4 * mm
            ))
            story.append(Paragraph(
                escape_xml(section["content"]), styles["transition"]
            ))
            story.append(Spacer(1, 2 * mm))
        else:
            flowables = markdown_to_flowables(
                _content_with_source_label_pdf(section), styles, font_name
            )
            # Überschrift + Einordnung + erster Absatz zusammenhalten
            # damit eine H3 nicht allein am Seitenende steht
            keep_count = min(4, len(flowables))
            if keep_count >= 2:
                story.append(KeepTogether(flowables[:keep_count]))
                story.extend(flowables[keep_count:])
            else:
                story.extend(flowables)
            story.append(Spacer(1, 2 * mm))
            story.append(HRFlowable(
                width="38%", thickness=0.6, color=HexColor("#cfe3d6"),
                spaceBefore=2 * mm, spaceAfter=4 * mm
            ))

    doc.build(story, onFirstPage=draw_page_chrome, onLaterPages=draw_page_chrome)
    _record_archived_file(output_path)  # für den iCloud-Cleanup (launchd kann nicht listen)


def create_single_markdown_pdf(
    markdown_text: str,
    generated_at: datetime.datetime,
    document_title: str = "Geniale Zusammenfassung",
) -> bytes:
    import tempfile

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        create_pdf(
            [{"type": "article", "content": markdown_text}],
            tmp_path,
            generated_at,
            document_title=document_title,
        )

        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ============================================================
# HAUPTFUNKTION FÜR WEB-APP
# ============================================================

SORT_PROMPT = """Du ordnest Artikel-Überschriften thematischen Kategorien zu.

KATEGORIEN (in dieser Reihenfolge):
1. Lokales
2. Innenpolitik & Gesellschaft
3. Internationale Politik
4. Wirtschaft & Finanzen
5. Technologie & Digitales
6. Wissenschaft & Gesundheit
7. Medien & Kultur
8. Kriminalität & Unglücke
9. Sport
10. Vermischtes

AUFGABE: Du bekommst eine nummerierte Liste von Artikel-Überschriften. Ordne jede Nummer einer Kategorie zu.
LOKAL-REGEL: „Lokales" ist ausschließlich für Themen aus Stadt oder Kreis Tübingen und Stadt oder Kreis Reutlingen. Orte wie Tübingen, Reutlingen, Rottenburg, Mössingen, Metzingen, Pfullingen oder Eningen zählen dazu. Meldungen aus anderen Regionen wie Bodensee, Stuttgart, Ulm oder Freiburg sind NICHT „Lokales", sondern nach Thema oder notfalls als „Vermischtes" einzuordnen.

ANTWORTFORMAT: Gib NUR ein JSON-Objekt zurück. Schlüssel = Artikelnummer als String, Wert = Kategoriename exakt wie oben.
Beispiel: {"0": "Lokales", "1": "Wirtschaft & Finanzen", "2": "Lokales"}

Keine Erklärungen, kein Markdown, nur das JSON-Objekt."""


# Feste Reihenfolge der Kategorien
CATEGORY_ORDER = [
    "Lokales",
    "Innenpolitik & Gesellschaft",
    "Internationale Politik",
    "Wirtschaft & Finanzen",
    "Technologie & Digitales",
    "Wissenschaft & Gesundheit",
    "Medien & Kultur",
    "Kriminalität & Unglücke",
    "Sport",
    "Vermischtes",
]

LOCAL_REGION_MARKERS = (
    "tübingen",
    "tuebingen",
    "reutlingen",
    "rottenburg",
    "mössingen",
    "moessingen",
    "metzingen",
    "pfullingen",
    "eningen",
    "wannweil",
    "gomaringen",
    "dusslingen",
    "dußlingen",
    "kusterdingen",
    "kirchentellinsfurt",
    "pliezhausen",
    "ofterdingen",
    "neustetten",
    "dettenhausen",
    "ammerbuch",
    "starzach",
    "kreis tübingen",
    "kreis tuebingen",
    "kreis reutlingen",
    "landkreis tübingen",
    "landkreis tuebingen",
    "landkreis reutlingen",
)


def _is_home_region_article(content: str) -> bool:
    lowered = normalize_unicode(content).lower()
    return any(marker in lowered for marker in LOCAL_REGION_MARKERS)


_AUDIO_MARKER_RE = re.compile(
    r"^(?:"
    r"(?:Letzter\s+)?Beitrag\s+\d+\s+von\s+\d+"       # „Beitrag 5 von 83" / „Letzter Beitrag 5 von 83"
    r"|Letzter\s+Beitrag,\s+\d+\s+von\s+\d+"           # „Letzter Beitrag, 82 von 83"
    r")\.\s*$",
    re.IGNORECASE,
)


def _is_audio_marker_line(line: str) -> bool:
    """True wenn die Zeile NUR aus einem Audio-Marker wie „Beitrag 5 von 83." besteht."""
    return bool(_AUDIO_MARKER_RE.match(line.strip()))


_TITLE_META_SKIP = {
    "was bleibt", "was bleibt:",
    "weiter geht's", "weiter gehts",
    "ende des briefings", "ende der podcastzusammenfassung",
    "willkommen zum briefing",
}


_TITLE_TRUNCATE_STOPWORDS = frozenset({
    "auf", "in", "an", "bei", "von", "zu", "für", "mit", "ohne", "aus",
    "über", "unter", "nach", "vor", "durch", "gegen", "um", "ab",
    "und", "oder", "aber", "als", "wie", "wenn", "weil",
    "der", "die", "das", "des", "dem", "den",
    "ein", "eine", "einen", "eines", "einem", "einer",
    "im", "am", "vom", "zum", "zur", "beim", "ins",
})


def _smart_title_truncate(text: str, max_len: int = 90) -> str:
    """Kürzt einen Text für die Verwendung als Titel an natürlicher Grenze.

    Schneidet bevorzugt bei Komma/Doppelpunkt/Gedankenstrich, fällt zurück auf
    Wortgrenze. Lässt das Ergebnis nicht auf einer Präposition oder einem
    Artikel enden (sonst wirkt der Titel mitten im Satz abgeschnitten).
    """
    if not text:
        return ""
    text = text.strip().rstrip(' .,;:')
    if len(text) <= max_len:
        return text

    # Erste natürliche Trennung nach Position 30 suchen
    candidate = ""
    for sep in (", ", ": ", "; ", " — ", " – ", " - "):
        idx = text.find(sep, 30)
        if idx != -1 and idx <= max_len:
            candidate = text[:idx]
            break

    if not candidate:
        # Bis zur letzten Wortgrenze vor max_len
        short = text[:max_len]
        last_space = short.rfind(" ")
        candidate = text[:last_space] if last_space > 40 else text[:max_len]

    # Trailing-Stopwords (Präpositionen, Artikel) abziehen, bis ein Inhaltswort am Ende steht
    while candidate:
        m = re.search(r"\s(\S+)$", candidate)
        if not m:
            break
        last_word = m.group(1).rstrip(",.;:").lower()
        if last_word in _TITLE_TRUNCATE_STOPWORDS:
            candidate = candidate[: m.start()].rstrip()
            continue
        break

    return candidate.rstrip(" ,;:") or text[:max_len]


def _synthesize_title_from_einordnung(text: str) -> str:
    """Aus einer Einordnung wie „Ein Kommentar beschreibt X" einen kurzen Titel bauen.

    Schneidet an natürlichen Satzgrenzen, nicht hart bei 8 Wörtern. Verhindert
    dass der Titel mitten im Satz auf einer Präposition endet (sonst landet der
    Rest des Satzes als unsauberer Body-Anfang).
    """
    if not text:
        return ""
    text = text.strip().strip('*').strip()
    m = re.match(
        r'^(?:Ein|Eine|Der|Die|Das|Eines|Diese[rsn]?|Dieser)\s+(\w+)\s+(?:beschreibt|zeigt|erklärt|analysiert|beleuchtet|berichtet|untersucht|liefert|stellt|bringt|öffnet|wirft|gibt|fragt|nimmt)\s+(.+?)\s*\.?\s*$',
        text, re.IGNORECASE,
    )
    if m:
        type_word = m.group(1)
        rest = m.group(2).rstrip(' .,;:')
        return f"{type_word}: {_smart_title_truncate(rest, max_len=80)}"[:120]
    return _smart_title_truncate(text.rstrip(' .,;:'), max_len=90)[:120]


def _looks_like_einordnung(text: str) -> bool:
    """Erkennt Einordnungs-/Untertitel-Zeilen (Aussagesätze, keine echten Titel)."""
    if not text:
        return False
    # Sehr lang und endet mit Punkt → typisch für Einordnung
    if len(text) >= 70 and text.rstrip().endswith('.'):
        return True
    # Beginnt mit „Ein/Eine/Der/Die/Das" + Verb („Ein Kommentar beschreibt…")
    if re.match(r'^(Ein|Eine|Der|Die|Das|Eines|Diese[rsn]?|Dieser)\s+\w+\s+(beschreibt|zeigt|erklärt|analysiert|beleuchtet|berichtet|untersucht|nimmt|liefert|stellt|bringt|öffnet|wirft|gibt|fragt)', text, re.IGNORECASE):
        return True
    return False


def _extract_title(content: str) -> str:
    """Extrahiert die erste echte Überschrift/Zeile — Audio-Marker, Meta und Einordnungs-
    Zeilen (in Kursiv oder klassische Aussage-Sätze) werden übersprungen.
    Wenn KEINE echte Titelzeile da ist, wird aus dem Einordnungs-Kursiv-Block ein
    Titel synthetisiert (statt zur ersten Body-Zeile zu fallen)."""
    in_italics_block = False
    italic_buf: list = []
    italic_collected = []  # Gesammelte Kursiv-Einordnungen (für Fallback)
    for line in content.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if _is_audio_marker_line(line):
            continue
        # Mehrzeiliger Kursiv-Block-Start
        if line.startswith('*') and not line.startswith('**'):
            stripped_inner = line.strip('*').strip()
            if line.endswith('*') and not line.endswith('**') and len(line) > 2:
                # Einzeilige Kursiv-Zeile
                if stripped_inner:
                    italic_collected.append(stripped_inner)
                in_italics_block = False
            else:
                in_italics_block = True
                italic_buf = [stripped_inner] if stripped_inner else []
            continue
        if in_italics_block:
            if line.endswith('*') and not line.endswith('**'):
                italic_buf.append(line.rstrip('*').strip())
                if italic_buf:
                    italic_collected.append(" ".join(italic_buf))
                in_italics_block = False
                italic_buf = []
            else:
                italic_buf.append(line)
            continue
        # Einzeilige Kursiv-Zeile
        m_italic = re.match(r'^\*([^*\n]+)\*\s*$', line)
        if m_italic and '**' not in line:
            italic_collected.append(m_italic.group(1).strip())
            continue
        # Markdown-Heading entfernen
        clean = re.sub(r'^#{1,4}\s*', '', line)
        # Vollständige Fett-Umschließung (** … **) entfernen
        clean = re.sub(r'^\*\*(.+?)\*\*$', r'\1', clean).strip()
        # Führendes ** (wenn trailing ** durch PDF-Umbruch fehlt) entfernen
        if clean.startswith('**'):
            clean = clean[2:].strip()
        # Trailing ** auch entfernen falls vorhanden
        if clean.endswith('**'):
            clean = clean[:-2].strip()
        # Einzelne *…*-Umschließung (Kursiv) auch entfernen
        m = re.match(r'^\*([^*\n]+)\*\s*$', clean)
        if m:
            clean = m.group(1).strip()
        # Meta-Zeilen („Was bleibt:", „Weiter geht's" etc.) sind keine Titel
        if clean.lower().strip(' :.') in _TITLE_META_SKIP:
            continue
        # Nach dem Strippen könnte die Zeile ein Audio-Marker sein (z.B. „### Beitrag 5 von 83.")
        if _is_audio_marker_line(clean):
            continue
        # Aussage-Sätze (typische Einordnungen) sind ebenfalls keine Titel — überspringen
        if _looks_like_einordnung(clean):
            continue
        if len(clean) > 5:
            # Wenn vorher eine Kursiv-Einordnung gesammelt wurde aber KEIN ###-Header
            # davor stand, ist „clean" die erste Body-Zeile. Die Kursiv-Einordnung ist
            # informativer — daraus einen Titel synthetisieren.
            if italic_collected and not line.startswith('#'):
                synth = _synthesize_title_from_einordnung(italic_collected[0])
                if synth:
                    return synth
            return clean[:120]
    # Fallback 1: Echte Titel-Zeile fehlt — versuche aus der Einordnung einen kurzen Titel
    # zu extrahieren. „Ein Kommentar beschreibt X" → „Kommentar: X (gekürzt)".
    # Sammelt mehrzeilige Einordnungen (Kursiv-Block) zu einem zusammenhängenden Text.
    raw_lines = [l.strip() for l in content.strip().splitlines() if l.strip()]
    # Kursiv-Block am Anfang aggregieren
    aggregated_lines = []
    block_buf: list = []
    in_block = False
    for line in raw_lines:
        if _is_audio_marker_line(line):
            continue
        if not in_block and line.startswith('*') and not line.startswith('**'):
            if line.endswith('*') and not line.endswith('**') and len(line) > 2:
                # Einzeiliger Kursiv-Block
                aggregated_lines.append(line.strip('*').strip())
            else:
                in_block = True
                block_buf = [line.lstrip('*').strip()]
            continue
        if in_block:
            if line.endswith('*') and not line.endswith('**'):
                block_buf.append(line.rstrip('*').strip())
                aggregated_lines.append(" ".join(block_buf))
                in_block = False
                block_buf = []
            else:
                block_buf.append(line)
            continue
        aggregated_lines.append(line)
    if in_block and block_buf:
        aggregated_lines.append(" ".join(block_buf))

    for line in aggregated_lines:
        if _is_audio_marker_line(line):
            continue
        clean = re.sub(r'^#{1,4}\s*', '', line)
        clean = re.sub(r'^\*\*(.+?)\*\*$', r'\1', clean).strip()
        m = re.match(r'^\*([^*\n]+)\*\s*$', clean)
        if m:
            clean = m.group(1).strip()
        if clean.lower().strip(' :.') in _TITLE_META_SKIP:
            continue
        if _is_audio_marker_line(clean):
            continue
        if len(clean) <= 5:
            continue
        # Pattern: „Ein/Eine/Der/Die/Das X beschreibt/zeigt/… Y." → „X: Y (gekürzt)"
        m2 = re.match(
            r'^(?:Ein|Eine|Der|Die|Das)\s+(\w+)\s+(?:beschreibt|zeigt|erklärt|analysiert|beleuchtet|berichtet|untersucht|liefert|stellt|bringt|öffnet|wirft|gibt|fragt|nimmt)\s+(.+?)\s*\.?\s*$',
            clean, re.IGNORECASE,
        )
        if m2:
            type_word = m2.group(1)
            rest = m2.group(2).rstrip(' .')
            rest_words = rest.split()
            short_rest = " ".join(rest_words[:8])
            return f"{type_word}: {short_rest}"[:120]
        # Sonst: einfach Aussagesatz auf 8 Wörter kürzen
        words = clean.rstrip('.').split()
        return " ".join(words[:8])[:120]
    # Notfall-Fallback: gib die ersten 120 Zeichen zurück, aber NIE einen Audio-Marker
    fallback = content.strip()[:120]
    if _is_audio_marker_line(fallback):
        return ""
    return fallback


def _sorting_model_candidates(model: str) -> List[str]:
    """Liefert ein paar robuste Fallback-Modelle für die Sortierung."""
    if model.startswith("gpt-"):
        candidates = [model, "gpt-5.4-mini", "gpt-4.1-mini"]
    else:
        candidates = [model, "claude-haiku-4-5-20251001"]

    result = []
    for candidate in candidates:
        if candidate not in result:
            result.append(candidate)
    return result


def _sort_articles_by_theme(client, articles: list, model: str) -> tuple:
    """
    Sortiert Artikel thematisch und fügt Kategorie-Übergänge ein.
    Gibt die sortierten Sections plus einen Erfolgsstatus zurück.
    """
    import json

    if len(articles) < 2:
        return articles, True

    # Überschriften extrahieren
    titles = []
    for i, article in enumerate(articles):
        title = _extract_title(article["content"])
        titles.append(f"{i}: {title}")

    titles_text = "\n".join(titles)

    mapping = None
    sort_text = f"Hier sind die Artikel:\n\n{titles_text}"
    for sort_model in _sorting_model_candidates(model):
        try:
            raw = summarize(
                client,
                sort_text,
                SORT_PROMPT,
                model=sort_model,
                max_tokens=2048,
                json_mode=sort_model.startswith("gpt-"),
                timeout_seconds=45.0,
            )
            if not raw:
                continue
            raw = raw.strip()
            raw = re.sub(r'^```json\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            mapping = json.loads(raw)
            if isinstance(mapping, dict) and mapping:
                break
        except Exception as e:
            print(
                f"[sort] Sortierung fehlgeschlagen ({sort_model}): {type(e).__name__}: {e}",
                file=sys.stderr,
            )

    if not isinstance(mapping, dict) or not mapping:
        return articles, False

    # Artikel nach Kategorien gruppieren
    categorized = {}
    for i, article in enumerate(articles):
        category = mapping.get(str(i), "Vermischtes")
        if category == "Lokales" and not _is_home_region_article(article["content"]):
            category = "Vermischtes"
        if category not in categorized:
            categorized[category] = []
        categorized[category].append(article)

    # In fester Reihenfolge zusammenbauen, mit Kategorie-Übergängen
    sorted_sections = []
    for category in CATEGORY_ORDER:
        if category in categorized:
            sorted_sections.append({"type": "transition", "content": category})
            sorted_sections.extend(categorized[category])

    # Falls unbekannte Kategorien existieren
    for category, items in categorized.items():
        if category not in CATEGORY_ORDER:
            sorted_sections.append({"type": "transition", "content": category})
            sorted_sections.extend(items)

    return sorted_sections, True


def _fetch_article_payload(url):
    """Holt und bereinigt den Rohtext eines Artikels."""
    text = fetch_article(url)
    if not text:
        return None
    source_label = source_label_from_url(url)
    text = _strip_source_specific_noise(text, source_label)
    text = _strip_general_boilerplate(text)
    if _looks_like_advertorial(text):
        return None
    return {
        "url": url,
        "source_label": source_label,
        "source_text": text,
    }


def _looks_like_advertorial(text: str) -> bool:
    """Erkennt Anzeigen/Advertorials anhand typischer Marker am Textanfang."""
    if not text:
        return False
    head = text.lstrip()[:400].lower()
    if not head:
        return False
    ad_markers = (
        "anzeige:", "anzeige ·", "anzeige —", "anzeige -", "anzeige\n",
        "werbung:", "werbung ·",
        "sponsored content", "sponsored post", "sponsored:",
        "advertorial", "verlagspartnerschaft", "verlagspartner:",
        "promotion:", "bezahlte anzeige",
    )
    return any(head.startswith(marker) for marker in ad_markers)


def _summarize_article_payload(client, payload, model, compact: bool = False):
    """Fasst einen bereits geholten Artikel zusammen."""
    source_label = payload["source_label"]
    text = payload["source_text"]
    summary = _summarize_article_from_source(client, text, model, compact=compact)
    if summary:
        return {
            "type": "article",
            "content": summary,
            "source_label": source_label,
            "_source_text": text,
        }
    return None


def _article_source_dedup_key(section: Optional[dict]) -> str:
    """Konservativer Dedupe-Key für inhaltsgleiche URL-Artikel."""
    if not section:
        return ""
    source_text = section.get("_source_text", "") or ""
    normalized = _normalize_text_for_dedup(source_text)
    if len(normalized) < 200:
        return ""
    return normalized


def _dedup_header(text: str) -> str:
    """Entfernt doppelte Titel/Einordnungen am Textanfang.
    
    GPT-5.x neigt dazu, beim Formatieren einen neuen Titel + Einordnung
    vor den bereits vorhandenen zu setzen. Diese Funktion erkennt das
    und entfernt die Duplikate.
    """
    lines = text.split("\n")
    # Sammle die ersten 8 non-empty Zeilen mit Index
    first_lines = []
    for idx, line in enumerate(lines):
        if line.strip():
            first_lines.append((idx, line.strip()))
        if len(first_lines) >= 8:
            break

    if len(first_lines) < 4:
        return text

    def _normalize(s):
        """Entfernt Markdown-Marker zum Vergleich."""
        s = re.sub(r'^#{1,4}\s*', '', s)
        s = s.strip('*').strip()
        return s.lower()

    # Suche nach Duplikat-Paaren in den ersten 8 Zeilen
    remove_indices = set()
    for i in range(len(first_lines)):
        for j in range(i + 1, len(first_lines)):
            a = _normalize(first_lines[i][1])
            b = _normalize(first_lines[j][1])
            if a and b and len(a) > 10:
                # Exakter Match oder einer ist Substring des anderen
                if a == b or (a in b) or (b in a):
                    # Erste Vorkommnisse entfernen (das Duplikat ist oben)
                    remove_indices.add(first_lines[i][0])
                    break

    if not remove_indices:
        return text

    new_lines = [line for idx, line in enumerate(lines) if idx not in remove_indices]
    # Leere Zeilen am Anfang bereinigen
    while new_lines and not new_lines[0].strip():
        new_lines.pop(0)
    return "\n".join(new_lines)


def _format_chunk(client, chunk, section_type, model):
    """Formatiert einen Paywall/Podcast-Chunk (für parallele Verarbeitung)."""
    if _looks_like_briefing_summary(chunk):
        return {"type": section_type, "content": _normalize_existing_briefing_markdown(chunk.strip())}
    formatted = summarize(client, chunk, FORMAT_PROMPT, model)
    if formatted:
        formatted = _normalize_existing_briefing_markdown(_dedup_header(formatted))
        return {"type": section_type, "content": formatted}
    return {"type": section_type, "content": chunk}


def _looks_like_briefing_summary(chunk: str) -> bool:
    """Erkennt fertige Briefings im Titel/Einordnung/Was-bleibt-Format."""
    stripped = chunk.strip()
    lowered = stripped.lower()

    if stripped.startswith("### "):
        return True
    if "#### was bleibt" in lowered:
        return True
    if "\nwas bleibt:" in lowered and re.search(r"weiter geht[\'\u2019]s\.?\s*$", stripped, flags=re.IGNORECASE):
        return True
    return False


def _normalize_copy_line(raw_line: str) -> str:
    """Normalisiert eine kopierte Zeile aus dem Browser."""
    line = raw_line.replace("\xa0", " ")
    return re.sub(r"\s+", " ", line).strip()


def _is_byline_line(line: str) -> bool:
    if line == "Von" or line.startswith("Von "):
        return True
    return bool(re.match(r"^Von\s+\d+\S*", line))


def _is_date_line(line: str) -> bool:
    return bool(
        re.search(r"\b\d{2}\.\d{2}\.\d{4}\b", line)
        or re.search(r"\b\d{1,2}:\d{2}\s*Uhr\b", line)
    )


def _is_footer_marker(line: str) -> bool:
    footer_terms = (
        "© ",
        "Privatsphäre",
        "Karriere",
        "AGB",
        "AGBs",
        "Datenschutz",
        "Mediadaten",
        "Erklärung zur Barrierefreiheit",
        "Kündigung",
        "Impressum",
        "Mehr zum Thema",
        "Zählpixel",
        "Einstellungen",
        "Mehr von swp.de",
        "Support",
        "Rechtliches",
        "Aboshop",
        "E-Paper",
        "Newsletter",
        "Push",
    )
    return any(term in line for term in footer_terms)


def _is_non_content_line(line: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", line.lower())
    if line in {
        "Menü schließen",
        "Mein Konto",
        "Abmelden",
        "Meine SWP",
        "Jetzt in der App anhören",
        "Zusammenfassung Neu",
        "gea.de",
        "Mein Profil",
        "Dem GEA folgen & informiert bleiben",
        "Service Abo-Shop Anzeigen Vorteilswelt E-Paper",
        "Erscheinungsbild",
        "Mehr von swp.de",
        "Support",
        "Rechtliches",
        "Privatsphäre",
    }:
        return True
    if line.startswith("Startseite"):
        return True
    if line.startswith("Meine Nachrichten "):
        return True
    if compact.startswith("meinenachrichten") and ("reutlingen" in compact or "neckaralb" in compact):
        return True
    if compact.startswith("serviceaboshopanzeigenvorteilsweltepaper"):
        return True
    if compact == "meinprofil":
        return True
    if compact.startswith("startseite"):
        return True
    if "demgeafolgen" in compact and "informiertbleiben" in compact:
        return True
    if line in {"kostenpflichtig", "Leserfrage", "Polizeimeldung", "Übersicht"}:
        return True
    return False


def _is_credit_line(line: str) -> bool:
    if not line:
        return False
    if re.search(r"(?:/AFP|/dpa|/AP|/Getty|/IMAGO|/REUTERS)\b", line, flags=re.IGNORECASE):
        return True
    if line.startswith(("Foto:", "Bild:")):
        return True
    if re.search(r"\bFoto:\s", line):
        return True

    letters = [char for char in line if char.isalpha()]
    if letters:
        upper_ratio = sum(1 for char in letters if char.isupper()) / len(letters)
        if upper_ratio > 0.75 and len(line.split()) <= 4:
            return True
    return False


def _looks_like_content_line(line: str) -> bool:
    words = line.split()
    if len(words) < 5:
        return False
    return line.endswith((".", "!", "?", ".”", "!”", "?”")) or len(line) >= 70


def _looks_like_teaser_label(line: str) -> bool:
    words = line.split()
    if line in {"Gesundheit", "Verkehr", "Politik", "Wohnungsbau", "Polizei", "Blaulicht", "Sport", "Übersicht", "Leserfrage", "kostenpflichtig"}:
        return True
    return 1 <= len(words) <= 5 and len(line) <= 45 and not re.search(r"[.!?]", line)


def _looks_like_teaser_headline(line: str) -> bool:
    words = line.split()
    return 3 <= len(words) <= 16 and len(line) <= 120 and not line.endswith(".")


def _looks_like_meta_or_promo_line(line: str) -> bool:
    if not line:
        return False
    if re.search(r"^Von\s+\d+\S*", line):
        return True
    if line in {"Logo SPD", "Stadt Reutlingen", "Nutzungsbedingungen Datenschutz Impressum Kontakt Karriere Abonnement kündigen"}:
        return True
    if re.match(r"^\d{3,}-\d{6,}$", line):
        return True
    return False


def _merge_header_lines(lines: List[str]) -> List[str]:
    """Fasst mehrzeilige Headlines wie 'Ressort / : / Titel' zusammen."""
    if len(lines) >= 3 and lines[1] == ":":
        return [f"{lines[0]}: {lines[2]}"] + lines[3:]
    if len(lines) >= 2 and lines[0].endswith(":"):
        return [f"{lines[0]} {lines[1]}"] + lines[2:]
    return lines


def _strip_footer_echo(body_lines: List[str], header_lines: List[str]) -> List[str]:
    """Entfernt wiederholte Titelreste kurz vor dem Footer."""
    if not body_lines or not header_lines:
        return body_lines

    title = header_lines[0].strip().lower()
    while body_lines and body_lines[-1].strip().lower() == title:
        body_lines.pop()

    if len(body_lines) >= 2:
        tail = body_lines[-1].strip().lower()
        prev = body_lines[-2].strip()
        if tail == title and 1 <= len(prev.split()) <= 4 and len(prev) <= 40:
            body_lines = body_lines[:-2]

    while body_lines:
        tail = body_lines[-1].strip()
        if 1 <= len(tail.split()) <= 4 and len(tail) <= 40 and not re.search(r"[.!?]", tail):
            body_lines.pop()
            continue
        break

    return body_lines


def _normalize_copied_article(chunk: str) -> str:
    """Bereinigt roh kopierten Browser-Text vor der Zusammenfassung."""
    lines = []
    for raw_line in chunk.splitlines():
        line = _normalize_copy_line(raw_line)
        if not line:
            continue
        if re.fullmatch(r"(?:-{2,}|={2,}|artikel ende|m{3,})", line, flags=re.IGNORECASE):
            continue
        if _looks_like_meta_or_promo_line(line):
            continue
        lines.append(line)

    if not lines:
        return chunk.strip()

    meta_idx = None
    for idx, line in enumerate(lines):
        if _is_byline_line(line) or _is_date_line(line):
            meta_idx = idx
            break

    header_lines = []
    body_start = 0
    if meta_idx is not None:
        header_start = max(0, meta_idx - 4)
        header_lines = lines[header_start:meta_idx]
        body_start = meta_idx

    header_lines = [line for line in _merge_header_lines(header_lines) if not _is_non_content_line(line)]

    body_lines = []
    seen_body = False
    i = body_start
    while i < len(lines):
        line = lines[i]

        if _is_footer_marker(line):
            break

        if not seen_body:
            if _is_byline_line(line) or _is_date_line(line) or _is_non_content_line(line) or _looks_like_meta_or_promo_line(line):
                i += 1
                continue
            if _is_credit_line(line):
                i += 1
                continue
            if i + 1 < len(lines) and _is_credit_line(lines[i + 1]):
                i += 2
                continue

        if i + 2 < len(lines):
            first, second, third = lines[i], lines[i + 1], lines[i + 2]
            if _looks_like_teaser_label(first) and second == ":" and _looks_like_teaser_headline(third):
                i += 3
                continue

        if _is_non_content_line(line) or _is_credit_line(line) or _looks_like_meta_or_promo_line(line):
            i += 1
            continue

        if not seen_body:
            if _looks_like_content_line(line):
                seen_body = True
            else:
                i += 1
                continue

        body_lines.append(line)
        i += 1

    body_lines = _strip_footer_echo(body_lines, header_lines)
    cleaned_parts = header_lines + body_lines
    return "\n".join(cleaned_parts).strip()


def _paywall_failure_label(chunk: str, index: int) -> str:
    """Baut eine kurze, UI-taugliche Fehlerbezeichnung für fehlgeschlagene Rohartikel."""
    preview = re.sub(r"\s+", " ", chunk).strip()
    if preview:
        preview = preview[:80]
        return f"Paywall {index + 1}: {preview}"
    return f"Paywall {index + 1}"


def _process_paywall_chunk(client, chunk, model, source_label_hint: Optional[str] = None, compact: bool = False):
    """Verarbeitet Paywall-Input: fertige Briefings formatieren, Rohtexte zusammenfassen."""
    source_label = source_label_from_text(chunk)
    if (not source_label or source_label == "Quelle unbekannt") and source_label_hint:
        source_label = source_label_hint
    if _looks_like_briefing_summary(chunk):
        cleaned_summary_chunk = _strip_source_specific_noise(chunk, source_label)
        section = _format_chunk(client, cleaned_summary_chunk, "manual", model)
        if source_label:
            section["source_label"] = source_label
        section["_source_text"] = cleaned_summary_chunk
        return {"ok": True, "section": section, "mode": "formatted"}

    cleaned_chunk = _normalize_copied_article(chunk)
    cleaned_chunk = _strip_source_specific_noise(cleaned_chunk, source_label)
    cleaned_chunk = _strip_general_boilerplate(cleaned_chunk)
    summary = _summarize_article_from_source(client, cleaned_chunk, model, compact=compact)
    if summary:
        section = {"type": "manual", "content": summary}
        if source_label:
            section["source_label"] = source_label
        section["_source_text"] = cleaned_chunk
        return {
            "ok": True,
            "section": section,
            "mode": "raw",
        }
    return {
        "ok": False,
        "section": None,
        "mode": "raw",
        "failed_input": cleaned_chunk or chunk,
    }


# Anzahl paralleler API-Calls
# OpenAI jetzt etwas mutiger, aber noch bewusst konservativ.
MAX_WORKERS_ANTHROPIC = 5
MAX_WORKERS_OPENAI = 3
ARTICLE_FETCH_WORKERS = 8
DEFAULT_MODEL_TIMEOUT_SECONDS = 90.0
CONTENT_CHECK_REQUEST_TIMEOUT_SECONDS = 75.0
CONTENT_CHECK_BATCH_BUFFER_SECONDS = 20.0
CONTENT_CHECK_PROGRESS_START = 0.80
CONTENT_CHECK_PROGRESS_END = 0.92


def _max_workers(model: str) -> int:
    """Gibt die passende Anzahl paralleler Worker für den Provider zurück."""
    return MAX_WORKERS_OPENAI if model.startswith("gpt-") else MAX_WORKERS_ANTHROPIC


def _article_fetch_workers(url_count: int) -> int:
    """Netzwerk-Fetches dürfen etwas breiter parallel laufen als Modellaufrufe."""
    return max(1, min(ARTICLE_FETCH_WORKERS, url_count))


# =====================================================================
# Watchdog mit Provider-Switch
# =====================================================================
# Bei langlaufenden LLM-Calls (Erzähl-Modus, Geniale Zusammenfassung)
# kann es passieren dass die API-Verbindung hängt (Cloudflare-Hänger,
# API-Outage). Der Wrapper unten versucht den Call mit dem primären
# Modell — bei Timeout/Fehler wird automatisch auf den anderen Provider
# (OpenAI ↔ Anthropic) gewechselt und nochmal versucht.
#
# Das hat den Vorteil dass:
# - Hängende API-Calls nach klarem Timeout abgebrochen werden
# - Automatischer Fallback-Provider die Qualität erhält
# - Keine deterministische Notlösung ohne LLM nötig

# Pro Provider-Versuch: max. 5 Min — danach wird der nächste probiert.
WATCHDOG_PER_ATTEMPT_TIMEOUT = 300.0  # Sekunden (= 5 Minuten)

# Sticky-Switch: Wenn ein Provider in DIESER Session schon Quota-/Auth-Fehler hatte,
# wird er für künftige Calls direkt übersprungen (kein erneutes 5-Min-Timeout).
_DEAD_PROVIDERS: set = set()


def reset_dead_providers():
    _DEAD_PROVIDERS.clear()


def _provider_for_model(model: str) -> str:
    if model.startswith("gpt-"):
        return "openai"
    if model.startswith("claude-"):
        return "anthropic"
    return "unknown"


def _alt_model_for(model: str) -> Optional[str]:
    """Wählt einen Fallback-Modell-Namen vom anderen Provider."""
    if not model:
        return None
    if model.startswith("gpt-"):
        # OpenAI hängt → auf Claude wechseln
        return "claude-sonnet-5"
    if model.startswith("claude-"):
        # Claude hängt → auf GPT wechseln
        return "gpt-5.4-mini"
    return None


def summarize_with_fallback(
    client,
    text: str,
    prompt: str,
    model: str,
    api_keys: Optional[dict] = None,
    max_tokens: int = 8192,
    json_mode: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Wie summarize(), aber mit Watchdog + automatischem Provider-Switch.

    Args:
        client: Bereits gebauter API-Client für `model`.
        api_keys: Dict mit „openai" und „anthropic" Keys, damit beim Switch
            ein neuer Client gebaut werden kann. Wenn None: kein Switch möglich.
        progress_callback: Wird mit Statusmeldungen aufgerufen
            (z.B. „Fallback auf Claude wegen Timeout").
    """
    def _notify(msg: str):
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    primary_provider = _provider_for_model(model)
    primary_dead = primary_provider in _DEAD_PROVIDERS

    # Versuch 1: primäres Modell — überspringen wenn schon tot
    if primary_dead:
        _notify(f"{primary_provider}: bereits in dieser Session als nicht-verfügbar markiert — skip")
    else:
        try:
            result = summarize(
                client, text, prompt, model=model,
                max_tokens=max_tokens, json_mode=json_mode,
                timeout_seconds=WATCHDOG_PER_ATTEMPT_TIMEOUT,
            )
            if result:
                return result
            _notify(f"{model}: leere Antwort, Versuch 2 mit anderem Provider…")
        except APIQuotaExhaustedError as exc:
            _DEAD_PROVIDERS.add(primary_provider)
            _notify(f"⚠️ {exc.provider}: Guthaben/Rate Limit erreicht — switche zum anderen Provider (sticky)")
        except Exception as exc:
            _notify(f"{model}: Fehler/Timeout ({type(exc).__name__}), Versuch 2 mit anderem Provider…")

    # Versuch 2: Fallback-Provider
    alt_model = _alt_model_for(model)
    if not alt_model or not api_keys:
        return None

    alt_key = api_keys.get("anthropic") if alt_model.startswith("claude-") else api_keys.get("openai")
    if not alt_key:
        return None

    try:
        alt_client = _build_client(alt_key, alt_model)
        result = summarize(
            alt_client, text, prompt, model=alt_model,
            max_tokens=max_tokens, json_mode=json_mode,
            timeout_seconds=WATCHDOG_PER_ATTEMPT_TIMEOUT,
        )
        # Cost-Tracker zusammenführen
        try:
            primary_tracker = getattr(client, "_briefing_cost_tracker", None)
            alt_tracker = getattr(alt_client, "_briefing_cost_tracker", None)
            if primary_tracker and alt_tracker:
                primary_tracker.merge(alt_tracker)
        except Exception:
            pass
        return result
    except APIQuotaExhaustedError as exc:
        alt_provider = _provider_for_model(alt_model)
        _DEAD_PROVIDERS.add(alt_provider)
        _notify(f"⚠️ {exc.provider}: Auch hier Guthaben aufgebraucht. Briefing kann nicht erzeugt werden — bitte Guthaben aufladen.")
        return None
    except Exception:
        return None


def _build_client(api_key: str, model: str):
    """Erstellt den passenden API-Client inklusive Cost-Tracker."""
    if model.startswith("gpt-"):
        import openai

        client = openai.OpenAI(api_key=api_key)
        client._briefing_cost_tracker = _BriefingCostTracker()
        has_responses = hasattr(client, "responses")
        print(
            f"[briefing] OpenAI-Client erstellt: Modell={model}, "
            f"SDK={openai.__version__}, "
            f"Responses API={'JA' if has_responses else 'NEIN → Chat Completions'}, "
            f"Workers={_max_workers(model)}",
            file=sys.stderr,
        )
        return client

    client = anthropic.Anthropic(api_key=api_key)
    client._briefing_cost_tracker = _BriefingCostTracker()
    return client


def _content_check_batch_timeout(model: str, item_count: int) -> float:
    """Leitet ein Gesamtzeitlimit für den Plausibilitäts-Check her."""
    workers = max(_max_workers(model), 1)
    batches = max(1, (item_count + workers - 1) // workers)
    return max(
        120.0,
        batches * (CONTENT_CHECK_REQUEST_TIMEOUT_SECONDS + 10.0) + CONTENT_CHECK_BATCH_BUFFER_SECONDS,
    )


def _build_recap_input(content_sections: List[dict]) -> str:
    """Baut den Recap-Input aus den bisherigen Inhaltsabschnitten."""
    def _extract_intro(content: str) -> str:
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if line.startswith("*") and line.endswith("*") and not line.startswith("**"):
                return _markdown_line_to_plain(line)
        return ""

    def _extract_what_remains(content: str) -> str:
        lines = content.splitlines()
        capture = False
        parts: List[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if not capture and re.match(r"^#{1,4}\s*Was bleibt:\s*$", line, flags=re.IGNORECASE):
                capture = True
                continue
            if not capture:
                continue
            if not line:
                if parts:
                    parts.append("")
                continue
            if _contains_regular_end_marker(line):
                break
            if _contains_briefing_end_marker(line):
                break
            if _contains_podcast_end_marker(line):
                break
            parts.append(_markdown_line_to_plain(line))
        text = "\n".join(parts).strip()
        return re.sub(r"\n{2,}", "\n", text)

    def _extract_body_excerpt(content: str, max_chars: int = 520) -> str:
        lines = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                lines.append("")
                continue
            if re.match(r"^#{1,4}\s*", line):
                continue
            if line.startswith("*") and line.endswith("*") and not line.startswith("**"):
                continue
            if _contains_regular_end_marker(line):
                break
            if _contains_briefing_end_marker(line):
                break
            if _contains_podcast_end_marker(line):
                break
            if re.match(r"^#{1,4}\s*Was bleibt:\s*$", line, flags=re.IGNORECASE):
                break
            lines.append(_markdown_line_to_plain(line))
        text = "\n".join(lines).strip()
        text = re.sub(r"\n{2,}", "\n", text)
        return text[:max_chars].strip()

    chunks = []
    for i, section in enumerate(content_sections, 1):
        content = section["content"]
        title = _extract_title(content)
        intro = _extract_intro(content)
        body = _extract_body_excerpt(content)
        was_bleibt = _extract_what_remains(content)
        source_label = section.get("source_label") or (
            "Wetter" if section.get("_weather") else ("Podcast" if section.get("type") == "podcast" else "Quelle unbekannt")
        )
        item_type = (
            "weather" if section.get("_weather") else
            "podcast" if section.get("type") == "podcast" else
            "article"
        )

        block_parts = [
            f"--- Beitrag [{i}] ---",
            f"Typ: {item_type}",
            f"Quelle: {source_label}",
            f"Titel: {title}",
        ]
        if intro:
            block_parts.append(f"Einordnung: {intro}")
        if body:
            block_parts.append(f"Kernabschnitt: {body}")
        if was_bleibt:
            block_parts.append(f"Was bleibt: {was_bleibt}")
        chunks.append("\n".join(block_parts))

    return "\n\n".join(chunks).strip()


def _extract_recap_ids(text: str) -> List[int]:
    return [int(match.group(1)) for match in re.finditer(r"(?m)^\[(\d+)\]\s+", text)]


def _validate_recap_ids(text: str, expected_count: int) -> tuple[bool, str]:
    found = _extract_recap_ids(text)
    counts = defaultdict(int)
    for item_id in found:
        counts[item_id] += 1

    expected = list(range(1, expected_count + 1))
    missing = [item_id for item_id in expected if counts[item_id] == 0]
    duplicates = [item_id for item_id in expected if counts[item_id] > 1]
    extras = [item_id for item_id in sorted(counts) if item_id < 1 or item_id > expected_count]

    ok = not missing and not duplicates and not extras and len(found) == expected_count
    problems = []
    if missing:
        problems.append("fehlend: " + ", ".join(str(item_id) for item_id in missing[:8]))
    if duplicates:
        problems.append("doppelt: " + ", ".join(str(item_id) for item_id in duplicates[:8]))
    if extras:
        problems.append("unerwartet: " + ", ".join(str(item_id) for item_id in extras[:8]))
    return ok, "; ".join(problems)


_GENIUS_SUMMARY_STOPWORDS = {
    "aber", "auch", "aus", "bei", "beim", "beiden", "bereits", "bleibt", "dabei",
    "dann", "dass", "dem", "den", "der", "des", "die", "dies", "diese", "diesem",
    "dieser", "dieses", "doch", "dort", "durch", "eine", "einem", "einen", "einer",
    "eines", "einfach", "einige", "einiges", "ersten", "fuer", "für", "ganze",
    "geht", "genannt", "genau", "gibt", "heute", "hier", "ihre", "ihren", "ihres",
    "ihrem", "ihnen", "immer", "kein", "keine", "konnte", "lässt", "laut", "mehr",
    "nicht", "noch", "oder", "schon", "seine", "seinen", "seiner", "seinem", "sein",
    "sich", "sind", "solche", "soll", "sollen", "sowie", "später", "steht", "teil",
    "thema", "ueber", "über", "um", "und", "unter", "viele", "vom", "von", "vor",
    "während", "war", "waren", "wird", "wurde", "wurden", "zeigt", "zeigen", "zum",
    "zur", "zusammen", "zwischen",
}


def _summary_anchor_tokens(text: str, limit: int = 12) -> List[str]:
    normalized = normalize_unicode(text or "").lower().replace("-", " ")
    tokens = []
    for token in re.findall(r"[a-zäöüß0-9]+", normalized):
        if len(token) < 4 or token.isdigit() or token in _GENIUS_SUMMARY_STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


_BRIEFING_BOILERPLATE_MARKERS = (
    "cookie", "cookies", "consent", "datenschutz", "privatsphäre", "privacy",
    "impressum", "rechtliches", "newsletter", "push", "partnerlink",
    "partnerlinks", "transparenzhinweis", "abo-shop", "epaper", "e-paper",
    "mein profil", "vorteilswelt", "dem gea folgen", "serviceabo-shop",
)


def _detect_briefing_boilerplate_contamination(summary_text: str) -> List[str]:
    lowered = normalize_unicode(summary_text or "").lower()
    issues = []

    # Wenn der Artikel selbst über Datenschutz/Cookies/Privacy handelt, nicht flaggen
    _topic_indicators = (
        "datenschutz-grundverordnung", "dsgvo", "gdpr", "e-privacy",
        "cookie-richtlinie", "cookie-gesetz", "tracking-verbot",
        "datenschutzbeauftragter", "privacy-regulierung",
    )
    is_topic_article = any(ind in lowered for ind in _topic_indicators)

    # Boilerplate-Phrasen die typisch fuer Portal-Reste sind (nicht fuer Artikeltext)
    _boilerplate_phrases = (
        "wir verwenden cookies",
        "diese website verwendet cookies",
        "diese seite nutzt cookies",
        "cookies akzeptieren",
        "alle akzeptieren",
        "cookie-einstellungen verwalten",
        "einwilligung widerrufen",
        "zur datenschutzerklärung",
        "jetzt newsletter abonnieren",
        "melden sie sich für unseren newsletter",
        "push-nachrichten aktivieren",
        "jetzt abo abschließen",
        "jetzt 30 tage kostenlos testen",
        "weiter lesen mit abo",
        "mein profil",
        "vorteilswelt",
        "abo-shop",
    )

    marker_messages = [
        (("transparenzhinweis", "partnerlink", "partnerlinks"), "Der Abschnitt enthält einen Transparenz- oder Partnerlink-Hinweis, der nicht zum eigentlichen Briefingkern gehört."),
        (("cookie-einstellungen", "cookies akzeptieren", "consent-banner"), "Der Abschnitt enthält Cookie- oder Consent-Reste, die nicht in ein Audio-Briefing gehören."),
        (("newsletter abonnieren", "push-nachrichten aktivieren", "mein profil", "vorteilswelt", "abo-shop", "e-paper bestellen"), "Der Abschnitt enthält Navigations-, Shop- oder Service-Reste statt reinen Inhalts."),
    ]

    if not is_topic_article:
        for needles, message in marker_messages:
            if any(needle in lowered for needle in needles):
                issues.append(message)

        # Zusaetzlich: ganze Boilerplate-Saetze erkennen
        for phrase in _boilerplate_phrases:
            if phrase in lowered:
                issues.append(f'Der Abschnitt enthält Portal-Boilerplate: \u201E{phrase}\u201C.')
                break  # Einer reicht als Signal

    return issues


def _extract_source_support_snippets(source_text: str, summary_text: str, issues: List[str], limit: int = 2) -> List[str]:
    normalized_source = normalize_unicode(source_text or "").strip()
    if not normalized_source:
        return []

    query_text = " ".join([summary_text or ""] + [issue or "" for issue in issues])
    query_tokens = _summary_anchor_tokens(query_text, limit=18)
    raw_candidates = re.split(r"\n{2,}|(?<=[.!?])\s+", normalized_source)
    candidates = []
    seen = set()
    for raw in raw_candidates:
        snippet = re.sub(r"\s+", " ", raw).strip()
        if len(snippet) < 24:
            continue
        snippet = snippet[:240].rstrip(" ,;:") + ("..." if len(snippet) > 240 else "")
        if snippet in seen:
            continue
        seen.add(snippet)
        snippet_tokens = set(_summary_anchor_tokens(snippet, limit=20))
        overlap = len(snippet_tokens.intersection(query_tokens))
        boilerplate_boost = 2 if any(marker in snippet.lower() for marker in _BRIEFING_BOILERPLATE_MARKERS) else 0
        if overlap <= 0 and boilerplate_boost == 0:
            continue
        candidates.append((overlap + boilerplate_boost, snippet))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [snippet for _, snippet in candidates[:limit]]


def _extract_recap_entry_map(text: str) -> dict:
    entries = {}
    current_id = None
    buffer = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        match = re.match(r"^\[(\d+)\]\s*(.*)$", line)
        if match:
            if current_id is not None:
                entries[current_id] = " ".join(part.strip() for part in buffer if part.strip()).strip()
            current_id = int(match.group(1))
            buffer = [match.group(2).strip()]
            continue
        if current_id is None:
            continue
        if line.startswith("#### "):
            entries[current_id] = " ".join(part.strip() for part in buffer if part.strip()).strip()
            current_id = None
            buffer = []
            continue
        buffer.append(line.strip())

    if current_id is not None:
        entries[current_id] = " ".join(part.strip() for part in buffer if part.strip()).strip()
    return entries


def _validate_genius_summary_coverage(text: str, content_sections: List[dict]) -> tuple[bool, str]:
    entry_map = _extract_recap_entry_map(text)
    problems = []

    for idx, section in enumerate(content_sections, 1):
        entry_text = entry_map.get(idx, "").strip()
        if len(entry_text) < 24:
            problems.append(f"{idx}: zu kurz")
            continue

        entry_tokens = set(_summary_anchor_tokens(entry_text, limit=40))
        title = _extract_title(section["content"])
        excerpt = _section_plain_excerpt(section["content"], max_chars=260)
        title_tokens = _summary_anchor_tokens(title, limit=6)
        context_tokens = _summary_anchor_tokens(f"{title} {excerpt}", limit=12)

        if section.get("_weather"):
            weather_hits = entry_tokens.intersection({"regen", "schnee", "wind", "kalt", "warm", "sonne", "wetter", "hirschau", "tübingen"})
            if weather_hits or len(entry_tokens.intersection(set(context_tokens))) >= 1:
                continue
            problems.append(f"{idx}: Wetter-Anker fehlt")
            continue

        if section.get("type") == "podcast":
            lowered_entry = normalize_unicode(entry_text).lower()
            if "podcast" in lowered_entry or entry_tokens.intersection(set(title_tokens)) or len(entry_tokens.intersection(set(context_tokens))) >= 2:
                continue
            problems.append(f"{idx}: Podcast-Anker fehlt")
            continue

        if title_tokens and entry_tokens.intersection(set(title_tokens)):
            continue
        if len(entry_tokens.intersection(set(context_tokens))) >= 2:
            continue
        problems.append(f"{idx}: zu generisch")

    return not problems, "; ".join(problems[:8])


def _normalize_genius_summary_mode(mode: str) -> str:
    normalized = normalize_unicode(str(mode or "")).strip().lower()
    if normalized == "short":
        return "short"
    if normalized == "long":
        return "long"
    return "standard"


def _genius_summary_prompt_for_mode(mode: str) -> str:
    normalized = _normalize_genius_summary_mode(mode)
    if normalized == "short":
        return GENIUS_SUMMARY_PROMPT_SHORT
    if normalized == "long":
        return GENIUS_SUMMARY_PROMPT_LONG
    return GENIUS_SUMMARY_PROMPT_STANDARD


def _strip_recap_ids(text: str) -> str:
    return re.sub(r"(?m)^(\s*)\[(\d+)\]\s+", r"\1", text).strip()


def _section_plain_excerpt(content: str, max_chars: int = 420) -> str:
    lines = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        # Audio-Marker-Zeilen ("Beitrag 5 von 83.") gehören nicht in den Excerpt —
        # sie werden vom Genie-Builder selbst wieder eingefügt und würden sonst doppelt erscheinen.
        if _is_audio_marker_line(line):
            continue
        if re.match(r"^#{1,4}\s*Was bleibt:\s*$", line, flags=re.IGNORECASE):
            break
        if _contains_regular_end_marker(line):
            break
        if _contains_briefing_end_marker(line):
            break
        if _contains_podcast_end_marker(line):
            break
        plain = _markdown_line_to_plain(line)
        if plain:
            lines.append(plain)
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text[:max_chars].strip()


def _build_genius_summary_input(content_sections: List[dict]) -> str:
    chunks = []
    for i, section in enumerate(content_sections, 1):
        content = section["content"]
        title = _extract_title(content)
        excerpt = _section_plain_excerpt(content, max_chars=700)
        source_label = section.get("source_label") or (
            "Wetter" if section.get("_weather") else ("Podcast" if section.get("type") == "podcast" else "Quelle unbekannt")
        )
        item_type = (
            "weather" if section.get("_weather") else
            "podcast" if section.get("type") == "podcast" else
            "article"
        )
        block_parts = [
            f"--- Beitrag [{i}] ---",
            f"Typ: {item_type}",
            f"Quelle: {source_label}",
            f"Titel: {title}",
        ]
        if excerpt:
            block_parts.append(f"Inhalt: {excerpt}")
        chunks.append("\n".join(block_parts))
    return "\n\n".join(chunks).strip()


_GERMAN_MONTH_RE = re.compile(
    r"^(?:Januar|Februar|M[aä]rz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\b",
    re.IGNORECASE,
)
_COMMON_ABBREV_RE = re.compile(
    r"\b(?:z\.\s?B|d\.\s?h|u\.\s?a|u\.\s?\u00e4|ca|bzw|evtl|etc|Nr|Dr|Prof|St|vs|Hr|Fr|Abs|Art|Bd|ggf|inkl|max|min|sog)\.\s*$",
    re.IGNORECASE,
)


def _split_into_sentences(text: str) -> List[str]:
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    merged: List[str] = []
    for raw_part in parts:
        part = raw_part.strip()
        if not part:
            continue
        if merged:
            prev = merged[-1]
            # Zusammenfügen, wenn der vorherige Satz mit Zahl+Punkt endet
            # und dieser Teil mit einem deutschen Monatsnamen beginnt (z.B. „läuft am 30. April 2026")
            if re.search(r"\b\d{1,2}\.\s*$", prev) and _GERMAN_MONTH_RE.match(part):
                merged[-1] = f"{prev} {part}"
                continue
            # Gängige Abkürzungen wie „z.B." oder „ca." beenden keinen Satz
            if _COMMON_ABBREV_RE.search(prev):
                merged[-1] = f"{prev} {part}"
                continue
        merged.append(part)
    return merged


def _trim_genius_entry_text(text: str, mode: str) -> str:
    normalized_mode = _normalize_genius_summary_mode(mode)
    sentences = _split_into_sentences(text)
    if not sentences:
        return text.strip()

    if normalized_mode == "short":
        max_sentences = 1
        max_chars = 240
    elif normalized_mode == "long":
        # Neue Lang-Variante: deutlich ausführlicher, nahe am Vollbriefing
        max_sentences = 12
        max_chars = 2400
    else:
        # Standard entspricht dem was vorher „Lang" war
        max_sentences = 6
        max_chars = 1250
    selected = []
    total_len = 0
    for sentence in sentences:
        projected = total_len + len(sentence) + (1 if selected else 0)
        if selected and (len(selected) >= max_sentences or projected > max_chars):
            break
        if not selected and len(sentence) > max_chars:
            selected.append(sentence[:max_chars].rstrip(" ,;:") + " …")
            return " ".join(selected).strip()
        selected.append(sentence)
        total_len = projected

    # Bei Überlänge den letzten Satz komplett weglassen statt mitten im Satz abzuschneiden.
    # Nur wenn nach dem Droppen gar nichts mehr übrig bleibt, wird hart gekürzt.
    while len(selected) > 1 and len(" ".join(selected)) > max_chars:
        selected.pop()
    result = " ".join(selected).strip()
    if len(result) > max_chars:
        result = result[:max_chars].rstrip(" ,;:") + " …"
    return result


def _lower_first_letter(text: str) -> str:
    if not text:
        return text
    first = text[0]
    lowered = first.lower()
    return lowered + text[1:] if first != lowered else text


def _build_genius_entry_text(section: dict, mode: str) -> str:
    title = _extract_title(section["content"]).strip()
    normalized_mode = _normalize_genius_summary_mode(mode)
    if normalized_mode == "short":
        excerpt_limit = 280
    elif normalized_mode == "long":
        # Neue Lang-Variante: mehr Quell-Text pro Beitrag berücksichtigen
        excerpt_limit = 2300
    else:
        # Standard: entspricht dem was vorher „Lang" war
        excerpt_limit = 1200
    excerpt = _section_plain_excerpt(section["content"], max_chars=excerpt_limit)
    excerpt = _trim_genius_entry_text(excerpt, mode)
    if not excerpt:
        excerpt = title
    if title and excerpt.startswith(title):
        rest = excerpt[len(title):].strip()
        if rest and not rest.startswith((": ", ":", " -", " –")):
            excerpt = f"{title}: {rest}"
    if title and title.lower() not in excerpt.lower():
        excerpt = f"{title}: {_lower_first_letter(excerpt)}" if excerpt else title
    return excerpt.strip()


_REGIONAL_KEYWORDS = (
    "tübingen", "tuebingen", "reutlingen", "hechingen", "rottenburg", "dußlingen",
    "dusslingen", "mössingen", "moessingen", "ammerbuch", "lustnau", "dottingen",
    "hirschau", "ohmenhausen", "gönningen", "goenningen", "bronnweiler", "kirchentellinsfurt",
    "schwäbisch", "schwaebisch", "baden-württemberg", "baden-wuerttemberg",
    "landkreis", "gemeinderat", "oberbürgermeister", "kreis tübingen",
)
_POLITIK_KEYWORDS = (
    "koalition", "bundestag", "kanzler", "minister", "regierung", "opposition",
    "parlament", "präsident", "eu-kommission", "brüssel", "europäisch", "putin",
    "trump", "iran", "ukraine", "nato", "russland", "israel", "gaza", "palästina",
    "diplomat", "außenminister", "verteidigungsminister", "bundeswehr",
    "laschet", "merz", "pistorius", "scholz", "habeck",
)
_WIRTSCHAFT_KEYWORDS = (
    "börse", "boerse", "unternehmen", "gewinn", "aktie", "inflation",
    "wirtschaft", "industrie", "steuer", "investor", "milliarde", "millionen euro",
    "konzern", "insolvenz", "handel", "export", "import", "arbeitsmarkt", "rente",
    "sozialversicherung", "mindestlohn",
)
# Kurze Keywords (ki, ai) sind raus — matchen zu viele False Positives.
# Alle Keywords sind jetzt mindestens 4 Zeichen und inhaltlich Tech-spezifisch.
_TECH_KEYWORDS = (
    "apple", "google", "microsoft", "openai", "claude", "chatgpt", "gemini",
    "software", "algorithm", "digital", "chrome", "macos", " ios ", "android",
    "tesla", "amazon", "smartphone", "computer", "roboter", "robot", "app-",
    " app ", " ki-", "künstliche intelligenz", "smart home",
)
# „tat", „haft" raus — zu viele False Positives in deutschen Wortzusammensetzungen.
_GERICHT_KEYWORDS = (
    "verhandlung", "prozess", "gericht", "urteil", "anklage", "staatsanwalt",
    "polizei", "ermittlung", "tatverdächtig", "mordkommission", "strafkammer",
    "bundesgerichtshof", "landgericht", "haftbefehl", "verurteilt", "angeklagt",
    "strafrecht", "verhaftet", "festnahme", "strafanzeige", "diebstahl",
)


def _classify_section_topic(section: dict) -> str:
    """Ordnet eine Section per Keyword-Scoring genau einem Ressort-Bucket zu.
    Titel-Treffer zählen stärker als Body-Treffer. Höchster Score gewinnt."""
    if section.get("_weather"):
        return "wetter"
    if section.get("type") == "podcast":
        return "podcast"
    # Zuverlässiges Inhalts-Signal: Claude setzt das type-Feld gelegentlich falsch
    # (z.B. BBC-Podcast als "article") — der Endmarker im Text lügt nicht. Ohne
    # diesen Check landete ein Podcast mitten zwischen den Artikeln (Bug 10.06.).
    if "Ende der Podcastzusammenfassung" in (section.get("content", "") or ""):
        return "podcast"
    title = _markdown_line_to_plain(_extract_title(section.get("content", ""))).strip().lower()
    # Body nur die ersten 300 Zeichen, ohne Titel-Dopplung
    body = (section.get("content", "") or "").lower()[:400]

    def count_hits(kws, text):
        return sum(1 for kw in kws if kw in text)

    categories = (
        ("regional", _REGIONAL_KEYWORDS),
        ("gericht", _GERICHT_KEYWORDS),
        ("tech", _TECH_KEYWORDS),
        ("wirtschaft", _WIRTSCHAFT_KEYWORDS),
        ("politik", _POLITIK_KEYWORDS),
    )
    scores = {}
    for key, kws in categories:
        # Titel-Treffer zählen 3-fach, Body-Treffer 1-fach
        title_hits = count_hits(kws, title)
        body_hits = count_hits(kws, body)
        scores[key] = title_hits * 3 + body_hits

    # Quellen-Hinweis: Lokalzeitungen aus dem Raum Tübingen/Reutlingen sprechen stark
    # für Regional — sanfter Bonus (kippt Unentschiedene, überstimmt keinen klaren
    # Titel-Treffer eines anderen Ressorts, der zählt 3-fach).
    _src = (section.get("source_label") or "").lower()
    if "tagblatt" in _src or "reutlinger" in _src or _src.strip() in ("gea", "swp"):
        scores["regional"] += 2

    # Bester Treffer
    best_key = max(scores, key=scores.get)
    if scores[best_key] == 0:
        return "sonstige"
    return best_key


def _bucket_sections_for_output(content_sections: List[dict]) -> List[tuple]:
    """Gruppiert Sections in thematische Ressort-Buckets mit definierter Reihenfolge.
    Gibt Liste von (Label, [sections]) zurück — nur Buckets die auch Inhalt haben."""
    buckets: dict[str, List[dict]] = {
        "wetter": [], "regional": [], "politik": [], "wirtschaft": [],
        "tech": [], "gericht": [], "sonstige": [], "podcast": [],
    }
    for section in content_sections:
        key = _classify_section_topic(section)
        buckets[key].append(section)

    ordered = [
        ("wetter", "Wetter"),
        ("regional", "Regional"),
        ("politik", "Politik und International"),
        ("wirtschaft", "Wirtschaft"),
        ("tech", "Tech"),
        ("gericht", "Gerichte und Ermittlungen"),
        ("sonstige", "Weitere Themen"),
        ("podcast", "Podcasts"),
    ]
    return [(label, buckets[k]) for k, label in ordered if buckets[k]]


def _cluster_sections_by_topic(content_sections: List[dict]) -> dict:
    """Gruppiert Sections grob in thematische Cluster (nur Titel, für Takeaway-Fallback)."""
    clusters: dict[str, List[str]] = {
        "regional": [], "politik": [], "wirtschaft": [],
        "tech": [], "gericht": [], "podcast": [], "sonstige": [],
    }
    for section in content_sections:
        title = _markdown_line_to_plain(_extract_title(section.get("content", ""))).strip()
        if not title:
            continue
        key = _classify_section_topic(section)
        if key == "wetter":
            continue  # Wetter ist kein eigener Takeaway-Cluster
        clusters.setdefault(key, []).append(title)
    return clusters


def _build_deterministic_genius_takeaway(content_sections: List[dict], mode: str) -> str:
    clusters = _cluster_sections_by_topic(content_sections)
    normalized_mode = _normalize_genius_summary_mode(mode)
    # Max-Titel pro Cluster je nach Modus
    if normalized_mode == "short":
        max_per_cluster = 2
    elif normalized_mode == "long":
        max_per_cluster = 5
    else:
        max_per_cluster = 3

    parts = []
    cluster_labels = [
        ("regional", "Regional"),
        ("politik", "Politik und international"),
        ("wirtschaft", "Wirtschaft"),
        ("tech", "Tech"),
        ("gericht", "Gerichte und Ermittlungen"),
        ("podcast", "Podcasts"),
    ]
    for key, label in cluster_labels:
        titles = clusters.get(key) or []
        if not titles:
            continue
        shown = titles[:max_per_cluster]
        parts.append(f"{label}: {', '.join(shown)}.")

    if not parts:
        # Fallback: Einfach die ersten Titel auflisten
        fallback_titles = []
        for section in content_sections[:6]:
            t = _markdown_line_to_plain(_extract_title(section.get("content", ""))).strip()
            if t:
                fallback_titles.append(t)
        if fallback_titles:
            return "Im Mittelpunkt stehen heute: " + ", ".join(fallback_titles[:6]) + "."
        return "Heute ist ein dichter Tag mit mehreren parallelen Entwicklungen."

    return " ".join(parts)


def _extract_genius_takeaway_text(text: str) -> str:
    lines = text.splitlines()
    capture = False
    parts = []
    # Toleranter Header-Match: #, ##, ###, ####, **..**, oder nackt — mit/ohne Doppelpunkt
    header_re = re.compile(
        r"^\s*(?:#{1,6}\s+|\*{1,3}\s*|_{1,2}\s*)?was\s+du\s+mitnehmen\s+kannst\s*:?\s*(?:\*{1,3}|_{1,2})?\s*$",
        re.IGNORECASE,
    )
    for raw_line in lines:
        line = raw_line.strip()
        if not capture:
            if header_re.match(line):
                capture = True
            continue
        if not line:
            continue
        if line.lower().startswith("ende der kurzfassung"):
            break
        if re.match(r"^\[\d+\]\s*", line):
            continue
        # Neue Section-Überschrift beendet den Takeaway
        if re.match(r"^#{1,6}\s+\S", line):
            break
        plain = _markdown_line_to_plain(line).strip()
        if plain:
            parts.append(plain)
    return " ".join(parts).strip()


def _genius_connector_for_index(index: int, total: int) -> str:
    if index == 1:
        return "Zum Einstieg"
    if index == total:
        return "Zum Abschluss"
    return "Nächster Artikel"


def _genius_audio_marker(index: int, total: int) -> str:
    """Nur noch der Connector (z.B. „Zum Einstieg.", „Nächster Artikel.").
    Der Counter („Beitrag 5 von 47.") wandert jetzt in die Markdown-Überschrift."""
    connector = _genius_connector_for_index(index, total)
    return f"{connector}."


def _strip_existing_genius_connector(text: str) -> str:
    text = text.strip()
    connector_patterns = [
        r"^Zum Einstieg:\s*",
        r"^Zum Einstieg\.\s*",
        r"^Nächster Punkt:\s*",
        r"^Nächster Punkt\.\s*",
        r"^Nächster Beitrag:\s*",
        r"^Nächster Beitrag\.\s*",
        r"^Nächster Artikel:\s*",
        r"^Nächster Artikel\.\s*",
        r"^Danach:\s*",
        r"^Danach\.\s*",
        r"^Außerdem:\s*",
        r"^Außerdem\.\s*",
        r"^Weiter im Briefing:\s*",
        r"^Weiter im Briefing\.\s*",
        r"^Noch ein Punkt:\s*",
        r"^Noch ein Punkt\.\s*",
        r"^Weiterer Beitrag:\s*",
        r"^Weiterer Beitrag\.\s*",
        r"^Als Nächstes:\s*",
        r"^Als Nächstes\.\s*",
        r"^Noch ein Beitrag:\s*",
        r"^Noch ein Beitrag\.\s*",
    ]
    for pattern in connector_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text.strip()


def _strip_inline_markdown(line: str) -> str:
    """Entfernt Inline-Markdown-Marker (Fett, Kursiv) ohne den Text zu verlieren."""
    # **fett** → fett
    line = re.sub(r'\*\*([^*\n]+?)\*\*', r'\1', line)
    # __fett__ → fett
    line = re.sub(r'__([^_\n]+?)__', r'\1', line)
    # *kursiv* → kursiv (vorsicht: nicht bei einzelnen Sternchen mitten im Wort)
    line = re.sub(r'(?<!\w)\*([^*\n]+?)\*(?!\w)', r'\1', line)
    # _kursiv_ → kursiv
    line = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'\1', line)
    return line


def _cleanup_podcast_content(content: str) -> str:
    """Bereinigt Podcast-Section-Content vor der Speicherung:
    - Erhält: Titel (erste ### Zeile) und den #### Was bleibt: Header
    - Entfernt: Zwischen-### Überschriften im Hauptteil, Inline-**bold**, Fett-Absätze
    - Deduziert: Wenn Titel sowohl als ### als auch als **fett** vorkommt, nur eins behalten
    Damit wird das PDF-Rendering sauber (kein grün+fetter Wurm-Absatz im Hauptteil)."""
    if not content:
        return content

    def _norm_for_dedup(s: str) -> str:
        return re.sub(r'\s+', ' ', s.lower()).strip()

    lines = content.splitlines()
    out = []
    title_seen = False
    known_title_norm = ""
    for raw in lines:
        stripped = raw.strip()

        # "#### Was bleibt:" bleibt unverändert
        if re.match(r'^#{3,4}\s*Was bleibt:\s*$', stripped, flags=re.IGNORECASE):
            out.append(raw)
            continue

        # Titel (allererste ### Zeile) bleibt unverändert
        header_match = re.match(r'^#{1,4}\s+(.+?)\s*$', stripped)
        if header_match:
            header_text_raw = _strip_inline_markdown(header_match.group(1)).strip()
            if not title_seen:
                out.append(raw)
                title_seen = True
                known_title_norm = _norm_for_dedup(header_text_raw)
                continue
            # Duplikat des Titels? Dann komplett überspringen
            if known_title_norm and _norm_for_dedup(header_text_raw) in known_title_norm or known_title_norm in _norm_for_dedup(header_text_raw):
                continue
            # Zwischenüberschrift → in Plain-Text-Absatz umwandeln
            if out and out[-1].strip():
                out.append("")
            out.append(header_text_raw)
            out.append("")
            continue

        # Fett-only-Zeile (`**Titel**` alleinstehend) → Plain-Text-Absatz
        bold_only = re.match(r'^\*\*(.+?)\*\*\s*$', stripped)
        if bold_only:
            bold_text = _strip_inline_markdown(bold_only.group(1)).strip()
            bold_norm = _norm_for_dedup(bold_text)
            if not title_seen:
                # Falls der Titel als **…** statt ### kam: in ### umwandeln
                out.append(f"### {bold_text}")
                title_seen = True
                known_title_norm = bold_norm
                continue
            # Duplikat des Titels? Skip
            if known_title_norm and (bold_norm == known_title_norm or bold_norm in known_title_norm or known_title_norm in bold_norm):
                continue
            if out and out[-1].strip():
                out.append("")
            out.append(bold_text)
            out.append("")
            continue

        # Einordnung (`*Kursiv-Zeile.*`) bleibt unverändert — das ist gewolltes Kursiv im PDF
        if re.match(r'^\*[^*].*[^*]\*\s*$', stripped) and '**' not in stripped:
            out.append(raw)
            continue

        # Normale Zeile: Inline-Markdown glätten
        out.append(_strip_inline_markdown(raw))

    text = "\n".join(out)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _build_podcast_full_text(section: dict) -> str:
    """Übernimmt den Podcast-Content in voller Länge und glättet Markdown-Formatierung,
    damit das PDF-Rendering sauber aussieht (keine ** oder ### sichtbar)."""
    raw = section.get("content", "") or ""
    out_lines = []
    title_seen = False
    for raw_line in raw.splitlines():
        stripped = raw_line.strip()
        if _is_audio_marker_line(stripped):
            continue
        if _contains_podcast_end_marker(stripped):
            continue
        if _contains_briefing_end_marker(stripped):
            continue
        if re.match(r"^#{1,4}\s*Was bleibt:\s*$", stripped, flags=re.IGNORECASE):
            continue
        if stripped.lower() in ("weiter geht's.", "weiter gehts.", "weiter geht's", "weiter gehts"):
            continue

        # Zwischenüberschriften (### Titel) in normales Format bringen:
        # Die allererste Header-Zeile ist der Titel → wird separat als Überschrift gesetzt, weg.
        # Folgende Header-Zeilen werden in „fettähnlichen" Absatz umgewandelt.
        header_match = re.match(r'^#{1,4}\s+(.+?)\s*$', stripped)
        if header_match:
            header_text = _strip_inline_markdown(header_match.group(1)).strip()
            if not title_seen:
                # Ist der Titel des Beitrags — wird draußen als Überschrift gesetzt, hier weg
                title_seen = True
                continue
            # Zwischenüberschrift: als eigene Absatzzeile ohne Markdown-Marker
            # (erzwingt Leerzeile davor/danach für saubere Absatzstruktur)
            if out_lines and out_lines[-1].strip():
                out_lines.append("")
            out_lines.append(header_text)
            out_lines.append("")
            continue

        # Fett-Markdown (**Titel** alleinstehend) auch als Zwischenüberschrift behandeln
        bold_only = re.match(r'^\*\*(.+?)\*\*\s*$', stripped)
        if bold_only:
            bold_text = _strip_inline_markdown(bold_only.group(1)).strip()
            if not title_seen:
                title_seen = True
                continue
            if out_lines and out_lines[-1].strip():
                out_lines.append("")
            out_lines.append(bold_text)
            out_lines.append("")
            continue

        # Einordnung (`*Kursiv-Zeile.*`) bleibt unverändert — das ist gewolltes Kursiv
        if re.match(r'^\*[^*].*[^*]\*\s*$', stripped) and '**' not in stripped:
            out_lines.append(raw_line)
            continue

        # Normale Zeile: Inline-Markdown entfernen, Zeilen-Struktur bewahren
        out_lines.append(_strip_inline_markdown(raw_line))

    text = "\n".join(out_lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _looks_like_title_list_takeaway(text: str) -> bool:
    """Erkennt den deterministischen Cluster-Fallback-Takeaway (Ressort: Titel1, Titel2, ...)."""
    if not text:
        return False
    # Typisches Muster: mehrere "Ressort-Label: Titel, Titel, Titel." Blöcke
    cluster_hits = 0
    for label in ("Regional:", "Politik und international:", "Wirtschaft:", "Tech:", "Gerichte und Ermittlungen:", "Podcasts:"):
        if label in text:
            cluster_hits += 1
    return cluster_hits >= 3


def _extract_essenz_block(sections: List[dict]) -> str:
    """Holt den Essenz-/Recap-Block aus dem Voll-Briefing für den Takeaway.
    Bevorzugt die Essenz ("Was wirklich bleibt") vor dem Recap ("Zusammenfassung"),
    weil die Essenz inhaltlicher ist. Gibt den Fließtext ohne Header/Marker zurück."""
    # Zuerst Essenz suchen, dann Recap als Fallback
    for flag in ("_essenz", "_recap"):
        for section in sections:
            if not section.get(flag):
                continue
            raw = section.get("content", "") or ""
            out = []
            for line in raw.splitlines():
                stripped = line.strip()
                if _is_audio_marker_line(stripped):
                    continue
                if _contains_briefing_end_marker(stripped):
                    continue
                if stripped.lower() in ("weiter geht's.", "weiter gehts.", "weiter geht's", "weiter gehts"):
                    continue
                if _contains_podcast_end_marker(stripped):
                    continue
                out.append(line)
            text = "\n".join(out).strip()
            text = _strip_inline_markdown(text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            # Header-Zeilen oben entfernen (### Titel, **Titel**, Plain-Text-Meta-Titel)
            ls = text.splitlines()
            while ls:
                while ls and not ls[0].strip():
                    ls.pop(0)
                if not ls:
                    break
                first = ls[0].strip()
                if re.match(r'^#{1,4}\s+', first):
                    ls.pop(0)
                    continue
                if re.match(r'^\*\*.+\*\*$', first):
                    ls.pop(0)
                    continue
                low = first.lower().strip(' :.')
                if low in ("was wirklich bleibt", "essenz", "zusammenfassung", "rückblick", "recap"):
                    ls.pop(0)
                    continue
                # Falls eine „Was bleibt:"-Sub-Zeile ganz oben steht, auch weg
                if re.match(r'^#{0,4}\s*Was bleibt\s*:?\s*$', first, re.IGNORECASE):
                    ls.pop(0)
                    continue
                break
            result = "\n".join(ls).strip()
            if result and len(result.split()) >= 20:
                return result
    return ""


def _extract_closing_block(sections: List[dict]) -> str:
    """Holt die Verabschiedung-/Zitat-Section aus dem Voll-Briefing für den Abschluss.
    Gibt den Text ohne Audio-Marker und ohne Doppel-Überschrift zurück."""
    for section in sections:
        if section.get("_verabschiedung"):
            raw = section.get("content", "") or ""
            out = []
            for line in raw.splitlines():
                stripped = line.strip()
                if _is_audio_marker_line(stripped):
                    continue
                if _contains_briefing_end_marker(stripped):
                    continue
                out.append(line)
            text = "\n".join(out).strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            # Oben liegende Header-Zeilen entfernen (### Bis zum nächsten Mal oder Plain-Text-Variante)
            ls = text.splitlines()
            while ls:
                while ls and not ls[0].strip():
                    ls.pop(0)
                if not ls:
                    break
                first = ls[0].strip()
                # Markdown-Header, Fett-Zeile oder Plain-Text "Bis zum nächsten …" am Anfang entfernen
                if re.match(r'^#{1,4}\s+', first):
                    ls.pop(0)
                    continue
                if re.match(r'^\*\*.+\*\*$', first):
                    ls.pop(0)
                    continue
                if first.lower().startswith("bis zum nächsten") and len(first) < 40:
                    ls.pop(0)
                    continue
                break
            while ls and not ls[0].strip():
                ls.pop(0)
            return "\n".join(ls).strip()
    return ""


def _lint_genius_summary(summary_text: str, total_sections: int) -> dict:
    """Lokaler Lint für die fertige Geniale — prüft Struktur und macht Auto-Repair.

    Returns:
        {
            "issues": [...],         # {"severity": "warning"|"info", "message": str}
            "auto_repairs": [...],   # Was automatisch korrigiert wurde
            "repaired_text": str,    # Ggf. korrigierter Text (oder Original)
        }
    """
    issues = []
    auto_repairs = []
    text = summary_text

    # 1) Pflicht-Elemente vorhanden?
    if "Kompakte Vollzusammenfassung" not in text:
        issues.append({"severity": "warning", "message": "Haupt-Überschrift 'Kompakte Vollzusammenfassung' fehlt."})
    if "Was du mitnehmen kannst" not in text:
        issues.append({"severity": "warning", "message": "Block 'Was du mitnehmen kannst' fehlt."})
    if "Bis zum nächsten Mal" not in text:
        issues.append({"severity": "info", "message": "Abschluss-Block 'Bis zum nächsten Mal' fehlt (Verabschiedung nicht aus Voll-Briefing extrahierbar)."})
    if "Ende der Kurzfassung" not in text:
        issues.append({"severity": "warning", "message": "End-Marker 'Ende der Kurzfassung' fehlt."})

    # 2) Nummerierung lückenlos?
    # Format: „#### Artikel N von M — …" (neu) oder „#### N/M …" (alt, Fallback)
    counters = re.findall(r'(?m)^#{3,4}\s+Artikel\s+(\d+)\s+von\s+(\d+)', text)
    if not counters:
        counters = re.findall(r'(?m)^#{3,4}\s+(\d+)/(\d+)', text)
    if counters:
        seen_nums = [int(c[0]) for c in counters]
        expected_total = int(counters[0][1])
        # Doppelte Nummern?
        if len(seen_nums) != len(set(seen_nums)):
            dupes = [n for n in set(seen_nums) if seen_nums.count(n) > 1]
            issues.append({"severity": "warning", "message": f"Doppelte Beitragsnummern: {dupes}"})
        # Lückenlos von 1 bis expected_total?
        missing = [n for n in range(1, expected_total + 1) if n not in seen_nums]
        if missing:
            issues.append({"severity": "warning", "message": f"Fehlende Beitragsnummern: {missing[:10]}{'...' if len(missing)>10 else ''}"})
        # Total-Zahl konsistent?
        totals_in_counters = set(int(c[1]) for c in counters)
        if len(totals_in_counters) > 1:
            issues.append({"severity": "warning", "message": f"Unterschiedliche Total-Zahlen: {sorted(totals_in_counters)}"})

    # 3) Inline-Counter-Dopplungen entfernen (Auto-Repair)
    inline_counter_pattern = re.compile(
        r'(?m)^((?:(?:Letzter\s+)?Beitrag\s+\d+\s+von\s+\d+|Letzter\s+Beitrag,\s+\d+\s+von\s+\d+)\.\s*)',
        re.IGNORECASE,
    )
    inline_matches = inline_counter_pattern.findall(text)
    if inline_matches:
        text = inline_counter_pattern.sub('', text)
        auto_repairs.append(f"{len(inline_matches)} Inline-Counter entfernt")

    # 4) Übrige **bold**-Marker im Fließtext
    remaining_bold = re.findall(r'(?<!^)(?<!\n)\*\*[^*\n]{2,80}\*\*', text)
    if remaining_bold:
        text = re.sub(r'\*\*([^*\n]{2,80})\*\*', r'\1', text)
        auto_repairs.append(f"{len(remaining_bold)} **bold**-Marker geglättet")

    # 5) Titel-Dopplungen (Überschrift-Titel == erste Textzeile)
    # Neues Format: „#### Artikel N von M — Quelle — Titel (Podcast)"
    # Der Titel ist der letzte Teil vor „(Podcast)" bzw. Zeilenende.
    pattern = re.compile(
        r'(?m)(^#{3,4}\s+Artikel\s+\d+\s+von\s+\d+\s+—\s+(?:[^—\n]+\s+—\s+)?([^\n(—]+?)(?:\s+\(Podcast\))?\s*)\n(\s*\n)([^\n]+)(\n)'
    )
    dup_count = 0
    def _remove_dup_title(m):
        nonlocal dup_count
        header_line = m.group(1)
        header_title = m.group(2).strip()
        blank = m.group(3)
        first_line = m.group(4).strip()
        trailing = m.group(5)
        # Check: first_line ist gleich oder startet mit header_title
        if first_line.lower() == header_title.lower() or first_line.lower().startswith(header_title.lower() + " "):
            dup_count += 1
            # Header + Leerzeile behalten, aber erste Textzeile (die Dopplung) weglassen
            return f"{header_line}\n{blank}"
        return m.group(0)
    text = pattern.sub(_remove_dup_title, text)
    if dup_count:
        auto_repairs.append(f"{dup_count} Titel-Dopplungen entfernt")

    # 6) Meta-Block-Reste als Artikel? — altes und neues Format
    meta_as_article = re.findall(
        r'(?m)^#{3,4}\s+(?:Artikel\s+\d+\s+von\s+\d+\s+—\s+(?:[^—\n]+\s+—\s+)?|\d+/\d+\s+)(Zusammenfassung|Rückblick|Essenz|Was wirklich bleibt|Bis zum nächsten)\s*$',
        text,
    )
    if meta_as_article:
        issues.append({"severity": "warning", "message": f"Meta-Block durchgerutscht als Artikel: {meta_as_article}"})

    # 7) Zu kurze Beiträge (< 20 Wörter) — Parser-Fehler-Hinweis
    beitrag_blocks = re.findall(
        r'(?m)^#{3,4}\s+(?:Artikel\s+\d+\s+von\s+\d+|\d+/\d+)\s+[^\n]+\n\n([^\n#]+(?:\n(?!#)[^\n]*)*)',
        text,
    )
    too_short = [i+1 for i, b in enumerate(beitrag_blocks) if len(b.split()) < 15]
    if too_short:
        issues.append({"severity": "info", "message": f"Sehr kurze Beiträge (< 15 Wörter): Position(en) {too_short[:5]}{'...' if len(too_short)>5 else ''}"})

    # 8) Leere Ressort-Sektionen (### Ressort gefolgt direkt von ### oder #### Was du)
    empty_ressorts = re.findall(r'(?m)^###\s+([A-ZÄÖÜ][a-zäöüß\s]+?)\n\s*\n(?=###\s+[A-ZÄÖÜ]|####\s+Was du)', text)
    if empty_ressorts:
        # Leere Ressort-Headers entfernen
        text = re.sub(r'(?m)^###\s+[A-ZÄÖÜ][a-zäöüß\s]+?\n\s*\n(?=###\s+[A-ZÄÖÜ]|####\s+Was du)', '', text)
        auto_repairs.append(f"{len(empty_ressorts)} leere Ressort-Sektionen entfernt")

    # 9) Doppelte Leerzeilen glätten
    text = re.sub(r'\n{4,}', '\n\n\n', text)

    return {
        "issues": issues,
        "auto_repairs": auto_repairs,
        "repaired_text": text.strip(),
    }


_PODCAST_CONDENSE_PROMPT = """Du verdichtest eine bereits fertige Podcast-Zusammenfassung auf MAXIMAL 450 Wörter (inklusive Titel und Was-bleibt-Block).

ZIEL Die Podcast-Zusammenfassung ist aktuell zu lang für eine kompakte Hörfassung. Verdichte sie auf MAXIMAL 450 Wörter OHNE Tiefenverlust bei Kernargumenten, Namen, Zahlen oder Zitaten. Das Wort-Limit ist eine harte Obergrenze, keine Richtlinie.

WAS BLEIBT
1) Titel (### Zeile am Anfang) — unverändert
2) Einordnung in Kursiv (*...*) — unverändert
3) Alle Sprecher-Namen und ihre Kernaussagen
4) Alle konkreten Zahlen, Daten, Orte, Zitate
5) Hauptthese/Leitfrage der Folge
6) Der „#### Was bleibt:"-Block am Ende — unverändert übernehmen
7) „Ende der Podcastzusammenfassung." am Schluss

WAS WEG KANN
- Wiederholungen derselben Aussage mit anderen Worten
- Ausschmückende Beispiele und Anekdoten (außer sie tragen ein Argument)
- Kontextsätze die nur die Prämisse erklären
- Mehrfach angeführte Belege (einer reicht)

STIL Flüssiger Fließtext, keine Zwischenüberschriften mit ### oder **fett** im Hauptteil. Vollständige Sätze. Keine Bullet Points.

Gib NUR den verdichteten Podcast-Text aus. Keine Kommentare, keine Erklärungen."""


def _condense_podcast_for_genius(client, podcast_content: str, model: str, max_words: int = 500) -> str:
    """Verdichtet einen Podcast-Text auf max. ~450 Wörter per LLM.
    Wenn der Text schon kurz genug ist, wird er unverändert zurückgegeben.
    Wenn das LLM-Ergebnis noch zu lang ist, wird nochmal verdichtet (bis zu 2x)."""
    if not podcast_content or not client:
        return podcast_content
    current_words = len(podcast_content.split())
    if current_words <= max_words:
        return podcast_content
    text = podcast_content
    for attempt in range(2):
        try:
            result = summarize(
                client,
                text,
                _PODCAST_CONDENSE_PROMPT,
                model,
            )
            if result and len(result.strip()) > 100:
                result = result.strip()
                result_words = len(result.split())
                if result_words < len(text.split()):
                    text = result
                    if result_words <= max_words:
                        return text
                    # Wenn immer noch zu lang: nochmal versuchen mit dem aktuellen Text
                    continue
        except Exception:
            break
    return text if text != podcast_content else podcast_content


_GENIUS_QUALITY_CHECK_PROMPT = """Du prüfst die Qualität einer kompakten Audio-Briefing-Zusammenfassung (Geniale Zusammenfassung).

ZIEL: Nur echte Qualitätsprobleme melden. Sei sparsam mit Warnungen. Kürzungen und Paraphrasen sind erwünscht, nicht fehlerhaft.

WARNEN NUR BEI:
1) FEHLENDE BEITRÄGE: Ein Beitrag aus der Quelle fehlt komplett in der Zusammenfassung
2) FALSCHE CLUSTER: Ein Beitrag ist offensichtlich im falschen Ressort (z.B. reiner Wirtschafts-Artikel unter Gerichte)
3) FAKTENFEHLER: Name, Zahl oder Ergebnis im Takeaway oder Abschluss widerspricht der Quelle
4) KORRUPTE STRUKTUR: Beitrags-Überschrift fehlt, Text ist Unsinn, Titel stimmt nicht mit Inhalt überein

NICHT WARNEN BEI:
- Kürzungen (erwünscht)
- Paraphrasen (erwünscht)
- Kleine Umformulierungen
- Cluster-Grenzfälle (wo ein Artikel in 2 Ressorts passen könnte)

AUSGABEFORMAT (JSON, keine andere Ausgabe):
{
  "issues": [
    {"severity": "warning", "beitrag": "44/72", "category": "falsches_cluster|fehlend|fakt|struktur", "message": "...", "fix_hint": "..."}
  ],
  "coverage_ok": true|false,
  "cluster_ok": true|false
}

Wenn alles gut: {"issues": [], "coverage_ok": true, "cluster_ok": true}"""


def _llm_check_genius_summary(
    client,
    summary_text: str,
    content_sections: List[dict],
    model: str,
) -> dict:
    """LLM-basierter Qualitäts-Check der Geniale. Gibt issues + Reparatur-Hinweise zurück."""
    # Input für LLM: Zusammenfassung + Liste aller Quell-Titel
    source_titles = []
    for i, s in enumerate(content_sections, 1):
        title = _markdown_line_to_plain(_extract_title(s.get("content", ""))).strip()
        src = s.get("source_label", "")
        typ = "Podcast" if s.get("type") == "podcast" else ("Wetter" if s.get("_weather") else "Artikel")
        source_titles.append(f"[{i}] {typ} ({src}): {title}")

    source_list = "\n".join(source_titles)
    check_input = (
        f"QUELLBEITRÄGE ({len(content_sections)} Stück):\n{source_list}\n\n"
        f"GENIALE ZUSAMMENFASSUNG ZUM PRÜFEN:\n{summary_text}"
    )

    try:
        result_text = summarize(
            client,
            check_input,
            _GENIUS_QUALITY_CHECK_PROMPT,
            model,
        )
    except Exception as exc:
        return {"issues": [], "coverage_ok": None, "cluster_ok": None, "error": str(exc)}

    # JSON aus dem Output extrahieren
    if not result_text:
        return {"issues": [], "coverage_ok": None, "cluster_ok": None}
    m = re.search(r'\{[\s\S]*\}', result_text)
    if not m:
        return {"issues": [], "coverage_ok": None, "cluster_ok": None}
    try:
        import json as _json
        data = _json.loads(m.group(0))
        if not isinstance(data, dict):
            return {"issues": [], "coverage_ok": None, "cluster_ok": None}
        if not isinstance(data.get("issues"), list):
            data["issues"] = []
        return data
    except Exception:
        return {"issues": [], "coverage_ok": None, "cluster_ok": None}


_GENIUS_REPAIR_PROMPT = """Du korrigierst gezielt konkret benannte Probleme in einer Geniale-Zusammenfassung.

REGELN
1) Ändere NUR die konkret benannten Beiträge/Stellen, nichts anderes.
2) Bewahre Formatierung (### Ressort, #### X/Y Titel, Leerzeilen).
3) Bei fehlenden Beiträgen: füge einen kurzen Absatz im passenden Ressort ein.
4) Bei falschem Cluster: Verschiebe den Beitrag in das richtige Ressort (Counter bleibt gleich!).
5) Bei Faktenfehlern: korrigiere den Faktum mit dem Wert aus der Quelle.
6) Keine neuen Kommentare, keine Erklärungen.

Gib NUR den komplett korrigierten Text aus, sonst nichts."""


def _llm_repair_genius_summary(
    client,
    summary_text: str,
    issues: List[dict],
    content_sections: List[dict],
    model: str,
) -> str:
    """LLM wird gebeten, die konkret benannten Probleme zu beheben. Liefert reparierten Text."""
    if not issues:
        return summary_text

    # Sammle Quell-Kontext für die relevanten Beiträge
    source_context_lines = []
    for i, s in enumerate(content_sections, 1):
        title = _markdown_line_to_plain(_extract_title(s.get("content", ""))).strip()
        body = _section_plain_excerpt(s.get("content", ""), max_chars=400)
        source_context_lines.append(f"[{i}] {title}\n{body}")

    issue_list = "\n".join(
        f"- [{issue.get('beitrag','?')}] {issue.get('category','?')}: {issue.get('message','')} (Hinweis: {issue.get('fix_hint','')})"
        for issue in issues
    )
    repair_input = (
        f"ZU KORRIGIERENDE PROBLEME:\n{issue_list}\n\n"
        f"QUELLKONTEXT:\n" + "\n\n".join(source_context_lines[:50]) + "\n\n"
        f"AKTUELLE GENIALE ZUSAMMENFASSUNG:\n{summary_text}"
    )

    try:
        result = summarize(
            client,
            repair_input,
            _GENIUS_REPAIR_PROMPT,
            model,
        )
        if result and len(result.strip()) > len(summary_text) / 2:
            return result.strip()
    except Exception:
        pass
    return summary_text


def _format_genius_summary_output(raw_summary: str, content_sections: List[dict], mode: str, all_sections: Optional[List[dict]] = None, direct_mode: bool = False) -> str:
    normalized_mode = _normalize_genius_summary_mode(mode)
    entry_map = _extract_recap_entry_map(raw_summary)
    # Takeaway-Priorität:
    # 1) Essenz-Block („Was wirklich bleibt") aus dem Voll-Briefing — hochwertiger LLM-Fließtext
    #    mit Plaus-Check geprüft, bevorzugte Quelle für den Takeaway.
    # 2) LLM-generierter Takeaway aus der raw_summary (falls Essenz fehlt oder zu kurz).
    # 3) Deterministischer Cluster-Fallback (nur Titel-Liste — echter Notfall).
    takeaway = ""
    if all_sections:
        takeaway = _extract_essenz_block(all_sections)
    if not takeaway:
        takeaway = _extract_genius_takeaway_text(raw_summary)
    if not takeaway or _looks_like_title_list_takeaway(takeaway):
        # Wenn sogar der LLM-Takeaway nur eine Titel-Auflistung ist, fall back auf deterministisch
        # (der ist zumindest klar strukturiert, auch wenn er nicht perfekt ist).
        takeaway = _build_deterministic_genius_takeaway(content_sections, normalized_mode)

    intro = (
        "*Hier ist das ganze Briefing in sehr knapper, aber vollständiger Form.*"
        if normalized_mode == "short"
        else (
            "*Hier ist das ganze Briefing in ausführlicher, aber immer noch kompakter Form.*"
            if normalized_mode == "long"
            else "*Hier ist das ganze Briefing in einer längeren, aber immer noch kompakten Form.*"
        )
    )
    lines = [
        "### Kompakte Vollzusammenfassung",
        "",
        intro,
        "",
    ]
    total = len(content_sections)

    META_SKIP = {"was bleibt", "was bleibt:", "weiter geht's", "weiter gehts",
                 "ende des briefings", "ende der podcastzusammenfassung",
                 "willkommen zum briefing"}

    # Inline-Counter-Regex vorab compilen
    _inline_counter_re = re.compile(
        r'^(?:(?:Letzter\s+)?Beitrag\s+\d+\s+von\s+\d+|Letzter\s+Beitrag,\s+\d+\s+von\s+\d+)\.\s*',
        re.IGNORECASE,
    )

    # Ursprünglichen Index pro Section erfassen (für entry_map-Lookup nach Bucket-Umsortierung)
    numbered = [(i, s) for i, s in enumerate(content_sections, 1)]

    # In thematische Buckets gruppieren — Reihenfolge innerhalb der Buckets bleibt erhalten
    bucket_order = [
        ("wetter", "Wetter"),
        ("regional", "Regional"),
        ("politik", "Politik und International"),
        ("wirtschaft", "Wirtschaft"),
        ("tech", "Tech"),
        ("gericht", "Gerichte und Ermittlungen"),
        ("sonstige", "Weitere Themen"),
        ("podcast", "Podcasts"),
    ]
    buckets: dict = {k: [] for k, _ in bucket_order}
    for orig_idx, section in numbered:
        buckets[_classify_section_topic(section)].append((orig_idx, section))

    global_pos = 0
    for bucket_key, bucket_label in bucket_order:
        items = buckets[bucket_key]
        if not items:
            continue
        # Ressort-Überschrift
        lines.append(f"### {bucket_label}")
        lines.append("")

        for orig_idx, section in items:
            global_pos += 1
            title = _markdown_line_to_plain(_extract_title(section["content"])).strip()
            # Robuste Rettung: greift bei leerem Titel ODER Audio-Marker als Titel
            if not title or _is_audio_marker_line(title) or title.lower().strip(' :.') in META_SKIP:
                raw_lines = [l.strip() for l in (section.get("content", "") or "").splitlines() if l.strip()]
                title = ""
                for rl in raw_lines:
                    if _is_audio_marker_line(rl):
                        continue
                    candidate = re.sub(r'^#{1,4}\s*', '', rl)
                    candidate = re.sub(r'^\*\*(.+?)\*\*$', r'\1', candidate)
                    candidate = candidate.strip()
                    if candidate.lower().strip(' :.') in META_SKIP:
                        continue
                    if len(candidate) > 5:
                        title = candidate[:120]
                        break

            # === Sonderbehandlung: Volltext bei Podcasts UND Wetter ===
            # Beides soll nicht gekürzt werden: Podcasts wegen Argumentations-Tiefe,
            # Wetter wegen Tagesverlauf + 3-Tages-Ausblick.
            is_podcast = section.get("type") == "podcast"
            is_weather = bool(section.get("_weather"))
            entry_source = ""  # aus Opus' Eintrag herausgelöste Quelle (Direkt-Modus)
            # Wetter-Volltext NUR im Voll-Pfad (dort ist die Wetter-Section schon schöne
            # Prosa). Im Direkt-Modus ist die Wetter-Section ROHDATEN ("Tagesübersicht: Tag
            # 2026-…, UV-Index None") → nicht vorlesetauglich. Dann lieber Opus' Eintrag
            # (er hat die Wetterlage bereits in flüssige Prosa gefasst).
            if is_podcast or (is_weather and not direct_mode):
                entry_text = _build_podcast_full_text(section)
                # Auch im Volltext inline-Counter entfernen (Safety net)
                entry_text = _inline_counter_re.sub('', entry_text, count=1)
            else:
                entry_text = _markdown_line_to_plain(entry_map.get(orig_idx, "")).strip()
                if not entry_text:
                    entry_text = _build_genius_entry_text(section, normalized_mode)
                entry_text = _strip_existing_genius_connector(entry_text)
                # Falls entry_text mit einem Audio-Marker als eigener Zeile anfängt: abschneiden
                et_lines = entry_text.splitlines()
                while et_lines and _is_audio_marker_line(et_lines[0]):
                    et_lines.pop(0)
                entry_text = "\n".join(et_lines).strip()
                # Audio-Marker INLINE am Zeilenanfang entfernen
                entry_text = _inline_counter_re.sub('', entry_text, count=1)
                # QUELLE aus Opus' Eintrag herauslösen: „(SWR) Text" / „(Quelle: SWR) Text".
                # So passt die Quelle IMMER zum angezeigten Text — egal ob Opus' [N] mit der
                # Eingabe-Reihenfolge übereinstimmt. Aus dem Text entfernt, damit beim Vorlesen
                # nichts doppelt kommt. NUR Direkt-Modus (nur dort fügt der Prompt sie ein).
                if direct_mode:
                    # Ganze Anfangsklammer (bis 90 Zeichen) erfassen und entfernen — auch wenn
                    # Opus Zusatz reinschreibt („(tagesschau - aus BBC-Quelle: …)"). Als Quelle
                    # nur den Outlet-Namen VORNE nehmen (vor erstem „ - " / „: ").
                    _msrc = re.match(r'^[\(\[]\s*(?:Quelle:?\s*)?([^)\]\n]{2,90}?)\s*[\)\]]\s*[—–:.\-]?\s+', entry_text)
                    if _msrc:
                        entry_text = entry_text[_msrc.end():].strip()
                        _cand = re.split(r'\s[–—-]\s|:\s', _msrc.group(1).strip(), 1)[0].strip(" .,;:–—-")
                        if 2 <= len(_cand) <= 35 and _cand.lower() not in ("quelle unbekannt", "unbekannt", "unknown", "quelle", "n/a", "k.a."):
                            entry_source = _cand
                # Sicherheits-Backup vor Title-Prefix-Strip — falls Strip alles wegnimmt
                _entry_before_strip = entry_text
                # Wenn entry_text mit "Titel: rest" beginnt, Titel-Prefix wegnehmen
                if title:
                    for sep in (": ", " — ", " – ", " - "):
                        prefix = f"{title}{sep}"
                        if entry_text.lower().startswith(prefix.lower()):
                            rest = entry_text[len(prefix):].strip()
                            if rest:
                                entry_text = rest[0].upper() + rest[1:]
                            break
                # Wenn entry_text mit "Titel " beginnt (ohne Separator), auch abschneiden
                if title and entry_text.lower().startswith(title.lower() + " "):
                    rest = entry_text[len(title):].strip()
                    if rest:
                        entry_text = rest[0].upper() + rest[1:]
                # Wenn der Title-Prefix-Strip den Body unter 20 Wörter gedrückt hat,
                # aber der Original-Body deutlich länger war: Strip rückgängig.
                # Das schützt vor leeren Beiträgen (z.B. wenn Title = Einordnungszeile war).
                if len(entry_text.split()) < 20 and len(_entry_before_strip.split()) >= 25:
                    entry_text = _entry_before_strip

            # Markdown-Überschrift: „Artikel N von M — Quelle — Titel (Podcast)"
            # Form liest sich beim Vorlesen klar und natürlich vor — Gedankenstriche
            # erzeugen Sprechpausen. Quelle steht vor dem Titel wie im Vollbriefing.
            if direct_mode:
                # Direkt-Modus: Quelle aus Opus' Eintrag (passt zum Text). Section-Quelle
                # NICHT als Fallback nutzen — die kann durch Opus' Umnummerierung daneben
                # liegen (lieber keine Quelle als eine falsche). Ausnahme Wetter: dort ist
                # die Zuordnung sicher ([1]) → Section-Quelle „DWD" als Fallback ok.
                source_label = entry_source or (section.get("source_label", "") if is_weather else "")
            else:
                source_label = (section.get("source_label") or "").strip()
            if source_label.lower() in ("", "quelle unbekannt", "podcast", "wetter"):
                source_label = ""
            parts = [f"Artikel {global_pos} von {total}"]
            if source_label:
                parts.append(source_label)
            if title and not _is_audio_marker_line(title):
                parts.append(title)
            header_line = " — ".join(parts)
            if is_podcast:
                header_line += " (Podcast)"
            lines.append(f"#### {header_line}")
            lines.append("")
            # Body absichern: H1-H3-Markdown-Headings (#, ##, ###) im entry_text
            # entfernen — das ist hier per Definition Body-Text und darf nicht als
            # H3 gerendert werden. Verhindert dass GPT-5.x-Output mit zusätzlichem
            # `### Title` den ganzen Beitragstext als H3 (fett) rendert.
            # `#### ` (H4) bleibt erhalten — das sind echte Sub-Headings wie
            # „Was bleibt:" oder Bucket-Titel.
            entry_text_clean = re.sub(r'^\s*#{1,3}\s+', '', entry_text, flags=re.MULTILINE)
            # Falls inmitten der Zeile noch `### ` o.ä. steht (durch Joining): durch Space ersetzen
            entry_text_clean = re.sub(r'(?<=\S)\s*#{1,3}\s+', ' ', entry_text_clean).strip()
            lines.append(entry_text_clean)
            lines.append("")
            # Ende-Marker nach jedem Artikel (außer beim letzten) — akustisch klare Trennung
            # beim Vorlesen (ElevenReader & Co.). Vor „Was du mitnehmen kannst" kein Marker.
            if global_pos < total:
                lines.append("Artikel Ende.")
                lines.append("")

    lines.extend([
        "#### Was du mitnehmen kannst",
        takeaway,
        "",
    ])

    # === Abschluss-Block: Was bleibt + Zitat/Wunsch zum Tag ===
    if all_sections:
        closing = _extract_closing_block(all_sections)
        if closing:
            lines.extend([
                "#### Bis zum nächsten Mal",
                closing,
                "",
            ])

    lines.append("Ende der Kurzfassung.")
    return "\n".join(lines).strip()


def _build_deterministic_genius_summary(sections: List[dict], mode: str = "standard") -> str:
    content_sections = [s for s in sections if s["type"] != "transition" and not s.get("_recap") and not s.get("_essenz") and not s.get("_verabschiedung") and not s.get("_preview")]
    if not content_sections:
        return ""

    # Deterministische Einträge als [idx]-Liste bauen und durch die volle Format-
    # Pipeline schicken — so bekommt auch der Sicherheitsnetz-Fallback die sauberen
    # Ressort- (### Bucket) und Beitrags-Überschriften (#### Artikel N von M — …),
    # statt nur rohe [1]-Zeilen. (Bug bis 3.6.: Fallback ohne formatierte Titel.)
    raw_lines = [f"[{idx}] {_build_genius_entry_text(section, mode)}" for idx, section in enumerate(content_sections, 1)]
    raw = "### Kompakte Vollzusammenfassung\n\n*Hier ist das ganze Briefing in dichter, aber vollständiger Form.*\n\n" + "\n".join(raw_lines)
    try:
        formatted = _format_genius_summary_output(raw, content_sections, mode, all_sections=sections)
        if formatted and "####" in formatted:
            return formatted
    except Exception:
        pass

    # Ur-Fallback: rohes [idx]-Format, falls die Format-Pipeline scheitert
    lines = [
        "### Kompakte Vollzusammenfassung",
        "",
        "*Hier ist das ganze Briefing in dichter, aber vollständiger Form.*",
        "",
    ]
    for idx, section in enumerate(content_sections, 1):
        lines.append(f"[{idx}] {_build_genius_entry_text(section, mode)}")
    lines.extend([
        "",
        "#### Was du mitnehmen kannst",
        _build_deterministic_genius_takeaway(content_sections, mode),
        "",
        "Ende der Kurzfassung.",
    ])
    return "\n".join(lines).strip()


def _build_genius_summary_meta(content_sections: List[dict], used_fallback: bool, ids_ok: bool, coverage_ok: bool, mode: str) -> dict:
    weather = 0
    articles = 0
    paywall = 0
    podcasts = 0

    for section in content_sections:
        if section.get("_weather"):
            weather += 1
        elif section.get("type") == "podcast":
            podcasts += 1
        elif section.get("type") == "manual":
            paywall += 1
        else:
            articles += 1

    return {
        "complete": True,
        "total": len(content_sections),
        "weather": weather,
        "articles": articles,
        "paywall": paywall,
        "podcasts": podcasts,
        "used_fallback": used_fallback,
        "ids_ok": ids_ok,
        "coverage_ok": coverage_ok,
        "mode": _normalize_genius_summary_mode(mode),
        "mode_label": (
            "Kurzversion"
            if _normalize_genius_summary_mode(mode) == "short"
            else ("Langversion" if _normalize_genius_summary_mode(mode) == "long" else "Standard")
        ),
    }


PREVIEW_PROMPT = """Du schreibst die Eröffnung eines Audio-Briefings: eine Vorschau auf ALLE Themen des Tages.
Du bekommst die Titel sämtlicher Beiträge. Deine Aufgabe: jeden einzelnen Beitrag erwähnen, aber so geschickt verpackt, dass es sich wie ein flüssiger, unterhaltsamer Teaser anhört — nicht wie eine Aufzählung.

REGELN:
- JEDEN Titel erwähnen. Keinen weglassen.
- Thematisch gruppieren: Fasse zusammengehörende Themen in einem Satz zusammen. Beispiel: "Lokal hat Tübingen gleich drei Geschichten parat: ein Maori-Schnitzwerk kehrt heim, eine Fahrschule setzt auf VR, und die Metzgerei Zeeb wird 100."
- Übergänge zwischen den Gruppen variieren: "International wird es ernst:", "Aus der Tech-Welt:", "Und dann wären da noch:", "Dazu kommen:" etc.
- Ton: Wie ein kluger Freund, der einem beim Kaffee erzählt was heute los ist. Souverän, neugierig machend, nicht aufgesetzt lustig.
- Kein Pathos, keine rhetorischen Fragen, kein "Bleiben Sie dran", kein "Spannend wird es auch bei".
- Am Ende die Gesamtzahl nennen.
- Deutsch. Kurze, klare Sätze.

FORMAT (exakt):
1) ### Willkommen zum Briefing
2) LEERZEILE
3) *{datum}*
4) LEERZEILE
5) Vorschau-Text als Fließtext (so viele Sätze wie nötig, um alle Themen unterzubringen)
6) LEERZEILE
7) Abschluss exakt: „Weiter geht's."

Gib NUR den Text aus. Keine Kommentare, keine Erklärungen."""


def _build_briefing_preview(client, sections: List[dict], model: str) -> Optional[dict]:
    """Erzeugt eine KI-generierte Vorschau für den Briefing-Anfang."""
    content_sections = [
        s for s in sections
        if s["type"] != "transition" and not s.get("_weather") and not s.get("_recap") and not s.get("_essenz") and not s.get("_verabschiedung")
    ]
    if len(content_sections) < 3:
        return None

    titles = []
    for s in content_sections:
        for line in s["content"].splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                titles.append(stripped[4:].strip())
                break

    if not titles:
        return None

    generated_at = get_berlin_now()
    date_str = generated_at.strftime("%d. %B %Y")
    month_map = {
        "January": "Januar", "February": "Februar", "March": "März",
        "April": "April", "May": "Mai", "June": "Juni",
        "July": "Juli", "August": "August", "September": "September",
        "October": "Oktober", "November": "November", "December": "Dezember",
    }
    for en, de in month_map.items():
        date_str = date_str.replace(en, de)

    n_articles = len([s for s in content_sections if s.get("type") in ("article", "manual")])
    n_podcasts = len([s for s in content_sections if s.get("type") == "podcast"])

    parts = []
    if n_articles:
        parts.append(f"{n_articles} Beiträge")
    if n_podcasts:
        parts.append(f"{n_podcasts} Podcasts")
    count_info = " und ".join(parts) if parts else f"{len(content_sections)} Beiträge"

    titles_text = "\n".join(f"- {t}" for t in titles)
    user_input = f"Datum: {date_str}\nAnzahl: {count_info}\n\nTitel der Beiträge:\n{titles_text}"

    prompt = PREVIEW_PROMPT.replace("{datum}", date_str)
    result = summarize(client, user_input, prompt, model, max_tokens=2000)

    if result:
        return {
            "type": "article",
            "content": result.strip(),
            "_preview": True,
            "_source_text": user_input,
        }

    # Fallback: einfache deterministische Vorschau
    preview_titles = titles[:8]
    preview_list = ", ".join(preview_titles)
    if len(titles) > 8:
        preview_list += f" und {len(titles) - 8} weitere Themen"

    text = (
        f"### Willkommen zum Briefing\n\n"
        f"*{date_str}*\n\n"
        f"Heute mit {count_info}. Es geht unter anderem um {preview_list}.\n\n"
        f"Weiter geht's."
    )

    return {
        "type": "article",
        "content": text,
        "_preview": True,
        "_source_text": user_input,
    }


def _build_deterministic_recap(sections: List[dict]) -> str:
    """Fällt auf einen regelbasierten Recap zurück, wenn der Modell-Recap IDs auslässt oder doppelt."""

    heading_map = {
        "Lokales": "Lokale Nachrichten",
        "Innenpolitik & Gesellschaft": "Innenpolitik-Gesellschaft",
        "Internationale Politik": "Internationale Politik",
        "Wirtschaft & Finanzen": "Wirtschaft & Finanzen",
        "Technologie & Digitales": "Technologie",
        "Wissenschaft & Gesundheit": "Wissenschaft",
        "Medien & Kultur": "Medien-Kultur",
        "Kriminalität & Unglücke": "Kriminalität-Unglücke",
        "Sport": "Sport",
        "Vermischtes": "Vermischtes",
        "Podcast-Zusammenfassungen": "Podcast-Zusammenfassungen",
    }
    heading_order = [
        "Wetter",
        "Lokale Nachrichten",
        "Innenpolitik-Gesellschaft",
        "Internationale Politik",
        "Wirtschaft & Finanzen",
        "Technologie",
        "Wissenschaft",
        "Medien-Kultur",
        "Kriminalität-Unglücke",
        "Sport",
        "Vermischtes",
        "Podcast-Zusammenfassungen",
    ]

    def _extract_intro(content: str) -> str:
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if line.startswith("*") and line.endswith("*") and not line.startswith("**"):
                return _markdown_line_to_plain(line)
        return ""

    def _extract_what_remains_sentence(content: str) -> str:
        capture = False
        parts: List[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not capture and re.match(r"^#{1,4}\s*Was bleibt:\s*$", line, flags=re.IGNORECASE):
                capture = True
                continue
            if not capture:
                continue
            if not line:
                if parts:
                    break
                continue
            if _contains_regular_end_marker(line):
                break
            if _contains_briefing_end_marker(line):
                break
            if _contains_podcast_end_marker(line):
                break
            parts.append(_markdown_line_to_plain(line))
        text = " ".join(parts).strip()
        if not text:
            return ""
        sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
        return sentence

    def _recap_sentence(section: dict) -> str:
        content = section["content"]
        title = _extract_title(content)
        if section.get("_weather"):
            summary = _extract_what_remains_sentence(content) or _extract_intro(content)
        elif section.get("type") == "podcast":
            summary = _extract_intro(content) or _extract_what_remains_sentence(content)
            if summary and not re.match(r"^(Der|Im)\s+Podcast\b", summary):
                summary = f"Der Podcast „{title}“ behandelt {summary[:1].lower() + summary[1:]}" if summary else title
        else:
            summary = _extract_intro(content) or _extract_what_remains_sentence(content)

        if not summary:
            summary = title

        summary = re.sub(r"\s+", " ", summary).strip()
        if summary == "Hier nochmal alles sortiert im Schnelldurchlauf.":
            summary = _extract_what_remains_sentence(content) or title
        if (
            section.get("type") != "podcast"
            and not section.get("_weather")
            and title
            and summary
            and not normalize_unicode(summary).lower().startswith(normalize_unicode(title).lower())
        ):
            summary = f"{title}: {summary}"
        if not re.search(r"[.!?…]$", summary):
            summary += "."
        return summary

    def _looks_like_weather_section(section: dict) -> bool:
        if section.get("_weather"):
            return True
        text = normalize_unicode(
            f"{section.get('content', '')}\n{section.get('_source_text', '')}"
        ).lower()
        weather_markers = (
            "wetter",
            "temperatur",
            "niederschlag",
            "schauer",
            "regen",
            "graupel",
            "schnee",
            "sonnenstunden",
            "bewölkung",
            "frost",
            "nebel",
            "windiger",
        )
        hits = sum(1 for marker in weather_markers if marker in text)
        return hits >= 3

    def _recap_heading_for_section(section: dict, fallback_heading: str) -> str:
        if section.get("type") == "podcast":
            return "Podcast-Zusammenfassungen"
        if _looks_like_weather_section(section):
            return "Wetter"

        combined_text = normalize_unicode(
            f"{section.get('content', '')}\n{section.get('_source_text', '')}"
        )
        if _is_home_region_article(combined_text):
            return "Lokale Nachrichten"

        return heading_map.get(fallback_heading or "", fallback_heading or "Vermischtes")

    grouped: dict[str, List[str]] = defaultdict(list)
    current_heading = ""
    for section in sections:
        if section["type"] == "transition":
            heading = section["content"].strip()
            if not heading or heading == "Zusammenfassung":
                continue
            if heading == "Ab jetzt Podcasts.":
                heading = "Podcast-Zusammenfassungen"
            current_heading = heading
            continue

        recap_heading = _recap_heading_for_section(section, current_heading)
        if recap_heading:
            grouped[recap_heading].append(_recap_sentence(section))

    lines = [
        "### Das war's für heute",
        "",
        "*Hier nochmal alles sortiert im Schnelldurchlauf.*",
        "",
    ]
    for heading in heading_order:
        sentences = grouped.get(heading, [])
        if not sentences:
            continue
        lines.extend([f"#### {heading}", ""])
        for sentence in sentences:
            lines.append(sentence)
            lines.append("")

    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend(["", "Ende des Briefings."])
    return "\n".join(lines)


NARRATIVE_WEAVE_PROMPT = """Du verwandelst eine Reihe einzelner Briefing-Beiträge in EINEN flüssigen, zusammenhängenden Erzähltext im Podcast-Stil — didaktisch klar, hörbar strukturiert und unterhaltsam zu konsumieren.

EINGABE
Du bekommst eine Liste von 3-12 fertigen Briefing-Sections (jede mit ### Titel, kursivem Einordnungssatz, Fließtext). Diese Sections gehören thematisch zusammen.

AUFGABE
Fasse sie zu EINER zusammenhängenden Erzähl-Section zusammen — wie ein gut moderierter Podcast-Block. Beim VORLESEN soll der Hörer jederzeit wissen: Wo bin ich gerade, welcher Beitrag ist das, wie viele kommen noch — UND er soll das Gefühl haben, dass jemand die Beiträge wirklich verstanden hat und ihm hilft, sie zu behalten.

DIDAKTIK & TON (das ist der Charakter des Erzählmodus)
- Sprich wie ein kluger, gut gelaunter Freund, der dir die Welt erklärt. Nicht Professor, nicht Nachrichtensprecher.
- Trockener Humor erlaubt, kleine Pointen wenn sie passen — aber nie auf Kosten der Substanz oder bei tragischen Themen.
- Konkrete Bilder statt abstrakte Begriffe: „so groß wie zwei Fußballfelder" statt „14.000 Quadratmeter".
- Hin und wieder ein kurzer, unaufdringlicher persönlicher Kommentar oder eine Einordnung erlaubt — aber sparsam, damit es nicht meinungslastig wird.
- KEIN Pathos, kein Drama-Aufpumpen, keine Phrasen wie „in Zeiten wie diesen".

BEITRAGS-AUFBAU IM ERZÄHLFLUSS (PFLICHT)
Jeder einzelne Beitrag in der Section folgt diesem Mini-Muster:

1. **Hörbarer Beitrags-Marker** (Position + Gesamtzahl), z.B.:
   - „Beginnen wir mit dem ersten von acht Themen aus der Region..."
   - „Zweitens, ebenfalls aus dem Tagblatt: ..."
   - „Beim vierten Beitrag wird es dunkler: ..."
   - „Wir sind beim fünften von acht — diesmal geht es um..."
   - „Der vorletzte Beitrag aus dieser Reihe: ..."
   - „Damit zum letzten Thema dieser Section: ..."
   Variere stark, nutze natürliche Sprache, NICHT die starre Floskel „Beitrag X von Y".

2. **Erzählender Hauptteil** — die GLEICHE inhaltliche Tiefe wie im Originalbeitrag. Nicht kürzen, nicht zusammenfassen, sondern in Erzählsprache umschreiben. Alle konkreten Namen, Zahlen, Zitate, Daten und Fakten aus dem Original MÜSSEN erhalten bleiben. Quellen elegant eingewoben („wie das Tagblatt schreibt", „laut BBC"). Ein Beitrag der im Original 250 Wörter hat, soll auch im Erzähltext ~250 Wörter haben — nicht 50.

3. **„Was bleibt"-Mini-Marker am Ende** des Beitrags — ein einziger Satz im Fließtext, der den Kern festhält. Auch hier variiert formuliert, nicht starr:
   - „Was hängenbleibt: ..."
   - „Der Punkt ist: ..."
   - „Wenn du dir nur eine Sache merkst: ..."
   - „Im Kern geht es darum, dass ..."
   - „Festhalten lässt sich: ..."
   Dieser Mini-Marker ist KEIN eigener Absatz und KEINE Überschrift, sondern der letzte Satz im Beitragsabsatz, klar erkennbar formuliert.

REGELN
- VOLLSTÄNDIGKEIT IST ABSOLUT KRITISCH. Du bekommst N Beiträge als Input und du MUSST genau N Absätze als Output liefern. KEIN EINZIGER Beitrag darf fehlen, verschmelzen oder weggelassen werden. Auch kurze, scheinbar unwichtige Meldungen (Polizei, Sport, Kurioses) MÜSSEN als eigener Absatz erscheinen. Prüfe VOR der Ausgabe: Hast du für JEDEN nummerierten Input-Beitrag einen eigenen Absatz mit Beitrags-Marker geschrieben?
- KEINE harten Marker („Weiter geht's", „#### Was bleibt:" als Block), KEINE Quellenüberschriften vor Beiträgen, KEINE Aufzählungszeichen, KEINE Bullets, KEINE ###-Unterüberschriften innerhalb der Section.
- Aktive Sprache, gesprochener Stil, Schlüsselbegriffe in Fettdruck.

FORMATIERUNG
Eine Section beginnt immer mit:
### [Titel der Section, z.B. „Aus Tübingen und der Region"]

*[Kurzer kursiver Einordnungssatz, was die Section abdeckt]*

[Erster Übergangs-Satz, der die Anzahl der Beiträge nennt: „In der Region gibt es heute acht Themen — los geht's."]

[Dann pro Beitrag EIN eigener Absatz mit Beitrags-Marker am Anfang und Was-bleibt-Mini-Marker am Ende.]

Gib NUR die fertige Section aus. Keine Meta-Kommentare, keine Erklärungen davor oder danach."""


def _weave_sections_narrative(client, sections_to_weave: List[dict], section_title: str, intro_hint: str, model: str, depth: str = "standard", api_keys: Optional[dict] = None) -> Optional[str]:
    """Verwebt mehrere Briefing-Sections zu einer Erzähl-Section.

    depth:
        "standard" = kompakter Podcast-Überblick (~70% der Original-Länge)
        "detailed" = volle Original-Tiefe, jeder Beitrag so ausführlich wie im klassischen Briefing
    """
    if not sections_to_weave:
        return None

    parts = [f'BEITRÄGE FÜR DIE SECTION "{section_title}":\n']
    parts.append(f"Intro-Hinweis: {intro_hint}\n\n")
    if depth == "detailed":
        parts.append("LÄNGEN-ANWEISUNG: AUSFÜHRLICH — jeder Beitrag soll die GLEICHE inhaltliche Tiefe haben wie im Original. Nicht kürzen. Alle Namen, Zahlen, Zitate behalten.\n\n")
    for i, s in enumerate(sections_to_weave, start=1):
        src = s.get("source_label", "Quelle unbekannt")
        parts.append(f"=== Beitrag {i} (Quelle: {src}) ===\n")
        parts.append(s.get("content", "").strip())
        parts.append("\n\n")
    parts.append(f'\n→ Verwebe diese {len(sections_to_weave)} Beiträge zu EINER Section mit dem Titel "{section_title}". Vollständigkeit ist Pflicht — jeder Beitrag muss als eigener Absatz erkennbar sein.')

    user_input = "".join(parts)
    if depth == "detailed":
        # Ausführlich: volles Budget — ~1200 Tokens/Beitrag
        _token_budget = max(8000, len(sections_to_weave) * 1200 + 2000)
    else:
        # Standard: kompakter — ~700 Tokens/Beitrag
        _token_budget = max(6000, len(sections_to_weave) * 700 + 1500)
    _token_budget = min(_token_budget, 16000)  # Hard-Cap
    # Watchdog mit Provider-Switch — wenn primärer LLM hängt/Quota leer, automatisch
    # zum anderen Provider wechseln. Funktioniert nur wenn api_keys übergeben werden.
    if api_keys:
        result = summarize_with_fallback(
            client, user_input, NARRATIVE_WEAVE_PROMPT, model,
            api_keys=api_keys, max_tokens=_token_budget,
        )
    else:
        result = summarize(client, user_input, NARRATIVE_WEAVE_PROMPT, model, max_tokens=_token_budget)
    return result


def _categorize_section_for_narrative(section: dict) -> str:
    """Ordnet eine Section einem Cluster zu — anhand von Quelle und Inhalt."""
    if section.get("_weather"):
        return "_weather"
    if section.get("_recap"):
        return "_recap"
    if section.get("_essenz"):
        return "_essenz"
    if section.get("_verabschiedung"):
        return "_verabschiedung"
    if section.get("_preview"):
        return "_preview"
    if section.get("type") == "podcast":
        return "podcast"

    src = (section.get("source_label") or "").lower()
    content = (section.get("content") or "").lower()[:500]

    # Lokales Tübingen / Region
    local_markers = ["tagblatt", "swp", "gea", "tübing", "reutling", "mössing", "rottenburg", "schwäb", "stuttgart", "heilbronn", "karlsruhe"]
    if any(m in src for m in local_markers) or any(m in content for m in local_markers[:6]):
        return "lokales"

    # Internationale Quellen
    intl_markers = ["bbc", "guardian", "reuters", "ap ", "nytimes", "washington post", "ft.com"]
    if any(m in src for m in intl_markers):
        return "international"

    # Wirtschaft
    biz_markers = ["handelsblatt", "wiwo", "manager magazin", "boerse", "finanzen"]
    if any(m in src for m in biz_markers):
        return "wirtschaft"

    # Tech
    tech_markers = ["heise", "golem", "t3n", "arstechnica", "the verge", "techcrunch", "stadt bremerhaven", "wired"]
    if any(m in src for m in tech_markers):
        return "tech"

    # Tagesschau, ZEIT etc. → unsortierter "politik" Cluster, wird später nochmal geprüft
    return "politik"


def convert_to_narrative_briefing(client, sections: List[dict], model: str, progress_callback: Optional[Callable] = None, depth: str = "standard", api_keys: Optional[dict] = None) -> List[dict]:
    """Wandelt ein fertiges strukturiertes Briefing in den Erzähl-Modus um.

    Gruppiert Sections thematisch und verwebt jede Gruppe zu einem zusammenhängenden
    Erzähl-Block. Wetter, Recap, Essenz und Verabschiedung bleiben unverändert.
    """
    def report(label: str, frac: float):
        if progress_callback:
            progress_callback(label, frac)

    # Sections nach Cluster gruppieren
    clusters = {
        "_weather": [],
        "_preview": [],
        "lokales": [],
        "politik": [],
        "wirtschaft": [],
        "international": [],
        "tech": [],
        "podcast": [],
        "_recap": [],
        "_essenz": [],
        "_verabschiedung": [],
    }
    for s in sections:
        if s.get("type") == "transition":
            continue
        cat = _categorize_section_for_narrative(s)
        clusters.setdefault(cat, []).append(s)

    cluster_meta = [
        ("_weather", "Wetter zum Auftakt", None),
        ("_preview", "Was heute drinsteckt", None),
        ("lokales", "Aus Tübingen und der Region", "Lokale Themen aus Tübingen, der Region und Süddeutschland"),
        ("politik", "Politik, Gesellschaft und Justiz", "Politische und gesellschaftliche Themen aus Deutschland"),
        ("wirtschaft", "Wirtschaft und Finanzen", "Wirtschaft, Märkte, Geldanlage und Rohstoffe"),
        ("international", "International und Welt", "Internationale Ereignisse, Konflikte und Hintergründe"),
        ("tech", "Technologie und Innovation", "Tech-News, KI, Verbraucher-Geräte"),
        ("podcast", "Aus den Podcasts", "Die heutigen Podcast-Folgen im Überblick"),
        ("_recap", "Rückblick", None),
        ("_essenz", "Was wirklich bleibt", None),
        ("_verabschiedung", "Bis zum nächsten Mal", None),
    ]

    result_sections = []
    weavable_keys = {"lokales", "politik", "wirtschaft", "international", "tech", "podcast"}
    n_weavable = sum(1 for k, _, _ in cluster_meta if k in weavable_keys and clusters.get(k))
    done = 0

    for key, title, intro in cluster_meta:
        bucket = clusters.get(key) or []
        if not bucket:
            continue

        # Pass-through Cluster (Wetter, Preview, Recap, Essenz, Verabschiedung)
        if key not in weavable_keys:
            for s in bucket:
                result_sections.append(s)
            continue

        # Weave-Cluster — bei großen Clustern in Sub-Chunks aufteilen,
        # damit das LLM keine Beiträge weglässt
        done += 1
        report(f"Erzähl-Modus: {title}...", 0.85 + (done / max(n_weavable, 1)) * 0.10)

        if len(bucket) == 1:
            # Bei nur einem Beitrag braucht's keinen Verwebungs-Call — übernehmen
            result_sections.append(bucket[0])
            continue

        # Sub-Chunks: max 8 Beiträge pro LLM-Call, damit nichts verloren geht
        MAX_PER_CHUNK = 8
        if len(bucket) <= MAX_PER_CHUNK:
            sub_chunks = [bucket]
        else:
            sub_chunks = [bucket[i:i + MAX_PER_CHUNK] for i in range(0, len(bucket), MAX_PER_CHUNK)]

        for chunk_idx, sub_bucket in enumerate(sub_chunks):
            chunk_title = title if len(sub_chunks) == 1 else f"{title} ({chunk_idx + 1}/{len(sub_chunks)})"
            chunk_intro = intro or title
            if len(sub_chunks) > 1:
                chunk_intro = f"{chunk_intro} — Teil {chunk_idx + 1} von {len(sub_chunks)}"

            woven = _weave_sections_narrative(client, sub_bucket, chunk_title, chunk_intro, model, depth=depth, api_keys=api_keys)
            if woven:
                new_section = {
                    "type": "article",
                    "content": woven,
                    "source_label": chunk_title,
                    "_narrative_cluster": key,
                }
                result_sections.append(new_section)
            else:
                # Fallback: einzelne Sections übernehmen
                for s in sub_bucket:
                    result_sections.append(s)
            continue

        if not sub_chunks:
            # Fallback: einzelne Sections übernehmen
            for s in bucket:
                result_sections.append(s)

    return result_sections


def _append_essenz_section(client, sections: List[dict], model: str) -> bool:
    """Erzeugt die Essenz-Section und hängt sie ans Ende."""
    content_sections = [s for s in sections if s["type"] != "transition"]
    if len(content_sections) <= 4:
        return False

    essenz_input = _build_recap_input(content_sections)
    essenz_text = summarize(client, essenz_input, ESSENZ_PROMPT, model, max_tokens=2000)
    if not essenz_text:
        return False

    essenz_text = _sanitize_briefing_output(_normalize_existing_briefing_markdown(essenz_text))
    # "Ende des Briefings." nur in der Essenz — aus dem Recap entfernen
    for s in sections:
        if s.get("_recap") and "Ende des Briefings." in s["content"]:
            s["content"] = s["content"].replace("Ende des Briefings.", "Weiter geht's.")

    sections.append({
        "type": "article",
        "content": essenz_text,
        "_essenz": True,
        "_source_text": essenz_input,
    })
    return True


def _append_verabschiedung(client, sections: List[dict], model: str) -> bool:
    """Erzeugt eine kurze Verabschiedung mit Zitat und hängt sie als allerletztes ans Briefing."""
    now = datetime.datetime.now()
    hour = now.hour
    if hour < 6:
        tageszeit = "Es ist Nacht (nach Mitternacht)"
    elif hour < 10:
        tageszeit = "Es ist früher Morgen"
    elif hour < 12:
        tageszeit = "Es ist Vormittag"
    elif hour < 14:
        tageszeit = "Es ist Mittag"
    elif hour < 18:
        tageszeit = "Es ist Nachmittag"
    elif hour < 21:
        tageszeit = "Es ist Abend"
    else:
        tageszeit = "Es ist später Abend"
    context = f"{tageszeit}, {now.strftime('%A %d. %B %Y')}."
    verabschiedung = summarize(client, context, VERABSCHIEDUNG_PROMPT, model, max_tokens=500)
    if not verabschiedung:
        return False

    verabschiedung = _sanitize_briefing_output(_normalize_existing_briefing_markdown(verabschiedung))
    # "Ende des Briefings." aus allen vorherigen Sections entfernen — kommt nur noch hier
    for s in sections:
        if "Ende des Briefings." in s.get("content", ""):
            s["content"] = s["content"].replace("Ende des Briefings.", "").strip()

    # Sicherstellen dass die Verabschiedung mit "Ende des Briefings." endet
    if "Ende des Briefings." not in verabschiedung:
        verabschiedung = verabschiedung.rstrip() + "\n\nEnde des Briefings."

    sections.append({
        "type": "article",
        "content": verabschiedung,
        "_verabschiedung": True,
    })
    return True


# Lokaler Spiegel der Eleven-Reader-TXTs in einem NICHT-iCloud-Ordner.
# Grund: Der launchd-Hintergrunddienst darf nach iCloud SCHREIBEN, aber dessen
# Inhalt NICHT auflisten (macOS gibt Agents keine iCloud-Drive-Leseansicht →
# glob liefert 0 Treffer). Das Wochen-Meta liest darum aus diesem Spiegel, der
# frei les-/auflistbar ist. iCloud bleibt fürs Handy-Sync vollständig erhalten.
_LOCAL_META_MIRROR_DIR = Path.home() / ".briefing_meta_mirror"
_LOCAL_META_MIRROR_TXT_DIR = _LOCAL_META_MIRROR_DIR / "Texte"


def _mirror_txt_to_local(filename: str, text: str) -> None:
    """Schreibt eine Eleven-Reader-TXT zusätzlich in den lokalen Meta-Spiegel.
    Best-effort — Fehler hier dürfen das Briefing nie blockieren."""
    try:
        _LOCAL_META_MIRROR_TXT_DIR.mkdir(parents=True, exist_ok=True)
        (_LOCAL_META_MIRROR_TXT_DIR / filename).write_text(text, encoding="utf-8")
    except Exception:
        pass


_ARCHIVE_INDEX_PATH = _LOCAL_META_MIRROR_DIR / ".archive_index.json"


def _record_archived_file(path) -> None:
    """Merkt einen ins iCloud-Archiv geschriebenen Dateipfad in einem LOKALEN Index.
    Grund: Der launchd-Hintergrunddienst kann iCloud NICHT auflisten (glob=0), aber
    sehr wohl per Pfad löschen. Der 21-Tage-Cleanup nutzt diesen Index, um alte
    iCloud-Dateien gezielt zu entfernen. Best-effort — Fehler nie nach außen."""
    try:
        p = str(path)
        if "com~apple~CloudDocs" not in p:
            return  # nur iCloud-Dateien; lokale Ordner werden per glob erfasst
        _ARCHIVE_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if _ARCHIVE_INDEX_PATH.exists():
            try:
                existing = json.loads(_ARCHIVE_INDEX_PATH.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        if p not in existing:
            existing.append(p)
            _ARCHIVE_INDEX_PATH.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


_RECENT_QUOTES_PATH = _LOCAL_META_MIRROR_DIR / ".recent_quotes.json"


def _load_recent_quotes(limit: int = 10) -> list:
    """Zuletzt verwendete Schlusszitate (für Rotation — LLMs haben Lieblingszitate)."""
    try:
        data = json.loads(_RECENT_QUOTES_PATH.read_text(encoding="utf-8"))
        return [q for q in data if isinstance(q, str)][-limit:]
    except Exception:
        return []


def _remember_quote_from_text(text: str) -> None:
    """Extrahiert das Schlusszitat aus der Verabschiedung und merkt es sich,
    damit künftige Briefings es NICHT wiederholen. Best-effort."""
    try:
        quotes = re.findall(r"[„\"“]([^\"“”„]{15,160})[\"“”]", text or "")
        if not quotes:
            return
        q = quotes[-1].strip()
        recent = _load_recent_quotes(limit=14)
        if q in recent:
            recent.remove(q)
        recent.append(q)
        _RECENT_QUOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RECENT_QUOTES_PATH.write_text(json.dumps(recent[-14:], ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _strip_meta_preamble(text: str) -> str:
    """Entfernt eine etwaige Modell-Vorrede vor dem ###-Titel des Wochen-Briefings.
    Opus stellt der Antwort gelegentlich einen Kommentar voran (z.B. 'Der /loop-
    Aufruf war ein Fehlgriff…'). Das echte Briefing beginnt mit '### Wochenrückblick'."""
    if not text:
        return text
    idx = text.find("###")
    if idx > 0:
        return text[idx:].lstrip()
    return text


def _discover_weekly_briefing_texts(archive_dir: str, days: int = 7) -> List[dict]:
    """Findet und dedupliziert Briefing-TXTs der letzten N Tage.

    Dedup-Logik: Mehrere Briefings am selben Tag (z.B. 14 Uhr + 20 Uhr) bleiben
    erhalten. Nur Korrekturen desselben Briefings (selber Base-Timestamp, z.B.
    briefing_2026-03-26_04-01-48 + briefing_2026-03-26_04-01-48_korrigiert_rep05-00)
    werden dedupliziert — die neueste Korrektur gewinnt.

    Returns:
        Liste von {"date": str, "time": str, "path": Path, "text": str, "is_compact": bool}
    """
    from pathlib import Path

    archive = Path(archive_dir)
    # TXT-Dateien liegen im Unterordner "Texte"
    txt_dir = archive / "Texte"
    if not txt_dir.exists():
        # Fallback: direkt im Archiv-Ordner suchen (Rückwärtskompatibilität)
        txt_dir = archive
    if not txt_dir.exists():
        return []

    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    # Drei Namensformate finden:
    # 1) Altes Format: briefing_YYYY-MM-DD_HH-MM-SS_*
    # 2) Mittleres Format: YYYY-MM-DD_HH-MM_briefing_*
    # 3) Neues Format (ab 2026-04-25): YYYY-MM-DD_HH-MM_tagesbriefing_*
    txt_files_old = list(txt_dir.glob("briefing_*_eleven-reader.txt"))
    txt_files_mid = list(txt_dir.glob("*_briefing_*_eleven-reader.txt"))
    txt_files_new = list(txt_dir.glob("*_tagesbriefing_*_eleven-reader.txt"))
    txt_files = sorted(set(txt_files_old + txt_files_mid + txt_files_new), reverse=True)

    # Gruppieren nach Base-Timestamp (Datum + Uhrzeit des Originals)
    by_base: dict = {}  # base_stamp -> list of (path, name, is_compact, is_korrigiert)
    for path in txt_files:
        name = path.stem
        # Format 1: briefing_2026-03-26_04-01-48_...
        match = re.match(r"briefing_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", name)
        # Format 2/3: 2026-04-12_11-30_briefing_... oder _tagesbriefing_...
        if not match:
            match = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})_(?:briefing|tagesbriefing)", name)
        if not match:
            continue
        date_str = match.group(1)
        time_str = match.group(2)
        try:
            file_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        base_stamp = f"{date_str}_{time_str}"
        # Variante-Typ erkennen für Auswahl-Prio:
        # - voll: tagesbriefing_voll oder altes briefing/_kompakt (ohne erzaehl/kompakt-lang/etc.)
        # - erzaehl: hat „erzaehl" im Namen
        # - kompakt-fassung: hat „tagesbriefing_kompakt_" (Geniale neu)
        is_erzaehl = "erzaehl" in name
        is_kompaktfassung = bool(re.search(r"tagesbriefing_kompakt_(?:kurz|standard|lang)", name))
        is_voll = (not is_erzaehl) and (not is_kompaktfassung)
        is_compact = "kompakt" in name and not is_kompaktfassung  # Legacy-Flag
        is_korrigiert = "korrigiert" in name or "repariert" in name or "_rep" in name
        by_base.setdefault(base_stamp, []).append((path, name, is_compact, is_korrigiert, is_voll, is_erzaehl, is_kompaktfassung))

    # Pro Base-Timestamp das beste wählen:
    # 1) Voll-Version bevorzugen (für Wochenbriefing-Analyse am informativsten)
    # 2) Bei mehreren: Korrektur bevorzugen
    # 3) Sonst alphabetisch
    results = []
    for base_stamp in sorted(by_base.keys()):
        candidates = by_base[base_stamp]
        # Sortier-Schlüssel: (is_voll, is_korrigiert, name) — Voll bevorzugt, dann Korrektur
        candidates.sort(key=lambda c: (c[4], c[3], c[1]), reverse=True)
        best_path = candidates[0][0]
        is_compact = candidates[0][2]
        date_str = base_stamp[:10]
        time_str = base_stamp[11:]
        try:
            text = best_path.read_text(encoding="utf-8")
            if text.strip():
                results.append({
                    "date": date_str,
                    "time": time_str,
                    "path": best_path,
                    "text": text,
                    "is_compact": is_compact,
                    "variants": len(candidates),
                })
        except Exception:
            continue

    # Fallback: Wenn keine TXTs, versuche PDFs zu lesen
    if not results:
        results = _discover_weekly_briefing_pdfs(archive, days, cutoff)

    return results


def _extract_text_from_pdf(pdf_path) -> str:
    """Extrahiert Text aus einer PDF-Datei via pdfplumber."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts)
    except Exception as e:
        print(f"[meta-briefing] PDF-Extraktion fehlgeschlagen für {pdf_path}: {e}", file=sys.stderr)
        return ""


def _discover_weekly_briefing_pdfs(archive, days: int, cutoff) -> List[dict]:
    """Fallback: Briefing-PDFs lesen wenn keine TXTs vorhanden.
    Erkennt drei Namensformate (alt/mittel/neu) und bevorzugt Voll-Versionen."""
    from pathlib import Path
    archive = Path(archive) if isinstance(archive, str) else archive

    pdfs_old = list(archive.glob("briefing_*.pdf"))
    pdfs_mid = list(archive.glob("*_briefing_*.pdf"))
    pdfs_new = list(archive.glob("*_tagesbriefing_*.pdf"))
    pdf_files = sorted(set(pdfs_old + pdfs_mid + pdfs_new), reverse=True)

    by_base: dict = {}
    for path in pdf_files:
        name = path.stem
        # Format 1: briefing_YYYY-MM-DD_HH-MM-SS_*
        match = re.match(r"briefing_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", name)
        # Format 2/3: YYYY-MM-DD_HH-MM_briefing_* oder _tagesbriefing_*
        if not match:
            match = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})_(?:briefing|tagesbriefing)", name)
        if not match:
            continue
        date_str = match.group(1)
        time_str = match.group(2)
        try:
            file_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        base_stamp = f"{date_str}_{time_str}"
        is_erzaehl = "erzaehl" in name
        is_kompaktfassung = bool(re.search(r"tagesbriefing_kompakt_(?:kurz|standard|lang)", name))
        is_voll = (not is_erzaehl) and (not is_kompaktfassung)
        is_compact = "kompakt" in name and not is_kompaktfassung
        is_korrigiert = "korrigiert" in name or "repariert" in name or "_rep" in name
        by_base.setdefault(base_stamp, []).append((path, name, is_compact, is_korrigiert, is_voll))

    results = []
    for base_stamp in sorted(by_base.keys()):
        candidates = by_base[base_stamp]
        # Voll-Version bevorzugen, dann Korrektur, dann alphabetisch
        candidates.sort(key=lambda c: (c[4], c[3], c[1]), reverse=True)
        best_path = candidates[0][0]
        is_compact = candidates[0][2]
        date_str = base_stamp[:10]
        time_str = base_stamp[11:]
        text = _extract_text_from_pdf(best_path)
        if text.strip():
            results.append({
                "date": date_str,
                "time": time_str,
                "path": best_path,
                "text": text,
                "is_compact": is_compact,
                "variants": len(candidates),
                "source": "pdf",
            })

    return results


def _build_meta_briefing_input(daily_briefings: List[dict]) -> Tuple[str, dict]:
    """Baut den Input fuer Wochen-Meta-Briefings mit Inventar und gekuerzten Tagesblocks."""
    def _slice_for_meta(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        head_len = int(max_chars * 0.50)
        middle_len = int(max_chars * 0.22)
        tail_len = max_chars - head_len - middle_len
        middle_start = max(0, (len(text) - middle_len) // 2)
        return (
            text[:head_len].rstrip()
            + "\n\n[... Mitte des Tagesbriefings gekürzt; Meta-Auszug springt zum Mittelteil ...]\n\n"
            + text[middle_start:middle_start + middle_len].strip()
            + "\n\n[... weiterer Mittelteil gekürzt; Meta-Auszug springt zum Ende mit Recap/Was-bleibt/Abschluss ...]\n\n"
            + text[-tail_len:].lstrip()
        )

    day_chunks = []
    manifest_lines = []
    total_chars = 0
    truncated_count = 0
    total_budget_chars = 180000
    max_per_briefing = min(28000, max(6000, total_budget_chars // max(len(daily_briefings), 1)))

    for idx, entry in enumerate(daily_briefings, start=1):
        raw_text = entry.get("text", "") or ""
        was_truncated = len(raw_text) > max_per_briefing
        if was_truncated:
            text = _slice_for_meta(raw_text, max_per_briefing)
            text += "\n\n[... gekürzt: Tagesbriefing war länger als das Meta-Briefing-Fenster ...]"
            truncated_count += 1
        else:
            text = raw_text

        time_label = (entry.get("time", "") or "").replace("-", ":")
        compact_label = " (kompakt)" if entry.get("is_compact") else ""
        source_label = entry.get("source", "txt")
        path_name = Path(entry["path"]).name if entry.get("path") else "unbekannte Datei"
        header = f"=== TAGESBRIEFING {idx}: {entry['date']} {time_label}{compact_label} | {source_label} | {path_name} ==="
        day_chunks.append(f"{header}\n{text}")
        total_chars += len(text)
        manifest_lines.append(
            f"- {idx}. {entry['date']} {time_label}{compact_label}: {path_name}, "
            f"{len(raw_text):,} Zeichen original, {len(text):,} Zeichen genutzt".replace(",", ".")
        )

    manifest = "\n".join(manifest_lines)
    combined = (
        "INPUT-INVENTAR\n"
        "Diese Liste dient nur zur Orientierung. Zitiere Dateinamen nicht im Briefing, "
        "aber nutze Datum und Wiederholungen für die Wochenanalyse.\n"
        f"{manifest}\n\n"
        "TAGESBRIEFINGS\n"
        + "\n\n".join(day_chunks)
    )
    return combined, {
        "total_chars_input": total_chars,
        "truncated_briefings": truncated_count,
        "source_files": [Path(e["path"]).name for e in daily_briefings if e.get("path")],
    }


def _validate_meta_briefing_text(text: str) -> dict:
    """Kleine Qualitätsprüfung fuer Wochen-Meta-Briefings."""
    required_sections = [
        "Die Lage in 5 Minuten",
        "Die großen Stränge",
        "Lokales aus Tübingen und Region",
        "Querverbindungen",
        "Unterschätzte Signale",
        "Der Coach-Blick auf nächste Woche",
        "Recap der Woche",
        "Was wirklich bleibt",
        "Bis zum nächsten Wochenrückblick",
    ]
    warnings = []
    notices = []
    normalized = text or ""
    word_count = len(re.findall(r"\b[\wÄÖÜäöüß-]+\b", normalized))

    for section in required_sections:
        if section not in normalized:
            warnings.append(f"Pflichtabschnitt fehlt: {section}")
    if "Ende des Wochen-Briefings." not in normalized:
        warnings.append("Abschlussmarker fehlt.")
    if word_count < 1800:
        warnings.append(f"Wochen-Briefing wirkt zu kurz ({word_count} Wörter).")
    elif word_count < 2400:
        notices.append(f"Wochen-Briefing ist eher knapp ({word_count} Wörter).")
    if word_count > 4600:
        notices.append(f"Wochen-Briefing ist sehr lang ({word_count} Wörter).")
    if re.search(r"\b(als KI|ich kann nicht|JSON|```)\b", normalized, re.IGNORECASE):
        warnings.append("Modell-Meta-Kommentar oder Codeblock im Output gefunden.")

    try:
        english_fragments = _detect_untranslated_english_fragments(normalized)
    except Exception:
        english_fragments = []
    if english_fragments:
        warnings.append("Mögliche englische Restfragmente: " + "; ".join(english_fragments[:3]))

    return {
        "ok": not warnings,
        "warnings": warnings,
        "notices": notices,
        "word_count": word_count,
        "required_sections": required_sections,
    }


def generate_meta_briefing(
    archive_dir: str,
    api_key: str,
    model: str,
    days: int = 7,
    progress_callback: Optional[Callable] = None,
    read_dir: Optional[str] = None,
) -> Optional[dict]:
    """Erzeugt ein Wochen-Meta-Briefing aus archivierten Tagesbriefings.

    Returns:
        {"text": str, "pdf": bytes, "dates": list, "stats": dict} oder None
    """
    def report(step, progress):
        if progress_callback:
            progress_callback(step, progress)

    client = _build_client(api_key, model)

    report("Tagesbriefings werden gesucht...", 0.05)
    # Discovery aus dem lokalen Spiegel (iCloud vom Hintergrunddienst nicht auflistbar).
    _read_from = read_dir or str(_LOCAL_META_MIRROR_DIR)
    daily_briefings = _discover_weekly_briefing_texts(_read_from, days=days)
    if not daily_briefings:
        return None

    report(f"{len(daily_briefings)} Tagesbriefings gefunden, werden aufbereitet...", 0.1)

    combined, input_meta = _build_meta_briefing_input(daily_briefings)

    report("Wochen-Meta-Briefing wird geschrieben...", 0.3)
    meta_text = summarize(
        client,
        combined,
        META_BRIEFING_PROMPT,
        model=model,
        max_tokens=8000,
    )
    if not meta_text:
        return None

    meta_text = _sanitize_briefing_output(_normalize_existing_briefing_markdown(_strip_meta_preamble(meta_text)))
    meta_text = tts_safe(meta_text)
    quality = _validate_meta_briefing_text(meta_text)

    report("PDF wird erzeugt...", 0.85)
    generated_at = get_berlin_now()
    meta_sections = [{"type": "article", "content": meta_text}]

    # PDF in Datei UND als Bytes für Download
    import tempfile
    pdf_bytes = None
    pdf_path = None
    try:
        # Persistent speichern in <archive>/Wochen-Briefings/
        archive_path_obj = Path(archive_dir)
        weekly_dir = archive_path_obj / "Wochen-Briefings"
        weekly_dir.mkdir(parents=True, exist_ok=True)
        ts = generated_at.strftime("%Y-%m-%d_%H-%M")
        pdf_path = weekly_dir / f"{ts}_wochenbriefing_{days}d.pdf"
        create_pdf(meta_sections, str(pdf_path), generated_at, document_title="Wochen-Meta-Briefing")
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
    except Exception as exc:
        print(f"[meta-briefing] PDF-Erzeugung fehlgeschlagen: {exc}", file=sys.stderr)

    # TXT auch persistent
    txt_bytes = None
    txt_path = None
    try:
        txt_bytes = meta_text.encode("utf-8")
        if pdf_path:
            txt_path = pdf_path.with_suffix(".txt")
            txt_path.write_text(meta_text, encoding="utf-8")
    except Exception:
        pass

    report("Fertig.", 1.0)
    return {
        "text": meta_text,
        "pdf": pdf_bytes,
        "pdf_path": str(pdf_path) if pdf_path else None,
        "txt": txt_bytes,
        "txt_path": str(txt_path) if txt_path else None,
        "dates": sorted(set(e["date"] for e in daily_briefings)),
        "stats": {
            "briefings": len(daily_briefings),
            "days": len(set(e["date"] for e in daily_briefings)),
            "total_chars_input": input_meta["total_chars_input"],
            "truncated_briefings": input_meta["truncated_briefings"],
            "source_files": input_meta["source_files"],
            "compact_only_days": sum(1 for e in daily_briefings if e["is_compact"]),
        },
        "quality": quality,
    }


def _append_recap_section(client, sections: List[dict], model: str) -> bool:
    """Erzeugt den Recap neu und hängt ihn an die Sections an."""
    content_sections = [s for s in sections if s["type"] != "transition"]
    if len(content_sections) <= 2:
        return False

    all_summaries = _build_recap_input(content_sections)
    recap = _build_deterministic_recap(sections)
    if not recap:
        return False

    sections.append({"type": "transition", "content": "Zusammenfassung"})
    sections.append({
        "type": "article",
        "content": recap,
        "_recap": True,
        "_source_text": all_summaries,
    })
    return True


def _build_content_check_payloads(sections: List[dict]) -> tuple[list, list]:
    """Bereitet Payloads für den Plausibilitäts-Check und die zugehörigen Section-Indizes auf."""
    payloads = []
    section_indices = []
    for idx, section in enumerate(sections):
        if section["type"] == "transition":
            continue
        if section.get("_preview"):
            continue
        if section.get("_essenz"):
            continue
        if section.get("_verabschiedung"):
            continue
        source_text = section.get("_source_text")
        if not source_text:
            continue
        title = _extract_title(section["content"])
        if section.get("_weather"):
            label = "Wetter"
            item_type = "weather"
        elif section.get("_recap"):
            label = "Recap"
            item_type = "recap"
        elif section["type"] == "podcast":
            label = f"Podcast {idx + 1}: {title}"
            item_type = "podcast"
        else:
            source_name = section.get("source_label", "Quelle unbekannt")
            label = f"{source_name}: {title}"
            item_type = "article"
        payloads.append({
            "_check_key": str(len(payloads)),
            "label": label[:140],
            "item_type": item_type,
            "source_text": source_text,
            "summary_text": section["content"],
        })
        section_indices.append(idx)
    return payloads, section_indices


def _content_check_label_for_section(section: dict, idx: int) -> str:
    title = _extract_title(section.get("content", ""))
    if section.get("_weather"):
        return "Wetter"
    if section.get("_recap"):
        return "Recap"
    if section.get("type") == "podcast":
        return f"Podcast {idx + 1}: {title}"
    source_name = section.get("source_label", "Quelle unbekannt")
    return f"{source_name}: {title}"


_CITATION_PATTERNS = re.compile(
    r"(?:laut|nach Angaben|wie|berichtet(?:e[tn])?|so|gegenüber|sagte?\s+(?:\w+\s+){0,3})"
    r"\s+(?:der|die|das|dem|den|einer|einem|eines)?\s*",
    re.IGNORECASE,
)


def _infer_source_for_lint(source_text: str) -> str:
    """Konservative Quellerkennung für den Lint-Check.

    Nur HOCHKONFIDENTE Treffer melden:
    1. Domain im Quelltext (z.B. 'tagblatt.de', 'zeit.de')
    2. URL im Text (Domain-Match)
    Substring-Matches im Fließtext werden NICHT mehr verwendet, um false
    positives zu vermeiden (z.B. 'Welt' in 'Umwelt' oder 'Tübinger Welt').
    """
    if not source_text:
        return "Quelle unbekannt"

    head = source_text[:1500]
    lowered = head.lower()

    _SOCIAL_MEDIA_NOISE = {"instagram", "youtube", "facebook", "tiktok", "twitter", "x.com", "threads", "reddit", "linkedin"}

    # 1. Explizite Domain-Marker im Text (sehr zuverlässig)
    DOMAIN_MARKERS = [
        ("tagblatt.de", "Schwäbisches Tagblatt"),
        ("swp.de", "Schwäbisches Tagblatt"),
        ("gea.de", "GEA"),
        ("zeit.de", "ZEIT"),
        ("spiegel.de", "SPIEGEL"),
        ("welt.de", "WELT"),
        ("faz.net", "FAZ"),
        ("handelsblatt.com", "Handelsblatt"),
        ("tagesschau.de", "tagesschau"),
        ("zdf.de", "ZDF"),
        ("ntv.de", "n-tv"),
        ("n-tv.de", "n-tv"),
        ("sueddeutsche.de", "SZ"),
        ("heise.de", "heise"),
        ("bbc.com", "BBC"),
        ("bbc.co.uk", "BBC"),
        ("arstechnica.com", "arstechnica"),
        ("stadt-bremerhaven.de", "stadt bremerhaven"),
    ]
    for marker, label in DOMAIN_MARKERS:
        if marker in lowered:
            return label

    # 2. URL im Quelltext (Domain-basiert), Social-Media ausgenommen
    for url_match in re.finditer(r"https?://[^\s)]+", source_text[:1500], flags=re.IGNORECASE):
        url_lower = url_match.group(0).lower()
        if not any(sm in url_lower for sm in _SOCIAL_MEDIA_NOISE):
            return source_label_from_url(url_match.group(0))

    return "Quelle unbekannt"


_UNTRANSLATED_ENGLISH_PATTERNS = (
    r"\bupcoming\b",
    r"\baerial\b",
    r"\bdisplay(?:s)?\b",
    r"\bcancelled\b",
    r"\bcanceled\b",
    r"\bunforeseen\b",
    r"\btechnical difficult(?:y|ies)\b",
    r"\bin line with\b",
    r"\bstandard safety protocols\b",
    r"\bdisappointment\b",
    r"\binconvenience\b",
    r"\battendees\b",
    r"\baccording to\b",
    r"\bspokesperson\b",
    r"\blaunched\b",
    r"\bstarted\b",
    r"\broughly\b",
    r"\bnearly\b",
    r"\baround\b",
    r"\bper square foot\b",
    r"\btenant sales\b",
    r"\bshoppers?\b",
    r"\bproperty\b",
    r"\bproperties\b",
)


def _detect_untranslated_english_fragments(text: str) -> List[str]:
    """Findet kurze englische Restfragmente in deutschen Briefing-Abschnitten."""
    normalized = normalize_unicode(text or "")
    lowered = normalized.lower()
    hits: List[str] = []
    for pattern in _UNTRANSLATED_ENGLISH_PATTERNS:
        if re.search(pattern, lowered):
            token = re.sub(r"\\b", "", pattern).replace("(?:s)?", "s").strip("\\")
            hits.append(token)
    if len(hits) >= 2:
        return [
            "Der Abschnitt enthält wahrscheinlich unübersetzte englische Restfragmente "
            f"({', '.join(hits[:5])})."
        ]

    return []


def _run_output_lint(sections: List[dict], expected_article_count: Optional[int] = None) -> dict:
    items = []
    checked = 0
    warnings = 0
    notices = 0

    for idx, section in enumerate(sections):
        if section.get("type") == "transition":
            continue
        if section.get("_preview") or section.get("_essenz") or section.get("_verabschiedung") or section.get("_ressort_header"):
            continue
        checked += 1
        content = normalize_unicode(section.get("content", "")).strip()
        source_text = section.get("_source_text", "") or ""
        label = _content_check_label_for_section(section, idx)
        hard_issues: List[str] = []
        soft_issues: List[str] = []

        contamination_issues = _detect_briefing_boilerplate_contamination(content)
        for issue in contamination_issues:
            if issue not in hard_issues:
                hard_issues.append(issue)

        for issue in _detect_untranslated_english_fragments(content):
            if issue not in hard_issues:
                hard_issues.append(issue)

        nonempty_lines = [line.strip() for line in content.splitlines() if line.strip()]
        # Führende Audio-Marker ("Beitrag X von Y.") ignorieren — die setzt der
        # Annotator vor den ### Titel. Sonst meldet der Lint fälschlich "Titel fehlt",
        # wenn er nach der Annotation läuft (z.B. im CLI-Pfad build_pdf_from_claude_json).
        _title_lines = [ln for ln in nonempty_lines if not _is_audio_marker_line(ln)]
        if content:
            if not _title_lines or not _title_lines[0].startswith("### "):
                hard_issues.append("Dem Abschnitt fehlt eine saubere Titel-Überschrift im Briefing-Format.")

            if not section.get("_recap") and section.get("type") != "transition":
                if not re.search(r"(?m)^####\s*Was bleibt:\s*$", content):
                    hard_issues.append("Dem Abschnitt fehlt der Block `Was bleibt:` im Briefing-Format.")

                if section.get("type") == "podcast":
                    if not any(_contains_podcast_end_marker(line) for line in nonempty_lines[-3:]):
                        hard_issues.append("Dem Abschnitt fehlt der erwartete Abschluss `Ende der Podcastzusammenfassung.`.")
                else:
                    if not any(_contains_regular_end_marker(line) for line in nonempty_lines[-3:]):
                        hard_issues.append("Dem Abschnitt fehlt der erwartete Abschluss `Weiter geht's.`.")

                if len(nonempty_lines) >= 2 and not section.get("_weather"):
                    # Suche kursiven Einordnungssatz in den ersten 4 Zeilen nach dem Titel
                    has_italic_intro = False
                    for cand in nonempty_lines[1:5]:
                        # Echter Kursiv-Satz: *...* aber nicht **...**
                        if (cand.startswith("*") and cand.endswith("*")
                                and not cand.startswith("**")
                                and not cand.endswith("**")):
                            has_italic_intro = True
                            break
                        # Auch akzeptieren: Zeile beginnt mit *...* gefolgt von normalem Text (eingeleiteter Kursivsatz)
                        if re.match(r"^\*[^*].+?[^*]\*\s*$", cand):
                            has_italic_intro = True
                            break
                    if not has_italic_intro:
                        soft_issues.append("Die Einordnungszeile unter dem Titel ist nicht sauber kursiv formatiert.")

            source_label = section.get("source_label") or "Quelle unbekannt"
            inferred_source = _infer_source_for_lint(source_text) if source_text else "Quelle unbekannt"
            if (
                not section.get("_weather")
                and not section.get("_recap")
                and section.get("type") != "podcast"
                and inferred_source
                and inferred_source != "Quelle unbekannt"
            ):
                if source_label == "Quelle unbekannt":
                    hard_issues.append(f"Das Quellenlabel fehlt; der Quelltext deutet auf `{inferred_source}`.")
                elif source_label != inferred_source:
                    hard_issues.append(f"Das Quellenlabel wirkt unplausibel; der Quelltext deutet eher auf `{inferred_source}` als auf `{source_label}`.")

        level = "ok"
        if hard_issues:
            level = "warn"
            warnings += 1
        elif soft_issues:
            level = "notice"
            notices += 1
        else:
            continue

        summary = (
            "Der Abschnitt enthält formale oder inhaltliche Fremdtext-/Layoutprobleme."
            if hard_issues
            else "Der Abschnitt ist inhaltlich okay, aber formal noch nicht ganz sauber."
        )

        items.append({
            "_check_key": f"lint:{idx}",
            "section_index": idx,
            "label": label,
            "item_type": "output_lint",
            "level": level,
            "summary": summary,
            "hard_issues": hard_issues[:4],
            "soft_issues": soft_issues[:4],
            "issues": hard_issues[:4] + soft_issues[:4],
            "source_support": _extract_source_support_snippets(
                source_text,
                content,
                hard_issues + soft_issues + [label],
                limit=2,
            ),
            "failed": False,
            "local_lint": True,
        })

    # === Coverage-Check: leere/fehlende Beitragsslots ===
    # Erkennt Fälle wie 24.5., wo der LLM 6 von 61 Beiträgen weggelassen hat.
    # Filter wie in _annotate_section_progress_markers — nur "echte" Beiträge zählen.
    _coverage_indices = [
        idx for idx, sec in enumerate(sections)
        if sec.get("type") != "transition"
        and not sec.get("_preview")
        and not sec.get("_essenz")
        and not sec.get("_verabschiedung")
        and not sec.get("_ressort_header")
        and not sec.get("_recap")
    ]
    _coverage_total = len(_coverage_indices)
    _empty_positions = []
    for _position, _section_index in enumerate(_coverage_indices, start=1):
        _content = (sections[_section_index].get("content") or "").strip()
        # Strippe Audio-Marker-Zeilen vor Leer-Prüfung
        _nonempty_lines = [
            ln for ln in _content.splitlines()
            if ln.strip() and not _is_audio_marker_line(ln.strip())
        ]
        # Beitrag gilt als "leer/zu dünn" bei weniger als 3 echten Zeilen oder < 80 Zeichen
        _joined = "\n".join(_nonempty_lines).strip()
        if len(_nonempty_lines) < 3 or len(_joined) < 80:
            _empty_positions.append(_position)

    if _empty_positions:
        items.append({
            "_check_key": "lint:coverage",
            "section_index": -1,
            "label": "Briefing-Vollständigkeit",
            "item_type": "output_lint",
            "level": "warn",
            "summary": (
                f"{len(_empty_positions)} von {_coverage_total} Beitragsslots sind leer oder zu dünn — "
                f"das LLM hat Beiträge ausgelassen."
            ),
            "hard_issues": [
                f"Leer/zu dünn an Position {', '.join(str(p) for p in _empty_positions[:10])}"
                + (f" (… und {len(_empty_positions)-10} weitere)" if len(_empty_positions) > 10 else "")
            ],
            "soft_issues": [],
            "issues": [f"{len(_empty_positions)} Beiträge fehlen oder sind leer"],
            "source_support": [],
            "failed": False,
            "local_lint": True,
            "coverage_check": True,
            "empty_positions": _empty_positions,
        })
        warnings += 1

    # === Coverage-Check 2: am Ende ganz fehlende Beiträge ===
    # Wenn der LLM weniger Beiträge liefert als laut Eingabe erwartet (z.B. hinten
    # abbricht), existiert kein leerer Slot — die Section fehlt komplett. Nicht
    # reparierbar (kein Quelltext für die Lücke), daher nur `notice` zur Sichtbarkeit.
    if expected_article_count and _coverage_total < expected_article_count:
        _missing_count = expected_article_count - _coverage_total
        items.append({
            "_check_key": "lint:coverage_missing",
            "section_index": -1,
            "label": "Briefing-Vollständigkeit",
            "item_type": "output_lint",
            "level": "notice",
            "summary": (
                f"Möglicherweise {_missing_count} Beitrag(e) ausgelassen — laut Eingabe wurden "
                f"{expected_article_count} erwartet, im Briefing stehen {_coverage_total}. "
                f"(Differenz kann auch von fehlgeschlagenen URL-Fetches kommen.)"
            ),
            "hard_issues": [],
            "soft_issues": [
                f"{_missing_count} Beitrag(e) weniger als erwartet — Eingabe prüfen oder neu erstellen."
            ],
            "issues": [f"{_missing_count} Beiträge fehlen ganz"],
            "source_support": [],
            "failed": False,
            "local_lint": True,
            "coverage_check": True,
            "missing_count": _missing_count,
        })
        notices += 1

    # === Title-Dedup: identische Beitragstitel erkennen ===
    # Der Pre-LLM-Duplikatfilter arbeitet auf Rohtext-Ähnlichkeit; wenn zwei
    # verschiedene Quellen (z.B. Reportage + Kommentar) am Ende dieselbe Headline
    # bekommen, fällt das durch. Hier: identische finale Titel markieren.
    _title_map: dict = {}
    for _position, _section_index in enumerate(_coverage_indices, start=1):
        _sec = sections[_section_index]
        if _sec.get("_weather") or _sec.get("_recap"):
            continue
        _title = _markdown_line_to_plain(_extract_title(_sec.get("content", "") or "")).strip().lower()
        if not _title or len(_title) < 5:
            continue
        _title_map.setdefault(_title, []).append((_position, _section_index))

    for _norm_title, _occurrences in _title_map.items():
        if len(_occurrences) < 2:
            continue
        _first_pos = _occurrences[0][0]
        _display_title = _markdown_line_to_plain(
            _extract_title(sections[_occurrences[0][1]].get("content", "") or "")
        ).strip()
        # Erstes Vorkommen behalten, alle späteren als warn markieren (reparierbar).
        for _pos, _sec_idx in _occurrences[1:]:
            items.append({
                "_check_key": f"lint:dup_title:{_sec_idx}",
                "section_index": _sec_idx,
                "label": f"Beitrag {_pos}",
                "item_type": "output_lint",
                "level": "warn",
                "summary": f"Der Titel ist identisch mit Beitrag {_first_pos} — beide tragen dieselbe Überschrift.",
                "hard_issues": [
                    f"Titel »{_display_title}« doppelt mit Beitrag {_first_pos}. "
                    f"Titel klar unterscheiden oder — falls gleiches Thema — Beitrag entfernen."
                ],
                "soft_issues": [],
                "issues": [f"Titel doppelt mit Beitrag {_first_pos}"],
                "source_support": [],
                "failed": False,
                "local_lint": True,
                "dup_title_check": True,
            })
            warnings += 1

    return {
        "enabled": True,
        "model": "lokaler Output-Lint",
        "model_display": "lokaler Output-Lint",
        "checked": checked,
        "warnings": warnings,
        "notices": notices,
        "ok": max(0, checked - warnings - notices),
        "failed": 0,
        "items": items,
        "all_items": items,
        "status": "warn" if warnings else ("notice" if notices else "ok"),
        "summary_note": "Technischer Rohtext- und Layout-Check läuft lokal ohne zusätzliche API-Kosten.",
    }


def _auto_repair_lint_issues(client, sections: List[dict], lint_data: dict, model: str,
                              report: Optional[Callable] = None) -> tuple:
    """Behebt Output-Lint-Warnungen automatisch: lokal wenn möglich, sonst per API.

    Returns:
        (repaired_sections, new_lint_data)
    """
    warn_items = [item for item in lint_data.get("items", []) if item.get("level") == "warn"]
    if not warn_items:
        return sections, lint_data

    def _step(label: str):
        if report:
            report(label, 0.79)

    repaired_sections = [copy.deepcopy(s) for s in sections]
    any_changed = False
    api_repair_needed = []

    for item in warn_items:
        section_idx = item.get("section_index")
        if section_idx is None or not (0 <= section_idx < len(repaired_sections)):
            continue

        section = repaired_sections[section_idx]
        if section.get("type") == "transition":
            continue

        content = section.get("content", "")
        issues = item.get("hard_issues", []) + item.get("soft_issues", [])

        # --- Schritt 1: Lokale Boilerplate-Bereinigung ---
        has_boilerplate = any(
            any(marker in issue.lower() for marker in (
                "fremdtext", "boilerplate", "cookie", "consent", "portal",
                "shop", "navigations", "partnerlink", "transparenz",
                "datenschutz", "newsletter", "impressum",
            ))
            for issue in issues
        )
        if has_boilerplate:
            cleaned = _sanitize_briefing_output(_normalize_existing_briefing_markdown(content))
            if cleaned != content:
                section["content"] = tts_safe(cleaned)
                any_changed = True
                continue

        # --- Schritt 2a: Fehlende ### Titel-Überschrift lokal fixen ---
        has_missing_title = any("Titel-Überschrift" in issue for issue in issues)
        if has_missing_title:
            lines = content.split("\n")
            for li, line in enumerate(lines):
                stripped = line.strip()
                if stripped:
                    if not stripped.startswith("### "):
                        # Entferne andere Heading-Marker (##, #, **) und setze ### davor
                        clean_title = re.sub(r"^#{1,6}\s*", "", stripped)
                        clean_title = re.sub(r"^\*\*(.+?)\*\*$", r"\1", clean_title)
                        lines[li] = f"### {clean_title}"
                        content = "\n".join(lines)
                        section["content"] = tts_safe(_sanitize_briefing_output(content))
                        any_changed = True
                    break

        # --- Schritt 2b: Layout-Reparatur per _ensure_briefing_shape ---
        has_layout = any(
            any(marker in issue for marker in (
                "Titel-Überschrift", "Was bleibt", "Abschluss", "Briefing-Format",
            ))
            for issue in issues
        )
        if has_layout:
            reshaped = _ensure_briefing_shape(client, content, model)
            if reshaped and reshaped != content:
                section["content"] = tts_safe(_sanitize_briefing_output(reshaped))
                any_changed = True
                continue

        # --- Schritt 3: API-Repair als Fallback ---
        api_repair_needed.append((section_idx, item))

    if api_repair_needed:
        _step(f"{len(api_repair_needed)} Abschnitte werden per API repariert...")
        for section_idx, item in api_repair_needed:
            repaired = _repair_section_from_check(
                client, repaired_sections[section_idx], item, model,
            )
            if repaired.get("content") != repaired_sections[section_idx].get("content"):
                repaired["content"] = tts_safe(repaired["content"])
                repaired_sections[section_idx] = repaired
                any_changed = True

    if not any_changed:
        return sections, lint_data

    # Re-run lint auf reparierten Sections
    new_lint = _run_output_lint(repaired_sections)
    return repaired_sections, new_lint


def _export_sections(sections: List[dict], generated_at: datetime.datetime, requested_exports: dict,
                     report: Optional[Callable[[str, float], None]] = None,
                     start_progress: float = 0.93) -> tuple:
    """Erzeugt die gewünschten Exportdateien aus fertigen Sections."""
    import tempfile

    export_pdf = bool(requested_exports.get("pdf"))
    export_epub = bool(requested_exports.get("epub"))
    export_txt = bool(requested_exports.get("eleven_txt"))

    exports = {
        "pdf": None,
        "epub": None,
        "eleven_txt": None,
    }
    export_status = {
        "pdf_ok": None,
        "epub_ok": None,
        "txt_ok": None,
    }

    def step(label: str, progress: float):
        if report:
            report(label, progress)

    # TXT immer erzeugen (auch wenn nicht als Export gewünscht) — für Archiv/Meta-Briefing
    try:
        step("Eleven-Reader-Text wird erzeugt...", start_progress)
        exports["eleven_txt"] = create_eleven_reader_text(sections, generated_at).encode("utf-8")
        export_status["txt_ok"] = True
    except Exception as e:
        export_status["txt_ok"] = False
        export_status["txt_error"] = str(e)

    if export_epub:
        try:
            step("EPUB wird erzeugt...", min(start_progress + 0.02, 0.97))
            exports["epub"] = create_epub(sections, generated_at)
            export_status["epub_ok"] = True
        except Exception as e:
            export_status["epub_ok"] = False
            export_status["epub_error"] = str(e)

    if export_pdf:
        step("PDF wird erzeugt...", min(start_progress + 0.04, 0.99))
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name

            create_pdf(sections, tmp_path, generated_at)

            with open(tmp_path, "rb") as f:
                exports["pdf"] = f.read()

            os.unlink(tmp_path)
            export_status["pdf_ok"] = True
        except Exception as e:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            export_status["pdf_ok"] = False
            export_status["pdf_error"] = str(e)

    return exports, export_status


def _merge_cost_dicts(base_cost: Optional[dict], additional_cost: Optional[dict]) -> dict:
    """Addiert zwei Cost-Dicts für die Anzeige des Gesamtbriefings."""
    base_cost = base_cost or {}
    additional_cost = additional_cost or {}
    if not base_cost:
        return copy.deepcopy(additional_cost)
    if not additional_cost:
        return copy.deepcopy(base_cost)

    merged = {
        "estimated": True,
        "currency": base_cost.get("currency") or additional_cost.get("currency") or "USD",
        "total_usd": round(float(base_cost.get("total_usd", 0.0) or 0.0) + float(additional_cost.get("total_usd", 0.0) or 0.0), 6),
        "calls": int(base_cost.get("calls", 0) or 0) + int(additional_cost.get("calls", 0) or 0),
        "billable_input_tokens": int(base_cost.get("billable_input_tokens", 0) or 0) + int(additional_cost.get("billable_input_tokens", 0) or 0),
        "cached_input_tokens": int(base_cost.get("cached_input_tokens", 0) or 0) + int(additional_cost.get("cached_input_tokens", 0) or 0),
        "output_tokens": int(base_cost.get("output_tokens", 0) or 0) + int(additional_cost.get("output_tokens", 0) or 0),
        "models": [],
        "notes": [],
    }

    buckets = {}
    for source in (base_cost, additional_cost):
        for item in source.get("models", []):
            model = item.get("model")
            if not model:
                continue
            bucket = buckets.setdefault(model, {
                "model": model,
                "provider": item.get("provider"),
                "pricing_key": item.get("pricing_key"),
                "calls": 0,
                "usd": 0.0,
                "billable_input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
            })
            bucket["calls"] += int(item.get("calls", 0) or 0)
            bucket["usd"] += float(item.get("usd", 0.0) or 0.0)
            bucket["billable_input_tokens"] += int(item.get("billable_input_tokens", 0) or 0)
            bucket["cached_input_tokens"] += int(item.get("cached_input_tokens", 0) or 0)
            bucket["output_tokens"] += int(item.get("output_tokens", 0) or 0)

        for note in source.get("notes", []):
            if note and note not in merged["notes"]:
                merged["notes"].append(note)

    merged["models"] = [
        {
            **item,
            "usd": round(item["usd"], 6),
        }
        for _, item in sorted(buckets.items())
    ]
    return merged


def rerun_briefing_sorting(
    api_key: str,
    sections: List[dict],
    existing_check: dict,
    model: str,
    requested_exports: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
) -> tuple:
    """Sortiert die Artikel eines vorhandenen Briefings erneut und baut die Exporte neu."""
    if not sections:
        return None, existing_check, sections

    requested_exports = requested_exports or existing_check.get("requested_exports") or {
        "pdf": True,
        "epub": True,
        "eleven_txt": True,
    }
    generated_at = _parse_created_at(existing_check.get("created_at_file"))
    client = _build_client(api_key, model)

    def report(step: str, progress: float):
        if progress_callback:
            progress_callback(step, progress)

    weather_sections = [copy.deepcopy(s) for s in sections if s.get("_weather")]
    article_sections = [
        copy.deepcopy(s) for s in sections
        if s["type"] == "article" and not s.get("_weather") and not s.get("_recap") and not s.get("_essenz") and not s.get("_verabschiedung")
    ]
    podcast_sections = [copy.deepcopy(s) for s in sections if s["type"] == "podcast"]

    sort_expected = len(article_sections) >= 2
    sorted_ok = None
    rebuilt_sections = list(weather_sections)
    if sort_expected:
        report("Artikel werden thematisch neu sortiert...", 0.20)
        sorted_articles, sorted_ok = _sort_articles_by_theme(client, article_sections, model)
        if not sorted_ok:
            print("[sort] Re-Sort erster Versuch fehlgeschlagen, starte automatischen Retry...", file=sys.stderr)
            report("Sortierung wird automatisch erneut versucht...", 0.28)
            time.sleep(2)
            sorted_articles, sorted_ok = _sort_articles_by_theme(client, article_sections, model)
        rebuilt_sections.extend(sorted_articles)
    else:
        rebuilt_sections.extend(article_sections)

    if podcast_sections:
        rebuilt_sections.append({"type": "transition", "content": "Ab jetzt Podcasts."})
        rebuilt_sections.extend(podcast_sections)

    report("Gesamtübersicht wird neu erstellt...", 0.55)
    has_recap = _append_recap_section(client, rebuilt_sections, model)
    _annotate_section_progress_markers(rebuilt_sections)

    for section in rebuilt_sections:
        cleaned = _sanitize_briefing_output(_normalize_existing_briefing_markdown(section["content"]))
        section["content"] = tts_safe(cleaned)

    exports, export_status = _export_sections(
        rebuilt_sections,
        generated_at,
        requested_exports,
        report=report,
        start_progress=0.78,
    )

    new_check = copy.deepcopy(existing_check)
    new_check["requested_exports"] = requested_exports
    new_check["sorting"] = {"expected": sort_expected, "ok": sorted_ok}
    new_check["recap"] = has_recap
    new_check["recap_expected"] = len([s for s in rebuilt_sections if s["type"] != "transition"]) > 2
    new_check["total"] = len([s for s in rebuilt_sections if s["type"] != "transition"])
    new_check["content_check"] = {
        "enabled": False,
        "mode": existing_check.get("content_check", {}).get("mode", "warn"),
        "model": None,
        "checked": 0,
        "warnings": 0,
        "notices": 0,
        "ok": 0,
        "failed": 0,
        "items": [],
        "all_items": [],
        "status": "stale_after_resort",
        "summary_note": "Plausibilitäts-Check nach neuer Sortierung nicht erneut ausgeführt.",
    }
    new_check.update(export_status)

    if new_check.get("complete"):
        new_check["status"] = "VOLLSTÄNDIG" if sorted_ok or not sort_expected else "VOLLSTÄNDIG (Sortierung degradiert)"

    rerun_cost = getattr(client, "_briefing_cost_tracker", _BriefingCostTracker()).as_dict()
    new_check["cost_delta"] = rerun_cost
    new_check["cost"] = _merge_cost_dicts(existing_check.get("cost"), rerun_cost)

    report("Fertig!", 1.0)
    return exports, new_check, rebuilt_sections


def _repair_section_from_check(client, section: dict, check_item: dict, model: str) -> dict:
    all_issues = check_item.get("hard_issues", []) + check_item.get("soft_issues", [])
    all_issues = [issue for issue in all_issues if not _is_non_issue_content_check_text(issue)]
    if not all_issues:
        return section

    content = section.get("content", "")

    # --- Schritt 1: Boilerplate-Issues lokal fixen, ohne API ---
    boilerplate_issues = []
    other_issues = []
    for issue in all_issues:
        if _categorize_repair_issue(issue) == "boilerplate":
            boilerplate_issues.append(issue)
        else:
            other_issues.append(issue)

    locally_fixed = False
    if boilerplate_issues:
        cleaned = _sanitize_briefing_output(_normalize_existing_briefing_markdown(content))
        cleaned = tts_safe(cleaned)
        if cleaned != content:
            content = cleaned
            locally_fixed = True

    # Wenn nur Boilerplate-Issues und lokal gefixt, fertig
    if not other_issues and locally_fixed:
        updated = copy.deepcopy(section)
        updated["content"] = content
        return updated

    # --- Schritt 2: Restliche Issues per API reparieren ---
    issues_formatted = []
    for issue in (other_issues if locally_fixed else all_issues):
        category = _categorize_repair_issue(issue)
        if issue in check_item.get("hard_issues", []):
            issues_formatted.append(f"- Hart: {issue}")
        else:
            issues_formatted.append(f"- Weich: {issue}")

    if not issues_formatted:
        if locally_fixed:
            updated = copy.deepcopy(section)
            updated["content"] = content
            return updated
        return section

    original_content = content
    original_lines = [line for line in original_content.splitlines() if line.strip()]
    original_len = len(original_content)

    prompt_input = (
        f"LABEL: {check_item.get('label', '')}\n\n"
        f"BEANSTANDUNGEN:\n" + "\n".join(issues_formatted) + "\n\n"
        f"QUELLTEXT:\n{section.get('_source_text', '')}\n\n"
        f"AKTUELLER ABSCHNITT:\n{original_content}"
    )
    repaired = summarize(
        client,
        prompt_input,
        SECTION_REPAIR_PROMPT,
        model=model,
        max_tokens=3500,
    )
    repaired = _ensure_briefing_shape(client, repaired, model)
    if not repaired:
        if locally_fixed:
            updated = copy.deepcopy(section)
            updated["content"] = original_content
            return updated
        return section

    repaired = _sanitize_briefing_output(_normalize_existing_briefing_markdown(repaired))

    # --- Schritt 3: Validierung — Repair nicht annehmen wenn zu viel verloren ---
    repaired_len = len(repaired)
    repaired_lines = [line for line in repaired.splitlines() if line.strip()]

    # Laengencheck: >35% kuerzer → ablehnen (LLM hat zu viel geloescht)
    if original_len > 100 and repaired_len < original_len * 0.65:
        print(f"[repair-reject] Zu viel Text verloren: {original_len} → {repaired_len} Zeichen ({repaired_len/original_len:.0%})",
              file=sys.stderr)
        if locally_fixed:
            updated = copy.deepcopy(section)
            updated["content"] = original_content
            return updated
        return section

    # Strukturcheck: Titel, Was-bleibt und Abschluss muessen noch da sein
    repaired_lower = repaired.lower()
    has_title = repaired.lstrip().startswith("### ")
    has_was_bleibt = "#### was bleibt" in repaired_lower or "\nwas bleibt:" in repaired_lower
    has_closing = bool(re.search(
        r"weiter geht[\'\u2019]s\.?|ende der podcastzusammenfassung|ende des briefings",
        repaired_lower,
    ))

    original_lower = original_content.lower()
    orig_had_title = original_content.lstrip().startswith("### ")
    orig_had_was_bleibt = "#### was bleibt" in original_lower or "\nwas bleibt:" in original_lower
    orig_had_closing = bool(re.search(
        r"weiter geht[\'\u2019]s\.?|ende der podcastzusammenfassung|ende des briefings",
        original_lower,
    ))

    structure_lost = (
        (orig_had_title and not has_title)
        or (orig_had_was_bleibt and not has_was_bleibt)
        or (orig_had_closing and not has_closing)
    )

    if structure_lost:
        print(f"[repair-reject] Strukturelemente verloren (title={has_title}, was_bleibt={has_was_bleibt}, closing={has_closing})",
              file=sys.stderr)
        if locally_fixed:
            updated = copy.deepcopy(section)
            updated["content"] = original_content
            return updated
        return section

    updated = copy.deepcopy(section)
    updated["content"] = tts_safe(repaired)
    return updated


def _categorize_repair_issue(issue: str) -> str:
    lowered = normalize_unicode(issue or "").lower()
    if "quellenlabel" in lowered:
        return "quellen"
    if any(marker in lowered for marker in ("partnerlink", "transparenz", "cookie", "impressum", "newsletter", "portal", "shop", "fremdtext", "boilerplate")):
        return "boilerplate"
    if any(marker in lowered for marker in ("briefing-format", "titel-überschrift", "was bleibt", "abschluss", "kursiv", "formatiert", "überschrift", "layout")):
        return "layout"
    return "inhalt"


def repair_briefing_content_issues(
    api_key: str,
    sections: List[dict],
    existing_check: dict,
    model: str,
    requested_exports: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
) -> tuple:
    """Repariert warnende Plausibilitätsfunde minimal und baut Exporte/Check neu."""
    if not sections or not existing_check:
        return None, existing_check, sections

    requested_exports = requested_exports or existing_check.get("requested_exports") or {
        "pdf": True,
        "epub": True,
        "eleven_txt": True,
    }
    generated_at = _parse_created_at(existing_check.get("created_at_file"))
    client = _build_client(api_key, model)

    def report(step: str, progress: float):
        if progress_callback:
            progress_callback(step, progress)

    rebuilt_sections = [copy.deepcopy(section) for section in sections if not section.get("_recap")]
    payloads, section_indices = _build_content_check_payloads(rebuilt_sections)
    warning_groups = {}
    repair_category_counts = defaultdict(int)
    source_item_lists = [
        existing_check.get("content_check", {}).get("items", []),
        existing_check.get("output_lint", {}).get("items", []),
    ]
    for list_idx, item_list in enumerate(source_item_lists):
        for item in item_list:
            is_output_lint = list_idx == 1
            if item.get("level") != "warn" and not (is_output_lint and item.get("level") == "notice"):
                continue
            hard_issues = [issue for issue in item.get("hard_issues", []) if not _is_non_issue_content_check_text(issue)]
            soft_issues = [issue for issue in item.get("soft_issues", []) if not _is_non_issue_content_check_text(issue)]
            if not hard_issues and not soft_issues:
                continue

            section_idx = item.get("section_index")
            if section_idx is None:
                try:
                    payload_idx = int(item.get("_check_key", "-1"))
                except Exception:
                    payload_idx = -1
                if 0 <= payload_idx < len(section_indices):
                    section_idx = section_indices[payload_idx]
            if section_idx is None or not (0 <= int(section_idx) < len(rebuilt_sections)):
                continue

            bucket = warning_groups.setdefault(int(section_idx), {
                "label": item.get("label", ""),
                "hard_issues": [],
                "soft_issues": [],
            })
            for issue in hard_issues:
                if issue not in bucket["hard_issues"]:
                    bucket["hard_issues"].append(issue)
                    repair_category_counts[_categorize_repair_issue(issue)] += 1
            for issue in soft_issues:
                if issue not in bucket["soft_issues"]:
                    bucket["soft_issues"].append(issue)
                    repair_category_counts[_categorize_repair_issue(issue)] += 1

    if not warning_groups:
        return None, existing_check, sections

    warning_items = sorted(warning_groups.items(), key=lambda pair: pair[0])
    total = len(warning_items)
    for pos, (section_idx, item) in enumerate(warning_items, start=1):
        report(f"Mangel {pos}/{total} wird korrigiert...", 0.08 + (0.34 * pos / max(total, 1)))
        rebuilt_sections[section_idx] = _repair_section_from_check(client, rebuilt_sections[section_idx], item, model)

    report("Gesamtübersicht wird aktualisiert...", 0.48)
    _append_recap_section(client, rebuilt_sections, model)
    _annotate_section_progress_markers(rebuilt_sections)

    for section in rebuilt_sections:
        cleaned = _sanitize_briefing_output(_normalize_existing_briefing_markdown(section["content"]))
        section["content"] = tts_safe(cleaned)

    exports, export_status = _export_sections(
        rebuilt_sections,
        generated_at,
        requested_exports,
        report=report,
        start_progress=0.58,
    )

    report("Plausibilitäts-Check läuft erneut...", 0.72)
    content_check_mode = existing_check.get("content_check", {}).get("mode", "warn")
    payloads, _ = _build_content_check_payloads(rebuilt_sections)
    content_check_data = _run_content_check(
        client,
        payloads,
        _content_check_models(model),
        content_check_mode,
        progress_callback=report,
        progress_start=0.72,
        progress_end=0.92,
    )

    new_check = copy.deepcopy(existing_check)
    new_check["repair_iteration"] = int(existing_check.get("repair_iteration", 0) or 0) + 1
    new_check["repair_time_file"] = get_berlin_now().strftime("%H-%M")
    new_check["requested_exports"] = requested_exports
    new_check["recap"] = any(section.get("_recap") for section in rebuilt_sections)
    new_check["recap_expected"] = len([s for s in rebuilt_sections if s["type"] != "transition"]) > 2
    new_check["total"] = len([s for s in rebuilt_sections if s["type"] != "transition"])
    new_check["content_check"] = content_check_data
    report("Format- und Rohtext-Check läuft erneut...", 0.94)
    new_check["output_lint"] = _run_output_lint(rebuilt_sections)
    before_warnings = int(existing_check.get("content_check", {}).get("warnings", 0) or 0) + int(existing_check.get("output_lint", {}).get("warnings", 0) or 0)
    before_notices = int(existing_check.get("content_check", {}).get("notices", 0) or 0) + int(existing_check.get("output_lint", {}).get("notices", 0) or 0)
    after_warnings = int(new_check.get("content_check", {}).get("warnings", 0) or 0) + int(new_check.get("output_lint", {}).get("warnings", 0) or 0)
    after_notices = int(new_check.get("content_check", {}).get("notices", 0) or 0) + int(new_check.get("output_lint", {}).get("notices", 0) or 0)
    if after_warnings > before_warnings:
        report("Korrektur verworfen, weil danach mehr Warnungen offen waren.", 1.0)
        return None, existing_check, sections
    new_check["repair_summary"] = {
        "iteration": new_check["repair_iteration"],
        "time_file": new_check["repair_time_file"],
        "sections_rebuilt": total,
        "labels": [item.get("label", "") for _, item in warning_items][:8],
        "categories": {
            "boilerplate": int(repair_category_counts.get("boilerplate", 0)),
            "layout": int(repair_category_counts.get("layout", 0)),
            "quellen": int(repair_category_counts.get("quellen", 0)),
            "inhalt": int(repair_category_counts.get("inhalt", 0)),
        },
        "before": {"warnings": before_warnings, "notices": before_notices},
        "after": {"warnings": after_warnings, "notices": after_notices},
    }
    new_check.update(export_status)
    rerun_cost = getattr(client, "_briefing_cost_tracker", _BriefingCostTracker()).as_dict()
    new_check["cost_delta"] = rerun_cost
    new_check["cost"] = _merge_cost_dicts(existing_check.get("cost"), rerun_cost)

    report("Fertig!", 1.0)
    return exports, new_check, rebuilt_sections


def merge_cost_dicts(base_cost: Optional[dict], additional_cost: Optional[dict]) -> dict:
    return _merge_cost_dicts(base_cost, additional_cost)


def generate_genius_summary(
    api_key: str,
    sections: List[dict],
    model: str,
    mode: str = "standard",
    progress_callback: Optional[Callable] = None,
    generated_at: Optional[datetime.datetime] = None,
    quality_check: bool = True,
    api_keys: Optional[dict] = None,
) -> tuple[Optional[str], Optional[bytes], dict, dict]:
    """Erzeugt nachträglich eine ausführlichere, aber kompakte Komplett-Zusammenfassung.

    Args:
        quality_check: Wenn True, laufen nach dem Format-Schritt: (1) lokaler Lint mit Auto-Repair,
                       (2) LLM-Qualitäts-Check mit Auto-Repair bei inhaltlichen Problemen.
    """
    mode = _normalize_genius_summary_mode(mode)

    def report(step: str, progress: float):
        if progress_callback:
            progress_callback(step, progress)

    content_sections = [s for s in sections if s["type"] != "transition" and not s.get("_recap") and not s.get("_essenz") and not s.get("_verabschiedung") and not s.get("_preview")]
    if len(content_sections) < 2:
        return None, None, {}, {
            "complete": False,
            "total": len(content_sections),
            "weather": 0,
            "articles": 0,
            "paywall": 0,
            "podcasts": 0,
            "used_fallback": False,
            "ids_ok": False,
            "coverage_ok": False,
            "mode": mode,
            "mode_label": "Kurzversion" if mode == "short" else ("Langversion" if mode == "long" else "Standard"),
        }

    report("Kurzfassung wird vorbereitet...", 0.10)
    client = _build_client(api_key, model)
    report("Kompaktfassung wird vorbereitet...", 0.20)

    # === Pro-Ressort-Modus bei vielen Beiträgen ===
    # Bei > 70 Beiträgen schafft kein LLM (weder GPT noch Claude) alle IDs in EINER
    # Antwort lückenlos. Daher: Pro Ressort einen separaten LLM-Call mit lokalen IDs,
    # die danach auf die globalen IDs zurückgemappt werden. Greift automatisch.
    use_per_cluster = len(content_sections) > 70
    summary = None

    if use_per_cluster:
        report(f"Viele Beiträge ({len(content_sections)}) — pro-Ressort-Modus aktiv", 0.25)
        # Buckets aus globalen IDs bauen
        buckets: dict = {}
        for orig_idx, sec in enumerate(content_sections, 1):
            bkey = _classify_section_topic(sec)
            buckets.setdefault(bkey, []).append((orig_idx, sec))
        bucket_order = ["wetter", "regional", "politik", "wirtschaft", "tech", "gericht", "sonstige", "podcast"]
        active_buckets = [(k, buckets[k]) for k in bucket_order if buckets.get(k)]
        combined_entry_map: dict = {}
        prompt = _genius_summary_prompt_for_mode(mode)
        n = len(active_buckets)
        for i, (bkey, items) in enumerate(active_buckets, 1):
            base_progress = 0.25 + (0.45 * (i - 1) / max(n, 1))
            label = {"wetter":"Wetter","regional":"Regional","politik":"Politik","wirtschaft":"Wirtschaft",
                     "tech":"Tech","gericht":"Gerichte","sonstige":"Weitere","podcast":"Podcasts"}.get(bkey, bkey)
            report(f"{i}/{n} {label}: {len(items)} Beiträge werden verdichtet…", base_progress)
            if bkey == "podcast":
                # Podcasts: direkt aus Voll übernehmen (keine Token-Kosten)
                for orig_idx, sec in items:
                    combined_entry_map[orig_idx] = _build_podcast_full_text(sec)
                continue
            sub_sections = [s for _, s in items]
            sub_input = _build_genius_summary_input(sub_sections)
            try:
                if api_keys:
                    sub_out = summarize_with_fallback(client, sub_input, prompt, model, api_keys=api_keys, max_tokens=8192)
                else:
                    sub_out = summarize(client, sub_input, prompt, model, max_tokens=8192)
            except APIQuotaExhaustedError:
                report(f"⚠️ Guthaben/Quota aufgebraucht — Briefing-Erzeugung abgebrochen", base_progress)
                return None, None, getattr(client, "_briefing_cost_tracker", _BriefingCostTracker()).as_dict(), {
                    "complete": False, "total": len(content_sections), "weather": 0,
                    "articles": 0, "paywall": 0, "podcasts": 0,
                    "used_fallback": False, "ids_ok": False, "coverage_ok": False,
                    "mode": mode, "mode_label": "Lang" if mode == "long" else ("Kurz" if mode == "short" else "Standard"),
                    "error": "API-Guthaben aufgebraucht — bitte aufladen.",
                }
            if not sub_out:
                continue
            sub_entry_map = _extract_recap_entry_map(sub_out)
            # lokale IDs (1..len(items)) auf orig_idx mappen
            for local_idx, (orig_idx, _) in enumerate(items, 1):
                text = sub_entry_map.get(local_idx, "")
                if text:
                    combined_entry_map[orig_idx] = text
        # Pseudo-raw_summary mit allen Einträgen für _format_genius_summary_output
        report("Geniale wird zusammengeführt…", 0.72)
        if combined_entry_map:
            raw_lines = ["### Kompakte Vollzusammenfassung", "", "*Hier ist das ganze Briefing in ausführlicher Form, nahe am Vollbriefing.*", ""]
            for orig_idx in sorted(combined_entry_map.keys()):
                raw_lines.append(f"[{orig_idx}] {combined_entry_map[orig_idx]}")
            summary = "\n".join(raw_lines)

    else:
        # Single-Call wie bisher (für kleine Briefings <= 70 Beiträge)
        prompt_input = _build_genius_summary_input(content_sections)
        report("Kompaktfassung wird geschrieben...", 0.35)
        with ThreadPoolExecutor(max_workers=1) as pool:
            if api_keys:
                summary_future = pool.submit(
                    summarize_with_fallback, client, prompt_input,
                    _genius_summary_prompt_for_mode(mode), model, api_keys,
                )
            else:
                summary_future = pool.submit(
                    summarize, client, prompt_input,
                    _genius_summary_prompt_for_mode(mode), model,
                )
            wait_progress = 0.35
            while not summary_future.done():
                time.sleep(0.6)
                wait_progress = min(0.68, wait_progress + 0.02)
                report("Kompaktfassung wird geschrieben...", wait_progress)
            try:
                summary = summary_future.result()
            except APIQuotaExhaustedError:
                report(f"⚠️ Guthaben/Quota aufgebraucht — Briefing-Erzeugung abgebrochen", 0.70)
                return None, None, getattr(client, "_briefing_cost_tracker", _BriefingCostTracker()).as_dict(), {
                    "complete": False, "total": len(content_sections), "weather": 0,
                    "articles": 0, "paywall": 0, "podcasts": 0,
                    "used_fallback": False, "ids_ok": False, "coverage_ok": False,
                    "mode": mode, "mode_label": "Lang" if mode == "long" else ("Kurz" if mode == "short" else "Standard"),
                    "error": "API-Guthaben aufgebraucht — bitte aufladen.",
                }

    report("Kurzfassung wird geprüft...", 0.72)
    valid_ids = False
    valid_coverage = False
    used_fallback = False
    if summary:
        valid_ids, _ = _validate_recap_ids(summary, len(content_sections))
        if valid_ids:
            valid_coverage, _ = _validate_genius_summary_coverage(summary, content_sections)

    if not summary or not valid_ids or not valid_coverage:
        report("Regelbasiertes Sicherheitsnetz wird angewendet...", 0.82)
        summary = _build_deterministic_genius_summary(sections, mode=mode)
        used_fallback = True

    if not summary:
        return None, None, getattr(client, "_briefing_cost_tracker", _BriefingCostTracker()).as_dict(), {
            "complete": False,
            "total": len(content_sections),
            "weather": 0,
            "articles": 0,
            "paywall": 0,
            "podcasts": 0,
            "used_fallback": used_fallback,
            "ids_ok": valid_ids,
            "coverage_ok": valid_coverage,
            "mode": mode,
            "mode_label": "Kurzversion" if mode == "short" else ("Langversion" if mode == "long" else "Standard"),
        }

    # === Podcasts verdichten: Alle Podcasts > 500 Wörter auf 300-500 kürzen (parallel) ===
    # Wichtig: Original-Sections nicht mutieren — Kopie anfertigen für die Geniale.
    condensed_count = 0
    genius_sections = copy.deepcopy(content_sections)
    try:
        podcast_targets = [s for s in genius_sections
                           if s.get("type") == "podcast"
                           and len(s.get("content", "").split()) > 500]
        if podcast_targets:
            report(f"Lange Podcasts werden verdichtet ({len(podcast_targets)} Stück)...", 0.78)
            with ThreadPoolExecutor(max_workers=min(_max_workers(model), len(podcast_targets))) as pool:
                future_to_sec = {
                    pool.submit(_condense_podcast_for_genius, client, s["content"], model, 500): s
                    for s in podcast_targets
                }
                for future in as_completed(future_to_sec):
                    s = future_to_sec[future]
                    try:
                        condensed = future.result()
                        if condensed and condensed != s["content"]:
                            s["content"] = condensed
                            condensed_count += 1
                    except Exception:
                        pass
    except Exception:
        pass

    summary = _format_genius_summary_output(summary, genius_sections, mode, all_sections=sections)

    # === Qualitätskette: lokaler Lint + optional LLM-Check ===
    lint_report = {"issues": [], "auto_repairs": [], "llm_issues": [], "llm_repair_applied": False}
    if quality_check:
        report("Qualitäts-Check der Kurzfassung läuft...", 0.82)
        lint = _lint_genius_summary(summary, len(content_sections))
        if lint.get("auto_repairs"):
            summary = lint.get("repaired_text") or summary
        lint_report["issues"] = lint.get("issues", [])
        lint_report["auto_repairs"] = lint.get("auto_repairs", [])

        # Phase B: LLM-Qualitäts-Check + Auto-Repair
        try:
            llm_check = _llm_check_genius_summary(client, summary, content_sections, model)
            lint_report["llm_issues"] = llm_check.get("issues", []) or []
            lint_report["coverage_ok"] = llm_check.get("coverage_ok")
            lint_report["cluster_ok"] = llm_check.get("cluster_ok")
            if lint_report["llm_issues"]:
                report("Geniale wird anhand der Prüfergebnisse nachgebessert...", 0.88)
                repaired = _llm_repair_genius_summary(
                    client, summary, lint_report["llm_issues"], content_sections, model
                )
                if repaired and repaired != summary:
                    # Nochmal lokalen Lint drüber laufen lassen (safety net)
                    lint2 = _lint_genius_summary(repaired, len(content_sections))
                    summary = lint2.get("repaired_text") or repaired
                    lint_report["llm_repair_applied"] = True
                    lint_report["auto_repairs"].extend(lint2.get("auto_repairs", []))
        except Exception as exc:
            lint_report["llm_error"] = str(exc)

    summary = tts_safe(summary)
    summary_pdf = None
    report("PDF der Kurzfassung wird erzeugt...", 0.92)
    try:
        summary_pdf = create_single_markdown_pdf(
            summary,
            generated_at or datetime.datetime.now(),
            document_title=(
                "Geniale Zusammenfassung"
                if mode == "standard"
                else ("Geniale Zusammenfassung (Kurz)" if mode == "short" else "Geniale Zusammenfassung (Lang)")
            ),
        )
    except Exception:
        summary_pdf = None
    report("Fertig!", 1.0)
    meta = _build_genius_summary_meta(content_sections, used_fallback, valid_ids, valid_coverage, mode)
    meta["quality_check"] = lint_report
    return summary, summary_pdf, getattr(client, "_briefing_cost_tracker", _BriefingCostTracker()).as_dict(), meta


def generate_briefing(
    api_key: str,
    urls_text: str = "",
    paywall_text: str = "",
    podcast_text: str = "",
    model: str = "claude-sonnet-5",
    include_weather: bool = True,
    requested_exports: Optional[dict] = None,
    content_check_enabled: bool = False,
    content_check_mode: str = "warn",
    selected_article_urls: Optional[List[str]] = None,
    prefetched_article_payloads: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
    compact_mode: bool = False,
    narrative_mode: bool = False,
) -> tuple:
    """
    Erzeugt ein komplettes Briefing.
    
    Returns:
        (exports, check_data, sections) — Export-Dict + Completeness-Dict + interne Sections.
        (None, None, None) wenn nichts zu verarbeiten war.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    started_at = get_berlin_now()
    done_count = 0
    done_lock = threading.Lock()
    requested_exports = requested_exports or {"pdf": True, "epub": True, "eleven_txt": True}
    export_pdf = bool(requested_exports.get("pdf"))
    export_epub = bool(requested_exports.get("epub"))
    export_txt = bool(requested_exports.get("eleven_txt"))
    content_check_mode = "full" if content_check_mode == "full" else "warn"
    item_progress_cap = 0.68
    recap_progress = 0.74
    output_lint_progress = 0.78
    finalize_progress = 0.93
    export_start_progress = 0.94

    def report(step: str, progress: float):
        if progress_callback:
            progress_callback(step, progress)

    def item_phase_progress() -> float:
        return item_progress_cap * (done_count / max(total_items, 1))

    def tick(label: str):
        nonlocal done_count
        with done_lock:
            done_count += 1
            report(label, item_phase_progress())

    client = _build_client(api_key, model)

    # --- Inputs vorbereiten ---
    extracted_urls, url_duplicates, rejected_urls = _extract_article_urls_internal(urls_text)
    if selected_article_urls is None:
        urls = extracted_urls
    else:
        selected_set = set(selected_article_urls)
        urls = [url for url in extracted_urls if url in selected_set]
    raw_paywall_chunks = split_paywall_articles(paywall_text)
    raw_podcast_chunks = split_podcast_summaries(podcast_text)
    paywall_chunks, paywall_duplicates = _deduplicate_items(raw_paywall_chunks, _paywall_chunk_dedup_key)
    podcast_chunks, podcast_duplicates = _deduplicate_items(raw_podcast_chunks, _podcast_chunk_dedup_key)
    paywall_source_hints = _infer_paywall_chunk_source_hints(paywall_chunks)
    duplicate_skips = {
        "urls": url_duplicates,
        "paywall": paywall_duplicates,
        "podcasts": podcast_duplicates,
        "article_content": 0,
        "total": url_duplicates + paywall_duplicates + podcast_duplicates,
    }

    total_items = (1 if include_weather else 0) + len(urls) + len(paywall_chunks) + len(podcast_chunks)
    if total_items == 0:
        return None, None, None

    sections = []

    # --- 1. Wetterbericht parallel zu Artikeln starten ---
    weather_future = None
    if include_weather:
        report("Wetterbericht wird erstellt...", 0)
        _weather_pool = ThreadPoolExecutor(max_workers=1)

        def _fetch_and_summarize_weather():
            weather_text = fetch_weather()
            if not weather_text:
                return None
            weather_summary = summarize(client, weather_text, WETTER_PROMPT, model)
            if not weather_summary:
                print("[retry] Wetterbericht fehlgeschlagen, versuche erneut...", file=sys.stderr)
                time.sleep(3)
                weather_summary = summarize(client, weather_text, WETTER_PROMPT, model)
            if weather_summary:
                return {
                    "type": "article",
                    "content": weather_summary,
                    "_weather": True,
                    "_source_text": weather_text,
                }
            return None

        weather_future = _weather_pool.submit(_fetch_and_summarize_weather)

    # --- 2. Artikel: Streaming-Pipeline (Fetch → sofort Summarize) ---
    article_results = [None] * len(urls)
    if urls:
        article_completed = 0
        articles_fetched = 0

        def article_tick():
            nonlocal article_completed
            article_completed += 1
            tick(f"Artikel {article_completed}/{len(urls)} zusammengefasst")

        def fetch_tick():
            nonlocal articles_fetched
            articles_fetched += 1
            # Fetch-Fortschritt als Bruchteil der ersten Hälfte des Artikel-Budgets anzeigen
            fetch_frac = articles_fetched / max(len(urls), 1)
            report(
                f"Artikel {articles_fetched}/{len(urls)} geladen, {article_completed} zusammengefasst...",
                item_progress_cap * 0.3 * fetch_frac,
            )

        prefetched_payloads = prefetched_article_payloads or {}
        fetched_payloads = [
            copy.deepcopy(prefetched_payloads.get(url)) if prefetched_payloads.get(url) else None
            for url in urls
        ]
        missing_fetch_indices = [i for i, payload in enumerate(fetched_payloads) if payload is None]
        already_fetched_indices = [i for i, payload in enumerate(fetched_payloads) if payload is not None]

        # Bereits vorgeladene zählen direkt als gefetcht
        articles_fetched = len(already_fetched_indices)

        # Streaming: Fetch und Summarize überlappend
        report(f"Artikel werden geladen und zusammengefasst (0/{len(urls)})...", item_phase_progress())
        summary_workers = _max_workers(model)
        fetch_workers = _article_fetch_workers(len(missing_fetch_indices)) if missing_fetch_indices else 1

        with ThreadPoolExecutor(max_workers=fetch_workers + summary_workers) as pool:
            summary_futures = {}

            # Bereits vorgeladene Artikel sofort zur Zusammenfassung einreichen
            for i in already_fetched_indices:
                future = pool.submit(_summarize_article_payload, client, fetched_payloads[i], model, compact=compact_mode)
                summary_futures[future] = i

            # Fetch-Futures starten
            fetch_futures = {}
            for i in missing_fetch_indices:
                future = pool.submit(_fetch_article_payload, urls[i])
                fetch_futures[future] = i

            # Sobald ein Fetch fertig ist, sofort Zusammenfassung starten
            for future in as_completed(fetch_futures):
                idx = fetch_futures[future]
                try:
                    payload = future.result()
                    if payload:
                        fetched_payloads[idx] = payload
                        sum_future = pool.submit(_summarize_article_payload, client, payload, model, compact=compact_mode)
                        summary_futures[sum_future] = idx
                    else:
                        print(f"[artikel-fetch] URL {idx}: kein Payload", file=sys.stderr)
                except Exception as e:
                    print(f"[artikel-fetch] URL {idx} fehlgeschlagen: {type(e).__name__}: {e}",
                          file=sys.stderr)
                fetch_tick()

            # Zusammenfassungs-Ergebnisse sammeln
            for future in as_completed(summary_futures):
                idx = summary_futures[future]
                try:
                    article_results[idx] = future.result()
                except Exception as e:
                    print(f"[artikel-summary] URL {idx} fehlgeschlagen: {type(e).__name__}: {e}",
                          file=sys.stderr)
                article_tick()

        # Retry: fehlgeschlagene Fetches
        failed_fetch = [i for i, payload in enumerate(fetched_payloads) if payload is None and i in set(missing_fetch_indices)]
        if failed_fetch:
            print(f"[retry] {len(failed_fetch)} Artikel-Fetches fehlgeschlagen, starte Retry...",
                  file=sys.stderr)
            report(f"{len(failed_fetch)} Artikeltexte werden nachgeholt...", item_phase_progress())
            for i in failed_fetch:
                payload = _fetch_article_payload(urls[i])
                if payload:
                    fetched_payloads[i] = payload
                    result = _summarize_article_payload(client, payload, model, compact=compact_mode)
                    if result:
                        article_results[i] = result
                    print(f"[retry] Artikel {i}: {'OK' if result else 'Summary fehlgeschlagen'}", file=sys.stderr)
                else:
                    print(f"[retry] Artikel-Fetch {i}: erneut fehlgeschlagen", file=sys.stderr)

        # Retry: fehlgeschlagene Summaries
        failed_summary = [i for i, r in enumerate(article_results) if r is None and fetched_payloads[i] is not None]
        if failed_summary:
            print(f"[retry] {len(failed_summary)} Artikel-Summaries fehlgeschlagen, starte Retry...",
                  file=sys.stderr)
            report(f"{len(failed_summary)} Artikel-Summaries werden nachgeholt...", item_phase_progress())
            for i in failed_summary:
                time.sleep(2)
                result = _summarize_article_payload(client, fetched_payloads[i], model, compact=compact_mode)
                if result:
                    article_results[i] = result
                    print(f"[retry] Artikel-Summary {i}: OK", file=sys.stderr)
                else:
                    print(f"[retry] Artikel-Summary {i}: erneut fehlgeschlagen", file=sys.stderr)

        # Tick für komplett fehlgeschlagene
        for i in range(len(urls)):
            if article_results[i] is None and fetched_payloads[i] is None:
                article_tick()

    # --- Wetter-Ergebnis einsammeln (sollte inzwischen fertig sein) ---
    if weather_future is not None:
        try:
            weather_result = weather_future.result(timeout=45)
            if weather_result:
                sections.append(weather_result)
        except Exception as e:
            print(f"[wetter] Fehlgeschlagen: {type(e).__name__}: {e}", file=sys.stderr)
        done_count += 1
        _weather_pool.shutdown(wait=False)

    # In Reihenfolge einfügen, plus konservative Deduplizierung auf Inhaltsebene.
    # Der Dedupe-Satz bleibt danach aktiv, damit Paywall-Blöcke nicht dasselbe
    # Thema erneut einfügen, wenn URL + kopierter Volltext parallel reinkamen.
    seen_article_content = set()
    for result in article_results:
        if result:
            content_key = _article_source_dedup_key(result)
            if content_key and content_key in seen_article_content:
                duplicate_skips["article_content"] += 1
                duplicate_skips["total"] += 1
                continue
            if content_key:
                seen_article_content.add(content_key)
            sections.append(result)

    # --- 3. Paywall-Artikel parallel verarbeiten ---
    paywall_results = [None] * len(paywall_chunks)
    paywall_failed = []
    if paywall_chunks:
        report(f"{len(paywall_chunks)} Paywall-Artikel werden verarbeitet...", item_phase_progress())
        with ThreadPoolExecutor(max_workers=_max_workers(model)) as pool:
            future_to_idx = {}
            for i, chunk in enumerate(paywall_chunks):
                future = pool.submit(_process_paywall_chunk, client, chunk, model, paywall_source_hints[i], compact=compact_mode)
                future_to_idx[future] = i

            for completed_paywalls, future in enumerate(as_completed(future_to_idx), start=1):
                idx = future_to_idx[future]
                try:
                    result = future.result()
                    if result.get("ok"):
                        paywall_results[idx] = result["section"]
                    else:
                        paywall_results[idx] = None
                        paywall_failed.append(_paywall_failure_label(result.get("failed_input", paywall_chunks[idx]), idx))
                except Exception:
                    paywall_results[idx] = None
                    paywall_failed.append(_paywall_failure_label(paywall_chunks[idx], idx))
                tick(f"Paywall {completed_paywalls}/{len(paywall_chunks)} fertig")

        for result in paywall_results:
            if result:
                content_key = _article_source_dedup_key(result)
                if content_key and content_key in seen_article_content:
                    duplicate_skips["article_content"] += 1
                    duplicate_skips["total"] += 1
                    continue
                if content_key:
                    seen_article_content.add(content_key)
                sections.append(result)

    # --- 4. Thematische Sortierung (Wetter separat, Artikel + Paywall zusammen) ---
    # Wetter-Section abtrennen (bleibt immer ganz oben)
    weather_sections = [s for s in sections if s.get("_weather")]
    article_sections = [s for s in sections if not s.get("_weather")]

    sort_expected = len(article_sections) >= 2
    sorted_ok = None
    if sort_expected:
        report("Artikel werden thematisch sortiert...", item_phase_progress())
        sorted_articles, sorted_ok = _sort_articles_by_theme(client, article_sections, model)
        if not sorted_ok:
            print("[sort] Erster Versuch fehlgeschlagen, starte automatischen Retry...", file=sys.stderr)
            report("Sortierung wird automatisch erneut versucht...", item_phase_progress())
            time.sleep(2)
            sorted_articles, sorted_ok = _sort_articles_by_theme(client, article_sections, model)
        sections = weather_sections + sorted_articles
    else:
        sections = weather_sections + article_sections

    # --- 5. Podcast-Überleitung + parallel formatieren ---
    podcast_results = [None] * len(podcast_chunks)
    if podcast_chunks:
        sections.append({"type": "transition", "content": "Ab jetzt Podcasts."})

        report(f"{len(podcast_chunks)} Podcasts werden formatiert...", item_phase_progress())
        with ThreadPoolExecutor(max_workers=_max_workers(model)) as pool:
            future_to_idx = {}
            for i, chunk in enumerate(podcast_chunks):
                future = pool.submit(_format_chunk, client, chunk, "podcast", model)
                future_to_idx[future] = i

            for completed_podcasts, future in enumerate(as_completed(future_to_idx), start=1):
                idx = future_to_idx[future]
                try:
                    podcast_results[idx] = future.result()
                except Exception:
                    podcast_results[idx] = {"type": "podcast", "content": podcast_chunks[idx]}
                if podcast_results[idx]:
                    podcast_results[idx]["_source_text"] = podcast_chunks[idx]
                tick(f"Podcast {completed_podcasts}/{len(podcast_chunks)} fertig")

        for result in podcast_results:
            if result:
                # Fehlenden Podcast-Endmarker lokal ergänzen (falls im User-Paste nicht drin)
                content = result.get("content", "")
                if content:
                    # Podcast-Content bereinigen: Zwischen-### und **bold** im Hauptteil raus,
                    # damit das PDF-Rendering sauber aussieht (kein grün+fetter Wurm-Absatz).
                    # Der Titel (erste ### Zeile) bleibt als Header — nur Zwischentitel betroffen.
                    content = _cleanup_podcast_content(content)
                    result["content"] = content
                    if not any(_contains_podcast_end_marker(line) for line in result["content"].splitlines()[-5:]):
                        result["content"] = result["content"].rstrip() + "\n\nEnde der Podcastzusammenfassung."
                sections.append(result)

    # --- 6. Recap (sequenziell, braucht alle Ergebnisse) ---
    content_sections = [s for s in sections if s["type"] != "transition"]
    if len(content_sections) > 2:
        report("Gesamtübersicht wird erstellt...", recap_progress)
        _append_recap_section(client, sections, model)

    # --- 6a. Essenz (nach Recap, vor Vorschau) ---
    report("Essenz wird destilliert...", recap_progress + 0.005)
    _append_essenz_section(client, sections, model)

    # --- 6a2. Verabschiedung (allerletztes Element) ---
    report("Verabschiedung wird formuliert...", recap_progress + 0.008)
    _append_verabschiedung(client, sections, model)

    # Hinweis: Der Erzähl-Modus läuft NICHT hier, sondern erst NACH Plaus-Check + Auto-Repair
    # in Schritt 9. So bleiben die Plaus-Checks und Auto-Repairs über die echten Originalbeiträge
    # gültig — und das Verweben passiert auf den bereits verbesserten Inhalten.

    # --- 6b. Vorschau (nach Recap, alle Infos verfügbar → an den Anfang setzen) ---
    report("Vorschau wird erstellt...", recap_progress + 0.01)
    preview = _build_briefing_preview(client, sections, model)
    if preview:
        insert_pos = 0
        for i, s in enumerate(sections):
            if s.get("_weather"):
                insert_pos = i + 1
                break
        sections.insert(insert_pos, preview)

    # Beitragszähler nur im klassischen Modus
    if not narrative_mode:
        _annotate_section_progress_markers(sections)

    # Bereinigung: "Ende des Briefings." nur am allerletzten Platz lassen
    _strip_premature_briefing_end_markers(sections)

    # --- 7. TTS-Nachbearbeitung aller Sections ---
    for s in sections:
        cleaned = _sanitize_briefing_output(_normalize_existing_briefing_markdown(s["content"]))
        s["content"] = tts_safe(cleaned)

    # --- 8. Optionaler inhaltlicher Plausibilitäts-Check ---
    content_check_data = {
        "enabled": False,
        "mode": content_check_mode,
        "model": None,
        "checked": 0,
        "warnings": 0,
        "notices": 0,
        "ok": 0,
        "failed": 0,
        "items": [],
        "all_items": [],
        "status": "off",
    }
    report("Format- und Rohtext-Check läuft...", output_lint_progress)
    output_lint_data = _run_output_lint(sections, expected_article_count=total_items)

    # --- Auto-Repair: Lint-Warnungen direkt beheben ---
    if output_lint_data.get("warnings", 0) > 0:
        report("Lint-Warnungen werden automatisch behoben...", output_lint_progress + 0.01)
        sections, output_lint_data = _auto_repair_lint_issues(
            client, sections, output_lint_data, model, report=report,
        )

    if content_check_enabled:
        check_models = _content_check_models(model)
        payloads, _ = _build_content_check_payloads(sections)

        report("Inhaltlicher Plausibilitäts-Check läuft...", CONTENT_CHECK_PROGRESS_START)
        content_check_data = _run_content_check(
            client,
            payloads,
            check_models,
            content_check_mode,
            progress_callback=report,
            progress_start=CONTENT_CHECK_PROGRESS_START,
            progress_end=CONTENT_CHECK_PROGRESS_END,
        )

        # --- Auto-Repair: Warnungen aus Content-Check direkt beheben ---
        warn_items = [item for item in content_check_data.get("items", []) if item.get("level") == "warn"]
        if warn_items:
            report(f"{len(warn_items)} Warnung(en) werden automatisch behoben...", CONTENT_CHECK_PROGRESS_END + 0.01)
            check_payloads, section_indices = _build_content_check_payloads(sections)
            for item in warn_items:
                hard_issues = [i for i in item.get("hard_issues", []) if not _is_non_issue_content_check_text(i)]
                soft_issues = [i for i in item.get("soft_issues", []) if not _is_non_issue_content_check_text(i)]
                if not hard_issues and not soft_issues:
                    continue
                section_idx = item.get("section_index")
                if section_idx is None:
                    try:
                        payload_idx = int(item.get("_check_key", "-1"))
                    except Exception:
                        payload_idx = -1
                    if 0 <= payload_idx < len(section_indices):
                        section_idx = section_indices[payload_idx]
                if section_idx is not None and 0 <= int(section_idx) < len(sections):
                    sections[int(section_idx)] = _repair_section_from_check(
                        client, sections[int(section_idx)],
                        {"hard_issues": hard_issues, "soft_issues": soft_issues, "label": item.get("label", "")},
                        model,
                    )

            # TTS-Nachbearbeitung für reparierte Sections wiederholen
            for s in sections:
                cleaned = _sanitize_briefing_output(_normalize_existing_briefing_markdown(s["content"]))
                s["content"] = tts_safe(cleaned)

            report("Reparaturen werden erneut geprüft...", CONTENT_CHECK_PROGRESS_END + 0.015)
            output_lint_data = _run_output_lint(sections)
            payloads, _ = _build_content_check_payloads(sections)
            content_check_data = _run_content_check(
                client,
                payloads,
                check_models,
                content_check_mode,
                progress_callback=report,
                progress_start=CONTENT_CHECK_PROGRESS_END + 0.02,
                progress_end=max(CONTENT_CHECK_PROGRESS_END + 0.06, 0.98),
            )
            content_check_data["auto_repaired"] = len(warn_items)

        report("Briefing wird finalisiert...", finalize_progress)

    # --- 8a. Erzähl-Modus (NACH Plaus-Check + Auto-Repair) ---
    # Reihenfolge ist wichtig: Erst die Originalbeiträge plausibel und sauber machen,
    # dann zu thematischen Erzähl-Blöcken verweben. So profitiert das Verweben von den
    # Auto-Repairs, und das Verweben kann selbst keine Plaus-Probleme einschleusen
    # (es ordnet nur um und schreibt Übergänge).
    if narrative_mode:
        report("Erzähl-Modus: thematisch verweben...", finalize_progress + 0.005)
        sections = convert_to_narrative_briefing(client, sections, model, progress_callback=progress_callback)
        # TTS-Nachbearbeitung der neuen Erzähl-Sections
        for s in sections:
            cleaned = _sanitize_briefing_output(_normalize_existing_briefing_markdown(s["content"]))
            s["content"] = tts_safe(cleaned)

    # --- 9. Completeness-Check (Terminal + UI) ---

    n_weather = 1 if any(s.get("_weather") for s in sections) else 0
    n_articles = len([s for s in article_results if s]) if urls else 0
    n_paywall = len([s for s in paywall_results if s]) if paywall_chunks else 0
    n_podcasts = len([s for s in podcast_results if s]) if podcast_chunks else 0
    n_content = len([s for s in sections if s["type"] not in ("transition",)])
    has_recap = any(s.get("_recap") for s in sections)

    check_lines = []
    missing = []

    if include_weather:
        if n_weather:
            check_lines.append("  Wetter: OK")
        else:
            check_lines.append("  Wetter: FEHLT")
            missing.append("Wetterbericht")

    if urls:
        failed_urls = [urls[i] for i, r in enumerate(article_results) if r is None]
        if failed_urls:
            check_lines.append(f"  Artikel: {n_articles}/{len(urls)} UNVOLLSTÄNDIG")
            for u in failed_urls:
                check_lines.append(f"    FEHLT: {u[:100]}")
                missing.append(f"Artikel: {u[:60]}")
        else:
            check_lines.append(f"  Artikel: {n_articles}/{len(urls)} OK")

    if paywall_chunks:
        if paywall_failed:
            check_lines.append(f"  Paywall: {n_paywall}/{len(paywall_chunks)} UNVOLLSTÄNDIG")
            for item in paywall_failed:
                check_lines.append(f"    FEHLT: {item}")
                missing.append(item)
        else:
            check_lines.append(f"  Paywall: {n_paywall}/{len(paywall_chunks)} OK")

    if podcast_chunks:
        check_lines.append(f"  Podcasts: {n_podcasts}/{len(podcast_chunks)} OK")

    if duplicate_skips["total"]:
        details = []
        if duplicate_skips["urls"]:
            details.append(f"{duplicate_skips['urls']} URL")
        if duplicate_skips["article_content"]:
            details.append(f"{duplicate_skips['article_content']} Artikelinhalt")
        if duplicate_skips["paywall"]:
            details.append(f"{duplicate_skips['paywall']} Paywall")
        if duplicate_skips["podcasts"]:
            details.append(f"{duplicate_skips['podcasts']} Podcast")
        joined = ", ".join(details)
        check_lines.append(f"  Duplikate übersprungen: {duplicate_skips['total']} ({joined})")
    if rejected_urls:
        check_lines.append(f"  Nicht-Artikel-Links übersprungen: {len(rejected_urls)}")

    recap_expected = len(content_sections) > 2
    if recap_expected:
        if has_recap:
            check_lines.append("  Recap: OK")
        else:
            check_lines.append("  Recap: FEHLT")
            missing.append("Gesamtübersicht")
    else:
        check_lines.append(f"  Recap: übersprungen (nur {len(content_sections)} Beiträge)")

    check_lines.append(f"  Gesamt: {n_content} Beiträge")

    if sort_expected:
        check_lines.append(f"  Sortierung: {'OK' if sorted_ok else 'degradiert (unsortiert)'}")

    check_text = "\n".join(check_lines)
    qa_warnings = (
        int((content_check_data or {}).get("warnings", 0) or 0)
        + int((content_check_data or {}).get("failed", 0) or 0)
        + int((output_lint_data or {}).get("warnings", 0) or 0)
    )
    qa_notices = (
        int((content_check_data or {}).get("notices", 0) or 0)
        + int((output_lint_data or {}).get("notices", 0) or 0)
    )
    all_ok = len(missing) == 0
    publishable = all_ok and qa_warnings == 0
    status = "VOLLSTÄNDIG" if all_ok else f"UNVOLLSTÄNDIG ({len(missing)} fehlen)"
    if all_ok and sort_expected and not sorted_ok:
        status = "VOLLSTÄNDIG (Sortierung degradiert)"
    if all_ok and qa_warnings:
        status = f"VOLLSTÄNDIG MIT QUALITÄTSWARNUNGEN ({qa_warnings})"
    elif all_ok and qa_notices:
        status = f"VOLLSTÄNDIG MIT HINWEISEN ({qa_notices})"
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"[briefing] COMPLETENESS CHECK: {status}", file=sys.stderr)
    print(f"{check_text}", file=sys.stderr)
    print(f"{'=' * 50}\n", file=sys.stderr)

    # Check-Daten für Web-UI
    generated_at = get_berlin_now()

    check_data = {
        "complete": all_ok,
        "publishable": publishable,
        "quality_status": "warn" if qa_warnings else ("notice" if qa_notices else "ok"),
        "quality_warnings": qa_warnings,
        "quality_notices": qa_notices,
        "status": status,
        "main_model": model,
        "created_at_display": generated_at.strftime("%d.%m.%Y %H:%M:%S"),
        "created_at_file": generated_at.strftime("%Y-%m-%d_%H-%M-%S"),
        "started_at_display": started_at.strftime("%d.%m.%Y %H:%M:%S"),
        "started_at_file": started_at.strftime("%Y-%m-%d_%H-%M-%S"),
        "requested_exports": {
            "pdf": export_pdf,
            "epub": export_epub,
            "eleven_txt": export_txt,
        },
        "weather": {"expected": 1 if include_weather else 0, "got": n_weather},
        "articles": {"expected": len(urls), "got": n_articles,
                     "failed": [urls[i] for i, r in enumerate(article_results) if r is None] if urls else [],
                     "rejected": rejected_urls},
        "paywall": {"expected": len(paywall_chunks), "got": n_paywall, "failed": paywall_failed},
        "podcasts": {"expected": len(podcast_chunks), "got": n_podcasts},
        "duplicates": duplicate_skips,
        "recap": has_recap,
        "recap_expected": recap_expected,
        "sorting": {"expected": sort_expected, "ok": sorted_ok},
        "total": n_content,
        "missing": missing,
        "content_check": content_check_data,
        "output_lint": output_lint_data,
        "cost": getattr(client, "_briefing_cost_tracker", _BriefingCostTracker()).as_dict(),
    }

    if not sections:
        return None, check_data, sections

    if all_ok:
        report(f"Fertig! Alle {n_content} Beiträge verarbeitet.", 0.95)
    else:
        report(f"Fertig mit Lücken: {', '.join(missing[:3])}{'...' if len(missing) > 3 else ''}", 0.95)

    exports, export_status = _export_sections(
        sections,
        generated_at,
        requested_exports,
        report=report,
        start_progress=export_start_progress,
    )
    check_data.update(export_status)

    report("Fertig!", 1.0)
    return exports, check_data, sections

# ============================================================
# CLAUDE-HANDOFF: Brücke zwischen App und Claude-Chat
# ============================================================


CLAUDE_HANDOFF_PROMPT = """Du erstellst ein komplettes Audio-Briefing aus den Rohdaten unten.

DEINE AUFGABE
Verwandle alle Artikel-Rohtexte in saubere Briefing-Zusammenfassungen, baue Recap, Essenz und Verabschiedung — und gib am Ende ALLES als ein einziges JSON-Dokument zurück, das die App direkt in eine PDF baut.

FORMAT JEDES ARTIKELS (in Markdown)
### Titel des Beitrags
*Ein einleitender, kursiver Einordnungssatz, der das Thema in einem Satz fasst.*

Fließtext-Zusammenfassung in 150-400 Wörtern (kompakt-Modus: 150-250). Aktive Sprache, konkrete Fakten, gesprochener Stil. Nichts erfinden — nur was im Quelltext steht. Quellen werden NICHT im Text genannt (kommen separat).

#### Was bleibt:

Der Merksatz, der hängenbleibt — 1, höchstens 2 Sätze. Fasse die Essenz so auf den Punkt, dass auch jemand, der den Beitrag verpasst hat, ihn in diesem einen Satz versteht, UND bring die Konsequenz mit: was sich dadurch ändert oder warum es zählt. Konkret, kein abstrakter Floskel-Schlusssatz.

Weiter geht's.

REGELN
- KEINE Aufzählungszeichen, KEINE Listen, KEINE horizontalen Trennlinien.
- Schlüsselbegriffe in **fett**.
- Umlaute korrekt (ä ö ü ß).
- Duze den Hörer wenn passend.
- Bei Podcasts: Zusammenfassungen wie geliefert übernehmen, ggf. nur formal säubern. Endmarker ist `Ende der Podcastzusammenfassung.` statt `Weiter geht's.`
- Wetter wird als ERSTE Section übernommen wie geliefert.
- Reihenfolge: Wetter → Artikel (nach Quellen-Region geordnet) → Podcasts → Recap → Essenz → Verabschiedung

RECAP (am Ende, ein Eintrag pro Beitrag)
### Rückblick
*Kurzer Überblick über alles was heute drin war.*

#### Lokales:
- **Titel 1** — ein Satz Kernaussage
- **Titel 2** — ein Satz Kernaussage
...
(thematisch gruppiert: Lokales, Politik, Wirtschaft, International, Tech, Kultur, Podcasts)

Weiter geht's.

ESSENZ
### Was wirklich bleibt
*Die Essenz aus dem heutigen Briefing — was über den Tag hinaus zählt.*

3-7 Absätze Fließtext, jeder beginnt mit dem Kerngedanken in **fett**, dann 2-3 Sätze Erklärung. Strukturelle Verschiebungen, Muster, Entscheidungen mit Langzeitwirkung. Keine Tageskleinigkeiten aufblasen.

VERABSCHIEDUNG
### Bis zum nächsten Mal

Klugen, weniger bekannten Zitat (echte Quelle, kein Kalenderspruch) + 1-2 Sätze Einordnung was du heute damit anfangen kannst + persönlicher Gruß passend zur Tageszeit (siehe TAGESZEIT unten). Max 90 Wörter.

Ende des Briefings.

═══════════════════════════════════════════════════════════
ENDFORMAT
═══════════════════════════════════════════════════════════

Gib am ENDE deiner Antwort EIN einziges JSON-Codeblock zurück mit dieser Struktur:

```json
{
  "compact_mode": true,
  "sections": [
    {
      "type": "article",
      "_weather": true,
      "source_label": "DWD",
      "content": "### Wetter Tübingen-Hirschau\\n\\n*Einordnung...*\\n\\nFließtext...\\n\\n#### Was bleibt:\\n\\nKern.\\n\\nWeiter geht's."
    },
    {
      "type": "article",
      "source_label": "Schwäbisches Tagblatt",
      "content": "### Titel\\n\\n*Einordnung...*\\n\\nText...\\n\\n#### Was bleibt:\\n\\nKern.\\n\\nWeiter geht's."
    },
    {
      "type": "podcast",
      "source_label": "Podcast 1",
      "content": "### Podcast-Titel\\n\\n*Einordnung...*\\n\\nText...\\n\\nEnde der Podcastzusammenfassung."
    },
    {
      "type": "article",
      "_recap": true,
      "source_label": "Rückblick",
      "content": "### Rückblick\\n\\n*Überblick...*\\n\\n#### Lokales:\\n- ...\\n\\nWeiter geht's."
    },
    {
      "type": "article",
      "_essenz": true,
      "source_label": "Essenz",
      "content": "### Was wirklich bleibt\\n\\n*Die Essenz...*\\n\\n**Punkt 1.** Erklärung..."
    },
    {
      "type": "article",
      "_verabschiedung": true,
      "source_label": "Abschluss",
      "content": "### Bis zum nächsten Mal\\n\\nZitat... Gruß.\\n\\nEnde des Briefings."
    }
  ]
}
```

WICHTIG
- JSON muss valides JSON sein (Escapes für Newlines mit \\\\n).
- Kein Markdown außerhalb des JSON-Codeblocks am Ende.
- Sprich vorher gerne kurz erläuternd, aber das JSON ist das Endergebnis.
- Reihenfolge der Sections = Reihenfolge im finalen Briefing.

═══════════════════════════════════════════════════════════
ROHDATEN
═══════════════════════════════════════════════════════════
"""


CLAUDE_HANDOFF_PROMPT_NARRATIVE = """Du erstellst ein komplettes Audio-Briefing im ERZÄHL-STIL (Podcast-Format) aus den Rohdaten unten.

DEINE AUFGABE
Verwandle alle Artikel-Rohtexte, Podcasts und das Wetter in EINEN einzigen, durchgehenden Erzähltext, der sich beim Vorlesen anfühlt wie ein guter NDR Info-Podcast — homogen, mit thematischen Übergängen, ohne Listenstruktur oder Beitragszähler. Am Ende: alles als JSON, das die App in eine PDF baut.

GRUNDPRINZIP
- KEINE harten Trennzeichen wie „Weiter geht's" oder „Beitrag X von Y" als Block-Header
- ABER: Beim VORLESEN soll der Hörer jederzeit wissen wo er gerade ist. Deshalb baust du am Anfang JEDES Beitrags einen natürlichen, hörbaren Marker ein, der die laufende Nummer und Gesamtzahl in der Section nennt — variiert formuliert, nicht starr.
- ALLES verwoben in EINEN großen Erzählfluss mit thematischen Übergängen
- Quellen elegant in den Fließtext einweben: „wie das Tagblatt schreibt", „laut BBC", „die ZEIT berichtet"

HÖRBARE BEITRAGS-MARKER + WAS-BLEIBT-MINI-MARKER (KRITISCH!)

Jeder einzelne Beitrag im Erzählfluss folgt diesem Muster:

1. Beitrags-Marker am Anfang (Position + Gesamtzahl, variiert formuliert):
   - „Beginnen wir mit dem ersten von acht Themen aus der Region..."
   - „Zweitens, ebenfalls aus dem Tagblatt: ..."
   - „Beim vierten Beitrag wird es dunkler: ..."
   - „Wir sind beim fünften von acht — diesmal geht es um..."
   - „Damit zum letzten Thema dieser Section: ..."

2. Erzählender Hauptteil (Quellen eingewoben, Namen/Zahlen/Zitate erhalten)

3. Was-bleibt-Mini-Marker am ENDE des Beitrags — EIN Satz im Fließtext, kein eigener Absatz, variiert formuliert:
   - „Was hängenbleibt: ..."
   - „Der Punkt ist: ..."
   - „Wenn du dir nur eine Sache merkst: ..."
   - „Im Kern geht es darum, dass ..."
   - „Festhalten lässt sich: ..."

DIDAKTIK & TON
- Sprich wie ein kluger, gut gelaunter Freund — nicht Professor, nicht Nachrichtensprecher.
- Trockener Humor erlaubt, aber nie auf Kosten der Substanz oder bei tragischen Themen.
- Konkrete Bilder statt abstrakte Begriffe.
- Kein Pathos, keine Phrasen wie „in Zeiten wie diesen"

VOLLSTÄNDIGKEIT (KRITISCH!)
- NICHTS WEGLASSEN. JEDER der nummerierten Artikel und JEDER Podcast muss eindeutig wiederzufinden sein. Kürze nur Wiederholungen.
- BEVOR du das JSON schreibst: Gehe die Artikel-Nummern (Artikel 1, Artikel 2, ...) und Podcast-Nummern (Podcast 1, ...) durch und prüfe für JEDEN, dass mindestens 2-3 Sätze davon im finalen Text auftauchen — auch bei kurzen oder vermeintlich isolierten Themen.
- Spezialthemen, lokale Veranstaltungen, kuriose Meldungen, Einzelschicksale: ALLE behalten, auch wenn sie nicht „wichtig" wirken. Die App soll keine Filterung übernehmen — das hast du schon vor der Übergabe gemacht.
- Bei thematisch isolierten Beiträgen: lieber einen kurzen Übergangssatz hinzufügen („Ein anderes Thema, das heute auftaucht: ...") als sie wegzulassen.
- Auch Podcasts werden integriert — als eigene, eindeutig erkennbare Absätze im Fluss
- Wetter als sanfter Auftakt
- Recap und Essenz als Schluss-Klammer

LÄNGE — INHALTSGETRIEBEN
- Pro Artikel: 280-500 Wörter im Erzähl-Fluss, je nach inhaltlicher Tiefe.
  - Ein kurzer Verbraucher-Tipp oder eine Promi-Meldung: 200-300 Wörter
  - Ein normaler Nachrichten-Artikel: 280-380 Wörter
  - Eine investigative Recherche / Hintergrundbericht / wichtige internationale Geschichte: 400-500 Wörter
- Pro Podcast: 600-1000 Wörter je nach Komplexität — Sprechernamen, Argumente, Zitate, konkrete Zahlen.
- Wetter: 250-350 Wörter mit Tagesverlauf und 2-3-Tages-Ausblick.
- Recap: 600-900 Wörter — alle Themen kurz erwähnt, thematisch gruppiert.
- Essenz: 600-900 Wörter — 4-7 substanzielle Punkte mit Erklärung.
- Verabschiedung: 80-120 Wörter — echtes Zitat + Tageszeit-Gruß.

GESAMTLÄNGE
- Die Gesamtlänge ergibt sich aus der Anzahl und Tiefe der Beiträge — KEINE feste Vorgabe.
- Bei 5 Artikeln passt ein 8-Seiten-Briefing, bei 90 Artikeln können es 80-100 Seiten werden.
- Was ZÄHLT: Jeder einzelne Artikel/Podcast hat genug Tiefe (siehe oben), und nichts wird weggelassen oder im Cluster verschmolzen.

VOLLSTÄNDIGKEIT IST PFLICHT
- Jeder nummerierte Artikel und jeder nummerierte Podcast muss als EIGENER Absatz im finalen Text erkennbar sein.
- BEVOR du das JSON schreibst: Gehe alle Artikel-Nummern und Podcast-Nummern durch und prüfe für JEDEN, dass mindestens 2-3 Sätze davon mit konkretem Inhalt im Text auftauchen.
- KEIN Cluster-Verschmelzen: Lieber 2-3 Sätze Übergang zwischen zwei Beiträgen, statt sie in einem Sammelabsatz zusammenzuzwingen.

STRUKTUR DES ERZÄHLTEXTS
1. **Einstiegs-Section**: Kurzer Auftakt (1-2 Sätze, was heute auf dem Programm steht)
2. **Wetter-Section**: Wetter als sanfter Tagesauftakt
3. **Eine bis drei große Haupt-Sections** (z.B. "Aus Tübingen und der Region", "Politik und Wirtschaft", "International, Tech und mehr"): Alle Beiträge thematisch geclustert in flüssiger Erzählung
4. **Podcast-Section**: Die Podcasts als verwobene Absätze (kein Trenner, aber mit Markern wie „Eine besonders spannende Folge des XY-Podcasts...")
5. **Recap-Section**: Kurzer Rückblick als eigener Absatz mit Einleitung wie „Bevor wir schließen, nochmal die wichtigsten Punkte des Tages..."
6. **Essenz-Section**: „Was bleibt" als reflektierender Absatz
7. **Verabschiedungs-Section**: Zitat + Gruß zur Tageszeit

SPRACHE
- Aktiv, konkret, gesprochen — nicht informativ-distanziert
- Duze den Hörer wenn passend
- Schlüsselbegriffe in **fett**
- Umlaute korrekt (ä ö ü ß)
- Keine Bullets, keine Listen, keine ###-Unterüberschriften innerhalb der Sections (nur die Hauptüberschrift pro Section)
- Länge darf ruhig groß sein — Vollständigkeit vor Kürze

ZITATE IN DER VERABSCHIEDUNG
- Echte, weniger bekannte Zitate von klugen Köpfen
- KEINE Glückskekse, KEIN Gandhi, KEIN „Carpe diem"
- Niemals erfinden
- Plus 1-2 Sätze was du heute damit anfangen kannst

═══════════════════════════════════════════════════════════
ENDFORMAT
═══════════════════════════════════════════════════════════

Gib am ENDE deiner Antwort EIN einziges JSON-Codeblock zurück:

```json
{
  "compact_mode": true,
  "narrative_mode": true,
  "sections": [
    {
      "type": "article",
      "_weather": true,
      "source_label": "Wetter",
      "content": "### Wetter zum Auftakt\\n\\n*Wie der Tag beginnt und was uns erwartet.*\\n\\nDer Tag startet mit ... [Fließtext, kein „Was bleibt", kein „Weiter geht's"]"
    },
    {
      "type": "article",
      "source_label": "Lokales und Region",
      "content": "### Aus Tübingen und der Region\\n\\n*Was lokal passiert und warum es wichtig ist.*\\n\\nIn Tübingen baut die Stadt neue E-Bus-Ladeplätze, wie das Schwäbische Tagblatt berichtet... [langer Fließtext mit allen lokalen Beiträgen verwoben]"
    },
    {
      "type": "article",
      "source_label": "Politik und Wirtschaft",
      "content": "### Politik, Wirtschaft und Macht\\n\\n*Ein Blick auf die großen Linien des Tages.*\\n\\n[Fließtext mit allen passenden Beiträgen]"
    },
    {
      "type": "article",
      "source_label": "International und Technologie",
      "content": "### Die Welt und die Technik\\n\\n*Was sich international und in der Tech-Branche tut.*\\n\\n[Fließtext]"
    },
    {
      "type": "article",
      "source_label": "Podcasts",
      "content": "### Aus den Podcasts\\n\\n*Die Episoden, die heute im Briefing stecken.*\\n\\nBesonders spannend war eine Folge von... [verwobene Podcast-Inhalte]"
    },
    {
      "type": "article",
      "_recap": true,
      "source_label": "Rückblick",
      "content": "### Rückblick\\n\\n*Bevor wir schließen, die wichtigsten Stränge des Tages noch einmal.*\\n\\nFließtext mit allen Beiträgen ganz kurz erwähnt..."
    },
    {
      "type": "article",
      "_essenz": true,
      "source_label": "Essenz",
      "content": "### Was wirklich bleibt\\n\\n*Die Essenz aus dem heutigen Tag — was über den Moment hinaus zählt.*\\n\\nFließtext-Reflexion in 3-7 Absätzen, jeder mit Kerngedanken in **fett** und Erklärung."
    },
    {
      "type": "article",
      "_verabschiedung": true,
      "source_label": "Abschluss",
      "content": "### Bis zum nächsten Mal\\n\\nEcht zitiert: \\"...\\" — Person. Ein Satz Einordnung, was du heute damit anfangen kannst. Persönlicher Gruß zur Tageszeit.\\n\\nEnde des Briefings."
    }
  ]
}
```

WICHTIG
- JSON muss valides JSON sein (Escapes für Newlines mit \\\\n).
- Kein Markdown außerhalb des JSON-Codeblocks am Ende.
- Sprich vorher gerne kurz erläuternd, aber das JSON ist das Endergebnis.
- Anzahl und Benennung der Haupt-Sections darfst du dem Tag anpassen — mindestens aber: Wetter, eine Inhalts-Section, Recap, Essenz, Verabschiedung.

═══════════════════════════════════════════════════════════
ROHDATEN
═══════════════════════════════════════════════════════════
"""


def build_claude_handoff_package(
    urls_text: str,
    paywall_text: str,
    podcast_text: str,
    include_weather: bool,
    compact_mode: bool,
    narrative_mode: bool = False,
    merge_duplicates: bool = True,
    include_prompt: bool = True,
) -> str:
    """Sammelt alle Rohdaten für die Übergabe an Claude im Chat.

    Macht KEINE LLM-Aufrufe — nur Fetching und Strukturierung. Output ist ein
    großer Markdown/Text-Block, den der User in einen Claude-Chat pasten kann.
    Claude macht dann die Zusammenfassungen und gibt JSON zurück, das die App
    in build_pdf_from_claude_json() in eine PDF verwandelt.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    base_prompt = CLAUDE_HANDOFF_PROMPT_NARRATIVE if narrative_mode else CLAUDE_HANDOFF_PROMPT
    now = get_berlin_now()
    if include_prompt:
        parts = [base_prompt]
        parts.append(f"\nERSTELLT AM: {now.strftime('%A, %d. %B %Y, %H:%M Uhr')}\n")
        parts.append(f"TAGESZEIT: {_tageszeit_label(now.hour)}\n")
        parts.append(f"STIL: {'ERZÄHL-MODUS (Podcast-Format, fließend)' if narrative_mode else 'KLASSISCH (strukturiert, Beitragszähler)'}\n")
        parts.append(f"KOMPAKT-MODUS: {'ja (kürzere Zusammenfassungen, 150-250 Wörter)' if compact_mode else 'nein (ausführlich, 200-400 Wörter)'}\n")
        if merge_duplicates:
            parts.append(
                "DOPPELTE THEMEN: Behandeln zwei gelieferte Artikel SICHER dasselbe konkrete "
                "Ereignis (gleiche Story aus verschiedenen Quellen), fasse sie zu EINEM Beitrag "
                "zusammen und integriere die einzigartigen Fakten beider Quellen — nichts darf "
                "verloren gehen. Füge in so einem Beitrag direkt nach dem kursiven Einordnungssatz "
                "eine zweite kursive Zeile ein: *Dieser Beitrag bündelt mehrere Quellen zum selben "
                "Ereignis.* Nur bei eindeutig identischem Ereignis zusammenführen; "
                "verschiedene Blickwinkel, Aspekte oder Folgeberichte bleiben getrennte Beiträge.\n"
            )
        _recent_q = _load_recent_quotes()
        if _recent_q:
            parts.append(
                "SCHLUSSZITAT: Verwende KEINES dieser zuletzt genutzten Zitate erneut — "
                "wähle ein frisches Zitat einer anderen Person: "
                + " | ".join(_recent_q) + "\n"
            )
        parts.append("\n")
    else:
        # Direkt-Kompakt (Genius): KEIN Briefing-/JSON-Prompt voranstellen. Die Anweisungen
        # kommen aus dem Genius-System-Prompt (--system-prompt). Sonst bekommt Opus zwei
        # widersprüchliche Aufträge — und folgt bei sehr vielen Beiträgen der hier
        # eingebetteten JSON-Briefing-Anweisung statt dem [N]-Kompakt-Format (Bug 13.06.).
        parts = [
            f"ROHDATEN ({now.strftime('%A, %d. %B %Y, %H:%M Uhr')}) — "
            "Wetter, Artikel-Quelltexte, Paywall-Texte und Podcast-Zusammenfassungen für die Kompaktfassung:\n\n"
        ]

    # 1. Wetter
    if include_weather:
        parts.append("─" * 60 + "\n")
        parts.append("WETTER (als erste Section in der finalen Briefing übernehmen)\n")
        parts.append("─" * 60 + "\n")
        try:
            weather_raw = fetch_weather()
            if weather_raw:
                parts.append(weather_raw)
                parts.append("\n")
            else:
                parts.append("(Wetter konnte nicht geholt werden — bitte Wetter-Section weglassen)\n")
        except Exception as exc:
            parts.append(f"(Wetter-Fehler: {exc} — bitte weglassen)\n")
        parts.append("\n")

    # 2. Artikel
    valid_urls, _, _ = _extract_article_urls_internal(urls_text or "")
    if valid_urls:
        parts.append("─" * 60 + "\n")
        parts.append(f"ARTIKEL ({len(valid_urls)} URLs — jeder bekommt eine eigene Section)\n")
        parts.append("─" * 60 + "\n\n")

        # Parallel fetchen
        payload_map = {}
        with ThreadPoolExecutor(max_workers=_article_fetch_workers(len(valid_urls))) as pool:
            future_to_url = {pool.submit(_fetch_article_payload, url): url for url in valid_urls}
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    payload = future.result()
                    if payload:
                        payload_map[url] = payload
                except Exception:
                    pass

        for i, url in enumerate(valid_urls, start=1):
            payload = payload_map.get(url)
            parts.append(f"### Artikel {i}\n")
            if payload:
                parts.append(f"QUELLE: {payload.get('source_label', 'unbekannt')}\n")
                parts.append(f"URL: {url}\n")
                text = payload.get("source_text", "")
                # Auf 6000 Zeichen kürzen damit Context nicht explodiert
                if len(text) > 6000:
                    text = text[:6000] + "\n\n[…Text gekürzt zur Übersicht…]"
                parts.append(f"\n{text}\n\n")
            else:
                parts.append(f"URL: {url}\n")
                parts.append("(Konnte nicht geladen werden — bitte überspringen)\n\n")

    # 3. Paywall-Texte
    paywall_chunks = split_paywall_articles(paywall_text or "") if paywall_text and paywall_text.strip() else []
    if paywall_chunks:
        parts.append("─" * 60 + "\n")
        parts.append(f"PAYWALL-TEXTE ({len(paywall_chunks)} Blöcke — jeder eine Section, Quelle aus Text ableiten)\n")
        parts.append("─" * 60 + "\n\n")
        for i, chunk in enumerate(paywall_chunks, start=1):
            chunk = _strip_paywall_navigation(chunk)
            parts.append(f"### Paywall {i}\n")
            inferred = source_label_from_text(chunk[:1500])
            parts.append(f"QUELLE (vermutet): {inferred}\n\n")
            text = chunk if len(chunk) <= 6000 else chunk[:6000] + "\n\n[…gekürzt…]"
            parts.append(f"{text}\n\n")

    # 4. Podcasts
    podcast_chunks = split_podcast_summaries(podcast_text or "") if podcast_text and podcast_text.strip() else []
    if podcast_chunks:
        parts.append("─" * 60 + "\n")
        parts.append(f"PODCASTS ({len(podcast_chunks)} fertige Zusammenfassungen — als 'podcast'-Section übernehmen)\n")
        parts.append("─" * 60 + "\n\n")
        for i, chunk in enumerate(podcast_chunks, start=1):
            parts.append(f"### Podcast {i}\n\n")
            parts.append(chunk.strip())
            parts.append("\n\n")

    parts.append("─" * 60 + "\n")
    parts.append("ENDE DER ROHDATEN\n")
    parts.append("─" * 60 + "\n\n")
    parts.append("Bitte erstelle nun das komplette Briefing nach den Regeln oben und gib am Ende den JSON-Codeblock zurück.\n")

    return "".join(parts)


def _tageszeit_label(hour: int) -> str:
    if hour < 6:
        return "Nacht (nach Mitternacht)"
    if hour < 10:
        return "Früher Morgen"
    if hour < 12:
        return "Vormittag"
    if hour < 14:
        return "Mittag"
    if hour < 18:
        return "Nachmittag"
    if hour < 21:
        return "Abend"
    return "Später Abend"


def _parse_briefing_text_to_sections(text: str) -> List[dict]:
    """Zerlegt einen fertigen Briefing-Text (aus PDF oder TXT) zurück in Sections.

    Erkennt:
    - Quellen-Header (Zeile mit Quellenname allein, z.B. „Schwäbisches Tagblatt")
    - Beitragszähler („Beitrag X von Y.")
    - ### Titel
    - „Weiter geht's." als Trenner
    - „Ende der Podcastzusammenfassung." für Podcasts
    - „Was bleibt:" wird mitgenommen
    - Wetter, Recap, Essenz, Verabschiedung über Heuristiken erkannt
    """
    if not text:
        return []

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    sections = []
    current_lines: List[str] = []
    current_source = None
    current_meta = {}

    def _flush_current():
        nonlocal current_lines, current_source, current_meta
        body = "\n".join(current_lines).strip()
        if not body or len(body) < 30:
            current_lines = []
            current_meta = {}
            return
        sec = {
            "type": "podcast" if current_meta.get("is_podcast") else "article",
            "content": body,
            "source_label": current_source or "",
        }
        for flag in ("_weather", "_recap", "_essenz", "_verabschiedung", "_preview", "_ressort_header"):
            if current_meta.get(flag):
                sec[flag] = True
        # Heuristik: Erster Beitrag (vor Preview) mit Temperatur-Indikatoren → Wetter-Flag setzen
        if not sec.get("_weather") and not sec.get("_preview") and not sec.get("_recap"):
            content_articles = [s for s in sections if not s.get("_preview") and s.get("type") != "transition"]
            if len(content_articles) == 0:
                # Erster echter Beitrag — Wetter-Heuristik
                body_lower = body.lower()
                has_temp = bool(re.search(r"\d+[.,]?\d*\s*(?:°|grad\b)", body_lower))
                has_weather_words = any(w in body_lower for w in (
                    "bewölkt", "bewoelkt", "sonne", "wind", "regen", "wetter", "niederschlag"
                ))
                has_location = any(w in body_lower for w in ("hirschau", "tübingen", "tuebingen"))
                if has_temp and (has_weather_words or has_location):
                    sec["_weather"] = True
                    if not sec["source_label"]:
                        sec["source_label"] = "DWD"
        # Heuristik: Source-Text setzen für Plaus-Check
        sec["_source_text"] = body
        sections.append(sec)
        current_lines = []
        current_meta = {}

    # Bekannte Source-Label-Kandidaten (erste Zeile eines neuen Beitrags)
    known_sources = {
        "Schwäbisches Tagblatt", "Tagblatt", "GEA", "tagesschau", "Tagesschau",
        "ZEIT", "SPIEGEL", "BBC", "Handelsblatt", "WELT", "FAZ", "SWR", "WDR", "NDR",
        "ZDF", "n-tv", "stadt bremerhaven", "arstechnica", "heise", "Golem", "t3n",
        "DWD", "Wetter", "Bright Sky", "MET Norway",
    }

    title_re = re.compile(r"^###\s+(.+)$")
    counter_re = re.compile(r"^(?:Letzter\s+)?Beitrag\s+\d+\s+von\s+\d+\.\s*$", re.IGNORECASE)
    weiter_re = re.compile(r"^\s*Weiter geht['']s\.?\s*$", re.IGNORECASE)
    podcast_end_re = re.compile(r"Ende der Podcastzusammenfassung\.?", re.IGNORECASE)
    briefing_end_re = re.compile(r"Ende des Briefings\.?", re.IGNORECASE)
    preview_re = re.compile(r"^Willkommen zum Briefing\s*$", re.IGNORECASE)
    # Seiten-Footer wie „Dienstag, 14. April 2026 - Audio-Briefing Seite 1"
    page_footer_re = re.compile(
        r"^(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),?\s+\d{1,2}\.\s*\w+\s+\d{4}\s*[-–—]\s*.+\s+Seite\s+\d+\s*$",
        re.IGNORECASE,
    )
    # Cover-Header "DIGITALES AUDIO-BRIEFING" und verwandte
    cover_header_re = re.compile(
        r"^(DIGITALES\s+AUDIO-BRIEFING|Audio[\s-]*Briefing|Erstellt\s+um\s+\d+)",
        re.IGNORECASE,
    )

    in_section = False
    seen_first_counter = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Alles vor dem ersten „Beitrag 1 von N" Counter ist Cover/Titel und wird ignoriert
        if not seen_first_counter and not counter_re.match(stripped):
            i += 1
            continue

        # Seiten-Footer ignorieren (stehen zwischen Beiträgen und würden sonst zu Pseudo-Sections)
        if page_footer_re.match(stripped):
            i += 1
            continue

        # Cover-Header-Reste die versehentlich innerhalb der Sections landen ignorieren
        if cover_header_re.match(stripped) and not in_section:
            i += 1
            continue

        # Quellen-Header (allein stehende Zeile, kurze Wörter, bekannter Name)
        if stripped and len(stripped) < 50 and stripped in known_sources:
            # Vor dem nächsten Beitrag: aktuellen flushen
            if in_section:
                _flush_current()
                in_section = False
            current_source = stripped
            i += 1
            continue

        # Beitragszähler — Marker dass jetzt ein neuer Beitrag startet
        if counter_re.match(stripped):
            if in_section:
                _flush_current()
            in_section = True
            seen_first_counter = True
            i += 1
            continue

        # Wetter erkennen (Heuristik: Titel mit "Wetter")
        if title_re.match(stripped) and "wetter" in stripped.lower():
            if in_section:
                _flush_current()
            current_meta["_weather"] = True
            current_source = current_source or "DWD"
            in_section = True
            current_lines.append(line)
            i += 1
            continue

        # Recap / Essenz / Verabschiedung anhand Titel erkennen
        if title_re.match(stripped):
            t_lower = stripped.lower()
            if "rückblick" in t_lower or "recap" in t_lower:
                if in_section:
                    _flush_current()
                current_meta["_recap"] = True
                in_section = True
                current_lines.append(line)
                i += 1
                continue
            if "was wirklich bleibt" in t_lower or "essenz" in t_lower:
                if in_section:
                    _flush_current()
                current_meta["_essenz"] = True
                in_section = True
                current_lines.append(line)
                i += 1
                continue
            if "bis zum nächsten" in t_lower or "verabschiedung" in t_lower:
                if in_section:
                    _flush_current()
                current_meta["_verabschiedung"] = True
                in_section = True
                current_lines.append(line)
                i += 1
                continue

        # Plain-Text-Meta-Titel (wenn ### beim PDF-Export verloren ging)
        # Zeile muss kurz, alleinstehend sein und genau ein bekanntes Meta-Muster treffen.
        if stripped and len(stripped) < 60 and not counter_re.match(stripped):
            pt_lower = stripped.lower().strip(' .:')
            if pt_lower in ("rückblick", "rueckblick", "recap", "zusammenfassung"):
                if in_section:
                    _flush_current()
                current_meta["_recap"] = True
                in_section = True
                current_lines.append(line)
                i += 1
                continue
            # "was bleibt" alleinstehend NICHT matchen — das ist Sub-Header jedes Beitrags.
            # Nur die spezifischeren Varianten als Essenz markieren.
            if pt_lower in ("was wirklich bleibt", "essenz"):
                if in_section:
                    _flush_current()
                current_meta["_essenz"] = True
                in_section = True
                current_lines.append(line)
                i += 1
                continue
            if pt_lower.startswith("bis zum nächsten") or pt_lower == "verabschiedung":
                if in_section:
                    _flush_current()
                current_meta["_verabschiedung"] = True
                in_section = True
                current_lines.append(line)
                i += 1
                continue

        # Vorschau erkennen
        if preview_re.match(stripped):
            if in_section:
                _flush_current()
            current_meta["_preview"] = True
            in_section = True
            current_lines.append(line)
            i += 1
            continue

        # "Weiter geht's" als Section-Ende
        if weiter_re.match(stripped):
            current_lines.append(line)
            _flush_current()
            in_section = False
            i += 1
            continue

        # Podcast-Endmarker
        if podcast_end_re.search(stripped):
            current_meta["is_podcast"] = True
            current_lines.append(line)
            _flush_current()
            in_section = False
            i += 1
            continue

        # Ende-des-Briefings-Marker
        if briefing_end_re.search(stripped):
            current_lines.append(line)
            _flush_current()
            in_section = False
            i += 1
            continue

        # Normale Inhaltszeile
        if in_section or stripped:
            if stripped or current_lines:  # Leerzeilen nur wenn Section bereits angefangen
                current_lines.append(line)
                in_section = True
        i += 1

    # Letzten Block flushen
    if in_section:
        _flush_current()

    return sections


def _verify_narrative_completeness(original_sections: List[dict], narrative_sections: List[dict]) -> dict:
    """Prüft ob alle Original-Beiträge im Erzähl-Output erkennbar sind.

    Strategie: Aus jeder Original-Section Schlüsselwörter extrahieren (Eigennamen, Zahlen,
    längere Substantive aus dem Titel und ersten Absatz). Prüfen ob mindestens eins davon
    im finalen Erzähl-Text auftaucht.
    """
    if not original_sections or not narrative_sections:
        return {"checked": 0, "found": 0, "missing": []}

    full_text = "\n\n".join(s.get("content", "") for s in narrative_sections)
    full_lower = full_text.lower()

    items = []
    for orig in original_sections:
        if orig.get("_weather") or orig.get("_recap") or orig.get("_essenz") or orig.get("_verabschiedung") or orig.get("_preview"):
            continue
        content = (orig.get("content") or "").strip()
        if not content or len(content) < 50:
            continue

        # Titel = erste ###-Zeile oder erste Nicht-Leer-Zeile
        title = ""
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("###"):
                title = re.sub(r"^#+\s*", "", line)
                break
            if line and not line.startswith(("*", "**", "[")):
                title = line
                break
        if not title:
            continue

        # Schlüsselwörter: Eigennamen, lange Substantive, Zahlen
        keywords = re.findall(r'\b[A-ZÄÖÜ][a-zäöüß-]{4,}\b', title)
        keywords += re.findall(r'\b\d{2,}\b', content[:300])
        keywords = list(dict.fromkeys(keywords))[:5]  # dedup, max 5

        if not keywords:
            keywords = [w for w in re.findall(r'\b\w{6,}\b', title)][:3]

        if not keywords:
            continue

        hit = any(kw.lower() in full_lower for kw in keywords)
        items.append({"title": title[:100], "keywords": keywords, "found": hit})

    found = sum(1 for it in items if it["found"])
    missing = [it for it in items if not it["found"]]
    return {
        "checked": len(items),
        "found": found,
        "missing": missing[:30],
    }


def convert_briefing_to_narrative_from_text(
    briefing_text: str,
    api_key: str,
    model: str,
    output_pdf_path: str,
    generated_at: Optional[datetime.datetime] = None,
    progress_callback: Optional[Callable] = None,
) -> dict:
    """Nimmt einen fertigen Briefing-Text (aus PDF/TXT extrahiert), zerlegt ihn in Sections
    und generiert daraus einen Erzähl-Modus-PDF.

    Returns: {ok, sections_count, error, output_path, completeness}
    """
    def report(step, frac):
        if progress_callback:
            progress_callback(step, frac)

    if generated_at is None:
        generated_at = get_berlin_now()

    report("Bestehendes Briefing wird zerlegt...", 0.05)
    sections = _parse_briefing_text_to_sections(briefing_text)
    if not sections:
        return {"ok": False, "sections_count": 0, "error": "Keine Sections im Text erkannt.", "output_path": None, "completeness": None}

    report(f"{len(sections)} Sections gefunden, werden verwoben...", 0.15)
    client = _build_client(api_key, model)

    new_sections = convert_to_narrative_briefing(client, sections, model, progress_callback=lambda s, f: report(s, 0.15 + f * 0.65))

    # TTS-Nachbearbeitung
    for s in new_sections:
        cleaned = _sanitize_briefing_output(_normalize_existing_briefing_markdown(s["content"]))
        s["content"] = tts_safe(cleaned)

    # Vollständigkeits-Check
    report("Vollständigkeit wird geprüft...", 0.85)
    completeness = _verify_narrative_completeness(sections, new_sections)
    print(f"[narrative-convert] Vollständigkeit: {completeness['found']}/{completeness['checked']}", file=sys.stderr)
    if completeness["missing"]:
        print(f"[narrative-convert] Fehlende Beiträge:", file=sys.stderr)
        for m in completeness["missing"][:10]:
            print(f"  - {m['title']}", file=sys.stderr)

    report("PDF wird gebaut...", 0.92)
    try:
        create_pdf(new_sections, output_pdf_path, generated_at, document_title="Audio-Briefing (Erzählmodus)")
        report("Fertig.", 1.0)
        return {
            "ok": True,
            "sections_count": len(new_sections),
            "original_sections_count": len(sections),
            "error": None,
            "output_path": output_pdf_path,
            "completeness": completeness,
        }
    except Exception as exc:
        return {"ok": False, "sections_count": len(new_sections), "error": f"PDF-Erstellung fehlgeschlagen: {exc}", "output_path": None, "completeness": completeness}


def convert_briefing_pdf_to_narrative(
    pdf_path: str,
    api_key: str,
    model: str,
    output_pdf_path: str,
    progress_callback: Optional[Callable] = None,
) -> dict:
    """Liest eine fertige Briefing-PDF, extrahiert den Text und konvertiert ihn in den Erzähl-Modus."""
    try:
        import pdfplumber
    except ImportError:
        return {"ok": False, "sections_count": 0, "error": "pdfplumber ist nicht installiert.", "output_path": None}

    try:
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                text_parts.append(t)
        full_text = "\n".join(text_parts)
    except Exception as exc:
        return {"ok": False, "sections_count": 0, "error": f"PDF konnte nicht gelesen werden: {exc}", "output_path": None}

    return convert_briefing_to_narrative_from_text(
        full_text,
        api_key=api_key,
        model=model,
        output_pdf_path=output_pdf_path,
        progress_callback=progress_callback,
    )


def _verify_briefing_completeness(handoff_text: str, briefing_text: str) -> dict:
    """Prüft, ob alle nummerierten Beiträge der Handoff-Datei im finalen Briefing-Text auftauchen.

    Strategie:
    - Aus jedem Artikel/Podcast/Paywall einen Titel extrahieren (erste sinnvolle Nicht-Meta-Zeile).
    - 2-3 Schlüsselbegriffe aus dem Titel extrahieren (Substantive, Eigennamen).
    - Im finalen Text suchen ob mindestens einer davon vorkommt.
    """
    if not handoff_text or not briefing_text:
        return {"checked": 0, "found": 0, "missing": []}

    briefing_lower = briefing_text.lower()
    items = []
    pattern = re.compile(r'^### (Artikel|Podcast|Paywall) (\d+)\s*$', re.MULTILINE)
    for m in pattern.finditer(handoff_text):
        kind = m.group(1)
        num = m.group(2)
        start = m.end()
        # Bis zur nächsten ### Marker oder bis zu 2000 Zeichen
        next_m = pattern.search(handoff_text, start)
        end = next_m.start() if next_m else min(len(handoff_text), start + 2000)
        body = handoff_text[start:end]
        # Erste sinnvolle Zeile als "Titel" nehmen
        title = ""
        for line in body.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(("QUELLE:", "URL:", "[", "**Leitfrage", "Leitfrage")):
                continue
            if "archive.today" in line or "Bildschirm" in line or "Versionsgeschichte" in line:
                continue
            if 15 < len(line) < 200:
                title = line
                break
        # Schlüsselwörter: längere Wörter mit Großbuchstaben oder Zahlen
        keywords = re.findall(r'\b[A-ZÄÖÜ][a-zäöüß-]{3,}\b|\b\d{2,}\b', title)
        if not keywords:
            # Fallback: alle Wörter > 5 Zeichen
            keywords = [w for w in re.findall(r'\b\w{6,}\b', title)]
        # Mindestens 1 Keyword muss im Briefing-Text vorkommen
        items.append({"kind": kind, "num": num, "title": title[:100], "keywords": keywords[:5]})

    found = []
    missing = []
    for item in items:
        if not item["keywords"]:
            continue
        hit = any(kw.lower() in briefing_lower for kw in item["keywords"])
        if hit:
            found.append(item)
        else:
            missing.append(item)

    return {
        "checked": len(items),
        "found": len(found),
        "missing": missing[:20],  # höchstens 20 zurückmelden
    }


def _repair_llm_json_quotes(raw: str) -> str:
    """Escaped nicht-escapte ASCII-Anführungszeichen innerhalb von JSON-String-Werten.

    Claude/LLM-Output (v.a. via CLI/Web-Chat) enthält manchmal Zitate oder deutsche
    Anführungszeichen, bei denen das schließende Zeichen ein nacktes " ist — das
    beendet den JSON-String vorzeitig und macht das JSON ungültig. Heuristik für
    pretty-printed JSON: Ein " im String ist nur dann ein echtes String-Ende, wenn
    danach (nach Whitespace) die JSON-Struktur plausibel weitergeht:
      - `}` / `]` / Ende
      - `,` gefolgt von neuem Key/Objekt (" oder {)
      - `:` gefolgt von einem Wert (" { [ Ziffer t f n -)
    Andernfalls ist es ein Inhalts-Anführungszeichen und wird zu \\" escaped.
    Bereits escapte \\" bleiben unberührt. Reiner Fallback — wird nur aufgerufen,
    wenn das normale json.loads bereits gescheitert ist.
    """
    out = []
    in_string = False
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if not in_string:
            out.append(c)
            if c == '"':
                in_string = True
            i += 1
            continue
        if c == '\\':
            # Escape-Sequenz unverändert übernehmen (z.B. \" \n \\)
            out.append(c)
            if i + 1 < n:
                out.append(raw[i + 1]); i += 2
            else:
                i += 1
            continue
        if c == '"':
            j = i + 1
            while j < n and raw[j] in ' \t\r\n':
                j += 1
            nxt = raw[j] if j < n else ''
            is_end = False
            if nxt in ('}', ']', ''):
                is_end = True
            elif nxt == ',':
                k = j + 1
                while k < n and raw[k] in ' \t\r\n':
                    k += 1
                after = raw[k] if k < n else ''
                if after in ('"', '{', ''):
                    is_end = True
            elif nxt == ':':
                k = j + 1
                while k < n and raw[k] in ' \t\r\n':
                    k += 1
                after = raw[k] if k < n else ''
                if after in ('"', '{', '[', 't', 'f', 'n', '-') or after.isdigit():
                    is_end = True
            if is_end:
                out.append(c); in_string = False
            else:
                out.append('\\"')
            i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def build_pdf_from_claude_json(
    claude_json_text: str,
    output_path: str,
    generated_at: Optional[datetime.datetime] = None,
    handoff_text: Optional[str] = None,
) -> dict:
    """Nimmt JSON-Output von Claude und baut daraus eine PDF.

    Wenn handoff_text mitgegeben wird, läuft eine Vollständigkeitsprüfung.
    Returns dict mit {ok, sections_count, error, completeness}.
    """
    if generated_at is None:
        generated_at = get_berlin_now()

    raw = (claude_json_text or "").strip()
    # JSON-Codeblock extrahieren falls in ```json ... ``` verpackt
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if fence_match:
        raw = fence_match.group(1)
    else:
        # Falls kein Codeblock: erstes { bis letztes }
        first = raw.find("{")
        last = raw.rfind("}")
        if first >= 0 and last > first:
            raw = raw[first:last + 1]

    try:
        data = json.loads(raw)
    except Exception as exc:
        # Fallback: häufigster LLM-Fehler ist ein nicht-escaptes " im Text (z.B.
        # schließendes „Zitat"). Einmal reparieren und erneut versuchen.
        try:
            data = json.loads(_repair_llm_json_quotes(raw))
            print("[claude-json] JSON nach Quote-Reparatur erfolgreich geparst.", file=sys.stderr)
        except Exception:
            return {"ok": False, "sections_count": 0, "error": f"JSON-Parsing fehlgeschlagen: {exc}"}

    sections = data.get("sections", [])
    if not sections or not isinstance(sections, list):
        return {"ok": False, "sections_count": 0, "error": "Keine 'sections'-Liste im JSON gefunden."}

    # Sections säubern und für create_pdf vorbereiten — gleiche Sanitize-Pipeline
    # wie der API-Pfad: Boilerplate weg, Markdown normalisieren, doppelte Header
    # entfernen. Das ist wichtig damit Claude-CLI-PDFs identisch aussehen wie
    # API-PDFs (TTS-Marker, Was-bleibt-Format, Beitrags-Endmarker).
    clean_sections = []
    for s in sections:
        if not isinstance(s, dict):
            continue
        content = (s.get("content") or "").strip()
        if not content:
            continue
        try:
            content = _sanitize_briefing_output(_normalize_existing_briefing_markdown(content))
        except Exception:
            # Wenn Sanitize fehlschlägt, lieber Roh-Content nehmen als Section verlieren
            pass
        if not content.strip():
            continue
        clean = {
            "type": s.get("type", "article"),
            "content": content,
            "source_label": s.get("source_label", ""),
        }
        for flag in ("_weather", "_recap", "_essenz", "_verabschiedung", "_preview", "_ressort_header"):
            if s.get(flag):
                clean[flag] = True
        clean_sections.append(clean)

    if not clean_sections:
        return {"ok": False, "sections_count": 0, "error": "Sections-Liste leer nach Bereinigung."}

    # Beitragszähler nur im klassischen Modus hinzufügen
    narrative_mode = bool(data.get("narrative_mode"))
    if not narrative_mode:
        try:
            _annotate_section_progress_markers(clean_sections)
        except Exception:
            pass

    try:
        create_pdf(clean_sections, output_path, generated_at, document_title="Audio-Briefing")
        # Output-Lint: lokale Plausibilitätsprüfung (Markdown-Struktur, Endmarker,
        # „Was bleibt:"-Block, Kontamination). Kein LLM-Call, schnell und kostenlos.
        output_lint = None
        try:
            output_lint = _run_output_lint(clean_sections)
        except Exception:
            output_lint = None
        # Sortier-Diagnose: lokal, prüft thematische Reihenfolge der Beiträge.
        sorting_diag = None
        try:
            sorting_diag = _diagnose_section_sorting(clean_sections)
        except Exception:
            sorting_diag = None
        # Eleven-Reader-TXT + Texte/-Backup direkt mitschreiben — wichtig für
        # Wochen-Meta-Briefing (das die Texte/-Dateien einliest).
        artifacts: dict = {"pdf": output_path}
        try:
            output_dir = os.path.dirname(output_path)
            base_name = os.path.splitext(os.path.basename(output_path))[0]
            texte_dir = os.path.join(output_dir, "Texte")
            os.makedirs(texte_dir, exist_ok=True)
            eleven_text = create_eleven_reader_text(clean_sections, generated_at)
            _txt_filename = f"{base_name}_eleven-reader.txt"
            txt_path = os.path.join(texte_dir, _txt_filename)
            with open(txt_path, "w", encoding="utf-8") as fp:
                fp.write(eleven_text)
            artifacts["eleven_txt"] = txt_path
            _record_archived_file(txt_path)  # für iCloud-Cleanup
            _write_current_eleven_copy(texte_dir, eleven_text)
            # Zusätzlich in den lokalen Meta-Spiegel (für Wochen-Meta, da iCloud
            # vom Hintergrunddienst nicht auflistbar ist).
            _mirror_txt_to_local(_txt_filename, eleven_text)
        except Exception:
            # TXT-Backup ist nice-to-have, kein Blocker
            pass
        # Schlusszitat der Verabschiedung für die Rotation merken (gegen Wiederholung)
        _remember_quote_from_text("\n".join((s.get("content", "") or "") for s in clean_sections[-3:]))
        # Vollständigkeitsprüfung wenn Handoff-Text mitgegeben
        completeness = None
        if handoff_text:
            briefing_text = "\n\n".join(s.get("content", "") for s in clean_sections)
            completeness = _verify_briefing_completeness(handoff_text, briefing_text)
        return {
            "ok": True,
            "sections_count": len(clean_sections),
            "error": None,
            "completeness": completeness,
            "artifacts": artifacts,
            "clean_sections": clean_sections,
            "output_lint": output_lint,
            "sorting_diag": sorting_diag,
        }
    except Exception as exc:
        return {"ok": False, "sections_count": len(clean_sections), "error": f"PDF-Erstellung fehlgeschlagen: {exc}", "completeness": None}


# ============================================================================
# Claude CLI One-Click-Pfad (kostenlos via Max-Abo)
# ============================================================================

def _locate_claude_cli() -> Optional[str]:
    """Findet das `claude` CLI-Binary auf dem System.

    Versucht in dieser Reihenfolge:
    1. `claude` im PATH (via shutil.which)
    2. Bekannte Installations-Pfade aus Claude Desktop App
    3. Versionierte Pfade unter Claude Application Support
    """
    import shutil
    import glob
    found = shutil.which("claude")
    if found:
        return found

    candidates = [
        "/Users/{user}/.claude/local/claude",
        "/Users/{user}/.local/bin/claude",
        "/opt/homebrew/bin/claude",
        "/usr/local/bin/claude",
    ]
    user = os.environ.get("USER", "")
    for tpl in candidates:
        path = tpl.format(user=user)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    # Versionierte Claude.app-Pfade durchsuchen
    pattern = f"/Users/{user}/Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude"
    matches = sorted(glob.glob(pattern), reverse=True)  # Neueste Version zuerst
    for path in matches:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return None


def _run_claude_cli_subprocess_streaming(
    cmd: List[str],
    stdin_text: str,
    *,
    cwd: Optional[str] = None,
    timeout_seconds: int = 900,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    base_progress: float = 0.10,
    max_progress: float = 0.78,
    expected_duration_s: float = 240.0,
    label: str = "Claude denkt",
    poll_interval_s: float = 2.0,
    parse_stream_json: bool = True,
    idle_timeout_s: float = 300.0,
    idle_warmup_s: float = 60.0,
    use_caffeinate: bool = True,
    prefer_full_text: bool = False,
) -> dict:
    """Führt einen `claude -p`-Subprozess aus und gibt Live-Progress-Updates.

    Wenn ``parse_stream_json`` aktiv ist (Default), wird `--output-format
    stream-json --include-partial-messages` automatisch zum cmd hinzugefügt
    und der stdout zeilenweise als JSON-Events geparst. Damit bekommen wir
    Live-Token-Updates während Claude tippt — der `text`-Mode würde alles
    erst am Ende auf einmal liefern und uns blind machen.

    Während Claude denkt/tippt, wird alle ``poll_interval_s`` Sekunden der
    `progress_callback` aufgerufen mit:
    - verstrichene Zeit
    - bisher empfangene Zeichen
    - asymptotische Progress-Schätzung zwischen base_progress und max_progress

    Returns dict {ok, error, returncode, stdout, stderr, elapsed}.
    Bei stream-json: stdout = vollständiger Antwort-Text aus result-Event
    (oder akkumulierter Token-Text wenn result fehlt).
    """
    import subprocess
    import threading
    import time
    import json as _json

    # cmd ggf. auf stream-json umkonfigurieren — überschreibt vorhandene Output-Flags
    if parse_stream_json:
        cmd = list(cmd)
        # Bestehende --output-format ... entfernen
        i = 0
        while i < len(cmd):
            if cmd[i] == "--output-format":
                # Flag + Wert raus
                del cmd[i:i + 2]
                continue
            i += 1
        cmd.extend(["--output-format", "stream-json", "--include-partial-messages", "--verbose"])

    # Mac vor Sleep schützen: caffeinate als Parent-Wrapper. Wenn der Mac über Nacht
    # in Sleep geht, würde der Anthropic-Stream sterben und der Subprocess endlos
    # auf Daten warten. caffeinate -i hält das System wach (Display darf schlafen).
    # Wenn der CLI-Aufruf endet, wird auch caffeinate gekillt (Parent-Verknüpfung).
    if use_caffeinate and sys.platform == "darwin":
        try:
            import shutil as _shutil
            _caf = _shutil.which("caffeinate")
            if _caf:
                # `-i` = idle sleep prevention. caffeinate exec'd das nachfolgende
                # Command und gibt die Sleep-Assertion frei wenn das Command endet.
                cmd = [_caf, "-i"] + list(cmd)
        except Exception:
            pass

    # SICHERHEIT: Env so säubern, dass `claude --print` GARANTIERT über Max-Abo
    # läuft, nicht über API-Credits. Wenn ANTHROPIC_BASE_URL oder ANTHROPIC_API_KEY
    # gesetzt sind (z.B. aus Claude Desktop heraus), würde Anthropic den Aufruf als
    # API-Request behandeln und die API-Credits belasten — auch wenn der User
    # eingeloggt ist. Das ist heute (30.04.2026) genau so passiert: $5,45 wurden
    # ungewollt von Florians API-Credits gezogen.
    safe_env = dict(os.environ)
    for var in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"):
        safe_env.pop(var, None)

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            bufsize=1,  # line-buffered
            env=safe_env,  # gesäubertes Env — keine API-Override-Variablen
        )
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"Claude CLI nicht ausführbar: {exc}",
                "returncode": -1, "stdout": "", "stderr": "", "elapsed": 0.0}
    except Exception as exc:
        return {"ok": False, "error": f"Subprocess-Start fehlgeschlagen: {exc}",
                "returncode": -1, "stdout": "", "stderr": "", "elapsed": 0.0}

    # Writer-Thread — verhindert dass großer stdin-Buffer den Main-Thread blockt
    writer_errors: List[str] = []

    def _writer():
        try:
            if proc.stdin:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
        except Exception as e:
            writer_errors.append(str(e))

    writer_thread = threading.Thread(target=_writer, daemon=True)
    writer_thread.start()

    # Reader-Threads — sammeln stdout/stderr asynchron
    stdout_chunks: List[str] = []  # rohe Zeilen (bei stream-json = JSON pro Zeile)
    stderr_chunks: List[str] = []
    accumulated_text: List[str] = []  # bei stream-json: live-akkumulierte Token-Deltas
    assistant_msgs: List[str] = []  # zusammengesetzte assistant-Event-Texte (SEPARAT — sonst
    # würde der Volltext doppelt gezählt: einmal als Deltas, einmal als assistant-Message)
    accumulated_thinking: List[str] = []  # extended_thinking-Tokens (für Status-Anzeige)
    final_result_text: List[Optional[str]] = [None]  # gesetzt bei result-Event
    cli_error_message: List[Optional[str]] = [None]
    seen_event_count = [0]
    final_usage: List[Optional[dict]] = [None]  # gesetzt bei result-Event mit usage-Daten
    final_cost_usd: List[Optional[float]] = [None]
    final_duration_ms: List[Optional[int]] = [None]

    def _read_stderr():
        try:
            for line in proc.stderr:
                stderr_chunks.append(line)
        except Exception:
            pass

    def _read_stdout_text():
        try:
            for line in proc.stdout:
                stdout_chunks.append(line)
        except Exception:
            pass

    def _read_stdout_stream_json():
        try:
            for line in proc.stdout:
                stdout_chunks.append(line)
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = _json.loads(stripped)
                except Exception:
                    continue
                seen_event_count[0] += 1
                etype = event.get("type")
                if etype == "stream_event":
                    inner = event.get("event") or {}
                    inner_type = inner.get("type")
                    if inner_type == "content_block_delta":
                        delta = inner.get("delta") or {}
                        delta_type = delta.get("type")
                        if delta_type == "text_delta":
                            accumulated_text.append(delta.get("text", ""))
                        elif delta_type == "thinking_delta":
                            # Extended-Thinking-Tokens separat tracken — wir sehen hier
                            # Reasoning-Tokens vor dem eigentlichen Output. Mit --effort low
                            # sollten kaum welche kommen, aber Status soll trotzdem
                            # ehrlich anzeigen wenn sie auftauchen.
                            accumulated_thinking.append(delta.get("thinking", ""))
                    elif inner_type == "content_block_start":
                        block = inner.get("content_block") or {}
                        if block.get("type") == "text":
                            txt = block.get("text", "")
                            if txt:
                                accumulated_text.append(txt)
                elif etype == "assistant":
                    # In eigene Liste — NICHT zu accumulated_text (Deltas), sonst doppelt!
                    msg = event.get("message") or {}
                    _msg_txt = "".join(
                        block.get("text", "")
                        for block in msg.get("content", [])
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                    if _msg_txt:
                        assistant_msgs.append(_msg_txt)
                elif etype == "result":
                    result_text = event.get("result")
                    if isinstance(result_text, str):
                        final_result_text[0] = result_text
                    if event.get("is_error"):
                        cli_error_message[0] = (event.get("subtype") or "result_error") + ": " + str(result_text or "")
                    # Token-Usage + Kosten + Dauer aus dem result-Event
                    usage_data = event.get("usage")
                    if isinstance(usage_data, dict):
                        final_usage[0] = usage_data
                    if "total_cost_usd" in event:
                        try:
                            final_cost_usd[0] = float(event["total_cost_usd"])
                        except Exception:
                            pass
                    if "duration_ms" in event:
                        try:
                            final_duration_ms[0] = int(event["duration_ms"])
                        except Exception:
                            pass
        except Exception:
            pass

    if parse_stream_json:
        out_thread = threading.Thread(target=_read_stdout_stream_json, daemon=True)
    else:
        out_thread = threading.Thread(target=_read_stdout_text, daemon=True)
    err_thread = threading.Thread(target=_read_stderr, daemon=True)
    out_thread.start()
    err_thread.start()

    started = time.time()
    last_callback = 0.0
    last_data_at = started  # Zeitpunkt der letzten neuen stdout-Bytes
    last_stdout_chars = 0

    while True:
        if proc.poll() is not None:
            break
        elapsed = time.time() - started

        # Idle-Detection: Wenn lange keine neuen Bytes kommen, ist die Verbindung
        # vermutlich tot (z.B. Mac war in Sleep, Network-Drop) — abbrechen statt
        # ewig warten. Greift erst nach idle_warmup_s Anlaufzeit, damit Extended
        # Thinking (kann ~30-60s ohne Output dauern) nicht fälschlich abgebrochen wird.
        current_chars = sum(len(c) for c in stdout_chunks)
        if current_chars > last_stdout_chars:
            last_stdout_chars = current_chars
            last_data_at = time.time()
        idle_for = time.time() - last_data_at
        if elapsed > idle_warmup_s and idle_for > idle_timeout_s:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            stdout_text = (
                final_result_text[0]
                if final_result_text[0] is not None
                else ("".join(accumulated_text) if parse_stream_json else "".join(stdout_chunks))
            )
            return {"ok": False,
                    "error": (
                        f"Idle-Abbruch: keine neuen Daten seit {int(idle_for)}s "
                        f"(nach {int(elapsed)}s Gesamtdauer). Vermutlich Network-Drop "
                        f"oder Mac war in Sleep — Verbindung zu Anthropic ist tot. "
                        f"Bitte erneut starten."
                    ),
                    "returncode": -1,
                    "stdout": stdout_text,
                    "stderr": "".join(stderr_chunks),
                    "elapsed": elapsed}

        if elapsed > timeout_seconds:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            stdout_text = (
                final_result_text[0]
                if final_result_text[0] is not None
                else ("".join(accumulated_text) if parse_stream_json else "".join(stdout_chunks))
            )
            return {"ok": False,
                    "error": f"Timeout nach {timeout_seconds}s",
                    "returncode": -1,
                    "stdout": stdout_text,
                    "stderr": "".join(stderr_chunks),
                    "elapsed": elapsed}

        # Progress-Callback alle poll_interval_s
        if progress_callback and (time.time() - last_callback) >= poll_interval_s:
            try:
                ratio_done = elapsed / max(expected_duration_s, 1.0)
                # Asymptotische Kurve: nähert sich max_progress, erreicht ihn aber nicht
                progress = base_progress + (max_progress - base_progress) * (1 - 1.0 / (1.0 + ratio_done * 1.5))
                progress = max(base_progress, min(max_progress, progress))
                if parse_stream_json:
                    chars = sum(len(t) for t in accumulated_text)
                    thinking_chars = sum(len(t) for t in accumulated_thinking)
                    if chars > 0:
                        suffix = f" · 🧠 {thinking_chars:,} Thinking" if thinking_chars > 0 else ""
                        status = f"{label} · seit {int(elapsed)}s · ~{chars:,} Zeichen empfangen{suffix}"
                    elif thinking_chars > 0:
                        # Sonnet ist im extended_thinking — wir sehen Reasoning-Tokens
                        status = (
                            f"{label} · seit {int(elapsed)}s · 🧠 Claude überlegt aktiv "
                            f"({thinking_chars:,} Thinking-Zeichen) — Output kommt danach"
                        )
                    elif seen_event_count[0] > 0:
                        if elapsed > 300:
                            status = (
                                f"{label} · seit {int(elapsed)}s · Claude denkt noch · "
                                f"{seen_event_count[0]} Events empfangen, aber noch kein Output"
                            )
                        else:
                            status = f"{label} · seit {int(elapsed)}s · Verbindung steht, Claude denkt ({seen_event_count[0]} Events)"
                    else:
                        status = f"{label} · seit {int(elapsed)}s · Verbindung wird aufgebaut"
                else:
                    bytes_received = sum(len(c) for c in stdout_chunks)
                    if bytes_received > 0:
                        status = f"{label} · seit {int(elapsed)}s · {bytes_received:,} Zeichen empfangen"
                    else:
                        status = f"{label} · seit {int(elapsed)}s · Output kommt am Ende auf einmal"
                progress_callback(status, progress)
                last_callback = time.time()
            except Exception:
                pass

        time.sleep(min(poll_interval_s, 0.5))

    # Subprozess fertig — restliche Threads abwarten
    out_thread.join(timeout=5)
    err_thread.join(timeout=5)
    writer_thread.join(timeout=2)
    elapsed = time.time() - started

    if parse_stream_json:
        _result_txt = final_result_text[0]
        # Volltext = die Live-Deltas (vollständig, auch über mehrere Nachrichten hinweg).
        # assistant_msgs NUR als Fallback, falls keine Deltas kamen — NIE beide addieren
        # (das verdoppelte sonst den Text: Deltas + assistant-Message = 2× derselbe Inhalt).
        _full_txt = "".join(accumulated_text) or "".join(assistant_msgs)
        if prefer_full_text and _full_txt and len(_full_txt) > len(_result_txt or ""):
            # Bei mehrteiligen Antworten (Claude splittet sehr lange Ausgaben über mehrere
            # Nachrichten) enthält das result-Event nur die LETZTE Nachricht. Für reine
            # Text-/Markdown-Pfade (Genius/Kompakt, Meta) ist der Volltext vollständiger.
            # NUR opt-in — JSON-Briefing-Pfade behalten das result-Event.
            stdout_text = _full_txt
        else:
            # Bevorzuge result-Event-Text (garantiert komplett im Einzelnachricht-Fall),
            # fallback auf den akkumulierten Volltext
            stdout_text = _result_txt if _result_txt is not None else _full_txt
    else:
        stdout_text = "".join(stdout_chunks)

    err_text = "".join(stderr_chunks)
    if cli_error_message[0]:
        err_text = (cli_error_message[0] + "\n" + err_text).strip()

    # Ein result-Event mit is_error=true (z.B. Quota/Limit/Auth) kommt teils mit
    # Exit-Code 0 — dann NICHT als Erfolg werten, sonst wird ein leeres/abgebrochenes
    # Briefing als ok durchgewunken und der echte Grund geht verloren.
    _cli_failed = cli_error_message[0] is not None
    return {
        "ok": not _cli_failed,
        "error": cli_error_message[0] if _cli_failed else None,
        "returncode": proc.returncode if not _cli_failed else (proc.returncode or 1),
        "stdout": stdout_text,
        "stderr": err_text,
        "elapsed": elapsed,
        "usage": final_usage[0],  # {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}
        "cost_usd": final_cost_usd[0],  # bei Max-Abo meist 0.0, bei API der echte $-Wert
        "duration_ms_claude": final_duration_ms[0],
    }


_CLAUDE_CLI_RESPONSE_PROMPT = """Du bekommst gleich ein großes Datenpaket mit Rohdaten für ein deutsches Audio-Tagesbriefing.

Im Datenpaket steckt oben ein detaillierter System-Prompt mit Anweisungen, wie das Briefing aussehen soll und in welchem JSON-Format du antworten musst.

Befolge die Anweisungen aus dem Datenpaket exakt. Liefere ausschließlich den geforderten JSON-Output (in einem ```json ... ``` Codeblock), ohne weitere Vorrede oder Kommentare. Keine Erklärungen davor oder danach.
"""


def run_briefing_via_claude_cli(
    handoff_text: str,
    output_pdf_path: str,
    *,
    model: str = "sonnet",
    timeout_seconds: int = 1800,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    cli_path: Optional[str] = None,
) -> dict:
    """Baut ein Briefing-PDF, indem es das Handoff-Paket via `claude` CLI an Claude
    Max-Abo schickt und die JSON-Antwort in eine PDF rendert.

    Parameters
    ----------
    handoff_text : str
        Vollständiges Handoff-Paket aus build_claude_handoff_package().
    output_pdf_path : str
        Zielpfad fürs PDF.
    model : str
        Claude-Modell-Alias oder voller Name (Default: "sonnet").
    timeout_seconds : int
        Maximale Wartezeit für die Claude-Antwort (Default: 15 Minuten).
    progress_callback : callable
        Optional: progress_callback(step_text, ratio_0_to_1).
    cli_path : str
        Optional: expliziter Pfad zum `claude` Binary (sonst Auto-Detect).

    Returns
    -------
    dict mit {ok, sections_count, error, completeness, raw_response, output_pdf_path}.
    """
    def _report(step: str, ratio: float):
        if progress_callback:
            try:
                progress_callback(step, max(0.0, min(1.0, ratio)))
            except Exception:
                pass

    if not handoff_text or not handoff_text.strip():
        return {"ok": False, "error": "Handoff-Text ist leer.", "raw_response": "",
                "sections_count": 0, "completeness": None, "output_pdf_path": None}

    cli = cli_path or _locate_claude_cli()
    if not cli:
        return {
            "ok": False,
            "error": "Claude CLI nicht gefunden. Stelle sicher, dass Claude Code installiert ist (https://claude.com/claude-code).",
            "raw_response": "", "sections_count": 0, "completeness": None,
            "output_pdf_path": None,
        }

    _report(f"Claude CLI gefunden ({os.path.basename(os.path.dirname(cli))})", 0.05)

    # Daten via stdin pipen — der gesamte handoff_text ist der "Prompt"
    # HINWEIS: `--bare` wird BEWUSST NICHT genutzt (bricht den OAuth/Max-Abo-Login).
    # `--print` für non-interactive Mode
    # `--output-format text` weil wir nur die Roh-Antwort brauchen
    # `--model` Auswahl des Modells
    # `--dangerously-skip-permissions` braucht's für reinen Print-Mode nicht zwingend,
    #   aber sicherheitshalber gegen interaktive Permission-Prompts
    cmd = [
        cli,
        "--print",
        "--output-format", "text",
        "--model", model,
        "--dangerously-skip-permissions",
        "--effort", "low",
        "--append-system-prompt", _CLAUDE_CLI_RESPONSE_PROMPT,
    ]

    _report(f"Briefing wird an Claude {model} geschickt…", 0.10)

    # Streaming-Wrapper: Live-Progress alle 2s während Claude denkt.
    # Erwartete Dauer pro Briefing: ~4 Min (240s) — Polling-Heuristik
    stream_result = _run_claude_cli_subprocess_streaming(
        cmd,
        handoff_text,
        cwd=os.path.dirname(os.path.abspath(output_pdf_path)) or os.getcwd(),
        timeout_seconds=timeout_seconds,
        progress_callback=progress_callback,
        base_progress=0.10,
        max_progress=0.78,
        # Realistische Erwartung: Sonnet braucht für 50+ Beiträge typisch 8–15 Min,
        # für sehr große Briefings (70+ Beiträge) auch mal 20+ Min
        expected_duration_s=600.0,
        label=f"Claude {model} schreibt das Briefing",
    )

    if not stream_result.get("ok"):
        return {"ok": False,
                "error": stream_result.get("error", "Subprocess-Fehler") + " — Bei sehr großen Briefings kannst du den manuellen Web-Chat-Pfad als Backup nutzen.",
                "raw_response": stream_result.get("stdout", ""),
                "sections_count": 0, "completeness": None, "output_pdf_path": None}

    stdout = stream_result["stdout"]
    stderr = stream_result["stderr"]
    elapsed = stream_result["elapsed"]
    returncode = stream_result["returncode"]
    _report(f"Claude hat geantwortet ({elapsed:.0f}s) — Ergebnis wird verarbeitet…", 0.82)

    if returncode != 0:
        # Häufige Fälle: Quota erreicht, Auth abgelaufen, CLI-Fehler
        err_short = (stderr or "").strip()[:600]
        out_short = (stdout or "").strip()[:600]
        msg = err_short or out_short or f"CLI-Exit-Code {returncode}"
        # Quota-spezifische Hinweise
        lower = (err_short + " " + out_short).lower()
        if "rate limit" in lower or "quota" in lower or "limit reached" in lower:
            msg = (f"Claude Max-Abo Limit erreicht. Warte ein paar Stunden oder nutze "
                   f"den API-Pfad. Original-Meldung: {msg}")
        elif ("socket" in lower or "closed unexpectedly" in lower
              or "econnreset" in lower or "fetch failed" in lower
              or "connection error" in lower or "network" in lower):
            msg = ("Verbindung zur Claude-CLI mittendrin abgebrochen. Das passiert bei "
                   "sehr großen Briefings (60+ Beiträge), weil der Lauf zu lange dauert. "
                   "Lösung: Nimm für große Briefings den API-Pfad weiter unten (zuverlässig, lädt "
                   "parallel) oder kreuze beim Claude-Pfad 'Nur Kompaktfassung direkt' an "
                   "bzw. wähle die Erzählversion ab.")
        elif "not authenticated" in lower or "login" in lower or "auth" in lower:
            msg = (f"Claude nicht eingeloggt. Im Terminal `claude` starten und einloggen, "
                   f"dann erneut versuchen. Original-Meldung: {msg}")
        return {"ok": False, "error": f"Claude CLI Fehler: {msg}",
                "raw_response": stdout or "", "sections_count": 0,
                "completeness": None, "output_pdf_path": None}

    raw_response = (stdout or "").strip()
    if not raw_response:
        return {"ok": False, "error": "Claude hat eine leere Antwort geliefert.",
                "raw_response": "", "sections_count": 0, "completeness": None,
                "output_pdf_path": None}

    # PDF aus dem Claude-JSON bauen (gleiche Pipeline wie Web-Chat-Pfad)
    _report("PDF wird gebaut…", 0.90)
    pdf_result = build_pdf_from_claude_json(
        raw_response,
        output_pdf_path,
        generated_at=get_berlin_now(),
        handoff_text=handoff_text,
    )

    if not pdf_result.get("ok"):
        # Roh-Antwort fürs Debuggen sichern — in den versteckten lokalen Debug-Ordner,
        # NICHT ins iCloud-Archiv (sonst Müll im sauberen Briefings-Ordner).
        debug_path = _save_debug_raw_response(output_pdf_path, raw_response)
        return {
            "ok": False,
            "error": f"{pdf_result.get('error')} (Roh-Antwort gespeichert: {debug_path})" if debug_path else pdf_result.get("error"),
            "raw_response": raw_response,
            "sections_count": pdf_result.get("sections_count", 0),
            "completeness": None,
            "output_pdf_path": None,
        }

    _report(f"Fertig — {pdf_result.get('sections_count')} Beiträge im PDF", 1.0)
    return {
        "ok": True,
        "error": None,
        "sections_count": pdf_result.get("sections_count", 0),
        "completeness": pdf_result.get("completeness"),
        "output_lint": pdf_result.get("output_lint"),
        "sorting_diag": pdf_result.get("sorting_diag"),
        "artifacts": pdf_result.get("artifacts"),
        "raw_response": raw_response,
        "output_pdf_path": output_pdf_path,
        "elapsed_seconds": elapsed,
        "clean_sections": pdf_result.get("clean_sections"),
        "usage": stream_result.get("usage"),
        "cost_usd": stream_result.get("cost_usd"),
    }


# ════════════════════════════════════════════════════════════════════
# CHUNKED CLI-PFAD — teilt große Briefings in Häppchen, damit die CLI-
# Verbindung nicht abreißt (Problem bei 60+ Beiträgen in einem Lauf).
# Jede Gruppe = ein kurzer CLI-Aufruf. Recap/Essenz/Verabschiedung als
# separater Finalisierungs-Aufruf. Neue Funktion neben dem alten Pfad —
# das bestehende run_briefing_via_claude_cli bleibt unangetastet.
# ════════════════════════════════════════════════════════════════════

_CLAUDE_CHUNK_ARTICLE_PROMPT = """Du erstellst einen TEIL eines deutschen Audio-Tagesbriefings aus den Rohdaten unten.

WICHTIG: Das ist nur ein Ausschnitt. Verwandle AUSSCHLIESSLICH die unten gelieferten Artikel/Paywall-Texte/Podcasts in Briefing-Sections. Baue KEIN Recap, KEINE Essenz, KEINE Verabschiedung — die kommen separat.

FORMAT JEDER SECTION (Markdown im content-Feld)
### Titel des Beitrags
*Ein einleitender, kursiver Einordnungssatz, der das Thema in einem Satz fasst.*

Fließtext-Zusammenfassung in 150-400 Wörtern (Kompakt-Modus: 150-250; Sehr-kurz-Modus: höchstens ~120 — exakte Vorgabe siehe KOMPAKT-MODUS-Zeile). HOHE INFORMATIONSDICHTE: pack die konkreten Fakten, Namen, Zahlen, Daten und Zusammenhänge so rein, dass der Hörer wirklich mitreden kann — kein oberflächliches Dahingleiten, keine Floskeln, keine Worthülsen. INTELLIGENTE TIEFE: bei tragweitenstarken oder komplexen Themen (Politik, Gericht, Wirtschaft, Konflikte, Wissenschaft) in die Tiefe gehen und die OBERE Wortgrenze nutzen; bei Kleinmeldungen knapp an der unteren Grenze bleiben. Aktive Sprache, konkrete Fakten, gesprochener Stil. Nichts erfinden — nur was im Quelltext steht. Quellen werden NICHT im Text genannt.

#### Was bleibt:

Der Merksatz, der hängenbleibt — 1, höchstens 2 Sätze. Fasse die Essenz so auf den Punkt, dass auch jemand, der den Beitrag verpasst hat, ihn in diesem einen Satz versteht, UND bring die Konsequenz mit: was sich dadurch ändert oder warum es zählt. Konkret mit dem Wesentlichen (Name/Zahl/Folge), kein abstrakter Floskel-Schlusssatz.

Weiter geht's.

REGELN
- Eine Section pro geliefertem Artikel/Paywall/Podcast. Nichts weglassen, nichts zusammenfassen.
- ZUSAMMENGEFÜHRTE QUELLEN: Enthält ein Block den Marker „WEITERE QUELLE ZUM SELBEN EREIGNIS", dann ist das EIN Ereignis aus mehreren Quellen. Schreibe genau EINE Section, die alle einzigartigen Fakten, Zahlen und Perspektiven ALLER Quellen integriert — keine zwei Sections daraus machen, keine Details verlieren. Füge direkt nach dem kursiven Einordnungssatz eine zweite kursive Zeile ein: *Dieser Beitrag bündelt mehrere Quellen zum selben Ereignis.*
- SPRACHE: durchgehend Deutsch. Übersetze englische/fremdsprachige Quellen restlos ins Deutsche. KEINE englischen Wortfetzen, Phrasen oder Halbsätze im Text stehen lassen — auch nicht mitten im Satz (also nicht „meaningful response", „being fully modernized", „square feet"). Nur Eigennamen/Originaltitel dürfen im Original bleiben. Englische Zitate ins Deutsche übersetzen.
- KEINE Aufzählungszeichen, KEINE Listen, KEINE Trennlinien. Schlüsselbegriffe in **fett**. Umlaute korrekt.
- Podcasts: Zusammenfassung wie geliefert übernehmen, nur formal säubern. Endmarker `Ende der Podcastzusammenfassung.` statt `Weiter geht's.`
- Wetter (falls geliefert): als Section mit "_weather": true.

ENDFORMAT — gib NUR diesen einen JSON-Codeblock zurück:
```json
{
  "sections": [
    {"type": "article", "source_label": "Schwäbisches Tagblatt", "content": "### Titel\\n\\n*Einordnung*\\n\\nText...\\n\\n#### Was bleibt:\\n\\nKern.\\n\\nWeiter geht's."}
  ]
}
```
JSON muss valide sein (Newlines als \\\\n, Anführungszeichen im Text als \\\\"). Kein Text nach dem JSON-Block.

═══════════════════════════════════════════════════════════
ROHDATEN (dieser Ausschnitt)
═══════════════════════════════════════════════════════════
"""

_CLAUDE_FINALIZE_PROMPT = """Du baust den Rahmen eines deutschen Audio-Tagesbriefings: eine Top-3-Vorschau (ganz an den Anfang), Rückblick, Essenz und Verabschiedung.

Unten findest du die Titel und Kernaussagen ALLER Beiträge des heutigen Briefings. Baue daraus vier Sections.

0) TOP-3-VORSCHAU (kommt GANZ AN DEN ANFANG des Briefings)
### Die drei wichtigsten Themen heute

1. Ein prägnanter Satz zur wichtigsten Story des Tages — mit dem konkreten Namen/Zahl, worum es geht.
2. Ein prägnanter Satz zur zweitwichtigsten Story.
3. Ein prägnanter Satz zur drittwichtigsten Story.

Wähle nach Tragweite und Relevanz für den Hörer, NICHT nach Reihenfolge im Briefing. Keine Wetter-/Podcast-Nennung hier, es sei denn, sie ist wirklich das Top-Thema.

1) RÜCKBLICK
### Rückblick

Ein lebendiger, fließender Rückblick in 2-4 kurzen Absätzen — KEINE Aufzählung, KEINE Bullet-Points, keine Strichliste (das klingt beim Vorlesen seelenlos und langweilig). Erzähl den Bogen des Tages mit Schwung und Stimme: gruppiere locker nach Themen (Politik/International, Wirtschaft, Tech, Lokales, Kultur/Sport — nur was vorkam) und verbinde sie zu einem Fluss. Nenne die markanten Geschichten konkret mit Namen/Stichwort, aber hak NICHT jede Kleinmeldung mechanisch ab — pick die, die den Tag wirklich geprägt haben. Gesprochen, mit Energie, gern trocken-pointiert, aber ohne Pathos. Es soll Lust machen, dranzubleiben, und zugleich in Erinnerung rufen, was lief.

Weiter geht's.

2) ESSENZ
### Was wirklich bleibt
*Die Essenz aus dem heutigen Briefing — was über den Tag hinaus zählt.*

3-6 Absätze Fließtext, jeder beginnt mit dem Kerngedanken in **fett**, dann 2-3 Sätze. Muster, strukturelle Verschiebungen, Langzeitwirkung. Keine Tageskleinigkeiten aufblasen.

3) VERABSCHIEDUNG
### Bis zum nächsten Mal

Kluges, weniger bekanntes Zitat (echte Quelle, kein Kalenderspruch) + 1-2 Sätze Einordnung + persönlicher Gruß passend zur Tageszeit. Max 90 Wörter.

Ende des Briefings.

ENDFORMAT — gib NUR diesen einen JSON-Codeblock zurück:
```json
{
  "sections": [
    {"type": "article", "_preview": true, "source_label": "Top 3", "content": "### Die drei wichtigsten Themen heute\\n\\n1. ...\\n2. ...\\n3. ..."},
    {"type": "article", "_recap": true, "source_label": "Rückblick", "content": "### Rückblick\\n\\n2-4 fließende Absätze, lebendig erzählt — keine Liste...\\n\\nWeiter geht's."},
    {"type": "article", "_essenz": true, "source_label": "Essenz", "content": "### Was wirklich bleibt\\n\\n*Essenz*\\n\\n**Punkt.** Erklärung..."},
    {"type": "article", "_verabschiedung": true, "source_label": "Abschluss", "content": "### Bis zum nächsten Mal\\n\\nZitat... Gruß.\\n\\nEnde des Briefings."}
  ]
}
```
JSON muss valide sein. Kein Text nach dem JSON-Block.

═══════════════════════════════════════════════════════════
BEITRÄGE DES HEUTIGEN BRIEFINGS
═══════════════════════════════════════════════════════════
"""


# (Entfernt 05.06.2026: _auto_drop_duplicate_items — der stille Mathematik-Dedup
#  wurde durch den klickbaren LLM-Dedup ersetzt; reine Stem/Jaccard-Mathematik
#  konnte „gleiche Story" nicht von „anderer Blickwinkel" unterscheiden.)


def _merge_duplicate_story_items(items, cli_path=None, progress_callback=None):
    """Führt Beiträge, die SICHER dasselbe konkrete Ereignis behandeln (gleiche Story
    aus mehreren Quellen), zu EINEM Item zusammen — statt eines stumpf zu verwerfen.

    Zweistufig: (1) Mathematik findet nur KANDIDATEN (Stem-Overlap, locker),
    (2) Claude bestätigt konservativ per llm_confirm_duplicate_clusters (gleiche
    Story → mergen; verschiedene Blickwinkel → getrennt lassen; im Zweifel getrennt).
    Beim Merge bleiben BEIDE Quelltexte vollständig erhalten (Marker „WEITERE
    QUELLE…"), der Artikel-Prompt integriert daraus alle einzigartigen Fakten —
    es geht also nichts verloren. Podcasts sind ausgenommen (kuratiert Florian selbst).

    Returns: (items, merged_count, merge_notes) — merge_notes = ["NDR + tagesschau: Grund", …]
    """
    eligible = [i for i, it in enumerate(items) if it.get("kind") in ("article", "paywall")]
    if len(eligible) < 2:
        return items, 0, []

    def _strip_qhead(body):
        # Entfernt den "QUELLE: …\nURL: …"-Kopf, damit Schlagzeile/Lead und der
        # Opus-Auszug auf dem ARTIKELINHALT basieren (Quelle/URL ist outlet-
        # spezifisch und würde sonst Ähnlichkeit verwässern bzw. den Auszug fressen).
        b = (body or "").strip()
        head, sep, rest = b.partition("\n\n")
        if sep and head.upper().startswith("QUELLE"):
            return rest.strip()
        return b

    stems = {}      # voller Body (erste 1500 Z.) — gut für kurze/mittlere Texte
    hl_stems = {}   # Schlagzeile/Lead (erste 250 Z.) — längenrobust für lange Artikel
    for i in eligible:
        body = items[i].get("body") or ""
        toks = _topic_similarity_tokens(body[:1500])
        stems[i] = {_stem_for_cluster(t) for t in toks if len(t) >= 5}
        hl = _strip_qhead(body)[:250]
        hl_stems[i] = {_stem_for_cluster(t) for t in _topic_similarity_tokens(hl) if len(t) >= 5}

    parent = {i: i for i in eligible}

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    near_miss = []  # Diagnose: knapp verfehlte Paare (zum Nachjustieren der Schwellen)
    for a_pos in range(len(eligible)):
        i = eligible[a_pos]
        for j in eligible[a_pos + 1:]:
            # Pfad 1 (bestehend): voller Body — Overlap≥4 UND Jaccard≥0.14.
            ov_f = len(stems[i] & stems[j]) if (len(stems[i]) >= 5 and len(stems[j]) >= 5) else 0
            un_f = len(stems[i] | stems[j]) or 1
            jac_f = ov_f / un_f
            path_full = ov_f >= 4 and jac_f >= 0.14
            # Pfad 2 (neu): Schlagzeile/Lead — Overlap≥6 UND Jaccard≥0.14. Längenrobust,
            # fängt lange Same-Event-Artikel, deren Jaccard im vollen Body verwässert.
            ov_h = len(hl_stems[i] & hl_stems[j])
            un_h = len(hl_stems[i] | hl_stems[j]) or 1
            jac_h = ov_h / un_h
            path_hl = ov_h >= 6 and jac_h >= 0.14
            if path_full or path_hl:
                ra, rb = _find(i), _find(j)
                if ra != rb:
                    parent[ra] = rb
            elif ov_h >= 4 or (ov_f >= 6 and jac_f >= 0.10):
                # Nur WIRKLICH knappe Verfehlungen loggen (Schlagzeile fast über Schwelle,
                # oder Body mit hohem Overlap nahe der Jaccard-Grenze) — sonst flutet
                # generisches Body-Vokabular (z.B. viele tagesschau-Artikel) das Log.
                near_miss.append(
                    f"{items[i].get('label','?')}~{items[j].get('label','?')} "
                    f"[body ov={ov_f} j={jac_f:.2f} | head ov={ov_h} j={jac_h:.2f}]"
                )

    if near_miss:
        print(f"[merge] {len(near_miss)} Beinahe-Treffer (knapp unter Schwelle): "
              f"{near_miss[:15]}", file=sys.stderr)

    groups = {}
    for i in eligible:
        groups.setdefault(_find(i), []).append(i)
    candidate_clusters = [sorted(g) for g in groups.values() if len(g) >= 2]
    if not candidate_clusters:
        print("[merge] keine Doppel-Kandidaten gefunden.", file=sys.stderr)
        return items, 0, []
    print(f"[merge] {len(candidate_clusters)} Kandidaten-Cluster — Opus prüft…", file=sys.stderr)

    if progress_callback:
        try:
            progress_callback(f"{len(candidate_clusters)} mögliche Doppel-Themen — Claude prüft (gleiche Story vs. Blickwinkel)…", 0.13)
        except Exception:
            pass

    clusters_payload = [
        {"members": [
            {"index": i, "url": "", "source_label": items[i].get("label", "?"),
             "excerpt": _strip_qhead(items[i].get("body") or "")[:900]}
            for i in grp
        ]}
        for grp in candidate_clusters
    ]
    try:
        # Urteils-Aufgabe (gleiche Story vs. Blickwinkel) → Opus, wie die anderen
        # Judgment-Schritte. Ein kleiner Call (nur Auszüge), gratis via Max-Abo.
        verdict = llm_confirm_duplicate_clusters(clusters_payload, model=_CLI_JUDGE_MODEL, cli_path=cli_path)
    except Exception:
        verdict = {}
    if not verdict:
        print("[merge] Opus-Prüfung lieferte kein Ergebnis — nichts gemergt (sicherheitshalber).", file=sys.stderr)
        return items, 0, []  # Prüfung nicht möglich → sicherheitshalber nichts mergen

    to_remove = set()
    merge_notes = []
    for ci, grp in enumerate(candidate_clusters, 1):
        v = verdict.get(ci) or {}
        if not v.get("is_duplicate"):
            continue
        keep = v.get("keep")
        if keep not in grp:
            keep = max(grp, key=lambda i: len(items[i].get("body") or ""))
        others = [i for i in grp if i != keep]
        if not others:
            continue
        merged_body = items[keep].get("body") or ""
        for o in others:
            merged_body += (
                f"\n\n--- WEITERE QUELLE ZUM SELBEN EREIGNIS ({items[o].get('label', '?')}) — "
                f"einzigartige Details einarbeiten, nichts doppeln ---\n\n"
                + (items[o].get("body") or "")
            )
        merged_label = items[keep].get("label", "?") + " + " + ", ".join(items[o].get("label", "?") for o in others)
        items[keep] = {**items[keep], "body": merged_body[:16000], "label": merged_label}
        to_remove.update(others)
        merge_notes.append(f"{merged_label}: {v.get('reason', '')[:120]}")

    if not to_remove:
        print("[merge] Opus: alle Kandidaten sind verschiedene Blickwinkel — nichts gemergt.", file=sys.stderr)
        return items, 0, []
    new_items = [it for i, it in enumerate(items) if i not in to_remove]
    print(f"[merge] {len(to_remove)} Quelle(n) in {len(merge_notes)} Thema/Themen zusammengeführt: {merge_notes}", file=sys.stderr)
    return new_items, len(merge_notes), merge_notes


def _friendly_cli_login_error(err_text: str, cli_path: Optional[str] = None) -> Optional[str]:
    """Erkennt die kryptische Abmelde-Meldung der Claude-CLI und macht daraus eine
    handlungsfähige Anleitung mit dem exakten Terminal-Befehl. None wenn nicht zutreffend."""
    if not err_text or ("Not logged in" not in err_text and "Please run /login" not in err_text):
        return None
    cli = cli_path or _locate_claude_cli() or "claude"
    return (
        "Claude-CLI ist abgemeldet (Anmelde-Token endgültig abgelaufen — passiert alle paar Monate). "
        "Einmalige Neuanmeldung: Terminal öffnen, diesen Befehl ausführen:  "
        f"'{cli}'  — dann /login eingeben und den Browser-Login bestätigen. "
        "Danach laufen Briefing und Podcast-Verdichtung sofort wieder."
    )


def check_cli_login(cli_path: Optional[str] = None) -> Optional[str]:
    """Schneller Anmelde-Test der Claude-CLI (~5-10s). Returns None wenn angemeldet,
    sonst die handlungsfähige Fehlermeldung. Für den Früh-Abbruch vor langen Läufen."""
    cli = cli_path or _locate_claude_cli()
    if not cli:
        return "Claude CLI nicht gefunden."
    try:
        sr = _run_claude_cli_subprocess_streaming(
            [cli, "--print", "--output-format", "text", "--model", "haiku",
             "--dangerously-skip-permissions", "--effort", "low"],
            "Antworte nur: OK", timeout_seconds=60, expected_duration_s=6.0,
            label="Anmelde-Check")
    except Exception as exc:
        return f"CLI-Check fehlgeschlagen: {exc}"
    out = ((sr.get("stdout") or "") + " " + (sr.get("stderr") or "")).strip()
    friendly = _friendly_cli_login_error(out, cli)
    if friendly:
        return friendly
    if sr.get("ok") and sr.get("returncode") == 0:
        return None
    return None  # anderer Fehler ≠ abgemeldet — Lauf nicht blockieren


APPLE_PODCAST_TTML_DIR = os.path.expanduser(
    "~/Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Library/Cache/Assets/TTML"
)
_TTML_NS_P = "{http://www.w3.org/ns/ttml}p"
_TTML_NS_SPAN = "{http://www.w3.org/ns/ttml}span"
_TTML_NS_AGENT = "{http://www.w3.org/ns/ttml#metadata}agent"
_TTML_NS_UNIT = "{http://podcasts.apple.com/transcript-ttml-internal}unit"


def apple_ttml_to_text(path: str) -> str:
    """Wandelt ein von Apple Podcasts gecachtes TTML-Transkript in Klartext um —
    mit Sprecher-Labels (Sprecherwechsel = neue Zeile). Die App cached das TTML,
    sobald man das Transkript einer Episode einmal geöffnet hat."""
    import xml.etree.ElementTree as _ET
    root = _ET.parse(path).getroot()
    lines = []
    current_agent = None
    current_words: list = []
    for p in root.iter(_TTML_NS_P):
        agent = p.get(_TTML_NS_AGENT) or current_agent or ""
        words = [s.text for s in p.iter(_TTML_NS_SPAN)
                 if s.get(_TTML_NS_UNIT) == "word" and s.text]
        if not words:
            raw = " ".join(t.strip() for t in p.itertext() if t and t.strip())
            words = [raw] if raw else []
        if not words:
            continue
        if agent != current_agent and current_words:
            lines.append(f"{current_agent or 'Sprecher'}: " + " ".join(current_words))
            current_words = []
        current_agent = agent
        current_words.extend(words)
    if current_words:
        lines.append(f"{current_agent or 'Sprecher'}: " + " ".join(current_words))
    return "\n".join(lines)


APPLE_PODCAST_DB = os.path.expanduser(
    "~/Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite")
_TTML_TITLE_CACHE: Dict[str, str] = {}


def apple_transcript_titles(paths: List[str]) -> Dict[str, str]:
    """Löst TTML-Cache-Dateien zu "Podcast — Episode" auf (Apple-Bibliotheks-DB, read-only).

    Join-Schlüssel: transcript_<ID> im Dateinamen ↔ ZMT*TRANSCRIPTIDENTIFIER.
    Eine Query über alle Identifier (33k Zeilen, <1s), Ergebnisse werden pro Pfad
    gecacht — Reruns kosten nichts.
    """
    import sqlite3

    need = {}
    for p in paths:
        if p in _TTML_TITLE_CACHE:
            continue
        m = re.search(r"transcript_(\d+)", os.path.basename(p) or "")
        if m:
            need[m.group(1)] = p
    if need:
        try:
            con = sqlite3.connect(f"file:{APPLE_PODCAST_DB}?mode=ro", uri=True, timeout=2)
            try:
                rows = con.execute(
                    "SELECT COALESCE(e.ZENTITLEDTRANSCRIPTIDENTIFIER, ''), COALESCE(e.ZFREETRANSCRIPTIDENTIFIER, ''), "
                    "COALESCE(e.ZTITLE, ''), COALESCE(p.ZTITLE, '') "
                    "FROM ZMTEPISODE e LEFT JOIN ZMTPODCAST p ON e.ZPODCASTUUID = p.ZUUID "
                    "WHERE e.ZENTITLEDTRANSCRIPTIDENTIFIER IS NOT NULL OR e.ZFREETRANSCRIPTIDENTIFIER IS NOT NULL"
                ).fetchall()
            finally:
                con.close()
            for ident1, ident2, ep_title, pod_title in rows:
                for ident in (ident1, ident2):
                    m = re.search(r"transcript_(\d+)", ident)
                    if m and m.group(1) in need:
                        title = " — ".join(x for x in (pod_title.strip(), ep_title.strip()) if x)
                        if title:
                            _TTML_TITLE_CACHE[need[m.group(1)]] = title
        except Exception as exc:
            print(f"[ttml-titel] Apple-DB nicht lesbar: {exc}", file=sys.stderr)
    return {p: _TTML_TITLE_CACHE[p] for p in paths if p in _TTML_TITLE_CACHE}


def list_apple_podcast_transcripts(limit: int = 15) -> List[dict]:
    """Listet die von Apple Podcasts lokal gecachten Transkripte (neueste zuerst).
    Returns: [{"path", "mtime", "size_kb", "minutes", "lang", "preview"}, …]"""
    import glob as _glob
    import xml.etree.ElementTree as _ET
    out = []
    for p in _glob.glob(os.path.join(APPLE_PODCAST_TTML_DIR, "**", "*.ttml"), recursive=True):
        try:
            st_ = os.stat(p)
            entry = {"path": p, "mtime": st_.st_mtime, "size_kb": st_.st_size // 1024,
                     "minutes": 0, "lang": "?", "preview": ""}
            try:
                root = _ET.parse(p).getroot()
                entry["lang"] = root.get("{http://www.w3.org/XML/1998/namespace}lang") or "?"
                body = root.find("{http://www.w3.org/ns/ttml}body")
                if body is not None and body.get("dur"):
                    entry["minutes"] = int(float(body.get("dur")) / 60)
                words = []
                for pp in root.iter(_TTML_NS_P):
                    words.extend(s.text for s in pp.iter(_TTML_NS_SPAN)
                                 if s.get(_TTML_NS_UNIT) == "word" and s.text)
                    if len(words) > 24:
                        break
                entry["preview"] = " ".join(words[:24])
            except Exception:
                pass
            out.append(entry)
        except Exception:
            continue
    # Bereits importierte/erledigte ausblenden (🗑️ und erfolgreiche Verdichtung
    # markieren imported → Eintrag verschwindet hier und in der Nachlese).
    try:
        _imported = set((_podcast_inbox_state_load().get("imported_ttml") or {}).keys())
        out = [e for e in out if os.path.basename(e["path"]) not in _imported and e["path"] not in _imported]
    except Exception:
        pass
    out.sort(key=lambda e: -e["mtime"])
    out = out[:limit]
    titles = apple_transcript_titles([e["path"] for e in out])
    for e in out:
        e["title"] = titles.get(e["path"], "")
    return out


PODCAST_OPML_PATH = os.path.expanduser(
    "~/Library/CloudStorage/OneDrive-Persönlich/Diverses/podcasts.opml")
PODCAST_INBOX_STATE_PATH = os.getenv("BRIEFING_INBOX_STATE_PATH") or os.path.expanduser("~/.briefing_podcast_inbox.json")


PODCAST_OPML_LOCAL_MIRROR = os.path.expanduser("~/.briefing_podcast_feeds.opml")


def load_podcast_feeds_from_opml(path: Optional[str] = None) -> List[dict]:
    """Liest Florians Podcast-Liste aus dem Pocket-Casts-OPML-Export.

    WICHTIG: Der launchd-Dienst darf ~/Library/CloudStorage (OneDrive) nicht lesen
    (TCC) — daher wird ein lokaler Spiegel gepflegt: Ist das OneDrive-Original
    lesbar, aktualisieren wir den Spiegel; sonst lesen wir den Spiegel.
    """
    import xml.etree.ElementTree as _ET

    def _parse(p):
        try:
            tree = _ET.parse(p)
        except Exception:
            return []
        out = []
        for o in tree.iter("outline"):
            url = o.get("xmlUrl")
            if url:
                out.append({"name": (o.get("text") or "").strip() or url.split("/")[2], "url": url})
        return out

    if path:
        return _parse(path)
    feeds = []
    try:
        if os.path.exists(PODCAST_OPML_PATH):
            feeds = _parse(PODCAST_OPML_PATH)
            if feeds:
                try:  # Spiegel aktualisieren, solange wir Zugriff haben
                    import shutil
                    shutil.copyfile(PODCAST_OPML_PATH, PODCAST_OPML_LOCAL_MIRROR)
                except Exception:
                    pass
    except Exception:
        feeds = []
    if not feeds and os.path.exists(PODCAST_OPML_LOCAL_MIRROR):
        feeds = _parse(PODCAST_OPML_LOCAL_MIRROR)
    return feeds


def _podcast_inbox_state_load() -> dict:
    try:
        with open(PODCAST_INBOX_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _podcast_inbox_state_save(state: dict) -> None:
    try:
        with open(PODCAST_INBOX_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def podcast_inbox_mark(guids: List[str], field: str = "archived",
                       eps_meta: Optional[List[dict]] = None) -> None:
    """Merkt Episoden dauerhaft als archiviert/zusammengefasst (kein Wiederauftauchen).
    eps_meta: die Episoden-Dicts dazu — werden mitgespeichert, damit das 🗂️-Archiv
    Titel anzeigen und Folgen wiederherstellen kann."""
    state = _podcast_inbox_state_load()
    eps = state.setdefault("episodes", {})
    now_iso = get_berlin_now().isoformat()
    meta_by_guid = {e["guid"]: e for e in (eps_meta or []) if e.get("guid")}
    for g in guids:
        entry = eps.setdefault(g, {})
        entry[field] = now_iso
        if g in meta_by_guid and "meta" not in entry:
            entry["meta"] = {k: v for k, v in meta_by_guid[g].items() if k != "pub_ts"}
    _podcast_inbox_state_save(state)


def podcast_inbox_archived_list(limit: int = 30) -> tuple:
    """Archivierte Folgen MIT Details, neueste zuerst. Returns (list, n_ohne_details)."""
    state = _podcast_inbox_state_load()
    with_meta, without = [], 0
    for g, v in (state.get("episodes") or {}).items():
        if not v.get("archived"):
            continue
        if v.get("meta"):
            with_meta.append({"guid": g, "archived": v["archived"], **v["meta"]})
        else:
            without += 1
    with_meta.sort(key=lambda e: e.get("archived", ""), reverse=True)
    return with_meta[:limit], without


def podcast_inbox_unarchive(guids: List[str]) -> None:
    """Holt versehentlich Archiviertes zurück (summarized bleibt unangetastet)."""
    state = _podcast_inbox_state_load()
    eps = state.get("episodes") or {}
    for g in guids:
        if g in eps:
            eps[g].pop("archived", None)
    _podcast_inbox_state_save(state)


def _episode_guid(feed_name: str, title: str, pub) -> str:
    import hashlib
    return hashlib.md5(f"{feed_name}|{title}|{pub}".encode()).hexdigest()[:16]


def fetch_new_podcast_episodes(days: int = 3, max_per_feed: int = 6,
                               progress_callback: Optional[Callable] = None) -> dict:
    """Holt die NEUEN Episoden (Zeitfenster, kein Back-Katalog!) aus allen OPML-Feeds.
    Archivierte/verdichtete Episoden werden ausgefiltert (persistenter Zustand).

    Returns: {"episodes": [...], "errors": [...], "n_feeds": int}
    Episode: {guid, feed, title, published(iso), age_h, transcript_url, transcript_type,
              episode_link, summarized(bool)}
    """
    import urllib.request as _ur
    import ssl as _ssl
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from email.utils import parsedate_to_datetime

    feeds = load_podcast_feeds_from_opml()
    if not feeds:
        return {"episodes": [], "n_feeds": 0, "errors": [
            "Podcast-Liste nicht lesbar (OneDrive-OPML für den Hintergrunddienst gesperrt und kein lokaler "
            f"Spiegel unter {PODCAST_OPML_LOCAL_MIRROR}). Fix: OPML aus Pocket Casts neu exportieren oder "
            "einmal von Hand dorthin kopieren."]}
    state = _podcast_inbox_state_load().get("episodes", {})
    cutoff = get_berlin_now().timestamp() - days * 86400
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE

    _tr_re = re.compile(r'<podcast:transcript[^>]*url="([^"]+)"[^>]*?(?:type="([^"]+)")?[^>]*/?>', re.IGNORECASE)
    _tr_re2 = re.compile(r'<podcast:transcript[^>]*type="([^"]+)"[^>]*url="([^"]+)"', re.IGNORECASE)

    def _tag(block, name):
        m = re.search(rf"<{name}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", block, re.DOTALL | re.IGNORECASE)
        return (m.group(1).strip() if m else "")

    def _one(feed):
        try:
            req = _ur.Request(feed["url"], headers={"User-Agent": "Mozilla/5.0 (BriefingApp)"})
            with _ur.urlopen(req, timeout=12, context=ctx) as r:
                data = r.read(600_000).decode("utf-8", errors="ignore")
        except Exception as exc:
            return [], f"{feed['name']}: {str(exc)[:60]}"
        # Podcast-Cover aus dem Channel-Kopf (für die Inbox-Anzeige, wie Pocket Casts)
        import html as _html

        def _unesc_url(u):
            # Feeds liefern URLs XML-escaped (&amp; statt &) — unentschärft liefern
            # Bild-/Audio-Server (z.B. tagesschau) dann 404.
            return _html.unescape((u or "").strip()) or None

        _head = data.split("<item", 1)[0]
        _m_img = (re.search(r'<itunes:image[^>]*href="([^"]+)"', _head, re.IGNORECASE)
                  or re.search(r"<image>.*?<url>\s*(.*?)\s*</url>", _head, re.DOTALL | re.IGNORECASE)
                  # Fallback: Channel-Bild steht bei manchen Feeds erst NACH den Items
                  or re.search(r'<itunes:image[^>]*href="([^"]+)"', data, re.IGNORECASE))
        feed_img = _unesc_url(_m_img.group(1)) if _m_img else None
        eps = []
        for item in re.findall(r"<item[\s>].*?</item>", data, re.DOTALL | re.IGNORECASE)[:max_per_feed * 3]:
            title = re.sub(r"<[^>]+>", "", _tag(item, "title"))
            pub_raw = _tag(item, "pubDate")
            try:
                pub_dt = parsedate_to_datetime(pub_raw)
                pub_ts = pub_dt.timestamp()
            except Exception:
                continue
            if pub_ts < cutoff:
                continue
            # Transkript-URL: bevorzugt vtt > srt > text > json > html
            cands = []
            for m in _tr_re.finditer(item):
                cands.append((m.group(1), (m.group(2) or "").lower()))
            for m in _tr_re2.finditer(item):
                cands.append((m.group(2), (m.group(1) or "").lower()))
            best = None
            for pref in ("vtt", "srt", "plain", "text", "json", "html"):
                for u, t in cands:
                    if pref in t or (pref in u.lower().rsplit(".", 1)[-1] if "." in u else False):
                        best = (u, t or pref)
                        break
                if best:
                    break
            if not best and cands:
                best = cands[0]
            guid = _episode_guid(feed["name"], title, pub_raw)
            st_ = state.get(guid, {})
            if st_.get("archived") or st_.get("summarized"):
                # Zusammengefasst = erledigt — taucht nie wieder in der Inbox auf (Florians Regel).
                continue
            # Episoden-Beschreibung (kurz, entHTMLt) + Cover (Episoden-Bild vor Feed-Bild)
            _desc_raw = _tag(item, "description") or _tag(item, "itunes:summary")
            _desc = _html.unescape(re.sub(r"<[^>]+>", " ", _desc_raw))
            _desc = " ".join(_desc.split())[:220]
            _dur_raw = _tag(item, "itunes:duration")
            _dur_min = None
            try:
                if ":" in _dur_raw:
                    _secs = 0
                    for _dp in _dur_raw.split(":"):
                        _secs = _secs * 60 + int(float(_dp))
                else:
                    _secs = int(float(_dur_raw))
                _dur_min = _secs // 60 if _secs >= 60 else None
            except Exception:
                _dur_min = None
            _m_ei = re.search(r'<itunes:image[^>]*href="([^"]+)"', item, re.IGNORECASE)
            _m_enc = re.search(r'<enclosure[^>]*url="([^"]+)"', item, re.IGNORECASE)
            eps.append({
                "guid": guid, "feed": feed["name"], "title": title[:140],
                "image": (_unesc_url(_m_ei.group(1)) if _m_ei else feed_img),
                "duration_min": _dur_min,
                "desc": _desc,
                "published": pub_dt.isoformat(), "pub_ts": pub_ts,
                "age_h": max(0, int((get_berlin_now().timestamp() - pub_ts) / 3600)),
                "audio_url": _unesc_url(_m_enc.group(1)) if _m_enc else None,
                "transcript_url": _unesc_url(best[0]) if best else None,
                "transcript_type": best[1] if best else None,
                "episode_link": _tag(item, "link")[:300],
                "summarized": bool(st_.get("summarized")),
            })
            if len(eps) >= max_per_feed:
                break
        return eps, None

    episodes, errors = [], []
    done = [0]
    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = {pool.submit(_one, f): f for f in feeds}
        for fu in as_completed(futs):
            eps, err = fu.result()
            episodes.extend(eps)
            if err:
                errors.append(err)
            done[0] += 1
            if progress_callback and done[0] % 20 == 0:
                try:
                    progress_callback(f"Feeds geprüft: {done[0]}/{len(feeds)}…", done[0] / len(feeds))
                except Exception:
                    pass
    episodes.sort(key=lambda e: -e["pub_ts"])
    return {"episodes": episodes, "errors": errors, "n_feeds": len(feeds)}


def download_feed_transcript(url: str, ttype: Optional[str] = None) -> Optional[str]:
    """Lädt ein Feed-Transkript (VTT/SRT/JSON/HTML/Text) und liefert Klartext."""
    import urllib.request as _ur
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    try:
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0 (BriefingApp)"})
        with _ur.urlopen(req, timeout=20, context=ctx) as r:
            raw = r.read(3_000_000).decode("utf-8", errors="ignore")
    except Exception:
        return None
    t = (ttype or "").lower() + " " + url.lower()
    if "json" in t and raw.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(raw)
            segs = data.get("segments") if isinstance(data, dict) else data
            parts = []
            last_speaker = None
            for s in (segs or []):
                if not isinstance(s, dict):
                    continue
                body = s.get("body") or s.get("text") or ""
                sp = s.get("speaker")
                if sp and sp != last_speaker:
                    parts.append(f"\n{sp}: {body}")
                    last_speaker = sp
                else:
                    parts.append(body)
            out = " ".join(p for p in parts if p).strip()
            if len(out) > 200:
                return out
        except Exception:
            pass
    if "html" in t:
        raw = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        raw = re.sub(r"<[^>]+>", " ", raw)
    return _strip_transcript_noise(raw)


def resolve_apple_episode_url(feed_name: str, episode_title: str) -> Optional[str]:
    """Findet die Apple-Podcasts-URL zur Episode (iTunes Search API, mit Cache).
    Fallback: Show-Seite. None wenn gar nichts gefunden."""
    import urllib.request as _ur
    import urllib.parse as _up
    state = _podcast_inbox_state_load()
    cache = state.setdefault("apple_ids", {})
    coll = cache.get(feed_name)
    try:
        if not coll:
            q = _up.quote(feed_name[:60])
            with _ur.urlopen(f"https://itunes.apple.com/search?media=podcast&country=DE&limit=3&term={q}", timeout=10) as r:
                res = json.loads(r.read().decode())
            hits = res.get("results") or []
            if not hits:
                return None
            coll = {"id": hits[0].get("collectionId"), "url": hits[0].get("collectionViewUrl")}
            cache[feed_name] = coll
            _podcast_inbox_state_save(state)
        # Episoden nachschlagen und per Titel matchen
        with _ur.urlopen(f"https://itunes.apple.com/lookup?id={coll['id']}&entity=podcastEpisode&limit=40&country=DE", timeout=10) as r:
            res = json.loads(r.read().decode())
        want = re.sub(r"\W+", " ", episode_title.lower()).strip()[:70]
        best_url = None
        for e in (res.get("results") or []):
            if e.get("kind") != "podcast-episode":
                continue
            have = re.sub(r"\W+", " ", str(e.get("trackName", "")).lower()).strip()
            if want[:40] in have or have[:40] in want:
                best_url = e.get("trackViewUrl")
                break
        return best_url or coll.get("url")
    except Exception:
        return (coll or {}).get("url")


def open_in_apple_podcasts(url: str) -> bool:
    """Öffnet die Episode/Show direkt in der Apple-Podcasts-App."""
    try:
        import subprocess as _sp
        _sp.run(["open", "-a", "Podcasts", url], timeout=10, check=False)
        return True
    except Exception:
        return False


def apple_container_accessible() -> bool:
    """Kann DIESER Prozess Apples Podcast-Transkript-Cache lesen? Der launchd-Dienst
    darf das ohne Vollen Festplattenzugriff NICHT (TCC, verifiziert 03.07. per
    launchd-Probe: Operation not permitted) — Claude-Kontext dagegen schon."""
    try:
        os.listdir(APPLE_PODCAST_TTML_DIR)
        return True
    except Exception:
        return False


def list_unimported_ttml(max_age_h: int = 48) -> List[str]:
    """Alle von Apple Podcasts gecachten Transkripte, die noch NICHT importiert
    wurden (Nachlese: Florian hat Transkripte angesehen, während die App nicht
    zugehört hat). Neueste zuerst."""
    import glob as _glob
    state = _podcast_inbox_state_load()
    imported = set(state.get("imported_ttml", {}).keys())
    cutoff = time.time() - max_age_h * 3600
    out = []
    for p in _glob.glob(os.path.join(APPLE_PODCAST_TTML_DIR, "**", "*.ttml"), recursive=True):
        try:
            st_ = os.stat(p)
            if p not in imported and st_.st_mtime > cutoff and st_.st_size > 20_000:
                out.append((st_.st_mtime, p))
        except Exception:
            continue
    out.sort(reverse=True)
    return [p for _, p in out]


def _looks_english(text: str) -> bool:
    t = f" {(text or '').lower()} "
    en = sum(w in t for w in (" the ", " and ", " of ", " to ", " with ", " how ", " why ",
                              " what ", " is ", " for ", " on ", " a ", " in the ", " you "))
    de = sum(w in t for w in (" der ", " die ", " das ", " und ", " mit ", " für ", " ist ",
                              " von ", " im ", " wie ", " warum ", " ein ", " eine ", " nicht "))
    return en >= 2 and en > de


def attach_inbox_translations(episodes: List[dict], cli_path: Optional[str] = None) -> int:
    """Übersetzt englische Episoden-Titel/Beschreibungen für die Inbox-Anzeige (haiku, EIN
    Batch-Call), cached dauerhaft pro guid im State-File. Original bleibt in title/desc —
    Anzeige nutzt title_de/desc_de. Returns Anzahl frisch übersetzter Folgen."""
    state = _podcast_inbox_state_load()
    cache = state.setdefault("translations", {})
    need = []
    for e in episodes:
        g = e.get("guid")
        if not g:
            continue
        if g in cache:
            e["title_de"] = cache[g].get("title_de") or None
            e["desc_de"] = cache[g].get("desc_de") or None
            continue
        if _looks_english(f"{e.get('title', '')} {e.get('desc', '')}"):
            need.append(e)
    if not need:
        return 0
    cli = cli_path or _locate_claude_cli()
    if not cli:
        return 0
    need = need[:120]
    payload = ("Übersetze Titel und Beschreibung dieser Podcast-Episoden idiomatisch ins Deutsche. "
               "Eigennamen, Podcast- und Produktnamen im Original lassen. Keine Anführungszeichen ergänzen.\n"
               "ANTWORT NUR ALS JSON: {\"items\": [{\"i\": 0, \"title_de\": \"…\", \"desc_de\": \"…\"}]}\n\n"
               + json.dumps([{"i": i, "title": e.get("title", ""), "desc": e.get("desc", "")}
                             for i, e in enumerate(need)], ensure_ascii=False))
    try:
        sr = _run_claude_cli_subprocess_streaming(
            [cli, "--print", "--output-format", "text", "--model", "haiku",
             "--dangerously-skip-permissions", "--effort", "low",
             "--append-system-prompt", "Antworte ausschließlich mit dem JSON-Objekt."],
            payload, timeout_seconds=240, expected_duration_s=45.0, label="Titel-Übersetzung")
        m = re.search(r"\{.*\}", (sr.get("stdout") or ""), re.DOTALL)
        data = json.loads(m.group(0)) if (sr.get("ok") and m) else {}
    except Exception as exc:
        print(f"[übersetzung] fehlgeschlagen: {exc}", file=sys.stderr)
        return 0
    n = 0
    for it in (data.get("items") or []):
        try:
            e = need[int(it["i"])]
        except Exception:
            continue
        t_de = str(it.get("title_de") or "").strip()[:200] or None
        d_de = str(it.get("desc_de") or "").strip()[:260] or None
        if t_de or d_de:
            e["title_de"], e["desc_de"] = t_de, d_de
            cache[e["guid"]] = {"title_de": t_de, "desc_de": d_de}
            n += 1
    if n:
        _podcast_inbox_state_save(state)
        print(f"[übersetzung] {n} englische Folge(n) für die Anzeige übersetzt.", file=sys.stderr)
    return n


def podcast_listen_list_add(ep: dict) -> None:
    """Merkt eine Folge fürs spätere Anhören (Pocket Casts) — persistent."""
    state = _podcast_inbox_state_load()
    ll = state.setdefault("listen_list", {})
    ll[ep["guid"]] = {"title": ep.get("title", "?"), "feed": ep.get("feed", "?"),
                      "published": ep.get("published", ""), "added": get_berlin_now().isoformat()}
    _podcast_inbox_state_save(state)


def podcast_listen_list() -> List[dict]:
    state = _podcast_inbox_state_load()
    out = [{"guid": g, **v} for g, v in (state.get("listen_list") or {}).items()]
    out.sort(key=lambda e: e.get("added", ""), reverse=True)
    return out


def podcast_listen_list_remove(guids: List[str]) -> None:
    state = _podcast_inbox_state_load()
    ll = state.get("listen_list") or {}
    for g in guids:
        ll.pop(g, None)
    state["listen_list"] = ll
    _podcast_inbox_state_save(state)


TOPIC_HISTORY_PATH = os.getenv("BRIEFING_TOPIC_HISTORY_PATH") or os.path.expanduser("~/.briefing_topic_history.json")


def _topic_history_load() -> list:
    try:
        with open(TOPIC_HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _topic_history_append(titles: List[str]) -> None:
    """Merkt die heutigen Briefing-Titel (rollend, 10 Tage) — Basis der Wiederholungs-Erkennung."""
    try:
        hist = _topic_history_load()
        today = get_berlin_now().strftime("%Y-%m-%d")
        entry = next((h for h in hist if h.get("date") == today), None)
        if entry is None:
            entry = {"date": today, "titles": []}
            hist.append(entry)
        seen = set(entry["titles"])
        entry["titles"] += [t for t in titles if t and t not in seen][:80]
        cutoff = (get_berlin_now() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        hist = [h for h in hist if h.get("date", "") >= cutoff]
        with open(TOPIC_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False)
    except Exception as exc:
        print(f"[themen-historie] {exc}", file=sys.stderr)


def _recent_topic_history_block(days: int = 5, cap: int = 45) -> str:
    """Baut den ZULETZT-BEHANDELT-Block für Prompts (leer wenn keine Historie)."""
    hist = _topic_history_load()
    cutoff = (get_berlin_now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    lines = []
    for h in sorted(hist, key=lambda x: x.get("date", ""), reverse=True):
        if h.get("date", "") < cutoff:
            continue
        for t in h.get("titles", []):
            lines.append(f"- [{h['date']}] {t}")
    if not lines:
        return ""
    return ("\n\nZULETZT BEHANDELT (Briefings der letzten Tage):\n" + "\n".join(lines[:cap])
            + "\nNutze diese Liste als BEWUSSTSEIN, nicht als Verbot: Wurde etwas schon behandelt, "
              "mach es kenntlich (z.B. wie neulich im Briefing erwähnt) und knüpfe daran an, statt bei Null zu beginnen. "
              "Wichtiges oder Komplexes darfst du trotzdem noch einmal erklären — setze Erinnerung nie voraus, "
              "der Hörer hat vieles davon vergessen. Ziel ist ein stimmiger, effizienter Beitrag: "
              "Bekanntes kurz auffrischen, Neues vertiefen, nichts wortgleich wiederholen.")


def split_special_topics(text: str) -> List[str]:
    """Sonderthemen-Feld aufteilen: mmm/---/===== trennen Themen (mehrzeilig erlaubt,
    wie in den anderen Feldern gewohnt); ohne Trenner gilt jede Zeile als eigenes Thema."""
    text = (text or "").strip()
    if not text:
        return []
    blocks = re.split(r"\n\s*(?:[mM]mm+|-{3,}|={3,})\s*(?:\n|$)", text)
    if len(blocks) > 1:
        out = [" ".join(b.split()) for b in blocks]
    else:
        out = [l.strip() for l in text.splitlines()]
    return [t[:500] for t in out if t.strip()]


_MISSING_TOPICS_PROMPT = """Du bist Nachrichten-Redakteur für ein persönliches deutsches Audio-Briefing.

AUFGABE Recherchiere im Netz (3-5 gezielte Suchen, seriöse Quellen), was HEUTE die wichtigsten Nachrichten-Themen sind: Weltgeschehen, Deutschland, Baden-Württemberg/Region Tübingen — plus die erkennbaren Interessensfelder des Hörers (aus den Listen unten ablesbar, z.B. Tech/KI). Vergleiche mit BEIDEM — was der Hörer in den letzten Tagen bereits im Briefing hatte UND was er für das heutige Briefing schon eingesammelt hat:
{history}
{staged}

ERGEBNIS Nenne die 3-6 WICHTIGSTEN Themen, die in KEINER der beiden Listen (auch nicht im heute Eingesammelten) vorkommen oder nur am Rand — Dinge mit echter Tragweite, kein Promi-Klatsch, nichts Kleinteiliges. Für jedes: ein prägnanter Sonderthema-Vorschlag (als recherchierbare Frage oder Stichwort formuliert) plus EIN Satz, warum es gerade relevant ist. Fehlt nichts Wesentliches, gib eine leere Liste zurück — lieber ehrlich leer als künstlich gefüllt.

ANTWORT NUR ALS JSON:
{{"missing": [{{"topic": "…", "why": "…"}}]}}"""


def suggest_missing_topics_via_cli(cli_path: Optional[str] = None, timeout_seconds: int = 300,
                                   staged_lines: Optional[List[str]] = None) -> dict:
    """🌍 Weltlage-Check: Was ist gerade wichtig, fehlt aber in den letzten Briefings?
    staged_lines: heute bereits eingesammelte Quellen (Links/Titel) — zählen als abgedeckt,
    damit der Check nichts vorschlägt, was schon im heutigen Briefing landet."""
    cli = cli_path or _locate_claude_cli()
    if not cli:
        return {"ok": False, "error": "Claude CLI nicht gefunden.", "topics": []}
    hist = _recent_topic_history_block(days=4, cap=60) or "\n(keine Historie vorhanden)"
    staged = ""
    if staged_lines:
        staged = ("\nBEREITS FÜR HEUTE EINGESAMMELT (geht ins heutige Briefing — zählt als abgedeckt; "
                  "Links anhand ihres Sprech-Pfads deuten):\n"
                  + "\n".join(f"- {l[:200]}" for l in staged_lines[:80]))
    payload = _MISSING_TOPICS_PROMPT.format(history=hist, staged=staged)
    cmd = [cli, "--print", "--output-format", "text", "--model", _CLI_JUDGE_MODEL,
           "--dangerously-skip-permissions", "--effort", "medium",
           "--append-system-prompt", "Antworte ausschließlich mit dem JSON-Objekt."]
    try:
        sr = _run_claude_cli_subprocess_streaming(cmd, payload, timeout_seconds=timeout_seconds,
                                                  expected_duration_s=90.0, label="Weltlage-Check")
        raw = (sr.get("stdout") or "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not (sr.get("ok") and m):
            return {"ok": False, "error": (sr.get("error") or raw[:200] or "leere Antwort"), "topics": []}
        data = json.loads(m.group(0))
        topics = [{"topic": str(t.get("topic") or "")[:300], "why": str(t.get("why") or "")[:300]}
                  for t in (data.get("missing") or []) if t.get("topic")]
        return {"ok": True, "error": None, "topics": topics}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "topics": []}


def append_special_topic(existing: str, topic: str) -> str:
    """Hängt ein Thema modus-gerecht an die Sonderthemen-Box: nutzt das Feld schon
    mmm-Trenner, kommt es als mmm-Block; sonst als neue Zeile (Zeilen-Modus)."""
    ex = (existing or "").rstrip()
    t = (topic or "").strip()
    if not t:
        return ex
    if not ex:
        return t
    if re.search(r"(?:^|\n)\s*(?:[mM]mm+|-{3,}|={3,})\s*(?:\n|$)", existing or ""):
        return f"{ex}\n\nmmm\n\n{t}"
    return f"{ex}\n{t}"


_SPECIAL_TOPIC_PROMPT = """Du schreibst EINEN zusätzlichen Vorlesebeitrag für ein persönliches deutsches Audio-Briefing — zu einem Thema, das der Hörer selbst eingeworfen hat.

THEMA/FRAGE: {topic}

VORGEHEN Recherchiere GEZIELT im Netz (1-3 Suchen, nur seriöse Quellen: Agenturen, Öffentlich-Rechtliche, Qualitätsmedien, Primärquellen). Liefert die Suche nichts Brauchbares, antworte aus gesichertem Wissen und kennzeichne Unsicheres ehrlich.

EHRLICHKEIT Ist die Frage eine Zuschreibungs- oder Faktenfrage (Hat X wirklich gesagt/getan …?), beantworte sie ehrlich — auch wenn die Antwort Nein oder So nicht belegt ist. Falsche Zuschreibungen klar benennen und wenn möglich die echte Quelle nennen. Nichts erfinden, nichts gefällig bestätigen.

FORM Beginne mit ### und einem prägnanten Titel (kein Clickbait). Danach Fließtext: hörbar, klar, lebendig, KEINE Bullet Points, keine Zwischenüberschriften. LÄNGE der Substanz angemessen: 120-350 Wörter — dünne Faktenlage kurz halten statt aufblasen. Durchgehend Deutsch (Eigennamen original). Web-Fakten mit kurzer Quellenangabe im Fluss (laut Reuters, …). Gib NUR den Beitrag aus — keine Vorrede, kein Kommentar."""


def podcast_inbox_selection_save(guids: List[str]) -> None:
    """Persistiert die aktuell angehakten Episoden — Auswahl überlebt Reload/Neustart."""
    state = _podcast_inbox_state_load()
    state["selected_guids"] = list(guids)
    _podcast_inbox_state_save(state)


def podcast_inbox_selection_load() -> List[str]:
    return list(_podcast_inbox_state_load().get("selected_guids") or [])


def podcast_inbox_cache_save(data: dict) -> None:
    """Merkt das letzte Fetch-Ergebnis auf Platte — die Inbox überlebt so
    Browserwechsel/Neustarts, bis ein neuer Fetch sie ersetzt."""
    try:
        state = _podcast_inbox_state_load()
        state["inbox_cache"] = {
            "saved_at": get_berlin_now().isoformat(),
            "episodes": (data or {}).get("episodes") or [],
            "n_feeds": (data or {}).get("n_feeds"),
        }
        _podcast_inbox_state_save(state)
    except Exception as exc:
        print(f"[inbox-cache] Speichern fehlgeschlagen: {exc}", file=sys.stderr)


def podcast_inbox_cache_load() -> Optional[dict]:
    """Lädt das letzte Fetch-Ergebnis; erledigte (archiviert/verdichtet) Episoden
    werden anhand des aktuellen Zustands rausgefiltert."""
    try:
        state = _podcast_inbox_state_load()
        cache = state.get("inbox_cache")
        if not cache or not cache.get("episodes"):
            return None
        ep_state = state.get("episodes", {})
        eps = [e for e in cache["episodes"]
               if not ep_state.get(e.get("guid"), {}).get("archived")
               and not ep_state.get(e.get("guid"), {}).get("summarized")]
        return {"episodes": eps, "n_feeds": cache.get("n_feeds"), "errors": [],
                "cached_at": cache.get("saved_at")}
    except Exception:
        return None


def mark_ttml_imported(paths: List[str]) -> None:
    state = _podcast_inbox_state_load()
    imp = state.setdefault("imported_ttml", {})
    now_iso = get_berlin_now().isoformat()
    for p in paths:
        imp[p] = now_iso
    _podcast_inbox_state_save(state)


def wait_for_new_apple_ttml(since_ts: float, timeout_s: int = 180, poll_s: float = 3.0) -> Optional[str]:
    """Wartet, bis Apple Podcasts ein NEUES Transkript cached (mtime > since_ts).
    Für den Ein-Klick-Flow: 🍎 öffnen → Florian tippt aufs Transkript → wir schnappen
    es uns automatisch. Returns Pfad oder None (Timeout)."""
    import glob as _glob
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            for p in _glob.glob(os.path.join(APPLE_PODCAST_TTML_DIR, "**", "*.ttml"), recursive=True):
                try:
                    st_ = os.stat(p)
                    # frisch UND fertig geschrieben (2s stabil + plausible Größe)
                    if st_.st_mtime > since_ts and st_.st_size > 20_000 and (time.time() - st_.st_mtime) > 2:
                        return p
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(poll_s)
    return None


_TOPIC_CLUSTER_PROMPT = """Du bekommst nummerierte Nachrichten-Quellen (Artikel, Paywall-Texte, Podcast-Zusammenfassungen) EINES Tages. Bündle sie zu THEMEN: Alles, was inhaltlich zum selben Themenkomplex gehört (z.B. mehrere Artikel + ein Podcast zum Koalitionsausschuss), kommt in EIN Thema. Eigenständige Storys bleiben eigene Themen — im Zweifel lieber getrennt lassen.

REGELN:
- JEDE Quellen-Nummer muss in GENAU EINEM Thema vorkommen. Keine weglassen, keine doppelt.
- Ein Thema kann auch nur eine Quelle haben (der Normalfall).
- Podcasts, die viele verschiedene Themen streifen, bleiben ein EIGENES Thema.
- Gib jedem Thema einen prägnanten deutschen Titel und ein Gewicht von 1 (Randnotiz) bis 5 (Topthema des Tages).
- Vergib die Gewichte STRENG: höchstens 1-2 Themen bekommen eine 5, die Masse liegt bei 2-3. Das Gewicht steuert später die Beitragslänge.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, keine Vorrede:
{"topics": [{"title": "Koalitionsausschuss einigt sich auf Haushalt", "members": [3, 7, 12], "weight": 5}, {"title": "...", "members": [1], "weight": 2}]}"""


_TOPIC_SYNTH_PROMPT = """Du schreibst EINEN Beitrag für ein persönliches Audio-Briefing (wird vorgelesen). Unten stehen alle Quellen zu EINEM Thema: {topic_title}. Verwebe sie zu EINEM durchgehenden, perfekten Vorlesetext — der Hörer soll danach vollständig informiert sein, ohne irgendetwas doppelt zu hören.

SO GEHST DU VOR:
- Integriere ALLE relevanten Fakten, Zahlen, Namen und Einschätzungen aus ALLEN Quellen. Nichts Wichtiges darf verloren gehen.
- Überschneiden sich Quellen, erzähle es EINMAL — ergänzt um die einzigartigen Details jeder Quelle.
- Nenne Quellen natürlich im Fließtext, wo es Mehrwert hat (zum Beispiel: laut SWR, wie die tagesschau berichtet, im Podcast Lage der Nation heißt es). Nicht bei jedem Satz.
- Widersprechen sich Quellen, benenne das explizit und fair.
- Unterscheide belegte Fakten von Einschätzungen und Meinungen.
- Nichts erfinden, nichts aus eigenem Wissen ergänzen.
- Hohe Informationsdichte, aktive Sprache, roter Faden mit Spannungsbogen.

FORMAT (exakt einhalten):
- Erste Zeile: ### plus prägnanter Titel
- Dann eine Zeile mit einem *kursiven Einordnungssatz* (mit Sternchen).
- Dann der Fließtext: {word_min} bis {word_max} Wörter. NUR Fließtext-Absätze, KEINE Listen, KEINE Aufzählungszeichen, KEINE Zwischenüberschriften.
- Dann: #### Was bleibt:
- Dann EIN prägnanter Merksatz (Konsequenz/Bedeutung, kein Inhaltsreferat).
- Letzte Zeile exakt: Weiter geht's.

Schreibe auf Deutsch. Zahlen und Abkürzungen aussprechbar (vorlesetauglich)."""


_SYNTH_WEB_ENRICH = """
WEB-ERGÄNZUNG (aktiviert): Wenn den gelieferten Quellen ein ZENTRALER Baustein für Verständnis oder Einordnung fehlt (Wer ist diese Person? Was war die Vorgeschichte? Was bedeutet dieser Fachbegriff? Eine offensichtlich fehlende Schlüsselzahl), darfst du dafür GEZIELT das WebSearch-Tool nutzen — höchstens 1-2 Suchen pro Thema. Regeln dafür:
- NUR seriöse Quellen verwerten: Nachrichtenagenturen (dpa, Reuters, AP), öffentlich-rechtliche Medien, etablierte Leitmedien, offizielle Stellen und Primärquellen.
- JEDE ergänzte Information im Text KLAR kennzeichnen, mit Quelle: zum Beispiel — Zur Einordnung, laut Reuters: … / Hintergrund, wie die dpa berichtet: …
- Die Ergänzung dient der LÜCKENFÜLLUNG, nie der Themenerweiterung. Der Kern des Beitrags bleiben die gelieferten Quellen.
- Widerspricht ein Suchergebnis den gelieferten Quellen, haben die GELIEFERTEN Quellen Vorrang — benenne den Widerspruch, entscheide ihn nicht.
- Liefert die Suche nichts Belastbares: Beitrag einfach OHNE Ergänzung schreiben. NIEMALS aus eigenem Wissen oder vage ergänzen.
"""

_SYNTH_NARRATIVE_STYLE = """
STIL — UNTERHALTSAM ERZÄHLT: Schreibe wie der Host eines exzellenten Magazin-Podcasts: Steig mit einem Hook ein (eine überraschende Zahl, eine Szene, eine Frage), erzähle mit rotem Faden und kleinen dramaturgischen Wendungen, nutze anschauliche Vergleiche und gelegentlich ein Augenzwinkern. ABER: bleibe zu 100 Prozent faktentreu, verwende AUSSCHLIESSLICH die Informationen aus den Quellen, und bei ernsten Themen (Gewalt, Tod, Katastrophen) bleibt der Ton respektvoll und nüchtern. Unterhaltsamkeit darf NIE auf Kosten der Information gehen — der Hörer will beides: gut unterhalten UND vollständig informiert sein.
"""


_SMART_WEIGHT_BUDGETS = {5: (300, 480), 4: (220, 340), 3: (150, 230), 2: (90, 140), 1: (50, 90)}
_SMART_WEIGHT_NOTES = {
    5: "Schwerpunkt des Tages — erzähle vollständig, mit Kontext und Einordnung",
    4: "wichtiges Thema — gründlich, aber ohne Ausschweifen",
    3: "solide Meldung — kompakt mit den Kernfakten",
    2: "kleinere Meldung — nur das Wesentliche in wenigen Sätzen",
    1: "Randnotiz — 2-3 Sätze genügen",
}


def _smart_topic_budget(weight: int, n_src: int) -> tuple:
    """Intelligente Länge: Wortbudget aus Tragweite (1-5) + kleinem Quellen-Bonus."""
    wmin, wmax = _SMART_WEIGHT_BUDGETS.get(int(weight or 3), _SMART_WEIGHT_BUDGETS[3])
    bonus = min(40 * (max(1, n_src) - 1), 160)
    return wmin + bonus // 2, wmax + bonus


def _synthesize_topics_from_items(items, weather_text=None, compact_mode=True, ultra_compact=False,
                                  cli_path=None, progress_callback=None, timeout_seconds=600,
                                  narrative_style=False, web_enrich=False, smart_length=False):
    """Themen-Synthese: bündelt alle Quellen thematisch (Opus) und schreibt pro Thema
    EINEN verwobenen Vorlesetext (Opus, parallel max 2). Vollständigkeits-Garantie:
    jede Quelle landet in genau einem Thema (Nachzügler werden als Einzelthemen ergänzt).

    Returns: (sections, failed_topics, n_topics)
    """
    def _report(msg, ratio):
        if progress_callback:
            try:
                progress_callback(msg, ratio)
            except Exception:
                pass

    cli = cli_path or _locate_claude_cli()
    if not cli or not items:
        return [], 0, 0

    def _body_core(it):
        b = (it.get("body") or "").strip()
        head, sep, rest = b.partition("\n\n")
        return rest.strip() if (sep and head.upper().startswith("QUELLE")) else b

    # 1) Themen-Clustering (ein kleiner Opus-Call über Kurzauszüge)
    _report("Themen-Synthese: Opus bündelt die Quellen zu Themen…", 0.18)
    lines = []
    for i, it in enumerate(items, 1):
        excerpt = " ".join(_body_core(it).split())[:220]
        lines.append(f"[{i}] ({it.get('label', '?')}, {it.get('kind', 'article')}) {excerpt}")
    payload = _TOPIC_CLUSTER_PROMPT + "\n\n=== QUELLEN ===\n\n" + "\n".join(lines)
    cmd = [cli, "--print", "--output-format", "text", "--model", _CLI_JUDGE_MODEL,
           "--dangerously-skip-permissions", "--effort", "low",
           "--append-system-prompt", "Antworte ausschließlich mit dem JSON-Objekt."]
    topics = []
    try:
        sr = _run_claude_cli_subprocess_streaming(cmd, payload, timeout_seconds=300,
                                                  expected_duration_s=40.0, label="Themen-Bündelung (Claude)")
        m = re.search(r"\{.*\}", (sr.get("stdout") or ""), re.DOTALL)
        data = json.loads(m.group(0)) if (sr.get("ok") and m) else {}
        for t in (data.get("topics") or []):
            members = [int(x) for x in (t.get("members") or []) if str(x).strip().isdigit() or isinstance(x, int)]
            members = [x for x in members if 1 <= x <= len(items)]
            if members:
                topics.append({"title": str(t.get("title") or "Thema")[:120],
                               "members": members, "weight": int(t.get("weight") or 3)})
    except Exception as exc:
        print(f"[synthese] Clustering fehlgeschlagen ({exc}) — jede Quelle wird eigenes Thema.", file=sys.stderr)

    # Vollständigkeit erzwingen: fehlende Quellen als Einzelthemen anhängen, Doppelte entfernen
    seen = set()
    for t in topics:
        t["members"] = [m_ for m_ in t["members"] if m_ not in seen and not seen.add(m_)]
    topics = [t for t in topics if t["members"]]
    missing = [i for i in range(1, len(items) + 1) if i not in seen]
    for i in missing:
        first = " ".join(_body_core(items[i - 1]).split())[:70]
        topics.append({"title": first or f"Quelle {i}", "members": [i], "weight": 2})
    if missing:
        print(f"[synthese] {len(missing)} Quelle(n) vom Clustering vergessen — als Einzelthemen ergänzt.", file=sys.stderr)
    topics.sort(key=lambda t: -t.get("weight", 3))
    multi = [t for t in topics if len(t["members"]) > 1]
    print(f"[synthese] {len(topics)} Themen aus {len(items)} Quellen ({len(multi)} davon gebündelt: "
          f"{[t['title'][:40] + ' <- ' + str(len(t['members'])) + ' Quellen' for t in multi[:5]]}).", file=sys.stderr)

    # 2) Pro Thema EINEN verwobenen Beitrag schreiben (Opus, parallel max 2)
    if smart_length:
        # 🧠 Intelligente Länge: Tragweite bestimmt das Budget — Unwichtiges radikal
        # kurz, Wichtiges voll erzählt. Sanfte Gesamtbremse schützt vor Monster-PDFs,
        # drosselt aber nur Gewicht ≤3 (Top-Themen bleiben unangetastet).
        for t in topics:
            t["_wmin"], t["_wmax"] = _smart_topic_budget(t.get("weight", 3), len(t["members"]))
            t["_note"] = _SMART_WEIGHT_NOTES.get(int(t.get("weight") or 3), _SMART_WEIGHT_NOTES[3])
        _est = sum((t["_wmin"] + t["_wmax"]) // 2 for t in topics)
        if _est > 8000:
            _f = max(0.65, 8000 / _est)
            for t in topics:
                if int(t.get("weight") or 3) <= 3:
                    t["_wmin"], t["_wmax"] = int(t["_wmin"] * _f), int(t["_wmax"] * _f)
            print(f"[synthese] Intelligente Länge: ~{_est} Wörter geschätzt → Gewicht ≤3 auf Faktor {_f:.2f} gedrosselt.", file=sys.stderr)
        _w_hist = {}
        for t in topics:
            _w_hist[t.get("weight", 3)] = _w_hist.get(t.get("weight", 3), 0) + 1
        print(f"[synthese] Intelligente Länge: Gewichtsverteilung {dict(sorted(_w_hist.items(), reverse=True))}, Zielumfang ~{sum((t['_wmin'] + t['_wmax']) // 2 for t in topics)} Wörter.", file=sys.stderr)
    if ultra_compact:
        base_min, base_max, per_src, cap = 90, 150, 70, 450
    elif compact_mode:
        base_min, base_max, per_src, cap = 150, 250, 110, 700
    else:
        base_min, base_max, per_src, cap = 220, 400, 150, 950

    def _write_topic(t):
        n_src = len(t["members"])
        if "_wmin" in t:
            wmin, wmax = t["_wmin"], t["_wmax"]
        else:
            wmin = base_min + (per_src // 2) * (n_src - 1)
            wmax = min(base_max + per_src * (n_src - 1), cap)
        prompt = _TOPIC_SYNTH_PROMPT.format(topic_title=t["title"], word_min=wmin, word_max=wmax)
        if "_wmin" in t:
            prompt += (f"\n\nGEWICHTUNG: Tragweite {t.get('weight', 3)}/5 — {t.get('_note', '')}. "
                       "Das Wortbudget ist ein Richtwert: bei dünner Substanz DEUTLICH unterschreiten, "
                       "bei echter Tiefe maßvoll (bis ~20%) überziehen.")
        if narrative_style:
            prompt += _SYNTH_NARRATIVE_STYLE
        if web_enrich:
            prompt += _SYNTH_WEB_ENRICH
        src_parts = []
        for m_ in t["members"]:
            it = items[m_ - 1]
            src_parts.append(f"--- QUELLE {m_} ({it.get('label', '?')}, {it.get('kind', 'article')}) ---\n\n{(it.get('body') or '')[:6500]}")
        pl = prompt + "\n\n=== QUELLEN ZU DIESEM THEMA ===\n\n" + "\n\n".join(src_parts)
        c = [cli, "--print", "--output-format", "text", "--model", _CLI_JUDGE_MODEL,
             "--dangerously-skip-permissions", "--effort", "medium"]
        for attempt in (1, 2):
            try:
                sr2 = _run_claude_cli_subprocess_streaming(
                    c, pl, timeout_seconds=timeout_seconds,
                    expected_duration_s=(200.0 if web_enrich else 120.0), label=f"Thema: {t['title'][:36]}")
                out = (sr2.get("stdout") or "").strip()
                if sr2.get("ok") and out.startswith("###") and "Was bleibt" in out:
                    return out
            except Exception:
                pass
        return None

    sections = []
    failed = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    done = [0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {pool.submit(_write_topic, t): ti for ti, t in enumerate(topics)}
        for f in as_completed(futs):
            ti = futs[f]
            try:
                results[ti] = f.result()
            except Exception:
                results[ti] = None
            done[0] += 1
            _report(f"Themen-Synthese: Beitrag {done[0]}/{len(topics)} geschrieben…",
                    0.2 + 0.5 * done[0] / max(len(topics), 1))
    for ti, t in enumerate(topics):
        md = results.get(ti)
        if not md:
            failed += 1
            print(f"[synthese] Thema fehlgeschlagen: {t['title'][:60]}", file=sys.stderr)
            continue
        labels = []
        for m_ in t["members"]:
            lab = (items[m_ - 1].get("label") or "").strip()
            if lab and lab not in labels:
                labels.append(lab)
        sections.append({"type": "article", "content": md, "source_label": " + ".join(labels[:4])})

    # 3) Wetter als eigene, deterministisch formatierte Section voranstellen
    if weather_text and str(weather_text).strip():
        sections.insert(0, {"type": "article", "_weather": True, "source_label": "DWD",
                            "content": f"### Wetter\n\n{str(weather_text).strip()}"})
    return sections, failed, len(topics)


PODCAST_SUMMARY_PROMPT = """Fasse das folgende Podcast-Transkript zusammen.

Beginne in der ersten Zeile mit **Podcastname – Episodentitel** in Fettdruck (falls erkennbar) und einer Kurzzusammenfassung in einem Satz. Stelle dann eine prägnante Leitfrage, die den Kern des Gesprächs erfasst. Fasse den Inhalt in der unten angegebenen LÄNGENVORGABE zusammen — und nutze die Spanne nach oben aus, wenn die Episode informationsdicht ist: lieber ein konkretes Detail, eine Zahl, ein Name mehr als eine Plattitüde. Beginne mit einem einleitenden Satz, der das Kernthema direkt aufgreift und die Leitfrage beantwortet. Gliedere den Text in Abschnitte mit ### Überschriften für jeden wesentlichen Aspekt. Jeder Abschnitt bietet eine kurzweilige, fesselnde Zusammenfassung des jeweiligen Punktes.

REGELN
Schreibe IMMER auf Deutsch, auch wenn die Episode englisch ist — englische Fachbegriffe darfst du beibehalten. Bevorzuge konkrete Zahlen, Namen, Daten und Beispiele aus der Episode gegenüber Allgemeinplätzen. Das Transkript ist automatisch erstellt — korrigiere offensichtliche Erkennungsfehler (z.B. falsch geschriebene Namen) stillschweigend. Ordne Kernaussagen den Sprechern zu, wenn mehrere Gesprächsteilnehmer beteiligt sind. Unterscheide klar zwischen belegten Fakten und persönlichen Einschätzungen der Sprecher. Wenn die Gesprächsteilnehmer unterschiedlicher Meinung sind, stelle beide Positionen fair gegenüber. Ignoriere Smalltalk, Werbung, Wiederholungen und Nebengespräche. Unklares oder nur angedeutetes Wissen weglassen — nichts hinzuerfinden. Nutze aktive Sprache, konkrete statt abstrakte Formulierungen und baue Spannungsbögen auf. Hebe Schlüsselbegriffe in Fettdruck hervor. Vergleiche und Analogien nur zur Veranschaulichung — keine erfundenen Beispiele.

FORMATIERUNG
NUR Fließtext und ### Überschriften. KEINE Aufzählungszeichen, Bullets, Spiegelstriche oder Listen jeglicher Art (kein -, *, kein 1., 2., 3.). KEINE horizontalen Trennlinien. Alles was als Liste dargestellt werden könnte, stattdessen als Fließtext mit Kommas oder Semikolons formulieren. Schließe mit einem Keytakeaway in Fettdruck, das den Mehrwert der Episode auf den Punkt bringt. Beende die Zusammenfassung IMMER mit einer leeren Zeile und dann exakt: Ende der Podcastzusammenfassung"""


def _strip_transcript_noise(text: str) -> str:
    """Entfernt SRT/VTT-Zeitstempel, Cue-Nummern und typische Transkript-Artefakte —
    spart je nach Format 50-75% Tokens, ohne Inhalt zu verlieren."""
    t = text or ""
    t = re.sub(r"^WEBVTT.*?\n", "", t, flags=re.IGNORECASE)
    # SRT/VTT-Zeitzeilen: 00:01:23,456 --> 00:01:25,000 (auch mit Punkt)
    t = re.sub(r"^\s*\d{1,2}:\d{2}(:\d{2})?[.,]\d{3}\s*-->\s*\d{1,2}:\d{2}(:\d{2})?[.,]\d{3}.*$", "", t, flags=re.MULTILINE)
    # Reine Cue-Nummern-Zeilen
    t = re.sub(r"^\s*\d{1,5}\s*$", "", t, flags=re.MULTILINE)
    # Inline-Zeitstempel wie [00:12:34] oder (12:34)
    t = re.sub(r"[\[(]\d{1,2}:\d{2}(:\d{2})?[\])]", "", t)
    # Geräusch-Marker
    t = re.sub(r"\[(?:Musik|Music|Applaus|Applause|Lachen|Laughter|Intro|Outro)\]", "", t, flags=re.IGNORECASE)
    # Leerzeilen-Orgien eindampfen
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def summarize_podcast_transcript_via_cli(transcript: str, cli_path: Optional[str] = None,
                                         model: str = "opus", timeout_seconds: int = 900) -> dict:
    """Verdichtet EIN rohes Podcast-Transkript zur Briefing-tauglichen Zusammenfassung
    (Florians Podcast-Prompt, endet garantiert mit dem Endmarker). Läuft übers Max-Abo.

    Returns: {"ok": bool, "summary": str, "error": str|None, "elapsed_seconds": float}
    """
    t0 = time.time()
    cli = cli_path or _locate_claude_cli()
    if not cli:
        return {"ok": False, "summary": "", "error": "Claude CLI nicht gefunden.", "elapsed_seconds": 0.0}
    cleaned = _strip_transcript_noise(transcript)
    if len(cleaned) < 500:
        return {"ok": False, "summary": "", "error": "Transkript zu kurz (unter 500 Zeichen).", "elapsed_seconds": 0.0}
    truncated_note = ""
    if len(cleaned) > 700_000:  # ~9-10 Stunden Sprechtext — Sicherheitsdeckel (Opus: 1M Kontext)
        cleaned = cleaned[:700_000]
        truncated_note = " (Transkript war extrem lang und wurde am Ende gekappt)"
    # Längenvorgabe skaliert mit dem Episodenumfang (~10-15k Zeichen ≈ 1 Sprechstunde):
    # kurze Wissenshäppchen bleiben knackig, ein 3-Stunden-Gespräch bekommt Raum.
    _n_chars = len(cleaned)
    if _n_chars < 25_000:
        _wspan = "300-500"
    elif _n_chars < 80_000:
        _wspan = "500-800"
    elif _n_chars < 180_000:
        _wspan = "800-1200"
    else:
        _wspan = "1200-1600"
    payload = (PODCAST_SUMMARY_PROMPT
               + f"\n\nLÄNGENVORGABE FÜR DIESE EPISODE: {_wspan} Wörter."
               + "\n\n=== TRANSKRIPT ===\n\n" + cleaned)
    cmd = [
        cli, "--print", "--output-format", "text", "--model", model,
        "--dangerously-skip-permissions", "--effort", "medium",
    ]
    try:
        sr = _run_claude_cli_subprocess_streaming(
            cmd, payload, timeout_seconds=timeout_seconds,
            expected_duration_s=150.0, label="Podcast-Zusammenfassung (Claude)",
        )
    except Exception as exc:
        return {"ok": False, "summary": "", "error": str(exc), "elapsed_seconds": time.time() - t0}
    if not sr.get("ok") or sr.get("returncode") != 0:
        err = (sr.get("stdout") or sr.get("stderr") or "CLI-Aufruf fehlgeschlagen").strip()[:200]
        err = _friendly_cli_login_error(err, cli) or err
        return {"ok": False, "summary": "", "error": err, "elapsed_seconds": time.time() - t0}
    summary = (sr.get("stdout") or "").strip()
    if len(summary) < 200:
        return {"ok": False, "summary": "", "error": f"Zusammenfassung verdächtig kurz: {summary[:120]}", "elapsed_seconds": time.time() - t0}
    if "Ende der Podcastzusammenfassung" not in summary:
        summary += "\n\nEnde der Podcastzusammenfassung"
    if truncated_note:
        summary = summary.replace("Ende der Podcastzusammenfassung", f"{truncated_note}\n\nEnde der Podcastzusammenfassung", 1)
    return {"ok": True, "summary": summary, "error": None, "elapsed_seconds": time.time() - t0}


_RAW_TRANSCRIPT_MIN_CHARS = 4000  # ohne Endmarker + länger als das = rohes Transkript


def preprocess_podcast_text(podcast_text: str, cli_path: Optional[str] = None,
                            progress_callback: Optional[Callable] = None) -> tuple:
    """Erkennt ROHE Transkripte im Podcast-Feld (kein Endmarker + lang) und verdichtet
    sie automatisch — fertige Zusammenfassungen bleiben unangetastet. Max. 2 parallel.

    Returns: (neuer_podcast_text, anzahl_verdichtet, fehler_liste)
    """
    if not podcast_text or not podcast_text.strip():
        return podcast_text, 0, []
    # Gleiche Trenner wie split_podcast_summaries (mmm / Artikel Ende, NICHT ---)
    normalized = re.sub(r"(?i)\bartikel ende\b|m{3,}", "\n<<<BRIEFING_SPLIT>>>\n", podcast_text)
    blocks = [b.strip() for b in normalized.split("<<<BRIEFING_SPLIT>>>") if b.strip()]
    raw_idx = [i for i, b in enumerate(blocks)
               if "Ende der Podcastzusammenfassung" not in b and len(b) >= _RAW_TRANSCRIPT_MIN_CHARS]
    if not raw_idx:
        return podcast_text, 0, []

    def _report(msg):
        if progress_callback:
            try:
                progress_callback(msg, 0.05)
            except Exception:
                pass

    print(f"[podcast-raw] {len(raw_idx)} rohes/rohe Transkript(e) erkannt — werden verdichtet…", file=sys.stderr)
    errors = []
    done_count = [0]
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {pool.submit(summarize_podcast_transcript_via_cli, blocks[i], cli_path): i for i in raw_idx}
        for f in as_completed(futs):
            i = futs[f]
            try:
                r = f.result()
            except Exception as exc:
                r = {"ok": False, "error": str(exc), "summary": ""}
            results[i] = r
            done_count[0] += 1
            _report(f"Podcast-Transkript {done_count[0]}/{len(raw_idx)} verdichtet…")
    n_ok = 0
    for i in raw_idx:
        r = results.get(i) or {}
        if r.get("ok"):
            blocks[i] = r["summary"]
            n_ok += 1
        else:
            first_line = blocks[i].splitlines()[0][:60] if blocks[i].splitlines() else "?"
            errors.append(f"{first_line}: {r.get('error', 'unbekannt')}")
            print(f"[podcast-raw] Verdichtung fehlgeschlagen ({r.get('error', '?')}) — Block bleibt roh.", file=sys.stderr)
    new_text = "\n\nmmm\n\n".join(blocks)
    if n_ok:
        print(f"[podcast-raw] {n_ok} Transkript(e) erfolgreich verdichtet.", file=sys.stderr)
    return new_text, n_ok, errors


_RESSORT_CLASSIFY_PROMPT = """Ordne jede nummerierte Nachrichten-Überschrift GENAU EINEM Ressort zu.

Ressorts (nur diese Schlüssel verwenden):
- regional: Tübingen, Reutlingen, Stuttgart, Baden-Württemberg, Südwesten, lokale Themen
- politik: Politik, Wahlen, Regierung, International, Krieg, Diplomatie, Gesellschaftspolitik
- wirtschaft: Unternehmen, Börse, Arbeitsmarkt, Preise, Handel, Verbraucher
- tech: Technologie, KI, Software, Internet, Wissenschaft, Forschung, Raumfahrt, Medizin-Forschung
- gericht: Gerichtsprozesse, Urteile, Ermittlungen, Kriminalität mit juristischem Fokus
- sonstige: Sport, Kultur, Vermischtes, Kurioses, Promi, alles was nirgends klar passt

WICHTIG: Eine LOKALE Story (Ort in Baden-Württemberg/Region Tübingen) gehört IMMER zu regional, auch wenn sie thematisch z.B. nach Wirtschaft oder Gericht klingt — Ausnahme: der Gerichts-/Prozessaspekt steht klar im Zentrum, dann gericht.

QUELLEN-HINWEIS: Vor jeder Überschrift steht in eckigen Klammern die Quelle. Nutze sie als starkes Signal: Schwäbisches Tagblatt, GEA und andere Lokalzeitungen aus dem Raum Tübingen/Reutlingen berichten fast immer regional. SWR berichtet überwiegend, aber nicht nur, aus dem Südwesten. tagesschau, BBC, FAZ, n-tv usw. sind überregional. Die Quelle ergänzt den Titel, ersetzt ihn nicht — eine dpa-Weltmeldung im Tagblatt bleibt überregional.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, keine Vorrede:
{"1": "regional", "2": "politik", ...}"""


def _classify_sections_via_cli(titles_by_idx, cli_path=None, timeout_seconds=150):
    """Lässt Claude alle Beitrags-Titel in einem Call den Ressorts zuordnen.

    Intelligente Alternative zur Keyword-Heuristik (die z.B. lokale Storys ohne
    Orts-Keyword im Titel verfehlt). Returns {idx: bucket} — nur valide Einträge;
    bei Fehler/keinem CLI: {} (Caller fällt pro Section auf die Heuristik zurück).
    """
    cli = cli_path or _locate_claude_cli()
    if not cli or not titles_by_idx:
        return {}
    valid_buckets = {"regional", "politik", "wirtschaft", "tech", "gericht", "sonstige"}
    lines = [f"{idx}. {title[:140]}" for idx, title in sorted(titles_by_idx.items())]
    payload = _RESSORT_CLASSIFY_PROMPT + "\n\n=== ÜBERSCHRIFTEN ===\n\n" + "\n".join(lines)
    cmd = [
        cli, "--print", "--output-format", "text", "--model", _CLI_JUDGE_MODEL,
        "--dangerously-skip-permissions", "--effort", "low",
        "--append-system-prompt", "Antworte ausschließlich mit dem JSON-Objekt, ohne Vorrede oder Erklärung.",
    ]
    try:
        sr = _run_claude_cli_subprocess_streaming(
            cmd, payload, timeout_seconds=timeout_seconds,
            expected_duration_s=25.0, label="Ressort-Zuordnung (Claude)",
        )
    except Exception:
        return {}
    if not sr.get("ok") or sr.get("returncode") != 0:
        return {}
    raw = (sr.get("stdout") or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        bucket = str(v).strip().lower()
        if idx in titles_by_idx and bucket in valid_buckets:
            out[idx] = bucket
    return out


def _drop_condensed_duplicates(sections, cli_path=None):
    """Sicherheitsnetz NACH der Kondensierung: Auf den fertigen Kurztexten ist die
    Dubletten-Erkennung nachweislich am treffsichersten (Diagnose 30.06. — auf
    Rohtexten verwässert outlet-eigenes Vokabular den Jaccard). Findet die Mathe
    hier ein Paar, entscheidet Opus streng; bei bestätigter Dublette bleibt der
    LÄNGERE Beitrag, die Quellen-Labels werden kombiniert. Wetter/Podcasts sind
    ausgenommen. Returns (sections, dropped_notes).
    """
    eligible = []
    for i, s in enumerate(sections):
        if s.get("_weather"):
            continue
        content = s.get("content", "") or ""
        if "Ende der Podcastzusammenfassung" in content or s.get("type") == "podcast":
            continue
        if len(content.strip()) < 120:
            continue
        eligible.append(i)
    if len(eligible) < 2:
        return sections, []

    stems = {}
    for i in eligible:
        toks = _topic_similarity_tokens((sections[i].get("content") or "")[:1200])
        stems[i] = {_stem_for_cluster(t) for t in toks if len(t) >= 5}

    pairs = []
    for a_pos in range(len(eligible)):
        i = eligible[a_pos]
        if len(stems[i]) < 5:
            continue
        for j in eligible[a_pos + 1:]:
            if len(stems[j]) < 5:
                continue
            ov = len(stems[i] & stems[j])
            un = len(stems[i] | stems[j]) or 1
            if ov >= 4 and (ov / un) >= 0.14:
                pairs.append((i, j))
    if not pairs:
        return sections, []

    print(f"[post-dedup] {len(pairs)} Kandidaten-Paar(e) auf kondensierten Texten — Opus prüft…", file=sys.stderr)
    clusters_payload = [
        {"members": [
            {"index": i, "url": "", "source_label": sections[i].get("source_label", "?"),
             "excerpt": (sections[i].get("content") or "")[:900]}
            for i in (a, b)
        ]}
        for a, b in pairs
    ]
    try:
        verdict = llm_confirm_duplicate_clusters(clusters_payload, model=_CLI_JUDGE_MODEL, cli_path=cli_path)
    except Exception:
        verdict = {}
    if not verdict:
        return sections, []

    to_drop = set()
    notes = []
    for ci, (a, b) in enumerate(pairs, 1):
        v = verdict.get(ci) or {}
        if not v.get("is_duplicate"):
            continue
        len_a = len(sections[a].get("content") or "")
        len_b = len(sections[b].get("content") or "")
        keep, drop = (a, b) if len_a >= len_b else (b, a)
        if drop in to_drop or keep in to_drop:
            continue
        labels = []
        for x in (keep, drop):
            lab = (sections[x].get("source_label") or "").strip()
            if lab and lab not in labels:
                labels.append(lab)
        if labels:
            sections[keep]["source_label"] = " + ".join(labels)
        to_drop.add(drop)
        notes.append(f"{' + '.join(labels) or '?'}: {str(v.get('reason', ''))[:100]}")

    if not to_drop:
        print("[post-dedup] Opus: keine echte Dublette — nichts entfernt.", file=sys.stderr)
        return sections, []
    new_sections = [s for i, s in enumerate(sections) if i not in to_drop]
    print(f"[post-dedup] {len(to_drop)} doppelte(r) Beitrag/Beiträge entfernt: {notes}", file=sys.stderr)
    return new_sections, notes


def _collect_briefing_raw_items(urls_text, paywall_text, podcast_text, include_weather, progress_callback=None):
    """Sammelt alle Rohdaten als flache Item-Liste für den chunked CLI-Pfad.

    Returns: (weather_text_or_None, items) wobei items = Liste von dicts:
      {"kind": "article"|"paywall"|"podcast", "label": str, "body": str}
    Reihenfolge: Artikel (Eingabe-Reihenfolge) → Paywall → Podcasts.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _report(step, ratio):
        if progress_callback:
            try:
                progress_callback(step, max(0.0, min(1.0, ratio)))
            except Exception:
                pass

    weather_text = None
    if include_weather:
        _report("Wetterbericht wird geladen…", 0.02)
        try:
            weather_text = fetch_weather() or None
        except Exception:
            weather_text = None

    items = []
    weak_fetches = []  # URLs, die kaum/keinen Inhalt lieferten (Fetch fehlgeschlagen / Cookie-Wall)

    valid_urls, _, _ = _extract_article_urls_internal(urls_text or "")
    if valid_urls:
        payload_map = {}
        with ThreadPoolExecutor(max_workers=_article_fetch_workers(len(valid_urls))) as pool:
            fut = {pool.submit(_fetch_article_payload, u): u for u in valid_urls}
            done = 0
            for f in as_completed(fut):
                done += 1
                _report(f"Artikel geladen ({done}/{len(valid_urls)})…", 0.03 + 0.12 * done / max(len(valid_urls), 1))
                try:
                    p = f.result()
                    if p:
                        payload_map[fut[f]] = p
                except Exception:
                    pass
        for url in valid_urls:
            p = payload_map.get(url)
            if not p:
                weak_fetches.append({"url": url, "chars": 0, "preview": "(kein Inhalt geladen)"})
                print(f"[fetch-weak] Fetch fehlgeschlagen: {url}", file=sys.stderr)
                continue
            text = p.get("source_text", "") or ""
            # Diagnose: NUR melden, wenn quasi nichts kam (Fetch fehlgeschlagen / Cookie-Wall).
            # Konservativ bei 180 Zeichen — ein echter Artikel-Body ist praktisch immer länger,
            # also (fast) keine Fehlalarme bei legitim kurzen Meldungen.
            _clean = text.strip()
            if len(_clean) < 180:
                weak_fetches.append({"url": url, "chars": len(_clean), "preview": _clean[:80].replace("\n", " ")})
                print(f"[fetch-weak] kaum Inhalt ({len(_clean)} Z.): {url} — '{_clean[:60]}'", file=sys.stderr)
            if len(text) > 6000:
                text = text[:6000] + "\n\n[…gekürzt…]"
            items.append({
                "kind": "article",
                "label": p.get("source_label", "unbekannt"),
                "body": f"QUELLE: {p.get('source_label', 'unbekannt')}\nURL: {url}\n\n{text}",
            })

    paywall_chunks = split_paywall_articles(paywall_text or "") if (paywall_text and paywall_text.strip()) else []
    for chunk in paywall_chunks:
        chunk = _strip_paywall_navigation(chunk)
        inferred = source_label_from_text(chunk[:1500])
        text = chunk if len(chunk) <= 6000 else chunk[:6000] + "\n\n[…gekürzt…]"
        items.append({
            "kind": "paywall",
            "label": inferred or "Paywall",
            "body": f"QUELLE (vermutet): {inferred}\n\n{text}",
        })

    # Rohe Transkripte (kein Endmarker + lang) erst automatisch verdichten —
    # fertige Zusammenfassungen laufen unverändert durch.
    if podcast_text and podcast_text.strip():
        try:
            podcast_text, _n_raw_sum, _raw_errs = preprocess_podcast_text(
                podcast_text, progress_callback=progress_callback)
            if _raw_errs:
                print(f"[podcast-raw] {len(_raw_errs)} Transkript(e) NICHT verdichtet: {_raw_errs}", file=sys.stderr)
        except Exception as _pp_exc:
            print(f"[podcast-raw] Vorverdichtung übersprungen wegen Fehler: {_pp_exc}", file=sys.stderr)
    podcast_chunks = split_podcast_summaries(podcast_text or "") if (podcast_text and podcast_text.strip()) else []
    for chunk in podcast_chunks:
        items.append({
            "kind": "podcast",
            "label": "Podcast",
            "body": chunk.strip(),
        })

    # Kein stiller Auto-Dedup hier: Dubletten werden über den klickbaren, LLM-
    # vorgefilterten Review (llm_confirm_duplicate_clusters) behandelt — Florian
    # behält die Kontrolle (gleiche Story raus, anderer Blickwinkel bleibt).

    return weather_text, items, weak_fetches


_SMART_ARTICLE_BUDGETS = {5: (220, 320), 4: (160, 240), 3: (110, 170), 2: (70, 110), 1: (40, 70)}

_ITEM_RATE_PROMPT = """Du bekommst nummerierte Nachrichten-Quellen EINES Tages (Titel/Auszug). Bewerte die TRAGWEITE jeder Quelle für ein persönliches Audio-Briefing: 5 = Topthema des Tages mit breiter Tragweite, 3 = solide Meldung, 1 = Randnotiz/Kuriosum. Vergib STRENG: höchstens 1-2 Fünfer, die Masse liegt bei 2-3. Podcasts, die ein wichtiges Thema vertiefen, entsprechend hoch bewerten.

ANTWORT NUR ALS JSON, genau ein Eintrag pro Quelle:
{"weights": [{"i": 1, "w": 3}, {"i": 2, "w": 5}]}"""


def _rate_items_via_cli(items, cli_path=None):
    """🧠 Klassischer Intelligent-Modus: EIN Call bewertet die Tragweite aller Items (1-5).
    Ergebnis landet als it["_weight"]; bei Fehler bleiben Items unbewertet → Kompakt-Fallback."""
    cli = cli_path or _locate_claude_cli()
    if not cli or not items:
        return 0
    lines = []
    for i, it in enumerate(items, 1):
        excerpt = " ".join((it.get("body") or "").split())[:200]
        lines.append(f"[{i}] ({it.get('label', '?')}, {it.get('kind', 'article')}) {excerpt}")
    payload = _ITEM_RATE_PROMPT + "\n\n=== QUELLEN ===\n\n" + "\n".join(lines)
    cmd = [cli, "--print", "--output-format", "text", "--model", _CLI_JUDGE_MODEL,
           "--dangerously-skip-permissions", "--effort", "low",
           "--append-system-prompt", "Antworte ausschließlich mit dem JSON-Objekt."]
    try:
        sr = _run_claude_cli_subprocess_streaming(cmd, payload, timeout_seconds=240,
                                                  expected_duration_s=40.0, label="Tragweite-Bewertung")
        m = re.search(r"\{.*\}", (sr.get("stdout") or ""), re.DOTALL)
        data = json.loads(m.group(0)) if (sr.get("ok") and m) else {}
    except Exception as exc:
        print(f"[intelligent] Bewertung fehlgeschlagen: {exc}", file=sys.stderr)
        return 0
    n = 0
    for w in (data.get("weights") or []):
        try:
            idx = int(w["i"]) - 1
            if 0 <= idx < len(items):
                items[idx]["_weight"] = max(1, min(5, int(w["w"])))
                n += 1
        except Exception:
            continue
    if n:
        hist = {}
        for it in items:
            hist[it.get("_weight", 0)] = hist.get(it.get("_weight", 0), 0) + 1
        print(f"[intelligent] {n}/{len(items)} Items bewertet — Verteilung: {dict(sorted(hist.items(), reverse=True))}", file=sys.stderr)
    return n


def _build_chunk_handoff(now, compact_mode, items, weather_text=None, ultra_compact=False, smart_length=False):
    """Baut den Handoff-Text für eine Item-Gruppe (chunked CLI-Pfad)."""
    parts = [_CLAUDE_CHUNK_ARTICLE_PROMPT]
    parts.append(f"\nERSTELLT AM: {now.strftime('%A, %d. %B %Y, %H:%M Uhr')}\n")
    smart_active = smart_length and any("_weight" in it for it in items)
    if smart_active:
        parts.append(
            "INTELLIGENTE LÄNGE: Jeder Beitrag trägt unten eine TRAGWEITE (1-5) mit Wortbudget. "
            "Halte dich daran — Top-Themen voll erzählen, Randnotizen in 2-3 Sätzen. Das Budget ist "
            "ein Richtwert: bei dünner Substanz DEUTLICH unterschreiten, bei echter Tiefe bis ~20% "
            "überziehen. Vollständig bleiben: Kernfakten, Namen und Zahlen immer nennen. "
            "Die oben erwähnte KOMPAKT-MODUS-Zeile entfällt heute — es gelten die TRAGWEITE-Budgets.\n\n"
        )
    elif ultra_compact:
        parts.append(
            "KOMPAKT-MODUS: SEHR KURZ — höchstens ~120 Wörter pro Beitrag, oft weniger. "
            "Nur der Kern + der Merksatz, KEINE Ausschmückung. Auch wichtige Themen knapp "
            "halten (max ~150 Wörter); Kleinmeldungen 1-2 Sätze. Trotzdem konkret (Name/Zahl), "
            "nichts erfinden, nichts weglassen.\n\n"
        )
    else:
        parts.append(f"KOMPAKT-MODUS: {'ja (150-250 Wörter)' if compact_mode else 'nein (200-400 Wörter)'}\n\n")
    if weather_text:
        parts.append("─" * 50 + "\nWETTER (als Section mit \"_weather\": true)\n" + "─" * 50 + "\n")
        parts.append(weather_text.strip() + "\n\n")
    for i, it in enumerate(items, start=1):
        kind_label = {"article": "ARTIKEL", "paywall": "PAYWALL-TEXT", "podcast": "PODCAST"}.get(it["kind"], "ARTIKEL")
        parts.append("─" * 50 + f"\n{kind_label} {i}\n" + "─" * 50 + "\n")
        if smart_active and "_weight" in it:
            _bw = it["_weight"]
            _bmin, _bmax = _SMART_ARTICLE_BUDGETS.get(_bw, (110, 170))
            parts.append(f"TRAGWEITE: {_bw}/5 — Wortbudget {_bmin}-{_bmax} ({_SMART_WEIGHT_NOTES.get(_bw, '')})\n")
        parts.append(it["body"].strip() + "\n\n")
    parts.append("─" * 50 + "\nENDE DIESES AUSSCHNITTS\n" + "─" * 50 + "\n")
    parts.append("Erstelle jetzt die Sections für genau diese Beiträge und gib den JSON-Block zurück.\n")
    return "".join(parts)


# Für die Qualitäts-/Synthese-kritischen Schritte nutzen wir Opus 4.8 (stärker),
# während die Masse (Artikel-Gruppen) auf dem schnelleren Sonnet bleibt. Opus läuft
# ebenfalls kostenlos übers Max-Abo. Verifiziert am 04.06.2026, dass das Alias greift.
_CLI_JUDGE_MODEL = "opus"


def run_briefing_via_claude_cli_chunked(
    urls_text: str,
    paywall_text: str,
    podcast_text: str,
    include_weather: bool,
    output_pdf_path: str,
    *,
    model: str = "sonnet",
    compact_mode: bool = True,
    ultra_compact: bool = False,
    chunk_size: int = 12,
    timeout_seconds: int = 600,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    cli_path: Optional[str] = None,
    merge_duplicates: bool = True,
    prepared: Optional[dict] = None,
    topic_synthesis: bool = False,
    synthesis_narrative: bool = False,
    synthesis_web_enrich: bool = False,
    content_check: bool = False,
    auto_repair: bool = False,
    special_topics: Optional[List[str]] = None,
    smart_length: bool = False,
) -> dict:
    """Wie run_briefing_via_claude_cli, aber in Häppchen — zuverlässig bei großen
    Briefings, weil kein einzelner CLI-Aufruf zu lange läuft (Socket-Abbruch-Schutz).

    Ablauf: Rohdaten sammeln → in Gruppen à chunk_size → pro Gruppe ein kurzer
    CLI-Aufruf (Artikel-Sections) → ein finaler Aufruf (Recap/Essenz/Verabschiedung)
    → alles mergen → PDF bauen. Läuft über Max-Abo, kostenlos.
    """
    def _report(step, ratio):
        if progress_callback:
            try:
                progress_callback(step, max(0.0, min(1.0, ratio)))
            except Exception:
                pass

    cli = cli_path or _locate_claude_cli()
    if not cli:
        return {"ok": False, "error": "Claude CLI nicht gefunden.", "raw_response": "",
                "sections_count": 0, "completeness": None, "output_pdf_path": None}

    cmd = [
        cli, "--print", "--output-format", "text", "--model", model,
        "--dangerously-skip-permissions", "--effort", "low",
        "--append-system-prompt", _CLAUDE_CLI_RESPONSE_PROMPT,
    ]
    cwd = os.path.dirname(os.path.abspath(output_pdf_path)) or os.getcwd()

    def _one_cli_call(handoff: str, label: str, base: float, span: float,
                      progress_cb=progress_callback, model_override=None) -> Optional[list]:
        """Ein CLI-Aufruf → geparste sections-Liste (oder None bei Fehler).

        progress_cb=None schaltet Live-Progress ab — nötig bei parallelen Gruppen,
        weil Streamlit-Aufrufe aus Worker-Threads nicht thread-safe sind.
        model_override setzt für genau diesen Aufruf ein anderes Modell (z.B. Opus
        für die Synthese), ohne das Basis-Modell der Artikel-Gruppen zu ändern.
        """
        _cmd = cmd
        if model_override:
            _cmd = list(cmd)
            for _k in range(len(_cmd) - 1):
                if _cmd[_k] == "--model":
                    _cmd[_k + 1] = model_override
                    break
        sr = _run_claude_cli_subprocess_streaming(
            _cmd, handoff, cwd=cwd, timeout_seconds=timeout_seconds,
            progress_callback=progress_cb, base_progress=base,
            max_progress=base + span, expected_duration_s=180.0, label=label,
        )
        if not sr.get("ok") or sr.get("returncode") != 0:
            return None
        raw = (sr.get("stdout") or "").strip()
        if not raw:
            return None
        # JSON extrahieren (Fence oder erstes {…letztes })
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL)
        block = m.group(1) if m else raw[raw.find("{"):raw.rfind("}") + 1]
        for candidate in (block, _repair_llm_json_quotes(block)):
            try:
                data = json.loads(candidate)
                secs = data.get("sections")
                if isinstance(secs, list):
                    return secs
            except Exception:
                continue
        return None

    _t_start = time.time()
    now = get_berlin_now()
    # Früh-Check (~5-10s): Wenn die CLI abgemeldet ist (Token hart abgelaufen),
    # sofort mit klarer Anleitung abbrechen — statt nach Minuten mit kryptischen
    # Gruppen-Fehlern (so geschehen 03.07.).
    if not prepared:  # bei Mehrfach-Längen reicht der Check des ersten Laufs
        _login_err = check_cli_login(cli)
        if _login_err and "abgemeldet" in _login_err:
            return {"ok": False, "error": _login_err, "raw_response": "",
                    "sections_count": 0, "completeness": None, "output_pdf_path": None}
    if prepared:
        # Wiederverwendung aus einem vorherigen Lauf (Mehrfach-Längen): Rohdaten
        # sind bereits gefetcht UND gemergt — spart den kompletten Fetch + Merge.
        weather_text = prepared.get("weather_text")
        items = list(prepared.get("items") or [])
        weak_fetches = list(prepared.get("weak_fetches") or [])
        merged_topics = prepared.get("merged_topics", 0)
        merge_notes = list(prepared.get("merge_notes") or [])
        print(f"[chunked-cli] Rohdaten aus vorherigem Lauf übernommen ({len(items)} Items, kein erneuter Fetch).", file=sys.stderr)
        if not items and not weather_text:
            return {"ok": False, "error": "Keine verwertbaren Inhalte gefunden.", "raw_response": "",
                    "sections_count": 0, "completeness": None, "output_pdf_path": None}
    else:
        _report("Rohdaten werden gesammelt…", 0.02)
        weather_text, items, weak_fetches = _collect_briefing_raw_items(
            urls_text, paywall_text, podcast_text, include_weather, progress_callback)
        if weak_fetches:
            print(f"[chunked-cli] {len(weak_fetches)} URL(s) mit kaum/keinem Inhalt (Fetch fehlgeschlagen).", file=sys.stderr)

        if not items and not weather_text:
            return {"ok": False, "error": "Keine verwertbaren Inhalte gefunden.", "raw_response": "",
                    "sections_count": 0, "completeness": None, "output_pdf_path": None}

        # Intelligenter Doppel-Themen-Merge: gleiche Story aus mehreren Quellen wird zu
        # EINEM Beitrag zusammengeführt (LLM-bestätigt, konservativ — Blickwinkel bleiben).
        merged_topics, merge_notes = 0, []
        if merge_duplicates and len(items) >= 2:
            try:
                items, merged_topics, merge_notes = _merge_duplicate_story_items(
                    items, cli_path=cli, progress_callback=progress_callback)
            except Exception as _merge_exc:
                print(f"[merge] übersprungen wegen Fehler: {_merge_exc}", file=sys.stderr)

    # Themen-Synthese-Modus: statt Beitrag-pro-Quelle bündelt Opus alle Quellen
    # thematisch und schreibt pro THEMA einen verwobenen Vorlesetext. Der Rest der
    # Pipeline (Dedup, Ressorts, Top-3, Finalize, PDF, TXT) läuft identisch weiter.
    _synth_sections: list = []
    _synth_failed = 0
    _synth_topics = 0
    if smart_length and not topic_synthesis and items and not any("_weight" in it for it in items):
        # 🧠 Klassischer Intelligent-Modus: ein schneller Judge-Call bewertet die
        # Tragweite aller Beiträge — die Gruppen-Prompts bekommen daraus Wortbudgets.
        _report("🧠 Tragweite der Beiträge wird bewertet…", 0.06)
        _rate_items_via_cli(items, cli_path=cli)

    if topic_synthesis:
        _synth_sections, _synth_failed, _synth_topics = _synthesize_topics_from_items(
            items, weather_text=weather_text, compact_mode=compact_mode,
            ultra_compact=ultra_compact, cli_path=cli,
            progress_callback=progress_callback, timeout_seconds=timeout_seconds,
            narrative_style=synthesis_narrative, web_enrich=synthesis_web_enrich,
            smart_length=smart_length)

    # In Gruppen teilen
    groups = [] if topic_synthesis else ([items[i:i + chunk_size] for i in range(0, len(items), chunk_size)] or [[]])
    n_groups = len(groups)
    all_sections: list = []
    failed_groups = 0

    # Gruppen PARALLEL ausführen (max. 2 gleichzeitig, siehe Cap unten). Jede Gruppe ist unabhängig —
    # global sortiert wird erst nach dem Merge. Das verkürzt die Wartezeit erheblich
    # (statt Summe aller Gruppen nur noch ~längste Welle). Läuft weiterhin komplett
    # über Max-Abo (Env-Stripping im Subprocess), kostenlos.
    # WICHTIG: progress_callback NICHT an die Worker-Threads geben — Streamlit ist
    # nicht thread-safe. Der Main-Thread meldet den Fortschritt beim Eintreffen der
    # Ergebnisse via as_completed.
    import concurrent.futures as _futures
    # Cap bewusst niedrig (2): jeder claude-CLI-Aufruf ist ein schwergewichtiger
    # Node/Electron-Prozess (~0,5–1,5 GB). Auf einem 16-GB-Mac würden 4 parallel
    # zusammen mit Claude-Desktop + Browser den Speicher sprengen → macOS killt die
    # App (genau das ist am 05.06. passiert). 2 ist speichersicher und ~2× schneller
    # als sequenziell.
    max_workers = max(1, min(2, n_groups))
    group_results: list = [None] * n_groups  # Reihenfolge der Gruppen erhalten

    def _run_group(gi: int, group: list) -> Optional[list]:
        handoff = _build_chunk_handoff(
            now, compact_mode, group,
            weather_text=weather_text if gi == 0 else None,
            ultra_compact=ultra_compact,
            smart_length=(smart_length and not topic_synthesis))
        secs = _one_cli_call(handoff, f"Gruppe {gi + 1}/{n_groups}", 0.0, 0.0, progress_cb=None)
        # Podcast-Typ DETERMINISTISCH stempeln: Wir wissen aus den Eingaben, welche
        # Items Podcasts sind — Claude setzt das type-Feld gelegentlich falsch
        # (z.B. „Politik mit Anne Will" → type article → landete mitten im Briefing).
        # Sections kommen laut Prompt in Item-Reihenfolge zurück (Wetter separat markiert).
        if secs:
            try:
                _content_secs = [s for s in secs if isinstance(s, dict) and not s.get("_weather")]
                if len(_content_secs) == len(group):
                    for _k, _it in enumerate(group):
                        if _it.get("kind") == "podcast":
                            _content_secs[_k]["type"] = "podcast"
            except Exception:
                pass
        return secs

    def _run_groups(indices: list, prog_base: float, prog_span: float, phase_label: str) -> dict:
        """Führt die angegebenen Gruppen-Indizes parallel aus → {gi: secs|None}.
        Fortschritt wird ausschließlich hier im Main-Thread gemeldet (thread-safe)."""
        out: dict = {}
        total = max(1, len(indices))
        done = 0
        with _futures.ThreadPoolExecutor(max_workers=max_workers) as _ex:
            _future_to_gi = {_ex.submit(_run_group, gi, groups[gi]): gi for gi in indices}
            for _fut in _futures.as_completed(_future_to_gi):
                gi = _future_to_gi[_fut]
                try:
                    out[gi] = _fut.result()
                except Exception as exc:
                    out[gi] = None
                    print(f"[chunked-cli] Gruppe {gi + 1} Ausnahme: {exc}", file=sys.stderr)
                done += 1
                _report(f"{phase_label} ({done}/{total}) — Claude schreibt parallel…",
                        prog_base + prog_span * done / total)
        return out

    # Durchgang 1: alle Gruppen parallel
    _report(f"{n_groups} Gruppen laufen ({max_workers} gleichzeitig) — der Balken steht "
            f"jetzt 1–2 Min still, bis die erste Gruppe fertig ist. Bitte laufen lassen.", 0.15)
    results = _run_groups(list(range(n_groups)), 0.15, 0.53, "Gruppe fertig")

    # Auto-Retry: jede gescheiterte Gruppe genau 1× erneut versuchen. Transiente
    # Fehler (Socket, JSON-Parse) sollen nicht still ~12 Beiträge verschlucken.
    _retry_indices = [gi for gi in range(n_groups) if results.get(gi) is None]
    if _retry_indices:
        print(f"[chunked-cli] {len(_retry_indices)} Gruppe(n) gescheitert — 1× erneuter Versuch.", file=sys.stderr)
        _report(f"{len(_retry_indices)} Gruppe(n) gescheitert — werden erneut versucht…", 0.68)
        _retry_results = _run_groups(_retry_indices, 0.68, 0.07, "Wiederholung")
        for gi, secs in _retry_results.items():
            if secs is not None:
                results[gi] = secs

    # Ergebnisse in ursprünglicher Gruppen-Reihenfolge zusammenführen
    for gi in range(n_groups):
        secs = results.get(gi)
        if secs is None:
            failed_groups += 1
            print(f"[chunked-cli] Gruppe {gi + 1} endgültig fehlgeschlagen oder leer.", file=sys.stderr)
            continue
        all_sections.extend(secs)

    if topic_synthesis:
        all_sections = _synth_sections
        failed_groups = _synth_failed
        n_groups = _synth_topics

    if not all_sections:
        return {"ok": False,
                "error": "Alle Gruppen fehlgeschlagen — keine Beiträge erzeugt. Versuche den API-Pfad.",
                "raw_response": "", "sections_count": 0, "completeness": None,
                "output_pdf_path": None}

    # Sicherheitsnetz: Dubletten auf den KONDENSIERTEN Texten erkennen (dort ist
    # die Erkennung am treffsichersten) — fängt, was der Roh-Merge übersehen hat.
    _report("Dubletten-Check auf den fertigen Beiträgen…", 0.74)
    all_sections, _post_dedup_notes = _drop_condensed_duplicates(all_sections, cli_path=cli)

    # Thematisch sortieren: Claude sieht pro Gruppe nur einen Ausschnitt und kann
    # nicht global ordnen. Ressort-Zuordnung: erst Heuristik, dann korrigiert ein
    # kleiner Opus-Call (alle Titel in einem Rutsch) — lokale Storys landen so
    # zuverlässig bei Regional, auch ohne Orts-Keyword im Titel. Wetter/Podcast
    # bleiben deterministisch (eigene, verlässliche Signale). Sort ist stable —
    # Eingabe-Reihenfolge bleibt innerhalb eines Buckets erhalten.
    _bucket_rank = {
        "wetter": 0, "regional": 1, "politik": 2, "wirtschaft": 3,
        "tech": 4, "gericht": 5, "sonstige": 6, "podcast": 7,
    }
    _report("Ressort-Zuordnung (Opus prüft alle Titel)…", 0.76)
    _heur_buckets = [_classify_section_topic(s) for s in all_sections]
    _titles_by_idx = {}
    for _i, s in enumerate(all_sections):
        if _heur_buckets[_i] in ("wetter", "podcast"):
            continue
        _t = _markdown_line_to_plain(_extract_title(s.get("content", "") or "")).strip()
        if _t:
            # Quelle mitgeben — Lokalzeitungen (Tagblatt, GEA) sind ein starkes Regional-Signal
            _src = (s.get("source_label") or "").strip()
            _titles_by_idx[_i] = f"[{_src}] {_t}" if _src else _t
    _llm_buckets = _classify_sections_via_cli(_titles_by_idx, cli_path=cli)
    _final_buckets = []
    _corrected = 0
    for _i, _hb in enumerate(_heur_buckets):
        _fb = _llm_buckets.get(_i, _hb) if _hb not in ("wetter", "podcast") else _hb
        if _fb != _hb:
            _corrected += 1
        _final_buckets.append(_fb)
    if _llm_buckets:
        print(f"[ressort] Opus-Zuordnung aktiv: {_corrected} von {len(_heur_buckets)} Heuristik-Zuordnungen korrigiert.", file=sys.stderr)
    else:
        print("[ressort] Opus-Zuordnung nicht verfügbar — Keyword-Heuristik bleibt.", file=sys.stderr)
    _order = sorted(range(len(all_sections)), key=lambda _i: (_bucket_rank.get(_final_buckets[_i], 6), _i))
    all_sections = [all_sections[_i] for _i in _order]
    _sorted_buckets = [_final_buckets[_i] for _i in _order]

    # === 🧠 Sonderthemen: Ad-hoc-Recherchen (Opus + Websuche), eigener Block am Ende ===
    special_ok_topics, special_failed_topics = [], []
    if special_topics:
        _report(f"🧠 Sonderthemen: {len(special_topics)} Recherche(n) via Opus…", 0.74)
        _hist_block = _recent_topic_history_block()
        # Heutiges Briefing als Kontext: verhindert, dass ein Sonderthema nachbaut,
        # was die heutigen Quellen ohnehin behandeln (z.B. Reformpaket doppelt).
        _today_titles = []
        for _ts9 in all_sections:
            _tt9 = _markdown_line_to_plain(_extract_title(_ts9.get("content", "") or "")).strip()
            if _tt9 and not _ts9.get("_weather"):
                _today_titles.append(_tt9)
        if _today_titles:
            _hist_block += ("\n\nIM HEUTIGEN BRIEFING BEREITS ENTHALTEN (nicht nachbauen — "
                            "höchstens ergänzen oder kurz Bezug nehmen):\n"
                            + "\n".join(f"- {_t9}" for _t9 in _today_titles[:40]))

        def _one_special(_topic):
            _pl = _SPECIAL_TOPIC_PROMPT.format(topic=_topic) + _hist_block
            _c = [cli, "--print", "--output-format", "text", "--model", _CLI_JUDGE_MODEL,
                  "--dangerously-skip-permissions", "--effort", "medium"]
            for _try in (1, 2):
                try:
                    _sr = _run_claude_cli_subprocess_streaming(
                        _c, _pl, timeout_seconds=420, expected_duration_s=120.0,
                        label=f"Sonderthema: {_topic[:32]}")
                    _out = (_sr.get("stdout") or "").strip()
                    if _sr.get("ok") and _out.startswith("###"):
                        return _out
                except Exception:
                    pass
            return None

        from concurrent.futures import ThreadPoolExecutor as _SpPool, as_completed as _sp_done
        _sp_results = {}
        with _SpPool(max_workers=2) as _spp:
            _sp_futs = {_spp.submit(_one_special, _t): _t for _t in special_topics}
            for _f in _sp_done(_sp_futs):
                try:
                    _sp_results[_sp_futs[_f]] = _f.result()
                except Exception:
                    _sp_results[_sp_futs[_f]] = None
        for _t in special_topics:
            _md = _sp_results.get(_t)
            if _md:
                all_sections.append({"type": "article", "_special": True,
                                     "source_label": "Sonderthema 🧠", "content": _md})
                _sorted_buckets.append("sonder")
                special_ok_topics.append(_t)
            else:
                special_failed_topics.append(_t)
                print(f"[sonderthema] fehlgeschlagen: {_t[:70]}", file=sys.stderr)
        if special_ok_topics:
            print(f"[sonderthema] {len(special_ok_topics)} Beitrag/Beiträge recherchiert und angehängt.", file=sys.stderr)

    # Finalisierung: Recap + Essenz + Verabschiedung aus den Titeln/Kernaussagen
    _report("Rückblick, Essenz und Verabschiedung werden gebaut…", 0.78)
    digest_lines = []
    for s in all_sections:
        if s.get("_weather"):
            continue
        title = _markdown_line_to_plain(_extract_title(s.get("content", "") or "")).strip()
        if title:
            digest_lines.append(f"- {title}")

    # Sichtbare Ressort-Überschriften zwischen den (bereits sortierten) Gruppen einfügen,
    # damit die thematische Gliederung im Voll-Briefing sichtbar ist (wie in der Kompaktfassung).
    # Wetter braucht keinen Header (ist selbsterklärend + eigene Section).
    _ressort_labels = {
        "regional": "Regional", "politik": "Politik & International", "wirtschaft": "Wirtschaft",
        "tech": "Tech & Wissenschaft", "gericht": "Gericht & Recht", "sonstige": "Weitere Themen",
        "podcast": "Podcasts", "sonder": "Sonderthemen",
    }
    _with_headers = []
    _last_bucket = None
    for _bk, s in zip(_sorted_buckets, all_sections):
        if _bk != _last_bucket:
            if _bk in _ressort_labels:
                _with_headers.append({
                    "type": "article", "_ressort_header": True, "source_label": "",
                    "content": f"# {_ressort_labels[_bk]}",
                })
            _last_bucket = _bk
        _with_headers.append(s)
    all_sections = _with_headers

    finalize_handoff = (
        _CLAUDE_FINALIZE_PROMPT
        + f"\nERSTELLT AM: {now.strftime('%A, %d. %B %Y, %H:%M Uhr')}\n"
        + f"TAGESZEIT: {_tageszeit_label(now.hour)}\n\n"
        + "\n".join(digest_lines) + "\n"
    )
    # Zitat-Rotation: LLMs greifen immer wieder zu denselben Lieblingszitaten
    # (z.B. 3× Anaïs Nin in einer Woche). Zuletzt verwendete ausschließen.
    _recent_quotes = _load_recent_quotes()
    if _recent_quotes:
        finalize_handoff += (
            "\nZULETZT VERWENDETE SCHLUSSZITATE — verwende KEINES davon erneut. "
            "Wähle ein frisches, thematisch passendes Zitat einer ANDEREN Person "
            "(gern auch mal überraschend: Wissenschaft, Literatur, Sport, Film):\n"
            + "\n".join(f"- {q}" for q in _recent_quotes) + "\n"
        )
    # Schlussteil ist die kreative Synthese → Opus 4.8 (stärker). Wenn Opus mal
    # nicht liefert, sauberer Fallback auf das Basis-Modell (Sonnet), damit das
    # Briefing nie ohne Recap/Essenz endet.
    final_secs = _one_cli_call(finalize_handoff, "Schlussteil (Opus)", 0.78, 0.08,
                               model_override=_CLI_JUDGE_MODEL)
    if not final_secs:
        print("[chunked-cli] Opus-Schlussteil leer — Fallback auf Basis-Modell.", file=sys.stderr)
        final_secs = _one_cli_call(finalize_handoff, "Schlussteil (Fallback)", 0.86, 0.04)
    if final_secs:
        # Top-3-Vorschau (_preview) kommt GANZ AN DEN ANFANG, Recap/Essenz/Verabschiedung ans Ende.
        _preview_secs = [s for s in final_secs if s.get("_preview")]
        _end_secs = [s for s in final_secs if not s.get("_preview")]
        if _preview_secs:
            all_sections[0:0] = _preview_secs
            print(f"[chunked-cli] Top-3-Vorschau vorangestellt.", file=sys.stderr)
        all_sections.extend(_end_secs)
    else:
        print("[chunked-cli] Finalisierung fehlgeschlagen — Briefing ohne Recap/Essenz.", file=sys.stderr)

    # === Inhaltlicher Plausibilitäts-Check + Auto-Repair (VOR dem PDF-Bau, damit
    # alle Ausgaben — PDF, TXT, ePub, Reader-Upload — die geprüfte Fassung tragen).
    # Quellenbasis: die bereits gesammelten items (+ Wetter), kein separates Handoff nötig.
    content_check_data = None
    content_repaired = 0
    if content_check and all_sections:
        _src_lines = []
        if weather_text and str(weather_text).strip():
            _src_lines.append(f"--- QUELLE W (DWD, weather) ---\n{str(weather_text).strip()[:2000]}")
        for _si, _sit in enumerate(items, 1):
            _src_lines.append(f"--- QUELLE {_si} ({_sit.get('label', '?')}, {_sit.get('kind', 'article')}) ---\n{(_sit.get('body') or '')[:7000]}")
        _report("Plausibilitäts-Check: Claude prüft jeden Beitrag gegen die Quellen…", 0.88)
        try:
            content_check_data = run_content_check_via_claude_cli(
                handoff_text="\n\n".join(_src_lines),
                briefing_sections=all_sections,
                model=_CLI_JUDGE_MODEL, cli_path=cli, timeout_seconds=900,
                progress_callback=(lambda s, r: _report(s, 0.88 + 0.05 * max(0.0, min(1.0, float(r))))) if progress_callback else None,
            )
        except Exception as _cc_exc:
            content_check_data = {"ok": False, "error": str(_cc_exc)[:200], "enabled": True,
                                  "checked": 0, "warnings": 0, "notices": 0, "items": []}
        if content_check_data.get("ok"):
            print(f"[plausi] {content_check_data.get('checked', 0)} Beiträge geprüft: "
                  f"{content_check_data.get('warnings', 0)} Warnungen, {content_check_data.get('notices', 0)} Hinweise.", file=sys.stderr)
        else:
            print(f"[plausi] Check fehlgeschlagen: {content_check_data.get('error')}", file=sys.stderr)
        if auto_repair and content_check_data.get("ok") and content_check_data.get("warnings", 0) > 0:
            _report(f"Auto-Repair: {content_check_data['warnings']} Beitrag/Beiträge werden korrigiert…", 0.93)
            try:
                _rep = run_briefing_repair_via_claude_cli(
                    handoff_text="\n\n".join(_src_lines),
                    briefing_sections=all_sections,
                    output_lint=None, content_check=content_check_data,
                    model=_CLI_JUDGE_MODEL, cli_path=cli, timeout_seconds=900,
                    progress_callback=(lambda s, r: _report(s, 0.93 + 0.04 * max(0.0, min(1.0, float(r))))) if progress_callback else None,
                )
            except Exception as _rep_exc:
                _rep = {"ok": False, "error": str(_rep_exc)[:200]}
            if _rep.get("ok") and _rep.get("repaired_count", 0) > 0:
                all_sections = _rep.get("sections") or all_sections
                content_repaired = int(_rep.get("repaired_count", 0))
                print(f"[plausi] {content_repaired} Beitrag/Beiträge repariert.", file=sys.stderr)
            elif not _rep.get("ok"):
                print(f"[plausi] Auto-Repair fehlgeschlagen: {_rep.get('error')}", file=sys.stderr)

    # PDF bauen (gleiche Pipeline wie der einteilige Pfad)
    _report("PDF wird gebaut…", 0.98 if content_check_data else 0.90)
    merged_json = json.dumps({"compact_mode": compact_mode, "sections": all_sections}, ensure_ascii=False)
    pdf_result = build_pdf_from_claude_json(merged_json, output_pdf_path, generated_at=now)

    if not pdf_result.get("ok"):
        return {"ok": False, "error": f"PDF-Bau fehlgeschlagen: {pdf_result.get('error')}",
                "raw_response": merged_json, "sections_count": 0,
                "completeness": None, "output_pdf_path": None}

    if special_topics is not None:
        # Themen-Historie nur für die Hauptversion (WhatsApp/Zweitlängen geben None):
        # heutige Titel + Sonderthemen merken → Wiederholungs-Erkennung morgen.
        _topic_history_append([_l.lstrip("- ").strip() for _l in digest_lines]
                              + [f"Sonderthema: {_t}" for _t in special_ok_topics])
    _report("Fertig.", 1.0)
    note = f" ({failed_groups} von {n_groups} Gruppen fehlgeschlagen)" if failed_groups else ""
    return {
        "ok": True, "error": None,
        "sections_count": pdf_result.get("sections_count", 0),
        "completeness": pdf_result.get("completeness"),
        "output_lint": pdf_result.get("output_lint"),
        "sorting_diag": pdf_result.get("sorting_diag"),
        "artifacts": pdf_result.get("artifacts"),
        "raw_response": merged_json,
        "output_pdf_path": output_pdf_path,
        "clean_sections": pdf_result.get("clean_sections"),
        "content_check": content_check_data,
        "content_repaired": content_repaired,
        "special_done": (len(special_ok_topics) if special_topics is not None else None),
        "special_failed_topics": special_failed_topics,
        "chunked_note": (f"Themen-Synthese: {n_groups} Themen aus {len(items)} Quellen" if topic_synthesis
                         else f"{n_groups} Gruppen{note}"),
        "failed_groups": failed_groups,
        "n_groups": n_groups,
        "chunk_size": chunk_size,
        "merged_topics": merged_topics,
        "merge_notes": merge_notes,
        "weak_fetches": weak_fetches,
        "elapsed_seconds": time.time() - _t_start,
        # Für Mehrfach-Längen: gefetchte+gemergte Rohdaten wiederverwendbar machen
        # (zweiter Lauf spart Fetch + Merge komplett).
        "prepared": {
            "weather_text": weather_text,
            "items": items,
            "weak_fetches": weak_fetches,
            "merged_topics": merged_topics,
            "merge_notes": merge_notes,
        },
    }


_GENIUS_DIRECT_FROM_RAW_PROMPT_LONG = """Du erstellst direkt aus Rohdaten (Wetter, Artikel-Quelltexte, Paywall-Briefings, Podcast-Zusammenfassungen) eine sehr ausführliche deutsche Audio-Kompaktfassung — Lang-Variante.

ZIEL Diese Langfassung soll beim Hören Spaß machen, gut kuratiert wirken, klare Zusammenhänge herstellen und JEDEN Beitrag so tief behandeln, dass der Hörer den Sachverhalt wirklich versteht — inklusive Namen, Zahlen, Kontext, Hintergrund, Entwicklung. Es darf kein Beitrag fehlen.

ABDECKUNG Jeder Beitrag aus den Rohdaten muss genau 1 Mal vorkommen. Nichts weglassen, nichts doppeln.

IDENTITÄT Nummeriere die Beiträge fortlaufend: erst Wetter (wenn vorhanden), dann alle ARTIKEL in der gegebenen Reihenfolge, dann alle PAYWALL-TEXTE, dann alle PODCASTS. Jede Beitragszeile beginnt mit der Nummer in eckigen Klammern: `[12] Satz...`. ZWINGEND: Halte dieses `[N]`-Format bei JEDEM einzelnen Beitrag durch — auch bei 80 oder mehr Beiträgen. Wechsle NIEMALS zu reinem Fließtext ohne `[N]`-Marker und fasse NIEMALS mehrere Beiträge unter einer Nummer zusammen, egal wie lang die Liste wird.

LÄNGE Pro Beitrag in der Regel 7 bis 10 klare, inhaltlich dichte Sätze. Bei komplexen Themen (Gericht, Politik, Konflikt) auch 10–14 Sätze. Bei Kleinstmeldungen reichen 4–5 Sätze. Nenne immer alle relevanten Namen, Zahlen, Daten, Orte und Einordnungen aus den Quelltexten. PODCAST-Beiträge in voller Länge übernehmen wie geliefert.

QUELLE Beginne jede Beitragszeile direkt nach der Nummer mit der Quelle in runden Klammern, dann der Text — Beispiel: `[7] (SWR) In Reutlingen …`. Nimm die Quelle aus der `QUELLE:`-Zeile der jeweiligen Rohdaten. Ist dort keine sinnvolle Quelle genannt oder steht nur „unbekannt", lass die Klammer KOMPLETT weg und beginne direkt mit dem Text. Nutze NUR den kurzen Sendernamen/das Medium in der Klammer (z.B. (tagesschau), (SWR), (n-tv), (dpa)) — keine Zusätze, keine Erklärung, kein Doppelpunkt-Anhang in der Klammer. Erfinde NIE eine Quelle. WICHTIG: Bei FAST JEDEM Beitrag steht eine Quelle in den Rohdaten — übernimm sie konsequent, auch wenn dort „QUELLE (vermutet):" steht (dann ohne das Wort „vermutet"). Lass die Klammer nur weg, wenn wirklich gar keine Quelle dasteht oder ausdrücklich „unbekannt".

TREUE Bleibe strikt bei den Quelltexten. Keine Wertung, keine zusätzlichen Fakten, keine dramatische Sprache.

SELBSTSTÄNDIGKEIT Schreibe so, dass jede Zeile den Sachverhalt eigenständig trägt. Keine Verweise wie „dort", „dabei", „dieser Fall" ohne klaren Bezug im selben Satz.

SPRACHE Durchgehend Deutsch. Übersetze englische oder fremdsprachige Quelltexte restlos ins Deutsche — KEINE englischen Wortfetzen, Phrasen oder Halbsätze stehen lassen, auch nicht mitten im Satz. Nur Eigennamen und Originaltitel dürfen im Original bleiben; englische Zitate ins Deutsche übersetzen.

ROHTEXT-HYGIENE Die Quelltexte können Reste von Navigation, Cookie-Hinweisen, Werbung oder Bildunterschriften enthalten. Ignoriere solche Fragmente vollständig und fasse nur den journalistischen Kern.

DUBLETTEN Beschreiben mehrere Beiträge dasselbe Ereignis, behandle jeden weiterhin als eigene [N]-Zeile, wiederhole die Fakten aber nicht wortgleich — beim zweiten nur die zusätzliche Perspektive nennen.

STIL Hörbar, klar, lebendig aber ohne Pathos. Keine Bullet Points. Deutsch (außer Eigennamen). NDR-Info-Podcast-Niveau, nicht steif.

FORMAT (exakt)
### Kompakte Vollzusammenfassung

*Hier ist das ganze Briefing in sehr ausführlicher Form.*

[1] 7 bis 10 Sätze zu Beitrag 1, mit allen Namen und Zahlen.
[2] 7 bis 10 Sätze zu Beitrag 2.
…

#### Was du mitnehmen kannst
12 bis 16 Sätze inhaltlicher Fließtext. Konkrete Themen und Entwicklungen des Tages mit Namen, Orten, Zahlen. NICHT über das Briefing selbst schreiben. Cluster: regional, politisch/international, Wirtschaft, Tech, Gerichtliches, Podcasts.

Ende der Kurzfassung.

Gib NUR die Kompaktfassung aus. Keine Kommentare, keine Erklärungen, keine Vorrede."""


_GENIUS_DIRECT_FROM_RAW_PROMPT_STANDARD = """Du erstellst direkt aus Rohdaten (Wetter, Artikel-Quelltexte, Paywall-Briefings, Podcast-Zusammenfassungen) eine deutsche Audio-Kompaktfassung — Standard-Variante.

ZIEL Ausgewogene Hörfassung: kürzer als das Vollbriefing, aber jeder Beitrag mit klarem Kern und genug Kontext. Es darf kein Beitrag fehlen.

ABDECKUNG Jeder Beitrag genau 1 Mal. Nichts weglassen, nichts doppeln.

IDENTITÄT Fortlaufende Nummerierung: erst Wetter (wenn vorhanden), dann ARTIKEL, dann PAYWALL-TEXTE, dann PODCASTS. Jede Zeile mit `[N] Satz...`.

LÄNGE Pro Beitrag 4 bis 6 klare Sätze, mit den wichtigsten Namen, Zahlen und Orten. PODCASTS in voller Länge übernehmen.

QUELLE Beginne jede Beitragszeile direkt nach der Nummer mit der Quelle in runden Klammern, dann der Text — Beispiel: `[7] (SWR) In Reutlingen …`. Nimm die Quelle aus der `QUELLE:`-Zeile der jeweiligen Rohdaten. Ist dort keine sinnvolle Quelle genannt oder steht nur „unbekannt", lass die Klammer KOMPLETT weg und beginne direkt mit dem Text. Nutze NUR den kurzen Sendernamen/das Medium in der Klammer (z.B. (tagesschau), (SWR), (n-tv), (dpa)) — keine Zusätze, keine Erklärung, kein Doppelpunkt-Anhang in der Klammer. Erfinde NIE eine Quelle. WICHTIG: Bei FAST JEDEM Beitrag steht eine Quelle in den Rohdaten — übernimm sie konsequent, auch wenn dort „QUELLE (vermutet):" steht (dann ohne das Wort „vermutet"). Lass die Klammer nur weg, wenn wirklich gar keine Quelle dasteht oder ausdrücklich „unbekannt".

TREUE Strikt bei den Quelltexten bleiben. Keine neue Wertung, keine zusätzlichen Fakten.

SPRACHE Durchgehend Deutsch. Übersetze englische oder fremdsprachige Quelltexte restlos ins Deutsche — KEINE englischen Wortfetzen, Phrasen oder Halbsätze stehen lassen, auch nicht mitten im Satz. Nur Eigennamen und Originaltitel dürfen im Original bleiben.

ROHTEXT-HYGIENE Die Quelltexte können Reste von Navigation, Cookie-Hinweisen, Werbung oder Bildunterschriften enthalten. Ignoriere solche Fragmente vollständig und fasse nur den journalistischen Kern.

DUBLETTEN Beschreiben mehrere Beiträge dasselbe Ereignis, behandle jeden weiterhin als eigene [N]-Zeile, wiederhole die Fakten aber nicht wortgleich.

STIL Hörbar, klar. Keine Bullet Points. Deutsch (außer Eigennamen).

FORMAT (exakt)
### Kompakte Vollzusammenfassung

*Hier ist das ganze Briefing in einer längeren, aber immer noch kompakten Form.*

[1] 4 bis 6 Sätze zu Beitrag 1.
[2] 4 bis 6 Sätze zu Beitrag 2.
…

#### Was du mitnehmen kannst
6 bis 10 Sätze inhaltlicher Fließtext. Wichtige Themen und Entwicklungen des Tages mit Namen, Orten, Zahlen.

Ende der Kurzfassung.

Gib NUR die Kompaktfassung aus. Keine Kommentare."""


_GENIUS_DIRECT_FROM_RAW_PROMPT_SHORT = """Du erstellst direkt aus Rohdaten (Wetter, Artikel-Quelltexte, Paywall-Briefings, Podcast-Zusammenfassungen) eine deutsche Audio-Kompaktfassung — Kurz-Variante.

ZIEL Sehr knappe Hörfassung. Kürzer als Standard, aber kein Beitrag darf fehlen.

ABDECKUNG Jeder Beitrag genau 1 Mal. Nichts weglassen, nichts doppeln.

IDENTITÄT Fortlaufende Nummerierung: erst Wetter, dann ARTIKEL, dann PAYWALL, dann PODCASTS. Jede Zeile mit `[N] Satz...`.

LÄNGE Pro Beitrag 1 kompakter Satz, nur wenn nötig 2 kurze Sätze. PODCASTS klar markieren, kurz zusammenfassen.

QUELLE Beginne jede Beitragszeile direkt nach der Nummer mit der Quelle in runden Klammern, dann der Text — Beispiel: `[7] (SWR) Kurzer Satz …`. Nimm die Quelle aus der `QUELLE:`-Zeile der jeweiligen Rohdaten. Ist keine sinnvolle Quelle genannt oder steht nur „unbekannt", lass die Klammer KOMPLETT weg. Nutze NUR den kurzen Sendernamen/das Medium in der Klammer (z.B. (tagesschau), (SWR), (n-tv), (dpa)) — keine Zusätze, keine Erklärung, kein Doppelpunkt-Anhang in der Klammer. Erfinde NIE eine Quelle. WICHTIG: Bei FAST JEDEM Beitrag steht eine Quelle in den Rohdaten — übernimm sie konsequent, auch wenn dort „QUELLE (vermutet):" steht (dann ohne das Wort „vermutet"). Lass die Klammer nur weg, wenn wirklich gar keine Quelle dasteht oder ausdrücklich „unbekannt".

TREUE Strikt bei den Quelltexten. Keine neue Wertung.

SPRACHE Durchgehend Deutsch. Übersetze englische oder fremdsprachige Quelltexte restlos ins Deutsche — KEINE englischen Wortfetzen oder Halbsätze stehen lassen, auch nicht mitten im Satz. Nur Eigennamen dürfen im Original bleiben.

ROHTEXT-HYGIENE Quelltexte können Navigations-, Cookie-, Werbe- oder Bildunterschriften-Reste enthalten. Ignoriere sie und fasse nur den journalistischen Kern.

STIL Hörbar, dicht, klar. Kurze Sätze. Keine Bullet Points. Deutsch.

FORMAT (exakt)
### Kompakte Vollzusammenfassung

*Hier ist das ganze Briefing in sehr knapper, aber vollständiger Form.*

[1] 1 kurzer Satz zu Beitrag 1.
[2] 1 kurzer Satz zu Beitrag 2.
…

#### Was du mitnehmen kannst
2 bis 4 Sätze inhaltlicher Fließtext. Wichtigste Themen mit Namen, Orten, Zahlen.

Ende der Kurzfassung.

Gib NUR die Kompaktfassung aus. Keine Kommentare."""


def _genius_direct_prompt_for_mode(mode: str) -> str:
    normalized = _normalize_genius_summary_mode(mode)
    if normalized == "short":
        return _GENIUS_DIRECT_FROM_RAW_PROMPT_SHORT
    if normalized == "long":
        return _GENIUS_DIRECT_FROM_RAW_PROMPT_LONG
    return _GENIUS_DIRECT_FROM_RAW_PROMPT_STANDARD


def _clean_pseudo_title(text: str, max_len: int = 90) -> str:
    """Kurztitel aus Rohtext — NIE mitten im Wort abschneiden (Direkt-Modus-Überschriften
    waren vorher hart bei 80 Zeichen abgehackt, z.B. „…Öffnungszeite"). Schneidet an der
    letzten Wortgrenze vor max_len und hängt Auslassungspunkte an."""
    t = " ".join((text or "").split())
    if len(t) <= max_len:
        return t
    cut = t[:max_len].rsplit(" ", 1)[0].rstrip(" ,;:–-")
    return (cut + " …") if cut else t[:max_len]


def _extract_pseudo_sections_from_handoff(handoff_text: str) -> List[dict]:
    """Extrahiert Pseudo-Sections aus einem Handoff-Paket — für Quality-Check
    und Format-Pipeline im Direct-Genius-Modus.

    Reihenfolge muss exakt zu der in `_GENIUS_DIRECT_FROM_RAW_PROMPT_*`
    geforderten ID-Vergabe passen: erst Wetter, dann ARTIKEL, dann PAYWALL,
    dann PODCASTS.
    """
    sections: List[dict] = []
    if not handoff_text:
        return sections

    # 1) Wetter: Block zwischen "WETTER (...)" Header und nächstem Trenner
    weather_match = re.search(
        r'WETTER\s*\([^)]*\)\s*\n[─\-=]+\s*\n(.*?)(?=\n[─\-=]{20,}|\Z)',
        handoff_text,
        flags=re.DOTALL,
    )
    if weather_match:
        weather_body = weather_match.group(1).strip()
        if weather_body and len(weather_body) > 50 and "konnte nicht geholt" not in weather_body.lower():
            sections.append({
                "type": "article",
                "_weather": True,
                "source_label": "DWD",
                "content": f"### Wetter\n\n*Aktuelle Lage*\n\n{weather_body[:4000]}",
            })

    # 2) ARTIKEL — `### Artikel N` Pattern (QUELLE: oder QUELLE (vermutet):)
    artikel_re = re.compile(
        r'###\s*Artikel\s+(\d+)\s*\n'
        r'(?:QUELLE\s*(?:\([^)]*\))?\s*:\s*(.*?)\s*\n)?'
        r'(?:URL:\s*(.*?)\s*\n)?'
        r'\s*\n?'
        r'(.*?)'
        r'(?=\n###\s*Artikel\s+\d+|\n###\s*Paywall|\n###\s*Podcast|\n[─\-=]{20,}|\Z)',
        flags=re.DOTALL,
    )
    for m in artikel_re.finditer(handoff_text):
        num = m.group(1) or "?"
        quelle = (m.group(2) or "?").strip() or "?"
        text_body = (m.group(4) or "").strip()
        if not text_body or len(text_body) < 30:
            # Auch leere Beiträge als Section führen, damit ID-Mapping konsistent bleibt
            text_body = f"(Konnte nicht geladen werden)"
        # Pseudo-Title aus den ersten ~80 Zeichen extrahieren (für source-list im Quality-Check)
        first_line = text_body.split("\n", 1)[0].strip()
        pseudo_title = _clean_pseudo_title(first_line) if len(first_line) >= 20 else f"Artikel {num} ({quelle})"
        sections.append({
            "type": "article",
            "source_label": quelle,
            "content": f"### {pseudo_title}\n\n*Quelle: {quelle}*\n\n{text_body[:4000]}",
        })

    # 3) PAYWALL-TEXTE — `### Paywall N` Pattern (QUELLE: oder QUELLE (vermutet):)
    paywall_re = re.compile(
        r'###\s*Paywall\s+(\d+)\s*\n'
        r'(?:QUELLE\s*(?:\([^)]*\))?\s*:\s*(.*?)\s*\n)?'
        r'\s*\n?'
        r'(.*?)'
        r'(?=\n###\s*Paywall\s+\d+|\n###\s*Podcast|\n[─\-=]{20,}|\Z)',
        flags=re.DOTALL,
    )
    for m in paywall_re.finditer(handoff_text):
        num = m.group(1) or "?"
        quelle = (m.group(2) or "Paywall").strip()
        text_body = (m.group(3) or "").strip()
        first_line = text_body.split("\n", 1)[0].strip()
        pseudo_title = _clean_pseudo_title(first_line) if len(first_line) >= 20 else f"Paywall-Artikel {num}"
        sections.append({
            "type": "article",
            "source_label": quelle,
            "content": f"### {pseudo_title}\n\n*Paywall-Artikel*\n\n{text_body[:4000]}",
        })

    # 4) PODCASTS — `### Podcast N` Pattern (QUELLE: oder QUELLE (vermutet):)
    podcast_re = re.compile(
        r'###\s*Podcast\s+(\d+)\s*\n'
        r'(?:QUELLE\s*(?:\([^)]*\))?\s*:\s*(.*?)\s*\n)?'
        r'\s*\n?'
        r'(.*?)'
        r'(?=\n###\s*Podcast\s+\d+|\n[─\-=]{20,}|\Z)',
        flags=re.DOTALL,
    )
    for m in podcast_re.finditer(handoff_text):
        num = m.group(1) or "?"
        quelle = (m.group(2) or "Podcast").strip()
        text_body = (m.group(3) or "").strip()
        first_line = text_body.split("\n", 1)[0].strip()
        pseudo_title = _clean_pseudo_title(first_line) if len(first_line) >= 20 else f"Podcast {num}"
        sections.append({
            "type": "podcast",
            "source_label": quelle,
            # Podcasts NICHT auf 4000 kürzen — sie sollen in voller Länge erscheinen
            # (wurden sonst mitten im Satz abgeschnitten). Großzügiges Sicherheits-Cap.
            "content": f"### {pseudo_title}\n\n*Podcast-Zusammenfassung*\n\n{text_body[:20000]}",
        })

    return sections


def _diagnose_section_sorting(sections: List[dict]) -> dict:
    """Lokale Sortier-Diagnose — prüft thematische Reihenfolge ohne LLM-Call.

    Klassifiziert jeden Beitrag in einen Bucket (wetter/regional/politik/…)
    und prüft ob die Reihenfolge der monoton steigenden Bucket-Priorität
    entspricht. Erlaubt eine kleine Toleranz (max ~12% Verstöße).

    Returns dict mit {ok, checked, violations, violation_threshold,
    buckets_actual, expected_order}.
    """
    content_sections = [
        s for s in sections
        if s.get("type") != "transition"
        and not s.get("_recap")
        and not s.get("_essenz")
        and not s.get("_verabschiedung")
        and not s.get("_preview")
        and not s.get("_ressort_header")
    ]

    if len(content_sections) < 3:
        return {
            "ok": True,
            "checked": len(content_sections),
            "violations": 0,
            "violation_threshold": 0,
            "buckets_actual": [],
            "expected_order": ["wetter", "regional", "politik", "wirtschaft", "tech", "gericht", "sonstige", "podcast"],
            "skipped_reason": "zu wenige Beiträge für sinnvolle Diagnose",
        }

    bucket_priority = {
        "wetter": 1,
        "regional": 2,
        "politik": 3,
        "wirtschaft": 4,
        "tech": 5,
        "gericht": 6,
        "sonstige": 7,
        "podcast": 8,
    }

    actual_buckets: List[str] = []
    for section in content_sections:
        try:
            actual_buckets.append(_classify_section_topic(section))
        except Exception:
            actual_buckets.append("sonstige")

    actual_priorities = [bucket_priority.get(b, 7) for b in actual_buckets]

    # Verstöße zählen: jeder absteigende Sprung in der Bucket-Priorität ist eine
    # mögliche Verletzung der Sortierreihenfolge.
    violations = 0
    violation_indices: List[int] = []
    for i in range(1, len(actual_priorities)):
        if actual_priorities[i] < actual_priorities[i - 1]:
            violations += 1
            violation_indices.append(i)

    # Toleranz: ~12% der Beiträge dürfen verschoben sein, mindestens 2 Verstöße erlaubt.
    threshold = max(2, len(content_sections) // 8)

    return {
        "ok": violations <= threshold,
        "checked": len(content_sections),
        "violations": violations,
        "violation_threshold": threshold,
        "violation_indices": violation_indices,
        "buckets_actual": actual_buckets,
        "expected_order": ["wetter", "regional", "politik", "wirtschaft", "tech", "gericht", "sonstige", "podcast"],
    }


_CLAUDE_CLI_CONTENT_CHECK_SYSTEM_PROMPT = """Du prüfst ein fertiges deutsches Audio-Tagesbriefing gegen seine Original-Rohdaten auf inhaltliche Plausibilität.

ZIEL
Finde nur materielle inhaltliche Fehler. Sei sparsam mit Warnungen. Ein gutes Briefing kürzt, paraphrasiert und rundet — das ist erwünscht, nicht fehlerhaft.

HART WARNEN NUR BEI:
1) Aussage im Briefing widerspricht dem Quelltext faktisch (Zahl falsch, Person verwechselt, Ergebnis verdreht)
2) Kernaussage fehlt komplett und dadurch entsteht ein falsches Gesamtbild
3) Briefing behauptet etwas Sicheres, das im Quelltext nur als Möglichkeit/Plan/Vermutung steht
4) Personen oder Fälle aus einer Sammelmeldung werden vermischt

NUR ALS HINWEIS (notice):
- Möglicherweise nützliches Detail fehlt, Kern aber intakt
- Leichte Überzeichnung ohne echte Bedeutungsverschiebung

KOMPLETT IGNORIEREN:
- Stilfragen, Tonalität, Satzlänge, Wortwahl
- Audio-Rundungen bei Zahlen (13,6 → „knapp 14", 2,4 Mio → „gut 2 Millionen")
- Auswahl von 3 von 7 Beispielen wenn Aussage stimmt
- Übersetzungs-Varianten ohne Bedeutungsänderung
- Briefing-Strukturelemente (### Titel, kursive Einordnung, „Was bleibt:", „Weiter geht's.", Beitragszähler)
- Markdown-Formatierung

ENTSCHEIDUNGSREGEL: Wenn unsicher zwischen „warn" und „notice" → notice. Wenn unsicher zwischen „notice" und „ok" → ok. Lieber einen echten Fehler übersehen als zehn Fehlalarme produzieren.

ANTWORT-FORMAT
Antworte ausschließlich mit EINEM JSON-Codeblock (```json … ```), nichts davor, nichts danach. Struktur:

```json
{
  "items": [
    {
      "section_index": 1,
      "label": "kurzer Titel oder Quelle des Beitrags",
      "level": "ok" | "notice" | "warn",
      "summary": "Ein-Satz-Befund (auch bei ok kurz halten)",
      "hard_issues": ["max 3 kurze materielle Probleme"],
      "soft_issues": ["max 3 kurze Hinweise"]
    }
  ],
  "warnings": 0,
  "notices": 0,
  "ok": 0,
  "checked": 0
}
```

Reihenfolge der `items` = Reihenfolge der Beiträge im fertigen Briefing.
Zähler `warnings`/`notices`/`ok` müssen mit den Levels in `items` übereinstimmen.
"""


def run_content_check_via_claude_cli(
    handoff_text: str,
    briefing_sections: List[dict],
    *,
    model: str = "sonnet",
    timeout_seconds: int = 1200,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    cli_path: Optional[str] = None,
) -> dict:
    """Inhaltlicher Plausibilitäts-Check eines fertigen Briefings via Claude CLI.

    Schickt Original-Rohdaten + fertiges Briefing in EINEM Call an Claude
    und lässt für jeden Beitrag prüfen, ob die Zusammenfassung faktisch
    zur Quelle passt. Output ist kompatibel zum API-Pfad-`content_check`-Dict
    (gleiche Felder: enabled, mode, checked, warnings, notices, ok, items).

    Returns dict mit zusätzlich {ok, error, raw_response, elapsed_seconds}.
    """
    import json as _json

    def _report(step: str, ratio: float):
        if progress_callback:
            try:
                progress_callback(step, max(0.0, min(1.0, float(ratio))))
            except Exception:
                pass

    if not handoff_text or not briefing_sections:
        return {"ok": False, "error": "Handoff-Text oder Briefing-Sections fehlen.",
                "enabled": True, "mode": "warn", "checked": 0, "warnings": 0,
                "notices": 0, "items": [], "raw_response": ""}

    cli = cli_path or _locate_claude_cli()
    if not cli:
        return {"ok": False, "error": "Claude CLI nicht gefunden.",
                "enabled": True, "mode": "warn", "checked": 0, "warnings": 0,
                "notices": 0, "items": [], "raw_response": ""}

    # Prüfbare Sections filtern (Recap/Essenz/Verabschiedung sind synthetische
    # Meta-Blöcke ohne klar identifizierbare Quelle — kein sinnvoller Check).
    checkable_sections = [
        s for s in briefing_sections
        if s.get("type") != "transition"
        and not s.get("_recap")
        and not s.get("_essenz")
        and not s.get("_verabschiedung")
        and not s.get("_preview")
        and not s.get("_ressort_header")
        and not s.get("_special")
    ]
    if not checkable_sections:
        return {"ok": True, "error": None,
                "enabled": True, "mode": "warn", "checked": 0, "warnings": 0,
                "notices": 0, "ok_count": 0, "items": [], "raw_response": "",
                "elapsed_seconds": 0.0}

    # Briefing als Markdown serialisieren mit eindeutigen Marker pro Beitrag,
    # damit Claude die Sections sicher matchen kann.
    briefing_lines = []
    for idx, section in enumerate(checkable_sections, start=1):
        source_label = section.get("source_label") or "?"
        briefing_lines.append(f"=== BEITRAG {idx} (Quelle: {source_label}) ===")
        briefing_lines.append((section.get("content") or "").strip())
        briefing_lines.append("")
    briefing_block = "\n".join(briefing_lines)

    prompt_input = (
        "═══════════════════════════════════════════════════════════\n"
        "ORIGINAL-ROHDATEN (was Claude beim Briefing-Schreiben bekam)\n"
        "═══════════════════════════════════════════════════════════\n\n"
        f"{handoff_text}\n\n"
        "═══════════════════════════════════════════════════════════\n"
        "FERTIGES BRIEFING (zu prüfen)\n"
        "═══════════════════════════════════════════════════════════\n\n"
        f"{briefing_block}\n"
    )

    cmd = [
        cli,
        "--print",
        "--model", model,
        "--dangerously-skip-permissions",
        "--effort", "low",
        "--system-prompt", _CLAUDE_CLI_CONTENT_CHECK_SYSTEM_PROMPT,
    ]

    _report(f"Inhaltlicher Plausibilitäts-Check ({len(checkable_sections)} Beiträge) wird an Claude geschickt…", 0.05)

    stream_result = _run_claude_cli_subprocess_streaming(
        cmd,
        prompt_input,
        cwd=os.getcwd(),
        timeout_seconds=timeout_seconds,
        progress_callback=progress_callback,
        base_progress=0.05,
        max_progress=0.85,
        expected_duration_s=300.0,
        label=f"Claude {model} prüft Plausibilität",
    )

    if not stream_result.get("ok"):
        return {"ok": False, "error": f"Content-Check — {stream_result.get('error')}",
                "enabled": True, "mode": "warn", "checked": 0, "warnings": 0,
                "notices": 0, "items": [],
                "raw_response": stream_result.get("stdout", "")}

    stdout = stream_result["stdout"]
    stderr = stream_result["stderr"]
    elapsed = stream_result["elapsed"]
    returncode = stream_result["returncode"]

    if returncode != 0:
        err_short = (stderr or "").strip()[:600]
        out_short = (stdout or "").strip()[:600]
        msg = err_short or out_short or f"CLI-Exit-Code {returncode}"
        return {"ok": False, "error": f"Claude CLI Fehler (Content-Check): {msg}",
                "enabled": True, "mode": "warn", "checked": 0, "warnings": 0,
                "notices": 0, "items": [], "raw_response": stdout or ""}

    raw_response = (stdout or "").strip()
    if not raw_response:
        return {"ok": False, "error": "Claude hat eine leere Antwort geliefert.",
                "enabled": True, "mode": "warn", "checked": 0, "warnings": 0,
                "notices": 0, "items": [], "raw_response": ""}

    # JSON extrahieren — bevorzugt aus Codeblock, sonst aus erstem { bis letztes }
    parsed = None
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_response, flags=re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
    else:
        first = raw_response.find("{")
        last = raw_response.rfind("}")
        candidate = raw_response[first:last + 1] if first >= 0 and last > first else raw_response
    try:
        parsed = _json.loads(candidate)
    except Exception as exc:
        return {"ok": False,
                "error": f"Content-Check JSON-Parsing fehlgeschlagen: {exc}",
                "enabled": True, "mode": "warn", "checked": 0, "warnings": 0,
                "notices": 0, "items": [], "raw_response": raw_response}

    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        items = []

    # Sanitize + zähle nach (Claude's Selbstzählung könnte abweichen)
    clean_items = []
    warnings = 0
    notices = 0
    ok_count = 0
    for entry in items:
        if not isinstance(entry, dict):
            continue
        level = str(entry.get("level", "ok")).lower().strip()
        if level not in ("ok", "notice", "warn", "warning"):
            level = "ok"
        if level == "warning":
            level = "warn"
        clean_items.append({
            "section_index": entry.get("section_index"),
            "label": str(entry.get("label", "Beitrag"))[:200],
            "level": level,
            "summary": str(entry.get("summary", ""))[:500],
            "hard_issues": [str(s)[:300] for s in (entry.get("hard_issues") or []) if s][:3],
            "soft_issues": [str(s)[:300] for s in (entry.get("soft_issues") or []) if s][:3],
        })
        if level == "warn":
            warnings += 1
        elif level == "notice":
            notices += 1
        else:
            ok_count += 1

    return {
        "ok": True,
        "error": None,
        "enabled": True,
        "mode": "warn",
        "model": model,
        "checked": len(checkable_sections),
        "warnings": warnings,
        "notices": notices,
        "ok_count": ok_count,
        "items": clean_items,
        "raw_response": raw_response,
        "elapsed_seconds": elapsed,
        "usage": stream_result.get("usage"),
    }


def _run_genius_quality_check_via_cli(
    summary_text: str,
    content_sections: List[dict],
    *,
    model: str,
    cli_path: Optional[str],
    timeout_seconds: int = 300,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> dict:
    """LLM-Quality-Check der Kompaktfassung via Claude CLI.

    Identisch zu _llm_check_genius_summary aus dem API-Pfad, aber subprocess-basiert.
    Returns dict {issues, coverage_ok, cluster_ok, error}.
    """
    import json as _json

    cli = cli_path or _locate_claude_cli()
    if not cli or not summary_text:
        return {"issues": [], "coverage_ok": None, "cluster_ok": None, "error": "no_cli"}

    source_titles = []
    for i, s in enumerate(content_sections, 1):
        title = _markdown_line_to_plain(_extract_title(s.get("content", ""))).strip()
        src = s.get("source_label", "")
        typ = "Podcast" if s.get("type") == "podcast" else ("Wetter" if s.get("_weather") else "Artikel")
        source_titles.append(f"[{i}] {typ} ({src}): {title}")
    source_list = "\n".join(source_titles)

    check_input = (
        f"QUELLBEITRÄGE ({len(content_sections)} Stück):\n{source_list}\n\n"
        f"GENIALE ZUSAMMENFASSUNG ZUM PRÜFEN:\n{summary_text}"
    )

    cmd = [
        cli, "--print",
        "--model", model,
        "--dangerously-skip-permissions",
        "--effort", "low",
        "--system-prompt", _GENIUS_QUALITY_CHECK_PROMPT,
    ]

    stream_result = _run_claude_cli_subprocess_streaming(
        cmd, check_input,
        cwd=os.getcwd(),
        timeout_seconds=timeout_seconds,
        progress_callback=progress_callback,
        base_progress=0.78,
        max_progress=0.84,
        expected_duration_s=90.0,
        label=f"Claude {model} prüft Kompaktfassung",
    )

    if not stream_result.get("ok") or stream_result.get("returncode") != 0:
        err = stream_result.get("error") or f"CLI exit {stream_result.get('returncode')}"
        return {"issues": [], "coverage_ok": None, "cluster_ok": None, "error": err}

    raw = (stream_result.get("stdout") or "").strip()
    if not raw:
        return {"issues": [], "coverage_ok": None, "cluster_ok": None}
    m = re.search(r'\{[\s\S]*\}', raw)
    if not m:
        return {"issues": [], "coverage_ok": None, "cluster_ok": None}
    try:
        data = _json.loads(m.group(0))
        if not isinstance(data, dict):
            return {"issues": [], "coverage_ok": None, "cluster_ok": None, "usage": stream_result.get("usage")}
        if not isinstance(data.get("issues"), list):
            data["issues"] = []
        data["usage"] = stream_result.get("usage")
        return data
    except Exception:
        return {"issues": [], "coverage_ok": None, "cluster_ok": None, "usage": stream_result.get("usage")}


def _run_genius_repair_via_cli(
    summary_text: str,
    issues: List[dict],
    content_sections: List[dict],
    *,
    model: str,
    cli_path: Optional[str],
    timeout_seconds: int = 300,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> str:
    """Repair der Kompaktfassung via Claude CLI. Returns reparierten Text oder Original.

    Identisch zu _llm_repair_genius_summary aus dem API-Pfad.
    """
    if not issues:
        return summary_text
    cli = cli_path or _locate_claude_cli()
    if not cli:
        return summary_text

    source_context_lines = []
    for i, s in enumerate(content_sections, 1):
        title = _markdown_line_to_plain(_extract_title(s.get("content", ""))).strip()
        body = _section_plain_excerpt(s.get("content", ""), max_chars=400)
        source_context_lines.append(f"[{i}] {title}\n{body}")

    issue_list = "\n".join(
        f"- [{issue.get('beitrag','?')}] {issue.get('category','?')}: {issue.get('message','')} (Hinweis: {issue.get('fix_hint','')})"
        for issue in issues
    )
    repair_input = (
        f"ZU KORRIGIERENDE PROBLEME:\n{issue_list}\n\n"
        f"QUELLKONTEXT:\n" + "\n\n".join(source_context_lines[:50]) + "\n\n"
        f"AKTUELLE GENIALE ZUSAMMENFASSUNG:\n{summary_text}"
    )

    cmd = [
        cli, "--print",
        "--model", model,
        "--dangerously-skip-permissions",
        "--effort", "low",
        "--system-prompt", _GENIUS_REPAIR_PROMPT,
    ]

    stream_result = _run_claude_cli_subprocess_streaming(
        cmd, repair_input,
        cwd=os.getcwd(),
        timeout_seconds=timeout_seconds,
        progress_callback=progress_callback,
        base_progress=0.86,
        max_progress=0.94,
        expected_duration_s=120.0,
        label=f"Claude {model} repariert Kompaktfassung",
    )

    if not stream_result.get("ok") or stream_result.get("returncode") != 0:
        return summary_text

    repaired = (stream_result.get("stdout") or "").strip()
    # Plausibilität: reparierter Text darf nicht radikal kürzer sein
    if repaired and len(repaired) > len(summary_text) / 2:
        return repaired
    return summary_text


_CLAUDE_CLI_REPAIR_SYSTEM_PROMPT = """Du reparierst gezielt einzelne Beiträge eines deutschen Audio-Tagesbriefings.

DU BEKOMMST
- Die Original-Rohdaten (zur Faktenprüfung)
- Die aktuellen Beiträge im JSON-Format mit Index
- Eine Liste von Befunden pro Beitrag (Markdown-Struktur-Probleme und/oder inhaltliche Fakten-Probleme)

DEINE AUFGABE
Schreibe NUR die Beiträge neu, die in der Befund-Liste auftauchen. Andere Beiträge nicht ändern.

PRO BEITRAG
- Markdown-Struktur-Probleme: ergänze fehlende `### Titel`, `*Einordnung*`, `#### Was bleibt:`, „Weiter geht's.", entferne Boilerplate-Reste.
- Faktische Probleme: korrigiere Zahlen/Namen gegen den Original-Quelltext.
- Behalte Stil, Länge und alle anderen Inhalte des Beitrags so unverändert wie möglich.
- Reparierte Beiträge müssen vollständig sein (Titel, Einordnung, Body, Was bleibt, Endmarker).

ANTWORT
Antworte ausschließlich mit EINEM JSON-Codeblock (```json … ```), Struktur:

```json
{
  "repaired": [
    {
      "section_index": 7,
      "content": "### Neuer korrigierter Beitragsinhalt\\n\\n*Einordnung*\\n\\nFließtext…\\n\\n#### Was bleibt:\\n\\nKern.\\n\\nWeiter geht's."
    }
  ]
}
```

`section_index` ist der 1-basierte Index aus der Befund-Liste.
Newlines im content als `\\n` escapen.
Nur Beiträge auflisten, die du wirklich repariert hast.
Keine Erklärungen außerhalb des JSON-Blocks.
"""


def run_briefing_repair_via_claude_cli(
    handoff_text: str,
    briefing_sections: List[dict],
    output_lint: Optional[dict],
    content_check: Optional[dict],
    *,
    model: str = "sonnet",
    timeout_seconds: int = 900,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    cli_path: Optional[str] = None,
) -> dict:
    """Repariert Beiträge mit Lint- oder Content-Warnungen via Claude CLI.

    Sammelt alle betroffenen Sections + ihre Befunde, schickt einen fokussierten
    Repair-Auftrag an Claude. Antwort ist ein JSON mit reparierten Beiträgen,
    die selektiv ins Sections-Array eingespielt werden.

    Returns dict {ok, error, repaired_count, repaired_indices, sections (neu),
    raw_response, elapsed_seconds}.
    """
    import json as _json

    def _report(step: str, ratio: float):
        if progress_callback:
            try:
                progress_callback(step, max(0.0, min(1.0, float(ratio))))
            except Exception:
                pass

    cli = cli_path or _locate_claude_cli()
    if not cli:
        return {"ok": False, "error": "Claude CLI nicht gefunden.",
                "repaired_count": 0, "repaired_indices": [],
                "sections": briefing_sections, "raw_response": ""}

    # Filter prüfbare Sections (gleiches Filter wie Content-Check)
    checkable_sections = [
        s for s in briefing_sections
        if s.get("type") != "transition"
        and not s.get("_recap")
        and not s.get("_essenz")
        and not s.get("_verabschiedung")
        and not s.get("_preview")
        and not s.get("_ressort_header")
        and not s.get("_special")
    ]

    # Befunde pro 1-basiertem Index sammeln
    issues_by_idx: Dict[int, Dict[str, list]] = {}

    if output_lint and output_lint.get("items"):
        for item in output_lint["items"]:
            sev = item.get("severity") or "notice"
            if sev not in ("warning", "warn"):
                continue
            sec_idx = item.get("section_index")
            if sec_idx is None:
                continue
            # output_lint nutzt 0-basierten Index → +1 für die Konvention im Repair
            one_idx = int(sec_idx) + 1
            entry = issues_by_idx.setdefault(one_idx, {"lint": [], "content": []})
            msg = item.get("message", "")
            label = item.get("label", "")
            entry["lint"].append(f"{label}: {msg}" if label else msg)

    if content_check and content_check.get("items"):
        for item in content_check["items"]:
            level = item.get("level") or "ok"
            if level != "warn":
                continue
            sec_idx = item.get("section_index")
            if sec_idx is None:
                continue
            one_idx = int(sec_idx)
            entry = issues_by_idx.setdefault(one_idx, {"lint": [], "content": []})
            summary = item.get("summary", "")
            for hi in item.get("hard_issues") or []:
                entry["content"].append(f"{summary} — {hi}" if summary else hi)
            if not item.get("hard_issues") and summary:
                entry["content"].append(summary)

    if not issues_by_idx:
        return {"ok": True, "error": None, "repaired_count": 0,
                "repaired_indices": [], "sections": briefing_sections,
                "raw_response": "", "elapsed_seconds": 0.0,
                "skipped_reason": "keine Warnungen zum Reparieren"}

    # Affected sections + ihre Inhalte als Input vorbereiten
    affected_payload = []
    for one_idx in sorted(issues_by_idx.keys()):
        zero_idx = one_idx - 1
        if zero_idx < 0 or zero_idx >= len(checkable_sections):
            continue
        section = checkable_sections[zero_idx]
        affected_payload.append({
            "section_index": one_idx,
            "label": section.get("source_label") or "?",
            "current_content": (section.get("content") or "").strip(),
            "lint_issues": issues_by_idx[one_idx]["lint"],
            "content_issues": issues_by_idx[one_idx]["content"],
        })

    if not affected_payload:
        return {"ok": True, "error": None, "repaired_count": 0,
                "repaired_indices": [], "sections": briefing_sections,
                "raw_response": "", "elapsed_seconds": 0.0,
                "skipped_reason": "keine Sections gemappt"}

    prompt_input = (
        "═══════════════════════════════════════════════════════════\n"
        "ORIGINAL-ROHDATEN (Quelle für Faktenprüfung)\n"
        "═══════════════════════════════════════════════════════════\n\n"
        f"{handoff_text}\n\n"
        "═══════════════════════════════════════════════════════════\n"
        f"ZU REPARIERENDE BEITRÄGE ({len(affected_payload)} Stück, mit Befunden)\n"
        "═══════════════════════════════════════════════════════════\n\n"
        f"{_json.dumps(affected_payload, ensure_ascii=False, indent=2)}\n"
    )

    cmd = [
        cli,
        "--print",
        "--model", model,
        "--dangerously-skip-permissions",
        "--effort", "low",
        "--system-prompt", _CLAUDE_CLI_REPAIR_SYSTEM_PROMPT,
    ]

    _report(f"Auto-Repair: {len(affected_payload)} Beitrag/Beiträge wird/werden an Claude geschickt…", 0.05)

    stream_result = _run_claude_cli_subprocess_streaming(
        cmd,
        prompt_input,
        cwd=os.getcwd(),
        timeout_seconds=timeout_seconds,
        progress_callback=progress_callback,
        base_progress=0.05,
        max_progress=0.85,
        expected_duration_s=120.0,
        label=f"Claude {model} repariert Beiträge",
    )

    if not stream_result.get("ok"):
        return {"ok": False, "error": f"Auto-Repair — {stream_result.get('error')}",
                "repaired_count": 0, "repaired_indices": [],
                "sections": briefing_sections,
                "raw_response": stream_result.get("stdout", "")}

    stdout = stream_result["stdout"]
    stderr = stream_result["stderr"]
    elapsed = stream_result["elapsed"]
    returncode = stream_result["returncode"]

    if returncode != 0:
        err_short = (stderr or "").strip()[:600]
        out_short = (stdout or "").strip()[:600]
        return {"ok": False, "error": f"Claude CLI Fehler (Repair): {err_short or out_short or returncode}",
                "repaired_count": 0, "repaired_indices": [],
                "sections": briefing_sections, "raw_response": stdout or ""}

    raw_response = (stdout or "").strip()
    if not raw_response:
        return {"ok": False, "error": "Auto-Repair: Claude hat eine leere Antwort geliefert.",
                "repaired_count": 0, "repaired_indices": [],
                "sections": briefing_sections, "raw_response": ""}

    # JSON parsen (gleiche Logik wie content_check)
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_response, flags=re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
    else:
        first = raw_response.find("{")
        last = raw_response.rfind("}")
        candidate = raw_response[first:last + 1] if first >= 0 and last > first else raw_response
    try:
        parsed = _json.loads(candidate)
    except Exception as exc:
        return {"ok": False, "error": f"Auto-Repair JSON-Parsing fehlgeschlagen: {exc}",
                "repaired_count": 0, "repaired_indices": [],
                "sections": briefing_sections, "raw_response": raw_response}

    repaired_list = parsed.get("repaired") if isinstance(parsed, dict) else None
    if not isinstance(repaired_list, list) or not repaired_list:
        return {"ok": True, "error": None, "repaired_count": 0,
                "repaired_indices": [], "sections": briefing_sections,
                "raw_response": raw_response, "elapsed_seconds": elapsed,
                "skipped_reason": "Claude hat nichts zu reparieren gefunden"}

    # Reparierte Beiträge ins clean_sections-Array einspielen
    # checkable_sections-Liste hat einen 1-basierten Index, der mit dem
    # ursprünglichen briefing_sections-Index nicht direkt identisch ist.
    # Wir nutzen die Identität-Referenz (jedes section ist ein dict, das auch in
    # briefing_sections steht).
    new_sections = list(briefing_sections)
    repaired_indices: List[int] = []
    for entry in repaired_list:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("section_index")
        new_content = entry.get("content")
        if idx is None or not isinstance(new_content, str) or not new_content.strip():
            continue
        try:
            zero_idx = int(idx) - 1
        except Exception:
            continue
        if zero_idx < 0 or zero_idx >= len(checkable_sections):
            continue
        target_section = checkable_sections[zero_idx]
        # Im ursprünglichen briefing_sections via Identität finden
        for i, original in enumerate(new_sections):
            if original is target_section:
                # Sanitize neuen Content wie im Hauptpfad
                try:
                    cleaned = _sanitize_briefing_output(_normalize_existing_briefing_markdown(new_content))
                    cleaned = tts_safe(cleaned)
                except Exception:
                    cleaned = new_content
                new_section = dict(original)
                new_section["content"] = cleaned
                new_sections[i] = new_section
                repaired_indices.append(idx)
                break

    return {
        "ok": True,
        "error": None,
        "repaired_count": len(repaired_indices),
        "repaired_indices": repaired_indices,
        "sections": new_sections,
        "raw_response": raw_response,
        "elapsed_seconds": elapsed,
        "usage": stream_result.get("usage"),
    }


_CLAUDE_CLI_GENIUS_RESPONSE_PROMPT = """Du bekommst gleich ein fertiges deutsches Audio-Briefing. Daraus sollst du eine Kompaktfassung schreiben.

Im System-Prompt unten stehen die genauen Anweisungen zu Format, Länge und Struktur. Halte dich exakt daran.

Antworte ausschließlich mit dem fertigen Markdown-Text der Kompaktfassung — ohne Vorrede, ohne Erklärungen davor oder danach, ohne JSON-Wrapper. Beginne direkt mit `### Kompakte Vollzusammenfassung`.

ZUSÄTZLICH — TOP-3-VORSCHAU: Direkt nach der kursiven Einleitungszeile füge diesen Abschnitt ein (vor den [N]-Einträgen):

#### Die drei wichtigsten Themen heute

1. Ein prägnanter Satz zur wichtigsten Story des Tages (mit Namen/Zahl).
2. Ein prägnanter Satz zur zweitwichtigsten Story.
3. Ein prägnanter Satz zur drittwichtigsten Story.

Wähle nach Tragweite und Relevanz für den Hörer, nicht nach Reihenfolge im Briefing.
"""


def _strip_recap_brackets(text: str) -> str:
    """Entfernt führende [N]-Marker am Zeilenanfang.

    Bei sehr vielen Beiträgen im Lang-Modus weicht Opus gelegentlich vom strikten
    [N]-Format ab und schreibt Fließtext. Damit solche (intelligenten) Fassungen
    natürlich vorgelesen werden, schneiden wir verbliebene „[5] "-Präfixe weg.
    """
    if not text:
        return text
    return re.sub(r"(?m)^[ \t>*-]*\[(\d+)\]\s+", "", text)


def _genius_raw_output_usable(text: str, mode: str = "standard") -> bool:
    """True, wenn Opus' Rohfassung strukturell eine echte Kompaktfassung ist.

    Dann ist sie der bessere (intelligente) Inhalt als das rohe deterministische
    Netz — auch wenn die strikte [N]-Validierung scheiterte (z.B. weil Opus bei
    80+ Beiträgen im Lang-Modus zu Fließtext ohne [N] gewechselt ist).
    """
    if not text:
        return False
    t = text.strip()
    if "Kompakte Vollzusammenfassung" not in t:
        return False
    # Mindestumfang: echte Zusammenfassung, kein Stub/Fehler/Refusal
    return len(t) >= 600 and t.count(".") >= 8


def _clean_pseudo_for_fallback(sections: List[dict]) -> List[dict]:
    """Bereinigt Pseudo-Sections (Direct-Modus) fürs deterministische Netz:
    entfernt die kursive „*Quelle: …*"/„*Paywall-Artikel*"-Zeile, damit der
    Quelle-Hinweis nicht mitten im vorgelesenen Artikeltext auftaucht
    (die Überschrift trägt die Quelle bereits)."""
    out: List[dict] = []
    for s in sections:
        c = dict(s)
        content = c.get("content", "") or ""
        content = re.sub(
            r"(?m)^\*(?:Quelle:[^\n]*|Paywall-Artikel|Aktuelle Lage)\*\s*$",
            "",
            content,
        )
        c["content"] = content
        out.append(c)
    return out


def _enrich_section_titles_via_cli(sections, text_by_idx, cli_path=None, model="sonnet", progress_callback=None):
    """Erzeugt knackige Schlagzeilen für die Überschriften und ersetzt die Section-Titel.

    text_by_idx: {orig_idx (1-basiert) -> Text, aus dem die Schlagzeile gebaut wird}.
    WICHTIG: Dieser Text MUSS derselbe sein, der später als Fließtext angezeigt wird
    (Opus' Eintrag [orig_idx]). Nur so passen Überschrift und Text IMMER zusammen —
    egal ob Opus' [N]-Nummerierung mit der Eingabereihenfolge übereinstimmt.

    EIN günstiger Sonnet-Call für alle Beiträge (schont Opus-Quota). Konservativ:
    nur anwenden, wenn ≥80% sauber erkannt; sonst Titel unverändert lassen (kein Risiko).
    """
    if not sections or not text_by_idx:
        return sections
    cli = cli_path or _locate_claude_cli()
    if not cli:
        return sections
    order = sorted(
        i for i in text_by_idx
        if 1 <= i <= len(sections) and (text_by_idx[i] or "").strip()
    )
    if not order:
        return sections
    items = []
    for i in order:
        _t = re.sub(
            r'^[\(\[]\s*(?:Quelle:?\s*)?[^)\]\n]{2,40}?\s*[\)\]]\s*[—–:.\-]?\s*',
            '', (text_by_idx[i] or ''),
        )
        items.append(f"[{i}] {' '.join(_t.split())[:450]}")
    prompt_input = "\n\n".join(items)
    system_prompt = (
        f"Du bekommst {len(items)} kurze Nachrichtentexte, jeweils mit [N] nummeriert. "
        "Schreibe für JEDEN eine knackige, vollständige deutsche Schlagzeile: 4-9 Wörter, "
        "aktiv und konkret, mit den wichtigsten Namen/Orten. KEINE Quelle, KEIN abschließender "
        "Punkt, keine Anführungszeichen. Antworte AUSSCHLIESSLICH als Liste, exakt eine Zeile "
        "pro Beitrag im Format `[N] Schlagzeile`, und verwende GENAU DIESELBEN Nummern wie in "
        "der Eingabe. Keine Vorrede, keine Erklärungen."
    )
    cmd = [cli, "--print", "--output-format", "text", "--model", model,
           "--dangerously-skip-permissions", "--effort", "low",
           "--system-prompt", system_prompt]
    try:
        res = _run_claude_cli_subprocess_streaming(
            cmd, prompt_input, timeout_seconds=300,
            progress_callback=progress_callback,
            label=f"Claude {model} schreibt Schlagzeilen",
            expected_duration_s=45.0, base_progress=0.74, max_progress=0.78,
            prefer_full_text=True,
        )
        if not res.get("ok"):
            return sections
        raw = (res.get("stdout") or "").strip()
        titles = {}
        for m in re.finditer(r"(?m)^\s*\[(\d+)\]\s+(.+?)\s*$", raw):
            idx = int(m.group(1))
            head = m.group(2).strip().strip('"„""').rstrip(" .")
            if idx in text_by_idx and 1 <= idx <= len(sections) and 4 <= len(head) <= 110:
                titles[idx] = head
        if len(titles) < max(1, int(len(order) * 0.8)):
            print(f"[titles] nur {len(titles)}/{len(order)} Schlagzeilen erkannt — behalte bisherige Titel.", file=sys.stderr)
            return sections
        for i in order:
            if i in titles:
                sections[i - 1]["content"] = re.sub(
                    r"^###[^\n]*", f"### {titles[i]}", sections[i - 1].get("content", ""), count=1
                )
        print(f"[titles] {len(titles)} Schlagzeilen erzeugt ({model}) — aus Opus-Einträgen, passgenau.", file=sys.stderr)
    except Exception as exc:
        print(f"[titles] Schlagzeilen-Schritt übersprungen: {exc}", file=sys.stderr)
    return sections


def _save_debug_raw_response(output_pdf_path: str, raw_response: str) -> Optional[str]:
    """Speichert Claudes Rohantwort als Debug-Datei — bewusst NICHT ins iCloud-Archiv
    (würde den sauberen Briefings-Ordner zumüllen), sondern in den versteckten lokalen
    Mirror `~/.briefing_meta_mirror/debug/`. Nur im Fehlerfall aufrufen."""
    try:
        debug_dir = _LOCAL_META_MIRROR_DIR / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        stem = os.path.basename(output_pdf_path or "briefing.pdf")
        if stem.endswith(".pdf"):
            stem = stem[:-4]
        path = debug_dir / f"{stem}_raw_response.txt"
        path.write_text(raw_response or "", encoding="utf-8")
        return str(path)
    except Exception:
        return None


def run_genius_summary_via_claude_cli(
    briefing_sections: List[dict],
    output_pdf_path: str,
    *,
    mode: str = "long",
    model: str = "sonnet",
    timeout_seconds: int = 1500,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    cli_path: Optional[str] = None,
    quality_lint: bool = True,
) -> dict:
    """Erzeugt eine Kompaktfassung (ehem. „Geniale Zusammenfassung") via Claude CLI.

    Nutzt die fertigen Briefing-Sections als Input und ruft Claude mit dem
    passenden GENIUS_SUMMARY_PROMPT auf. Output wird durch die gleiche
    Format/Lint/PDF-Pipeline geschickt wie der API-Pfad.

    Parameters
    ----------
    briefing_sections : List[dict]
        Bereits-fertige Sections aus dem Voll-Briefing (CLI- oder API-Pfad).
    output_pdf_path : str
        Zielpfad für die Kompakt-PDF.
    mode : str
        "long" (Default), "standard" oder "short".
    model : str
        Claude-Modell (Default: "sonnet").
    quality_lint : bool
        Ob nach der LLM-Antwort der lokale Lint laufen soll. Default an —
        repariert kleine Format-Fehler ohne weitere LLM-Calls.

    Returns
    -------
    dict mit {ok, output_pdf_path, mode, error, elapsed_seconds, raw_response, meta}.
    """
    def _report(step: str, ratio: float):
        if progress_callback:
            try:
                progress_callback(step, max(0.0, min(1.0, ratio)))
            except Exception:
                pass

    if not briefing_sections:
        return {"ok": False, "error": "Keine Briefing-Sections übergeben.",
                "output_pdf_path": None, "mode": mode, "raw_response": ""}

    # Content-Sections filtern (Recap/Essenz/Verabschiedung sind Meta — nicht Teil der Kompaktfassung)
    content_sections = [
        s for s in briefing_sections
        if s.get("type") != "transition"
        and not s.get("_recap")
        and not s.get("_essenz")
        and not s.get("_verabschiedung")
        and not s.get("_preview")
        and not s.get("_ressort_header")
    ]
    if not content_sections:
        return {"ok": False, "error": "Keine inhaltlichen Sections für die Kompaktfassung gefunden.",
                "output_pdf_path": None, "mode": mode, "raw_response": ""}

    cli = cli_path or _locate_claude_cli()
    if not cli:
        return {"ok": False, "error": "Claude CLI nicht gefunden.",
                "output_pdf_path": None, "mode": mode, "raw_response": ""}

    normalized_mode = _normalize_genius_summary_mode(mode)
    system_prompt = _genius_summary_prompt_for_mode(normalized_mode)
    prompt_input = _build_genius_summary_input(content_sections)

    _report(f"Kompaktfassung wird an Claude {model} geschickt…", 0.05)

    cmd = [
        cli,
        "--print",
        "--output-format", "text",
        "--model", model,
        "--dangerously-skip-permissions",
        "--effort", "low",
        "--system-prompt", system_prompt,
        "--append-system-prompt", _CLAUDE_CLI_GENIUS_RESPONSE_PROMPT,
    ]

    # Erwartete Dauer: ~3 Min (180s) für Kompaktfassung
    stream_result = _run_claude_cli_subprocess_streaming(
        cmd,
        prompt_input,
        cwd=os.path.dirname(os.path.abspath(output_pdf_path)) or os.getcwd(),
        timeout_seconds=timeout_seconds,
        progress_callback=progress_callback,
        base_progress=0.08,
        max_progress=0.70,
        expected_duration_s=180.0,
        label=f"Claude {model} schreibt Kompaktfassung",
        prefer_full_text=True,
    )

    if not stream_result.get("ok"):
        return {"ok": False, "error": f"Kompaktfassung — {stream_result.get('error')}",
                "output_pdf_path": None, "mode": mode, "raw_response": stream_result.get("stdout", "")}

    stdout = stream_result["stdout"]
    stderr = stream_result["stderr"]
    elapsed = stream_result["elapsed"]
    returncode = stream_result["returncode"]
    _report(f"Kompaktfassung von Claude empfangen ({elapsed:.0f}s)…", 0.74)

    if returncode != 0:
        err_short = (stderr or "").strip()[:600]
        out_short = (stdout or "").strip()[:600]
        msg = err_short or out_short or f"CLI-Exit-Code {returncode}"
        return {"ok": False, "error": f"Claude CLI Fehler (Kompakt): {msg}",
                "output_pdf_path": None, "mode": mode, "raw_response": stdout or ""}

    raw_response = (stdout or "").strip()
    if not raw_response:
        return {"ok": False, "error": "Claude hat eine leere Antwort geliefert (Kompakt).",
                "output_pdf_path": None, "mode": mode, "raw_response": ""}

    # Manche Modelle hängen Vorrede dran — Kompaktfassung beginnt mit ### Kompakte Vollzusammenfassung
    marker = "### Kompakte Vollzusammenfassung"
    if marker in raw_response:
        idx = raw_response.find(marker)
        if idx > 0:
            raw_response = raw_response[idx:]

    # TOP-3-VORSCHAU herauslösen (die Format-Pipeline kennt nur [N]-Einträge und
    # würde den Block verwerfen) — wird nach dem Formatieren wieder vorn eingesetzt.
    top3_block = ""
    _m3 = re.search(r"####\s*Die drei wichtigsten Themen heute[\s\S]*?(?=\n\s*\[|\n####|\n###|\Z)", raw_response)
    if _m3:
        top3_block = _m3.group(0).strip()
        raw_response = raw_response.replace(_m3.group(0), "\n", 1)

    # HYBRID-REPARATUR: Fehlen einzelne [N]-Einträge im LLM-Output (bei 80+ Beiträgen
    # lässt das Modell gelegentlich 1-3 aus), werden NUR diese deterministisch ergänzt —
    # der intelligente Text bleibt erhalten. Vorher verwarf EIN fehlender Eintrag ALLES
    # („Sicherheitsnetz aktiv" = komplette deterministische Fassung).
    hybrid_filled = 0
    try:
        _found_ids = set(_extract_recap_ids(raw_response))
        _missing_ids = [i for i in range(1, len(content_sections) + 1) if i not in _found_ids]
        if _missing_ids and len(_missing_ids) <= max(3, len(content_sections) // 2):
            _fills = "\n".join(
                f"[{i}] {_build_genius_entry_text(content_sections[i - 1], normalized_mode)}"
                for i in _missing_ids
            )
            _pos = raw_response.find("#### Was du mitnehmen")
            if _pos > 0:
                raw_response = raw_response[:_pos].rstrip() + "\n" + _fills + "\n\n" + raw_response[_pos:]
            else:
                raw_response = raw_response.rstrip() + "\n" + _fills + "\n"
            hybrid_filled = len(_missing_ids)
            print(f"[genius] Hybrid-Reparatur: {hybrid_filled} fehlende Einträge ergänzt (IDs {_missing_ids[:10]}) — Opus-Text bleibt erhalten.", file=sys.stderr)
    except Exception as _hx:
        print(f"[genius] Hybrid-Reparatur übersprungen: {_hx}", file=sys.stderr)

    # WICHTIG: Validierung auf der ROHEN [N]-Antwort (VOR dem Formatieren) — genau wie
    # der API-Pfad. Der Formatter wandelt [N] → „Artikel N von M"-Überschriften um;
    # auf dem formatierten Text fände _extract_recap_ids NICHTS und würde IMMER ins
    # deterministische Sicherheitsnetz fallen. (Bug bis 13.06.: die intelligente
    # Opus-Kompaktfassung ging auf dem CLI-Pfad dadurch faktisch immer verloren.)
    used_fallback = False
    skip_qc = False
    soft_intelligent = False
    valid_ids = False
    valid_coverage = False
    _id_problems = ""
    _cov_problems = ""
    try:
        valid_ids, _id_problems = _validate_recap_ids(raw_response, len(content_sections))
        if valid_ids:
            valid_coverage, _cov_problems = _validate_genius_summary_coverage(raw_response, content_sections)
    except Exception:
        pass

    if not valid_ids or not valid_coverage:
        print(f"[genius] Validierung fehlgeschlagen (ids_ok={valid_ids} {_id_problems!r} | coverage_ok={valid_coverage} {_cov_problems!r}) → prüfe Opus-Rohfassung.", file=sys.stderr)
        # Roh-Antwort fürs Debuggen sichern (nur im Fehlerfall, in den versteckten
        # lokalen Debug-Ordner — NICHT ins iCloud-Archiv).
        _save_debug_raw_response(output_pdf_path, raw_response)
        _found = _extract_recap_ids(raw_response)
        if len(_found) >= max(1, int(len(content_sections) * 0.6)):
            # Genug [N]-Einträge vorhanden → strukturiert formatieren (beste Lesbarkeit).
            try:
                summary = _format_genius_summary_output(raw_response, content_sections, normalized_mode, all_sections=briefing_sections)
            except Exception:
                summary = _strip_recap_brackets(raw_response)
            soft_intelligent = True
            print(f"[genius] → {len(_found)}/{len(content_sections)} [N]-Einträge da, intelligente Fassung strukturiert verwendet.", file=sys.stderr)
        elif _genius_raw_output_usable(raw_response, normalized_mode):
            # Opus hat (bei sehr vielen Beiträgen im Lang-Modus) zu Fließtext ohne [N] gewechselt —
            # trotzdem die INTELLIGENTE Fassung nehmen, nicht das rohe deterministische Netz.
            summary = _strip_recap_brackets(raw_response)
            soft_intelligent = True
            skip_qc = True
            print("[genius] → intelligente Opus-Fließtextfassung verwendet (kein deterministisches Netz).", file=sys.stderr)
        else:
            # Rohantwort wirklich unbrauchbar (leer/Fehler/Stub) → deterministisches Netz.
            try:
                fallback = _build_deterministic_genius_summary(briefing_sections, mode=normalized_mode)
                summary = fallback if fallback else _strip_recap_brackets(raw_response)
                used_fallback = bool(fallback)
            except Exception:
                summary = _strip_recap_brackets(raw_response)
            print("[genius] → deterministisches Netz (Rohfassung unbrauchbar).", file=sys.stderr)
        # Top-3 in die Soft-Fassung einsetzen
        if soft_intelligent and top3_block:
            _mintro = re.search(r"^###[^\n]*\n+\*[^\n]*\*\n", summary)
            if _mintro:
                summary = summary[:_mintro.end()] + "\n" + top3_block + "\n" + summary[_mintro.end():]
            else:
                summary = top3_block + "\n\n" + summary
    else:
        # Intelligente Opus-Fassung formatieren (Bucket-Sortierung, „Was du mitnehmen kannst", Abschluss)
        _report("Kompaktfassung wird nachformatiert…", 0.80)
        try:
            summary = _format_genius_summary_output(raw_response, content_sections, normalized_mode, all_sections=briefing_sections)
        except Exception:
            summary = raw_response
        # Top-3-Vorschau nach Haupttitel + kursiver Einleitung einsetzen
        if top3_block:
            _mintro = re.search(r"^###[^\n]*\n+\*[^\n]*\*\n", summary)
            if _mintro:
                summary = summary[:_mintro.end()] + "\n" + top3_block + "\n" + summary[_mintro.end():]
            else:
                summary = top3_block + "\n\n" + summary

    if quality_lint and not used_fallback and not skip_qc:
        try:
            lint = _lint_genius_summary(summary, len(content_sections))
            if lint.get("auto_repairs"):
                summary = lint.get("repaired_text") or summary
        except Exception:
            pass

    # === LLM-Quality-Check + Auto-Repair via CLI ===
    # Nur wenn der primäre/strukturierte Pfad gelaufen ist (kein deterministisches Netz,
    # keine [N]-lose Fließtextfassung — dort hätte der ID-basierte Check keine Basis).
    quality_issues: List[dict] = []
    quality_repair_applied = False
    if not used_fallback and not skip_qc:
        try:
            _report("Kompakt-Qualitätscheck via Claude…", 0.78)
            qc = _run_genius_quality_check_via_cli(
                summary, content_sections,
                model=_CLI_JUDGE_MODEL, cli_path=cli,
                progress_callback=progress_callback,
            )
            quality_issues = qc.get("issues") or []
            if quality_issues:
                _report(f"Kompaktfassung wird repariert ({len(quality_issues)} Probleme)…", 0.86)
                repaired = _run_genius_repair_via_cli(
                    summary, quality_issues, content_sections,
                    model=_CLI_JUDGE_MODEL, cli_path=cli,
                    progress_callback=progress_callback,
                )
                if repaired and repaired != summary:
                    # Final lokalen Lint nochmal drüberlaufen lassen
                    try:
                        lint2 = _lint_genius_summary(repaired, len(content_sections))
                        summary = lint2.get("repaired_text") or repaired
                    except Exception:
                        summary = repaired
                    quality_repair_applied = True
        except Exception as exc:
            print(f"[genius-cli-quality] Kompakt-Qualitätscheck fehlgeschlagen: {exc}", file=sys.stderr)

    summary = tts_safe(summary)

    # PDF bauen
    _report("Kompakt-PDF wird gebaut…", 0.92)
    try:
        title_map = {
            "long": "Geniale Zusammenfassung (Lang)",
            "short": "Geniale Zusammenfassung (Kurz)",
            "standard": "Geniale Zusammenfassung",
        }
        document_title = title_map.get(normalized_mode, "Geniale Zusammenfassung")
        pdf_bytes = create_single_markdown_pdf(
            summary,
            datetime.datetime.now(),
            document_title=document_title,
        )
        if not pdf_bytes:
            return {"ok": False, "error": "PDF-Erstellung lieferte leere Bytes.",
                    "output_pdf_path": None, "mode": mode, "raw_response": raw_response}
        with open(output_pdf_path, "wb") as fp:
            fp.write(pdf_bytes)
    except Exception as exc:
        return {"ok": False, "error": f"PDF-Erstellung fehlgeschlagen: {exc}",
                "output_pdf_path": None, "mode": mode, "raw_response": raw_response}

    _report(f"Kompaktfassung fertig ({elapsed:.0f}s)", 1.0)

    meta = {
        "mode": normalized_mode,
        "mode_label": "Lang" if normalized_mode == "long" else ("Kurz" if normalized_mode == "short" else "Standard"),
        "used_fallback": used_fallback,
        "soft_intelligent": soft_intelligent,
        "ids_ok": valid_ids,
        "coverage_ok": valid_coverage,
        "total": len(content_sections),
        "quality_issues": quality_issues,
        "quality_repair_applied": quality_repair_applied,
        "hybrid_filled": hybrid_filled,
    }

    return {
        "ok": True,
        "error": None,
        "output_pdf_path": output_pdf_path,
        "mode": normalized_mode,
        "raw_response": raw_response,
        "summary_text": summary,
        "elapsed_seconds": elapsed,
        "meta": meta,
        "usage": stream_result.get("usage"),
        "cost_usd": stream_result.get("cost_usd"),
    }


def run_direct_genius_via_claude_cli(
    handoff_text: str,
    output_pdf_path: str,
    *,
    mode: str = "long",
    model: str = "sonnet",
    timeout_seconds: int = 1800,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    cli_path: Optional[str] = None,
    quality_lint: bool = True,
) -> dict:
    """Direkt-Kompaktfassung aus Rohdaten via Claude CLI — überspringt das Voll-Briefing.

    Spart ~30–40% Token + Wartezeit gegenüber Voll→Kompakt-Pipeline. Nutzt einen
    eigenen System-Prompt (`_GENIUS_DIRECT_FROM_RAW_PROMPT_*`), der direkt aus
    Rohdaten (Wetter+Artikel+Paywall+Podcast) eine Kompaktfassung schreibt.

    Pseudo-Sections aus dem Handoff werden für die Format-Pipeline und den
    Quality-Check + Repair genutzt — gleiche Qualitäts-Sicherung wie der
    Voll→Kompakt-Pfad.

    Returns dict {ok, output_pdf_path, mode, error, elapsed_seconds, raw_response, meta}.
    """
    def _report(step: str, ratio: float):
        if progress_callback:
            try:
                progress_callback(step, max(0.0, min(1.0, float(ratio))))
            except Exception:
                pass

    if not handoff_text or not handoff_text.strip():
        return {"ok": False, "error": "Handoff-Text ist leer.",
                "output_pdf_path": None, "mode": mode, "raw_response": ""}

    cli = cli_path or _locate_claude_cli()
    if not cli:
        return {"ok": False, "error": "Claude CLI nicht gefunden.",
                "output_pdf_path": None, "mode": mode, "raw_response": ""}

    normalized_mode = _normalize_genius_summary_mode(mode)

    # Pseudo-Sections aus Handoff extrahieren — werden für Format-Pipeline,
    # Quality-Check und Repair benötigt.
    _report("Pseudo-Sections werden aus Rohdaten extrahiert…", 0.02)
    pseudo_sections = _extract_pseudo_sections_from_handoff(handoff_text)
    if not pseudo_sections:
        return {"ok": False,
                "error": "Konnte keine Beiträge aus dem Handoff-Text extrahieren.",
                "output_pdf_path": None, "mode": mode, "raw_response": ""}

    system_prompt = _genius_direct_prompt_for_mode(normalized_mode)

    # Der Direkt-Modus muss Verstehen + Übersetzen + Kuratieren in EINEM Durchgang
    # leisten (der Voll-Weg teilt das auf einen separaten Umschreib-Schritt auf).
    # Mit Opus heben wir den Aufwand daher auf "medium", damit die einmalige
    # Roh-zu-Kompakt-Verdichtung dieselbe Qualität wie der Voll-Weg erreicht.
    # Sonnet bleibt aus Kostengründen bei "low".
    _direct_effort = "medium" if (model or "").lower() == "opus" else "low"

    cmd = [
        cli,
        "--print",
        "--output-format", "text",
        "--model", model,
        "--dangerously-skip-permissions",
        "--effort", _direct_effort,
        "--system-prompt", system_prompt,
        "--append-system-prompt", _CLAUDE_CLI_GENIUS_RESPONSE_PROMPT,
    ]

    _report(f"Direkt-Kompaktfassung wird an Claude {model} geschickt ({len(pseudo_sections)} Beiträge)…", 0.05)

    stream_result = _run_claude_cli_subprocess_streaming(
        cmd,
        handoff_text,
        cwd=os.path.dirname(os.path.abspath(output_pdf_path)) or os.getcwd(),
        timeout_seconds=timeout_seconds,
        progress_callback=progress_callback,
        base_progress=0.08,
        max_progress=0.70,
        # Direkt-Modus ist genauso aufwändig wie Voll-Briefing — Claude muss
        # alle Quelltexte lesen und verarbeiten. Bei 60+ Beiträgen rechnen wir
        # mit 12-20 Min realistisch.
        expected_duration_s=720.0,
        label=f"Claude {model} schreibt Direkt-Kompaktfassung",
        prefer_full_text=True,
    )

    if not stream_result.get("ok"):
        return {"ok": False, "error": f"Direkt-Kompakt — {stream_result.get('error')}",
                "output_pdf_path": None, "mode": mode,
                "raw_response": stream_result.get("stdout", "")}

    stdout = stream_result["stdout"]
    stderr = stream_result["stderr"]
    elapsed = stream_result["elapsed"]
    returncode = stream_result["returncode"]
    _report(f"Antwort empfangen ({elapsed:.0f}s) — wird formatiert…", 0.74)

    if returncode != 0:
        err_short = (stderr or "").strip()[:600]
        out_short = (stdout or "").strip()[:600]
        msg = err_short or out_short or f"CLI-Exit-Code {returncode}"
        return {"ok": False, "error": f"Claude CLI Fehler (Direkt-Kompakt): {msg}",
                "output_pdf_path": None, "mode": mode, "raw_response": stdout or ""}

    raw_response = (stdout or "").strip()
    if not raw_response:
        return {"ok": False, "error": "Claude hat eine leere Antwort geliefert.",
                "output_pdf_path": None, "mode": mode, "raw_response": ""}

    # Vorrede abschneiden falls vorhanden
    marker = "### Kompakte Vollzusammenfassung"
    if marker in raw_response:
        idx = raw_response.find(marker)
        if idx > 0:
            raw_response = raw_response[idx:]

    # Top-3-Vorschau herauslösen (Format-Pipeline würde sie verwerfen), später vorn einsetzen
    top3_block = ""
    _m3 = re.search(r"####\s*Die drei wichtigsten Themen heute[\s\S]*?(?=\n\s*\[|\n####|\n###|\Z)", raw_response)
    if _m3:
        top3_block = _m3.group(0).strip()
        raw_response = raw_response.replace(_m3.group(0), "\n", 1)

    # Schöne Schlagzeilen für die Überschriften — die Schlagzeile MUSS aus demselben Text
    # gebaut werden, der später als Fließtext erscheint, sonst passt sie nicht:
    #  - Artikel/Wetter: das ist Opus' Eintrag[N] (entry_map).
    #  - PODCASTS: das ist der VOLLTEXT der Section (der Formatter zeigt Podcasts in voller
    #    Länge aus der Section, NICHT aus entry_map) → sonst Titel↔Text-Mismatch (Bug 14.06.).
    try:
        _entry_map_for_titles = _extract_recap_entry_map(raw_response)
        _texts_for_titles = {}
        for _idx, _sec in enumerate(pseudo_sections, 1):
            if _sec.get("type") == "podcast":
                _txt = _build_podcast_full_text(_sec)
            else:
                _txt = _entry_map_for_titles.get(_idx, "")
            if _txt and _txt.strip():
                _texts_for_titles[_idx] = _txt
        if _texts_for_titles:
            _report("Schlagzeilen werden erzeugt…", 0.74)
            pseudo_sections = _enrich_section_titles_via_cli(
                pseudo_sections, _texts_for_titles, cli_path=cli, progress_callback=progress_callback
            )
    except Exception as _tx:
        print(f"[titles] übersprungen: {_tx}", file=sys.stderr)

    # Validierung auf der ROHEN [N]-Antwort VOR dem Formatieren (siehe Voll→Kompakt-Pfad:
    # auf dem formatierten „Artikel N"-Text fände _extract_recap_ids nichts → immer Fallback).
    used_fallback = False
    skip_qc = False
    soft_intelligent = False
    valid_ids = False
    valid_coverage = False
    try:
        valid_ids, _ = _validate_recap_ids(raw_response, len(pseudo_sections))
        if valid_ids:
            valid_coverage, _ = _validate_genius_summary_coverage(raw_response, pseudo_sections)
    except Exception:
        pass

    if not valid_ids or not valid_coverage:
        print(f"[genius-direct] Validierung fehlgeschlagen (ids_ok={valid_ids}, coverage_ok={valid_coverage}) → prüfe Opus-Rohfassung.", file=sys.stderr)
        # Roh-Antwort fürs Debuggen sichern (nur im Fehlerfall, in den versteckten
        # lokalen Debug-Ordner — NICHT ins iCloud-Archiv).
        _save_debug_raw_response(output_pdf_path, raw_response)
        _found = _extract_recap_ids(raw_response)
        if len(_found) >= max(1, int(len(pseudo_sections) * 0.6)):
            # Genug [N]-Einträge vorhanden → strukturiert formatieren (beste Lesbarkeit).
            try:
                summary = _format_genius_summary_output(raw_response, pseudo_sections, normalized_mode, all_sections=pseudo_sections, direct_mode=True)
            except Exception:
                summary = _strip_recap_brackets(raw_response)
            soft_intelligent = True
            print(f"[genius-direct] → {len(_found)}/{len(pseudo_sections)} [N]-Einträge da, intelligente Fassung strukturiert verwendet.", file=sys.stderr)
        elif _genius_raw_output_usable(raw_response, normalized_mode):
            # Opus hat bei sehr vielen Beiträgen (Lang-Modus) zu Fließtext ohne [N] gewechselt —
            # trotzdem die INTELLIGENTE Fassung nehmen, NICHT das rohe Quelltext-Netz.
            summary = _strip_recap_brackets(raw_response)
            soft_intelligent = True
            skip_qc = True
            print("[genius-direct] → intelligente Opus-Fließtextfassung verwendet (kein Quelltext-Netz).", file=sys.stderr)
        else:
            # Rohantwort wirklich unbrauchbar → deterministisches Netz (Quelle-Präfix bereinigt).
            try:
                fallback = _build_deterministic_genius_summary(_clean_pseudo_for_fallback(pseudo_sections), mode=normalized_mode)
                summary = fallback if fallback else _strip_recap_brackets(raw_response)
                used_fallback = bool(fallback)
            except Exception:
                summary = _strip_recap_brackets(raw_response)
            print("[genius-direct] → deterministisches Netz (Rohfassung unbrauchbar).", file=sys.stderr)
        # Top-3 in die Soft-Fassung einsetzen
        if soft_intelligent and top3_block:
            _mintro = re.search(r"^###[^\n]*\n+\*[^\n]*\*\n", summary)
            if _mintro:
                summary = summary[:_mintro.end()] + "\n" + top3_block + "\n" + summary[_mintro.end():]
            else:
                summary = top3_block + "\n\n" + summary
    else:
        # Intelligente Fassung formatieren (Bucket-Sortierung, Was-du-mitnehmen-kannst, Abschluss)
        _report("Kompaktfassung wird nachformatiert…", 0.78)
        try:
            summary = _format_genius_summary_output(
                raw_response, pseudo_sections, normalized_mode, all_sections=pseudo_sections, direct_mode=True
            )
        except Exception:
            summary = raw_response
        if top3_block:
            _mintro = re.search(r"^###[^\n]*\n+\*[^\n]*\*\n", summary)
            if _mintro:
                summary = summary[:_mintro.end()] + "\n" + top3_block + "\n" + summary[_mintro.end():]
            else:
                summary = top3_block + "\n\n" + summary

    # Lokaler Lint
    if quality_lint and not used_fallback and not skip_qc:
        try:
            lint = _lint_genius_summary(summary, len(pseudo_sections))
            if lint.get("auto_repairs"):
                summary = lint.get("repaired_text") or summary
        except Exception:
            pass

    # === LLM-Quality-Check + Auto-Repair via CLI (gleiche Logik wie Voll→Kompakt-Pfad) ===
    # Übersprungen bei [N]-loser Fließtextfassung (ID-basierter Check hätte keine Basis).
    quality_issues: List[dict] = []
    quality_repair_applied = False
    if not used_fallback and not skip_qc:
        try:
            _report("Kompakt-Qualitätscheck via Claude…", 0.82)
            qc = _run_genius_quality_check_via_cli(
                summary, pseudo_sections,
                model=_CLI_JUDGE_MODEL, cli_path=cli,
                progress_callback=progress_callback,
            )
            quality_issues = qc.get("issues") or []
            if quality_issues:
                _report(f"Kompaktfassung wird repariert ({len(quality_issues)} Probleme)…", 0.88)
                repaired = _run_genius_repair_via_cli(
                    summary, quality_issues, pseudo_sections,
                    model=_CLI_JUDGE_MODEL, cli_path=cli,
                    progress_callback=progress_callback,
                )
                if repaired and repaired != summary:
                    try:
                        lint2 = _lint_genius_summary(repaired, len(pseudo_sections))
                        summary = lint2.get("repaired_text") or repaired
                    except Exception:
                        summary = repaired
                    quality_repair_applied = True
        except Exception as exc:
            print(f"[direct-genius-quality] Quality-Check fehlgeschlagen: {exc}", file=sys.stderr)

    summary = tts_safe(summary)

    # PDF bauen
    _report("Kompakt-PDF wird gebaut…", 0.94)
    try:
        title_map = {
            "long": "Geniale Zusammenfassung (Lang)",
            "short": "Geniale Zusammenfassung (Kurz)",
            "standard": "Geniale Zusammenfassung",
        }
        document_title = title_map.get(normalized_mode, "Geniale Zusammenfassung")
        pdf_bytes = create_single_markdown_pdf(
            summary,
            datetime.datetime.now(),
            document_title=document_title,
        )
        if not pdf_bytes:
            return {"ok": False, "error": "PDF-Erstellung lieferte leere Bytes.",
                    "output_pdf_path": None, "mode": mode, "raw_response": raw_response}
        with open(output_pdf_path, "wb") as fp:
            fp.write(pdf_bytes)
    except Exception as exc:
        return {"ok": False, "error": f"PDF-Erstellung fehlgeschlagen: {exc}",
                "output_pdf_path": None, "mode": mode, "raw_response": raw_response}

    # Eleven-Reader-TXT zusätzlich schreiben — damit die NUR-Kompakt-Fassung auch im
    # Wochen-Meta-Briefing berücksichtigt wird (das liest die TXTs aus dem lokalen Spiegel;
    # der Direkt-Modus erzeugt sonst nur die PDF → Tag fehlte im Meta).
    try:
        _eleven_text = "\n".join(_markdown_line_to_plain(_l) for _l in summary.splitlines()).strip()
        _base = os.path.splitext(os.path.basename(output_pdf_path))[0]
        _txt_filename = f"{_base}_eleven-reader.txt"
        try:
            _texte_dir = os.path.join(os.path.dirname(os.path.abspath(output_pdf_path)), "Texte")
            os.makedirs(_texte_dir, exist_ok=True)
            _txt_path = os.path.join(_texte_dir, _txt_filename)
            with open(_txt_path, "w", encoding="utf-8") as _fp:
                _fp.write(_eleven_text)
            _record_archived_file(_txt_path)  # für iCloud-Cleanup
            _write_current_eleven_copy(_texte_dir, _eleven_text)
        except Exception:
            pass
        # Lokaler Meta-Spiegel — DAS liest das Wochen-Meta (iCloud ist vom Dienst nicht auflistbar)
        _mirror_txt_to_local(_txt_filename, _eleven_text)
        print(f"[direct-genius] Eleven-Reader-TXT für Wochen-Meta geschrieben: {_txt_filename}", file=sys.stderr)
    except Exception as _ex:
        print(f"[direct-genius] Eleven-TXT/Spiegel übersprungen: {_ex}", file=sys.stderr)

    _report(f"Direkt-Kompaktfassung fertig ({elapsed:.0f}s)", 1.0)

    meta = {
        "mode": normalized_mode,
        "mode_label": "Lang" if normalized_mode == "long" else ("Kurz" if normalized_mode == "short" else "Standard"),
        "used_fallback": used_fallback,
        "soft_intelligent": soft_intelligent,
        "ids_ok": valid_ids,
        "coverage_ok": valid_coverage,
        "total": len(pseudo_sections),
        "quality_issues": quality_issues,
        "quality_repair_applied": quality_repair_applied,
        "pseudo_sections_count": len(pseudo_sections),
        "direct_mode": True,
    }

    return {
        "ok": True,
        "error": None,
        "output_pdf_path": output_pdf_path,
        "mode": normalized_mode,
        "raw_response": raw_response,
        "summary_text": summary,
        "elapsed_seconds": elapsed,
        "meta": meta,
        "usage": stream_result.get("usage"),
        "cost_usd": stream_result.get("cost_usd"),
    }


_CLAUDE_CLI_META_RESPONSE_PROMPT = """Du bekommst gleich mehrere fertige Tages-Audio-Briefings als Input. Daraus sollst du ein Wochen-Meta-Briefing schreiben.

Im System-Prompt unten stehen die genauen Anweisungen zu Format, Länge und Struktur.

Antworte ausschließlich mit dem fertigen Markdown-Text des Wochen-Briefings — ohne Vorrede, ohne Erklärungen davor oder danach, ohne JSON-Wrapper.
"""


def run_meta_briefing_via_claude_cli(
    archive_dir: str,
    output_pdf_path: Optional[str] = None,
    *,
    days: int = 7,
    model: str = "sonnet",
    timeout_seconds: int = 900,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    cli_path: Optional[str] = None,
    read_dir: Optional[str] = None,
) -> Optional[dict]:
    """Wochen-Meta-Briefing via Claude CLI (Max-Abo, kostenlos).

    Identisches Verhalten wie generate_meta_briefing(), aber via subprocess
    statt API-Call. Liest die archivierten Tages-TXT-Dateien, fasst sie zu
    einem Input-Block zusammen, schickt sie an `claude -p` mit dem
    META_BRIEFING_PROMPT und baut Markdown + PDF + TXT.

    Returns dict {text, pdf, pdf_path, txt, txt_path, dates, stats, elapsed_seconds}
    oder None wenn keine Daily-Briefings gefunden wurden.
    """
    def _report(step: str, ratio: float):
        if progress_callback:
            try:
                progress_callback(step, max(0.0, min(1.0, float(ratio))))
            except Exception:
                pass

    cli = cli_path or _locate_claude_cli()
    if not cli:
        return {"ok": False, "error": "Claude CLI nicht gefunden.",
                "text": None, "pdf": None, "pdf_path": None,
                "txt": None, "txt_path": None, "dates": [], "stats": {}}

    _report("Tagesbriefings werden gesucht...", 0.05)
    # Discovery aus dem lokalen Spiegel (iCloud ist vom Hintergrunddienst nicht
    # auflistbar). Output-PDF geht weiterhin nach archive_dir (iCloud, Handy-Sync).
    _read_from = read_dir or str(_LOCAL_META_MIRROR_DIR)
    daily_briefings = _discover_weekly_briefing_texts(_read_from, days=days)
    if not daily_briefings:
        return None

    _report(f"{len(daily_briefings)} Tagesbriefings gefunden, werden aufbereitet...", 0.10)

    combined, input_meta = _build_meta_briefing_input(daily_briefings)

    _report(f"Wochen-Meta-Briefing wird an Claude {model} geschickt…", 0.20)

    # WICHTIG: KEIN --system-prompt verwenden. Im launchd-Hintergrundkontext lässt
    # `claude --print --system-prompt <gross>` den Aufruf hängen/leer zurückkommen
    # (verifiziert 05.06.2026). Der funktionierende Tagesbriefing-Pfad faltet die
    # Anweisungen in den stdin-Input und nutzt nur --append-system-prompt. Genau das
    # machen wir hier auch: META_BRIEFING_PROMPT wandert in den Input.
    meta_input = (
        META_BRIEFING_PROMPT
        + "\n\n=== HIER DIE TAGESBRIEFINGS DER WOCHE ===\n\n"
        + combined
    )
    cmd = [
        cli,
        "--print",
        "--output-format", "text",
        "--model", model,
        "--dangerously-skip-permissions",
        # Synthese-Aufgabe (Muster/Querverbindungen/Coach) → hohe Sorgfalt. Ein
        # einziger Aufruf, kostenlos. „low" ließ Opus gelegentlich abbrechen und
        # nur die Schlusszeile liefern.
        "--effort", "high",
        "--append-system-prompt", _CLAUDE_CLI_META_RESPONSE_PROMPT,
    ]

    def _meta_attempt(att_base: float, att_max: float, label: str):
        """Ein CLI-Versuch → (meta_text|None, stream_result)."""
        sr = _run_claude_cli_subprocess_streaming(
            cmd, meta_input, cwd=archive_dir or os.getcwd(),
            timeout_seconds=timeout_seconds, progress_callback=progress_callback,
            base_progress=att_base, max_progress=att_max, expected_duration_s=180.0,
            label=label,
        )
        if not sr.get("ok") or sr.get("returncode") != 0:
            return None, sr
        return ((sr.get("stdout") or "").strip() or None), sr

    def _looks_complete(txt: str) -> bool:
        """Grober Vollständigkeits-Check gegen near-empty Antworten (nur Schlusszeile)."""
        if not txt or len(txt.split()) < 800:
            return False
        return ("Die Lage in 5 Minuten" in txt) or ("Die großen Stränge" in txt)

    # Bis zu 2 Versuche: Opus liefert mit --effort gelegentlich eine near-empty
    # Antwort. Bei offensichtlich unvollständigem Ergebnis genau 1× erneut.
    meta_text, stream_result = _meta_attempt(0.20, 0.68, f"Claude {model} schreibt Wochen-Briefing")
    if not _looks_complete(meta_text or ""):
        _wc = len((meta_text or "").split())
        print(f"[meta-briefing-cli] Antwort unvollständig ({_wc} Wörter) — 1× erneuter Versuch.", file=sys.stderr)
        _report("Antwort war unvollständig — wird erneut versucht…", 0.70)
        meta_text2, stream_result2 = _meta_attempt(0.70, 0.78, f"Claude {model} — Wiederholung")
        if _looks_complete(meta_text2 or "") or len((meta_text2 or "").split()) > _wc:
            meta_text, stream_result = meta_text2, stream_result2

    if stream_result is None or not stream_result.get("ok"):
        return {"ok": False, "error": f"Wochen-Meta — {(stream_result or {}).get('error')}",
                "text": None, "pdf": None, "pdf_path": None,
                "txt": None, "txt_path": None, "dates": [], "stats": {}}

    stdout = stream_result["stdout"]
    stderr = stream_result["stderr"]
    elapsed = stream_result["elapsed"]
    returncode = stream_result["returncode"]

    if returncode != 0:
        err_short = (stderr or "").strip()[:600]
        out_short = (stdout or "").strip()[:600]
        msg = err_short or out_short or f"CLI-Exit-Code {returncode}"
        return {"ok": False, "error": f"Claude CLI Fehler: {msg}",
                "text": None, "pdf": None, "pdf_path": None,
                "txt": None, "txt_path": None, "dates": [], "stats": {}}

    if not meta_text:
        return {"ok": False, "error": "Claude hat eine leere Antwort geliefert.",
                "text": None, "pdf": None, "pdf_path": None,
                "txt": None, "txt_path": None, "dates": [], "stats": {}}

    _report("Antwort wird formatiert und nachbereitet…", 0.80)
    meta_text = _sanitize_briefing_output(_normalize_existing_briefing_markdown(_strip_meta_preamble(meta_text)))
    meta_text = tts_safe(meta_text)
    quality = _validate_meta_briefing_text(meta_text)

    _report("PDF wird erzeugt…", 0.90)
    generated_at = get_berlin_now()
    meta_sections = [{"type": "article", "content": meta_text}]

    pdf_bytes = None
    pdf_path = None
    txt_bytes = None
    txt_path = None
    try:
        archive_path_obj = Path(archive_dir)
        weekly_dir = archive_path_obj / "Wochen-Briefings"
        weekly_dir.mkdir(parents=True, exist_ok=True)
        ts = generated_at.strftime("%Y-%m-%d_%H-%M")
        pdf_path = weekly_dir / f"{ts}_wochenbriefing_{days}d.pdf"
        create_pdf(meta_sections, str(pdf_path), generated_at, document_title="Wochen-Meta-Briefing")
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
    except Exception as exc:
        print(f"[meta-briefing-cli] PDF-Erzeugung fehlgeschlagen: {exc}", file=sys.stderr)

    try:
        txt_bytes = meta_text.encode("utf-8")
        if pdf_path:
            txt_path = Path(str(pdf_path)).with_suffix(".txt")
            txt_path.write_text(meta_text, encoding="utf-8")
    except Exception:
        pass

    _report(f"Fertig — Wochen-Briefing in {elapsed:.0f}s erstellt", 1.0)
    return {
        "ok": True,
        "error": None,
        "text": meta_text,
        "pdf": pdf_bytes,
        "pdf_path": str(pdf_path) if pdf_path else None,
        "txt": txt_bytes,
        "txt_path": str(txt_path) if txt_path else None,
        "dates": sorted(set(e["date"] for e in daily_briefings)),
        "stats": {
            "briefings": len(daily_briefings),
            "days": len(set(e["date"] for e in daily_briefings)),
            "total_chars_input": input_meta["total_chars_input"],
            "truncated_briefings": input_meta["truncated_briefings"],
            "source_files": input_meta["source_files"],
            "compact_only_days": sum(1 for e in daily_briefings if e["is_compact"]),
        },
        "quality": quality,
        "elapsed_seconds": elapsed,
        "usage": stream_result.get("usage"),
        "cost_usd": stream_result.get("cost_usd"),
    }
