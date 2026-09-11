#!/usr/bin/env python3
"""Nachhol-Briefing: EIN großer Artikelberg → zwei hörbare Briefings.

Hintergrund (11.09.2026): Nach einer Woche ohne Briefing lagen 293 Artikel in der
Feedly-Merkliste. In EIN Briefing gepackt wären das ~3,5 Stunden Audio gewesen —
der Längendeckel der App skaliert mit der Themenzahl, bremst also nicht. Deshalb
teilt dieser Läufer den Berg am Datum:

  Teil 1 "Aktuell"    — die jüngsten Tage, normale Länge, plus die Podcasts
  Teil 2 "Rückblick"  — die älteren Tage, kompakte Länge (alte Nachricht = Kern reicht)

Das Datum je Artikel steckt im Feedly-Eintrags-Schlüssel (hex-Zeitstempel), die
Reihenfolge der Blöcke entspricht 1:1 der Reihenfolge der gemerkten Einträge.

Jeder Teil läuft als eigener Lauf über briefing_scheduled.py (Max-Abo, 0 €) und
räumt am Ende NUR seine eigenen Artikel aus der Merkliste.
"""
import datetime
import json
import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

DRAFT = os.path.join(APP_DIR, ".briefing_draft.json")
PENDING = os.path.expanduser("~/.briefing_feedly_pending.json")
ARBEIT = os.path.join(APP_DIR, ".nachholen")
LOG = os.path.expanduser("~/Library/Logs/briefing-nachholen.log")


