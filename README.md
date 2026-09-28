# Audio Briefing

A self-hosted news-digest pipeline. It collects regional and national news from
RSS feeds, a Feedly read-later list, newsletters and podcast transcripts,
condenses them with large language models and renders the result as a PDF, as
a text/ePub edition for text-to-speech listening, and as a short reading
edition that goes out to readers via a WhatsApp broadcast.

Built and maintained by one person since July 2026 for daily use in Tübingen,
Germany. UI, prompts and code comments are in German.

## What it does

| Step | How |
|---|---|
| Collect | Feedly read-later list (`feedly_fetch.py`), RSS and newsletter feeds (`newsletter_fetch.py`), full-text extraction with trafilatura/readability, paywalled sites via a persistent Playwright profile with the user's own subscriptions |
| Podcasts | Transcripts from Pocket Casts and Podcasting 2.0 feed transcripts (`pocketcasts_fetch.py`), or local transcription with mlx-whisper on Apple Silicon (`local_transcribe.py`) |
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
feedly_fetch.py        Feedly read-later + full text (Playwright)
newsletter_fetch.py    Newsletter feeds
pocketcasts_fetch.py   Podcast transcripts (Pocket Casts, feed transcripts)
local_transcribe.py    Local Whisper transcription
reader_upload.py       ePub build + ElevenReader upload
wochenbriefing_lauf.py Weekly meta-briefing
briefing_schedule*.py  Scheduled headless runs (launchd)
wartung.py, fehlerbuch.py  Nightly maintenance + error journal
prompts/, templates/   Prompt texts
launchd/               launchd plists
fonts/                 DejaVu fonts for the PDF (Bitstream Vera license)
```

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
