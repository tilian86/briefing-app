"""
Audio-Briefing Web-App
======================
Streamlit-basierte Web-Oberfläche für den Audio-Briefing-Generator.

Starten mit:
    streamlit run briefing_app.py --server.address 0.0.0.0 --server.port 8501
"""

import streamlit as st
import streamlit.components.v1 as components
import base64
import datetime
import html
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional
from briefing_core import (
    _annotate_section_progress_markers,
    _discover_weekly_briefing_texts,
    _LOCAL_META_MIRROR_DIR,
    _mirror_txt_to_local,
    _record_archived_file,
    _locate_claude_cli,
    create_eleven_reader_text,
    create_pdf,
    _parse_briefing_text_to_sections,
    build_claude_handoff_package,
    build_pdf_from_claude_json,
    convert_briefing_pdf_to_narrative,
    convert_briefing_to_narrative_from_text,
    detect_truncated_paywall_blocks,
    extract_article_urls,
    generate_meta_briefing,
    inspect_article_urls,
    generate_briefing,
    generate_genius_summary,
    merge_cost_dicts,
    assess_article_fetch_quality,
    find_potential_topic_duplicates,
    llm_confirm_duplicate_clusters,
    summarize_podcast_transcript_via_cli,
    preprocess_podcast_text,
    list_apple_podcast_transcripts,
    apple_ttml_to_text,
    fetch_new_podcast_episodes,
    podcast_inbox_cache_save,
    podcast_inbox_cache_load,
    podcast_listen_list_add,
    podcast_listen_list,
    podcast_listen_list_remove,
    podcast_inbox_archived_list,
    podcast_inbox_unarchive,
    podcast_inbox_selection_save,
    podcast_inbox_selection_load,
    split_special_topics,
    combine_podcast_field,
    suggest_missing_topics_via_cli,
    append_special_topic,
    attach_inbox_translations,
    download_feed_transcript,
    resolve_apple_episode_url,
    open_in_apple_podcasts,
    podcast_inbox_mark,
    wait_for_new_apple_ttml,
    list_unimported_ttml,
    mark_ttml_imported,
    apple_container_accessible,
    _extract_article_urls_internal,
    repair_briefing_content_issues,
    rerun_briefing_sorting,
    run_briefing_repair_via_claude_cli,
    run_briefing_via_claude_cli,
    run_briefing_via_claude_cli_chunked,
    run_content_check_via_claude_cli,
    run_direct_genius_via_claude_cli,
    run_genius_summary_via_claude_cli,
    run_meta_briefing_via_claude_cli,
    split_paywall_articles,
    split_podcast_summaries,
)

_APP_DIR = Path(__file__).resolve().parent
_ICON_PATH = _APP_DIR / "assets" / "briefing_icon.svg"
_ICON_DATA_URI = None
if _ICON_PATH.exists():
    try:
        _ICON_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_ICON_PATH.read_bytes()).decode("ascii")
    except Exception:
        _ICON_DATA_URI = None

# .env laden (falls vorhanden, direkt neben diesem Script)
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                _val = _val.strip().strip('"').strip("'")
                os.environ.setdefault(_key.strip(), _val)

# ============================================================
# SEITEN-KONFIGURATION
# ============================================================

st.set_page_config(
    page_title="Audio-Briefing",
    page_icon=str(_ICON_PATH) if _ICON_PATH.exists() else "📰",
    layout="wide",
    initial_sidebar_state="collapsed",
)
# Tests MÜSSEN BRIEFING_DRAFT_PATH auf einen Wegwerf-Pfad setzen — AppTest führt
# die echte App aus und hat am 03.07. Florians echten Draft mit Testdaten überschrieben.
_DRAFT_PATH = Path(os.getenv("BRIEFING_DRAFT_PATH") or (_APP_DIR / ".briefing_draft.json"))
_LAST_BRIEFING_DIR = _APP_DIR / ".last_briefing"
_PRIMARY_ARCHIVE_DIR = Path("/Users/florian/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Briefings")
_FALLBACK_ARCHIVE_DIR = _APP_DIR / ".archive"
_GENIUS_ARCHIVE_SUBDIR = "Kompaktfassungen"
_TXT_ARCHIVE_SUBDIR = "Texte"
_LAST_BRIEFING_CHECK_PATH = _LAST_BRIEFING_DIR / "check.json"
_LAST_BRIEFING_SECTIONS_PATH = _LAST_BRIEFING_DIR / "sections.json"
_LAST_BRIEFING_EXPORT_FILES = {
    "pdf": _LAST_BRIEFING_DIR / "briefing.pdf",
    "epub": _LAST_BRIEFING_DIR / "briefing.epub",
    "eleven_txt": _LAST_BRIEFING_DIR / "briefing_eleven.txt",
}
_ARCHIVE_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".epub": "application/epub+zip",
    ".txt": "text/plain; charset=utf-8",
}
_DRAFT_DEFAULTS = {
    "provider": "OpenAI (GPT)",
    "model": "gpt-5.4-mini",
    "include_weather": True,
    "content_check_enabled": True,
    "content_check_mode": "warn",
    "openai_balance_input": "",
    "anthropic_balance_input": "",
    "openai_estimated_remaining_usd": None,
    "anthropic_estimated_remaining_usd": None,
    "openai_balance_spend_multiplier": 1.0,
    "anthropic_balance_spend_multiplier": 1.0,
    "openai_balance_spend_since_correction_usd": 0.0,
    "anthropic_balance_spend_since_correction_usd": 0.0,
    "openai_balance_note": "",
    "anthropic_balance_note": "",
    "openai_manual_balance_input": "",
    "anthropic_manual_balance_input": "",
    "openai_manual_balance_prefill_source": "",
    "anthropic_manual_balance_prefill_source": "",
    "openai_balance_skip_learning": False,
    "anthropic_balance_skip_learning": False,
    "openai_balance_skip_learning_reset_pending": False,
    "anthropic_balance_skip_learning_reset_pending": False,
    "urls_text": "",
    "paywall_text": "",
    "podcast_text": "",
    # Daily-Driver-Standards (03.07.): Themen-Synthese + Magazin-Stil + Web-Ergänzung
    # sind an, Länge Kürzer — letzte Wahl überlebt Seiten-Refresh wie alle Draft-Keys.
    "topic_synthesis_mode": True,
    "synthesis_narrative_style": True,
    "synthesis_web_enrich": True,
    "auto_reader_upload": True,
    "reader_cleanup_days": 14,
    "whatsapp_pdf_additional": True,
    "last_meta_created_iso": "",
    "briefing_depth_multi": ["Kürzer"],
    "export_pdf": True,
    "genius_summary_mode": "long",
    # Run-Optionen (vorher nicht persistiert — bei Code-Reload gingen Checkbox-Werte verloren)
    "compact_mode": True,
    "narrative_additional_main": False,
    "genius_additional_main": True,
    "narrative_depth_radio": "Standard",
    "genius_depth_radio_main": "Keine",
    "api_auto_repair_enabled": True,
    "quality_check_enabled": True,
    "special_topics_text": "",
    "auto_briefing_when_done": False,
}


def _resolve_archive_dir(for_write: bool = False) -> Path:
    if for_write:
        try:
            _PRIMARY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            return _PRIMARY_ARCHIVE_DIR
        except Exception:
            _FALLBACK_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            return _FALLBACK_ARCHIVE_DIR

    if _PRIMARY_ARCHIVE_DIR.exists():
        return _PRIMARY_ARCHIVE_DIR
    if _FALLBACK_ARCHIVE_DIR.exists():
        return _FALLBACK_ARCHIVE_DIR
    return _PRIMARY_ARCHIVE_DIR


def _archive_write_candidates(subdir: Optional[str] = None) -> list:
    candidates = []
    for base in (_PRIMARY_ARCHIVE_DIR, _FALLBACK_ARCHIVE_DIR):
        path = base / subdir if subdir else base
        if path not in candidates:
            candidates.append(path)
    return candidates


def _write_archive_bytes(file_name: str, data: bytes, subdir: Optional[str] = None) -> tuple:
    if not data:
        return None, None

    # Eleven-Reader-TXTs zusätzlich in den lokalen Meta-Spiegel schreiben, damit
    # das Wochen-Meta sie auflisten kann (iCloud ist vom Hintergrunddienst nicht
    # auflistbar). Best-effort.
    if subdir == _TXT_ARCHIVE_SUBDIR and file_name.endswith("_eleven-reader.txt"):
        try:
            _mirror_txt_to_local(file_name, data.decode("utf-8"))
        except Exception:
            pass

    last_error = None
    for candidate_dir in _archive_write_candidates(subdir=subdir):
        try:
            candidate_dir.mkdir(parents=True, exist_ok=True)
            target = candidate_dir / file_name
            target.write_bytes(data)
            _record_archived_file(target)  # für den iCloud-Cleanup (launchd kann nicht listen)
            return target, None
        except Exception as exc:
            last_error = str(exc)
            continue
    return None, last_error