def log(msg):
    zeile = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    print(zeile, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(zeile + "\n")
    except Exception:
        pass


def eintrag_datum(entry_id):
    """Veröffentlichungszeit aus dem Feedly-Schlüssel (hex-Millisekunden)."""
    try:
        hex_ts = entry_id.split("_", 1)[1].split(":")[0]
        return datetime.datetime.fromtimestamp(int(hex_ts, 16) / 1000)
    except Exception:
        return None


def teile_auf(bloecke, ids, grenze):
    """Blöcke + Feedly-Schlüssel in (jung, alt) trennen — Grenze ist ein date()."""
    jung_b, jung_i, alt_b, alt_i = [], [], [], []
    for i, block in enumerate(bloecke):
        eid = ids[i] if i < len(ids) else ""
        dt = eintrag_datum(eid)
        if dt is None or dt.date() >= grenze:
            jung_b.append(block); jung_i.append(eid)
        else:
            alt_b.append(block); alt_i.append(eid)
    return (jung_b, [x for x in jung_i if x]), (alt_b, [x for x in alt_i if x])


def lauf(name, titel, bloecke, ids, user_id, tiefe, podcast_text, vorlage, trenner):
    """Einen Teil bauen: eigener Entwurf, eigene Merklisten-Auswahl, eigener Lauf."""
    os.makedirs(ARBEIT, exist_ok=True)
    entwurf = dict(vorlage)
    entwurf["paywall_text"] = trenner.join(bloecke)
    entwurf["podcast_text"] = podcast_text or ""
    entwurf["raw_transcript_inbox"] = ""
    entwurf["urls_text"] = ""
    entwurf["briefing_depth_multi"] = [tiefe]
    pfad = os.path.join(ARBEIT, "entwurf_%s.json" % name)
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump(entwurf, f, ensure_ascii=False)

    # Merkliste auf genau diese Artikel setzen — der Läufer räumt am Ende nur die weg.
    with open(PENDING, "w", encoding="utf-8") as f:
        json.dump({"entry_ids": ids, "user_id": user_id}, f, ensure_ascii=False)

    log("▶ %s: %d Artikel, %d Podcast-Zusammenfassungen, Tiefe „%s“"
        % (titel, len(bloecke), len([b for b in (podcast_text or "").split(trenner) if b.strip()]), tiefe))
    env = dict(os.environ)
    env["BRIEFING_ENTWURF"] = pfad
    env["BRIEFING_TITEL"] = titel
    env["BRIEFING_MANUELL"] = "1"
    start = datetime.datetime.now()
    r = subprocess.run([sys.executable, os.path.join(APP_DIR, "briefing_scheduled.py")],
                       env=env, cwd=APP_DIR)
    dauer = int((datetime.datetime.now() - start).total_seconds())
    log("%s %s nach %d:%02d min (Rückgabe %s)"
        % ("✅" if r.returncode == 0 else "❌", titel, dauer // 60, dauer % 60, r.returncode))
    return r.returncode == 0


def main():
    import briefing_core as core
    trenner = "\n\nmmm\n\n"
    grenze_arg = None
    nur = None
    for a in sys.argv[1:]:
        if a.startswith("--ab="):
            grenze_arg = datetime.date.fromisoformat(a.split("=", 1)[1])
        elif a.startswith("--nur="):
            nur = a.split("=", 1)[1]

    vorlage = json.load(open(DRAFT, encoding="utf-8"))
    bloecke = core.split_paywall_articles(vorlage.get("paywall_text") or "")
    pend = json.load(open(PENDING, encoding="utf-8"))
    ids, user_id = pend.get("entry_ids") or [], pend.get("user_id") or ""
    if len(ids) != len(bloecke):
        log("⚠️ %d Blöcke, aber %d Merklisten-Einträge — Datum-Zuordnung unsicher, "
            "es wird mittig geteilt." % (len(bloecke), len(ids)))
        ids = ids + [""] * (len(bloecke) - len(ids))

    if grenze_arg is None:
        # Standard: die letzten drei Tage sind „aktuell“.
        grenze_arg = (datetime.date.today() - datetime.timedelta(days=2))
    (jb, ji), (ab, ai) = teile_auf(bloecke, ids, grenze_arg)
    log("Aufteilung an %s: %d aktuell / %d Rückblick (von %d)"
        % (grenze_arg.strftime("%d.%m."), len(jb), len(ab), len(bloecke)))

    # Podcasts: gerettete Roh-Transkripte zusammenfassen (gehören ins aktuelle Briefing).
    podcast_text = (vorlage.get("podcast_text") or "").strip()
    roh = (vorlage.get("raw_transcript_inbox") or "").strip()
    if roh and nur in (None, "1"):
        import re as _re
        teile = [b.strip() for b in _re.split(r"(?im)^\s*(?:m{3,}|-{3,}|={3,}|artikel ende)\s*$", roh) if b.strip()]
        log("🎧 %d Roh-Transkripte werden zusammengefasst (2 parallel)…" % len(teile))
        from concurrent.futures import ThreadPoolExecutor
        fertig, kaputt = [], []
        with ThreadPoolExecutor(max_workers=2) as pool:
            for t, r in zip(teile, pool.map(core.summarize_podcast_transcript_via_cli, teile)):
                kopf = (t.splitlines() or ["?"])[0][:60]
                if r.get("ok") and (r.get("summary") or "").strip():
                    fertig.append(r["summary"].strip())
                    log("   ✅ %s (%d Wörter)" % (kopf, len(r["summary"].split())))
                else:
                    kaputt.append(kopf)
                    log("   ❌ %s — %s" % (kopf, core.klartext_cli_fehler(r.get("error"))[:90]))
        if fertig:
            podcast_text = (podcast_text + trenner if podcast_text else "") + trenner.join(fertig)
        if kaputt:
            log("⚠️ %d Transkripte ohne Zusammenfassung — Rohtext bleibt im Entwurf erhalten." % len(kaputt))

    heute = datetime.date.today()
    ok1 = ok2 = True
    if nur in (None, "1") and jb:
        ok1 = lauf("aktuell", "Briefing Aktuell %s" % heute.strftime("%d.%m."),
                   jb, ji, user_id, "Intelligent", podcast_text, vorlage, trenner)
    if nur in (None, "2") and ab:
        ok2 = lauf("rueckblick", "Briefing Rückblick %s–%s"
                   % ((grenze_arg - datetime.timedelta(days=5)).strftime("%d.%m."),
                      (grenze_arg - datetime.timedelta(days=1)).strftime("%d.%m.")),
                   ab, ai, user_id, "Intelligent kompakt", "", vorlage, trenner)
    log("Fertig. Aktuell=%s, Rückblick=%s" % (ok1, ok2))
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
