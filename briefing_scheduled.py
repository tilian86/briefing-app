#!/usr/bin/env python3
"""Headless-Runner für TERMINIERTE Briefings.

Wird von einem launchd-Einmaljob (local.florian.briefing-schedule) zur geplanten
Zeit gestartet. Baut das Briefing 1:1 wie der App-Knopf — aus den Quellen, die im
gespeicherten Entwurf (.briefing_draft.json) stehen — lädt es in den ElevenReader
und schreibt das Ergebnis in .briefing_job_status.json. Die App zeigt das beim
nächsten Öffnen an (inkl. rotem Upload-Warnkasten, falls die Zustellung hakt).

Am Ende räumt er sich selbst auf: pmset-Weckzeit abbestellen + launchd-Job entladen
(via briefing_schedule.disarm()). Läuft komplett über das Max-Abo, keine API-Kosten.
"""
import datetime
import json
import os
import sys
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

DRAFT_PATH = os.path.join(APP_DIR, ".briefing_draft.json")
STATUS_PATH = os.path.join(APP_DIR, ".briefing_job_status.json")
LOG_PATH = os.path.expanduser("~/Library/Logs/briefing-scheduled.log")

_WD = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_PODCAST_MODE_MAP = {"Einweben (kürzen)": "woven", "Länger erhalten": "soft", "Original übernehmen": "verbatim"}
_SMART_CAP = {"Intelligent kompakt": 75, "Intelligent": 105, "Intelligent ausführlich": 210}


def _log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def _write_status(status):
    try:
        with open(STATUS_PATH, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)
    except Exception as e:
        _log("Status schreiben fehlgeschlagen: %s" % e)


def _cleanup():
    """Weckzeit + launchd-Einmaljob wieder entfernen (Selbstaufräumen)."""
    try:
        import briefing_schedule
        briefing_schedule.disarm()
        _log("Aufgeräumt: Weckzeit + Zeitplan entfernt.")
    except Exception as e:
        _log("Cleanup-Fehler: %s" % e)


