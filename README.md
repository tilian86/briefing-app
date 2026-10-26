# Audio Briefing

A self-hosted news-digest pipeline. It collects regional and national news from
RSS feeds, a reading basket, newsletters and podcast transcripts,
condenses them with large language models and renders the result as a PDF, as
a text/ePub edition for text-to-speech listening, and as a short reading
edition that goes out to readers via a WhatsApp broadcast.

Built and maintained by one person since July 2026 for daily use in Tübingen,
Germany. UI, prompts and code comments are in German.

## What it does

| Step | How |
|---|---|
| Collect | Reading basket of the companion RSS reader Feedfunk (`vorauswahl_quelle.py`; the old Feedly read-later path in `feedly_fetch.py` is switched off via `FEEDLY_AKTIV`), RSS and newsletter feeds (`newsletter_fetch.py`), full-text extraction with trafilatura/readability, paywalled sites via a persistent Playwright profile with the user's own subscriptions |
| Podcasts | Single source: the queue of the author's own podcast player Hörfunk (episodes flagged "for the briefing", transcripts delivered with them) via `hoerfunk_quelle.py`. Pocket Casts and Apple Podcasts were removed on 2026-10-12 |
| Condense | Two modes: *synthesis* (weaves all sources on one topic into one piece) and *classic* (one piece per source). Duplicate detection, section classification, three length levels |
| Quality | Plausibility check against the sources plus automatic repair pass before the briefing is released |
| Render | PDF (reportlab), ePub/TXT upload to ElevenReader for audio (`reader_upload.py`), a compact WhatsApp reading edition, an optional weekly meta-briefing (`wochenbriefing_lauf.py`) |
| Operate | Streamlit UI (`briefing_app.py`), background jobs that survive browser reloads, scheduled headless runs via launchd (`briefing_schedule.py`, `briefing_scheduled.py`), macOS menu-bar status item (`StatusBar/`) |
| Maintain | Structured error journal (`fehlerbuch.py`) and a nightly maintenance run (`wartung.py`) that fixes clear-cut breakage such as changed feed markup, verifies imports, commits, and leaves anything critical for a human |

### LLM backends

The main path runs Claude through the Claude Code CLI (flat-rate subscription);
the Anthropic and OpenAI APIs are supported as fallbacks with per-run cost
tracking. Model choice per task (classification vs. writing vs. final review)
is configurable at the top of `briefing_core.py`.

## Layout

```
briefing_app.py        Streamlit UI and job orchestration
briefing_core.py       Pipeline: fetching, condensing, quality check, rendering
feedly_fetch.py        Full text + paywall check (Playwright); Feedly path off
selbst_anmeldung.py    Self-login for SWP/GEA from the macOS keychain (see "Selbst-Anmeldung")
vorauswahl_quelle.py   Feedfunk basket: fetch + mark done
newsletter_fetch.py    Newsletter feeds
hoerfunk_quelle.py     Podcast queue + transcripts (Hörfunk, the only podcast source)
reader_upload.py       ePub build + ElevenReader upload
wochenbriefing_lauf.py Weekly meta-briefing (retries + night catch-up, see below)
briefing_schedule*.py  Scheduled headless runs (launchd)
wartung.py, fehlerbuch.py  Nightly maintenance + error journal
prompts/, templates/   Prompt texts
launchd/               launchd plists
fonts/                 DejaVu fonts for the PDF (Bitstream Vera license)
```

### Weekly meta-briefing: retries and catch-up

`wochenbriefing_lauf.py` (launchd job `de.florian.wochenbriefing`) does not give up after one failure:

- **Sunday 19:10** - up to 3 build attempts in the same run, 10 minutes apart. From attempt 2 on the
  Claude CLI runs in a local working directory (`~/.briefing_cli_cwd`) instead of the iCloud archive.
  Every failure writes the tail of the CLI output (stderr, event count, stdin errors, cwd) to
  `~/Library/Logs/wochenbriefing.log` and, as a critical entry, to the error journal (`fehlerbuch.py`).
- **Mon/Tue/Wed 03:20** - extra `StartCalendarInterval` entries of the same job catch up a failed
  Sunday (state in `.wochenbriefing_nachholen.json`, git-ignored). The script decides by itself: if
  the week's briefing already exists it exits at once without touching anything. After a failed run it
  sets a `pmset schedule wake` for 03:18 (needs `/etc/sudoers.d/briefing-pmset`; skipped otherwise).
  Catch-up only applies to Sundays from 2026-10-18 on (`NACHHOLEN_AB_SONNTAG`).
- **Last night (Wed) fails too** - exactly one Pushfunk push (held back until 08:00) plus a critical
  journal entry "bitte klaeren". Nothing more after that.
- **Delivery** - the briefing never goes to people on its own, not even when caught up at night:
  the pipeline only uploads to ElevenReader and *prepares* the WhatsApp companion text
  (`wa_runde.nach_briefing(..., auto=False)`); sending happens only via the button in the app.

Tests (offline, dummies only): `python3 -m unittest tests.test_wochenbriefing_nachholen`


## Selbst-Anmeldung

*(Deutsch, weil es nur die Einrichtung auf Florians Mac betrifft.)*