_CLEANUP_RETENTION_DAYS = 21
_CLEANUP_EXTENSIONS = (".pdf", ".txt", ".epub")
_CLEANUP_FILENAME_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _cleanup_old_briefings(retention_days: int = _CLEANUP_RETENTION_DAYS) -> dict:
    """Löscht Briefing-Dateien (PDF/TXT/EPUB), die älter als `retention_days` sind.

    Das Alter wird primär aus dem Datum im Dateinamen (YYYY-MM-DD) bestimmt, weil
    iCloud die Datei-mtime auf den Sync-Zeitpunkt setzt. Nur wenn kein Datum im Namen
    steht, dient die mtime als Fallback. Dateien ohne erkennbares Alter bleiben erhalten.
    """
    cutoff = datetime.date.today() - datetime.timedelta(days=retention_days)
    deleted, errors = [], []
    seen_dirs = set()
    for base in (_PRIMARY_ARCHIVE_DIR, _FALLBACK_ARCHIVE_DIR, _LOCAL_META_MIRROR_DIR):
        try:
            if not base.exists() or base in seen_dirs:
                continue
            seen_dirs.add(base)
        except Exception:
            continue
        for path in base.rglob("*"):
            try:
                if not path.is_file() or path.suffix.lower() not in _CLEANUP_EXTENSIONS:
                    continue
                m = _CLEANUP_FILENAME_DATE_RE.search(path.name)
                if m:
                    try:
                        file_date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    except ValueError:
                        continue  # ungültiges Datum → sicherheitshalber behalten
                else:
                    # Kein Datum im Namen → mtime nur als Fallback
                    file_date = datetime.date.fromtimestamp(path.stat().st_mtime)
                if file_date < cutoff:
                    path.unlink()
                    deleted.append(path.name)
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")

    # iCloud kann der launchd-Hintergrunddienst NICHT auflisten (rglob liefert 0),
    # aber per Pfad löschen geht. Daher zusätzlich über den lokalen Pfad-Index
    # alte iCloud-Dateien gezielt entfernen + Index aufräumen.
    try:
        _index_path = _LOCAL_META_MIRROR_DIR / ".archive_index.json"
        if _index_path.exists():
            _entries = json.loads(_index_path.read_text(encoding="utf-8"))
            if isinstance(_entries, list):
                _survivors = []
                for _p in _entries:
                    try:
                        _pp = Path(_p)
                        _m = _CLEANUP_FILENAME_DATE_RE.search(_pp.name)
                        if not _m:
                            _survivors.append(_p)
                            continue
                        _fd = datetime.date(int(_m.group(1)), int(_m.group(2)), int(_m.group(3)))
                        if _fd < cutoff:
                            try:
                                if _pp.exists():
                                    _pp.unlink()
                                    deleted.append(_pp.name)
                            except Exception:
                                pass
                            # aus Index entfernt (alt)
                        else:
                            _survivors.append(_p)  # jung → behalten (exists() im launchd-Kontext unzuverlässig)
                    except Exception as exc:
                        errors.append(f"index {_p}: {exc}")
                        _survivors.append(_p)
                _index_path.write_text(json.dumps(_survivors, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        errors.append(f"archive-index: {exc}")

    return {"deleted": deleted, "errors": errors, "cutoff": cutoff}


def _load_draft() -> dict:
    if not _DRAFT_PATH.exists():
        return {}
    try:
        return json.loads(_DRAFT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _backup_draft_before_shrink(new_data: dict):
    """Sichert den alten Draft weg, bevor ein Textfeld massiv schrumpft (≥200 Z. → <20%).
    Letzte Verteidigungslinie gegen jede Art von Daten-Wipe — egal welche Ursache."""
    try:
        if not _DRAFT_PATH.exists():
            return
        old = json.loads(_DRAFT_PATH.read_text(encoding="utf-8"))
        for f in ("urls_text", "paywall_text", "podcast_text"):
            o, n = len(old.get(f) or ""), len(new_data.get(f) or "")
            if o >= 200 and n < o * 0.2:
                bdir = _APP_DIR / ".briefing_draft_backups"
                bdir.mkdir(exist_ok=True)
                stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                (bdir / f"draft_{stamp}.json").write_text(
                    json.dumps(old, ensure_ascii=False), encoding="utf-8")
                _bks = sorted(bdir.glob("draft_*.json"))
                for _old_bk in _bks[:-15]:
                    _old_bk.unlink()
                print(f"[draft-schutz] {f} schrumpft {o}→{n} Zeichen — Backup {stamp} angelegt.", file=sys.stderr)
                break
    except Exception:
        pass


def _save_draft():
    try:
        draft_data = {
            key: st.session_state.get(key, default)
            for key, default in _DRAFT_DEFAULTS.items()
        }
        _backup_draft_before_shrink(draft_data)
        _DRAFT_PATH.write_text(
            json.dumps(draft_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _load_last_briefing():
    if not _LAST_BRIEFING_CHECK_PATH.exists():
        return None, None, None
    try:
        check = json.loads(_LAST_BRIEFING_CHECK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None, None, None

    exports = {}
    for key, path in _LAST_BRIEFING_EXPORT_FILES.items():
        if path.exists():
            try:
                exports[key] = path.read_bytes()
            except Exception:
                exports[key] = None
        else:
            exports[key] = None

    sections = None
    if _LAST_BRIEFING_SECTIONS_PATH.exists():
        try:
            sections = json.loads(_LAST_BRIEFING_SECTIONS_PATH.read_text(encoding="utf-8"))
        except Exception:
            sections = None

    if not any(exports.values()):
        return None, None, sections
    return exports, check, sections


def _persist_last_briefing(exports, check, sections=None):
    if not exports or not check:
        return
    try:
        _LAST_BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
        _LAST_BRIEFING_CHECK_PATH.write_text(
            json.dumps(check, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if sections is not None:
            _LAST_BRIEFING_SECTIONS_PATH.write_text(
                json.dumps(sections, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        elif _LAST_BRIEFING_SECTIONS_PATH.exists():
            _LAST_BRIEFING_SECTIONS_PATH.unlink()
        for key, path in _LAST_BRIEFING_EXPORT_FILES.items():
            data = exports.get(key)
            if data:
                path.write_bytes(data)
            elif path.exists():
                path.unlink()
    except Exception:
        pass


def _clear_persisted_last_briefing():
    try:
        if _LAST_BRIEFING_DIR.exists():
            shutil.rmtree(_LAST_BRIEFING_DIR)
    except Exception:
        pass


def _export_file_name(key: str, file_stamp: str, suffix: str = "") -> str:
    # Neues Namensschema: YYYY-MM-DD_HH-MM_tagesbriefing_voll[_suffix].ext
    # „voll" weil das die Vollversion ist (nicht Erzählmodus, nicht Kompaktfassung).
    # Suffix kann z.B. „_korrigiert" enthalten.
    stamp_short = file_stamp
    if len(file_stamp) == 19 and file_stamp[16] == "-":
        stamp_short = file_stamp[:16]
    base = "tagesbriefing_voll"
    if key == "pdf":
        return f"{stamp_short}_{base}{suffix}.pdf"
    if key == "epub":
        return f"{stamp_short}_{base}{suffix}.epub"
    if key == "eleven_txt":
        return f"{stamp_short}_{base}{suffix}_eleven-reader.txt"
    raise KeyError(key)


def _repair_archive_suffix(check: dict) -> str:
    iteration = int(check.get("repair_iteration", 0) or 0)
    repair_time = str(check.get("repair_time_file") or "").strip()
    time_suffix = f"_rep{repair_time}" if repair_time else ""
    if iteration <= 0:
        return ""
    if iteration == 1:
        return f"_korrigiert{time_suffix}"
    return f"_korrigiert-{iteration}{time_suffix}"


def _save_exports_to_archive(exports: dict, check: dict, suffix: str = "") -> dict:
    saved = []
    errors = []
    archive_dir = _resolve_archive_dir(for_write=True)
    if not exports or not check:
        return {"dir": str(archive_dir), "saved": saved, "errors": errors}

    file_stamp = check.get("created_at_file") or datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    requested = check.get("requested_exports", {"pdf": True, "epub": True, "eleven_txt": True})

    txt_saved = False
    for key in ("pdf", "epub", "eleven_txt"):
        if not requested.get(key):
            continue
        data = exports.get(key)
        if not data:
            continue
        file_name = _export_file_name(key, file_stamp, suffix=suffix)
        # TXT in Unterordner, PDF/EPUB direkt
        subdir = _TXT_ARCHIVE_SUBDIR if key == "eleven_txt" else None
        target, error = _write_archive_bytes(file_name, data, subdir=subdir)
        if target:
            if not subdir:
                archive_dir = target.parent
            saved.append(str(target))
            if key == "eleven_txt":
                txt_saved = True
        elif error:
            errors.append(f"{file_name}: {error}")

    # TXT immer mitspeichern (für Wochen-Meta-Briefing), auch wenn nicht als Export angehakt
    if not txt_saved and exports.get("eleven_txt"):
        txt_name = _export_file_name("eleven_txt", file_stamp, suffix=suffix)
        target, error = _write_archive_bytes(txt_name, exports["eleven_txt"], subdir=_TXT_ARCHIVE_SUBDIR)
        if target:
            saved.append(str(target))

    return {"dir": str(archive_dir), "saved": saved, "errors": errors}


def _cleanup_uncorrected_archive_versions(check: dict) -> list[str]:
    """Loescht unkorrigierte Archivdateien, wenn eine korrigierte Version existiert."""
    removed = []
    if not check:
        return removed

    file_stamp = check.get("created_at_file") or ""
    if not file_stamp:
        return removed

    requested = check.get("requested_exports", {"pdf": True, "epub": False, "eleven_txt": True})
    archive_dir = _resolve_archive_dir()
    candidates = []
    for key in ("pdf", "epub", "eleven_txt"):
        if key != "eleven_txt" and not requested.get(key):
            continue
        if key == "eleven_txt" and not (requested.get(key) or check.get("txt_ok") is not False):
            continue
        file_name = _export_file_name(key, file_stamp, suffix="")
        base_dir = archive_dir / _TXT_ARCHIVE_SUBDIR if key == "eleven_txt" else archive_dir
        candidates.append(base_dir / file_name)

    corrected_paths = set()
    for path_text in ((check.get("archive") or {}).get("saved") or []):
        try:
            corrected_paths.add(Path(path_text).resolve())
        except Exception:
            pass

    for path in candidates:
        try:
            resolved = path.resolve()
            if resolved in corrected_paths:
                continue
            if path.exists() and path.is_file():
                path.unlink()
                removed.append(str(path))
        except Exception:
            continue
    return removed


def _genius_summary_file_name(file_stamp: str, mode: str = "standard") -> str:
    # Neues Namensschema: YYYY-MM-DD_HH-MM_tagesbriefing_kompakt_<mode>.pdf
    stamp_short = file_stamp[:16] if len(file_stamp) == 19 and file_stamp[16] == "-" else file_stamp
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode == "short":
        return f"{stamp_short}_tagesbriefing_kompakt_kurz.pdf"
    if normalized_mode == "long":
        return f"{stamp_short}_tagesbriefing_kompakt_lang.pdf"
    return f"{stamp_short}_tagesbriefing_kompakt_standard.pdf"


def _save_genius_summary_pdf_to_archive(pdf_bytes: bytes, file_stamp: str, mode: str = "standard") -> dict:
    archive_dir = _resolve_archive_dir(for_write=True) / _GENIUS_ARCHIVE_SUBDIR
    if not pdf_bytes:
        return {"dir": str(archive_dir), "path": None, "error": None}
    file_name = _genius_summary_file_name(file_stamp, mode=mode)
    target, error = _write_archive_bytes(file_name, pdf_bytes, subdir=_GENIUS_ARCHIVE_SUBDIR)
    if target:
        return {"dir": str(target.parent), "path": str(target), "error": None}
    return {"dir": str(archive_dir), "path": None, "error": error}


_NARRATIVE_PROMPT = """Du verwandelst ein strukturiertes Audio-Briefing in einen flüssigen, zusammenhängenden Erzähltext im Podcast-Stil.

AUFGABE
Lies das komplette Briefing unten und schreibe es in einen einzigen, durchgehenden Text um, der sich beim Vorlesen wie ein guter NDR Info-Podcast anfühlt — homogen, mit thematischen Übergängen, ohne Zähler oder Listenstruktur.

REGELN
- NICHTS WEGLASSEN. Jeder Beitrag, jeder Fakt, jede Quelle muss erhalten bleiben. Kürze nur Wiederholungen.
- KEINE Beitragszähler ("Beitrag 5 von 51"), KEINE harten Trennzeichen ("Weiter geht's", "Ende der Podcastzusammenfassung"), KEINE Quellenüberschriften vor Artikeln.
- Quellen elegant in den Fließtext einweben: "wie das Tagblatt schreibt", "laut BBC", "die ZEIT berichtet", "ein Beitrag im Doppelgänger-Podcast"
- Thematische Übergänge bauen: chronologisch, geografisch (Tübingen → Region → Deutschland → International), thematisch (Politik → Wirtschaft → Tech → Kultur → Persönliches)
- Übergänge dürfen kurz sein: "Während in Berlin ...", "Ganz anders in den USA ...", "Ein anderes Thema beschäftigt gerade ..."
- Wetter als Auftakt, Recap und Essenz als Schluss-Klammer behalten — beides natürlich integrieren
- Podcast-Zusammenfassungen dürfen einen eigenen Absatz pro Episode bekommen, aber ohne harte Trennung — als gehörten sie zum Erzählfluss
- Sprache: gesprochen, aktiv, konkret. Keine Bullets, keine Aufzählungen, keine Markdown-Überschriften außer ggf. einem Eingangs-Titel
- Duze den Hörer wenn passend
- Umlaute korrekt: ä ö ü ß
- Länge: das fertige Ergebnis darf gerne lang sein — Ziel ist Vollständigkeit, nicht Kürze

STRUKTUR
1. Kurzer Einstieg (1-2 Sätze, was heute auf dem Programm steht)
2. Wetter als sanfter Tagesauftakt
3. Hauptteil: alle Beiträge in flüssiger Erzählung mit Übergängen
4. Recap/Essenz als Schluss-Klammer
5. Verabschiedung (falls im Original vorhanden)

Gib NUR den fertigen Erzähltext aus. Keine Meta-Kommentare, keine Erklärungen.

═══════════════════════════════════════════════════════════
HIER IST DAS BRIEFING:
═══════════════════════════════════════════════════════════

"""


def _build_narrative_export_text(sections: list, file_stamp: str) -> str:
    """Baut einen Rohtext-Export für Claude mit System-Prompt + allen Briefing-Inhalten."""
    parts = [_NARRATIVE_PROMPT]
    parts.append(f"Briefing vom {file_stamp}\n\n")
    for section in sections:
        if section.get("type") == "transition":
            continue
        content = (section.get("content") or "").strip()
        if not content:
            continue
        # Quellenlabel als Kontext mitgeben (für Claude, aber so dass er es einweben kann)
        source = section.get("source_label") or ""
        if source and source != "Quelle unbekannt" and not section.get("_weather") and not section.get("_recap") and not section.get("_essenz") and not section.get("_verabschiedung") and not section.get("_preview"):
            parts.append(f"[Quelle: {source}]\n")
        parts.append(content)
        parts.append("\n\n")
    return "".join(parts).rstrip() + "\n"


def _list_archived_briefings(limit: int = 12):
    archive_dir = _resolve_archive_dir()
    files = []
    try:
        if archive_dir.exists():
            files = [
                path for path in archive_dir.iterdir()
                if path.is_file() and path.suffix.lower() in _ARCHIVE_MIME_TYPES
            ]
    except Exception:
        files = []
    # Fallback: Der launchd-Hintergrunddienst kann iCloud NICHT auflisten (iterdir
    # liefert dort 0) — daher zusätzlich den lokalen Pfad-Index nutzen, der alle
    # geschriebenen iCloud-Dateien kennt. So erscheint das Archiv trotzdem.
    if not files:
        try:
            _idx = _LOCAL_META_MIRROR_DIR / ".archive_index.json"
            if _idx.exists():
                for _p in json.loads(_idx.read_text(encoding="utf-8")):
                    pp = Path(_p)
                    # exists() funktioniert im launchd-Dienst (nur Auflisten nicht) —
                    # so erscheinen keine längst gelöschten Dateien im Picker.
                    if pp.suffix.lower() in _ARCHIVE_MIME_TYPES and pp.exists():
                        files.append(pp)
        except Exception:
            pass

    def _sort_key(p):
        m = _CLEANUP_FILENAME_DATE_RE.search(p.name)
        if m:
            return m.group(0)
        try:
            return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        except Exception:
            return ""
    files = sorted(set(files), key=_sort_key, reverse=True)
    return files[:limit]


def _render_balance_panel(provider_key: str, title: str):
    reset_pending_key = _balance_skip_learning_reset_pending_key(provider_key)
    if st.session_state.get(reset_pending_key):
        st.session_state[_balance_skip_learning_key(provider_key)] = False
        st.session_state[reset_pending_key] = False

    current_remaining = st.session_state.get(_balance_remaining_key(provider_key))
    factor = float(st.session_state.get(_balance_multiplier_key(provider_key), 1.0) or 1.0)
    note = st.session_state.get(_balance_note_key(provider_key), "")
    error = st.session_state.get(_balance_error_key(provider_key))

    # -- Kopfzeile: Titel + aktueller Rest kompakt --
    rest_str = f" — Rest: {current_remaining:.2f} USD" if current_remaining is not None else ""
    factor_str = f" · Faktor {factor:.2f}" if abs(factor - 1.0) >= 0.01 else ""
    st.markdown(f"**{title}**{rest_str}{factor_str}")
    if note:
        st.caption(note)
    if error:
        st.caption(error)

    # -- Einzelnes Formular: Aufladen ODER Rest korrigieren --
    with st.form(f"{provider_key}_balance_combined_form", clear_on_submit=False):
        col_topup, col_actual = st.columns(2)
        with col_topup:
            st.text_input("Aufladen (+USD)", key=f"{provider_key}_topup_input", placeholder="z. B. 5.00")
        with col_actual:
            st.text_input("Tats. Rest (USD)", key=_balance_input_key(provider_key), placeholder="z. B. 10.50")
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            topup_submitted = st.form_submit_button("＋ Aufladen", use_container_width=True)
        with btn_col2:
            correct_submitted = st.form_submit_button("Stand korrigieren", use_container_width=True)
        st.checkbox("Ohne Lerneffekt (z. B. externer Verbrauch)", key=_balance_skip_learning_key(provider_key),
                     help="Aktivieren wenn außerhalb der App Guthaben verbraucht wurde — dann wird der Lernfaktor nicht angepasst.")

        if topup_submitted:
            topup_raw = st.session_state.get(f"{provider_key}_topup_input", "")
            if topup_raw.strip():
                topup_value, topup_error = _parse_optional_usd(topup_raw)
                if topup_error:
                    st.session_state[_balance_error_key(provider_key)] = topup_error
                elif topup_value is not None and topup_value > 0:
                    remaining_key = _balance_remaining_key(provider_key)
                    old_remaining = float(st.session_state.get(remaining_key) or 0.0)
                    st.session_state[remaining_key] = round(old_remaining + topup_value, 6)
                    st.session_state[_balance_note_key(provider_key)] = f"Aufladung +{topup_value:.2f} USD verbucht."
                    st.session_state[_balance_error_key(provider_key)] = None
                    _save_draft()
                    st.rerun()

        if correct_submitted:
            raw_value = st.session_state.get(_balance_input_key(provider_key), "")
            if raw_value.strip():
                skip_learning = bool(st.session_state.get(_balance_skip_learning_key(provider_key), False))
                ok = _apply_manual_balance(provider_key, raw_value, skip_learning=skip_learning)
                if ok:
                    st.session_state[_balance_skip_learning_reset_pending_key(provider_key)] = True
                    _save_draft()
                    st.rerun()


def _clear_briefing_state():
    for key in ("urls_text", "paywall_text", "podcast_text"):
        st.session_state[key] = ""
    _clear_topic_review_state()
    st.session_state.briefing_exports = None
    st.session_state.briefing_check = None
    st.session_state.briefing_sections = None
    st.session_state.genius_summary_text = None
    st.session_state.genius_summary_pdf = None
    st.session_state.genius_summary_meta = None
    st.session_state.confirm_clear = False
    # Dedup-/Vorab-Check-State mit zurücksetzen (sonst hängen alte Cluster/Verdikte)
    for _k in list(st.session_state.keys()):
        if isinstance(_k, str) and _k.startswith("dup_"):
            st.session_state.pop(_k, None)
    st.session_state["url_quality_results"] = None
    st.session_state.client_draft_clear_token = datetime.datetime.now().isoformat()
    _clear_persisted_last_briefing()
    _save_draft()


def _set_confirm_clear(value: bool):
    st.session_state.confirm_clear = value


def _render_mobile_input_buffer():
    clear_token = st.session_state.get("client_draft_clear_token", "")
    script = f"""
    <script>
    const root = window.parent.document;
    const clearToken = {json.dumps(clear_token)};
    const clearMarkerKey = 'audio_briefing_client_clear_token';
    const fields = [
      {{label: 'Artikel-URLs', key: 'audio_briefing_urls_text'}},
      {{label: 'Paywall-Artikel', key: 'audio_briefing_paywall_text'}},
      {{label: 'Podcast-Zusammenfassungen', key: 'audio_briefing_podcast_text'}},
    ];

    if (clearToken && localStorage.getItem(clearMarkerKey) !== clearToken) {{
      fields.forEach((field) => localStorage.removeItem(field.key));
      localStorage.setItem(clearMarkerKey, clearToken);
    }}

    function bindField(field) {{
      const textarea = root.querySelector(`textarea[aria-label="${{field.label}}"]`);
      if (!textarea) return;

      if (!textarea.dataset.audioBriefingBound) {{
        const saved = localStorage.getItem(field.key);
        // NUR in LEERE Felder zurückspielen: Server-Inhalt (z.B. eingefügte
        // Zusammenfassungen) darf nie von einem alten Handy-Puffer überschrieben werden.
        if (saved && !textarea.value) {{
          textarea.value = saved;
          textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
          textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}

        const save = () => localStorage.setItem(field.key, textarea.value || '');
        textarea.addEventListener('input', save);
        textarea.addEventListener('change', save);
        textarea.dataset.audioBriefingBound = '1';
      }}
    }}

    function bindAll() {{
      fields.forEach(bindField);
    }}

    bindAll();
    setInterval(bindAll, 800);
    window.addEventListener('visibilitychange', bindAll);
    window.addEventListener('pagehide', bindAll);
    </script>
    """
    components.html(script, height=0, width=0)


def _render_copy_to_clipboard_button(label: str, text: str, key: str):
    if not text:
        return
    button_id = f"copy_btn_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"
    payload = json.dumps(text)
    html = f"""
    <div style="padding:0;margin:0;">
      <button id="{button_id}" style="
        width:100%;
        min-height:42px;
        border:1px solid #e5e5e5;
        border-radius:10px;
        background:#ffffff;
        color:#1a1a1a;
        font-size:0.9rem;
        font-weight:600;
        box-shadow:0 1px 3px rgba(0,0,0,0.06);
        cursor:pointer;
        transition:all 0.2s ease;
      ">{label}</button>
    </div>
    <style>
      #{button_id}:hover {{
        border-color: #1a6b3c !important;
        color: #1a6b3c !important;
      }}
      @media (prefers-color-scheme: dark) {{
        #{button_id} {{
          background: #1e1e1e !important;
          color: #e0e0e0 !important;
          border-color: #333 !important;
          box-shadow: 0 1px 3px rgba(0,0,0,0.2) !important;
        }}
        #{button_id}:hover {{
          border-color: #1ed760 !important;
          color: #1ed760 !important;
        }}
      }}
    </style>
    <script>
    const button = document.getElementById({json.dumps(button_id)});
    const originalLabel = {json.dumps(label)};
    const payload = {payload};

    async function copyText() {{
      try {{
        await navigator.clipboard.writeText(payload);
        return true;
      }} catch (err) {{
        try {{
          const textarea = document.createElement('textarea');
          textarea.value = payload;
          textarea.style.position = 'fixed';
          textarea.style.opacity = '0';
          document.body.appendChild(textarea);
          textarea.focus();
          textarea.select();
          const ok = document.execCommand('copy');
          document.body.removeChild(textarea);
          return ok;
        }} catch (_) {{
          return false;
        }}
      }}
    }}

    button.addEventListener('click', async () => {{
      const ok = await copyText();
      button.innerText = ok ? 'Kopiert' : 'Kopieren fehlgeschlagen';
      setTimeout(() => {{
        button.innerText = originalLabel;
      }}, 1600);
    }});
    </script>
    """
    components.html(html, height=42)


def _build_content_check_report_text(check: dict) -> str:
    if not check:
        return ""
    content_check = check.get("content_check", {})
    output_lint = check.get("output_lint", {})
    if not content_check.get("enabled") and not output_lint.get("enabled"):
        return ""

    lines = [f"Briefing erzeugt mit {check.get('main_model') or 'Briefing-Modell'}."]
    if content_check.get("enabled"):
        model_name = content_check.get("model_display") or content_check.get("model", "Prüfmodell")
        lines.append(
            f"Geprüft mit {model_name}: {content_check.get('ok', 0)} unauffällig, "
            f"{content_check.get('warnings', 0)} wichtige Warnungen, "
            f"{content_check.get('notices', 0)} kleinere Hinweise, "
            f"{content_check.get('failed', 0)} fehlgeschlagen."
        )
        if content_check.get("summary_note"):
            lines.extend(["", content_check["summary_note"]])

    if output_lint.get("enabled"):
        lines.extend([
            "",
            (
                f"Lokaler Output-Lint: {output_lint.get('ok', 0)} unauffällig, "
                f"{output_lint.get('warnings', 0)} wichtige Warnungen, "
                f"{output_lint.get('notices', 0)} kleinere Hinweise, "
                f"{output_lint.get('failed', 0)} Prüfungen fehlgeschlagen."
            ),
        ])
        if output_lint.get("summary_note"):
            lines.extend(["", output_lint["summary_note"]])

    repair_summary = check.get("repair_summary") or {}
    if repair_summary:
        categories = repair_summary.get("categories", {})
        parts = []
        if categories.get("boilerplate"):
            parts.append(f"Boilerplate {categories['boilerplate']}")
        if categories.get("layout"):
            parts.append(f"Layout {categories['layout']}")
        if categories.get("quellen"):
            parts.append(f"Quellen {categories['quellen']}")
        if categories.get("inhalt"):
            parts.append(f"Inhalt {categories['inhalt']}")
        category_text = ", ".join(parts) if parts else "keine Kategorien erfasst"
        lines.extend([
            "",
            (
                f"Korrekturlauf {repair_summary.get('iteration', 1)} um {repair_summary.get('time_file', '--:--').replace('-', ':')} Uhr: "
                f"{repair_summary.get('sections_rebuilt', 0)} Abschnitte neu gebaut. "
                f"Warnungen {repair_summary.get('before', {}).get('warnings', 0)} → {repair_summary.get('after', {}).get('warnings', 0)}, "
                f"Hinweise {repair_summary.get('before', {}).get('notices', 0)} → {repair_summary.get('after', {}).get('notices', 0)}. "
                f"Schwerpunkte: {category_text}."
            ),
        ])

    all_items = []
    for item in content_check.get("items", []):
        item_copy = dict(item)
        item_copy["_origin_label"] = "Plausibilitäts-Check"
        all_items.append(item_copy)
    for item in output_lint.get("items", []):
        item_copy = dict(item)
        item_copy["_origin_label"] = "Output-Lint"
        all_items.append(item_copy)
    all_items.sort(key=lambda item: (item.get("section_index", 10**9), 0 if item.get("item_type") != "output_lint" else 1, item.get("label", "")))

    if all_items:
        lines.extend(["", "Plausibilitäts-Details"])
        for item in all_items:
            lines.extend(["", item.get("label", "Unbenannter Abschnitt")])
            if item.get("_origin_label"):
                lines.append(item["_origin_label"])
            level = item.get("level")
            if level == "warn":
                lines.append("Warnung")
            elif level == "notice":
                lines.append("Hinweis")
            summary = item.get("summary", "").strip()
            if summary:
                lines.append(summary)
            if item.get("faithfulness") is not None or item.get("coverage") is not None:
                scores = []
                if item.get("faithfulness") is not None:
                    scores.append(f"Faithfulness {item['faithfulness']}/5")
                if item.get("coverage") is not None:
                    scores.append(f"Coverage {item['coverage']}/5")
                if scores:
                    lines.append(" | ".join(scores))
            for issue in item.get("hard_issues", []):
                lines.append(f"- Warnung: {issue}")
            for issue in item.get("soft_issues", []):
                lines.append(f"- Hinweis: {issue}")
            if not item.get("hard_issues") and not item.get("soft_issues"):
                for issue in item.get("issues", []):
                    lines.append(f"- {issue}")
            for support in item.get("source_support", []):
                lines.append(f"- Quelltext-Stelle: {support}")
            for strength in item.get("strengths", []):
                lines.append(f"- Plus: {strength}")

    return "\n".join(lines).strip()


def _format_repair_summary_text(check: dict) -> str:
    summary = (check or {}).get("repair_summary") or {}
    if not summary:
        return ""
    categories = summary.get("categories", {})
    parts = []
    if categories.get("boilerplate"):
        parts.append(f"Boilerplate {categories['boilerplate']}")
    if categories.get("layout"):
        parts.append(f"Layout {categories['layout']}")
    if categories.get("quellen"):
        parts.append(f"Quellen {categories['quellen']}")
    if categories.get("inhalt"):
        parts.append(f"Inhalt {categories['inhalt']}")
    category_text = " · ".join(parts) if parts else "ohne Kategoriedetails"
    return (
        f"Korrekturlauf {summary.get('iteration', 1)} um {summary.get('time_file', '--:--').replace('-', ':')} Uhr: "
        f"{summary.get('sections_rebuilt', 0)} Abschnitte neu gebaut, "
        f"Warnungen {summary.get('before', {}).get('warnings', 0)} → {summary.get('after', {}).get('warnings', 0)}, "
        f"Hinweise {summary.get('before', {}).get('notices', 0)} → {summary.get('after', {}).get('notices', 0)}. "
        f"{category_text}."
    )


def _urls_need_visual_split(raw_text: str, parsed_urls: list[str]) -> bool:
    if len(parsed_urls) < 2:
        return False
    nonempty_lines = [line for line in raw_text.splitlines() if line.strip()]
    return len(nonempty_lines) < len(parsed_urls)


def _normalize_urls_text(raw_text: str) -> str:
    urls = extract_article_urls(raw_text)
    return "\n".join(urls)


def _schedule_clone_service_stop():
    launchd_label = (os.getenv("BRIEFING_INSTANCE_LAUNCHD_LABEL") or "").strip()
    if not launchd_label:
        return False
    stop_cmd = (
        "sleep 1; "
        "UID_NUM=$(id -u); "
        f"launchctl bootout gui/$UID_NUM/{launchd_label} >/dev/null 2>&1 || true"
    )
    try:
        subprocess.Popen(
            ["/bin/zsh", "-lc", stop_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _clear_topic_review_state():
    """No-op Stub – Topic-Dedup und Relevanzfilter wurden entfernt."""
    return None


def _parse_optional_usd(raw: str):
    cleaned = (raw or "").strip()
    if not cleaned:
        return None, None
    cleaned = cleaned.replace("€", "").replace("$", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        value = float(cleaned)
    except Exception:
        return None, "Bitte einen Betrag wie 12.50 oder 12,50 eingeben."
    if value < 0:
        return None, "Bitte keinen negativen Betrag eingeben."
    return round(value, 4), None


def _balance_remaining_key(provider_key: str) -> str:
    return f"{provider_key}_estimated_remaining_usd"


def _balance_error_key(provider_key: str) -> str:
    return f"{provider_key}_balance_error"


def _balance_multiplier_key(provider_key: str) -> str:
    return f"{provider_key}_balance_spend_multiplier"


def _balance_spend_since_key(provider_key: str) -> str:
    return f"{provider_key}_balance_spend_since_correction_usd"


def _balance_note_key(provider_key: str) -> str:
    return f"{provider_key}_balance_note"


def _balance_input_key(provider_key: str) -> str:
    return f"{provider_key}_manual_balance_input"


def _balance_prefill_source_key(provider_key: str) -> str:
    return f"{provider_key}_manual_balance_prefill_source"


def _balance_skip_learning_key(provider_key: str) -> str:
    return f"{provider_key}_balance_skip_learning"


def _balance_skip_learning_reset_pending_key(provider_key: str) -> str:
    return f"{provider_key}_balance_skip_learning_reset_pending"


def _sync_manual_balance_input(provider_key: str):
    current_remaining = st.session_state.get(_balance_remaining_key(provider_key))
    display_value = "" if current_remaining is None else f"{current_remaining:.2f}"
    source_key = _balance_prefill_source_key(provider_key)
    input_key = _balance_input_key(provider_key)
    if st.session_state.get(source_key) != display_value:
        st.session_state[input_key] = display_value
        st.session_state[source_key] = display_value


def _apply_manual_balance(provider_key: str, raw_value: str, skip_learning: bool = False):
    value, error = _parse_optional_usd(raw_value)
    st.session_state[_balance_error_key(provider_key)] = error
    if error is not None:
        st.session_state[_balance_note_key(provider_key)] = ""
        _save_draft()
        return False

    remaining_key = _balance_remaining_key(provider_key)
    current_remaining = st.session_state.get(remaining_key)
    spend_since_key = _balance_spend_since_key(provider_key)
    multiplier_key = _balance_multiplier_key(provider_key)
    current_multiplier = float(st.session_state.get(multiplier_key, 1.0) or 1.0)
    spend_since = float(st.session_state.get(spend_since_key, 0.0) or 0.0)

    notes = []
    if current_remaining is None:
        notes.append("Startwert gesetzt.")
    else:
        diff = value - float(current_remaining)
        if abs(diff) < 0.01:
            notes.append("Manuell bestätigt.")
        else:
            notes.append("Korrektur übernommen.")

        if skip_learning:
            notes.append("Lernfaktor bewusst unverändert.")
        elif spend_since >= 0.01:
            observed_spend = float(current_remaining) - value
            raw_ratio = observed_spend / spend_since if spend_since else None
            if raw_ratio is not None and 0.5 <= raw_ratio <= 1.5:
                learned_multiplier = (current_multiplier * 0.75) + (raw_ratio * 0.25)
                learned_multiplier = round(max(0.5, min(1.5, learned_multiplier)), 4)
                st.session_state[multiplier_key] = learned_multiplier
                notes.append(f"Lernfaktor jetzt {learned_multiplier:.2f}.")
            else:
                notes.append("Lernfaktor wegen starker Abweichung unverändert.")

    st.session_state[remaining_key] = value
    st.session_state[spend_since_key] = 0.0
    st.session_state[_balance_note_key(provider_key)] = " ".join(notes).strip()
    _save_draft()
    return True


def _apply_cost_to_balances(check: dict):
    if not check:
        return
    cost = check.get("cost_delta") or check.get("cost", {})
    if not cost:
        return

    spent_by_provider = {"OpenAI": 0.0, "Anthropic": 0.0}
    for item in cost.get("models", []):
        provider = item.get("provider")
        if provider in spent_by_provider:
            spent_by_provider[provider] += float(item.get("usd", 0.0) or 0.0)

    provider_map = {
        "OpenAI": "openai",
        "Anthropic": "anthropic",
    }
    updated = False
    for provider_name, prefix in provider_map.items():
        spent = float(spent_by_provider.get(provider_name, 0.0) or 0.0)
        remaining_key = _balance_remaining_key(prefix)
        current_remaining = st.session_state.get(remaining_key)
        if spent and current_remaining is not None:
            multiplier = float(st.session_state.get(_balance_multiplier_key(prefix), 1.0) or 1.0)
            adjusted_spent = spent * multiplier
            st.session_state[remaining_key] = round(current_remaining - adjusted_spent, 6)
            st.session_state[_balance_spend_since_key(prefix)] = round(
                float(st.session_state.get(_balance_spend_since_key(prefix), 0.0) or 0.0) + spent,
                6,
            )
            updated = True

    if updated:
        _save_draft()


_saved_draft = _load_draft()
_saved_exports, _saved_check, _saved_sections = _load_last_briefing()

# Auto-Aufräumen: Briefings älter als 21 Tage einmal pro Session löschen
if not st.session_state.get("_cleanup_done"):
    try:
        _cleanup_result = _cleanup_old_briefings()
        st.session_state["_cleanup_done"] = True
        st.session_state["_cleanup_count"] = len(_cleanup_result.get("deleted", []))
        st.session_state["_cleanup_deleted"] = _cleanup_result.get("deleted", [])
        st.session_state["_cleanup_errors"] = _cleanup_result.get("errors", [])
    except Exception:
        st.session_state["_cleanup_done"] = True
        st.session_state["_cleanup_count"] = 0
# Sichtbare Rückmeldung des Aufräumens (nur wenn wirklich etwas gelöscht wurde)
if st.session_state.get("_cleanup_count", 0) and not st.session_state.get("_cleanup_shown"):
    st.session_state["_cleanup_shown"] = True
    try:
        st.toast(f"🧹 {st.session_state['_cleanup_count']} alte Briefing-Dateien (>21 Tage) aufgeräumt.")
    except Exception:
        pass
for _key, _default in _DRAFT_DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _saved_draft.get(_key, _default)

if "briefing_exports" not in st.session_state:
    st.session_state.briefing_exports = _saved_exports
if "briefing_check" not in st.session_state:
    st.session_state.briefing_check = _saved_check
if "briefing_sections" not in st.session_state:
    st.session_state.briefing_sections = _saved_sections
if "genius_summary_text" not in st.session_state:
    st.session_state.genius_summary_text = None
if "genius_summary_pdf" not in st.session_state:
    st.session_state.genius_summary_pdf = None
if "genius_summary_meta" not in st.session_state:
    st.session_state.genius_summary_meta = None
if "confirm_clear" not in st.session_state:
    st.session_state.confirm_clear = False
if "client_draft_clear_token" not in st.session_state:
    st.session_state.client_draft_clear_token = ""
if "urls_text_pending_value" not in st.session_state:
    st.session_state.urls_text_pending_value = None
if "paywall_text_pending_value" not in st.session_state:
    st.session_state.paywall_text_pending_value = None
if "podcast_text_pending_value" not in st.session_state:
    st.session_state.podcast_text_pending_value = None

for _provider_key in ("openai", "anthropic"):
    _sync_manual_balance_input(_provider_key)

# ============================================================
# CUSTOM CSS
# ============================================================

st.markdown("""
<style>
    /* ===== DESIGN SYSTEM — clean, minimal, editorial ===== */
    :root {
        --b-green: #1a6b3c;
        --b-green-light: #e8f5ee;
        --b-green-accent: #1ed760;
        --b-ink: #1a1a1a;
        --b-secondary: #4a4a4a;
        --b-muted: #7a7a7a;
        --b-faint: #a0a0a0;
        --b-border: #e5e5e5;
        --b-bg: #fafafa;
        --b-card: #ffffff;
        --b-shadow: 0 1px 2px rgba(0,0,0,0.05), 0 8px 18px rgba(0,0,0,0.04);
        --b-shadow-lg: 0 8px 22px rgba(0,0,0,0.08);
        --b-radius: 8px;
        --b-radius-sm: 8px;
        --b-warn: #b7791f;
        --b-warn-bg: #fff7e6;
        --b-danger: #b42318;
        --b-danger-bg: #fff1f0;
    }

    * { -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
    footer { visibility: hidden; }
    html, body, [data-testid="stAppViewContainer"], .main,
    section[data-testid="stSidebar"] { -webkit-overflow-scrolling: touch; }

    [data-testid="stAppViewContainer"] {
        background: var(--b-bg);
    }
    .block-container {
        max-width: 1080px;
        margin: 0 auto;
        padding-top: 1rem;
        padding-bottom: 3rem;
    }

    /* ===== SIDEBAR ===== */
    [data-testid="stSidebar"] > div:first-child {
        background: var(--b-card);
        border-right: 1px solid var(--b-border);
    }
    [data-testid="stSidebar"] .stRadio > div,
    [data-testid="stSidebar"] .stCheckbox,
    [data-testid="stSidebar"] .stSelectbox,
    [data-testid="stSidebar"] .stTextInput {
        background: var(--b-bg);
        border: 1px solid var(--b-border);
        border-radius: var(--b-radius-sm);
        padding: 0.35rem 0.55rem;
        transition: border-color 0.15s ease;
    }
    [data-testid="stSidebar"] .stRadio > div:hover,
    [data-testid="stSidebar"] .stCheckbox:hover,
    [data-testid="stSidebar"] .stSelectbox:hover,
    [data-testid="stSidebar"] .stTextInput:hover {
        border-color: var(--b-green);
    }

    /* ===== HERO — compact dark header card ===== */
    .briefing-hero {
        position: relative;
        overflow: hidden;
        padding: 1.35rem 1.45rem;
        margin: 0 0 1.35rem 0;
        border-radius: var(--b-radius);
        background: linear-gradient(135deg, #1a3a2a 0%, #1a4a32 50%, #1a3a28 100%);
        color: #ffffff;
        box-shadow: var(--b-shadow-lg);
    }
    .briefing-hero::before, .briefing-hero::after { display: none; }

    .briefing-hero-grid {
        position: relative;
        z-index: 1;
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 1.5rem;
        align-items: start;
    }
    .briefing-kicker { display: none; }

    .briefing-title-row {
        display: flex;
        align-items: flex-start;
        gap: 0;
        flex-direction: column;
    }
    .briefing-brand-icon-wrap { display: none; }

    .briefing-title-copy h1 {
        margin: 0;
        color: #ffffff;
        font-size: 1.65rem;
        line-height: 1.15;
        font-weight: 800;
        letter-spacing: 0;
    }
    .briefing-date {
        margin: 0.15rem 0 0 0;
        color: rgba(255,255,255,0.6);
        font-size: 0.85rem;
        font-weight: 400;
    }
    .briefing-subline {
        margin: 0.5rem 0 0 0;
        color: rgba(255,255,255,0.7);
        font-size: 0.88rem;
        line-height: 1.45;
        max-width: 32rem;
    }
    .briefing-instance {
        margin: 0.3rem 0 0 0;
        color: rgba(255,255,255,0.45);
        font-size: 0.78rem;
    }

    .briefing-chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.85rem;
        margin-top: 0.7rem;
    }
    .briefing-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0;
        border-radius: 0;
        background: transparent;
        border: none;
        color: rgba(255,255,255,0.85);
        font-size: 0.78rem;
        font-weight: 500;
        box-shadow: none;
        cursor: default;
        user-select: none;
    }
    .briefing-chip::before {
        content: "";
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--b-green-accent);
        flex-shrink: 0;
    }
    .briefing-copyright {
        margin-top: 0.6rem;
        color: rgba(255,255,255,0.3);
        font-size: 0.7rem;
    }

    /* Hero workflow panel (right side) */
    .briefing-hero-panel {
        position: relative;
        padding: 1rem 1.1rem;
        border-radius: var(--b-radius);
        background: rgba(255,255,255,0.1);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        color: #ffffff;
        min-width: 220px;
        max-width: 280px;
    }
    .briefing-hero-panel::after { display: none; }
    .briefing-panel-label {
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: rgba(255,255,255,0.5);
        margin-bottom: 0.4rem;
        font-weight: 600;
    }
    .briefing-panel-title {
        font-size: 0.88rem;
        font-weight: 600;
        line-height: 1.35;
        margin: 0 0 0.5rem 0;
        color: rgba(255,255,255,0.9);
    }
    .briefing-panel-copy {
        font-size: 0.8rem;
        line-height: 1.45;
        color: rgba(255,255,255,0.55);
        margin: 0 0 0.7rem 0;
    }
    .briefing-panel-tags {
        display: flex;
        flex-wrap: wrap;
        gap: 0.55rem;
    }
    .briefing-panel-tags span {
        display: inline-flex;
        align-items: center;
        gap: 0.3rem;
        padding: 0;
        border-radius: 0;
        background: transparent;
        border: none;
        font-size: 0.74rem;
        font-weight: 500;
        color: rgba(255,255,255,0.75);
    }
    .briefing-panel-tags span::before {
        content: "";
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--b-green-accent);
        flex-shrink: 0;
    }

    .briefing-flow {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.55rem;
        margin: -0.75rem 0 1.25rem 0;
    }
    .briefing-flow-step {
        padding: 0.72rem 0.78rem;
        border-radius: var(--b-radius);
        background: var(--b-card);
        border: 1px solid var(--b-border);
        box-shadow: var(--b-shadow);
        min-height: 74px;
    }
    .briefing-flow-step strong {
        display: block;
        color: var(--b-ink);
        font-size: 0.86rem;
        line-height: 1.2;
        margin-bottom: 0.22rem;
    }
    .briefing-flow-step span {
        display: block;
        color: var(--b-muted);
        font-size: 0.76rem;
        line-height: 1.35;
    }

    /* ===== SECTION LABELS (h4) — simple bold, not card ===== */
    h4 {
        margin-top: 1.6rem;
        margin-bottom: 0.35rem;
        font-size: 1.05rem;
        letter-spacing: 0;
        text-transform: none;
        color: var(--b-ink);
        font-weight: 700;
    }

    /* ===== PILLS ===== */
    .briefing-pill-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.4rem;
        margin: 0.4rem 0 0.1rem 0;
    }
    .briefing-pill {
        display: inline-flex;
        align-items: center;
        padding: 0.3rem 0.6rem;
        border-radius: 8px;
        background: var(--b-green-light);
        border: none;
        box-shadow: none;
        color: var(--b-green);
        font-size: 0.8rem;
        font-weight: 600;
    }

    /* ===== URL META ===== */
    .briefing-url-meta {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        flex-wrap: wrap;
        margin: 0.2rem 0 0.4rem 0;
    }
    .briefing-url-count {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.3rem 0.6rem;
        border-radius: 8px;
        background: var(--b-ink);
        color: #ffffff;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .briefing-url-count strong {
        color: var(--b-green-accent);
        font-weight: 700;
    }
    .briefing-url-note {
        color: var(--b-muted);
        font-size: 0.82rem;
    }

    /* ===== SECTION HEADERS ===== */
    .briefing-section-head {
        margin: 1.6rem 0 0.85rem 0;
        padding: 0 0 0 0.85rem;
        border-left: 3px solid var(--b-green);
        color: var(--b-ink);
    }
    .briefing-section-head .eyebrow {
        display: inline-block;
        margin-bottom: 0.3rem;
        font-size: 0.68rem;
        font-weight: 600;
        letter-spacing: 0;
        text-transform: uppercase;
        color: var(--b-green);
    }
    .briefing-section-head h3 {
        margin: 0;
        font-size: 1.05rem;
        line-height: 1.25;
        font-weight: 700;
        color: var(--b-ink);
    }
    .briefing-section-head p {
        margin: 0.3rem 0 0 0;
        color: var(--b-muted);
        font-size: 0.82rem;
        line-height: 1.4;
    }

    /* ===== METRIC CARDS ===== */
    .briefing-metrics-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
        gap: 0.6rem;
        margin: 0.7rem 0 0.8rem 0;
    }
    .briefing-metric-card {
        padding: 0.75rem 0.7rem;
        border-radius: var(--b-radius-sm);
        background: var(--b-card);
        border: 1px solid var(--b-border);
        box-shadow: var(--b-shadow);
        transition: transform 0.15s ease;
    }
    .briefing-metric-card:hover { transform: translateY(-1px); }
    .briefing-metric-state { font-size: 1.1rem; margin-bottom: 0.15rem; }
    .briefing-metric-value { font-size: 0.9rem; font-weight: 700; color: var(--b-ink); }
    .briefing-metric-label {
        margin-top: 0.1rem;
        font-size: 0.7rem;
        color: var(--b-muted);
        text-transform: uppercase;
        letter-spacing: 0;
        font-weight: 500;
    }

    /* ===== INFO / STATUS ===== */
    .briefing-info-card {
        margin: 0.7rem 0;
        padding: 0.85rem 1rem;
        border-radius: var(--b-radius-sm);
        background: var(--b-card);
        border: 1px solid var(--b-border);
        box-shadow: var(--b-shadow);
    }
    .briefing-info-card strong { color: var(--b-ink); }

    .briefing-status-banner {
        margin: 0.2rem 0 1rem 0;
        padding: 0.9rem 1rem;
        border-radius: var(--b-radius);
        background: var(--b-green-light);
        border: 1px solid #c3e6d1;
        box-shadow: none;
    }
    .briefing-status-banner h3 { margin: 0; color: var(--b-green); font-size: 1.05rem; font-weight: 700; }
    .briefing-status-banner p { margin: 0.3rem 0 0 0; color: var(--b-secondary); font-size: 0.85rem; }
    .briefing-status-banner.warn {
        background: var(--b-warn-bg);
        border-color: #f4d38f;
    }
    .briefing-status-banner.warn h3 { color: var(--b-warn); }
    .briefing-status-banner.danger {
        background: var(--b-danger-bg);
        border-color: #ffc9c5;
    }
    .briefing-status-banner.danger h3 { color: var(--b-danger); }
    .briefing-status-row {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 0.55rem;
        margin: 0.75rem 0 0 0;
    }
    .briefing-status-cell {
        padding: 0.55rem 0.62rem;
        border-radius: var(--b-radius-sm);
        background: rgba(255,255,255,0.55);
        border: 1px solid rgba(0,0,0,0.04);
    }
    .briefing-status-cell strong {
        display: block;
        font-size: 0.9rem;
        color: var(--b-ink);
    }
    .briefing-status-cell span {
        display: block;
        margin-top: 0.12rem;
        font-size: 0.72rem;
        color: var(--b-muted);
        text-transform: uppercase;
        letter-spacing: 0;
        font-weight: 650;
    }

    .briefing-download-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 0.65rem;
        margin: 0.75rem 0 0.45rem 0;
    }
    .briefing-download-card {
        padding: 0.8rem 0.85rem;
        border-radius: var(--b-radius);
        background: var(--b-card);
        border: 1px solid var(--b-border);
        box-shadow: var(--b-shadow);
    }
    .briefing-download-card strong {
        display: block;
        color: var(--b-ink);
        font-size: 0.92rem;
        margin-bottom: 0.18rem;
    }
    .briefing-download-card span {
        display: block;
        color: var(--b-muted);
        font-size: 0.76rem;
        line-height: 1.35;
    }

    .briefing-detail-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(165px, 1fr));
        gap: 0.62rem;
        margin: 0.85rem 0 1rem 0;
    }
    .briefing-detail-card {
        padding: 0.75rem 0.8rem;
        border-radius: var(--b-radius);
        background: var(--b-card);
        border: 1px solid var(--b-border);
        box-shadow: var(--b-shadow);
    }
    .briefing-detail-card strong {
        display: block;
        color: var(--b-ink);
        font-size: 0.95rem;
        line-height: 1.25;
        margin-bottom: 0.18rem;
    }
    .briefing-detail-card span {
        display: block;
        color: var(--b-muted);
        font-size: 0.75rem;
        line-height: 1.35;
    }
    .briefing-detail-card.ok { border-left: 3px solid var(--b-green); }
    .briefing-detail-card.warn { border-left: 3px solid var(--b-warn); }
    .briefing-detail-card.danger { border-left: 3px solid var(--b-danger); }

    /* ===== LISTS ===== */
    .stMarkdown ul { list-style: none; padding-left: 0; margin: 0.25rem 0 0.5rem 0; }
    .stMarkdown ul li {
        position: relative;
        padding: 0.25rem 0 0.25rem 1rem;
        font-size: 0.88rem;
        line-height: 1.5;
        color: var(--b-secondary);
        border: none !important;
        background: none !important;
        box-shadow: none !important;
        border-radius: 0 !important;
        cursor: default;
    }
    .stMarkdown ul li::before {
        content: "";
        position: absolute;
        left: 0;
        top: 0.68rem;
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: var(--b-green);
        opacity: 0.4;
    }
    .stMarkdown ul li code {
        font-size: 0.8rem;
        padding: 0.12rem 0.35rem;
        border-radius: 4px;
        background: var(--b-bg);
        color: var(--b-ink);
        border: 1px solid var(--b-border);
    }

    /* ===== INPUTS ===== */
    .stTextArea textarea {
        font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
        font-size: 0.84rem;
        line-height: 1.5;
        border-radius: var(--b-radius-sm);
        border: 1px solid var(--b-border);
        box-shadow: none;
        background: var(--b-card);
        transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }
    .stTextArea textarea:focus,
    .stTextInput input:focus {
        border-color: var(--b-green) !important;
        box-shadow: 0 0 0 3px rgba(26, 107, 60, 0.08) !important;
    }

    /* ===== BUTTONS ===== */
    .stButton > button, .stDownloadButton > button {
        min-height: 44px;
        border-radius: var(--b-radius-sm);
        border: 1px solid var(--b-border);
        background: var(--b-card);
        color: var(--b-ink);
        font-weight: 600;
        font-size: 0.9rem;
        box-shadow: none;
        transition: all 0.15s ease;
    }
    .stButton > button:hover, .stDownloadButton > button:hover {
        border-color: var(--b-green);
        background: var(--b-bg);
    }
    .stButton > button:active, .stDownloadButton > button:active {
        background: var(--b-border);
    }
    .stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
        background: var(--b-green);
        color: #ffffff;
        border-color: var(--b-green);
        font-weight: 700;
    }
    .stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover {
        background: #1a7a42;
        border-color: #1a7a42;
    }

    /* ===== PROGRESS ===== */
    .stProgress > div > div { background: var(--b-border); border-radius: 999px; height: 5px; }
    .stProgress > div > div > div > div {
        background: linear-gradient(90deg, var(--b-green) 0%, var(--b-green-accent) 100%);
        border-radius: 999px;
    }

    /* ===== EXPANDER ===== */
    [data-testid="stExpander"] {
        background: var(--b-card);
        border: 1px solid var(--b-border);
        border-radius: var(--b-radius-sm);
        box-shadow: none;
        overflow: hidden;
    }

    .status-text { font-size: 0.86rem; color: var(--b-muted); padding: 0.4rem 0; }
    hr { border: none; height: 1px; background: var(--b-border); margin: 1.2rem 0; }

    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.1); border-radius: 3px; }

    /* ===== RESPONSIVE ===== */
    @media (max-width: 980px) {
        .briefing-hero-grid { grid-template-columns: 1fr; }
        .briefing-hero-panel { max-width: none; }
        .briefing-flow { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .briefing-status-row { grid-template-columns: 1fr; }
    }
    @media (max-width: 640px) {
        .block-container { padding-top: 0.8rem; }
        .briefing-hero { padding: 1.1rem 1.2rem; border-radius: var(--b-radius); }
        .briefing-title-copy h1 { font-size: 1.35rem; }
        .briefing-subline { font-size: 0.82rem; }
        .briefing-url-meta { align-items: flex-start; }
        .briefing-flow { grid-template-columns: 1fr; }
    }

    /* ===== DARK MODE ===== */
    @media (prefers-color-scheme: dark) {
        :root {
            --b-ink: #e4e8e5;
            --b-secondary: #b8bfba;
            --b-muted: #7a827d;
            --b-faint: #5a625d;
            --b-border: rgba(255,255,255,0.08);
            --b-bg: #0e1210;
            --b-card: #181c1a;
            --b-shadow: 0 1px 3px rgba(0,0,0,0.2);
            --b-shadow-lg: 0 4px 16px rgba(0,0,0,0.3);
            --b-green: #2ecc71;
            --b-green-light: rgba(46,204,113,0.08);
            --b-green-accent: #3ce87a;
            --b-warn-bg: rgba(183,121,31,0.12);
            --b-danger-bg: rgba(180,35,24,0.12);
        }

        [data-testid="stAppViewContainer"] {
            background: var(--b-bg) !important;
        }
        [data-testid="stSidebar"] > div:first-child {
            background: #141816 !important;
            border-right: 1px solid var(--b-border);
        }
        [data-testid="stSidebar"] .stRadio > div,
        [data-testid="stSidebar"] .stCheckbox,
        [data-testid="stSidebar"] .stSelectbox,
        [data-testid="stSidebar"] .stTextInput {
            background: rgba(255,255,255,0.03);
            border-color: var(--b-border);
        }

        .briefing-hero {
            background: linear-gradient(135deg, #122218 0%, #163024 50%, #122218 100%) !important;
        }
        .briefing-hero-panel {
            background: rgba(255,255,255,0.06);
        }

        .briefing-chip { color: rgba(255,255,255,0.7); }
        .briefing-copyright { color: rgba(255,255,255,0.2); }
        .briefing-pill { background: var(--b-green-light); color: var(--b-green); }

        .briefing-url-count { background: rgba(255,255,255,0.08); color: var(--b-ink); }

        .briefing-section-head {
            background: linear-gradient(135deg, #122218 0%, #163024 100%);
        }

        .briefing-status-banner {
            background: var(--b-green-light);
            border-color: rgba(46,204,113,0.15);
        }
        .briefing-status-banner h3 { color: var(--b-green); }
        .briefing-status-cell {
            background: rgba(255,255,255,0.04);
            border-color: var(--b-border);
        }

        h4 { color: var(--b-green); }

        .stMarkdown ul li { color: var(--b-secondary); }
        .stMarkdown ul li code {
            background: rgba(255,255,255,0.04);
            border-color: var(--b-border);
            color: var(--b-ink);
        }

        .stTextArea textarea {
            background: var(--b-card);
            border-color: var(--b-border);
            color: var(--b-ink);
        }
        .stTextInput input {
            background: var(--b-card);
            border: 1px solid var(--b-border);
            color: var(--b-ink);
        }

        .stButton > button, .stDownloadButton > button {
            background: var(--b-card);
            border-color: var(--b-border);
            color: var(--b-ink);
        }
        .stButton > button:hover, .stDownloadButton > button:hover {
            border-color: var(--b-green);
            background: rgba(255,255,255,0.04);
        }
        .stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
            background: var(--b-green);
            color: #0a1a0e;
            border-color: var(--b-green);
        }

        .stProgress > div > div { background: var(--b-border); }

        .stMarkdown, .stMarkdown p, .stMarkdown li,
        .stMarkdown span, .stText { color: var(--b-ink) !important; }
        .stSelectbox label, .stTextInput label,
        .stTextArea label, .stCheckbox label,
        .stRadio label, .stSlider label { color: var(--b-ink) !important; }
        .stSelectbox [data-baseweb="select"] {
            background: var(--b-card);
            border-color: var(--b-border);
        }

        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); }
    }
</style>
""", unsafe_allow_html=True)

# ============================================================
# HEADER
# ============================================================

# Deutsche Wochentage/Monate (locale-unabhängig)
_WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONATE = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
           "Juli", "August", "September", "Oktober", "November", "Dezember"]
_today = datetime.date.today()
_date_str = f"{_WOCHENTAGE[_today.weekday()]}, {_today.day}. {_MONATE[_today.month]} {_today.year}"
_instance_label = (os.getenv("BRIEFING_INSTANCE_LABEL") or "").strip()
_instance_port = (os.getenv("BRIEFING_INSTANCE_PORT") or "").strip()
_instance_line = ""
if _instance_label:
    _instance_line = f"<p class='briefing-instance'>Instanz: {html.escape(_instance_label)}"
    if _instance_port:
        _instance_line += f" · Port {html.escape(_instance_port)}"
    _instance_line += "</p>"
_hero_icon_html = (
    f"<img src=\"{_ICON_DATA_URI}\" alt=\"Audio-Briefing\">"
    if _ICON_DATA_URI
    else "<div class='briefing-brand-fallback'>AB</div>"
)
st.markdown(
    """
<div class="briefing-hero">
  <div class="briefing-hero-grid">
    <div class="briefing-hero-copy">
      <div class="briefing-kicker">Audio-first Briefings</div>
      <div class="briefing-title-row">
        <div class="briefing-brand-icon-wrap">"""
    + _hero_icon_html +
    """</div>
        <div class="briefing-title-copy">
          <h1>Audio-Briefing</h1>
          <p class="briefing-date">"""
    + html.escape(_date_str) +
    """</p>
          <p class="briefing-subline">Quellen rein, sauber geprüftes Audio-Briefing raus — <strong>kostenlos</strong> über dein Claude Max-Abo. Optimiert für mobile Nutzung, TTS und iCloud-Archiv.</p>
          """
    + _instance_line +
    """
        </div>
      </div>
      <div class="briefing-chip-row">
        <span class="briefing-chip">Mobil stabil</span>
        <span class="briefing-chip">Qualitäts-Gate</span>
        <span class="briefing-chip">Lang-Kompakt</span>
        <span class="briefing-chip">PDF + TXT</span>
        <span class="briefing-chip">iCloud-Archiv</span>
      </div>
      <div class="briefing-copyright">© Florian Sebastian Thiel</div>
    </div>
    <div class="briefing-hero-panel">
      <div class="briefing-panel-label">Workflow</div>
      <div class="briefing-panel-title">Jeder Lauf erzeugt Vollbriefing, Lang-Kompaktfassung, Qualitätsprüfung und Archivdateien.</div>
      <p class="briefing-panel-copy">Standard ist der kostenlose Claude-Weg — ein Klick genügt, optionale Schalter bleiben eingeklappt.</p>
      <div class="briefing-panel-tags">
        <span>Duplikatfilter</span>
        <span>Repair</span>
        <span>PDF</span>
        <span>Reader-TXT</span>
      </div>
    </div>
  </div>
</div>
<div class="briefing-flow">
  <div class="briefing-flow-step"><strong>1. Quellen sammeln</strong><span>URLs, Paywall-Texte, Podcasts und Wetter laufen in einen einheitlichen Pool.</span></div>
  <div class="briefing-flow-step"><strong>2. Duplikate filtern</strong><span>Artikel und Rohtexte werden vor der Ausgabe gegeneinander geprüft.</span></div>
  <div class="briefing-flow-step"><strong>3. Qualität prüfen</strong><span>Plausibilitäts-Check, Output-Lint und Auto-Repair sind fest aktiv.</span></div>
  <div class="briefing-flow-step"><strong>4. PDF archivieren</strong><span>Voll-PDF, Reader-TXT und Lang-Kompaktfassung sind der Standard.</span></div>
</div>
""",
    unsafe_allow_html=True,
)

# ============================================================
# State-Init (API-Setup steht im API-Block weiter unten)
# ============================================================

st.session_state["content_check_mode"] = "warn"

if os.getenv("BRIEFING_INSTANCE_LAUNCHD_LABEL"):
    st.caption("ℹ️ Diese Instanz läuft als Hintergrunddienst.")

# Default-Provider beibehalten (verhindert KeyError beim Erststart)
if "provider" not in st.session_state:
    st.session_state["provider"] = "OpenAI (GPT)"

st.markdown('<div id="nav-top" style="position:relative; top:-64px;"></div>', unsafe_allow_html=True)
st.markdown("### 📥 Eingabe")
st.caption("URLs, Paywall-Text und Podcast-Zusammenfassungen — was nicht eingetragen ist, fliegt nicht ins Briefing.")
st.caption("➡️ So startest du: hier Quellen einfügen, dann unten im grünen Block auf „🤖 Briefing automatisch via Claude bauen (kostenlos)“ klicken.")

# URLs
if st.session_state.get("urls_text_pending_value") is not None:
    st.session_state["urls_text"] = st.session_state.get("urls_text_pending_value") or ""
    st.session_state["urls_text_pending_value"] = None
    _save_draft()

# Paywall
if st.session_state.get("paywall_text_pending_value") is not None:
    st.session_state["paywall_text"] = st.session_state.get("paywall_text_pending_value") or ""
    st.session_state["paywall_text_pending_value"] = None
    _save_draft()

# Podcasts (Einwurf-Feld hängt fertige Zusammenfassungen hier an)
if st.session_state.get("special_topics_text_pending_value") is not None:
    st.session_state["special_topics_text"] = st.session_state.get("special_topics_text_pending_value") or ""
    st.session_state["special_topics_text_pending_value"] = None
if st.session_state.get("podcast_text_pending_value") is not None:
    st.session_state["podcast_text"] = st.session_state.get("podcast_text_pending_value") or ""
    st.session_state["podcast_text_pending_value"] = None
    _save_draft()
if st.session_state.get("raw_transcript_inbox_clear"):
    st.session_state["raw_transcript_inbox"] = ""
    st.session_state["raw_transcript_inbox_clear"] = False

# --- Scroll-to-Bottom per Streamlit components.html ---

def _scroll_textarea(aria_label: str):
    """Scrollt ein Streamlit-Textarea anhand seines aria-label nach unten."""
    components.html(f"""
    <script>
    (function() {{
        const areas = window.parent.document.querySelectorAll('textarea');
        for (const ta of areas) {{
            if (ta.getAttribute('aria-label') === '{aria_label}') {{
                ta.scrollTop = ta.scrollHeight;
                ta.focus();
                return;
            }}
        }}
    }})();
    </script>
    """, height=0)

st.markdown('<div id="nav-urls" style="position:relative; top:-64px;"></div>', unsafe_allow_html=True)
st.markdown("#### Artikel-URLs")
urls_text = st.text_area(
    "Artikel-URLs",
    placeholder="https://www.example.com/artikel-1\nhttps://www.example.com/artikel-2\nhttps://...",
    height=180,
    help="Eine URL pro Zeile oder mehrere direkt hintereinander mit neuem https://. Werden automatisch abgerufen und zusammengefasst.",
    key="urls_text",
    label_visibility="collapsed",
)
_url_desc_col, _url_scroll_col = st.columns([6, 1])
with _url_desc_col:
    with st.expander("ℹ️ Hinweise zur URL-Eingabe", expanded=False):
        st.caption("Eine URL pro Zeile ist ideal — die App erkennt aber auch verklebte Links automatisch, sobald der nächste mit `https://` oder `http://` beginnt. Doppelte Links werden nur einmal verarbeitet.")
with _url_scroll_col:
    if st.button("↓ Ende", key="scroll_urls", use_container_width=True):
        _scroll_textarea("Artikel-URLs")
url_preview = inspect_article_urls(urls_text)
st.markdown(
    f"<div class='briefing-url-meta'><span class='briefing-url-count'>Aktuell erkannt: <strong>{len(url_preview['urls'])}</strong> URL{'s' if len(url_preview['urls']) != 1 else ''}</span><span class='briefing-url-note'>Nicht-Artikel-Links werden automatisch markiert und später übersprungen.</span></div>",
    unsafe_allow_html=True,
)
split_detected = _urls_need_visual_split(urls_text, url_preview["urls"])
split_info_col, split_action_col = st.columns([3.2, 1.2])
with split_info_col:
    if split_detected:
        st.caption(f"ℹ️ Verklebte Links erkannt: {len(url_preview['urls'])} URLs wurden getrennt erkannt.")
    else:
        st.caption("Optional: erkannte URLs automatisch in einzelne Zeilen aufteilen.")
with split_action_col:
    if st.button(
        "URLs sauber trennen",
        key="normalize_urls_button",
        use_container_width=True,
        disabled=len(url_preview["urls"]) < 2 or not urls_text.strip(),
    ):
        st.session_state["urls_text_pending_value"] = _normalize_urls_text(urls_text)
        st.rerun()

# Pre-Flight: Quellen vorab prüfen (Fetch-Qualität), BEVOR das Briefing gebaut wird —
# damit schwache URLs (Cookie-Wall/Paywall/Fehler) rausfliegen können, solange es noch geht.
if urls_text.strip():
    if st.button("🔍 Quellen vorab prüfen", use_container_width=True,
                 help="Lädt alle URLs einmal und zeigt: (1) welche kaum/keinen Text liefern (Cookie-Wall, Paywall, Fehler) und (2) welche URLs vermutlich DIESELBE Story doppelt sind — BEVOR du das Briefing baust. So kannst du schwache oder doppelte Quellen vorher rauswerfen. Dauert ~eine Minute (Laden + kurzer Doppel-Check via Claude, kostenlos übers Abo)."):
        _check_urls = _extract_article_urls_internal(urls_text)[0]
        if not _check_urls:
            st.caption("Keine gültigen URLs erkannt.")
        else:
            with st.spinner(f"{len(_check_urls)} Quellen werden geladen und geprüft…"):
                _q = assess_article_fetch_quality(_check_urls)
            _bad = [r for r in _q if not r.get("ok")]
            if _bad:
                st.warning(f"⚠️ {len(_bad)} von {len(_q)} URLs liefern kaum/keinen brauchbaren Text:")
                _ic = {"failed": "🔴", "thin": "⚠️", "boilerplate": "🟠"}
                for r in _bad:
                    st.caption(f"{_ic.get(r.get('level'), '⚠️')} {r.get('word_count', 0)} Wörter — {r.get('url')}")
                st.caption("Tipp: diese URLs rauswerfen oder den Artikeltext direkt als Paywall-Text einfügen — dann ist das Briefing vollständig.")
            else:
                st.success(f"✅ Alle {len(_q)} URLs liefern brauchbaren Text — du kannst loslegen.")
            # Doppel-Storys VOR dem Lauf sichtbar machen (dein Vorfilter): Mathe schlägt
            # Kandidaten vor, Opus bestätigt streng — nur echte Same-Event-Paare werden gezeigt.
            _dup_clusters = find_potential_topic_duplicates(_q)
            if _dup_clusters:
                with st.spinner(f"{len(_dup_clusters)} mögliche Doppel-Themen — Claude prüft kurz (gleiche Story vs. Blickwinkel)…"):
                    try:
                        _dup_verdict = llm_confirm_duplicate_clusters(_dup_clusters, model="opus")
                    except Exception:
                        _dup_verdict = {}
                _same_story = []
                for _ci, _cl in enumerate(_dup_clusters, 1):
                    _v = _dup_verdict.get(_ci) or {}
                    if _v.get("is_duplicate"):
                        _same_story.append((_cl, _v))
                if _same_story:
                    st.warning(f"👯 {len(_same_story)} Doppel-Story(s) — dieselbe Geschichte steckt mehrfach in deinen URLs:")
                    for _cl, _v in _same_story:
                        _mems = _cl.get("members", [])
                        _idx_part = " + ".join(f"URL {m.get('index', 0) + 1} ({m.get('source_label', '?')})" for m in _mems)
                        st.caption(f"· {_idx_part} — {str(_v.get('reason', ''))[:90]}")
                        for _m in _mems:
                            st.caption(f"   ↳ {_m.get('url', '?')}")
                    st.caption("Du kannst je eine URL entfernen — oder alles drinlassen: der Auto-Merge fasst sie beim Erstellen ohnehin zu EINEM Beitrag zusammen.")
                elif _dup_verdict:
                    st.caption("👍 Keine Doppel-Storys — die ähnlich wirkenden URLs sind verschiedene Blickwinkel.")

# Paywall-Artikel
st.markdown('<div id="nav-paywall" style="position:relative; top:-64px;"></div>', unsafe_allow_html=True)
st.markdown("#### Paywall-Artikel")
paywall_text = st.text_area(
    "Paywall-Artikel",
    placeholder='Roh kopierten Artikeltext oder fertige Briefings hier einfügen.\nMehrere Blöcke mit mmm, Mmmmmm, ---, ===== oder "Artikel Ende" trennen.\nFertige Briefings gehen auch weiter mit "Weiter geht\u2019s."',
    height=220,
    help="Hier kannst du fertige Paywall-Briefings oder direkt kopierten Artikeltext einfügen. Rohtexte werden automatisch mit dem Artikel-Prompt zusammengefasst.",
    key="paywall_text",
    label_visibility="collapsed",
)
_paywall_blocks = split_paywall_articles(paywall_text) if paywall_text.strip() else []
st.markdown(
    f"<div class='briefing-url-meta'><span class='briefing-url-count'>Aktuell erkannt: <strong>{len(_paywall_blocks)}</strong> {'Blöcke' if len(_paywall_blocks) != 1 else 'Block'}</span></div>",
    unsafe_allow_html=True,
)

# Paywall-Erkennung: warnt wenn ein Block offensichtlich abgeschnitten ist
_truncated = detect_truncated_paywall_blocks(_paywall_blocks) if _paywall_blocks else []
if _truncated:
    st.warning(
        f"⚠️ {len(_truncated)} Block{'e' if len(_truncated) != 1 else ''} sind offensichtlich abgeschnitten "
        f"(Paywall-Hinweis statt Volltext erkannt). "
        f"Du warst beim Kopieren wahrscheinlich nicht eingeloggt."
    )
    with st.expander(f"❌ Details + Aktionen — {len(_truncated)} abgeschnittene Blöcke", expanded=True):
        for t in _truncated:
            block_no = t["index"] + 1
            st.markdown(f"**Block {block_no}** — nur {t['content_length']} Zeichen Inhalt vor dem Paywall-Marker")
            if t.get("url"):
                st.markdown(f"  → URL: `{t['url']}`")
            else:
                st.markdown("  → Keine URL im Block — bitte manuell in SWP nachholen.")

        st.markdown("---")
        st.markdown("**Was möchtest du tun?**")

        _act_col1, _act_col2 = st.columns(2)
        with _act_col1:
            if st.button(
                "🗑️ Abgeschnittene Blöcke entfernen",
                key="remove_truncated_paywall",
                use_container_width=True,
                help="Entfernt nur die abgeschnittenen Blöcke aus dem Paywall-Feld. Die anderen bleiben drin. Danach kannst du die nachgeholten Volltexte einfach unten anhängen.",
            ):
                _truncated_indices = {t["index"] for t in _truncated}
                _kept_blocks = [b for i, b in enumerate(_paywall_blocks) if i not in _truncated_indices]
                _new_text = "\n\nmmm\n\n".join(_kept_blocks)
                if _new_text and not _new_text.endswith("mmm"):
                    _new_text += "\n\nmmm\n"
                st.session_state["paywall_text_pending_value"] = _new_text
                st.session_state["paywall_text_pending_set"] = True
                st.success(f"{len(_truncated)} abgeschnittene Block{'e' if len(_truncated) != 1 else ''} entfernt. Hol jetzt die Volltexte und füge sie unten an.")
                st.rerun()
        with _act_col2:
            if st.button(
                "✅ Trotzdem behalten",
                key="keep_truncated_paywall",
                use_container_width=True,
                help="Lässt die abgeschnittenen Blöcke wie sie sind. Sie werden ins Briefing übernommen, sind aber inhaltlich dünn.",
            ):
                st.info("OK, abgeschnittene Blöcke bleiben drin. Sie werden ins Briefing übernommen.")

        st.caption(
            "**Workflow-Tipp:** 1) Auf 'Entfernen' klicken, 2) bei SWP einloggen, 3) Volltexte neu kopieren "
            "und unten ans Paywall-Feld anhängen (mit `mmm` davor). Künftig: Vor dem Kopieren immer erst einloggen!"
        )
_pw_desc_col, _pw_scroll_col = st.columns([6, 1])
with _pw_desc_col:
    with st.expander("ℹ️ Hinweise zur Paywall-Eingabe", expanded=False):
        st.caption("Hier kannst du fertige Briefings oder roh kopierten Artikeltext einfügen. Rohtexte werden automatisch zusammengefasst, fertige Briefings nur formatiert. Mehrere Blöcke trennst du mit drei oder mehr `m` (`mmm`/`Mmmmmm`), mit `---` oder mit `Artikel Ende`. Doppelte Blöcke landen nur einmal im Briefing.")
        st.caption("Auf iPhone puffert die App diese Eingabe zusätzlich lokal im Browser, damit ein kurzer App-Wechsel den zuletzt getippten Text nicht verliert.")
with _pw_scroll_col:
    if st.button("↓ Ende", key="scroll_paywall", use_container_width=True):
        _scroll_textarea("Paywall-Artikel")

# Podcast-Zusammenfassungen
st.markdown('<div id="nav-podcast" style="position:relative; top:-64px;"></div>', unsafe_allow_html=True)
st.markdown("#### Podcast-Zusammenfassungen")

def _maybe_autostart_briefing(source: str):
    """🚀 Auto-Start: Wenn die Option an ist und KEINE Podcast-Arbeit mehr offen
    (keine Runde, keine Hintergrund-Jobs, keine Whisper-Frage/-Arbeit), Briefing zünden."""
    if not st.session_state.get("auto_briefing_when_done"):
        return
    if (st.session_state.get("apple_round") or st.session_state.get("_round_jobs")
            or st.session_state.get("whisper_running") or st.session_state.get("whisper_queue")):
        return
    st.session_state["_auto_run_briefing"] = True
    st.session_state["_podcast_inbox_last_msg"] = f"🚀 Podcasts fertig ({source}) — das Briefing startet automatisch…"


# ── 📡 Episoden-Inbox: neue Folgen aus den OPML-Feeds (Pocket-Casts-Ersatz, optional) ──
st.markdown('<div id="nav-inbox" style="position:relative; top:-64px;"></div>', unsafe_allow_html=True)
with st.expander("📡 Episoden-Inbox — neue Folgen aus deinen Feeds", expanded=False):
    st.caption("Zeigt NUR neue Folgen im gewählten Zeitfenster — nie den Back-Katalog. Archiviertes bleibt dauerhaft weg. 📄 = Transkript im Feed (null Klicks nötig) · 🍎 = einmal in Apple Podcasts antippen, dann unten abholen. Der Pocket-Casts-Weg übers Einwurf-Feld bleibt wie gehabt.")
    _ib_c1, _ib_c2 = st.columns([2, 3])
    with _ib_c1:
        _ib_days = st.selectbox("Zeitfenster", [1, 2, 3, 7, 14], index=2, key="podcast_inbox_days",
                                format_func=lambda d: "letzte 24 Stunden" if d == 1 else f"letzte {d} Tage")
    with _ib_c2:
        st.write("")
        if st.button("🔄 Neue Episoden laden", key="podcast_inbox_fetch", use_container_width=True):
            with st.spinner("Prüfe alle Feeds (parallel, ~15-30s) — englische Titel werden fürs Anzeigen übersetzt…"):
                st.session_state["podcast_inbox_data"] = fetch_new_podcast_episodes(days=int(_ib_days))
                try:
                    attach_inbox_translations(st.session_state["podcast_inbox_data"].get("episodes") or [])
                except Exception:
                    pass
                podcast_inbox_cache_save(st.session_state["podcast_inbox_data"])
    if st.session_state.get("podcast_inbox_data") is None:
        # Frische Session (Browserwechsel/Neustart): letzten Stand von Platte holen —
        # die Liste bleibt, bis ein neuer Fetch sie ersetzt; Erledigtes ist rausgefiltert.
        _ib_cached = podcast_inbox_cache_load()
        if _ib_cached and _ib_cached.get("episodes"):
            st.session_state["podcast_inbox_data"] = _ib_cached
    _ib = st.session_state.get("podcast_inbox_data") or {}
    if _ib.get("cached_at"):
        try:
            _ca_dt = datetime.datetime.fromisoformat(_ib["cached_at"])
            _ca_min = int((datetime.datetime.now(_ca_dt.tzinfo) - _ca_dt).total_seconds() // 60)
            _ca_lbl = f"vor {_ca_min} Min" if _ca_min < 120 else f"vor {_ca_min // 60} Std"
            st.caption(f"📥 Stand vom letzten Laden ({_ca_lbl}) — 🔄 drücken für frische Folgen.")
        except Exception:
            pass
    _ib_eps = _ib.get("episodes") or []
    if _ib.get("errors"):
        st.caption(f"⚠️ {len(_ib['errors'])} Feed(s) nicht erreichbar (u.a. {_ib['errors'][0][:50]}…)")
    if _ib_eps:
        _apple_ok = apple_container_accessible()

        def _inbox_done(_g):
            # NUR bei wirklich eingefügtem Transkript aufrufen: markiert erledigt
            # UND nimmt die Folge sofort aus der angezeigten Liste (Florians Regel).
            _d0 = st.session_state.get("podcast_inbox_data") or {}
            _meta0 = [x for x in (_d0.get("episodes") or []) if x.get("guid") == _g]
            podcast_inbox_mark([_g], "summarized", eps_meta=_meta0)
            podcast_inbox_mark([_g], "archived", eps_meta=_meta0)
            _d0["episodes"] = [x for x in (_d0.get("episodes") or []) if x.get("guid") != _g]
            st.session_state["podcast_inbox_data"] = _d0

        _n_auto = sum(1 for e in _ib_eps if e.get("transcript_url"))
        st.caption(f"**{len(_ib_eps)} neue Folgen** aus {_ib.get('n_feeds', '?')} Feeds — davon {_n_auto} mit 📄 Feed-Transkript (vollautomatisch).")
        if "_ibx_sel_all_apply" in st.session_state:
            _apply_v = bool(st.session_state.pop("_ibx_sel_all_apply"))
            for _e0 in _ib_eps[:60]:
                st.session_state[f"ibx_{_e0['guid']}"] = _apply_v
            st.session_state["ibx_select_all"] = _apply_v
            st.session_state["_ibx_sel_all_prev"] = _apply_v
        _sel_all_ibx = st.checkbox(f"Alle auswählen ({len(_ib_eps[:60])})", key="ibx_select_all",
                                   help="Setzt alle Häkchen auf einmal — danach kannst du einzelne wieder abwählen. Nochmal klicken wählt alle ab.")
        if bool(_sel_all_ibx) != bool(st.session_state.get("_ibx_sel_all_prev", False)):
            # Umschalten wirkt auf die echten Zeilen-Häkchen (sichtbar!) — Werte werden
            # VOR der Instanziierung der Zeilen-Checkboxen gesetzt, das erlaubt Streamlit.
            for _e0 in _ib_eps[:60]:
                st.session_state[f"ibx_{_e0['guid']}"] = bool(_sel_all_ibx)
            st.session_state["_ibx_sel_all_prev"] = bool(_sel_all_ibx)
        if not st.session_state.get("_ibx_sel_seeded"):
            # Gespeicherte Auswahl wiederherstellen (überlebt Reload/Deploy) —
            # nur einmal pro Session, damit bewusstes Abwählen nicht überschrieben wird.
            for _g0 in podcast_inbox_selection_load():
                st.session_state.setdefault(f"ibx_{_g0}", True)
            st.session_state["_ibx_sel_seeded"] = True
        _ll_guids = {_x["guid"] for _x in podcast_listen_list()}
        for _e in _ib_eps[:60]:
            _cols = st.columns([0.8, 1.1, 8.5, 2.2, 0.8, 0.8])
            with _cols[0]:
                st.checkbox(" ", key=f"ibx_{_e['guid']}", label_visibility="collapsed")
            with _cols[1]:
                if _e.get("image"):
                    try:
                        st.image(_e["image"], width=52)
                    except Exception:
                        st.markdown("🎙️")
                else:
                    st.markdown("🎙️")
            with _cols[2]:
                _badge = "📄" if _e.get("transcript_url") else "🍎"
                _done = " · ✅ schon zusammengefasst" if _e.get("summarized") else ""
                if _e["guid"] in _ll_guids:
                    _done += " · 🎧 gemerkt"
                _age = f"vor {_e['age_h']}h" if _e["age_h"] < 48 else f"vor {_e['age_h'] // 24}d"
                _dm = _e.get("duration_min")
                if _dm:
                    _age += " · ⏱️ " + (f"{_dm // 60} Std {_dm % 60:02d}" if _dm >= 60 else f"{_dm} Min")
                _desc_html = ""
                if _e.get("desc"):
                    _desc_html = f"  \n<small style='opacity:.65'>{html.escape(_e['desc'])}</small>"
                _title_disp = _e.get("title_de") or _e["title"]
                _desc_disp = _e.get("desc_de") or _e.get("desc")
                if _desc_disp and _e.get("desc_de"):
                    _desc_html = f"  \n<small style='opacity:.65'>{html.escape(_desc_disp)}</small>"
                st.markdown(
                    f"{_badge} **{html.escape(_title_disp)}**  \n"
                    f"<small>{html.escape(_e['feed'])} · {_age}{_done}</small>{_desc_html}",
                    unsafe_allow_html=True,
                )
                if _e.get("title_de"):
                    with st.popover("🇬🇧 Original", use_container_width=False):
                        st.markdown(f"**{html.escape(_e['title'])}**")
                        if _e.get("desc"):
                            st.caption(_e["desc"])
            with _cols[3]:
                if not _e.get("transcript_url") and not _apple_ok:
                    st.caption("🎙️ via ✨ (lokal)")
                elif not _e.get("transcript_url"):
                    if st.button("🍎 holen", key=f"ibo_{_e['guid']}", use_container_width=True,
                                 disabled=bool(st.session_state.get("apple_round")),
                                 help="Startet eine EINZEL-Runde nur für diese Folge (Anzeige: Folge 1/1). Für alle angehakten 🍎-Folgen: unten Apple-Runde starten. Gesperrt, solange schon eine Runde läuft."):
                        st.session_state["apple_round"] = {"eps": [_e], "idx": 0, "opened": None, "collected": []}
                        st.rerun()
            with _cols[4]:
                _on_ll = _e["guid"] in _ll_guids
                if st.button("✔️🎧" if _on_ll else "🎧", key=f"iblisten_{_e['guid']}",
                             help=("Steht auf der Anhören-Merkliste — Klick nimmt sie wieder runter." if _on_ll
                                   else "Zum Anhören merken (für Pocket Casts) — nur ein Merkzettel: die Folge bleibt hier in der Inbox, zusammenfassen geht weiterhin.")):
                    if _on_ll:
                        podcast_listen_list_remove([_e["guid"]])
                        st.session_state["_podcast_inbox_last_msg"] = f"🎧 Von der Merkliste genommen: {_e['title'][:60]}"
                    else:
                        podcast_listen_list_add(_e)
                        st.session_state["_podcast_inbox_last_msg"] = f"🎧 Gemerkt fürs Anhören: {_e['title'][:60]} — bleibt in der Inbox."
                    st.rerun()
            with _cols[5]:
                if st.button("🗑️", key=f"ibarch_{_e['guid']}",
                             help="Archivieren wie in Pocket Casts — verschwindet sofort und taucht in der Inbox nie wieder auf. Rückholbar übers 🗂️-Archiv unten."):
                    podcast_inbox_mark([_e["guid"]], "archived", eps_meta=[_e])
                    _d9 = st.session_state.get("podcast_inbox_data") or {}
                    _d9["episodes"] = [x for x in (_d9.get("episodes") or []) if x.get("guid") != _e["guid"]]
                    st.session_state["podcast_inbox_data"] = _d9
                    st.rerun()

        _sel_all_bottom = st.checkbox(f"Alle auswählen ({len(_ib_eps[:60])})", key="ibx_select_all_bottom",
                                      help="Wie das Kästchen oben — nur bequem hier unten bei den Knöpfen.")
        if bool(_sel_all_bottom) != bool(st.session_state.get("_ibx_sel_all_bottom_prev", False)):
            st.session_state["_ibx_sel_all_bottom_prev"] = bool(_sel_all_bottom)
            st.session_state["_ibx_sel_all_apply"] = bool(_sel_all_bottom)
            st.rerun()
        _act1, _act2, _act3 = st.columns(3)
        _sel_guids = [e["guid"] for e in _ib_eps[:60] if st.session_state.get(f"ibx_{e['guid']}")]
        if st.session_state.get("_ibx_sel_persisted") != _sel_guids:
            podcast_inbox_selection_save(_sel_guids)
            st.session_state["_ibx_sel_persisted"] = _sel_guids
        with _act1:
            if st.button(f"✨ Ausgewählte zusammenfassen ({len(_sel_guids)})", key="ibx_summarize",
                         use_container_width=True, disabled=not _sel_guids):
                _sel_eps = [e for e in _ib_eps if e["guid"] in _sel_guids]
                _auto = [e for e in _sel_eps if e.get("transcript_url")]
                _manual = [e for e in _sel_eps if not e.get("transcript_url")]
                _sums, _errs = [], []
                _pr = st.progress(0)
                _stt = st.empty()

                def _process_auto(_ea):
                    # Läuft im Worker-Thread: NUR Netz/Subprozess, kein st.*!
                    _txta = download_feed_transcript(_ea["transcript_url"], _ea.get("transcript_type"))
                    if not _txta or len(_txta) < 500:
                        return (_ea, None, "Transkript-Download leer")
                    _ra = summarize_podcast_transcript_via_cli(f"Podcast: {_ea['feed']} — Episode: {_ea['title']}\n\n{_txta}")
                    if _ra.get("ok"):
                        return (_ea, _ra["summary"], None)
                    return (_ea, None, str(_ra.get("error", "?"))[:120])

                if _auto:
                    from concurrent.futures import ThreadPoolExecutor as _AutoPool, as_completed as _auto_done
                    _stt.caption(f"0/{len(_auto)} fertig — zwei Folgen laufen parallel…")
                    _dn = 0
                    with _AutoPool(max_workers=2) as _apx:
                        _afuts = {_apx.submit(_process_auto, _ea): _ea for _ea in _auto}
                        for _fa in _auto_done(_afuts):
                            try:
                                _ea, _suma, _erra = _fa.result()
                            except Exception as _exa9:
                                _ea, _suma, _erra = _afuts[_fa], None, str(_exa9)[:120]
                            _dn += 1
                            if _suma:
                                _sums.append(_suma)
                                _inbox_done(_ea["guid"])
                            else:
                                _errs.append(f"{_ea['title'][:40]}: {_erra}")
                            _pr.progress(_dn / len(_auto))
                            _stt.caption(f"{_dn}/{len(_auto)} fertig — zuletzt: {_ea['title'][:45]}")
                _pr.empty()
                _stt.empty()
                if _manual and _apple_ok:
                    _errs.append(f"{len(_manual)} 🍎-Folge(n) übersprungen — dafür die 🍎 Apple-Runde nutzen (fertige Apple-Transkripte, viel schneller als lokal).")
                    _manual = []
                if _manual:
                    # NIE automatisch whispern — in die Warteschlange, die Frage-UI übernimmt.
                    _wq8 = st.session_state.get("whisper_queue") or []
                    _known8 = {x.get("guid") for x in _wq8}
                    _wq8 += [m for m in _manual if m["guid"] not in _known8]
                    st.session_state["whisper_queue"] = _wq8
                if _sums:
                    st.session_state["podcast_text_pending_value"] = combine_podcast_field(
                        st.session_state.get("podcast_text"), _sums)
                    st.session_state["_podcast_inbox_last_msg"] = f"✅ {len(_sums)} Folge(n) zusammengefasst und unten angefügt."
                if _errs:
                    st.session_state["_podcast_inbox_errors"] = _errs
                _maybe_autostart_briefing("✨-Zusammenfassen")
                st.rerun()
        with _act2:
            if st.button(f"🗑️ Ausgewählte archivieren ({len(_sel_guids)})", key="ibx_archive",
                         use_container_width=True, disabled=not _sel_guids):
                podcast_inbox_mark(_sel_guids, "archived", eps_meta=[e for e in _ib_eps if e["guid"] in _sel_guids])
                _d = st.session_state.get("podcast_inbox_data") or {}
                _d["episodes"] = [e for e in (_d.get("episodes") or []) if e["guid"] not in _sel_guids]
                st.session_state["podcast_inbox_data"] = _d
                st.rerun()
        with _act3:
            if st.button("🗑️ ALLE hier archivieren", key="ibx_archive_all", use_container_width=True,
                         help="Markiert alle aktuell angezeigten Folgen als erledigt — wie Aufräumen in Pocket Casts."):
                podcast_inbox_mark([e["guid"] for e in _ib_eps], "archived", eps_meta=list(_ib_eps))
                st.session_state["podcast_inbox_data"] = {"episodes": [], "n_feeds": _ib.get("n_feeds"), "errors": []}
                st.rerun()
        _sel_apple = [e for e in _ib_eps[:60] if e["guid"] in _sel_guids and not e.get("transcript_url")] if _apple_ok else []
        _nachlese = list_unimported_ttml(max_age_h=48) if _apple_ok else []
        if not _apple_ok:
            st.caption("ℹ️ 🍎-Folgen laufen über die LOKALE Transkription (einfach anhaken + ✨ — dauert ~5 Min pro Podcast-Stunde, völlig automatisch). Der Apple-Transkript-Weg ist für den Hintergrunddienst von macOS gesperrt.")
            with st.expander("🍎 Apple-Weg trotzdem freischalten (optional)", expanded=False):
                st.markdown("Systemeinstellungen → **Datenschutz & Sicherheit** → **Voller Festplattenzugriff** → ➕ → mit **⌘⇧G** diesen Pfad einfügen:\n```\n/Library/Developer/CommandLineTools/usr/bin/python3\n```\nDann aktivieren und die Briefing-App neu starten (Knopf unten in der App oder beim nächsten Mac-Start automatisch). Danach erscheinen hier 🍎-Runde und Nachlese.")
        _act4, _act5 = st.columns(2)
        with _act4:
            _round_active = bool(st.session_state.get("apple_round"))
            if st.button(f"🍎 Apple-Runde starten ({len(_sel_apple)})", key="ibx_apple_round",
                         use_container_width=True, disabled=(not _sel_apple) or _round_active,
                         help="Öffnet die ausgewählten 🍎-Folgen NACHEINANDER in Apple Podcasts — du tippst dort jeweils nur aufs Transkript, die App merkt es, öffnet sofort die nächste Folge und fasst am Ende alles in einem Rutsch zusammen. Kein Zeitdruck: pro Folge kannst du auch überspringen oder sie in die lokale Warteschlange legen."):
                st.session_state["apple_round"] = {"eps": _sel_apple, "idx": 0, "opened": None, "collected": []}
                st.rerun()
        with _act5:
            if st.button(f"🥡 Nachlese: {len(_nachlese)} angesehene(s) Transkript(e) einsammeln", key="ibx_nachlese",
                         use_container_width=True, disabled=not _nachlese,
                         help="Sammelt alle Transkripte ein, die du in den letzten 48h in Apple Podcasts angesehen hast und die noch nicht importiert wurden — fasst sie zusammen und hängt sie unten an. Perfekt, wenn du Transkripte angeschaut hast, während die App nicht zugehört hat."):
                _box2 = st.empty()
                _nsums, _nerrs = [], []

                def _process_nl(_np):
                    try:
                        _ntxt = apple_ttml_to_text(_np)
                    except Exception as _nex:
                        return (_np, None, f"TTML unlesbar: {str(_nex)[:80]}")
                    _nr = summarize_podcast_transcript_via_cli(_ntxt)
                    if _nr.get("ok"):
                        return (_np, _nr["summary"], None)
                    return (_np, None, str(_nr.get("error", "?"))[:120])

                from concurrent.futures import ThreadPoolExecutor as _NlPool, as_completed as _nl_done
                _box2.info(f"🥡 0/{len(_nachlese)} fertig — zwei parallel…")
                _ndn = 0
                with _NlPool(max_workers=2) as _npx:
                    _nfuts = {_npx.submit(_process_nl, _np9): _np9 for _np9 in _nachlese}
                    for _nf in _nl_done(_nfuts):
                        try:
                            _np9, _sumn, _errn = _nf.result()
                        except Exception as _nex2:
                            _np9, _sumn, _errn = _nfuts[_nf], None, str(_nex2)[:120]
                        _ndn += 1
                        if _sumn:
                            _nsums.append(_sumn)
                            mark_ttml_imported([_np9])
                        else:
                            _nerrs.append(_errn)
                        _box2.info(f"🥡 {_ndn}/{len(_nachlese)} fertig…")
                _box2.empty()
                if _nsums:
                    st.session_state["podcast_text_pending_value"] = combine_podcast_field(
                        st.session_state.get("podcast_text"), _nsums)
                    st.session_state["_podcast_inbox_last_msg"] = f"🥡✅ Nachlese: {len(_nsums)} Transkript(e) zusammengefasst und unten angefügt."
                if _nerrs:
                    st.session_state["_podcast_inbox_errors"] = _nerrs
                st.rerun()

        # ---- 🍎 Apple-Runde: reaktive Schrittmaschine — kein Countdown, DU entscheidest ----
        _ar = st.session_state.get("apple_round")
        if _ar:
            _ar_eps = _ar.get("eps") or []
            _ar_i = int(_ar.get("idx") or 0)
            if _ar_i >= len(_ar_eps):
                # Runde fertig — die Zusammenfassungen laufen längst im Hintergrund;
                # der Kollektor unten fügt sie ein, sobald sie fertig sind.
                _n_open = len(st.session_state.get("_round_jobs") or [])
                _wq_n = len(st.session_state.get("whisper_queue") or [])
                _msg9 = "🍎 Runde fertig."
                if _n_open:
                    _msg9 += f" {_n_open} Zusammenfassung(en) laufen im Hintergrund und erscheinen unten automatisch."
                if _wq_n:
                    _msg9 += f" {_wq_n} Folge(n) warten in der Whisper-Frage unten."
                st.session_state["_podcast_inbox_last_msg"] = _msg9
                st.session_state["apple_round"] = None
                st.rerun()
            else:
                _cur = _ar_eps[_ar_i]
                if not _ar.get("opened"):
                    _aurl = resolve_apple_episode_url(_cur["feed"], _cur["title"])
                    _ar["open_failed"] = not (_aurl and open_in_apple_podcasts(_aurl))
                    _ar["opened"] = datetime.datetime.now().timestamp()
                    st.session_state["apple_round"] = _ar
                    st.rerun()
                _wait_s = int(datetime.datetime.now().timestamp() - float(_ar["opened"]))
                if _ar.get("open_failed"):
                    st.warning(f"🍎 Folge {_ar_i + 1}/{len(_ar_eps)}: **{_cur['title'][:60]}** ließ sich nicht automatisch öffnen — in Apple Podcasts manuell suchen und aufs Transkript tippen, oder unten entscheiden.")
                else:
                    st.info(f"🍎 Folge {_ar_i + 1}/{len(_ar_eps)}: **{_cur['title'][:60]}** — in Apple Podcasts aufs Transkript tippen, ich erkenne es automatisch. (Warte seit {_wait_s}s, kein Zeitlimit.)")
                _rc1, _rc2, _rc3 = st.columns(3)
                with _rc1:
                    if st.button("⏭️ Überspringen", key=f"ar_skip_{_ar_i}", use_container_width=True,
                                 help="Weiter zur nächsten Folge — diese bleibt unangetastet in der Liste (z.B. später per Nachlese holen)."):
                        _ar["idx"] = _ar_i + 1
                        _ar["opened"] = None
                        st.session_state["apple_round"] = _ar
                        st.rerun()
                with _rc2:
                    if st.button("🎙️ Whisper-Warteschlange", key=f"ar_wq_{_ar_i}", use_container_width=True,
                                 help="Kein Transkript bei Apple (bei ganz frischen Folgen normal)? Kommt in die lokale Warteschlange — NACH der Runde entscheidest du, ob sofort transkribiert wird oder ob wir auf Apple warten."):
                        _wq0 = st.session_state.get("whisper_queue") or []
                        if _cur["guid"] not in [x.get("guid") for x in _wq0]:
                            _wq0.append(_cur)
                        st.session_state["whisper_queue"] = _wq0
                        _ar["idx"] = _ar_i + 1
                        _ar["opened"] = None
                        st.session_state["apple_round"] = _ar
                        st.rerun()
                with _rc3:
                    if st.button("⏹️ Runde beenden", key=f"ar_stop_{_ar_i}", use_container_width=True,
                                 help="Restliche Folgen abbrechen — bereits erkannte Transkripte werden noch zusammengefasst."):
                        _ar["eps"] = _ar_eps[:_ar_i]
                        _ar["idx"] = len(_ar["eps"])
                        st.session_state["apple_round"] = _ar
                        st.rerun()
                # Kurzer Horch-Schritt (4s), dann sofort neu rendern — so bleiben die Knöpfe klickbar.
                _hit = wait_for_new_apple_ttml(float(_ar["opened"]) - 2, timeout_s=4)
                _seen_paths = set(st.session_state.get("_round_paths") or [])
                if _hit and _hit not in _seen_paths:
                    # Sofort im Hintergrund zusammenfassen (2 parallel) — die Runde läuft
                    # ohne Wartezeit weiter, Ergebnisse sammelt der Kollektor unten ein.
                    _seen_paths.add(_hit)
                    st.session_state["_round_paths"] = list(_seen_paths)
                    try:
                        _txtj = apple_ttml_to_text(_hit)
                        if st.session_state.get("_sum_pool") is None:
                            from concurrent.futures import ThreadPoolExecutor as _SumPool
                            st.session_state["_sum_pool"] = _SumPool(max_workers=2)
                        _futj = st.session_state["_sum_pool"].submit(
                            summarize_podcast_transcript_via_cli,
                            f"Podcast: {_cur['feed']} — Episode: {_cur['title']}\n\n{_txtj}")
                        _jobs9 = st.session_state.get("_round_jobs") or []
                        _jobs9.append({"guid": _cur["guid"], "title": _cur["title"], "path": _hit, "fut": _futj})
                        st.session_state["_round_jobs"] = _jobs9
                    except Exception as _jex:
                        st.session_state["_podcast_inbox_errors"] = (st.session_state.get("_podcast_inbox_errors") or []) + [f"{_cur['title'][:40]}: TTML unlesbar ({str(_jex)[:80]})"]
                    _ar["idx"] = _ar_i + 1
                    _ar["opened"] = None
                    st.session_state["apple_round"] = _ar
                    if _ar["idx"] >= len(_ar_eps):
                        try:
                            subprocess.Popen(["open", "http://localhost:8501"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception:
                            pass
                st.rerun()

        # ---- 🎙️ Whisper-Warteschlange: fragt erst, läuft dann Folge für Folge (stoppbar) ----
        _wq = st.session_state.get("whisper_queue") or []
        if _wq and not st.session_state.get("apple_round"):
            if not st.session_state.get("whisper_running"):
                st.warning("🎙️ Ohne Apple-Transkript: " + " · ".join(_e9["title"][:38] for _e9 in _wq[:4]) + (" …" if len(_wq) > 4 else ""))
                _qc1, _qc2 = st.columns(2)
                with _qc1:
                    if st.button(f"🎙️ Jetzt lokal transkribieren ({len(_wq)})", key="wq_start", use_container_width=True,
                                 help="Whisper auf dem M1 — grob 5 Min pro Podcast-Stunde. Jederzeit stoppbar, endet dann nach der laufenden Folge."):
                        st.session_state["whisper_running"] = True
                        st.rerun()
                with _qc2:
                    if st.button("⏳ Später — beim nächsten Lauf per Apple mitnehmen", key="wq_later", use_container_width=True,
                                 help="Folgen bleiben unangetastet in der Liste. Apple liefert Transkripte meist wenige Stunden nach Erscheinen — dann klappt die 🍎-Runde."):
                        st.session_state["whisper_queue"] = []
                        st.session_state["_podcast_inbox_last_msg"] = f"⏳ Okay — {len(_wq)} Folge(n) bleiben in der Liste, beim nächsten Mal einfach wieder mit in die 🍎-Runde nehmen."
                        st.rerun()
            else:
                # Eine Folge pro Durchlauf — nur so greift der Stopp-Klick zwischen zwei Folgen.
                _we = _wq[0]
                _sc1, _sc2 = st.columns([1, 2])
                with _sc1:
                    if st.button("⏹️ Stopp", key="wq_stop", use_container_width=True,
                                 help="Die laufende Folge wird noch fertig transkribiert und eingefügt, danach pausiert die Warteschlange."):
                        st.session_state["whisper_running"] = False
                        st.rerun()
                with _sc2:
                    st.caption(f"Whisper läuft — noch {len(_wq)} Folge(n) in der Warteschlange. Stopp greift nach der laufenden Folge.")
                _wbox = st.empty()
                _werr = None
                if not _we.get("audio_url"):
                    _werr = f"{_we['title'][:40]}: keine Audio-URL im Feed."
                else:
                    try:
                        from local_transcribe import transcribe_audio_url as _lta9
                    except ImportError:
                        _lta9 = None
                        _werr = "Lokale Transkription nicht installiert (pip3 install --user mlx-whisper)."
                        st.session_state["whisper_running"] = False
                    if _lta9:
                        _wbox.info(f"🎙️ Transkribiere: {_we['title'][:45]}… (M1, ~5 Min pro Podcast-Stunde)")
                        _wt = _lta9(_we["audio_url"], progress_callback=lambda _m, _t=_we: _wbox.info(f"🎙️ {_t['title'][:35]}: {_m}"))
                        if not _wt.get("ok"):
                            _werr = f"{_we['title'][:40]}: {str(_wt.get('error', '?'))[:90]}"
                        else:
                            _wbox.info(f"✨ Fasse zusammen: {_we['title'][:45]}… (~1-3 Min)")
                            _wr = summarize_podcast_transcript_via_cli(f"Podcast: {_we['feed']} — Episode: {_we['title']}\n\n{_wt['text']}")
                            if _wr.get("ok"):
                                st.session_state["podcast_text_pending_value"] = combine_podcast_field(
                                    st.session_state.get("podcast_text"), [_wr["summary"]])
                                _inbox_done(_we["guid"])
                                st.session_state["_wq_ok_count"] = int(st.session_state.get("_wq_ok_count") or 0) + 1
                            else:
                                _werr = f"{_we['title'][:40]}: {str(_wr.get('error', '?'))[:90]}"
                _wbox.empty()
                if _werr:
                    st.session_state["_podcast_inbox_errors"] = (st.session_state.get("_podcast_inbox_errors") or []) + [_werr]
                st.session_state["whisper_queue"] = _wq[1:]
                if not _wq[1:]:
                    st.session_state["whisper_running"] = False
                    _nok = int(st.session_state.get("_wq_ok_count") or 0)
                    if _nok:
                        st.session_state["_podcast_inbox_last_msg"] = f"🎙️✅ Whisper fertig: {_nok} Folge(n) lokal transkribiert, zusammengefasst und unten angefügt."
                    st.session_state["_wq_ok_count"] = 0
                    _maybe_autostart_briefing("Whisper")
                st.rerun()
    elif st.session_state.get("podcast_inbox_data") is not None:
        st.caption("Keine neuen Folgen im Zeitfenster (oder alles schon archiviert). 👍")

    # 🎧 Anhören-Merkliste: was du beim Durchgehen fürs Hören markiert hast —
    # deine Einkaufsliste für Pocket Casts. Unabhängig vom Zeitfenster, bleibt bis ✅.
    _ll = podcast_listen_list()
    if _ll:
        st.markdown("---")
        st.markdown(f"**🎧 Anhören-Merkliste ({len(_ll)})** — beim nächsten Pocket-Casts-Besuch eintragen:")
        for _le in _ll:
            _lc1, _lc2 = st.columns([12, 1])
            with _lc1:
                _ld = (_le.get("published") or "")[:10]
                st.markdown(f"<small>{html.escape(_le.get('feed', '?'))}</small> — **{html.escape(_le.get('title', '?'))}**" + (f" <small>({_ld})</small>" if _ld else ""), unsafe_allow_html=True)
            with _lc2:
                if st.button("✅", key=f"lldone_{_le['guid']}", help="Erledigt — in Pocket Casts eingetragen, von der Merkliste nehmen."):
                    podcast_listen_list_remove([_le["guid"]])
                    st.rerun()
        _render_copy_to_clipboard_button(
            "📋 Merkliste kopieren",
            "\n".join(f"{_le.get('feed', '?')} — {_le.get('title', '?')}" for _le in _ll),
            key="listen_list_copy",
        )

    # 🗂️ Archiv: chronologisch, mit Ein-Klick-Wiederherstellung (versehentliche 🗑️).
    _arch_list, _arch_old = podcast_inbox_archived_list(limit=30)
    if _arch_list or _arch_old:
        with st.expander(f"🗂️ Archiv — zuletzt archivierte Folgen ({len(_arch_list)})", expanded=False):
            for _ae9 in _arch_list:
                _ac1, _ac2 = st.columns([12, 1])
                with _ac1:
                    try:
                        _ad9 = datetime.datetime.fromisoformat(_ae9["archived"]).strftime("%d.%m. %H:%M")
                    except Exception:
                        _ad9 = "?"
                    st.markdown(f"<small>{_ad9} · {html.escape(_ae9.get('feed', '?'))}</small> — **{html.escape(_ae9.get('title', '?'))}**", unsafe_allow_html=True)
                with _ac2:
                    if st.button("↩️", key=f"unarch_{_ae9['guid']}", help="Zurück in die Inbox holen."):
                        podcast_inbox_unarchive([_ae9["guid"]])
                        _dr = st.session_state.get("podcast_inbox_data") or {"episodes": []}
                        if _ae9["guid"] not in [x.get("guid") for x in (_dr.get("episodes") or [])]:
                            _ep_back = {k: v for k, v in _ae9.items() if k != "archived"}
                            _dr.setdefault("episodes", []).insert(0, _ep_back)
                            st.session_state["podcast_inbox_data"] = _dr
                        st.session_state["_podcast_inbox_last_msg"] = f"↩️ Zurückgeholt: {_ae9.get('title', '?')[:60]}"
                        st.rerun()
            if _arch_old:
                st.caption(f"Dazu {_arch_old} ältere Einträge aus der Zeit vor dem Archiv-Umbau — von denen sind nur Fingerabdrücke gespeichert, keine Titel. Sie bleiben einfach dauerhaft ausgeblendet.")

with st.expander("🎙️ Roh-Transkript einwerfen (wird sofort zusammengefasst)", expanded=False):
    st.caption("Transkript aus Pocket Casts hier reinkopieren (auch mehrere, mit mmm getrennt) → Knopf drücken → die fertige Zusammenfassung landet automatisch unten im Podcast-Feld. Der Riesen-Text verschwindet danach — das Feld unten bleibt schlank. Läuft über Opus/Max-Abo, 0 €. Dauer: ~2-4 Min pro Stunde Podcast, zwei laufen parallel.")
    _raw_inbox = st.text_area(
        "Roh-Transkript",
        placeholder="Transkript(e) hier einfügen — mehrere mit mmm trennen. Zeitstempel (SRT/VTT) stören nicht, die werden automatisch entfernt.",
        height=180,
        key="raw_transcript_inbox",
        label_visibility="collapsed",
    )
    if st.button("✨ Zusammenfassen und unten anfügen", key="summarize_raw_transcripts",
                 use_container_width=True, disabled=not (_raw_inbox or "").strip()):
        _raw_blocks = [b.strip() for b in re.split(r"(?i)m{3,}", _raw_inbox) if b.strip()]
        _new_summaries = []
        _inbox_errors = []
        _prog = st.progress(0)
        _stat = st.empty()
        for _bi, _blk in enumerate(_raw_blocks, 1):
            _first = (_blk.splitlines() or ["?"])[0][:60]
            _stat.caption(f"Fasse zusammen {_bi}/{len(_raw_blocks)}: {_first}…")
            _r = summarize_podcast_transcript_via_cli(_blk)
            if _r.get("ok"):
                _new_summaries.append(_r["summary"])
            else:
                _inbox_errors.append(f"{_first}: {_r.get('error', '?')}")
            _prog.progress(_bi / len(_raw_blocks))
        _prog.empty()
        _stat.empty()
        if _new_summaries:
            st.session_state["podcast_text_pending_value"] = combine_podcast_field(
                st.session_state.get("podcast_text"), _new_summaries)
            st.session_state["raw_transcript_inbox_clear"] = True
            st.session_state["_podcast_inbox_last_msg"] = (
                f"✅ {len(_new_summaries)} Podcast-Zusammenfassung(en) erstellt und unten ins Podcast-Feld angefügt."
            )
            _maybe_autostart_briefing("Einwurf")
        if _inbox_errors:
            st.session_state["_podcast_inbox_errors"] = _inbox_errors
        st.rerun()
    # 🍎 Apple-Podcasts-Import: Die Podcasts-App cached jedes einmal GEÖFFNETE
    # Transkript lokal als TTML — von dort holen wir den VOLLEN Text (das manuelle
    # Kopieren in der App schneidet ab). Workflow: Episode in Apple Podcasts öffnen →
    # Transkript anzeigen → hier erscheint sie zum Zusammenfassen.
    st.markdown("---")
    _ttml_list = list_apple_podcast_transcripts(limit=10)
    if _ttml_list:
        st.caption("🍎 Oder aus Apple Podcasts übernehmen (Transkript dort einmal öffnen, dann taucht es hier auf — voller Text, ohne Abschneiden):")
        import datetime as _dt_ttml
        _sel_all_ttml = st.checkbox(f"Alle auswählen ({len(_ttml_list)})", key="apple_ttml_select_all")
        _ttml_selected = []
        for _ti, _t in enumerate(_ttml_list):
            _d = _dt_ttml.datetime.fromtimestamp(_t["mtime"]).strftime("%d.%m. %H:%M")
            if _t.get("title"):
                _name = f"**{_t['title']}**"
            else:
                _name = f"{(_t['preview'] or '(keine Vorschau)')[:70]}…"
            _lbl = f"{_d} · {_t['minutes']} Min · {_t['lang']} · {_name}"
            _tc1, _tc2, _tc3 = st.columns([13, 1, 1])
            with _tc1:
                if st.checkbox(_lbl, key=f"apple_ttml_{_ti}") or _sel_all_ttml:
                    _ttml_selected.append(_t)
            with _tc2:
                if _t.get("title") and st.button("🎧", key=f"apple_ttml_open_{_ti}",
                                                 help="In Apple Podcasts öffnen — kurz reinhören/nachschauen, worum es geht."):
                    _feed9, _sep9, _ep9 = _t["title"].partition(" — ")
                    _ou = resolve_apple_episode_url(_feed9.strip(), _ep9.strip() or _feed9.strip())
                    if _ou and open_in_apple_podcasts(_ou):
                        st.toast(f"🎧 {_t['title'][:60]} in Apple Podcasts geöffnet")
                    else:
                        st.toast("Konnte die Folge nicht automatisch öffnen — in Apple Podcasts suchen.")
            with _tc3:
                if st.button("🗑️", key=f"apple_ttml_del_{_ti}",
                             help="Aus dieser Liste (und der Nachlese) entfernen — z.B. wenn du das Transkript nur versehentlich geöffnet hast. Apples Cache-Datei bleibt unberührt."):
                    mark_ttml_imported([_t["path"]])
                    st.session_state["_podcast_inbox_last_msg"] = f"🗑️ Entfernt: {_t.get('title') or (_t['preview'] or '?')[:50]}"
                    st.rerun()
        if st.button(f"🍎 Ausgewählte zusammenfassen und unten anfügen ({len(_ttml_selected)})", key="summarize_apple_ttml",
                     use_container_width=True, disabled=not _ttml_selected):
            _new_sums = []
            _ttml_errs = []
            _prog2 = st.progress(0)
            _stat2 = st.empty()
            def _process_ttml(_t):
                # Worker-Thread: nur Datei+Subprozess, kein st.*
                _tn = _t.get("title") or (_t.get("preview") or "?")[:50]
                try:
                    _txt = apple_ttml_to_text(_t["path"])
                except Exception as _exc:
                    return (_t, _tn, None, f"TTML unlesbar ({str(_exc)[:80]})")
                if _t.get("title"):
                    _txt = f"Podcast-Episode: {_t['title']}\n\n{_txt}"
                _r = summarize_podcast_transcript_via_cli(_txt)
                if _r.get("ok"):
                    return (_t, _tn, _r["summary"], None)
                return (_t, _tn, None, str(_r.get("error", "?"))[:120])

            from concurrent.futures import ThreadPoolExecutor as _TtmlPool, as_completed as _ttml_done
            _stat2.caption(f"0/{len(_ttml_selected)} fertig — zwei Folgen laufen parallel (~1-3 Min pro Folge)…")
            _tdn = 0
            with _TtmlPool(max_workers=2) as _tpx:
                _tfuts = {_tpx.submit(_process_ttml, _t): _t for _t in _ttml_selected}
                for _tf in _ttml_done(_tfuts):
                    try:
                        _t, _tn, _sumt, _errt = _tf.result()
                    except Exception as _tex:
                        _t, _tn, _sumt, _errt = _tfuts[_tf], "?", None, str(_tex)[:120]
                    _tdn += 1
                    if _sumt:
                        _new_sums.append(_sumt)
                        mark_ttml_imported([_t["path"]])
                    else:
                        _ttml_errs.append(f"{_tn[:40]}: {_errt}")
                    _prog2.progress(_tdn / len(_ttml_selected))
                    _stat2.caption(f"{_tdn}/{len(_ttml_selected)} fertig — zuletzt: {_tn[:50]}")
            _prog2.empty()
            _stat2.empty()
            if _new_sums:
                st.session_state["podcast_text_pending_value"] = combine_podcast_field(
                    st.session_state.get("podcast_text"), _new_sums)
                st.session_state["_podcast_inbox_last_msg"] = (
                    f"✅ {len(_new_sums)} Apple-Podcasts-Transkript(e) zusammengefasst und unten angefügt."
                )
            if _ttml_errs:
                st.session_state["_podcast_inbox_errors"] = _ttml_errs
            st.rerun()
    else:
        st.caption("🍎 Tipp: Auch Apple Podcasts kann als Quelle dienen — Transkript einer Episode dort einmal öffnen, dann erscheint sie hier zum direkten Zusammenfassen (voller Text, ohne das Abschneide-Problem beim Kopieren).")
@st.fragment(run_every=4)
def _round_jobs_collector():
    """Sammelt fertige Hintergrund-Zusammenfassungen (Apple-Runde) ein — läuft alle 4s
    als Fragment, ohne die Seite zu blockieren. Erfolg → unten anfügen + aus Inbox."""
    _jobs = st.session_state.get("_round_jobs") or []
    if not _jobs:
        return
    _left, _got = [], 0
    for _j in _jobs:
        if not _j["fut"].done():
            _left.append(_j)
            continue
        try:
            _r = _j["fut"].result()
        except Exception as _jex2:
            _r = {"ok": False, "error": str(_jex2)[:120]}
        if _r.get("ok"):
            _base = st.session_state.get("podcast_text_pending_value")
            if _base is None:
                _base = st.session_state.get("podcast_text")
            st.session_state["podcast_text_pending_value"] = combine_podcast_field(_base, [_r["summary"]])
            mark_ttml_imported([_j["path"]])
            _d7 = st.session_state.get("podcast_inbox_data") or {}
            _meta7 = [x for x in (_d7.get("episodes") or []) if x.get("guid") == _j["guid"]]
            podcast_inbox_mark([_j["guid"]], "summarized", eps_meta=_meta7)
            podcast_inbox_mark([_j["guid"]], "archived", eps_meta=_meta7)
            _d7["episodes"] = [x for x in (_d7.get("episodes") or []) if x.get("guid") != _j["guid"]]
            st.session_state["podcast_inbox_data"] = _d7
            _got += 1
        else:
            st.session_state["_podcast_inbox_errors"] = (st.session_state.get("_podcast_inbox_errors") or []) + [
                f"{_j['title'][:40]}: {str(_r.get('error', '?'))[:100]}"]
    st.session_state["_round_jobs"] = _left
    if _left:
        st.caption(f"⏳ {len(_left)} Podcast-Zusammenfassung(en) laufen im Hintergrund — erscheinen automatisch unten…")
    if _got or (not _left and _jobs):
        if _got:
            st.session_state["_podcast_inbox_last_msg"] = f"✅ {_got} Hintergrund-Zusammenfassung(en) eingefügt." + (f" Noch {len(_left)} offen." if _left else " Alle fertig.")
        if not _left:
            _maybe_autostart_briefing("Hintergrund-Zusammenfassungen")
        st.rerun(scope="app")


_round_jobs_collector()

if st.session_state.get("_podcast_inbox_last_msg"):
    st.success(st.session_state.pop("_podcast_inbox_last_msg"))
if st.session_state.get("_podcast_inbox_errors"):
    for _e in st.session_state.pop("_podcast_inbox_errors"):
        st.warning(f"⚠️ Nicht zusammengefasst — {_e}")
st.checkbox(
    "🚀 Briefing automatisch starten, sobald ALLE Podcast-Zusammenfassungen fertig sind",
    key="auto_briefing_when_done",
    help="Für den typischen Schluss-Schritt: Du stößt Runde/✨/Einwurf an, gehst weg — und sobald die letzte Zusammenfassung eingefügt ist (und keine Whisper-Frage offen), startet das Briefing von selbst mit deinen aktuellen Einstellungen. Gilt, solange das Häkchen an ist.",
)
podcast_text = st.text_area(
    "Podcast-Zusammenfassungen",
    placeholder='Zusammenfassungen hier einfügen.\nTrennung am Endmarker "Ende der Podcastzusammenfassung." oder mit mmm, Mmmmmm, ---, ===== oder "Artikel Ende".',
    height=220,
    help="Fertige Podcast-Zusammenfassungen. Bereits sauber formatierte Blöcke werden unverändert übernommen. Auch ROHE Transkripte (ohne Endmarker) kannst du hier lassen — alles ohne Endmarker und länger als ~4000 Zeichen wird beim Erstellen automatisch zusammengefasst. Komfortabler: das Einwurf-Feld oben.",
    key="podcast_text",
    label_visibility="collapsed",
)
_podcast_blocks = split_podcast_summaries(podcast_text) if podcast_text.strip() else []
st.markdown(
    f"<div class='briefing-url-meta'><span class='briefing-url-count'>Aktuell erkannt: <strong>{len(_podcast_blocks)}</strong> {'Blöcke' if len(_podcast_blocks) != 1 else 'Block'}</span></div>",
    unsafe_allow_html=True,
)
_pc_desc_col, _pc_scroll_col = st.columns([6, 1])
with _pc_desc_col:
    with st.expander("ℹ️ Hinweise zur Podcast-Eingabe", expanded=False):
        st.caption("Mehrere Podcasts werden am Endmarker `Ende der Podcastzusammenfassung.` oder alternativ mit drei oder mehr `m` (`mmm`/`Mmmmmm`) getrennt. Sauber formatierte Blöcke bleiben unverändert; andere werden nur formatiert. Doppelte Blöcke landen nur einmal im Briefing.")
with _pc_scroll_col:
    if st.button("↓ Ende", key="scroll_podcasts", use_container_width=True):
        _scroll_textarea("Podcast-Zusammenfassungen")
_n_sp0 = len(split_special_topics(st.session_state.get("special_topics_text") or ""))
with st.expander(f"🧠 Sonderthemen — eigene Fragen ins Briefing{f' ({_n_sp0})' if _n_sp0 else ''}", expanded=False):
    st.caption("Ein Thema pro Zeile — oder wie gewohnt mit mmm getrennt, dann darf ein Thema auch mehrere Zeilen/Sätze haben. Stichworte oder ganze Fragen. Opus recherchiert dazu gezielt im Netz (seriöse Quellen) und webt je einen angemessen langen Beitrag als eigenen Block ins Briefing. Zuschreibungs-Fragen (Hat X wirklich gesagt …?) werden ehrlich geprüft. Die App hat ein Gedächtnis für die letzten Tage: Schon Behandeltes wird erkannt und eingeordnet (wie neulich berichtet …) — Wichtiges oder Komplexes darf aber bewusst nochmal erklärt werden, nichts wird stur weggelassen. Nach erfolgreichem Lauf leert sich die Box (Fehlgeschlagenes bleibt drin).")
    st.text_area(
        "Sonderthemen",
        key="special_topics_text",
        height=100,
        label_visibility="collapsed",
        placeholder="Kim Elzemer — was hat er seit Amtsantritt konkret bewegt?\n\nmmm\n\nHat Sokrates wirklich gesagt, man solle sich nur auf Änderbares\nkonzentrieren? Im Podcast hieß es, das sei eigentlich stoisch.",
    )
    _wl_c1, _wl_c2 = st.columns([1, 2])
    with _wl_c1:
        if st.button("🌍 Weltlage-Check", key="missing_topics_btn",
                     help="Opus recherchiert die aktuell wichtigsten Themen (Welt, Deutschland, Region, deine Interessensfelder) und vergleicht sie mit deinen Briefings der letzten Tage. Vorschläge übernimmst du per ➕ direkt als Sonderthema. ~1-2 Min, 0 € übers Abo."):
            with st.spinner("🌍 Vergleiche die Nachrichtenlage mit deinen letzten Briefings UND dem heute Eingesammelten (~1-2 Min)…"):
                # Heutige Eingaben zählen als abgedeckt: Links (Slugs sind sprechend),
                # erste Zeilen der Paywall-/Podcast-Blöcke, vorhandene Sonderthemen.
                _staged = [l.strip() for l in (st.session_state.get("urls_text") or "").splitlines()
                           if l.strip().startswith("http")]
                for _fld in ("paywall_text", "podcast_text"):
                    _blocks9 = re.split(r"(?im)^\s*(?:m{3,}|-{3,}|={3,}|artikel ende)\s*$",
                                        st.session_state.get(_fld) or "")
                    for _b9 in _blocks9:
                        _first9 = next((x.strip() for x in _b9.splitlines() if x.strip()), "")
                        if len(_first9) > 15:
                            _staged.append(_first9.strip("*# "))
                _staged += split_special_topics(st.session_state.get("special_topics_text") or "")
                st.session_state["_missing_topics_result"] = suggest_missing_topics_via_cli(staged_lines=_staged)
    with _wl_c2:
        st.caption("Was ist gerade wichtig, kam aber in deinen Briefings noch nicht vor? Ein Klick, und du bekommst Vorschläge für Sonderthemen.")
    _mt = st.session_state.get("_missing_topics_result")
    if _mt is not None:
        if not _mt.get("ok"):
            st.warning(f"🌍 Weltlage-Check fehlgeschlagen: {str(_mt.get('error'))[:150]}")
        elif not _mt.get("topics"):
            st.success("🌍 Nichts Wesentliches verpasst — deine Briefings decken die aktuelle Lage ab.")
        else:
            for _mi, _mtop in enumerate(_mt["topics"]):
                _mc1, _mc2 = st.columns([12, 1])
                with _mc1:
                    st.markdown(f"**{html.escape(_mtop['topic'])}**  \n<small style='opacity:.7'>{html.escape(_mtop.get('why', ''))}</small>", unsafe_allow_html=True)
                with _mc2:
                    if st.button("➕", key=f"mt_add_{_mi}", help="Als Sonderthema übernehmen."):
                        st.session_state["special_topics_text_pending_value"] = append_special_topic(
                            st.session_state.get("special_topics_text"), _mtop["topic"])
                        _mt["topics"] = [t for _j, t in enumerate(_mt["topics"]) if _j != _mi]
                        st.session_state["_missing_topics_result"] = _mt
                        st.rerun()

_render_mobile_input_buffer()

export_pdf = True
export_epub = False
export_txt = False
_save_draft()

_PAYWALL_ITEM_PREFIX = "__paywall_block_"

# Typische Navigations-Marker am Anfang von kopierten Paywall-Texten (GEA, SWP, Tagblatt etc.)
_PAYWALL_NAV_INDICATORS = (
    # Generelle Account/Menü-Marker
    "e-paper", "alle themen", "menü schließen", "mein konto", "abmelden",
    "meine swp", "meine gea", "merkliste", "newsletter", "podcasts", "push",
    "meine nachrichten", "abo-shop", "anzeigen", "vorteilswelt",
    "mein profil", "startseite", "dem gea", "informiert bleiben",
    "navigation", "hauptmenü", "untermenü", "hilfe", "rechtliches",
    "agbs", "impressum", "datenschutz", "kontakt", "karriere",
    "abonnement kündigen", "nutzungsbedingungen", "los geht's",
    "sport ulm", "sport kreis", "ratiopharm ulm", "ssv ulm",
    "kaufberater", "los geht's tübingen", "kultur tübingen",
    # SWP "Services & Portale"-Block (häufig nach dem Haupt-Menü)
    "services & portale", "bluum", "jobs & arbeitgeber",
    "trauer - todesanzeigen", "todesanzeigen", "immobilien - immobilienangebote",
    "auto - kfz-angebote", "kfz-angebote", "kleinanzeigen",
    "werben mit swp", "werben mit", "mediadaten", "online-anzeigenaufgabe",
    "sonderthemen", "swp shop", "unternehmen der region",
    "erscheinungsbild", "mehr von swp", "aboshop", "trauerportal",
    "wochenblätter lesen", "wochenblätter", "ihre meinung",
    # Theme-Switcher
    "automatisch\n", "hell\n", "dunkel\n",
)


def _paywall_excerpt_for_cluster(block: str, target_chars: int = 600) -> str:
    """Extrahiert einen sinnvollen Excerpt aus einem Paywall-Block für die
    Cluster-Detection. Überspringt den Navigations-Vorspann (E-Paper-Menü,
    Themen-Listen, Service-Portale wie "bluum/Trauer/Immobilien/Auto" etc.),
    sonst matchen alle Blöcke gegen alle wegen identischer Navigation.

    Strategie (in dieser Reihenfolge probieren):
    1. SWP-Pattern `[Ressort]\\n:\\n[Title]` — Doppelpunkt allein → Article-Start
    2. GEA-Pattern `[ALLCAPS-Ressort]\\n[Title]` — single-word ALLCAPS-Kategorie
    3. Fallback: Skip-Heuristik (Navi-Marker, ALLCAPS-Klumpen, kurze Service-Items)
    """
    if not block:
        return ""
    raw_lines = block.splitlines()
    stripped_lines = [ln.strip() for ln in raw_lines]

    # --- Strategie 1: SWP-Pattern `\n:\n` ---
    for i, ln in enumerate(stripped_lines):
        if ln == ":" and i + 1 < len(stripped_lines):
            # Vor und nach `:` müssen plausibel sein
            prev = stripped_lines[i - 1] if i > 0 else ""
            nxt = stripped_lines[i + 1]
            if prev and len(prev) < 80 and nxt and len(nxt) > 15:
                # Title ist die Zeile nach `:`, Inhalt folgt
                rest_lines = stripped_lines[i + 1:]
                rest = "\n".join(l for l in rest_lines if l).strip()
                if len(rest) >= 80:
                    return rest[:target_chars]

    # --- Strategie 2: GEA-Pattern ALLCAPS-Kategorie + Title in nächster Zeile ---
    _GEA_KATEGORIE_SET = {
        "ENTWICKLUNG", "KUNST", "LEUTE", "POLIZEIMELDUNG", "MEDIZIN", "SPORT",
        "WIRTSCHAFT", "POLITIK", "KULTUR", "GESELLSCHAFT", "REGION", "VERKEHR",
        "PARTEIEN", "JUSTIZ", "BILDUNG", "GESUNDHEIT", "UMWELT", "WAHLEN",
        "TIERE", "WETTER", "EVENT", "VERANSTALTUNG", "INTERVIEW", "PORTRÄT",
        "KOMMENTAR", "ANALYSE", "REPORT", "REPORTAGE", "VORSORGE", "NETZAUSBAU",
        "IMMOBILIEN", "CARSHARING", "EINSCHULUNG", "WAHL", "PARTEI",
    }
    for i, ln in enumerate(stripped_lines):
        if ln in _GEA_KATEGORIE_SET and i + 1 < len(stripped_lines):
            nxt = stripped_lines[i + 1]
            if nxt and len(nxt) > 15 and not nxt.isupper():
                rest_lines = stripped_lines[i + 1:]
                rest = "\n".join(l for l in rest_lines if l).strip()
                if len(rest) >= 80:
                    return rest[:target_chars]

    # --- Strategie 3: Fallback-Skip-Heuristik ---
    start_idx = 0
    for i, ln in enumerate(stripped_lines):
        if not ln:
            continue
        low = ln.lower()
        # Skip sehr kurze Zeilen ohne Satzstruktur (Menü-Items)
        if len(ln) < 30 and ln.count(" ") < 3:
            continue
        # Skip Zeilen mit eindeutigen Navi-Markern
        if any(ind in low for ind in _PAYWALL_NAV_INDICATORS):
            continue
        # Skip Service-Listing-Pattern "Begriff - Beschreibung" mit Bindestrich-Trenner
        if " - " in ln and len(ln) < 60 and ln.count(",") == 0 and ln.count(".") == 0:
            continue
        # Skip Mega-Klumpen mit hoher Großbuchstaben-Quote
        if len(ln) > 30:
            letters = [c for c in ln if c.isalpha()]
            if letters:
                caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
                if caps_ratio > 0.5:
                    continue
            if ln.count(" ") < 3:
                continue
        start_idx = i
        break

    rest = "\n".join(l for l in stripped_lines[start_idx:] if l).strip()
    if not rest:
        rest = block.strip()
    return rest[:target_chars]
def _run_briefing_generation(selected_urls=None, prefetched_payloads=None):
    _run_t0 = datetime.datetime.now()
    compact_mode = bool(st.session_state.get("compact_mode", True))
    _want_narrative = st.session_state.get("narrative_additional_main", False)
    # "Keine" = keine separate Kompaktfassung erzeugen.
    _want_genius = st.session_state.get("genius_depth_radio_main", "Keine") != "Keine"
    progress_bar = st.progress(0)
    status_text = st.empty()
    percent_text = st.empty()
    last_progress = {"value": 0.0}

    def on_progress(step: str, progress: float):
        progress_value = max(last_progress["value"], max(0.0, min(progress, 1.0)))
        last_progress["value"] = progress_value
        percent = int(round(progress_value * 100))
        progress_bar.progress(progress_value)
        percent_text.markdown(f"**{percent}%**")
        status_text.markdown(
            f"<div class='status-text'>⏳ {step}</div>",
            unsafe_allow_html=True,
        )

    with st.spinner(""):
        exports, check, sections = generate_briefing(
            api_key=api_key,
            urls_text=urls_text,
            paywall_text=paywall_text,
            podcast_text=podcast_text,
            model=model,
            include_weather=include_weather,
            requested_exports={
                "pdf": True,
                "epub": False,
                "eleven_txt": True,
            },
            content_check_enabled=True,
            content_check_mode=st.session_state.get("content_check_mode", "warn"),
            selected_article_urls=selected_urls,
            prefetched_article_payloads=prefetched_payloads,
            progress_callback=on_progress,
            compact_mode=compact_mode,
            narrative_mode=False,  # Haupt-Briefing immer klassisch
        )

    progress_bar.empty()
    percent_text.empty()
    _clear_topic_review_state()
    if exports:
        check = check or {}
        check["compact_mode"] = compact_mode
        check["narrative_mode"] = False
        check["_run_duration_s"] = (datetime.datetime.now() - _run_t0).total_seconds()
        # Im neuen Naming-Schema ist „kompakt" kein Dateinamen-Suffix mehr.
        compact_suffix = ""
        check["archive"] = _save_exports_to_archive(exports, check, suffix=compact_suffix)
        st.session_state.briefing_exports = exports
        st.session_state.briefing_check = check
        st.session_state.briefing_sections = sections
        st.session_state.genius_summary_text = None
        st.session_state.genius_summary_pdf = None
        st.session_state.genius_summary_meta = None
        _persist_last_briefing(exports, check, sections)
        _apply_cost_to_balances(check)

        # === Auto-Repair (wenn aktiviert UND Plausi-Check Warnungen findet) ===
        if (
            api_key
        ):
            _content_check = (check or {}).get("content_check") or {}
            _output_lint = (check or {}).get("output_lint") or {}
            # Plausi-Check liefert `warnings` (harte Issues) + `notices` (Hinweise) + `failed` (Check selbst gescheitert).
            # Auto-Repair triggert nur bei harten Warnungen oder Failed-Checks, nicht bei reinen Hinweisen.
            _plausi_warn = int(_content_check.get("warnings", 0))
            _plausi_failed = int(_content_check.get("failed", 0))
            _lint_warn = int(_output_lint.get("warnings", 0))
            _warn_count = _plausi_warn + _plausi_failed + _lint_warn
            if _warn_count > 0:
                _ar_progress = st.progress(0)
                _ar_status = st.empty()

                def _ar_cb(step, frac):
                    _ar_progress.progress(max(0.0, min(frac, 1.0)))
                    _ar_status.markdown(f"<div class='status-text'>🔧 Auto-Repair: {step}</div>", unsafe_allow_html=True)

                try:
                    repaired_exports, repaired_check, repaired_sections = repair_briefing_content_issues(
                        api_key=api_key,
                        sections=sections,
                        existing_check=check,
                        model=model,
                        requested_exports=check.get("requested_exports"),
                        progress_callback=_ar_cb,
                    )
                except Exception as exc:
                    _ar_progress.empty()
                    _ar_status.warning(f"⚠️ Auto-Repair fehlgeschlagen: {exc}. Original-Briefing bleibt erhalten.")
                    repaired_exports = None

                _ar_progress.empty()
                _ar_status.empty()
                if repaired_exports:
                    repaired_check = repaired_check or {}
                    repaired_check["archive"] = _save_exports_to_archive(
                        repaired_exports,
                        repaired_check,
                        suffix=_repair_archive_suffix(repaired_check),
                    )
                    _removed_originals = _cleanup_uncorrected_archive_versions(repaired_check)
                    if _removed_originals:
                        repaired_check["archive"]["removed_originals"] = _removed_originals
                    st.session_state.briefing_exports = repaired_exports
                    st.session_state.briefing_check = repaired_check
                    st.session_state.briefing_sections = repaired_sections
                    _persist_last_briefing(repaired_exports, repaired_check, repaired_sections)
                    _apply_cost_to_balances(repaired_check)
                    exports = repaired_exports
                    check = repaired_check
                    sections = repaired_sections
                    repair_note = _format_repair_summary_text(repaired_check)
                    st.success(repair_note or f"🔧 Auto-Repair: {_warn_count} Warnung(en) automatisch behoben.")

        # === Erzähl-Version erstellen (wenn gewünscht) ===
        if _want_narrative and sections:
            _narr_progress = st.progress(0)
            _narr_status = st.empty()
            def _narr_cb(step, frac):
                _narr_progress.progress(max(0.0, min(frac, 1.0)))
                _narr_status.caption(f"🎙️ Erzähl-Version: {step}")

            _narr_status.caption("🎙️ Erzähl-Version wird zusätzlich erstellt...")
            import copy as _copy
            _narr_sections = _copy.deepcopy(sections)
            _narr_client = None
            try:
                from briefing_core import _build_client, convert_to_narrative_briefing, create_pdf, _sanitize_briefing_output, _normalize_existing_briefing_markdown, tts_safe, _strip_premature_briefing_end_markers
                _narr_client = _build_client(api_key, model)
                _narr_depth = "detailed" if st.session_state.get("narrative_depth_radio") == "Ausführlich" else "standard"
                _narr_result = convert_to_narrative_briefing(_narr_client, _narr_sections, model, progress_callback=_narr_cb, depth=_narr_depth, api_keys=api_keys)

                # TTS + Bereinigung
                for s in _narr_result:
                    cleaned = _sanitize_briefing_output(_normalize_existing_briefing_markdown(s["content"]))
                    s["content"] = tts_safe(cleaned)
                _strip_premature_briefing_end_markers(_narr_result)

                # PDF erzeugen
                _narr_stamp = check.get("created_at_file") or datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                _narr_stamp_short = _narr_stamp[:16] if len(_narr_stamp) == 19 and _narr_stamp[16] == "-" else _narr_stamp
                _narr_suffix = "_erzaehl"
                _narr_filename = f"{_narr_stamp_short}_tagesbriefing{_narr_suffix}.pdf"
                _narr_archive_dir = _resolve_archive_dir(for_write=True)
                _narr_path = _narr_archive_dir / _narr_filename
                _generated_at = datetime.datetime.now()
                try:
                    _gen_str = check.get("created_at_file", "")
                    _generated_at = datetime.datetime.strptime(_gen_str, "%Y-%m-%d_%H-%M-%S")
                except Exception:
                    pass

                create_pdf(_narr_result, str(_narr_path), _generated_at, document_title="Audio-Briefing (Erzählmodus)")

                # TXT-Version im Texte/-Ordner speichern (für Wochen-Meta-Briefing)
                try:
                    from briefing_core import create_eleven_reader_text
                    _narr_txt_content = create_eleven_reader_text(_narr_result, _generated_at)
                    _narr_txt_filename = f"{_narr_stamp_short}_tagesbriefing{_narr_suffix}_eleven-reader.txt"
                    _narr_txt_dir = _narr_archive_dir / _TXT_ARCHIVE_SUBDIR
                    _narr_txt_dir.mkdir(parents=True, exist_ok=True)
                    (_narr_txt_dir / _narr_txt_filename).write_text(_narr_txt_content, encoding="utf-8")
                    _mirror_txt_to_local(_narr_txt_filename, _narr_txt_content)
                except Exception:
                    pass  # TXT ist nice-to-have, nicht kritisch

                _narr_progress.empty()
                _narr_status.empty()
                st.success(f"🎙️ Erzähl-Version zusätzlich erstellt: `{_narr_filename}`")
                with open(_narr_path, "rb") as f:
                    st.download_button(
                        "🎙️ Erzähl-PDF herunterladen",
                        data=f.read(),
                        file_name=_narr_filename,
                        mime="application/pdf",
                        use_container_width=True,
                        on_click="ignore",
                        key=f"narrative_additional_download_{_narr_stamp_short}",
                    )
            except Exception as _narr_exc:
                _narr_progress.empty()
                _narr_status.empty()
                st.warning(f"⚠️ Erzähl-Version konnte nicht erstellt werden: {_narr_exc}")

        # === Geniale Zusammenfassung erstellen (wenn gewünscht) ===
        if _want_genius and sections:
            _genius_depth_label = st.session_state.get("genius_depth_radio_main", "Keine")
            if _genius_depth_label == "Beide":
                _genius_modes = ["long", "short"]
            elif _genius_depth_label == "Kurz":
                _genius_modes = ["short"]
            else:
                _genius_modes = ["long"]
            for _genius_mode in _genius_modes:
                _genius_progress = st.progress(0)
                _genius_status = st.empty()

                def _genius_cb(step, frac):
                    _genius_progress.progress(max(0.0, min(frac, 1.0)))
                    _genius_status.caption(f"✨ Kompaktfassung: {step}")

                _genius_status.caption("✨ Kompaktfassung wird zusätzlich erstellt...")
                try:
                    _generated_at = None
                    try:
                        _gen_str = check.get("created_at_file", "")
                        _generated_at = datetime.datetime.strptime(_gen_str, "%Y-%m-%d_%H-%M-%S")
                    except Exception:
                        _generated_at = None

                    _genius_text, _genius_pdf, _genius_cost, _genius_meta = generate_genius_summary(
                        api_key,
                        sections,
                        model,
                        mode=_genius_mode,
                        progress_callback=_genius_cb,
                        generated_at=_generated_at,
                        api_keys=api_keys,
                    )
                    if _genius_text:
                        st.session_state.genius_summary_text = _genius_text
                        st.session_state.genius_summary_pdf = _genius_pdf
                        st.session_state.genius_summary_meta = _genius_meta
                        _genius_file_stamp = check.get("created_at_file") or datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                        _genius_archive_info = {"dir": str(_resolve_archive_dir()), "path": None, "error": None}
                        if _genius_pdf:
                            _genius_archive_info = _save_genius_summary_pdf_to_archive(
                                _genius_pdf,
                                _genius_file_stamp,
                                mode=_genius_mode,
                            )
                        check["genius_summary_archive"] = _genius_archive_info
                        check["genius_summary_meta"] = _genius_meta
                        check["cost"] = merge_cost_dicts(check.get("cost"), _genius_cost)
                        st.session_state.briefing_check = check
                        _persist_last_briefing(
                            st.session_state.get("briefing_exports"),
                            check,
                            st.session_state.get("briefing_sections"),
                        )
                        _apply_cost_to_balances({"cost_delta": _genius_cost})

                        _genius_progress.empty()
                        _genius_status.empty()
                        _genius_filename = _genius_summary_file_name(_genius_file_stamp, mode=_genius_mode)
                        st.success(f"✨ Kompaktfassung zusätzlich erstellt: `{_genius_filename}`")
                        if _genius_pdf:
                            st.download_button(
                                "✨ Kompaktfassung (PDF) herunterladen",
                                data=_genius_pdf,
                                file_name=_genius_filename,
                                mime="application/pdf",
                                use_container_width=True,
                                on_click="ignore",
                                key=f"genius_additional_download_{_genius_file_stamp}_{_genius_mode}",
                            )
                    else:
                        _genius_progress.empty()
                        _genius_status.empty()
                        st.warning("⚠️ Kompaktfassung konnte nicht erstellt werden.")
                except Exception as _genius_exc:
                    _genius_progress.empty()
                    _genius_status.empty()
                    st.warning(f"⚠️ Kompaktfassung konnte nicht erstellt werden: {_genius_exc}")

        # Gesamtdauer (inkl. Auto-Repair + Kompaktfassung) festhalten — überlebt den Rerun
        try:
            _total_s = (datetime.datetime.now() - _run_t0).total_seconds()
            if isinstance(st.session_state.get("briefing_check"), dict):
                st.session_state.briefing_check["_run_duration_s"] = _total_s
        except Exception:
            pass

        # Rerun, damit ein eventuell noch sichtbarer Review-Dialog aus dem UI verschwindet
        # (Der Dialog wurde schon gerendert BEVOR der Click-Handler hier ausgeführt wurde.)
        st.rerun()

    if st.session_state.briefing_exports is None:
        st.session_state.briefing_check = check
    _apply_cost_to_balances(check)
    if check and check.get("paywall", {}).get("failed"):
        status_text.error("Paywall-Rohtexte konnten nicht sauber zusammengefasst werden.")
        st.markdown("**Fehlgeschlagene Paywall-Rohtexte:**")
        for item in check["paywall"]["failed"]:
            st.markdown(f"- `{item}`")
    elif check and check.get("articles", {}).get("failed"):
        status_text.error("Artikel-URLs konnten nicht sauber verarbeitet werden.")
        st.markdown("**Fehlgeschlagene Artikel:**")
        for url in check["articles"]["failed"]:
            st.markdown(f"- `{url[:100]}`")
    else:
        status_text.error("Keine Inhalte zum Verarbeiten. Prüfe deine Eingaben.")

# ============================================================
def render_exports(exports, check):
    """Zeigt Download-Buttons und Status fuer die letzte Generierung an."""
    check_data = check or {}
    content_check = check_data.get("content_check", {}) or {}
    output_lint = check_data.get("output_lint", {}) or {}
    warning_count = int((content_check.get("warnings", 0) or 0) + (output_lint.get("warnings", 0) or 0))
    notice_count = int((content_check.get("notices", 0) or 0) + (output_lint.get("notices", 0) or 0))
    publishable = bool(check_data.get("publishable", check_data.get("complete", True) and warning_count == 0))
    quality_status = (check_data.get("quality_status") or "").strip()
    requested = check_data.get("requested_exports", {"pdf": True, "epub": False, "eleven_txt": True})
    archive_info = check_data.get("archive", {}) or {}
    archive_saved = archive_info.get("saved", []) or []

    if warning_count or not publishable:
        banner_class = " danger"
        banner_title = "Qualitätswarnungen offen"
        banner_copy = "Das Briefing wurde erzeugt, sollte aber vor dem Weiterverwenden noch geprüft oder repariert werden."
    elif notice_count:
        banner_class = " warn"
        banner_title = "Briefing mit Hinweisen bereit"
        banner_copy = "Die Hauptausgabe ist verwendbar. Kleinere Hinweise sind im Prüfstand dokumentiert."
    else:
        banner_class = ""
        banner_title = "Briefing bereit"
        banner_copy = "PDF, Reader-Text, Archiv und Lang-Kompaktfassung stehen direkt bereit."

    quality_label = quality_status or ("sauber" if publishable else "prüfen")
    contribution_count = check_data.get("total")
    contribution_label = f"{contribution_count} Beiträge" if contribution_count is not None else "Beiträge"
    _run_dur = check_data.get("_run_duration_s")
    if _run_dur:
        _m, _s = divmod(int(_run_dur), 60)
        _dur_label = f"{_m} Min {_s} Sek" if _m else f"{_s} Sek"
        st.caption(f"⏱️ Dauer dieses Briefings (API): {_dur_label}")
    export_count = len([key for key, enabled in requested.items() if enabled and exports.get(key)])
    export_label = f"{export_count} Dateien" if export_count else "Downloads"
    archive_label = f"{len(archive_saved)} gespeichert" if archive_saved else "Archiv bereit"

    st.markdown(
        f"""
        <div class="briefing-status-banner{banner_class}">
          <h3>{html.escape(banner_title)}</h3>
          <p>{html.escape(banner_copy)}</p>
          <div class="briefing-status-row">
            <div class="briefing-status-cell"><strong>{html.escape(quality_label)}</strong><span>Qualität</span></div>
            <div class="briefing-status-cell"><strong>{html.escape(contribution_label)}</strong><span>Inhalt</span></div>
            <div class="briefing-status-cell"><strong>{html.escape(export_label)} · {html.escape(archive_label)}</strong><span>Ausgabe</span></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    repair_note = _format_repair_summary_text(check_data)
    if repair_note:
        removed_originals = ((check_data.get("archive") or {}).get("removed_originals") or [])
        removed_text = " Die unkorrigierte Archivversion wurde entfernt." if removed_originals else ""
        st.markdown(
            f"""
            <div class="briefing-status-banner warn">
              <h3>Automatisch korrigiert</h3>
              <p>{html.escape(repair_note + removed_text)}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    cost = check_data.get("cost", {})
    if cost:
        total_usd = float(cost.get("total_usd", 0.0) or 0.0)
        st.markdown(
            f"""
            <div class="briefing-info-card">
              <strong>Geschätzte API-Kosten dieses Briefings: ${total_usd:.2f}</strong><br>
              <span style="color:var(--b-muted);">
                {f"{cost.get('calls', 0)} Modellaufrufe · {cost.get('billable_input_tokens', 0):,} Input-Tokens · {cost.get('cached_input_tokens', 0):,} Cache-Tokens · {cost.get('output_tokens', 0):,} Output-Tokens".replace(",", ".")}
              </span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if cost.get("models") or cost.get("notes"):
            with st.expander("Kosten-Details"):
                for item in cost.get("models", []):
                    st.markdown(
                        f"- `{item['model']}`: ${item.get('usd', 0.0):.2f} "
                        f"bei {item.get('calls', 0)} Calls "
                        f"({item.get('billable_input_tokens', 0):,} Input · "
                        f"{item.get('cached_input_tokens', 0):,} Cache · "
                        f"{item.get('output_tokens', 0):,} Output)".replace(",", ".")
                    )
                for note in cost.get("notes", []):
                    st.caption(f"Hinweis: {note}")
                st.caption("Schätzung auf Basis der hinterlegten Standard-Preise pro Modellfamilie. Exportformate selbst kosten praktisch nichts extra.")
        remaining_lines = []
        if st.session_state.get("openai_estimated_remaining_usd") is not None:
            remaining_lines.append(f"OpenAI-Rest geschätzt: USD {st.session_state['openai_estimated_remaining_usd']:.2f}")
        if st.session_state.get("anthropic_estimated_remaining_usd") is not None:
            remaining_lines.append(f"Anthropic-Rest geschätzt: USD {st.session_state['anthropic_estimated_remaining_usd']:.2f}")
        if remaining_lines:
            st.caption(" · ".join(remaining_lines))

    file_stamp = check_data.get("created_at_file")
    if not file_stamp:
        file_stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    # Im neuen Naming-Schema ist „kompakt" kein Dateinamen-Suffix mehr — der Voll-Name
    # heißt einheitlich „tagesbriefing_voll" unabhängig vom compact_mode.
    compact_suffix = ""
    export_specs = []
    if requested.get("pdf"):
        export_specs.append(("pdf", "PDF herunterladen", "Vollbriefing", "Druckfertige Hauptausgabe für Lesen, Teilen und Archiv.", _export_file_name("pdf", file_stamp, compact_suffix), "application/pdf"))
    if requested.get("epub"):
        export_specs.append(("epub", "EPUB herunterladen", "E-Reader", "Optionales Lesegerät-Format, falls es für diesen Lauf erzeugt wurde.", _export_file_name("epub", file_stamp, compact_suffix), "application/epub+zip"))
    if requested.get("eleven_txt"):
        export_specs.append(("eleven_txt", "Reader-TXT herunterladen", "Audio-Text", "Bereinigter Text für Eleven Reader und Wochen-Meta-Briefing.", _export_file_name("eleven_txt", file_stamp, compact_suffix), "text/plain; charset=utf-8"))

    if not export_specs:
        return
    st.markdown(
        """
        <div class="briefing-section-head">
          <div class="eyebrow">Exporte</div>
          <h3>Downloads und Archiv</h3>
          <p>Die wichtigsten Formate stehen direkt bereit. Archivierte Versionen bleiben darunter erreichbar.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    export_cols = st.columns(len(export_specs))
    for col, (key, label, title, description, file_name, mime) in zip(export_cols, export_specs):
        with col:
            state_text = "Bereit" if exports.get(key) else "Nicht erzeugt"
            st.markdown(
                f"""
                <div class="briefing-download-card">
                  <strong>{html.escape(title)}</strong>
                  <span>{html.escape(description)}<br>{html.escape(state_text)}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if exports.get(key):
                st.download_button(
                    label=label,
                    data=exports[key],
                    file_name=file_name,
                    mime=mime,
                    use_container_width=True,
                    on_click="ignore",
                )
            else:
                st.button(
                    label,
                    disabled=True,
                    use_container_width=True,
                )

    st.caption("Exporte bleiben nach Reloads erhalten und verschwinden erst bei `Neues Briefing`.")

    # ============================================================
    # ROHTEXT FÜR CLAUDE (Erzähl-Modus über Max-Abo, kostet 0$)
    # ============================================================
    sections_for_export = st.session_state.get("briefing_sections") or []
    if sections_for_export:
        with st.expander("🎙️ Rohtext für Claude (Podcast-Stil) — nur bei Bedarf"):
            st.caption("Lädt das komplette Briefing als Rohtext mit Anweisungen herunter. In Claude einfügen, Claude verwebt alles zu einem zusammenhängenden Erzähltext — homogen, mit Übergängen, ohne dass etwas verloren geht.")
            narrative_payload = _build_narrative_export_text(sections_for_export, file_stamp)
            narrative_col1, narrative_col2 = st.columns([1, 1])
            with narrative_col1:
                st.download_button(
                    label="📋 Rohtext + Prompt herunterladen",
                    data=narrative_payload.encode("utf-8"),
                    file_name=f"{file_stamp[:16] if len(file_stamp) == 19 else file_stamp}_tagesbriefing{compact_suffix}_fuer-claude.txt",
                    mime="text/plain; charset=utf-8",
                    use_container_width=True,
                    on_click="ignore",
                    key=f"narrative_download_{file_stamp}",
                )
            with narrative_col2:
                with st.popover("📄 Direkt anzeigen (kopieren)", use_container_width=True):
                    st.code(narrative_payload, language="markdown")
            st.caption("Der Text enthält oben einen System-Prompt für Claude und darunter alle Briefing-Inhalte. Kostet nichts (läuft über dein Claude-Abo).")
    archive_info = (check or {}).get("archive", {})
    archive_saved = archive_info.get("saved", [])
    archive_errors = archive_info.get("errors", [])
    if archive_saved:
        archive_dir_text = archive_info.get('dir', str(_resolve_archive_dir()))
        st.caption(f"Gespeichert in `{archive_dir_text}`.")
        if archive_dir_text != str(_PRIMARY_ARCHIVE_DIR):
            st.caption("Lokales Fallback-Archiv (nicht iCloud).")
    elif archive_errors:
        st.caption(f"Automatisches Speichern in `{archive_info.get('dir', str(_resolve_archive_dir()))}` fehlgeschlagen.")

    if check and requested.get("pdf") and check.get("pdf_ok") is False:
        st.warning("PDF konnte nicht sauber erzeugt werden.")
    if check and requested.get("epub") and check.get("epub_ok") is False:
        st.warning("EPUB konnte nicht sauber erzeugt werden.")
    if check and requested.get("eleven_txt") and check.get("txt_ok") is False:
        st.warning("Der Eleven-Reader-Text konnte nicht sauber erzeugt werden.")
    if archive_errors:
        for error in archive_errors:
            st.warning(f"Archivspeicherung: {error}")

    archived_files = _list_archived_briefings()
    if archived_files:
        st.markdown(
            """
            <div class="briefing-section-head">
              <div class="eyebrow">Archiv</div>
              <h3>Gespeicherte Briefings</h3>
              <p>Die letzten Dateien aus dem Briefings-Ordner bleiben direkt in der App abrufbar.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.expander("Archiv im Briefings-Ordner"):
            st.caption(f"Ordner: `{_resolve_archive_dir()}`")
            for path in archived_files:
                label_col, action_col = st.columns([3.2, 1.4])
                with label_col:
                    modified = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
                    st.markdown(f"**{path.name}**")
                    st.caption(f"Gespeichert: {modified}")
                with action_col:
                    st.download_button(
                        label="Öffnen/Download",
                        data=path.read_bytes(),
                        file_name=path.name,
                        mime=_ARCHIVE_MIME_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                        key=f"archive_{path.name}_{int(path.stat().st_mtime)}",
                        use_container_width=True,
                        on_click="ignore",
                    )

    sections = st.session_state.get("briefing_sections") or []
    # Kompaktfassung: entweder aus aktuellem Briefing ODER aus hochgeladener PDF
    _genius_sections = sections  # Default: aktuelles Briefing
    _genius_file_stamp = file_stamp
    _genius_source = "aktuelles Briefing"

    st.markdown("---")
    st.markdown(
        """
        <div class="briefing-section-head">
          <div class="eyebrow">Zusatzformat</div>
          <h3>Kompaktfassung</h3>
          <p>Die lange Kompaktfassung des ganzen Briefings. Detaillierter als der Recap, aber deutlich kürzer als das Vollbriefing.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Optional: PDF hochladen statt aktuelles Briefing zu nutzen
    _genius_pdf_upload = st.file_uploader(
        "Optional: Briefing-PDF hochladen",
        type=["pdf"],
        key="genius_pdf_upload",
        help="Wenn du eine fertige Briefing-PDF hochlädst, wird die Kompaktfassung daraus statt aus dem aktuellen Briefing erzeugt. Leer lassen = aktuelles Briefing verwenden.",
    )
    if _genius_pdf_upload is not None:
        try:
            import pdfplumber, tempfile as _tf
            _tmp = Path(_tf.gettempdir()) / f"genius_upload_{_genius_pdf_upload.name}"
            _tmp.write_bytes(_genius_pdf_upload.getbuffer())
            with pdfplumber.open(str(_tmp)) as _pdf:
                _extracted_text = "\n".join((p.extract_text() or "") for p in _pdf.pages)
            _tmp.unlink(missing_ok=True)
            _genius_sections = _parse_briefing_text_to_sections(_extracted_text)
            if _genius_sections:
                _genius_source = _genius_pdf_upload.name
                # Timestamp aus dem PDF-Namen extrahieren falls möglich
                import re as _re
                _ts_match = _re.search(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})", _genius_pdf_upload.name)
                if _ts_match:
                    _genius_file_stamp = _ts_match.group(1)
                st.success(f"✅ PDF geladen: {len(_genius_sections)} Beiträge aus `{_genius_pdf_upload.name}` erkannt.")
            else:
                st.warning("Das PDF konnte geladen werden, aber keine Briefing-Beiträge darin erkannt. Versuche es mit dem aktuellen Briefing.")
                _genius_sections = sections
        except Exception as _exc:
            st.error(f"Fehler beim Laden des PDFs: {_exc}")
            _genius_sections = sections

    if _genius_sections:
        genius_mode_label = "Lang"
        genius_mode = "long"
        st.session_state["genius_summary_mode_radio"] = "Lang"
        st.session_state["genius_summary_mode"] = genius_mode
        st.caption("Version: Lang")

        button_label = "Kompaktfassung erzeugen"
        if st.session_state.get("genius_summary_text"):
            button_label = "Kompaktfassung neu erzeugen"

        genius_progress_mount = st.empty()
        genius_percent_mount = st.empty()
        genius_status_mount = st.empty()

        if st.button(
            button_label,
            key="genius_summary_button",
            use_container_width=True,
            disabled=not api_key,
        ):
            progress_bar = genius_progress_mount.progress(0)
            last_progress = {"value": 0.0}

            def on_genius_progress(step: str, progress: float):
                progress_value = max(last_progress["value"], max(0.0, min(progress, 1.0)))
                last_progress["value"] = progress_value
                percent = int(round(progress_value * 100))
                progress_bar.progress(progress_value)
                genius_percent_mount.markdown(f"**{percent}%**")
                genius_status_mount.markdown(
                    f"<div class='status-text'>⏳ {step}</div>",
                    unsafe_allow_html=True,
                )

            generated_at = None
            if check and check.get("created_at_file"):
                try:
                    generated_at = datetime.datetime.strptime(check["created_at_file"], "%Y-%m-%d_%H-%M-%S")
                except Exception:
                    generated_at = None

            genius_summary_text, genius_summary_pdf, genius_summary_cost, genius_summary_meta = generate_genius_summary(
                api_key,
                _genius_sections,
                model,
                mode=genius_mode,
                progress_callback=on_genius_progress,
                generated_at=generated_at,
                api_keys=api_keys,
            )
            if genius_summary_text:
                st.session_state.genius_summary_text = genius_summary_text
                st.session_state.genius_summary_pdf = genius_summary_pdf
                st.session_state.genius_summary_meta = genius_summary_meta
                if check is not None:
                    genius_archive_info = {"dir": str(_resolve_archive_dir()), "path": None, "error": None}
                    if genius_summary_pdf:
                        genius_archive_info = _save_genius_summary_pdf_to_archive(
                            genius_summary_pdf,
                            _genius_file_stamp,
                            mode=genius_mode,
                        )
                    check["genius_summary_archive"] = genius_archive_info
                    check["genius_summary_meta"] = genius_summary_meta
                    check["cost"] = merge_cost_dicts(check.get("cost"), genius_summary_cost)
                    st.session_state.briefing_check = check
                    _persist_last_briefing(
                        st.session_state.get("briefing_exports"),
                        check,
                        st.session_state.get("briefing_sections"),
                    )
                _apply_cost_to_balances({"cost_delta": genius_summary_cost})
                st.rerun()
            else:
                genius_progress_mount.empty()
                genius_percent_mount.empty()
                genius_status_mount.empty()
                st.warning("Die Kompaktfassung konnte nicht erzeugt werden.")

        if st.session_state.get("genius_summary_text"):
            genius_pdf = st.session_state.get("genius_summary_pdf")
            genius_meta = st.session_state.get("genius_summary_meta") or (check or {}).get("genius_summary_meta", {})
            if genius_pdf:
                st.download_button(
                    label="📥 Kompaktfassung (PDF)",
                    data=genius_pdf,
                    file_name=_genius_summary_file_name(_genius_file_stamp, genius_meta.get("mode", genius_mode)),
                    mime="application/pdf",
                    use_container_width=True,
                    on_click="ignore",
                )
            else:
                st.warning("Die Kompaktfassung ist da, aber die PDF-Erzeugung ist fehlgeschlagen.")
            genius_archive = (check or {}).get("genius_summary_archive", {})
            if genius_archive.get("path"):
                st.caption(f"Automatisch gespeichert in `{genius_archive['path']}`.")
            elif genius_archive.get("error"):
                st.caption(f"Automatisches Speichern der Kompaktfassung fehlgeschlagen: {genius_archive['error']}")
            if genius_meta:
                complete_label = "✅ Vollständig" if genius_meta.get("complete") else "⚠️ Unvollständig"
                parts = []
                if genius_meta.get("weather"):
                    parts.append(f"{genius_meta['weather']} Wetter")
                if genius_meta.get("articles"):
                    parts.append(f"{genius_meta['articles']} Artikel")
                if genius_meta.get("paywall"):
                    parts.append(f"{genius_meta['paywall']} Paywall")
                if genius_meta.get("podcasts"):
                    parts.append(f"{genius_meta['podcasts']} Podcasts")
                if genius_meta.get("total") is not None:
                    mode_caption = genius_meta.get("mode_label")
                    if mode_caption:
                        st.caption(f"{complete_label} · {genius_meta['total']} Beiträge · {mode_caption}")
                    else:
                        st.caption(f"{complete_label} · {genius_meta['total']} Beiträge")
                if parts:
                    st.caption("Enthalten: " + " · ".join(parts))
                if genius_meta.get("used_fallback"):
                    st.caption("Absicherung aktiv: Die Kompaktfassung wurde auf die vollständige Sicherheitsfassung zurückgesetzt.")
                # Qualitäts-Check-Meldung
                qc = genius_meta.get("quality_check") or {}
                if qc:
                    auto_repairs = qc.get("auto_repairs") or []
                    issues = qc.get("issues") or []
                    llm_issues = qc.get("llm_issues") or []
                    llm_repair_applied = qc.get("llm_repair_applied")
                    parts_qc = []
                    if not issues and not llm_issues and not auto_repairs:
                        parts_qc.append("Qualitäts-Check: sauber")
                    else:
                        warn_count = len([i for i in issues if i.get("severity") == "warning"])
                        info_count = len([i for i in issues if i.get("severity") == "info"])
                        if warn_count:
                            parts_qc.append(f"{warn_count} Struktur-Warnungen")
                        if info_count:
                            parts_qc.append(f"{info_count} Hinweise")
                        if auto_repairs:
                            parts_qc.append(f"{len(auto_repairs)} Auto-Repairs")
                        if llm_issues:
                            parts_qc.append(f"LLM-Check: {len(llm_issues)} inhaltliche Punkte")
                        if llm_repair_applied:
                            parts_qc.append("LLM-Auto-Repair angewendet")
                    if parts_qc:
                        st.caption("✨ " + " · ".join(parts_qc))
                    # Details ausklappbar
                    if issues or llm_issues or auto_repairs:
                        with st.expander("Qualitäts-Check Details"):
                            if auto_repairs:
                                st.markdown("**Automatisch korrigiert:**")
                                for r in auto_repairs: st.markdown(f"- {r}")
                            if issues:
                                st.markdown("**Struktur-Check:**")
                                for i in issues: st.markdown(f"- {i.get('severity','?')}: {i.get('message','')}")
                            if llm_issues:
                                st.markdown("**LLM-Qualitäts-Check:**")
                                for i in llm_issues:
                                    st.markdown(f"- [{i.get('beitrag','?')}] {i.get('category','?')}: {i.get('message','')}")

    if check:
        st.markdown("---")
        st.markdown(
            """
            <div class="briefing-section-head">
              <div class="eyebrow">Prüfstand</div>
              <h3>Vollständigkeit und Qualitätslage</h3>
              <p>Hier siehst du auf einen Blick, was erfolgreich verarbeitet wurde und wo noch etwas fehlt oder nachgeschärft werden kann.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        content_check = check.get("content_check", {}) or {}
        output_lint = check.get("output_lint", {}) or {}
        combined_warnings = content_check.get("warnings", 0) + output_lint.get("warnings", 0)
        combined_notices = content_check.get("notices", 0) + output_lint.get("notices", 0)
        publishable = bool(check.get("publishable", check.get("complete", False) and combined_warnings == 0))
        quality_status = (check.get("quality_status") or "").strip()
        if combined_warnings or not publishable:
            st.markdown(
                f"""
                <div class="briefing-status-banner danger">
                  <h3>Vor dem Verwenden prüfen</h3>
                  <p>{html.escape(quality_status or 'Es gibt noch wichtige Warnungen im Qualitäts-Check.')}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        elif combined_notices:
            st.markdown(
                f"""
                <div class="briefing-status-banner warn">
                  <h3>Verwendbar mit Hinweisen</h3>
                  <p>{html.escape(quality_status or 'Die Ausgabe ist grundsätzlich sauber, enthält aber kleinere Hinweise.')}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                """
                <div class="briefing-status-banner">
                  <h3>Qualitätslage sauber</h3>
                  <p>Vollständigkeit, Plausibilitäts-Check und lokaler Output-Lint melden keine offenen Warnungen.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

        def _safe_count(group: str, field: str) -> int:
            try:
                return int((check.get(group, {}) or {}).get(field, 0) or 0)
            except Exception:
                return 0

        expected_total = 0
        got_total = 0
        for group in ("weather", "articles", "paywall", "podcasts"):
            expected_total += _safe_count(group, "expected")
            got_total += _safe_count(group, "got")

        article_failed = len((check.get("articles", {}) or {}).get("failed", []) or [])
        paywall_failed = len((check.get("paywall", {}) or {}).get("failed", []) or [])
        rejected_count = len((check.get("articles", {}) or {}).get("rejected", []) or [])
        failure_count = article_failed + paywall_failed
        duplicates = check.get("duplicates", {}) or {}
        duplicate_total = int(duplicates.get("total", 0) or 0)
        archive_saved = len(((check.get("archive", {}) or {}).get("saved", []) or []))
        requested_exports = check.get("requested_exports", {}) or {}
        export_states = []
        if requested_exports.get("pdf"):
            export_states.append("PDF OK" if check.get("pdf_ok") is not False else "PDF Fehler")
        if requested_exports.get("eleven_txt"):
            export_states.append("TXT OK" if check.get("txt_ok") is not False else "TXT Fehler")
        if requested_exports.get("epub"):
            export_states.append("EPUB OK" if check.get("epub_ok") is not False else "EPUB Fehler")
        export_label = " · ".join(export_states) if export_states else "Keine Exporte"
        mode_label = "Kürzer" if check.get("compact_mode", True) else "Ausführlich"

        detail_cards = [
            ("Modus", mode_label, "Klassisches Briefing", "ok"),
            (
                "Verarbeitet",
                f"{got_total}/{expected_total}" if expected_total else str(got_total),
                "Erwartete Inhalte im Lauf",
                "ok" if expected_total == 0 or got_total >= expected_total else "danger",
            ),
            (
                "Offene Fehler",
                "Keine" if failure_count == 0 else str(failure_count),
                "Artikel/Paywall nicht verarbeitet",
                "ok" if failure_count == 0 else "danger",
            ),
            (
                "Qualität",
                f"{combined_warnings} Warnungen · {combined_notices} Hinweise",
                "Plausibilitäts-Check + Output-Lint",
                "danger" if combined_warnings else ("warn" if combined_notices else "ok"),
            ),
            (
                "Duplikate",
                str(duplicate_total),
                "Automatisch übersprungen",
                "warn" if duplicate_total else "ok",
            ),
            (
                "Ausgabe",
                f"{export_label} · {archive_saved} archiviert",
                "Downloads und Archiv",
                "ok" if archive_saved and "Fehler" not in export_label else "warn",
            ),
        ]
        detail_html = "".join(
            f"<div class='briefing-detail-card {state}'>"
            f"<strong>{html.escape(value)}</strong>"
            f"<span>{html.escape(title)} · {html.escape(caption)}</span>"
            f"</div>"
            for title, value, caption, state in detail_cards
        )
        st.markdown(f"<div class='briefing-detail-grid'>{detail_html}</div>", unsafe_allow_html=True)

        issue_lines = []
        if article_failed:
            issue_lines.append(f"{article_failed} Artikel fehlgeschlagen")
        if paywall_failed:
            issue_lines.append(f"{paywall_failed} Paywall-Block nicht verarbeitet")
        if rejected_count:
            issue_lines.append(f"{rejected_count} Nicht-Artikel-Link übersprungen")
        if duplicate_total:
            issue_lines.append(f"{duplicate_total} Duplikate automatisch entfernt")
        if issue_lines:
            st.caption("Detailstatus: " + " · ".join(issue_lines))
        
        if check["complete"]:
            if check.get("sorting", {}).get("expected") and not check["sorting"]["ok"]:
                st.markdown("#### ✅ Vollständig (unsortiert)")
            else:
                st.markdown("#### ✅ Vollständig")
        else:
            st.markdown("#### ⚠️ Unvollständig")

        items = []
        
        if check["weather"]["expected"]:
            ok = check["weather"]["got"] > 0
            items.append(("Wetter", "1/1" if ok else "0/1", ok))
        
        if check["articles"]["expected"]:
            n = check["articles"]["got"]
            total = check["articles"]["expected"]
            items.append(("Artikel", f"{n}/{total}", n == total))
        
        if check["paywall"]["expected"]:
            n = check["paywall"]["got"]
            total = check["paywall"]["expected"]
            items.append(("Paywall", f"{n}/{total}", n == total))
        
        if check["podcasts"]["expected"]:
            n = check["podcasts"]["got"]
            total = check["podcasts"]["expected"]
            items.append(("Podcasts", f"{n}/{total}", n == total))

        if check.get("sorting", {}).get("expected"):
            items.append((
                "Sortierung",
                "OK" if check["sorting"]["ok"] else "Warnung",
                bool(check["sorting"]["ok"])
            ))
        
        if check.get("recap_expected", True):
            items.append(("Recap", "1/1" if check["recap"] else "0/1", check["recap"]))

        metrics_html = "".join(
            f"<div class='briefing-metric-card'>"
            f"<div class='briefing-metric-state'>{'✅' if ok else '❌'}</div>"
            f"<div class='briefing-metric-value'>{html.escape(str(count))}</div>"
            f"<div class='briefing-metric-label'>{html.escape(label)}</div>"
            f"</div>"
            for label, count, ok in items
        )
        st.markdown(f"<div class='briefing-metrics-grid'>{metrics_html}</div>", unsafe_allow_html=True)
        
        if check["articles"].get("failed"):
            st.markdown("")
            st.markdown("**Fehlgeschlagene Artikel:**")
            _paywall_domains = {"nytimes.com", "wsj.com", "ft.com", "economist.com", "washingtonpost.com", "theathletic.com", "bloomberg.com"}
            for url in check["articles"]["failed"]:
                _is_paywall = any(d in url.lower() for d in _paywall_domains)
                if _is_paywall:
                    st.markdown(f"- `{url[:100]}` — ⚠️ **Paywall-Seite**: Text manuell ins Paywall-Feld kopieren")
                else:
                    st.markdown(f"- `{url[:100]}`")

        if check["articles"].get("rejected"):
            st.markdown("")
            st.markdown("**Übersprungene Nicht-Artikel-Links:**")
            for item in check["articles"]["rejected"]:
                if isinstance(item, dict):
                    st.markdown(f"- `{item.get('url', '')[:120]}` — {item.get('reason', 'kein Artikel')}")
                else:
                    st.markdown(f"- `{str(item)[:120]}`")

        if check["paywall"].get("failed"):
            st.markdown("")
            st.markdown("**Fehlgeschlagene Paywall-Rohtexte:**")
            for item in check["paywall"]["failed"]:
                st.markdown(f"- `{item}`")
        
        if check.get("sorting", {}).get("expected") and not check["sorting"]["ok"]:
            st.caption("⚠️ Thematische Sortierung fehlgeschlagen — Artikel sind unsortiert.")
            if st.session_state.get("briefing_sections"):
                if st.button(
                    "Sortierung erneut versuchen",
                    key="rerun_sorting_button",
                    use_container_width=True,
                    disabled=not api_key,
                ):
                    resort_progress = st.progress(0)
                    resort_status = st.empty()
                    resort_percent = st.empty()
                    last_progress = {"value": 0.0}

                    def on_resort_progress(step: str, progress: float):
                        progress_value = max(last_progress["value"], max(0.0, min(progress, 1.0)))
                        last_progress["value"] = progress_value
                        percent = int(round(progress_value * 100))
                        resort_progress.progress(progress_value)
                        resort_percent.markdown(f"**{percent}%**")
                        resort_status.markdown(
                            f"<div class='status-text'>⏳ {step}</div>",
                            unsafe_allow_html=True,
                        )

                    with st.spinner(""):
                        resort_exports, resort_check, resort_sections = rerun_briefing_sorting(
                            api_key=api_key,
                            sections=st.session_state.get("briefing_sections") or [],
                            existing_check=check,
                            model=model,
                            requested_exports=check.get("requested_exports"),
                            progress_callback=on_resort_progress,
                        )

                    resort_progress.empty()
                    resort_percent.empty()
                    if resort_exports:
                        resort_check = resort_check or {}
                        resort_check["archive"] = _save_exports_to_archive(resort_exports, resort_check)
                        st.session_state.briefing_exports = resort_exports
                        st.session_state.briefing_check = resort_check
                        st.session_state.briefing_sections = resort_sections
                        st.session_state.genius_summary_text = None
                        st.session_state.genius_summary_pdf = None
                        st.session_state.genius_summary_meta = None
                        _persist_last_briefing(resort_exports, resort_check, resort_sections)
                        _apply_cost_to_balances(resort_check)
                        st.success("Sortierung und Exporte wurden neu aufgebaut.")
                        st.rerun()
                    else:
                        resort_status.error("Die neue Sortierung hat nicht geklappt.")
            else:
                st.caption("Für einen Re-Sort fehlt der interne Zustand dieses Briefings. Ein neuer Lauf speichert das künftig mit.")

        if duplicates.get("total"):
            parts = []
            if duplicates.get("urls"):
                parts.append(f"{duplicates['urls']} URL")
            if duplicates.get("article_content"):
                parts.append(f"{duplicates['article_content']} Artikelinhalt")
            if duplicates.get("paywall"):
                parts.append(f"{duplicates['paywall']} Paywall")
            if duplicates.get("podcasts"):
                parts.append(f"{duplicates['podcasts']} Podcast")
            st.caption(f"ℹ️ Automatisch übersprungen: {duplicates['total']} Duplikate ({', '.join(parts)}).")

        detail_items = []
        for item in content_check.get("items", []):
            item_copy = dict(item)
            item_copy["_origin_label"] = "Plausibilitäts-Check"
            detail_items.append(item_copy)
        for item in output_lint.get("items", []):
            item_copy = dict(item)
            item_copy["_origin_label"] = "Output-Lint"
            detail_items.append(item_copy)
        _severity_order = {"warn": 0, "notice": 1, "ok": 2}
        detail_items.sort(key=lambda item: (_severity_order.get(item.get("level", "ok"), 2), item.get("section_index", 10**9), item.get("label", "")))

        if content_check.get("enabled") or output_lint.get("enabled"):
            st.markdown("")
            main_model_name = check.get("main_model") or "Briefing-Modell"
            if combined_warnings == 0 and combined_notices == 0:
                st.markdown("**🟢 Inhaltlich keine klaren Auffälligkeiten**")
            elif combined_warnings == 0:
                st.markdown(f"**🔵 Kleinere Hinweise: {combined_notices}**")
            else:
                st.markdown(f"**🟡 Wichtige Warnsignale: {combined_warnings}**")
            st.caption(f"Briefing erzeugt mit {main_model_name}.")
            if content_check.get("enabled"):
                model_name = content_check.get("model_display") or content_check.get("model", "Prüfmodell")
                st.caption(
                    f"Geprüft mit {model_name}: {content_check.get('ok', 0)} unauffällig, "
                    f"{content_check.get('warnings', 0)} wichtige Warnungen, "
                    f"{content_check.get('notices', 0)} kleinere Hinweise, "
                    f"{content_check.get('failed', 0)} fehlgeschlagen."
                )
                if content_check.get("summary_note"):
                    st.caption(content_check["summary_note"])
            if output_lint.get("enabled"):
                st.caption(
                    f"Lokaler Output-Lint: {output_lint.get('ok', 0)} unauffällig, "
                    f"{output_lint.get('warnings', 0)} wichtige Warnungen, "
                    f"{output_lint.get('notices', 0)} kleinere Hinweise, "
                    f"{output_lint.get('failed', 0)} Prüfungen fehlgeschlagen."
                )
                if output_lint.get("summary_note"):
                    st.caption(output_lint["summary_note"])
            if content_check.get("auto_repaired"):
                st.caption(f"Auto-Repair: {content_check['auto_repaired']} Warnung(en) wurden automatisch behoben.")
            repair_summary_text = _format_repair_summary_text(check)
            if repair_summary_text:
                st.caption(repair_summary_text)
            content_check_report_text = _build_content_check_report_text(check)
            if content_check_report_text:
                _render_copy_to_clipboard_button(
                    "Plausi-Text kopieren",
                    content_check_report_text,
                    key=f"content_check_copy_{file_stamp}",
                )
            if (
                combined_warnings > 0
                and st.session_state.get("briefing_sections")
                and api_key
            ):
                if st.button(
                    "Verbleibende Mängel erneut beheben",
                    key="repair_content_issues_button",
                    use_container_width=True,
                ):
                    repair_progress = st.progress(0)
                    repair_status = st.empty()
                    repair_percent = st.empty()
                    last_progress = {"value": 0.0}

                    def on_repair_progress(step: str, progress: float):
                        progress_value = max(last_progress["value"], max(0.0, min(progress, 1.0)))
                        last_progress["value"] = progress_value
                        percent = int(round(progress_value * 100))
                        repair_progress.progress(progress_value)
                        repair_percent.markdown(f"**{percent}%**")
                        repair_status.markdown(
                            f"<div class='status-text'>⏳ {step}</div>",
                            unsafe_allow_html=True,
                        )

                    with st.spinner(""):
                        repaired_exports, repaired_check, repaired_sections = repair_briefing_content_issues(
                            api_key=api_key,
                            sections=st.session_state.get("briefing_sections") or [],
                            existing_check=check,
                            model=model,
                            requested_exports=check.get("requested_exports"),
                            progress_callback=on_repair_progress,
                        )

                    repair_progress.empty()
                    repair_percent.empty()
                    if repaired_exports:
                        repaired_check = repaired_check or {}
                        repaired_check["archive"] = _save_exports_to_archive(
                            repaired_exports,
                            repaired_check,
                            suffix=_repair_archive_suffix(repaired_check),
                        )
                        _removed_originals = _cleanup_uncorrected_archive_versions(repaired_check)
                        if _removed_originals:
                            repaired_check["archive"]["removed_originals"] = _removed_originals
                        st.session_state.briefing_exports = repaired_exports
                        st.session_state.briefing_check = repaired_check
                        st.session_state.briefing_sections = repaired_sections
                        st.session_state.genius_summary_text = None
                        st.session_state.genius_summary_pdf = None
                        st.session_state.genius_summary_meta = None
                        _persist_last_briefing(repaired_exports, repaired_check, repaired_sections)
                        _apply_cost_to_balances(repaired_check)
                        repair_note = _format_repair_summary_text(repaired_check)
                        st.success(repair_note or "Beanstandete Abschnitte wurden neu gebaut.")
                        st.rerun()
                    else:
                        repair_status.error("Für diesen Lauf gab es keine reparierbaren Warnungen.")
            if detail_items:
                with st.expander("Plausibilitäts-Details"):
                    _has_auto_repairs = bool(content_check.get("auto_repaired") or output_lint.get("warnings", 0) > 0)
                    if _has_auto_repairs:
                        st.caption("ℹ️ Gefundene Warnungen wurden automatisch behoben. Das fertige PDF/EPUB enthält bereits die korrigierten Fassungen.")
                    for item in detail_items:
                        level = item.get("level", "ok")
                        if level == "warn":
                            st.markdown(f"🔴 **{item['label']}**")
                        elif level == "notice":
                            st.markdown(f"🟡 **{item['label']}**")
                        else:
                            st.markdown(f"🟢 **{item['label']}**")
                        st.markdown(item.get("summary", ""))
                        if item.get("faithfulness") is not None or item.get("coverage") is not None:
                            scores = []
                            if item.get("faithfulness") is not None:
                                scores.append(f"Faithfulness {item['faithfulness']}/5")
                            if item.get("coverage") is not None:
                                scores.append(f"Coverage {item['coverage']}/5")
                            if scores:
                                st.caption(" | ".join(scores))
                        for issue in item.get("hard_issues", []):
                            st.markdown(f"- Warnung: {issue}")
                        for issue in item.get("soft_issues", []):
                            st.markdown(f"- Hinweis: {issue}")
                        if not item.get("hard_issues") and not item.get("soft_issues"):
                            for issue in item.get("issues", []):
                                st.markdown(f"- {issue}")
                        for support in item.get("source_support", []):
                            st.markdown(f"- Quelltext-Stelle: {support}")
                        if item.get("strengths"):
                            for strength in item["strengths"]:
                                st.markdown(f"- Plus: {strength}")

        st.caption(f"Gesamt: {check['total']} Beiträge")

# Vorab-Check + Eingabe-Übersicht (Standard = kostenloser Claude-Weg; API = eingeklappte Alternative weiter unten)
# ============================================================

st.markdown("---")

# === Plausibilitäts-Check + Auto-Repair ===
content_check_enabled = True
api_auto_repair_enabled = True
st.session_state["content_check_enabled"] = True
st.session_state["api_auto_repair_enabled"] = True
st.caption("Qualitätssicherung ist fest aktiv: Plausibilitäts-Check, lokaler Output-Lint und Auto-Repair laufen bei jedem API-Briefing.")

# Wetter ist immer eingeschlossen — früh definieren, da Übersicht + Claude-Block
# es vor dem (nach unten verschobenen) API-Konfigblock nutzen.
include_weather = True
st.session_state["include_weather"] = True

# === Übersicht: Was wird ins Briefing gehen? ===
_ovr_parts = []
if include_weather:
    _ovr_parts.append("Wetter")
_urls_n = len(url_preview["urls"])
if _urls_n > 0:
    _ovr_parts.append(f"{_urls_n} URL{'s' if _urls_n != 1 else ''}")
_pw_n = len(_paywall_blocks) if _paywall_blocks else 0
if _pw_n > 0:
    _ovr_parts.append(f"{_pw_n} Paywall-Artikel" if _pw_n != 1 else "1 Paywall-Artikel")
_pc_n = len(_podcast_blocks) if _podcast_blocks else 0
if _pc_n > 0:
    _ovr_parts.append(f"{_pc_n} Podcast{'s' if _pc_n != 1 else ''}")
if _ovr_parts:
    _ovr_pills = "".join(f"<span class='briefing-pill'>{html.escape(p)}</span>" for p in _ovr_parts)
    st.markdown(f"<div class='briefing-pill-row' style='margin-top:0.6em;margin-bottom:0.3em;'>{_ovr_pills}</div>", unsafe_allow_html=True)
else:
    st.caption("⚠️ Noch keine Eingabe — füge URLs/Paywall/Podcasts ein oder aktiviere Wetter.")

st.markdown("---")

# === Output-Varianten ===
_mode_col1, _mode_col2, _mode_col3 = st.columns(3)
with _mode_col1:
    # Mehrfachauswahl: eine oder mehrere Längen anklicken — bei mehreren werden die
    # Versionen nacheinander erstellt, die Artikel aber nur EINMAL geladen+gemergt.
    _valid_depths = ("Intelligent", "Sehr kurz", "Kürzer", "Ausführlich")
    if "briefing_depth_multi" not in st.session_state:
        # Migration: alter Radio-Wert (falls vorhanden) wird zur Vorauswahl.
        _old_depth = st.session_state.get("briefing_depth_radio")
        st.session_state["briefing_depth_multi"] = [_old_depth] if _old_depth in _valid_depths else ["Kürzer"]
    st.session_state["briefing_depth_multi"] = [
        d for d in st.session_state["briefing_depth_multi"] if d in _valid_depths
    ] or ["Kürzer"]
    _briefing_depth_sel = st.multiselect(
        "Briefing-Länge(n)",
        options=list(_valid_depths),
        key="briefing_depth_multi",
        help="Alle sind ein VOLLES Briefing in voller Qualität. 🧠 Intelligent: Opus gewichtet jedes Thema automatisch (Tragweite 1-5, du musst NICHTS bewerten) — Top-Storys werden voll erzählt, Randnotizen auf 2-3 Sätze eingedampft; Gesamtlänge bleibt im Rahmen, Substanz geht vor. Sehr kurz: ~120 Wörter/Beitrag überall gleich. Kürzer: knackig, gleichmäßig. Ausführlich: mehr Kontext überall. MEHRERE anklicken = alle Versionen in einem Rutsch. Intelligent wirkt in beiden Modi — verwoben (Synthese) und klassisch Artikel für Artikel.",
    )
    _depths_to_run = [d for d in _valid_depths if d in (_briefing_depth_sel or [])] or ["Kürzer"]
    st.session_state["_depths_to_run"] = _depths_to_run
    _briefing_depth = _depths_to_run[0]
    compact_mode = _briefing_depth != "Ausführlich"
    ultra_compact = _briefing_depth == "Sehr kurz"
    st.session_state["compact_mode"] = compact_mode
    st.session_state["ultra_compact"] = ultra_compact
    if len(_depths_to_run) > 1:
        st.caption(f"🔁 {len(_depths_to_run)} Versionen werden nacheinander erstellt — gemeinsame Artikel-Basis, nur die Verdichtung läuft pro Länge.")
    elif _briefing_depth == "Intelligent":
        st.caption("🧠 Opus verteilt die Länge selbst: Schwerpunkte voll, Randnotizen in 2-3 Sätzen — vollständig bleibt es immer. Funktioniert verwoben UND klassisch.")
    elif ultra_compact:
        st.caption("Sehr kurz: höchstens ~120 Wörter pro Beitrag — volles Briefing, aber für umfangreiche Tage (viele Artikel) deutlich kürzer.")
    elif compact_mode:
        st.caption("Kürzere Artikel — volles Briefing in voller Qualität, nur knackiger. Für die meisten Tagesläufe.")
    else:
        st.caption("Ausführlicher: mehr Kontext pro Beitrag, dafür länger.")
    st.checkbox(
        "📱 WhatsApp-Lese-PDF zusätzlich", key="whatsapp_pdf_additional",
        help="Erstellt bei jedem Lauf zusätzlich eine kompakte LESE-Version für deinen WhatsApp-Broadcast: klassisches Format (ein Beitrag pro Quelle, wie deine Leser es kennen) in der Stufe Sehr kurz. Artikel werden nur EINMAL geladen — es kommt nur ein zweiter Verdichtungs-Durchlauf dazu (0 € übers Abo). Datei endet auf _whatsapp.pdf. Geht NICHT automatisch an ElevenReader.",
    )
with _mode_col2:
    topic_synthesis_mode = st.checkbox(
        "🧵 Themen-Synthese", value=True, key="topic_synthesis_mode",
        help="Persönliches Briefing statt Einzelbeiträge: Opus bündelt ALLE Quellen (Links, Paywall-Texte, Podcasts) thematisch — z.B. drei Artikel + ein Podcast zum Koalitionsausschuss werden EIN verwobener Vorlesetext. Nichts doppelt, nichts fehlt (jede Quelle wird garantiert genau einem Thema zugeordnet). Ersetzt die frühere Erzähl-Version. Nur im kostenlosen Claude-Weg.",
    )
    narrative_additional = False  # Erzähl-Version durch Themen-Synthese ersetzt (Code bleibt schlafend erhalten)
    if topic_synthesis_mode:
        st.caption("🧵 Alle Quellen werden thematisch zu je EINEM Beitrag verwoben — effizient informiert, nichts doppelt.")
        st.checkbox(
            "🎙️ Unterhaltsam erzählt (Magazin-Stil)", value=True, key="synthesis_narrative_style",
            help="Die Themen-Beiträge werden wie von einem guten Magazin-Podcast-Host erzählt: Hook, roter Faden, anschauliche Vergleiche — aber strikt faktentreu, nur mit deinen Quellen, und ernste Themen bleiben ernst. Ohne Häkchen: sachlich-klarer Nachrichtenstil.",
        )
        st.checkbox(
            "🌐 Fehlendes intelligent ergänzen (Websuche)", value=True, key="synthesis_web_enrich",
            help="Fehlt deinen Quellen ein zentraler Baustein (Wer ist die Person? Vorgeschichte? Schlüsselzahl?), darf Opus GEZIELT im Netz nachschlagen — max. 1-2 Suchen pro Thema, nur seriöse Quellen (Agenturen, Öffentlich-Rechtliche, Primärquellen). Jede Ergänzung wird im Text klar gekennzeichnet (Zur Einordnung, laut Reuters: …). Nur Lückenfüllung, nie neue Themen; bei Widerspruch gewinnen DEINE Quellen. Macht den Lauf etwas langsamer.",
        )
with _mode_col3:
    # Altlasten normalisieren: früher gab es "Standard" — auf gültige Option mappen,
    # sonst crasht st.radio (gespeicherter Wert nicht in options).
    if st.session_state.get("genius_depth_radio_main") not in ("Lang", "Kurz", "Beide", "Keine"):
        st.session_state["genius_depth_radio_main"] = "Keine"
    _kompakt_laenge = st.radio(
        "✨ Kompaktfassung",
        options=["Lang", "Kurz", "Beide", "Keine"],
        horizontal=True,
        key="genius_depth_radio_main",
        help="Separate kuratierte Verdichtung. Lang: nah am Voll-Briefing. Kurz: stark verdichtet. Beide: beide Versionen. Keine: gar keine Kompaktfassung erzeugen — spart Zeit + Opus-Kontingent, sinnvoll wenn dir das kürzere Voll-Briefing reicht (hat ja jetzt Top-3).",
    )
    genius_additional = _kompakt_laenge != "Keine"
    st.session_state["genius_additional_main"] = genius_additional
    if not genius_additional:
        st.caption("Keine separate Kompaktfassung — nur das Voll-Briefing wird erstellt.")
    st.checkbox(
        "🔍 Qualitäts-Check + Auto-Korrektur", key="quality_check_enabled",
        help="Nach dem Erstellen prüft Claude jeden Beitrag inhaltlich gegen die Quellen (Zahlen, Kernaussagen, Verfälschungen) und korrigiert Warnungen automatisch — alle Ausgaben (PDF, TXT, ePub, ElevenReader) tragen die geprüfte Fassung. Das Zusammenführen von Doppel-Themen läuft immer, unabhängig von diesem Schalter. ~3–5 Min extra pro Lauf, 0 € übers Abo.",
    )

if narrative_additional:
    _narr_depth_col, _narr_info_col = st.columns([1, 3])
    with _narr_depth_col:
        narrative_depth = st.radio(
            "Erzähl-Tiefe",
            options=["Standard", "Ausführlich"],
            key="narrative_depth_radio",
            horizontal=True,
            help="Standard = kompakter Überblick über alle Themen. Ausführlich = volle Original-Tiefe je Beitrag.",
        )
    with _narr_info_col:
        if narrative_depth == "Standard":
            st.caption("🎙️ Kompakter Podcast-Stil — schneller Überblick über alle Themen")
        else:
            st.caption("🎙️ Ausführliche Erzähl-Version — gleiche Tiefe wie das klassische Briefing, nur flüssiger erzählt")

genius_depth_label = "Lang"

# "Neues Briefing" bleibt sichtbar (geteilter Reset für beide Pfade)
_nb_col1, _nb_col2 = st.columns([3, 1])
with _nb_col2:
    st.button(
        "Neues Briefing",
        use_container_width=True,
        on_click=_set_confirm_clear,
        args=(True,),
    )

if st.session_state.confirm_clear:
    st.warning("Aktuelle Texte und letzte Downloads wirklich löschen?")
    confirm_col1, confirm_col2 = st.columns(2)
    with confirm_col1:
        st.button(
            "Ja, löschen",
            type="secondary",
            use_container_width=True,
            on_click=_clear_briefing_state,
        )
    with confirm_col2:
        st.button(
            "Abbrechen",
            use_container_width=True,
            on_click=_set_confirm_clear,
            args=(False,),
        )

# ============================================================
# 🦉 STANDARD: Briefing via Claude-CLI (kostenlos, Max-Abo)
#    — prominent/offen. API-Version steht als Option WEITER UNTEN.
# ============================================================

st.markdown("---")
st.markdown('<div id="nav-claude" style="position:relative; top:-64px;"></div>', unsafe_allow_html=True)
with st.expander("🦉 Briefing mit Claude erstellen (kostenlos via Max-Abo) — Standard", expanded=True):
    _cli_path = _locate_claude_cli()
    _cli_available = bool(_cli_path)

    st.caption("Standard-Weg: **kostenlos** über dein Claude Max-Abo (0,00 € pro Briefing). Voll-PDF + Kompaktfassung in einem Lauf — die Beiträge werden parallel verarbeitet.")
    if not _cli_available:
        st.warning("⚠️ Claude CLI nicht gefunden. Installiere [Claude Code](https://claude.com/claude-code), um den kostenlosen Standard-Weg zu nutzen. Solange greift der kostenpflichtige API-Pfad weiter unten.")
    else:
        _total_units = len(url_preview["urls"]) + len(_paywall_blocks if _paywall_blocks else []) + len(_podcast_blocks if _podcast_blocks else [])
        with st.expander("ℹ️ Wofür ist das & wie lange dauert es?", expanded=False):
            st.caption("Holt alle Quellen (Artikel, Paywall, Podcasts, Wetter), fasst jeden Beitrag zusammen und baut Voll-PDF + Kompaktfassung — alles über dein Claude Max-Abo, kostenlos. Parallele Verarbeitung, daher zügig.")
            if _total_units >= 60:
                st.caption(f"⏱️ Sehr großes Briefing ({_total_units} Beiträge): grob ~8–15 Min, mit Checks + Kompakt ~12–22 Min.")
            elif _total_units >= 30:
                st.caption(f"⏱️ Mittelgroßes Briefing ({_total_units} Beiträge): grob ~4–8 Min, mit Checks + Kompakt ~8–14 Min.")
            elif _total_units >= 10:
                st.caption(f"⏱️ Standard-Briefing ({_total_units} Beiträge): grob ~3–6 Min, mit Checks + Kompakt ~6–10 Min.")
            else:
                st.caption("⏱️ Kleines Briefing — meist in wenigen Minuten fertig.")

    # Direkt-Modus-Status früh aus session_state lesen (Widget wird erst weiter unten
    # erzeugt). Bei aktivem Direkt-Modus haben Modell (Artikel) und Stil keine Wirkung —
    # es wird kein Voll-PDF mit Artikeln erzeugt — daher ausgrauen.
    _direct_only_now = bool(st.session_state.get("claude_cli_direct_genius_only", False))
    with st.expander("🎧 ElevenReader-Automatik (Briefing landet von selbst auf dem iPhone)", expanded=False):
        st.checkbox(
            "Nach jedem Briefing automatisch in meine ElevenReader-Bibliothek hochladen",
            key="auto_reader_upload",
            help="Lädt die Vorlese-TXT nach jedem Lauf per unsichtbarem Browser in deine ElevenReader-Bibliothek (elevenreader.io) — die synct aufs iPhone, dort kommt die Hörbuch-bereit-Meldung. Braucht den Einmal-Login unten. Kostenlos.",
        )
        _rp_exists = os.path.isdir(os.path.expanduser("~/.briefing_reader_profile"))
        st.caption(("🟢 Profil vorhanden — Verbindung wird beim Upload geprüft." if _rp_exists
                    else "⚪ Noch nicht verbunden — einmal den Login-Knopf drücken."))
        st.selectbox(
            "🗑️ Automatisch aufräumen: Tagesbriefings älter als …",
            options=[0, 7, 14, 21], key="reader_cleanup_days",
            format_func=lambda d: "Aus" if d == 0 else f"{d} Tage",
            help="Löscht nach jedem Upload automatisch alte Tagesbriefing-Einträge aus deiner Bibliothek. Fasst NUR Titel an, die mit Tagesbriefing beginnen — Bücher und Wochenbriefings bleiben unberührt.",
        )
        _rd_col1, _rd_col2 = st.columns(2)
        with _rd_col1:
            if st.button("🔐 Einmal-Login-Fenster öffnen", key="reader_login_btn", use_container_width=True,
                         help="Öffnet ein eigenes Browserfenster. Dort einmal bei ElevenReader/ElevenLabs anmelden — das Fenster schließt sich selbst, sobald der Login erkannt ist."):
                import subprocess as _sp
                _sp.Popen([sys.executable, str(_APP_DIR / "reader_upload.py"), "--login"],
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                st.info("Login-Fenster geöffnet (kann ein paar Sekunden dauern). Dort anmelden — es schließt sich von selbst.")
        with _rd_col2:
            if st.button("🧪 Verbindung testen", key="reader_status_btn", use_container_width=True):
                import subprocess as _sp
                with st.spinner("Prüfe Anmeldung (~15s)…"):
                    _st = _sp.run([sys.executable, str(_APP_DIR / "reader_upload.py"), "--status"],
                                  capture_output=True, text=True, timeout=90)
                if "ANGEMELDET" in (_st.stdout or "") and "NICHT" not in (_st.stdout or ""):
                    st.success("✅ Verbunden — Uploads laufen automatisch.")
                else:
                    st.warning("Noch nicht angemeldet — bitte den Einmal-Login machen.")
    with st.expander("⚙️ Feineinstellungen — Modell & Kompaktfassung (Standard passt)", expanded=False):
        st.caption("Alles hier hat sinnvolle Dauerwerte — du musst normalerweise nichts anfassen. Der Qualitäts-Check steht oben bei den Briefing-Optionen; Doppel-Themen werden immer zusammengeführt.")
        _cli_model = st.selectbox(
            "Modell (Artikel)",
            options=["sonnet", "opus", "haiku"],
            index=0,
            key="claude_cli_model",
            help="Modell für die Artikel-Zusammenfassungen. sonnet = empfohlen — löst automatisch auf das neueste Sonnet auf, aktuell Sonnet 5 (fast Opus-Qualität, schnell). opus = beste Qualität, langsamer. haiku = schneller, knapper.",
            disabled=not _cli_available or _direct_only_now,
        )
        if _direct_only_now:
            st.caption("⚪ Im Direkt-Modus ohne Wirkung — es wird kein Voll-PDF mit Artikeln erzeugt.")

        # Kompaktfassung-Optionen (Auswahl Lang/Kurz/Beide steht oben in den Einstellungen)
        _cli_genius_mode = st.session_state.get("genius_depth_radio_main", "Keine")
        st.session_state["claude_cli_genius_mode"] = _cli_genius_mode
        _cli_genius_opus = st.checkbox(
            "🧠 Kompaktfassung mit Opus (beste Qualität)",
            value=True,
            key="claude_cli_genius_opus",
            help="Erzeugt die Kompaktfassung mit Opus 4.8 statt mit dem Artikel-Modell. Verdichtung + Qualitätscheck + ggf. Repair laufen auf Opus (bis zu 3 Opus-Aufrufe pro Kompaktfassung — kein Pro-Artikel-Aufruf, also quota-schonend). Etwas langsamer. Die Artikel-Masse bleibt beim Artikel-Modell.",
            disabled=not _cli_available or _cli_genius_mode == "Keine",
        )
        _cli_genius_model = "opus" if _cli_genius_opus else _cli_model
        _cli_genius_enabled = _cli_genius_mode != "Keine"
        if _cli_genius_enabled:
            _kompakt_disp = "Lang + Kurz" if _cli_genius_mode == "Beide" else _cli_genius_mode
            st.caption(f"✨ Kompaktfassung: **{_kompakt_disp}** (oben gewählt) · Modell: {'Opus' if _cli_genius_opus else _cli_model}")
        else:
            st.caption("✨ Kompaktfassung: **Keine** (oben gewählt) — es wird nur das Voll-Briefing erstellt.")
        _cli_genius_internal_mode = {"Lang": "long", "Standard": "standard", "Kurz": "short", "Beide": "long"}.get(_cli_genius_mode, "long")
        # Erzählmodus entfernt (03.07.) — die Themen-Synthese mit Magazin-Stil (oben)
        # hat ihn ersetzt; die alten Codepfade bleiben schlafend erhalten.
        _cli_narrative_selected = False

        # Direct-Mode-Checkbox: Nur Kompaktfassung, kein Voll-Briefing
        _cli_direct_genius_only = st.checkbox(
            "📋 Nur Kompaktfassung direkt (kein Voll-PDF) — schneller, weniger Quota-Verbrauch",
            value=False,
            key="claude_cli_direct_genius_only",
            help=(
                "Wenn aktiv: Claude schreibt direkt aus den Rohdaten eine Kompaktfassung. "
                "Kein Voll-PDF, kein Voll-Briefing-Qualitäts-Check. "
                "Aber: Kompakt-Lint, Quality-Check und Auto-Repair für die Kompaktfassung laufen "
                "weiter — Qualität bleibt gleich. ~5–10 Min Wartezeit, 1–3 Max-Abo-Calls."
            ),
            disabled=not _cli_available or not _cli_genius_enabled,
        )
        if _cli_direct_genius_only and not _cli_genius_enabled:
            st.caption('⚠️ Direkt-Modus + „Keine Kompaktfassung" widersprechen sich — wähle oben eine Länge (Lang/Kurz) ODER deaktiviere den Direkt-Modus.')

        # "Erzähl-Version zusätzlich" wird über den geteilten Schalter oben gesteuert
        # (gilt für beide Wege) — kein eigenes Steuerelement/Statuszeile hier.
        _cli_narrative_extra = bool(st.session_state.get("narrative_additional_main", False))
        # Qualitäts-Schalter steht oben bei den Briefing-Optionen (ein Schalter für
        # Check + Auto-Korrektur); Doppel-Themen-Merge läuft immer.
        _cli_content_check_enabled = bool(st.session_state.get("quality_check_enabled", True)) and _cli_available
        _cli_auto_repair_enabled = _cli_content_check_enabled
        _cli_merge_duplicates = True


    def _format_token_usage(usage: Optional[dict], label: str = "") -> str:
        """Formatiert ein usage-Dict aus stream-json result-Event als Caption-String."""
        if not usage or not isinstance(usage, dict):
            return ""
        inp = usage.get("input_tokens") or 0
        out = usage.get("output_tokens") or 0
        cache_create = usage.get("cache_creation_input_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        parts = []
        if inp:
            parts.append(f"{inp:,} In".replace(",", "."))
        if out:
            parts.append(f"{out:,} Out".replace(",", "."))
        if cache_read:
            parts.append(f"{cache_read:,} Cache".replace(",", "."))
        if cache_create:
            parts.append(f"{cache_create:,} CacheCreate".replace(",", "."))
        if not parts:
            return ""
        prefix = f"{label} · " if label else ""
        return f"{prefix}🪙 {' · '.join(parts)} Token (Max-Abo)"


    # Token-Akkumulator für Gesamtanzeige am Ende
    _cli_token_accumulator = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "calls": 0}


    def _accumulate_usage(usage: Optional[dict]):
        if not usage or not isinstance(usage, dict):
            return
        _cli_token_accumulator["input"] += int(usage.get("input_tokens") or 0)
        _cli_token_accumulator["output"] += int(usage.get("output_tokens") or 0)
        _cli_token_accumulator["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
        _cli_token_accumulator["cache_create"] += int(usage.get("cache_creation_input_tokens") or 0)
        _cli_token_accumulator["calls"] += 1


    _auto_fire = bool(st.session_state.pop("_auto_run_briefing", False)) and _cli_available
    if _auto_fire:
        st.info("🚀 Automatisch gestartet — alle Podcast-Zusammenfassungen waren fertig.")
    if st.button(
        "🤖 Briefing automatisch via Claude bauen (kostenlos)",
        key="claude_cli_run_button_main",
        use_container_width=True,
        type="primary",
        disabled=not _cli_available,
        help="Holt alle Artikel + Wetter, schickt sie an Claude, baut Voll-PDF + Kompaktfassung. ~5–10 Min Wartezeit. Nutzt dein Max-Abo, keine API-Kosten.",
    ) or _auto_fire:
        if not (urls_text.strip() or paywall_text.strip() or podcast_text.strip() or include_weather):
            st.warning("Mindestens ein Feld ausfüllen oder Wetter aktivieren.")
        elif _cli_direct_genius_only and not _cli_genius_enabled:
            st.warning('Direkt-Modus aktiv, aber keine Kompaktfassung gewählt. Bitte oben Lang, Kurz oder Beide wählen.')
        elif _cli_direct_genius_only:
            # ============================================================
            # DIREKT-MODUS: Nur Kompaktfassung direkt aus Rohdaten
            # ============================================================
            if _cli_genius_mode == "Beide":
                st.info("ℹ️ Im Direkt-Modus wird bei der Auswahl Beide nur die Lang-Fassung erzeugt. Für Lang UND Kurz den normalen Lauf nutzen (das Häkchen bei Nur-Kompaktfassung-direkt entfernen).")
            _direct_progress = st.progress(0)
            _direct_status = st.empty()

            def _on_direct_progress(step: str, ratio: float):
                try:
                    _direct_progress.progress(max(0.0, min(1.0, float(ratio))))
                    _direct_status.caption(step)
                except Exception:
                    pass

            try:
                if podcast_text and podcast_text.strip():
                    podcast_text, _n_pod_raw, _ = preprocess_podcast_text(
                        podcast_text, progress_callback=_on_direct_progress)
                _on_direct_progress("Rohdaten werden geholt und verpackt…", 0.02)
                handoff_text = build_claude_handoff_package(
                    urls_text=urls_text,
                    paywall_text=paywall_text,
                    podcast_text=podcast_text,
                    include_weather=include_weather,
                    compact_mode=st.session_state.get("compact_mode", True),
                    narrative_mode=False,
                    # KEIN Briefing-/JSON-Prompt voranstellen — der Genius-System-Prompt liefert
                    # die Anweisungen. Sonst folgt Opus bei vielen Beiträgen der eingebetteten
                    # JSON-Briefing-Anweisung statt dem [N]-Kompakt-Format.
                    include_prompt=False,
                )
                st.session_state["claude_handoff_text"] = handoff_text

                _ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
                _direct_suffix_map = {"long": "_lang", "short": "_kurz", "standard": "_standard"}
                _direct_suffix = _direct_suffix_map.get(_cli_genius_internal_mode, "_lang")
                archive_dir = _resolve_archive_dir(for_write=True)
                _kompakt_dir = archive_dir / "Kompaktfassungen"
                _kompakt_dir.mkdir(parents=True, exist_ok=True)
                _direct_pdf_path = _kompakt_dir / f"{_ts}_tagesbriefing_kompakt{_direct_suffix}_direkt.pdf"

                _direct_result = run_direct_genius_via_claude_cli(
                    handoff_text=handoff_text,
                    output_pdf_path=str(_direct_pdf_path),
                    mode=_cli_genius_internal_mode,
                    model=_cli_genius_model,
                    progress_callback=_on_direct_progress,
                    cli_path=_cli_path,
                )

                _direct_progress.empty()
                _direct_status.empty()

                if _direct_result["ok"]:
                    _d_meta = _direct_result.get("meta", {}) or {}
                    _d_elapsed = _direct_result.get("elapsed_seconds") or 0
                    _accumulate_usage(_direct_result.get("usage"))
                    _d_badges = []
                    if _d_meta.get("used_fallback"):
                        _d_badges.append("⚠️ Sicherheitsnetz aktiv")
                    elif _d_meta.get("soft_intelligent"):
                        _d_badges.append("🧠 Intelligente Opus-Fassung (Fließtext, ohne strenge Nummerierung)")
                    else:
                        _d_badges.append(f"✨ {_d_meta.get('mode_label', _cli_genius_mode)}-Variante (Direkt)")
                    _d_badges.append(f"{_d_meta.get('total', '?')} Beiträge")
                    _q_issues = _d_meta.get("quality_issues") or []
                    _q_applied = _d_meta.get("quality_repair_applied", False)
                    if _q_applied:
                        _d_badges.append(f"🔧 {len(_q_issues)} Quality-Issue{'s' if len(_q_issues) != 1 else ''} repariert")
                    elif _q_issues:
                        _d_badges.append(f"⚠️ {len(_q_issues)} Issue{'s' if len(_q_issues) != 1 else ''} (nicht repariert)")
                    elif not _d_meta.get("used_fallback") and not _d_meta.get("soft_intelligent"):
                        _d_badges.append("✅ Quality-Check sauber")

                    st.success(
                        f"✅ Direkt-Kompaktfassung fertig in {_d_elapsed:.0f}s — "
                        f"`{_direct_pdf_path.name}` (0,00 €, kein Voll-PDF)"
                    )
                    st.caption(" · ".join(_d_badges))
                    _direct_usage_str = _format_token_usage(_direct_result.get("usage"))
                    if _direct_usage_str:
                        st.caption(_direct_usage_str)
                    # Gesamt-Token-Anzeige am Ende des Direct-Modus-Laufs
                    _t = _cli_token_accumulator
                    if _t.get("calls", 0) >= 1:
                        _total_in = _t["input"] + _t["cache_read"] + _t["cache_create"]
                        _total_out = _t["output"]
                        st.info(
                            f"📊 **Gesamt-Verbrauch dieses Laufs (Max-Abo):** "
                            f"{_t['calls']} Call{'s' if _t['calls'] != 1 else ''} · "
                            f"{_total_in:,} Input + {_total_out:,} Output Token "
                            f"≈ {(_total_in + _total_out) / 1000:.0f}k Token"
                            .replace(",", ".")
                        )
                    if _q_issues:
                        with st.expander(
                            f"📋 Quality-Check Details: {len(_q_issues)} Befund{'e' if len(_q_issues) != 1 else ''}",
                            expanded=False,
                        ):
                            for issue in _q_issues:
                                cat = issue.get("category", "?")
                                msg = issue.get("message", "")
                                b = issue.get("beitrag", "?")
                                st.markdown(f"- **{cat}** [{b}]: {msg}")
                                if issue.get("fix_hint"):
                                    st.caption(f"  Hinweis: {issue['fix_hint']}")
                    try:
                        with open(_direct_pdf_path, "rb") as fp:
                            st.download_button(
                                label=f"📥 Kompaktfassung-PDF ({_cli_genius_mode}) herunterladen",
                                data=fp.read(),
                                file_name=_direct_pdf_path.name,
                                mime="application/pdf",
                                use_container_width=True,
                                on_click="ignore",
                                key=f"claude_cli_direct_pdf_{_ts}",
                            )
                    except Exception as exc:
                        st.warning(f"PDF gespeichert, Download-Fehler: {exc}")
                    st.caption(f"📂 Abgelegt in `{_kompakt_dir}`")
                else:
                    st.error(f"Direkt-Kompaktfassung fehlgeschlagen: {_direct_result.get('error')}")
                    if _direct_result.get("raw_response"):
                        with st.expander("🔍 Roh-Antwort (Debug)", expanded=False):
                            st.code(_direct_result["raw_response"][:8000], language="markdown")
            except Exception as exc:
                try:
                    _direct_progress.empty()
                    _direct_status.empty()
                except Exception:
                    pass
                st.error(f"Unerwarteter Fehler: {exc}")
        else:
            _save_draft()  # aktuelle Einstellungen (Synthese/Stil/Länge …) refresh-fest sichern
            _cli_progress = st.progress(0)
            _cli_status = st.empty()

            def _on_cli_progress(step: str, ratio: float):
                try:
                    _cli_progress.progress(max(0.0, min(1.0, float(ratio))))
                    _cli_status.caption(step)
                except Exception:
                    pass

            try:
                # Rohe Podcast-Transkripte (kein Endmarker + lang) VOR allem anderen
                # verdichten — gilt für beide Pfade (chunked + einteilig).
                if podcast_text and podcast_text.strip():
                    podcast_text, _n_pod_raw, _pod_raw_errs = preprocess_podcast_text(
                        podcast_text, progress_callback=_on_cli_progress)
                    if _n_pod_raw:
                        st.caption(f"🎙️ {_n_pod_raw} rohes/rohe Podcast-Transkript(e) automatisch zusammengefasst.")
                    for _pe in (_pod_raw_errs or []):
                        st.warning(f"⚠️ Podcast-Transkript nicht zusammengefasst — {_pe}")
                # Bei großen Briefings (25+ Beiträge) chunked laufen lassen, damit die
                # CLI-Verbindung nicht abreißt. Erzählmodus nur im einteiligen Pfad.
                _cli_item_count = (
                    len(url_preview["urls"])
                    + len(_paywall_blocks or [])
                    + len(_podcast_blocks or [])
                )
                _depths_run = st.session_state.get("_depths_to_run") or ["Kürzer"]
                _synth_on = bool(st.session_state.get("topic_synthesis_mode", False))
                _wa_on = bool(st.session_state.get("whatsapp_pdf_additional", True))
                # Mehrere Längen oder Themen-Synthese → immer Häppchen-Modus (nur der
                # kann Basis-Wiederverwendung bzw. die Synthese-Pipeline).
                _use_chunked = ((_cli_item_count >= 25) or len(_depths_run) > 1 or _synth_on or _wa_on) and not _cli_narrative_selected
                if len(_depths_run) > 1 and _cli_narrative_selected:
                    st.caption("ℹ️ Erzähl-Version gewählt — es wird nur die erste Länge erstellt.")

                _ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
                _suffix = "_claude_erzaehl" if _cli_narrative_selected else ("_claude_synthese" if _synth_on else "_claude")
                archive_dir = _resolve_archive_dir(for_write=True)
                archive_dir.mkdir(parents=True, exist_ok=True)
                out_path = archive_dir / f"{_ts}_briefing{_suffix}.pdf"
                _multi_results = []  # bei Mehrfach-Längen: [(Länge, Pfad, result), …]

                if _use_chunked:
                    _on_cli_progress(f"Großes Briefing ({_cli_item_count} Beiträge) — Häppchen-Modus startet…", 0.02)
                    handoff_text = ""  # im chunked-Pfad nicht gebaut; verhindert NameError unten
                    st.session_state["claude_handoff_text"] = ""
                    _depth_suffix = {"Sehr kurz": "_sehr-kurz", "Kürzer": "_kuerzer", "Ausführlich": "_ausfuehrlich", "Intelligent": "_intelligent"}
                    # Lauf-Plan: WhatsApp-Lese-Version (klassisch, Sehr kurz) ZUERST —
                    # sie füllt die geteilte Basis; die Hauptversionen laufen danach,
                    # damit _AKTUELL-TXT und Reader-Upload die Hörversion tragen.
                    _run_plan = []
                    if _wa_on:
                        _run_plan.append({"label": "WhatsApp 📱", "depth": "Sehr kurz", "synth": False,
                                          "suffix": "_whatsapp", "wa": True})
                    for _depth in _depths_run:
                        _sfx = _depth_suffix[_depth] if (len(_depths_run) > 1) else ""
                        _run_plan.append({"label": _depth, "depth": _depth, "synth": _synth_on,
                                          "suffix": _sfx, "wa": False})
                    _multi = len(_run_plan) > 1
                    # Plausi-Check + Auto-Repair laufen für die ERSTE Hauptversion (nicht WhatsApp,
                    # nicht Zweitlängen — die teilen dieselbe geprüfte Rohdaten-Basis).
                    _check_idx = next((_ci for _ci, _cr in enumerate(_run_plan) if not _cr["wa"]), 0)
                    _special_list = split_special_topics(st.session_state.get("special_topics_text") or "")
                    _prepared_pkg = None
                    _multi_results = []  # [(Label, Pfad, result), …]
                    for _di, _rp in enumerate(_run_plan):
                        _depth = _rp["depth"]
                        if _multi or _rp["suffix"]:
                            out_path = archive_dir / f"{_ts}_briefing{_suffix}{_rp['suffix'] or _depth_suffix[_depth]}.pdf"
                        _pref = f"Version {_di + 1}/{len(_run_plan)} ({_rp['label']}) · " if _multi else ""

                        def _on_cli_progress_v(step, ratio, _p=_pref):
                            _on_cli_progress(_p + str(step), ratio)

                        cli_result = run_briefing_via_claude_cli_chunked(
                            urls_text=urls_text,
                            paywall_text=paywall_text,
                            podcast_text=podcast_text,
                            include_weather=include_weather,
                            output_pdf_path=str(out_path),
                            model=_cli_model,
                            compact_mode=(_depth != "Ausführlich"),
                            ultra_compact=(_depth == "Sehr kurz"),
                            progress_callback=_on_cli_progress_v,
                            cli_path=_cli_path,
                            merge_duplicates=_cli_merge_duplicates,
                            prepared=_prepared_pkg,
                            topic_synthesis=_rp["synth"],
                            synthesis_narrative=bool(_rp["synth"] and st.session_state.get("synthesis_narrative_style", False)),
                            synthesis_web_enrich=bool(_rp["synth"] and st.session_state.get("synthesis_web_enrich", True)),
                            content_check=bool(_cli_content_check_enabled and _di == _check_idx),
                            auto_repair=bool(_cli_auto_repair_enabled and _di == _check_idx),
                            special_topics=(_special_list if _di == _check_idx else None),
                            smart_length=bool(_rp["depth"] == "Intelligent"),
                        )
                        if not cli_result.get("ok"):
                            break  # Fehler-Handling unten greift für cli_result
                        _prepared_pkg = cli_result.get("prepared")
                        _multi_results.append((_rp["label"], out_path, cli_result))
                else:
                    _on_cli_progress("Rohdaten werden geholt und für Claude verpackt…", 0.02)
                    handoff_text = build_claude_handoff_package(
                        urls_text=urls_text,
                        paywall_text=paywall_text,
                        podcast_text=podcast_text,
                        include_weather=include_weather,
                        compact_mode=st.session_state.get("compact_mode", True),
                        narrative_mode=_cli_narrative_selected,
                        merge_duplicates=_cli_merge_duplicates,
                    )
                    st.session_state["claude_handoff_text"] = handoff_text
                    cli_result = run_briefing_via_claude_cli(
                        handoff_text=handoff_text,
                        output_pdf_path=str(out_path),
                        model=_cli_model,
                        progress_callback=_on_cli_progress,
                        cli_path=_cli_path,
                    )

                _cli_progress.empty()
                _cli_status.empty()

                if cli_result["ok"]:
                    elapsed = cli_result.get("elapsed_seconds") or 0
                    _accumulate_usage(cli_result.get("usage"))
                    _chunk_note = cli_result.get("chunked_note")
                    if elapsed:
                        _em, _es = divmod(int(elapsed), 60)
                        _timing = f"in {_em} Min {_es} Sek " if _em else f"in {_es} Sek "
                    else:
                        _timing = ""
                    _mode_note = f" · Häppchen-Modus: {_chunk_note}" if _chunk_note else ""
                    st.success(
                        f"✅ Voll-Briefing fertig {_timing}— "
                        f"{cli_result['sections_count']} Beiträge → `{out_path.name}` "
                        f"(0,00 €){_mode_note}"
                    )
                    if len(_multi_results) > 1:
                        st.info(f"🔁 Alle {len(_multi_results)} Versionen erstellt (gemeinsame Artikel-Basis, nur einmal geladen):")
                        for _md, _mp, _mr in _multi_results:
                            st.caption(f"   · {_md}: `{_mp.name}` — {_mr.get('sections_count', 0)} Beiträge")
                    _sp_main = next((r for _l9, _p9, r in _multi_results if r.get("special_done") is not None), None)
                    if _sp_main is not None:
                        _sp_failed = _sp_main.get("special_failed_topics") or []
                        st.session_state["special_topics_text_pending_value"] = "\n".join(_sp_failed)
                        if _sp_main.get("special_done"):
                            st.success(f"🧠 {_sp_main['special_done']} Sonderthema/-themen recherchiert und als eigener Block eingewoben.")
                        if _sp_failed:
                            st.warning(f"🧠 {len(_sp_failed)} Sonderthema/-themen fehlgeschlagen — bleiben in der Box für den nächsten Lauf.")
                    for _md, _mp, _mr in _multi_results:
                        if str(_md).startswith("WhatsApp"):
                            st.success(f"📱 WhatsApp-Lese-PDF bereit zum Verschicken: `{_mp.name}`")
                    # 🎧 Automatisch in die ElevenReader-Bibliothek hochladen (synct aufs iPhone)
                    if st.session_state.get("auto_reader_upload", True) and _multi_results:
                        try:
                            from reader_upload import upload_briefing_txt, upload_briefing_epub, cleanup_old_briefings
                            _wd_de = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
                            _now_up = datetime.datetime.now()
                            for _md, _mp, _mr in _multi_results:
                                if str(_md).startswith("WhatsApp"):
                                    continue  # Lese-Version ist fürs Verschicken, nicht für die Hör-Bibliothek
                                _txtp = (_mr.get("artifacts") or {}).get("eleven_txt")
                                if not _txtp:
                                    continue
                                _ttl = f"Tagesbriefing {_wd_de[_now_up.weekday()]} {_now_up.strftime('%d.%m.')}"
                                if len(_multi_results) > 1:
                                    _ttl += f" – {_md}"
                                if st.session_state.get("topic_synthesis_mode"):
                                    _ttl += " 🧵"
                                with st.spinner(f"🎧 Sende an ElevenReader (mit Ressort-Kapiteln): {_ttl}…"):
                                    _ur = upload_briefing_epub(_txtp, _ttl)
                                    if not _ur.get("ok"):
                                        _ur = upload_briefing_txt(_txtp, _ttl)
                                if _ur.get("ok"):
                                    st.success(f"🎧 Liegt in deiner ElevenReader-Bibliothek: {_ttl} ({_ur['elapsed_seconds']:.0f}s) — iPhone meldet sich gleich.")
                                else:
                                    st.warning(f"🎧 ElevenReader-Upload: {_ur.get('error')} — TXT liegt wie immer im Texte-Ordner (Kurzbefehl-Fallback).")
                            _cd = int(st.session_state.get("reader_cleanup_days", 14) or 0)
                            if _cd > 0:
                                try:
                                    with st.spinner(f"🗑️ Räume auf: Tagesbriefings älter als {_cd} Tage…"):
                                        _cl = cleanup_old_briefings(_cd)
                                    if _cl.get("deleted"):
                                        st.caption(f"🗑️ {len(_cl['deleted'])} alte(s) Tagesbriefing(s) aus der Bibliothek entfernt.")
                                except Exception as _clex:
                                    st.caption(f"🗑️ Aufräumen übersprungen: {_clex}")
                        except ImportError:
                            st.caption("🎧 ElevenReader-Upload übersprungen — Playwright fehlt (pip3 install --user playwright).")
                        except Exception as _rex:
                            st.warning(f"🎧 ElevenReader-Upload übersprungen: {_rex}")
                    # Zusammengeführte Doppel-Themen transparent zeigen
                    _merged_n = cli_result.get("merged_topics", 0) or 0
                    if _merged_n:
                        st.info(f"🧬 {_merged_n} Doppel-Thema/Themen aus mehreren Quellen zu je EINEM Beitrag zusammengeführt (alle Details integriert).")
                        for _mn in (cli_result.get("merge_notes") or [])[:5]:
                            st.caption(f"   · {_mn}")
                    # Deutlich warnen, wenn ganze Gruppen gescheitert sind (≈ Beiträge fehlen)
                    _failed_g = cli_result.get("failed_groups", 0) or 0
                    if _failed_g > 0:
                        _approx = _failed_g * (cli_result.get("chunk_size") or 12)
                        st.warning(
                            f"⚠️ {_failed_g} von {cli_result.get('n_groups', '?')} Gruppen konnten trotz "
                            f"Wiederholung NICHT erzeugt werden — es fehlen ca. {_approx} Beiträge. "
                            f"Tipp: nochmal erstellen oder den API-Pfad (unten) nutzen."
                        )
                    # Diagnose: URLs, die kaum/keinen Inhalt lieferten (Fetch fehlgeschlagen / Cookie-Wall)
                    _weak = cli_result.get("weak_fetches") or []
                    if _weak:
                        with st.expander(f"⚠️ {len(_weak)} URL(s) lieferten kaum Inhalt (Fetch fehlgeschlagen, z.B. Cookie-Wall) — diese Storys fehlen", expanded=False):
                            for _wf in _weak:
                                st.caption(f"· {_wf.get('chars', 0)} Zeichen — {_wf.get('url', '?')}")
                            st.caption("Tipp: solche Quellen als Paywall-Text einfügen (Inhalt direkt reinkopieren) statt als URL — oder weglassen.")
                    _voll_usage_str = _format_token_usage(cli_result.get("usage"))
                    if _voll_usage_str:
                        st.caption(_voll_usage_str)
                    try:
                        with open(out_path, "rb") as fp:
                            st.download_button(
                                label="📥 Voll-PDF herunterladen",
                                data=fp.read(),
                                file_name=out_path.name,
                                mime="application/pdf",
                                use_container_width=True,
                                on_click="ignore",
                                key=f"claude_cli_pdf_main_{_ts}",
                            )
                    except Exception as exc:
                        st.warning(f"PDF gespeichert, Download-Button-Fehler: {exc}")
                    st.caption(f"📂 PDF abgelegt in `{archive_dir}`")

                    _from_pipeline = cli_result.get("artifacts") or {}
                    _eleven_path = _from_pipeline.get("eleven_txt")
                    if _eleven_path:
                        st.caption(f"📝 Eleven-Reader-TXT für Wochen-Meta gespeichert: `{Path(_eleven_path).name}`")

                    comp = cli_result.get("completeness")
                    if comp:
                        ratio = comp["found"] / max(comp["checked"], 1)
                        if comp["missing"]:
                            st.warning(
                                f"⚠️ Vollständigkeit: {comp['found']}/{comp['checked']} "
                                f"Beiträge erkannt ({int(ratio * 100)}%)"
                            )
                            with st.expander(f"❌ {len(comp['missing'])} fehlende Beiträge"):
                                for item in comp["missing"]:
                                    st.markdown(f"- **{item['kind']} {item['num']}**: {item['title']}")
                        else:
                            st.success(f"✅ Vollständigkeit: alle {comp['checked']} Beiträge gefunden")

                    # === Plausibilitäts-Check (lokaler Output-Lint) ===
                    _output_lint = cli_result.get("output_lint")
                    if _output_lint and _output_lint.get("checked", 0) > 0:
                        _lint_warnings = _output_lint.get("warnings", 0)
                        _lint_notices = _output_lint.get("notices", 0)
                        _lint_ok = _output_lint.get("ok", 0)
                        _lint_checked = _output_lint.get("checked", 0)
                        if _lint_warnings == 0 and _lint_notices == 0:
                            st.success(f"✅ Qualitäts-Check (lokal): alle {_lint_checked} Beiträge sauber strukturiert")
                        else:
                            _lint_msg = f"📋 Qualitäts-Check (lokal): {_lint_ok}/{_lint_checked} sauber"
                            if _lint_warnings:
                                _lint_msg += f" · {_lint_warnings} Warnung{'en' if _lint_warnings != 1 else ''}"
                            if _lint_notices:
                                _lint_msg += f" · {_lint_notices} Hinweis{'e' if _lint_notices != 1 else ''}"
                            if _lint_warnings:
                                st.warning(_lint_msg)
                            else:
                                st.info(_lint_msg)
                            _lint_items = _output_lint.get("items", []) or []
                            if _lint_items:
                                with st.expander(
                                    f"📋 Details: {len(_lint_items)} Befund{'e' if len(_lint_items) != 1 else ''}",
                                    expanded=False,
                                ):
                                    for item in _lint_items[:30]:
                                        severity = item.get("severity", "notice")
                                        icon = "⚠️" if severity == "warning" else "ℹ️"
                                        label = item.get("label", "Beitrag")
                                        msg = item.get("message", "")
                                        st.markdown(f"{icon} **{label}**: {msg}")
                                    if len(_lint_items) > 30:
                                        st.caption(f"… und {len(_lint_items) - 30} weitere — siehe Voll-PDF zum Vergleich")

                    # === Sortier-Diagnose (lokal) ===
                    _sorting_diag = cli_result.get("sorting_diag")
                    if _sorting_diag and _sorting_diag.get("checked", 0) >= 3:
                        if _sorting_diag.get("ok"):
                            st.success(
                                f"✅ Sortier-Diagnose: thematische Reihenfolge plausibel "
                                f"({_sorting_diag.get('checked')} Beiträge, "
                                f"{_sorting_diag.get('violations', 0)} Mini-Sprünge im Toleranzfenster)"
                            )
                        else:
                            st.warning(
                                f"⚠️ Sortier-Diagnose: {_sorting_diag.get('violations', 0)} Themen-Sprünge "
                                f"(Toleranz: {_sorting_diag.get('violation_threshold', 0)}). "
                                f"Reihenfolge ist nicht klar nach Wetter→Lokal→Politik→… sortiert."
                            )
                            with st.expander("📋 Bucket-Reihenfolge anzeigen", expanded=False):
                                _buckets = _sorting_diag.get("buckets_actual") or []
                                for i, b in enumerate(_buckets, 1):
                                    st.markdown(f"- Beitrag {i}: `{b}`")
                                st.caption(f"Erwartete Reihenfolge: {' → '.join(_sorting_diag.get('expected_order', []))}")

                    # === Inhaltlicher Plausibilitäts-Check via Claude (extra CLI-Call) ===
                    # Im chunked-Pfad übersprungen: ein zusätzlicher langer Claude-Call
                    # bei großen Briefings widerspricht dem Sinn des Häppchen-Modus.
                    # Der lokale Output-Lint (oben) hat das Briefing bereits geprüft.
                    _clean_sections_for_check = cli_result.get("clean_sections") or []
                    if _use_chunked and _cli_content_check_enabled:
                        # Der Check lief IN der Pipeline (Hauptversion, vor dem PDF-Bau) —
                        # hier nur noch das Ergebnis anzeigen.
                        _cc_chunked = None
                        for _lbl9, _pth9, _res9 in (_multi_results or []):
                            if _res9.get("content_check"):
                                _cc_chunked = _res9["content_check"]
                                _cc_rep_n = _res9.get("content_repaired", 0)
                                break
                        else:
                            _cc_rep_n = 0
                        if _cc_chunked and _cc_chunked.get("ok"):
                            _ccw = _cc_chunked.get("warnings", 0)
                            _ccn = _cc_chunked.get("notices", 0)
                            _ccc = _cc_chunked.get("checked", 0)
                            _rep_note = f" · 🔧 {_cc_rep_n} Beitrag{'e' if _cc_rep_n != 1 else ''} automatisch repariert" if _cc_rep_n else ""
                            if _ccw == 0 and _ccn == 0:
                                st.success(f"🔍 Plausibilitäts-Check: alle {_ccc} Beiträge inhaltlich sauber gegen die Quellen geprüft.{_rep_note}")
                            elif _ccw == 0:
                                st.info(f"🔍 Plausibilitäts-Check: {_ccc} Beiträge geprüft · {_ccn} kleinere Hinweise.{_rep_note}")
                            elif _cc_rep_n:
                                st.info(f"🔍 Plausibilitäts-Check: {_ccc} Beiträge geprüft · {_ccw} Warnung{'en' if _ccw != 1 else ''} gefunden{_rep_note} — die Ausgaben tragen die korrigierte Fassung.")
                            else:
                                st.warning(f"🔍 Plausibilitäts-Check: {_ccc} Beiträge geprüft · {_ccw} Warnung{'en' if _ccw != 1 else ''} (Auto-Repair war aus oder erfolglos).")
                            _cc_findings = [it for it in (_cc_chunked.get("items") or []) if it.get("level") in ("warn", "notice")]
                            if _cc_findings:
                                with st.expander(f"🔎 Plausi-Befunde: {len(_cc_findings)}", expanded=False):
                                    for _fit in _cc_findings:
                                        _fic = "⚠️" if _fit.get("level") == "warn" else "ℹ️"
                                        st.markdown(f"{_fic} **{_fit.get('label', 'Beitrag')}** — {_fit.get('summary', '')}")
                                        for _hi in (_fit.get("hard_issues") or []):
                                            st.caption(f"• {_hi}")
                        elif _cc_chunked:
                            st.warning(f"🔍 Plausibilitäts-Check fehlgeschlagen: {str(_cc_chunked.get('error', '?'))[:160]} — Briefing ist trotzdem fertig (ungeprüft).")
                    if _cli_content_check_enabled and _clean_sections_for_check and not _use_chunked:
                        st.markdown("---")
                        st.markdown("##### 🔍 Inhaltlicher Plausibilitäts-Check via Claude")
                        _cc_progress = st.progress(0)
                        _cc_status = st.empty()

                        def _on_cc_progress(step: str, ratio: float):
                            try:
                                _cc_progress.progress(max(0.0, min(1.0, float(ratio))))
                                _cc_status.caption(step)
                            except Exception:
                                pass

                        try:
                            _cc_result = run_content_check_via_claude_cli(
                                handoff_text=handoff_text,
                                briefing_sections=_clean_sections_for_check,
                                model=_cli_model,
                                progress_callback=_on_cc_progress,
                                cli_path=_cli_path,
                            )
                            _cc_progress.empty()
                            _cc_status.empty()

                            if _cc_result.get("ok"):
                                _accumulate_usage(_cc_result.get("usage") if isinstance(_cc_result, dict) else None)
                                _cc_warn = _cc_result.get("warnings", 0)
                                _cc_notice = _cc_result.get("notices", 0)
                                _cc_ok = _cc_result.get("ok_count", 0)
                                _cc_checked = _cc_result.get("checked", 0)
                                _cc_elapsed = _cc_result.get("elapsed_seconds") or 0
                                if _cc_warn == 0 and _cc_notice == 0:
                                    st.success(
                                        f"✅ Plausibilitäts-Check: alle {_cc_checked} Beiträge "
                                        f"inhaltlich plausibel ({_cc_elapsed:.0f}s, 0,00 €)"
                                    )
                                else:
                                    _cc_msg = (
                                        f"🔍 Plausibilitäts-Check: {_cc_ok}/{_cc_checked} ok"
                                        f"{f' · {_cc_warn} Warnung{chr(101) if _cc_warn != 1 else chr(0)}'.replace(chr(0), '') if _cc_warn else ''}"
                                        f"{f' · {_cc_notice} Hinweis{chr(101) if _cc_notice != 1 else chr(0)}'.replace(chr(0), '') if _cc_notice else ''}"
                                        f" ({_cc_elapsed:.0f}s, 0,00 €)"
                                    )
                                    if _cc_warn > 0:
                                        st.warning(_cc_msg)
                                    else:
                                        st.info(_cc_msg)

                                # Details
                                _cc_items = [it for it in (_cc_result.get("items") or []) if it.get("level") in ("warn", "notice")]
                                if _cc_items:
                                    with st.expander(
                                        f"🔎 Details: {len(_cc_items)} Befund{'e' if len(_cc_items) != 1 else ''}",
                                        expanded=_cc_warn > 0,
                                    ):
                                        for item in _cc_items:
                                            level = item.get("level", "ok")
                                            icon = "⚠️" if level == "warn" else "ℹ️"
                                            idx = item.get("section_index")
                                            idx_str = f"#{idx} · " if idx else ""
                                            label = item.get("label", "Beitrag")
                                            summary = item.get("summary", "")
                                            st.markdown(f"{icon} **{idx_str}{label}**")
                                            if summary:
                                                st.caption(summary)
                                            for issue in item.get("hard_issues", []) or []:
                                                st.markdown(f"   - 🔴 {issue}")
                                            for issue in item.get("soft_issues", []) or []:
                                                st.markdown(f"   - 🟡 {issue}")
                            else:
                                st.warning(f"Plausibilitäts-Check fehlgeschlagen: {_cc_result.get('error')}")
                                if _cc_result.get("raw_response"):
                                    with st.expander("🔍 Roh-Antwort (Debug)", expanded=False):
                                        st.code(_cc_result["raw_response"][:6000], language="markdown")
                        except Exception as exc:
                            try:
                                _cc_progress.empty()
                                _cc_status.empty()
                            except Exception:
                                pass
                            st.error(f"Unerwarteter Fehler beim Plausibilitäts-Check: {exc}")

                    # === Auto-Repair (wenn Lint- oder Content-Warnungen + Checkbox an) ===
                    _repair_lint_warns = (_output_lint or {}).get("warnings", 0) if _output_lint else 0
                    try:
                        _cc_for_repair = _cc_result if (_cc_result and _cc_result.get("ok")) else None
                    except NameError:
                        _cc_for_repair = None
                    _repair_content_warns = (_cc_for_repair or {}).get("warnings", 0) if _cc_for_repair else 0
                    _total_warns = _repair_lint_warns + _repair_content_warns

                    # _clean_sections wird auch für die nachfolgende Kompaktfassung benötigt
                    _clean_sections = _clean_sections_for_check

                    if _cli_auto_repair_enabled and _total_warns > 0 and _clean_sections_for_check and not _use_chunked:
                        st.markdown("---")
                        st.markdown(f"##### 🔧 Auto-Repair via Claude ({_total_warns} Warnung{'en' if _total_warns != 1 else ''})")
                        _rep_progress = st.progress(0)
                        _rep_status = st.empty()

                        def _on_rep_progress(step: str, ratio: float):
                            try:
                                _rep_progress.progress(max(0.0, min(1.0, float(ratio))))
                                _rep_status.caption(step)
                            except Exception:
                                pass

                        try:
                            _rep_result = run_briefing_repair_via_claude_cli(
                                handoff_text=handoff_text,
                                briefing_sections=_clean_sections_for_check,
                                output_lint=_output_lint,
                                content_check=_cc_for_repair,
                                model=_cli_model,
                                progress_callback=_on_rep_progress,
                                cli_path=_cli_path,
                            )
                            _rep_progress.empty()
                            _rep_status.empty()

                            if _rep_result.get("ok"):
                                _accumulate_usage(_rep_result.get("usage"))
                            if _rep_result.get("ok") and _rep_result.get("repaired_count", 0) > 0:
                                _new_sections = _rep_result.get("sections") or _clean_sections_for_check
                                _rep_elapsed = _rep_result.get("elapsed_seconds") or 0
                                # PDF neu bauen mit reparierten Sections
                                try:
                                    # Annotate Progress-Markers (klassischer Modus)
                                    if not _cli_narrative_selected:
                                        try:
                                            _annotate_section_progress_markers(_new_sections)
                                        except Exception:
                                            pass
                                    create_pdf(
                                        _new_sections,
                                        str(out_path),
                                        datetime.datetime.now(),
                                        document_title="Audio-Briefing",
                                    )
                                    # Eleven-TXT auch neu schreiben (für Wochen-Meta)
                                    try:
                                        _texte_dir = out_path.parent / "Texte"
                                        _texte_dir.mkdir(parents=True, exist_ok=True)
                                        _eleven_text = create_eleven_reader_text(_new_sections, datetime.datetime.now())
                                        _txt_path = _texte_dir / f"{out_path.stem}_eleven-reader.txt"
                                        _txt_path.write_text(_eleven_text, encoding="utf-8")
                                        # WICHTIG: reparierte Fassung auch in den Meta-Spiegel,
                                        # sonst liest das Wochen-Meta die alte/unreparierte Version.
                                        _mirror_txt_to_local(_txt_path.name, _eleven_text)
                                    except Exception:
                                        pass

                                    st.success(
                                        f"✅ Auto-Repair: {_rep_result['repaired_count']} Beitrag/Beiträge repariert "
                                        f"in {_rep_elapsed:.0f}s — PDF neu gebaut (0,00 €)"
                                    )
                                    _idx_str = ", ".join(f"#{i}" for i in (_rep_result.get('repaired_indices') or []))
                                    if _idx_str:
                                        st.caption(f"Reparierte Beiträge: {_idx_str}")
                                    with open(out_path, "rb") as fp:
                                        st.download_button(
                                            label="📥 Repariertes Voll-PDF herunterladen",
                                            data=fp.read(),
                                            file_name=out_path.name,
                                            mime="application/pdf",
                                            use_container_width=True,
                                            on_click="ignore",
                                            key=f"claude_cli_pdf_repaired_{_ts}",
                                        )
                                    # Aktualisierte Sections für die Kompaktfassung nutzen
                                    _clean_sections = _new_sections
                                except Exception as exc:
                                    st.warning(f"Reparatur-Beiträge empfangen, aber PDF-Neubau fehlgeschlagen: {exc}")
                            elif _rep_result.get("ok") and _rep_result.get("repaired_count", 0) == 0:
                                _reason = _rep_result.get("skipped_reason") or "keine Änderungen"
                                st.info(f"ℹ️ Auto-Repair übersprungen — {_reason}.")
                            else:
                                st.warning(f"Auto-Repair fehlgeschlagen: {_rep_result.get('error')}")
                                if _rep_result.get("raw_response"):
                                    with st.expander("🔧 Roh-Antwort (Debug)", expanded=False):
                                        st.code(_rep_result["raw_response"][:6000], language="markdown")
                        except Exception as exc:
                            try:
                                _rep_progress.empty()
                                _rep_status.empty()
                            except Exception:
                                pass
                            st.error(f"Unerwarteter Fehler beim Auto-Repair: {exc}")

                    # === Erzählversion zusätzlich (kompletter zweiter Voll-Lauf) ===
                    if _cli_narrative_extra and not _cli_narrative_selected:
                        st.markdown("---")
                        st.markdown("##### 🎙️ Erzählversion wird erstellt (zweiter Voll-Lauf, Podcast-Stil)")
                        _narr_progress = st.progress(0)
                        _narr_status = st.empty()

                        def _on_narr_progress(step: str, ratio: float):
                            try:
                                _narr_progress.progress(max(0.0, min(1.0, float(ratio))))
                                _narr_status.caption(step)
                            except Exception:
                                pass

                        try:
                            # Neuer Handoff-Text mit narrative_mode=True (holt Artikel ein zweites Mal —
                            # ist HTTP-Scraping, kostet nichts, ~30s extra)
                            _on_narr_progress("Rohdaten werden für Erzählversion neu verpackt…", 0.02)
                            narr_handoff = build_claude_handoff_package(
                                urls_text=urls_text,
                                paywall_text=paywall_text,
                                podcast_text=podcast_text,
                                include_weather=include_weather,
                                compact_mode=st.session_state.get("compact_mode", True),
                                narrative_mode=True,
                            )
                            _narr_pdf_path = archive_dir / f"{_ts}_briefing_claude_erzaehl.pdf"
                            _narr_result = run_briefing_via_claude_cli(
                                handoff_text=narr_handoff,
                                output_pdf_path=str(_narr_pdf_path),
                                model=_cli_model,
                                progress_callback=_on_narr_progress,
                                cli_path=_cli_path,
                            )
                            _narr_progress.empty()
                            _narr_status.empty()

                            if _narr_result.get("ok"):
                                _accumulate_usage(_narr_result.get("usage"))
                                _narr_elapsed = _narr_result.get("elapsed_seconds") or 0
                                st.success(
                                    f"✅ Erzählversion fertig in {_narr_elapsed:.0f}s — "
                                    f"{_narr_result.get('sections_count', '?')} Beiträge → "
                                    f"`{_narr_pdf_path.name}` (0,00 €)"
                                )
                                try:
                                    with open(_narr_pdf_path, "rb") as fp:
                                        st.download_button(
                                            label="📥 Erzählversion-PDF herunterladen",
                                            data=fp.read(),
                                            file_name=_narr_pdf_path.name,
                                            mime="application/pdf",
                                            use_container_width=True,
                                            on_click="ignore",
                                            key=f"claude_cli_narr_pdf_{_ts}",
                                        )
                                except Exception as exc:
                                    st.warning(f"Erzähl-PDF gespeichert, Download-Fehler: {exc}")
                                _narr_arts = _narr_result.get("artifacts") or {}
                                _narr_eleven = _narr_arts.get("eleven_txt")
                                if _narr_eleven:
                                    st.caption(f"📝 Erzähl-Eleven-TXT: `{Path(_narr_eleven).name}`")
                            else:
                                st.warning(f"Erzählversion fehlgeschlagen: {_narr_result.get('error')}")
                        except Exception as exc:
                            try:
                                _narr_progress.empty()
                                _narr_status.empty()
                            except Exception:
                                pass
                            st.error(f"Unerwarteter Fehler bei Erzählversion: {exc}")
                    elif _cli_narrative_extra and _cli_narrative_selected:
                        st.caption("ℹ️ Erzählversion wurde bereits als Hauptlauf gewählt — kein zweiter Lauf nötig.")

                    # === Kompaktfassung (zweiter CLI-Call) — wenn aktiviert ===
                    if _cli_genius_enabled and _clean_sections:
                        st.markdown("---")
                        st.markdown(f"##### ✨ Kompaktfassung wird erstellt ({_cli_genius_mode})")
                        _genius_progress = st.progress(0)
                        _genius_status = st.empty()

                        def _on_genius_progress(step: str, ratio: float):
                            try:
                                _genius_progress.progress(max(0.0, min(1.0, float(ratio))))
                                _genius_status.caption(step)
                            except Exception:
                                pass

                        _genius_suffix_map = {"long": "_lang", "short": "_kurz", "standard": "_standard"}
                        _genius_label_map = {"long": "Lang", "short": "Kurz", "standard": "Standard"}
                        _kompakt_dir = archive_dir / "Kompaktfassungen"
                        _kompakt_dir.mkdir(parents=True, exist_ok=True)
                        # "Beide" erzeugt BEIDE Varianten (Lang + Kurz), sonst die gewählte.
                        _genius_run_modes = ["long", "short"] if _cli_genius_mode == "Beide" else [_cli_genius_internal_mode]
                        for _gm in _genius_run_modes:
                            _genius_suffix = _genius_suffix_map.get(_gm, "_lang")
                            _gm_label = _genius_label_map.get(_gm, _cli_genius_mode)
                            _genius_pdf_path = _kompakt_dir / f"{_ts}_tagesbriefing_kompakt{_genius_suffix}.pdf"
                            try:
                                _genius_result = run_genius_summary_via_claude_cli(
                                    briefing_sections=_clean_sections,
                                    output_pdf_path=str(_genius_pdf_path),
                                    mode=_gm,
                                    model=_cli_genius_model,
                                    progress_callback=_on_genius_progress,
                                    cli_path=_cli_path,
                                )
                                _genius_progress.empty()
                                _genius_status.empty()

                                if _genius_result["ok"]:
                                    _accumulate_usage(_genius_result.get("usage"))
                                    _g_meta = _genius_result.get("meta", {}) or {}
                                    _g_elapsed = _genius_result.get("elapsed_seconds") or 0
                                    _badges = []
                                    if _g_meta.get("used_fallback"):
                                        _badges.append("⚠️ Sicherheitsnetz aktiv")
                                    elif _g_meta.get("soft_intelligent"):
                                        _badges.append("🧠 Intelligente Opus-Fassung (Fließtext, ohne strenge Nummerierung)")
                                    else:
                                        _badges.append(f"✨ {_g_meta.get('mode_label', _gm_label)}-Variante")
                                    _badges.append(f"{_g_meta.get('total', '?')} Beiträge")
                                    _q_issues = _g_meta.get("quality_issues") or []
                                    _q_applied = _g_meta.get("quality_repair_applied", False)
                                    if _q_applied:
                                        _badges.append(f"🔧 {len(_q_issues)} Quality-Issue{'s' if len(_q_issues) != 1 else ''} repariert")
                                    elif _q_issues:
                                        _badges.append(f"⚠️ {len(_q_issues)} Quality-Issue{'s' if len(_q_issues) != 1 else ''} (nicht repariert)")
                                    elif not _g_meta.get("used_fallback") and not _g_meta.get("soft_intelligent"):
                                        _badges.append("✅ Quality-Check sauber")
                                    if _g_meta.get("hybrid_filled"):
                                        _badges.append(f"🩹 {_g_meta['hybrid_filled']} fehlende Einträge automatisch ergänzt")

                                    st.success(
                                        f"✅ Kompaktfassung fertig in {_g_elapsed:.0f}s — `{_genius_pdf_path.name}` (0,00 €)"
                                    )
                                    st.caption(" · ".join(_badges))
                                    if _q_issues:
                                        with st.expander(
                                            f"📋 Quality-Check Details: {len(_q_issues)} Befund{'e' if len(_q_issues) != 1 else ''}",
                                            expanded=False,
                                        ):
                                            for issue in _q_issues:
                                                cat = issue.get("category", "?")
                                                msg = issue.get("message", "")
                                                b = issue.get("beitrag", "?")
                                                st.markdown(f"- **{cat}** [{b}]: {msg}")
                                                if issue.get("fix_hint"):
                                                    st.caption(f"  Hinweis: {issue['fix_hint']}")
                                    try:
                                        with open(_genius_pdf_path, "rb") as fp:
                                            st.download_button(
                                                label=f"📥 Kompaktfassung-PDF ({_gm_label}) herunterladen",
                                                data=fp.read(),
                                                file_name=_genius_pdf_path.name,
                                                mime="application/pdf",
                                                use_container_width=True,
                                                on_click="ignore",
                                                key=f"claude_cli_genius_pdf_main_{_ts}_{_gm}",
                                            )
                                    except Exception as exc:
                                        st.warning(f"Kompakt-PDF gespeichert, Download-Button-Fehler: {exc}")
                                    st.caption(f"📂 Abgelegt in `{_kompakt_dir}`")
                                else:
                                    st.error(f"Kompaktfassung fehlgeschlagen: {_genius_result['error']}")
                                    if _genius_result.get("raw_response"):
                                        with st.expander("🔍 Roh-Antwort der Kompaktfassung (Debug)"):
                                            st.code(_genius_result["raw_response"][:8000], language="markdown")
                            except Exception as exc:
                                _genius_progress.empty()
                                _genius_status.empty()
                                st.error(f"Unerwarteter Fehler bei Kompaktfassung: {exc}")

                    # === Gesamt-Token-Verbrauch über alle Calls dieses Laufs ===
                    _t = _cli_token_accumulator
                    if _t.get("calls", 0) >= 1:
                        _total_in = _t["input"] + _t["cache_read"] + _t["cache_create"]
                        _total_out = _t["output"]
                        st.markdown("---")
                        st.info(
                            f"📊 **Gesamt-Verbrauch dieses Laufs (Max-Abo):** "
                            f"{_t['calls']} Call{'s' if _t['calls'] != 1 else ''} · "
                            f"{_total_in:,} Input + {_total_out:,} Output Token "
                            f"≈ {(_total_in + _total_out) / 1000:.0f}k Token gesamt"
                            .replace(",", ".")
                        )
                        if _t.get("cache_read", 0) > 0:
                            st.caption(
                                f"Davon {_t['cache_read']:,} Cache-gelesen (günstiger). ".replace(",", ".") +
                                "Bei deinem Max-Abo ist der Verbrauch ein Anteil deines 5h-Limits — keine direkten Geldkosten."
                            )
                        else:
                            st.caption("Bei deinem Max-Abo ist der Verbrauch ein Anteil deines 5h-Limits — keine direkten Geldkosten.")
                else:
                    st.error(f"Claude-CLI-Pfad fehlgeschlagen: {cli_result['error']}")
                    if cli_result.get("raw_response"):
                        with st.expander("🔍 Roh-Antwort von Claude (Debug)"):
                            st.code(cli_result["raw_response"][:8000], language="markdown")
                    st.caption(
                        "Tipp: Bei Limit oder Parsing-Fehler kannst du den API-Pfad weiter unten nutzen — "
                        "oder den manuellen Web-Chat-Pfad im Erweitert-Bereich."
                    )
            except Exception as exc:
                _cli_progress.empty()
                _cli_status.empty()
                st.error(f"Unerwarteter Fehler: {exc}")

# ============================================================
# Hinweis auf abgelehnte URLs (Statistik-Pills stehen in der Übersicht oben)
# ============================================================
if url_preview["rejected"]:
    rejected_list = ", ".join(r["url"] if isinstance(r, dict) else str(r) for r in url_preview["rejected"])
    st.caption(f"⚠️ {len(url_preview['rejected'])} Link{'s' if len(url_preview['rejected']) != 1 else ''} sehen nicht wie Artikel-/Eventseiten aus und werden übersprungen: {rejected_list}")



st.markdown("---")
st.markdown('<div id="nav-meta" style="position:relative; top:-64px;"></div>', unsafe_allow_html=True)
with st.expander("🗓️ Wochen-Meta-Briefing", expanded=False):
    st.caption("Liest die archivierten Tagesbriefings und baut daraus Wochenrückblick, Metaebene und Coach-Blick für die nächste Woche.")
    # Eigener API-Key-Bezug (der API-Block steht weiter unten): nur für den Backup-Knopf.
    _meta_api_key = os.getenv("OPENAI_API_KEY") or ""
    _meta_cli_path = _locate_claude_cli()
    _meta_cli_available = bool(_meta_cli_path)

    meta_col1, meta_col2, meta_col3 = st.columns([2, 1, 1])
    with meta_col1:
        _last_meta = st.session_state.get("last_meta_created_iso") or ""
        try:
            _meta_age_d = (datetime.datetime.now() - datetime.datetime.fromisoformat(_last_meta)).days if _last_meta else None
        except Exception:
            _meta_age_d = None
        if _meta_age_d is not None and _meta_age_d >= 7:
            st.info(f"⏰ Dein letztes Wochen-Briefing ist {_meta_age_d} Tage her — Zeit für ein neues?")
        meta_button_cli = st.button(
            "🤖 Wochen-Meta + Coach via Claude",
            key="meta_briefing_cli_button",
            use_container_width=True,
            type="primary",
            disabled=not _meta_cli_available,
            help="Nutzt dein Claude Max-Abo via CLI. Kostet nichts, ~3–5 Min Wartezeit.",
        )
    with meta_col2:
        meta_cli_model = st.selectbox(
            "Modell",
            options=["opus", "sonnet", "haiku"],
            index=0,
            key="meta_cli_model",
            help="Opus 4.8 ist hier Standard: Das Wochen-Meta ist reine Synthese/Analyse (Muster, Querverbindungen, Coach-Blick) — genau Opus' Stärke. Nur ein Aufruf, also kein Tempo-Nachteil. Alles gratis übers Max-Abo.",
            disabled=not _meta_cli_available,
        )
    with meta_col3:
        meta_days = st.number_input("Tage", min_value=2, max_value=21, value=7, key="meta_days", label_visibility="collapsed")
        st.caption("Zeitraum")

    _meta_archive_dir = _resolve_archive_dir()  # für Output-PDF (iCloud, Handy-Sync)
    # WICHTIG: Discovery liest aus dem lokalen Spiegel, nicht aus iCloud — der
    # launchd-Hintergrunddienst darf iCloud nicht auflisten (glob = 0 Treffer).
    try:
        _meta_available_briefings = _discover_weekly_briefing_texts(str(_LOCAL_META_MIRROR_DIR), days=int(meta_days))
    except Exception as _meta_scan_exc:
        _meta_available_briefings = []
        st.warning(f"Archiv-Scan fehlgeschlagen: {_meta_scan_exc}")
    _meta_dates = sorted({entry.get("date", "") for entry in _meta_available_briefings if entry.get("date")})
    if _meta_available_briefings:
        st.caption(
            f"Gefunden: {len(_meta_available_briefings)} Tagesbriefing-TXT(s) aus {len(_meta_dates)} Tag(en) "
            f"im lokalen Spiegel (iCloud-Synchronisation bleibt unberührt)."
        )
    else:
        _txt_dir = _LOCAL_META_MIRROR_DIR / _TXT_ARCHIVE_SUBDIR
        st.warning(f"Keine Tagesbriefing-TXTs im Zeitraum gefunden. Gesucht in `{_txt_dir}`.")

    if not _meta_cli_available:
        st.caption("⚠️ Claude CLI nicht gefunden. Nutze den Backup-API-Block in diesem Abschnitt.")

    # API-Backup kompakt darunter
    meta_button = False
    with st.expander("💸 Backup: via OpenAI/Anthropic-API (ca. USD 0.20-0.50)", expanded=False):
        meta_api_model = st.selectbox(
            "API-Modell",
            options=["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.2", "gpt-4.1-mini"],
            index=2,
            key="meta_api_model",
            help="Für Wochen-Meta: gpt-5.4-mini ist der Standard. gpt-5.5/gpt-5.4 sind stärker, aber teurer.",
        )
        meta_button = st.button(
            "Wochen-Meta + Coach via API erstellen",
            key="meta_briefing_button",
            use_container_width=True,
            disabled=not _meta_api_key or not _meta_available_briefings,
            help="Nur als Backup, falls der Claude-CLI-Pfad nicht verfügbar ist.",
        )

    if meta_button_cli:
        if not _meta_available_briefings:
            st.warning(f"Keine Tagesbriefing-Texte der letzten {int(meta_days)} Tage gefunden. Erstelle erst Tagesbriefings — sie landen automatisch im lokalen Spiegel, aus dem das Wochen-Meta liest.")
            st.stop()
        meta_progress = st.progress(0)
        meta_status = st.empty()

        def _meta_progress(step, progress):
            meta_progress.progress(max(0.0, min(progress, 1.0)))
            meta_status.caption(step)

        try:
            result = run_meta_briefing_via_claude_cli(
                archive_dir=str(_meta_archive_dir),
                days=int(meta_days),
                model=meta_cli_model,
                progress_callback=_meta_progress,
                cli_path=_meta_cli_path,
            )
        except Exception as exc:
            result = {"ok": False, "error": f"Unerwarteter Fehler: {exc}"}
        meta_progress.empty()
        meta_status.empty()
        if result is None:
            st.warning(f"Keine Briefing-Texte der letzten {int(meta_days)} Tage im Archiv gefunden.")
        elif result.get("ok") is False:
            st.error(f"Wochen-Briefing via Claude fehlgeschlagen: {result.get('error')}")
            st.caption("Tipp: den Backup-API-Block in diesem Abschnitt nutzen (ca. USD 0.20-0.50).")
        else:
            _g_elapsed = result.get("elapsed_seconds") or 0
            st.success(f"✅ Wochen-Briefing fertig in {_g_elapsed:.0f}s — 0,00 € Kosten")
            st.session_state["meta_briefing_result"] = result
            st.session_state["last_meta_created_iso"] = datetime.datetime.now().isoformat()
            _save_draft()
            if st.session_state.get("auto_reader_upload", True) and result.get("txt_path"):
                try:
                    from reader_upload import upload_briefing_txt as _up_meta
                    _mt_title = f"Wochenbriefing bis {datetime.datetime.now().strftime('%d.%m.')}"
                    with st.spinner(f"🎧 Sende an ElevenReader: {_mt_title}…"):
                        _mur = _up_meta(result["txt_path"], _mt_title)
                    if _mur.get("ok"):
                        st.success(f"🎧 {_mt_title} liegt in deiner ElevenReader-Bibliothek.")
                    else:
                        st.warning(f"🎧 Meta-Upload: {_mur.get('error')}")
                except Exception as _mex:
                    st.warning(f"🎧 Meta-Upload übersprungen: {_mex}")

    if meta_button:
        if not _meta_available_briefings:
            st.warning(f"Keine Tagesbriefing-Texte der letzten {int(meta_days)} Tage gefunden. Erstelle erst Tagesbriefings — sie landen automatisch im lokalen Spiegel, aus dem das Wochen-Meta liest.")
            st.stop()
        meta_progress = st.progress(0)
        meta_status = st.empty()

        def _meta_progress(step, progress):
            meta_progress.progress(max(0.0, min(progress, 1.0)))
            meta_status.caption(step)

        result = generate_meta_briefing(
            archive_dir=str(_meta_archive_dir),
            api_key=_meta_api_key,
            model=meta_api_model,
            days=int(meta_days),
            progress_callback=_meta_progress,
        )
        meta_progress.empty()
        meta_status.empty()
        # Persistent im Session State speichern, damit Refresh nicht löscht
        st.session_state["meta_briefing_result"] = result

    # Anzeige des Wochen-Briefings (auch nach Refresh)
    _meta_result = st.session_state.get("meta_briefing_result")
    if _meta_result:
        stats = _meta_result.get("stats", {})
        quality = _meta_result.get("quality") or {}
        quality_present = bool(_meta_result.get("quality"))
        quality_warnings = quality.get("warnings") or []
        quality_notices = quality.get("notices") or []
        word_count = quality.get("word_count")
        if not quality_present:
            st.markdown(
                """
                <div class="briefing-status-banner warn">
                  <h3>Wochen-Meta bereit</h3>
                  <p>Dieses Ergebnis stammt noch ohne neue Meta-Qualitätsprüfung. Bei einem neuen Lauf wird der Coach-Aufbau automatisch geprüft.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        elif quality_warnings:
            st.markdown(
                f"""
                <div class="briefing-status-banner danger">
                  <h3>Wochen-Meta erstellt, aber prüfen</h3>
                  <p>{len(quality_warnings)} Qualitätswarnung(en). Öffne die Details, bevor du das PDF nutzt.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        elif quality_notices:
            st.markdown(
                f"""
                <div class="briefing-status-banner warn">
                  <h3>Wochen-Meta mit Hinweisen bereit</h3>
                  <p>{len(quality_notices)} kleinere Hinweise. Inhalt und Coach-Teil wurden erzeugt.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                """
                <div class="briefing-status-banner">
                  <h3>Wochen-Meta + Coach bereit</h3>
                  <p>Struktur, Abschluss und Grundqualität sehen sauber aus.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        dates_text = ", ".join(_meta_result.get("dates", []))
        word_text = f" · {word_count} Wörter" if word_count else ""
        st.success(f"Aus {stats.get('briefings', '?')} Briefings über {stats.get('days', '?')} Tage erstellt ({dates_text}){word_text}.")
        if stats.get("truncated_briefings"):
            st.caption(f"Input-Hinweis: {stats['truncated_briefings']} sehr lange Tagesbriefing(s) wurden für den Modellkontext gekürzt.")
        if quality_warnings or quality_notices:
            with st.expander("Wochen-Meta-Qualitätsdetails"):
                for item in quality_warnings:
                    st.markdown(f"- Warnung: {item}")
                for item in quality_notices:
                    st.markdown(f"- Hinweis: {item}")
                source_files = stats.get("source_files") or []
                if source_files:
                    st.caption("Genutzte Quellen: " + ", ".join(source_files[:12]) + (" ..." if len(source_files) > 12 else ""))
        if _meta_result.get("pdf_path"):
            st.caption(f"💾 Gespeichert: `{Path(_meta_result['pdf_path']).name}`")
        with st.expander("📖 Wochen-Briefing-Text anzeigen", expanded=False):
            st.markdown(_meta_result["text"])
        dl_col1, dl_col2, clear_col = st.columns([1, 1, 1])
        if _meta_result.get("pdf"):
            with dl_col1:
                week_stamp = datetime.datetime.now().strftime("%Y-KW%V")
                st.download_button(
                    "📄 PDF",
                    data=_meta_result["pdf"],
                    file_name=f"{week_stamp}_wochenbriefing.pdf",
                    mime="application/pdf",
                    key="meta_pdf_download",
                    use_container_width=True,
                    on_click="ignore",
                )
        if _meta_result.get("txt"):
            with dl_col2:
                week_stamp = datetime.datetime.now().strftime("%Y-KW%V")
                st.download_button(
                    "📝 TXT",
                    data=_meta_result["txt"],
                    file_name=f"{week_stamp}_wochenbriefing.txt",
                    mime="text/plain",
                    key="meta_txt_download",
                    use_container_width=True,
                    on_click="ignore",
                )
        with clear_col:
            if st.button("🗑️ Verwerfen", use_container_width=True, key="meta_clear_button"):
                st.session_state.pop("meta_briefing_result", None)
                st.rerun()
    elif meta_button or meta_button_cli:
        st.warning(f"Keine Briefing-Texte der letzten {int(meta_days)} Tage im Archiv gefunden. Erstelle zunächst ein Tagesbriefing, damit TXT-Dateien gespeichert werden.")

# ============================================================
# 💳 API-Version (kostenpflichtig) — ausklappbare Option UNTER dem Standard
# ============================================================
st.markdown("---")
st.markdown("#### 🧰 Weitere Wege & Werkzeuge")
st.caption("Alles hier unten ist **optional und eingeklappt** — der Standard ist der kostenlose Claude-Weg oben. Aufklappen nur bei Bedarf.")

with st.expander("💸 API-Version (kostenpflichtig · OpenAI/Anthropic) — nur falls der Claude-Weg mal klemmt", expanded=False):
    st.caption("Stabile, kostenpflichtige Alternative. Der **Standard ist Claude oben (kostenlos)**.")

    # === API-Setup: Provider | Key | Guthaben ===
    _env_oai = os.environ.get("OPENAI_API_KEY", "")
    _env_ant = os.environ.get("ANTHROPIC_API_KEY", "")

    _setup_col1, _setup_col2, _setup_col3 = st.columns([2, 2, 3])
    with _setup_col1:
        provider = st.radio(
            "API-Provider",
            ["Anthropic (Claude)", "OpenAI (GPT)"],
            key="provider",
            horizontal=True,
        )
        is_openai = provider.startswith("OpenAI")

    with _setup_col2:
        if is_openai:
            env_key = _env_oai
            key_placeholder = "sk-..."
            key_label = "OpenAI API-Key"
        else:
            env_key = _env_ant
            key_placeholder = "sk-ant-..."
            key_label = "Anthropic API-Key"
        if env_key:
            api_key = env_key
            st.markdown("**API-Key**")
            st.success("✅ Aus Umgebung geladen")
        else:
            api_key = st.text_input(
                key_label,
                type="password",
                placeholder=key_placeholder,
                help="Wird nur für diese Sitzung gespeichert.",
            )

    api_keys = {
        "openai": api_key if is_openai else _env_oai,
        "anthropic": api_key if not is_openai else _env_ant,
    }

    with _setup_col3:
        _oai_rest = st.session_state.get(_balance_remaining_key("openai"))
        _ant_rest = st.session_state.get(_balance_remaining_key("anthropic"))
        _balance_parts = []
        if _oai_rest is not None:
            _balance_parts.append(f"OpenAI ${_oai_rest:.2f}")
        if _ant_rest is not None:
            _balance_parts.append(f"Anthropic ${_ant_rest:.2f}")
        _balance_summary = " · ".join(_balance_parts) if _balance_parts else "Noch kein Guthaben gesetzt"
        st.caption(f"💰 Guthaben: {_balance_summary}")
        with st.container(border=True):
            _render_balance_panel("openai", "OpenAI")
            st.divider()
            _render_balance_panel("anthropic", "Anthropic")
            st.caption("Externe API-Nutzung außerhalb der App kann den Lernfaktor beeinflussen.")

    # Pre-Flight-Warnung bei niedrigem Guthaben
    _prov_key = "openai" if is_openai else "anthropic"
    _remaining = st.session_state.get(_balance_remaining_key(_prov_key))
    if _remaining is not None and _remaining < 1.00:
        _other_prov = "anthropic" if is_openai else "openai"
        _other_remaining = st.session_state.get(_balance_remaining_key(_other_prov))
        _other_key = api_keys.get(_other_prov, "")
        msg = f"⚠️ **Guthaben niedrig**: {provider} hat geschätzt nur noch USD {_remaining:.2f}. Briefings könnten mittendrin abbrechen."
        if _other_key and _other_remaining is not None and _other_remaining >= 1.00:
            msg += f" — Bei Bedarf wechselt der Watchdog auf den anderen Provider (Restguthaben dort: USD {_other_remaining:.2f})."
        else:
            msg += " — Aufladen vor dem nächsten Lauf empfohlen."
        st.warning(msg)

    st.markdown("---")
    # === Modell + Wetter ===
    _opt_col1, _opt_col2 = st.columns([2, 1])
    with _opt_col1:
        if is_openai:
            _api_models = [
                "gpt-5.5",
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-5.4-nano",
                "gpt-5.2",
                "gpt-4.1-mini",
            ]
            if st.session_state.get("model") not in _api_models:
                st.session_state["model"] = "gpt-5.4-mini"
            model = st.selectbox(
                "Modell",
                _api_models,
                index=_api_models.index(st.session_state.get("model", "gpt-5.4-mini")),
                key="model",
                help="GPT-5.4-mini: empfohlener Standard für tägliche Briefings. GPT-5.5/GPT-5.4: stärker, aber teurer. GPT-5.4-nano: sehr günstig, eher für einfache Checks.",
            )
        else:
            _api_models = ["claude-sonnet-5", "claude-haiku-4-5-20251001"]
            if st.session_state.get("model") not in _api_models:
                st.session_state["model"] = "claude-sonnet-5"
            model = st.selectbox(
                "Modell",
                _api_models,
                key="model",
                help="claude-sonnet-5 = beste Qualität (bis 31.08.2026 sogar günstiger dank Einführungspreis). claude-haiku-4-5 = schneller und günstiger.",
            )
    with _opt_col2:
        include_weather = True
        st.session_state["include_weather"] = True
        st.caption("Wetterbericht ist immer eingeschlossen.")


    st.markdown("**Briefing über die API erzeugen**")
    start_button = st.button(
        "Briefing via API erstellen",
        type="primary",
        use_container_width=True,
        disabled=not api_key,
        key="api_start_button",
    )
    if not api_key:
        st.caption("⚠️ API-Key fehlt")
    if start_button:
        if not url_preview["urls"] and not paywall_text.strip() and not podcast_text.strip() and not include_weather:
            st.warning("Mindestens ein Feld ausfüllen oder Wetterbericht aktivieren.")
        else:
            _run_briefing_generation()

# API-Ergebnis + Downloads IMMER sichtbar zeigen (nicht im zugeklappten Expander
# verstecken), damit man nach einem API-Lauf direkt drankommt.
if st.session_state.briefing_exports:
    st.markdown("#### 💸 Letztes API-Briefing")
    render_exports(st.session_state.briefing_exports, st.session_state.briefing_check)


# ============================================================
# 🌐 Backup: Manueller Claude-Web-Chat-Pfad (auch kostenlos)
# ============================================================

st.markdown("---")
with st.expander("🌐 Backup: Manueller Claude-Web-Chat-Pfad (auch kostenlos)", expanded=False):

    st.caption(
        "**Wann brauchst du das?** Nur wenn der Auto-Pfad oben ausfällt "
        "(z.B. Max-Abo-Limit erreicht oder Claude-CLI nicht verfügbar). "
        "Sonst ignorieren — der Auto-Pfad oben macht dasselbe in einem Klick."
    )
    st.caption("Workflow: 1) Schritt 1 baut Text-Paket → 2) Du pastest in claude.ai → 3) Schritt 2 baut PDF aus der Antwort.")

    claude_style = st.radio(
        "Briefing-Stil für Claude",
        options=["Klassisch (strukturiert, mit Beitragszählern)", "Erzähl-Modus (Podcast-Format, fließend)"],
        key="claude_handoff_style",
        horizontal=True,
        help="Beide Stile sind möglich. Klassisch = wie das normale Briefing. Erzähl-Modus = fließender Podcast-Text mit hörbaren Beitrags-Markern.",
    )
    _narrative_mode_selected = claude_style.startswith("Erzähl")

    handoff_col1, handoff_col2 = st.columns(2)
    with handoff_col1:
        if st.button(
            "📤 Schritt 1: Rohdaten für Claude vorbereiten",
            key="claude_handoff_prepare_button",
            use_container_width=True,
            help="Holt alle Artikel + Wetter aus den Feldern oben und baut ein großes Text-Paket mit Anweisungen, das du in Claude einfügen kannst. Funktioniert in beiden Stilen (klassisch + Erzähl-Modus).",
        ):
            if not (urls_text.strip() or paywall_text.strip() or podcast_text.strip() or include_weather):
                st.warning("Mindestens ein Feld ausfüllen oder Wetter aktivieren.")
            else:
                with st.spinner("Hole Artikel und baue Übergabe-Paket..."):
                    try:
                        handoff_text = build_claude_handoff_package(
                            urls_text=urls_text,
                            paywall_text=paywall_text,
                            podcast_text=podcast_text,
                            include_weather=include_weather,
                            compact_mode=st.session_state.get("compact_mode", True),
                            narrative_mode=_narrative_mode_selected,
                        )
                        st.session_state["claude_handoff_text"] = handoff_text
                        _style_label = "Erzähl-Modus" if _narrative_mode_selected else "klassischer Modus"
                        st.success(f"Übergabe-Paket fertig ({len(handoff_text):,} Zeichen, {_style_label}). Unten herunterladen oder anzeigen.")
                    except Exception as exc:
                        st.error(f"Fehler beim Bauen des Übergabe-Pakets: {exc}")

    if st.session_state.get("claude_handoff_text"):
        handoff_text = st.session_state["claude_handoff_text"]
        # Filename stabil pro Briefing-Lauf (aus Session-State, nicht bei jedem Rerun neu)
        if "claude_handoff_filename" not in st.session_state or st.session_state.get(
            "claude_handoff_text_hash"
        ) != hash(handoff_text):
            _ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
            st.session_state["claude_handoff_filename"] = f"{_ts}_claude-handoff.txt"
            st.session_state["claude_handoff_text_hash"] = hash(handoff_text)
        _handoff_filename = st.session_state["claude_handoff_filename"]
        # KEIN Auto-Save mehr in ~/Downloads — der User kann die TXT bei Bedarf
        # manuell über den Download-Button speichern. Das vermeidet den Datei-Spam
        # bei jedem Streamlit-Rerun + bei jedem Auto-Pfad-Lauf.
        _saved_path = None

        dl_col, view_col, code_col = st.columns(3)
        with dl_col:
            st.download_button(
                label="💾 Download (.txt)",
                data=handoff_text.encode("utf-8"),
                file_name=_handoff_filename,
                mime="text/plain; charset=utf-8",
                use_container_width=True,
                on_click="ignore",
            )
        with view_col:
            with st.popover("👁️ Anzeigen", use_container_width=True):
                st.code(handoff_text, language="markdown")
        with code_col:
            with st.popover("🤖 In Claude Code öffnen", use_container_width=True):
                st.markdown("**Befehl für Claude Code (zum Kopieren):**")
                _style_for_command = "im Erzähl-Stil" if _narrative_mode_selected else "im klassischen Stil"
                _claude_code_path = str(_saved_path) if _saved_path else f"~/Downloads/{_handoff_filename}"
                _claude_code_command = (
                    f"Verarbeite die Handoff-Datei {_claude_code_path} {_style_for_command} und baue daraus die finale PDF. "
                    f"Folge dabei dem Standard-Workflow aus /Users/florian/Projects/apps/briefing-app/CLAUDE_BRIEFING_GUIDE.md "
                    f"und nutze die Vorlage /Users/florian/Projects/apps/briefing-app/templates/claude_briefing_template.py "
                    f"als Ausgangspunkt. Wichtig: 280-500 Wörter pro Beitrag (je nach Tiefe), Vollständigkeit ist Pflicht."
                )
                st.code(_claude_code_command, language="text")
                if _saved_path:
                    st.caption(f"✅ Datei automatisch gespeichert: `{_saved_path.name}` in ~/Downloads/")
                else:
                    st.caption("⚠️ Datei muss noch manuell heruntergeladen werden (siehe Button links).")
                st.markdown("---")
                st.markdown(
                    "**So geht's:**\n"
                    "1. Diesen Befehl markieren und kopieren (cmd+c)\n"
                    "2. Neuen Claude-Code-Chat öffnen (`claude` im Terminal oder über die App)\n"
                    "3. Befehl einfügen und Enter drücken\n"
                    "4. Claude Code lädt die Datei, verfasst alle Zusammenfassungen, baut die PDF und legt sie in den Briefings-Ordner\n"
                    "5. Du musst nichts mehr in Schritt 2 unten tun — die PDF ist direkt fertig"
                )

        if _saved_path:
            st.caption(f"📂 Übergabe-Datei liegt in `~/Downloads/{_handoff_filename}` — bereit für Claude Code.")
        st.caption("Alternativ: Diesen Text in einen Claude-Web-Chat (aktuelles Claude Sonnet) einfügen. Claude antwortet mit JSON, das du dann unten in Schritt 2 einfügst.")

    with handoff_col2:
        pass  # Button-Layout

    st.markdown("**Schritt 2: PDF aus Claude-Antwort bauen**")
    claude_response = st.text_area(
        "Claude-Antwort einfügen",
        placeholder='Hier den kompletten Output von Claude einfügen — auch mit Vorrede, der JSON-Block wird automatisch extrahiert. Beispiel:\n\nHier ist dein Briefing...\n```json\n{"sections": [...]}\n```',
        height=180,
        key="claude_response_input",
        label_visibility="collapsed",
    )

    if st.button(
        "📥 PDF aus Claude-Antwort bauen",
        key="claude_handoff_build_button",
        use_container_width=True,
        type="primary",
        disabled=not claude_response.strip(),
    ):
        with st.spinner("Baue PDF aus Claude-Output..."):
            _ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
            archive_dir = _resolve_archive_dir()
            archive_dir.mkdir(parents=True, exist_ok=True)
            # Erkennen ob narrative_mode im JSON ist (für Dateinamen)
            _is_narrative = '"narrative_mode": true' in claude_response or '"narrative_mode":true' in claude_response
            _suffix = "_claude_erzaehl" if _is_narrative else "_claude"
            out_path = archive_dir / f"{_ts}_briefing{_suffix}.pdf"
            # Handoff-Text aus Session State holen für Vollständigkeitsprüfung
            _handoff_for_check = st.session_state.get("claude_handoff_text")
            result = build_pdf_from_claude_json(
                claude_response,
                str(out_path),
                generated_at=datetime.datetime.now(),
                handoff_text=_handoff_for_check,
            )
            if result["ok"]:
                st.success(f"PDF gebaut: {result['sections_count']} Beiträge → `{out_path.name}`")
                with open(out_path, "rb") as f:
                    st.download_button(
                        label="📥 Claude-PDF herunterladen",
                        data=f.read(),
                        file_name=out_path.name,
                        mime="application/pdf",
                        use_container_width=True,
                        on_click="ignore",
                        key=f"download_claude_pdf_{_ts}",
                    )
                st.caption(f"Gespeichert in `{archive_dir}`")

                # Vollständigkeits-Report
                comp = result.get("completeness")
                if comp:
                    ratio = comp["found"] / max(comp["checked"], 1)
                    if comp["missing"]:
                        st.warning(f"⚠️ Vollständigkeit: {comp['found']}/{comp['checked']} Beiträge gefunden ({int(ratio*100)}%)")
                        with st.expander(f"❌ {len(comp['missing'])} fehlende Beiträge"):
                            for item in comp["missing"]:
                                st.markdown(f"- **{item['kind']} {item['num']}**: {item['title']}")
                    else:
                        st.success(f"✅ Vollständigkeit: alle {comp['checked']} Beiträge gefunden")
            else:
                st.error(f"Fehler: {result['error']}")

    # ============================================================
# WOCHEN-META-BRIEFING (Expander, unabhängig vom Tagesbriefing)
# ============================================================


# ============================================================
# ========================================================
# 🎙️ Erweitert: Bestehendes Briefing in Erzählmodus konvertieren
# ========================================================

st.markdown("---")
with st.expander("🎙️ Erweitert: Briefing in Erzählmodus konvertieren (API, kostenpflichtig)", expanded=False):
    with st.expander("ℹ️ Wofür ist das?", expanded=False):
        st.caption(
            "Nimmt eine fertige Briefing-PDF und schreibt sie als zusammenhängenden Erzähltext im Podcast-Stil neu. "
            "Praktisch, wenn du dasselbe Briefing **klassisch strukturiert** zum Weiterleiten und **fließend erzählt** zum Anhören brauchst."
        )
        st.caption(f"📂 iCloud-Archiv: `{_resolve_archive_dir()}`")

    _convert_tab1, _convert_tab2 = st.tabs(["📂 Aus iCloud-Archiv", "⬆️ PDF hochladen"])

    _pdf_to_convert = None  # Pfad zum zu konvertierenden PDF
    _pdf_display_name = None  # Anzeigename
    _temp_uploaded_path = None  # Pfad zur temporären Upload-Datei

    with _convert_tab1:
        # Nur wirklich vorhandene PDFs (exists() funktioniert im launchd-Dienst,
        # nur das Auflisten nicht) und VOLLE Pfade behalten (Unterordner wie
        # Wochen-Briefings/ gehen sonst verloren).
        _pdf_path_by_name = {
            p.name: p for p in _list_archived_briefings(limit=40)
            if p.suffix.lower() == ".pdf" and p.exists()
        }
        _pdf_options = list(_pdf_path_by_name.keys())
        if _pdf_options:
            _selected_pdf = st.selectbox(
                "Briefing aus dem Archiv auswählen",
                options=_pdf_options,
                key="convert_pdf_select",
                help="Zeigt vorhandene Briefing-PDFs aus deinem Archiv.",
            )
            if _selected_pdf:
                _pdf_to_convert = _pdf_path_by_name.get(_selected_pdf)
                _pdf_display_name = _selected_pdf
                try:
                    _file_size_kb = _pdf_to_convert.stat().st_size // 1024 if _pdf_to_convert else 0
                    st.caption(f"✅ Ausgewählt: `{_selected_pdf}` ({_file_size_kb} KB)")
                except Exception:
                    st.caption(f"✅ Ausgewählt: `{_selected_pdf}`")
        else:
            st.info("Noch keine Briefings im Archiv. Erstelle zuerst ein Briefing über die App oben.")

    with _convert_tab2:
        _uploaded_pdf = st.file_uploader(
            "PDF zum Konvertieren hochladen",
            type=["pdf"],
            key="convert_pdf_upload",
            help="Lade eine fertige Briefing-PDF von deinem Computer oder iPhone hoch. Funktioniert mit jedem klassischen Briefing aus der App oder von einer anderen Quelle.",
        )
        if _uploaded_pdf is not None:
            # In ein temporäres File schreiben damit pdfplumber drauf zugreifen kann
            import tempfile
            _temp_dir = Path(tempfile.gettempdir())
            _temp_uploaded_path = _temp_dir / f"upload_convert_{_uploaded_pdf.name}"
            _temp_uploaded_path.write_bytes(_uploaded_pdf.getbuffer())
            _pdf_to_convert = _temp_uploaded_path
            _pdf_display_name = _uploaded_pdf.name
            _size_kb = len(_uploaded_pdf.getbuffer()) // 1024
            st.caption(f"✅ Hochgeladen: `{_uploaded_pdf.name}` ({_size_kb} KB)")

    # Konvertieren-Button (zentral, gilt für beide Tabs)
    _convert_btn = st.button(
        "🎙️ Konvertieren in Erzählmodus",
        key="convert_to_narrative_button",
        use_container_width=True,
        disabled=not (_pdf_to_convert and api_key),
        type="primary",
        help=("Verwebt das Briefing zu einem fließenden Podcast-Text mit hörbaren Beitrags-Markern und 'Was bleibt'-Punkten." if (_pdf_to_convert and api_key) else ("Wähle erst ein PDF aus oder lade eines hoch." if not _pdf_to_convert else "API-Key fehlt — diese Funktion läuft über die kostenpflichtige API. Key im API-Block weiter oben eintragen.")),
    )

    if _convert_btn and _pdf_to_convert:
        # Output-Pfad immer in den iCloud-Ordner, mit _erzaehl-Suffix
        _stem = Path(_pdf_display_name).stem
        if _stem.endswith("_erzaehl"):
            _out_name = _stem + "_v2.pdf"
        else:
            _out_name = _stem + "_erzaehl.pdf"
        _out_path = _resolve_archive_dir() / _out_name

        _conv_progress = st.progress(0)
        _conv_status = st.empty()
        def _conv_progress_cb(step, frac):
            _conv_progress.progress(max(0.0, min(frac, 1.0)))
            _conv_status.caption(step)

        with st.spinner(f"Konvertiere `{_pdf_display_name}`..."):
            _result = convert_briefing_pdf_to_narrative(
                pdf_path=str(_pdf_to_convert),
                api_key=api_key,
                model=model,
                output_pdf_path=str(_out_path),
                progress_callback=_conv_progress_cb,
            )

        _conv_progress.empty()
        _conv_status.empty()

        # Temp-Upload aufräumen
        if _temp_uploaded_path and _temp_uploaded_path.exists():
            try:
                _temp_uploaded_path.unlink()
            except Exception:
                pass

        if _result["ok"]:
            st.success(f"✅ Erzähl-PDF gebaut: {_result['sections_count']} Beiträge aus {_result.get('original_sections_count', '?')} ursprünglichen Beiträgen → `{_out_name}`")
            with open(_out_path, "rb") as f:
                st.download_button(
                    "📥 Erzähl-PDF herunterladen",
                    data=f.read(),
                    file_name=_out_name,
                    mime="application/pdf",
                    key=f"download_converted_{_out_name}",
                    use_container_width=True,
                    on_click="ignore",
                )
            st.caption(f"Auch gespeichert in `{_resolve_archive_dir()}`")

            # Vollständigkeits-Report
            _comp = _result.get("completeness")
            if _comp:
                ratio = _comp["found"] / max(_comp["checked"], 1)
                if _comp["missing"]:
                    st.warning(f"⚠️ Vollständigkeit: {_comp['found']}/{_comp['checked']} Beiträge erkannt ({int(ratio*100)}%)")
                    with st.expander(f"❌ {len(_comp['missing'])} möglicherweise fehlende Beiträge"):
                        for item in _comp["missing"]:
                            st.markdown(f"- **{item['title']}** (Schlüsselwörter: {', '.join(item.get('keywords', []))})")
                else:
                    st.success(f"✅ Vollständigkeit: alle {_comp['checked']} Beiträge im Erzähltext erkannt")
        else:
            st.error(f"Fehler bei der Konvertierung: {_result['error']}")

    # ============================================================
    # ERZÄHLMODUS-PROMPT zum Kopieren (für Claude-Chat ohne App-Umweg)
    # ============================================================

    st.markdown("---")
    _prompt_path = _APP_DIR / "prompts" / "erzaehlmodus_prompt.md"
    if _prompt_path.exists():
        with st.expander("📋 Erzählmodus-Prompt zum Kopieren (für externen Claude-Chat)"):
            st.caption("Diesen Prompt kannst du in einen Claude-Chat reinkopieren, wenn du ein Briefing manuell in den Erzählmodus konvertieren willst. Direkt darunter dann den Briefing-Text einfügen.")
            _prompt_text = _prompt_path.read_text(encoding="utf-8")
            col_dl, col_show = st.columns(2)
            with col_dl:
                st.download_button(
                    "💾 Prompt herunterladen (.md)",
                    data=_prompt_text.encode("utf-8"),
                    file_name="erzaehlmodus_prompt.md",
                    mime="text/markdown",
                    key="download_narrative_prompt",
                    use_container_width=True,
                )
            with col_show:
                with st.popover("👁️ Prompt anzeigen", use_container_width=True):
                    st.code(_prompt_text, language="markdown")


# ============================================================
# 🧭 Schnell-Navigation: feste Leiste rechts
#    Zwei Sorten: "Springen + vorbereiten" (st.button → hängt Leerzeile/Trenner an,
#    fokussiert das Feld mit Cursor am Ende) und "nur springen" (reine Anker, sofort).
# ============================================================
st.markdown("""
<style>
html { scroll-behavior: smooth; }
.st-key-side_nav {
  position: fixed; right: 10px; top: 28%; z-index: 9999; width: 54px;
  display: flex; flex-direction: column; gap: 4px;
  background: rgba(252, 252, 253, 0.94); border: 1px solid #e4e4e7;
  border-radius: 14px; padding: 8px 6px; box-shadow: 0 2px 12px rgba(0,0,0,0.09);
}
.st-key-side_nav .stButton button {
  width: 40px; min-height: 40px; height: 40px; padding: 0;
  border-radius: 10px; font-size: 19px; line-height: 1;
}
.st-key-side_nav a.nav-jump {
  display: flex; width: 40px; height: 40px; align-items: center; justify-content: center;
  font-size: 19px; text-decoration: none; border: 1px solid #d9d9de; border-radius: 10px;
  background: #ffffff;
}
.st-key-side_nav a.nav-jump:hover { background: #eef1f6; }
@media (max-width: 1100px) { .st-key-side_nav { display: none; } }
</style>
""", unsafe_allow_html=True)


def _nav_prepare(field_key: str, label: str, separator: str):
    """Hängt Leerzeile/Trenner ans Feldende (falls nötig) und merkt das Fokus-Ziel."""
    _cur = (st.session_state.get(field_key) or "").rstrip()
    if not _cur:
        # Feld (aus Serversicht) leer → NICHTS schreiben, nur hinspringen/fokussieren.
        # Schutz: ein Nav-Klick darf niemals Inhalt überschreiben können (03.07. passiert).
        st.session_state["_nav_focus"] = label
        st.rerun()
    if separator == "\n":
        _new = _cur + "\n"
    elif _cur.endswith("mmm"):
        _new = _cur + "\n\n"
    else:
        _new = _cur + "\n\nmmm\n\n"
    st.session_state[f"{field_key}_pending_value"] = _new
    st.session_state["_nav_focus"] = label
    st.rerun()


with st.container(key="side_nav"):
    st.markdown('<a class="nav-jump" href="#nav-top" title="Zum Seitenanfang">⬆️</a>', unsafe_allow_html=True)
    if st.button("🔗", key="nav_btn_urls", help="Zu den Artikel-Links — neue Zeile ist vorbereitet, direkt Strg+V"):
        _nav_prepare("urls_text", "Artikel-URLs", "\n")
    if st.button("🔒", key="nav_btn_paywall", help="Zu den Paywall-Artikeln — mmm-Trenner ist vorbereitet, direkt Strg+V"):
        _nav_prepare("paywall_text", "Paywall-Artikel", "mmm")
    if st.button("🎙️", key="nav_btn_podcast", help="Zu den Podcast-Zusammenfassungen — mmm-Trenner ist vorbereitet, direkt Strg+V"):
        _nav_prepare("podcast_text", "Podcast-Zusammenfassungen", "mmm")
    st.markdown(
        '<a class="nav-jump" href="#nav-inbox" title="Episoden-Inbox">📡</a>'
        '<a class="nav-jump" href="#nav-claude" title="Briefing mit Claude erstellen">🦉</a>'
        '<a class="nav-jump" href="#nav-meta" title="Wochen-Meta-Briefing">🗓️</a>',
        unsafe_allow_html=True,
    )

_nav_focus_label = st.session_state.pop("_nav_focus", None)
if _nav_focus_label:
    import streamlit.components.v1 as _nav_components
    _nav_components.html(f"""<script>
    (function() {{
      let tries = 0;
      function go() {{
        const ta = window.parent.document.querySelector('textarea[aria-label="{_nav_focus_label}"]');
        if (ta) {{
          ta.scrollIntoView({{behavior: "smooth", block: "center"}});
          ta.focus();
          const n = ta.value.length;
          try {{ ta.setSelectionRange(n, n); }} catch (e) {{}}
        }} else if (tries++ < 20) {{
          setTimeout(go, 150);
        }}
      }}
      setTimeout(go, 250);
    }})();
    </script>""", height=0)