def main():
    _log("=== Terminierter Lauf gestartet ===")
    import briefing_core as core
    from reader_upload import upload_briefing_epub, upload_briefing_txt

    if not os.path.exists(DRAFT_PATH):
        _log("Kein Entwurf gefunden — Abbruch.")
        _cleanup()
        return
    draft = json.loads(open(DRAFT_PATH, encoding="utf-8").read())

    urls = draft.get("urls_text", "") or ""
    paywall = draft.get("paywall_text", "") or ""
    podcast = draft.get("podcast_text", "") or ""
    if not (urls.strip() or paywall.strip() or podcast.strip()):
        _log("Entwurf leer (keine Quellen) — Abbruch.")
        _cleanup()
        return

    depths = draft.get("briefing_depth_multi") or ["Intelligent"]
    depth = depths[0] if depths else "Intelligent"
    synth = bool(draft.get("topic_synthesis_mode", True))
    magazin = bool(draft.get("synthesis_narrative_style", True))
    web = bool(draft.get("synthesis_web_enrich", True))
    qc = bool(draft.get("quality_check_enabled", True))
    upload = bool(draft.get("auto_reader_upload", True))
    podcast_mode = _PODCAST_MODE_MAP.get(draft.get("podcast_synth_mode", "Einweben (kürzen)"), "woven")
    specials = core.split_special_topics(draft.get("special_topics_text") or "")
    model = draft.get("claude_cli_model") or "sonnet"

    now = datetime.datetime.now()
    ts = now.strftime("%Y-%m-%d_%H-%M")
    title_base = "Tagesbriefing %s %s" % (_WD[now.weekday()], now.strftime("%d.%m."))

    # Archiv wie die App (iCloud, sonst lokaler Fallback)
    archive = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Briefings")
    try:
        os.makedirs(archive, exist_ok=True)
    except Exception:
        archive = os.path.join(APP_DIR, ".archive")
        os.makedirs(archive, exist_ok=True)

    suffix = "_claude_synthese" if synth else "_claude"
    out_pdf = os.path.join(archive, "%s_briefing%s.pdf" % (ts, suffix))

    inp_counts = {
        "urls": len([l for l in urls.splitlines() if l.strip().startswith("http")]),
        "paywall": len(core.split_paywall_articles(paywall)) if paywall.strip() else 0,
        "podcasts": len(core.split_podcast_summaries(podcast)) if podcast.strip() else 0,
        "specials": len(specials),
    }
    status = {"active": True, "started": now.isoformat(), "step": "⏰ Terminierter Lauf gestartet…",
              "ratio": 0.0, "done": False, "cancel": False, "results": [], "inputs": inp_counts,
              "scheduled": True}
    _write_status(status)

    def _cb(step, ratio):
        status["step"] = "%s: %s" % (depth, step)
        try:
            status["ratio"] = max(0.0, min(1.0, float(ratio)))
        except Exception:
            pass
        _write_status(status)

    try:
        status["usage_5h_before"] = core._read_real_5h_usage()
    except Exception:
        pass

    try:
        r = core.run_briefing_via_claude_cli_chunked(
            urls_text=urls, paywall_text=paywall, podcast_text=podcast,
            include_weather=True, output_pdf_path=out_pdf, model=model,
            compact_mode=(depth != "Ausführlich"),
            ultra_compact=(depth == "Sehr kurz"),
            merge_duplicates=True, prepared=None,
            topic_synthesis=synth,
            synthesis_narrative=bool(synth and magazin),
            synthesis_web_enrich=bool(synth and web),
            content_check=qc, auto_repair=qc,
            special_topics=(specials or None),
            smart_length=bool(depth.startswith("Intelligent")),
            smart_cap=_SMART_CAP.get(depth, 0),
            podcast_mode=podcast_mode,
            progress_callback=_cb,
        )
    except Exception as e:
        _log("Bau-Ausnahme:\n" + traceback.format_exc())
        status.update({"active": True, "done": True, "failed": True,
                       "step": "❌ Fehler beim Bau: %s" % str(e)[:150]})
        _write_status(status)
        _cleanup()
        return

    entry = {"label": depth, "ok": bool(r.get("ok")), "pdf": out_pdf,
             "eleven_txt": (r.get("artifacts") or {}).get("eleven_txt"),
             "sections": r.get("beitrag_count") or r.get("sections_count"),
             "elapsed": int(r.get("elapsed_seconds") or 0),
             "error": r.get("error"), "upload": None, "wa": False}
    cc = r.get("content_check") or {}
    if cc:
        entry["plausi"] = "%sW/%sN, repariert %s" % (cc.get("warnings", "?"), cc.get("notices", "?"), r.get("content_repaired", 0))
    # 26.08.: Der Qualitaetsscore stand nur im Log — die App zeigte nach einem
    # terminierten Lauf gar keinen. Jetzt wandert er wie beim App-Lauf mit.
    if r.get("self_check"):
        entry["self_check"] = r["self_check"]
    if r.get("uncovered_sources"):
        status["uncovered_sources"] = r["uncovered_sources"]

    if not r.get("ok"):
        _log("Bau fehlgeschlagen: %s" % r.get("error"))
        status.update({"active": True, "done": True, "failed": True,
                       "limit_hit": bool(r.get("limit_hit")),
                       "step": ("⛔ Limit erreicht — " if r.get("limit_hit") else "❌ ") + str(r.get("error"))[:150],
                       "results": [entry]})
        _write_status(status)
        _cleanup()
        return

    # Upload wie die App: ePub bevorzugt, TXT als Fallback; Titel in Zeile 1 setzen.
    if upload:
        txtp = (r.get("artifacts") or {}).get("eleven_txt")
        if txtp and os.path.exists(txtp):
            ttl = title_base + (" · %s" % depth) + (" 🧵" if synth else "")
            try:
                tx = open(txtp, encoding="utf-8").read().split("\n")
                if tx and tx[0].strip() in ("Audio-Briefing", ""):
                    tx[0] = ttl
                    open(txtp, "w", encoding="utf-8").write("\n".join(tx))
            except Exception:
                pass
            try:
                ur = upload_briefing_epub(txtp, ttl)
                if not ur.get("ok"):
                    ur = upload_briefing_txt(txtp, ttl)
                entry["upload"] = ("ok: " + ttl) if ur.get("ok") else ("fail: " + str(ur.get("error"))[:100])
            except Exception as e:
                entry["upload"] = "fail: " + str(e)[:100]
            _log("Upload: %s" % entry["upload"])

    try:
        status["usage_5h_after"] = core._read_real_5h_usage()
    except Exception:
        pass
    status.update({"active": True, "done": True, "step": "✅ Fertig (terminiert).", "ratio": 1.0, "results": [entry]})
    _write_status(status)
    _log("=== Fertig: %s Beiträge, Upload=%s ===" % (entry.get("sections"), entry.get("upload")))
    _cleanup()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log("Fataler Fehler:\n" + traceback.format_exc())
        try:
            _cleanup()
        except Exception:
            pass