Die App meldet sich bei **SWP/Tagblatt** (swp.de) und **GEA** (gea.de) selbst an, wenn ein
Zeitungs-Login abgelaufen ist. Vor jedem Artikel-Abruf (Knopf „Aus Feedfunk holen“, Auto-Kette)
prüft sie kurz an echten Abo-Artikeln, ob die Logins tragen, und meldet sich bei Bedarf im
selben Browser-Profil (`~/.briefing_news_profile`) neu an. Auch der terminierte Lauf pflegt die
Logins am Ende (`briefing_scheduled.py`, nicht bei Nachhol-Läufen). Das Passwort liegt **nur im
macOS-Schlüsselbund** — nie im Code, in Dateien, Logs oder Commits.

**Einmal einrichten:** im Terminal `briefing-logins` (= `./logins_einrichten.sh`). Der Helfer fragt
je Zeitung die E-Mail und verdeckt das Passwort ab, ersetzt alte Einträge und bietet danach einen
echten Test an. Von Hand geht es auch (`-w` ganz am Ende ohne Wert, dann fragt macOS):

```bash
security add-generic-password -U -s briefing-login-swp -a 'E-MAIL' -w
security add-generic-password -U -s briefing-login-gea -a 'E-MAIL' -w
```

`E-MAIL` ist die Anmelde-Adresse der jeweiligen Zeitung. Fehlt ein Eintrag, ist das kein Fehler:
die App bleibt beim alten Weg (Hinweis + „🔓 Anmeldefenster öffnen“).

**Prüfen und ausprobieren**

- In der App zeigt die Zeile unter den Knöpfen „🔑 Selbst-Anmeldung: SWP ✓ / GEA ✓ (Schlüsselbund)“
  oder „fehlt – einrichten“.
- Knopf **„🔑 Selbst-Anmeldung jetzt testen“**: meldet beide Zeitungen im Profil ab (Zustimmungs-
  Cookies bleiben), mit den Schlüsselbund-Daten wieder an und prüft mit echten Abo-Artikeln. Klappt es
  nicht, werden die bisherigen Cookies zurückgesetzt. Beim ersten Zugriff fragt macOS evtl. nach dem
  Schlüsselbund-Zugriff für „security“/„Python“ — „Immer erlauben“.
- Im Terminal: `python3 selbst_anmeldung.py` (Status der Einträge), `--pruefen` (wie vor dem Abruf),
  `--testen swp` bzw. `--testen gea` (Probe: abmelden, selbst anmelden, nachprüfen, bei Misserfolg
  Rollback). `--sichtbar` zeigt dabei das Browser-Fenster (zum Zuschauen beim ersten Mal); eine Push
  kommt vom Terminal aus nur mit `--push` (oder `--dry` = nur ausgeben). Das Profil muss frei sein
  (App-Abruf und Anmeldefenster dürfen nicht laufen).

**Schutz und Grenzen**

- Bot-Schutz (Captcha, Turnstile, reCAPTCHA …) wird **nicht umgangen**: erkannt → Abbruch, Meldung.
- Zustimmungsbanner: nur „ablehnen“/„nur notwendige“ wird geklickt, nie „akzeptieren“. Hat das Banner
  keine solche Wahl (SWP im frischen Profil), meldet die App das; du entscheidest dann einmal im
  Anmeldefenster.
- Nach einem abgelehnten Passwort **keine automatischen Versuche für 6 Stunden**, höchstens 4 Anläufe
  je Zeitung und Tag (der Test-Knopf hebt das auf).
- Bleibt ein Login fehlend: **eine** Push „🔐 Briefing: SWP-Login klappt nicht – bitte anmelden“ je
  Zeitung und Tag (Merkdatei `.briefing_selbst_anmeldung.json`, git-ignored).
- Notschalter: Umgebungsvariable `BRIEFING_SELBST_ANMELDUNG=0` schaltet alles ab (alter Weg).
  Log: `~/Library/Logs/briefing-selbst-anmeldung.log` (enthält nie Konto oder Passwort).

Tests (offline, gegen nachgebaute lokale Seiten): `python3 -m unittest tests.test_selbst_anmeldung`

## Running it

Requirements: macOS, Python 3.9+, `pip install -r requirements.txt`,
Playwright with Chromium, `ffmpeg`, and either the Claude Code CLI or API keys
in the environment.

```bash
streamlit run briefing_app.py
```

The scripts under `*.command`, `start_briefing_app.sh` and `launchd/` contain
the author's local paths and serve as templates. The project is tuned to one
machine and one reader group; expect to adjust paths, sources and the weather
location before it fits yours.

## Companion repositories

Regional papers in the area publish no RSS feeds, so these repositories
generate them with GitHub Actions and feed the briefing:

- [gea-rss-feeds](https://github.com/tilian86/gea-rss-feeds) — Reutlinger General-Anzeiger
- [kontext-rss](https://github.com/tilian86/kontext-rss) — KONTEXT:Wochenzeitung
- [rausgegangen-rss](https://github.com/tilian86/rausgegangen-rss) — Rausgegangen event listings

## License

MIT, see [LICENSE](LICENSE). The bundled DejaVu fonts keep their own license.
