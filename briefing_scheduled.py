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

# 11.09.: Für Nachhol-Läufe (mehrere Teile aus EINEM Artikelberg) lassen sich
# Entwurf, Titel und das Selbst-Aufräumen per Umgebungsvariable übersteuern.
DRAFT_PATH = os.environ.get("BRIEFING_ENTWURF") or os.path.join(APP_DIR, ".briefing_draft.json")
TITEL_OVERRIDE = (os.environ.get("BRIEFING_TITEL") or "").strip()
MANUELL = bool(os.environ.get("BRIEFING_MANUELL"))
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
    if MANUELL:
        # Ein manuell gestarteter Nachhol-Lauf darf einen ECHT terminierten
        # Briefing-Termin nicht mit abräumen.
        return
    try:
        import briefing_schedule
        briefing_schedule.disarm()
        _log("Aufgeräumt: Weckzeit + Zeitplan entfernt.")
    except Exception as e:
        _log("Cleanup-Fehler: %s" % e)


def main():
    _log("=== Terminierter Lauf gestartet ===")
    # 30.08.: Der Status wurde erst NACH dem Laden des Entwurfs geschrieben.
    # Starb der Lauf davor (kaputte Entwurfsdatei, Import-Fehler), stand in der
    # Statusdatei weiter der VORIGE Lauf — die App zeigte "✅ Fertig" von
    # gestern, und dass heute nichts lief, sah niemand. Jetzt wird sofort
    # gestempelt, und der Absturz-Handler unten schreibt das Scheitern hinein.
    import datetime as _dt
    _write_status({"active": True, "started": _dt.datetime.now().isoformat(),
                   "step": "⏰ Terminierter Lauf gestartet…", "ratio": 0.0,
                   "done": False, "cancel": False, "results": [], "scheduled": True})
    import briefing_core as core
    from reader_upload import upload_briefing_epub, upload_briefing_txt

    if not os.path.exists(DRAFT_PATH):
        _log("Kein Entwurf gefunden — Abbruch.")
        _write_status({"active": True, "started": _dt.datetime.now().isoformat(),
                       "step": "❌ Kein Entwurf gefunden — nichts zu bauen.",
                       "ratio": 1.0, "done": True, "failed": True, "results": []})
        _cleanup()
        return
    draft = json.loads(open(DRAFT_PATH, encoding="utf-8").read())

    urls = draft.get("urls_text", "") or ""
    paywall = draft.get("paywall_text", "") or ""
    podcast = draft.get("podcast_text", "") or ""
    if not (urls.strip() or paywall.strip() or podcast.strip()):
        _log("Entwurf leer (keine Quellen) — Abbruch.")
        _write_status({"active": True, "started": _dt.datetime.now().isoformat(),
                       "step": "❌ Entwurf war leer — keine Quellen zum Bauen.",
                       "ratio": 1.0, "done": True, "failed": True, "results": []})
        _cleanup()
        return

    # 22.09.: Die App erzwingt Florians Standardschalter bei JEDEM Sitzungsstart
    # (_ERZWUNGENE_DEFAULTS in briefing_app.py) — in der Oberfläche sehen sie darum
    # immer angehakt aus. Dieser Lauf hier las aber die ROHE Entwurfsdatei. Stand
    # dort ein alter Stand mit ausgeschalteten Schaltern (nachweislich am 14.08. und
    # am 22.09. passiert), baute der terminierte Lauf still ohne Themenbündelung,
    # ohne Qualitätsprüfung und — am schlimmsten — ohne Reader-Upload, während in
    # der App alles richtig aussah. Dieselbe Liste, derselbe Schutz.
    _ERZWUNGEN = {
        "topic_synthesis_mode": True,
        "synthesis_narrative_style": True,
        "synthesis_web_enrich": True,
        "quality_check_enabled": True,
        "auto_reader_upload": True,
        "podcast_synth_mode": "Länger erhalten",
        "briefing_depth_multi": ["Intelligent"],
    }
    for _k, _v in _ERZWUNGEN.items():
        if draft.get(_k) != _v:
            _log("Entwurf-Schalter korrigiert: %s = %r -> %r" % (_k, draft.get(_k), _v))
            draft[_k] = _v

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
    title_base = TITEL_OVERRIDE or ("Tagesbriefing %s %s" % (_WD[now.weekday()], now.strftime("%d.%m.")))

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
    # 04.09.: Merkliste beim START einfrieren - wie die App. Artikel, die
    # WAEHREND des Laufs eintreffen, stehen nicht im Briefing und duerfen am
    # Ende nicht mit entfernt werden.
    try:
        import feedly_fetch as _fl_snap
        feedly_pending = _fl_snap.load_pending()
    except Exception as _e:
        _log("Feedly-Schnappschuss nicht lesbar: %s" % _e)
        feedly_pending = {"entry_ids": [], "user_id": ""}

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

    # 11.09.: Vorabprüfung. Vorher lief ein Briefing zwanzig Minuten und starb dann
    # an der abgelaufenen Claude-Anmeldung — inklusive aller Podcasts. Jetzt wird das
    # in fünf Sekunden vorher gemerkt, und der Entwurf bleibt unangetastet.
    try:
        _anm = core.pruefe_claude_anmeldung()
    except Exception as _e:
        _anm = {"ok": True, "meldung": "Vorabprüfung übersprungen (%s)" % _e}
    if not _anm.get("ok"):
        _log("Vorabprüfung fehlgeschlagen: %s" % _anm.get("meldung"))
        status.update({"active": True, "done": True, "failed": True,
                       "step": "❌ %s" % _anm.get("meldung")})
        _write_status(status)
        try:
            import fehlerbuch
            fehlerbuch.eintragen("Claude-Anmeldung", str(_anm.get("meldung")), None, "kritisch")
        except Exception:
            pass
        _cleanup()
        return

    # 22.09.: Zweite Vorabprüfung. Die Claude-Anmeldung wurde geprüft, die des
    # ElevenReaders nicht — ein abgelaufener Reader-Login fiel erst NACH dem
    # kompletten Bau auf (Florian war am 22.09. stillschweigend ausgeloggt).
    # Das Briefing ist dann gebaut, liegt aber nirgends zum Hören.
    if upload:
        try:
            from reader_upload import is_logged_in as _reader_ok
            if not _reader_ok():
                _log("ElevenReader nicht angemeldet — Lauf gestoppt, bevor Arbeit verfällt.")
                status.update({"active": True, "done": True, "failed": True,
                               "step": "❌ ElevenReader ist abgemeldet — bitte in der App neu anmelden."})
                _write_status(status)
                try:
                    import fehlerbuch
                    fehlerbuch.eintragen("ElevenReader-Anmeldung",
                                         "Reader abgemeldet — Briefing nicht gebaut.", None, "kritisch")
                except Exception:
                    pass
                _cleanup()
                return
        except Exception as _e:
            _log("Reader-Vorabprüfung übersprungen (%s)" % _e)

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

    # 📥 Erledigte Artikel aus der Feedly-Merkliste entfernen. 04.09.: Diesen
    # Schritt gab es nur im App-Knopf, nicht hier. Nach einem terminierten Lauf
    # blieb die Merkliste komplett stehen - beim naechsten Abruf waeren alle
    # Artikel erneut gekommen. Gleiche Sicherung wie in der App: fehlt auch nur
    # eine Quelle im Briefing, bleibt die Merkliste unangetastet.
    # 05.09.: Nur fehlende ARTIKEL sperren das Aufraeumen. Ein ausgefallener
    # Podcast kommt nicht aus der Feedly-Merkliste (siehe briefing_app.py).
    _unc_artikel = [u for u in (status.get("uncovered_sources") or [])
                    if (u.get("kind") or "article") != "podcast"]
    # 11.09.: Frueher hielten ein paar Ausfaelle die GANZE Merkliste fest — nach dem
    # Lauf standen 133 laengst erledigte Artikel weiter in Read Later. Jetzt bleiben
    # nur die Ausgefallenen liegen, der Rest wird abgeraeumt.
    _ids_frei, _bleiben = (feedly_pending.get("entry_ids") or []), []
    if _unc_artikel:
        try:
            _ids_frei, _bleiben = core.feedly_ids_ohne_ausfaelle(
                paywall, feedly_pending.get("entry_ids") or [], _unc_artikel)
        except Exception as _zex:
            _log("Ausfall-Zuordnung fehlgeschlagen: %s" % _zex)
            _ids_frei, _bleiben = None, []

    if _unc_artikel and _ids_frei is None:
        status["feedly_removed"] = 0
        status["feedly_hinweis"] = (
            "Merkliste NICHT geleert — %d Artikel fehlen im Briefing und liessen sich "
            "nicht eindeutig zuordnen. Nach einem vollstaendigen Lauf wird aufgeraeumt."
            % len(_unc_artikel))
        _log(status["feedly_hinweis"])
    elif _ids_frei:
        try:
            import feedly_fetch as _fl_done
            _ids = _ids_frei
            if _bleiben:
                status["feedly_hinweis"] = (
                    "%d Artikel bleiben in der Merkliste — sie haben es nicht ins "
                    "Briefing geschafft: %s" % (len(_bleiben), ", ".join(_bleiben[:6])))
                _log(status["feedly_hinweis"])
            _log("Entferne %d erledigte Artikel aus der Feedly-Merkliste…" % len(_ids))
            _n_weg = _fl_done.mark_done(_ids, feedly_pending.get("user_id") or "")
            if _n_weg:
                _fl_done.remove_pending(_ids)
            status["feedly_removed"] = _n_weg
            _log("Merkliste: %d Artikel entfernt." % _n_weg)
        except Exception as _flex:
            status["feedly_error"] = str(_flex)[:160]
            _log("Merkliste aufraeumen fehlgeschlagen: %s" % _flex)
            try:
                import fehlerbuch
                fehlerbuch.eintragen(
                    "Feedly-Merkliste",
                    "Aufraeumen nach terminiertem Lauf fehlgeschlagen: %s" % str(_flex)[:200],
                    {"artikel": len(feedly_pending.get("entry_ids") or [])}, "normal")
            except Exception:
                pass

    try:
        status["usage_5h_after"] = core._read_real_5h_usage()
    except Exception:
        pass
    # 30.08.: Hier stand IMMER "✅ Fertig", auch wenn beim Schreiben Quellen
    # ausgefallen waren. Die App-Variante sagt an derselben Stelle laengst die
    # Wahrheit — der terminierte Lauf jetzt auch.
    _fehlend = len(status.get("uncovered_sources") or [])
    _schluss = ("✅ Fertig (terminiert)." if not _fehlend else
                f"⚠️ Fertig (terminiert) — aber {_fehlend} Quelle(n) fehlen im "
                f"Briefing (Limit/Auslastung beim Schreiben).")
    status.update({"active": True, "done": True, "step": _schluss, "ratio": 1.0, "results": [entry]})
    _write_status(status)
    _log("=== Fertig: %s Beiträge, Upload=%s ===" % (entry.get("sections"), entry.get("upload")))
    _cleanup()


if __name__ == "__main__":
    try:
        main()
    except Exception as _fatal:
        _log("Fataler Fehler:\n" + traceback.format_exc())
        # Muss in die Statusdatei — sonst zeigt die App den Lauf von gestern.
        try:
            import datetime as _dt2
            _write_status({"active": True, "started": _dt2.datetime.now().isoformat(),
                           "step": f"❌ Terminierter Lauf abgestürzt: {str(_fatal)[:150]}",
                           "ratio": 1.0, "done": True, "failed": True, "results": []})
        except Exception:
            pass
        try:
            _cleanup()
        except Exception:
            pass
