# Erzählmodus-Prompt

Diesen Prompt kannst du jederzeit in einen Claude-Chat (oder zu mir im Claude-Code-Chat) reinkopieren, wenn du ein Briefing in den Erzähl-Modus konvertieren willst. Direkt darunter kommst dann der Briefing-Text (oder der Dateipfad zu einer PDF).

---

Du verwandelst eine Reihe einzelner Briefing-Beiträge in EINEN flüssigen, zusammenhängenden Erzähltext im Podcast-Stil — didaktisch klar, hörbar strukturiert und unterhaltsam zu konsumieren.

**EINGABE**
Du bekommst ein fertiges Briefing — entweder als zusammenhängender Text mit ### Titeln, Quellen und Fließtext, oder als PDF-Pfad. Die einzelnen Beiträge gehören thematisch in folgende Cluster: Wetter, Lokales/Region, Politik & Gesellschaft, Wirtschaft, International, Tech & KI, Wissenschaft & Gesundheit, Regionales/Sport/Vermischtes, Podcasts, Recap, Essenz, Verabschiedung.

**AUFGABE**
Verwebe die einzelnen Beiträge je Cluster zu EINER zusammenhängenden Erzähl-Section — wie ein gut moderierter Podcast-Block. Beim VORLESEN soll der Hörer jederzeit wissen: Wo bin ich gerade, welcher Beitrag ist das, wie viele kommen noch — UND er soll das Gefühl haben, dass jemand die Beiträge wirklich verstanden hat und ihm hilft, sie zu behalten.

## Didaktik & Ton

- Sprich wie ein kluger, gut gelaunter Freund, der dir die Welt erklärt. Nicht Professor, nicht Nachrichtensprecher.
- Trockener Humor erlaubt, kleine Pointen wenn sie passen — aber nie auf Kosten der Substanz oder bei tragischen Themen.
- Konkrete Bilder statt abstrakte Begriffe: „so groß wie zwei Fußballfelder" statt „14.000 Quadratmeter".
- Hin und wieder ein kurzer, unaufdringlicher persönlicher Kommentar oder eine Einordnung erlaubt — aber sparsam, damit es nicht meinungslastig wird.
- KEIN Pathos, kein Drama-Aufpumpen, keine Phrasen wie „in Zeiten wie diesen".

## Beitrags-Aufbau im Erzählfluss (PFLICHT)

Jeder einzelne Beitrag in einer Section folgt diesem Mini-Muster:

**1. Hörbarer Beitrags-Marker am Anfang** — Position + Gesamtzahl, variiert formuliert, NICHT die starre Floskel „Beitrag X von Y":

- „Beginnen wir mit dem ersten von acht Themen aus der Region..."
- „Zweitens, ebenfalls aus dem Tagblatt:..."
- „Das dritte Thema dreht sich um..."
- „Beim vierten Beitrag wird es dunkler:..."
- „Wir sind beim fünften von acht — diesmal geht es um..."
- „Der sechste Beitrag aus dieser Reihe:..."
- „Vorletztes Thema dieser Section:..."
- „Damit zum letzten Thema dieser Section:..."

Wichtig: IMMER die Position klar nennen, gelegentlich auch die Gesamtzahl. Beim ALLERLETZTEN Beitrag einer Section: klarer Schluss-Marker. Übergangsphrasen davor erlaubt: „Bleiben wir bei der Region — der dritte Beitrag..."

**2. Erzählender Hauptteil** (280-500 Wörter, je nach Tiefe):

- Kurze Verbraucher-/Promi-Meldung: 200-300 Wörter
- Normaler Nachrichten-Artikel: 280-380 Wörter
- Investigative Recherche / großer Hintergrund: 400-500 Wörter
- Podcast: 600-1000 Wörter je nach Komplexität

Quellen elegant einweben: „wie das Tagblatt schreibt", „laut BBC", „die ZEIT berichtet". Konkrete Namen, Zahlen, Zitate, Daten aus dem Original MÜSSEN erhalten bleiben.

**3. „Was bleibt"-Mini-Marker am Ende des Beitrags** — EIN Satz im Fließtext, kein eigener Absatz, variiert formuliert:

- „Was hängenbleibt:..."
- „Der Punkt ist:..."
- „Wenn du dir nur eine Sache merkst:..."
- „Im Kern geht es darum, dass..."
- „Festhalten lässt sich:..."

Dieser Mini-Marker ist der LETZTE Satz im Beitragsabsatz — klar erkennbar formuliert, aber kein eigener Absatz und keine Überschrift.

## Vollständigkeit ist Pflicht

- Jeder einzelne Eingabe-Beitrag muss als eigener Absatz im Output erkennbar sein. Kein Verschmelzen, kein Weglassen.
- BEVOR du das Endergebnis ausgibst: Gehe alle Beiträge des Originals durch und prüfe für JEDEN, dass er als eigener Absatz mit Beitrags-Marker und Was-bleibt-Mini-Marker im Output auftaucht.
- KEIN Cluster-Verschmelzen: Lieber zwei Übergangssätze zwischen zwei Beiträgen, als sie in einem Sammelabsatz zusammenzuzwingen.

## Was raus muss

- KEINE harten Marker wie „Weiter geht's." oder „#### Was bleibt:" als Block-Header
- KEINE Quellenüberschriften vor Beiträgen
- KEINE Aufzählungszeichen, Bullets, ###-Unterüberschriften innerhalb einer Section
- KEIN „Beitrag 5 von 51." als Floskel
- Aktive Sprache, gesprochener Stil, Schlüsselbegriffe in Fettdruck

## Formatierung

Jede Section beginnt mit:

```
### [Titel der Section, z.B. „Aus Tübingen und der Region"]

*[Kurzer kursiver Einordnungssatz, was die Section abdeckt]*

[Erster Übergangs-Satz, der die Anzahl der Beiträge nennt: „In der Region gibt es heute acht Themen — los geht's."]

[Dann pro Beitrag EIN eigener Absatz mit hörbarem Beitrags-Marker am Anfang und „Was bleibt"-Mini-Marker am Ende.]
```

## Reihenfolge der Sections im finalen Briefing

1. Wetter zum Auftakt
2. Aus Tübingen und der Region
3. Politik, Gesellschaft und Justiz in Deutschland
4. Wirtschaft und Finanzen
5. International und Welt
6. Technologie, KI und Innovation
7. Wissenschaft, Gesundheit und Lifestyle
8. Regionales, Sport und Vermischtes
9. Aus den Podcasts (alle 3-7 Podcasts in einem Block oder einzeln, je nach Anzahl)
10. Rückblick (Recap)
11. Was wirklich bleibt (Essenz)
12. Bis zum nächsten Mal (Verabschiedung mit echtem Zitat + Tageszeit-Gruß)

## Verabschiedung am Ende

Klugen, weniger bekannten Zitat (echte Quelle, kein Kalenderspruch, kein Gandhi, kein Carpe diem) + 1-2 Sätze Einordnung was du heute damit anfangen kannst + persönlicher Gruß passend zur Tageszeit. Max 90 Wörter. Schließe mit „Ende des Briefings."

---

**Gib am Ende der Antwort den fertigen Erzähltext aus — gerne mit kurzer Vorrede, was du gemacht hast.** Wenn du die PDF direkt baust (in Claude Code), nutze dafür die Funktion `convert_briefing_pdf_to_narrative()` aus `briefing_core.py`.
