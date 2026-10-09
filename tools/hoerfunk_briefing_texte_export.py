#!/usr/bin/env python3
"""📻 Hörfunk-Nachtrag (09.10.2026): Podcast-Zusammenfassungen früherer Briefings als
JSON-Liste — Hörfunk zeigt sie dann bei der Folge an. Seit 09.10. schickt die App den
Text beim „verbaut“-Melden selbst mit; dieses Skript holt nur das Alte nach.

Aufruf:  python3 tools/hoerfunk_briefing_texte_export.py ZIEL.json [--alle]

Liest nur, ändert nichts. Quellen:
  1. .briefing_podcasts_verbaut.json — welche Folge in welchem Briefing stand
     (Schlüssel = podcast_verbaut.fingerabdruck des fetten Titels, 60 Tage).
  2. Entwurfs-Schnappschüsse mit podcast_text (~/.briefing_draft_*.json,
     .briefing_draft_backups/, ~/.briefing_meta_mirror/, .nachholen/, rettung_*/) —
     der Feldstand, also genau der Text, der in den Bau ging.
  3. Claude-CLI-Protokolle der Podcast-Zusammenfassungen (~/.claude/projects/*/*.jsonl):
     exakte Titel aus Pocket Casts/Hörfunk („Podcast: X — Episode: Y“) und der Text,
     wo kein Schnappschuss existiert; belegte Faktencheck-Korrekturen werden wie in
     summarize_podcast_transcript_via_cli nachgezogen.
  Die Vorlese-Texte im Archiv taugen NICHT: umgeschrieben („Länger erhalten“), ohne
  Markdown, ohne Folgentitel — eine Zuordnung wäre geraten.

Standard: nur Folgen, die nachweislich in einem Briefing standen (Quelle 1),
datum = Briefing-Datum. --alle: zusätzlich alle übrigen Zusammenfassungen,
datum = Tag der Zusammenfassung (ob sie gebaut wurden, ist dort nicht belegt).
"""
import datetime
import glob
import json
import os
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
import podcast_verbaut as pv  # noqa: E402
from pocketcasts_fetch import hoerfunk_text  # noqa: E402

try:  # exakt wie die App trennen und Korrekturen prüfen
    from briefing_core import split_podcast_summaries, _verify_podcast_fixes, _loads_llm_json  # noqa: E402
except Exception:  # pragma: no cover — ohne App-Abhängigkeiten: einfacher Ersatz, keine Korrekturen
    _verify_podcast_fixes = None
    _loads_llm_json = None

    def split_podcast_summaries(text):
        t = re.sub(r"(?i)\bartikel ende\b|(?<![A-Za-zÄÖÜäöüß])m{3,}(?![A-Za-zÄÖÜäöüß])", "\n<<<S>>>\n", text or "")
        return [b.strip() for b in t.split("<<<S>>>") if len(b.strip()) > 30]

HOME = Path.home()
CLI_PROJEKTE = HOME / ".claude" / "projects"
START_ZUSAMMENFASSUNG = "Fasse das folgende Podcast-Transkript zusammen"
START_FAKTENCHECK = "Du prüfst eine Podcast-ZUSAMMENFASSUNG"
MARKER = "Ende der Podcastzusammenfassung"
KOPF = re.compile(r"Podcast:[ \t]*(.+?)(?:[ \t]+—[ \t]+Episode:[ \t]*(.+))?[ \t]*$")
SCHNAPPSCHUESSE = [
    str(APP / ".briefing_draft.json"), str(APP / ".briefing_draft_backups" / "*.json"),
    str(HOME / ".briefing_draft_*.json"), str(HOME / ".briefing_meta_mirror" / "draft_backup_*.json"),
    str(APP / ".nachholen" / "*.json"), str(APP / ".nachholen" / "sicherung" / "*.json"),
    str(APP / "rettung_*" / "*.json"),
]


def _flach(s):
    return " ".join(hoerfunk_text(s).split())


def _lokal(ts_iso):
    try:
        return datetime.datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except Exception:
        return None


def _text_aus(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _sitzung(pfad):
    """(art, zeit, eingabe, ausgabe) einer Headless-Sitzung oder None."""
    eingabe, zeit, ausgabe = None, None, []
    try:
        with open(pfad, encoding="utf-8", errors="replace") as fh:
            for i, zeile in enumerate(fh):
                if eingabe is None:
                    if i > 8:
                        return None
                    if '"user"' not in zeile:
                        continue
                    o = json.loads(zeile)
                    if o.get("type") != "user":
                        continue
                    eingabe = _text_aus((o.get("message") or {}).get("content")).lstrip()
                    if not (eingabe.startswith(START_ZUSAMMENFASSUNG) or eingabe.startswith(START_FAKTENCHECK)):
                        return None
                    zeit = _lokal(o.get("timestamp") or "")
                elif '"assistant"' in zeile:
                    o = json.loads(zeile)
                    if o.get("type") == "assistant":
                        ausgabe.append(_text_aus((o.get("message") or {}).get("content")))
    except Exception:
        return None
    if eingabe is None:
        return None
    art = "summe" if eingabe.startswith(START_ZUSAMMENFASSUNG) else "check"
    return art, zeit, eingabe, "\n".join(a for a in ausgabe if a).strip()


def _titel_teilen(t):
    for sep in (" – ", " — ", " - ", ": "):
        if sep in t:
            a, b = t.split(sep, 1)
            return a.strip(), b.strip()
    return t.strip(), ""


def cli_kandidaten():
    summen, checks = [], {}
    for pfad in glob.glob(str(CLI_PROJEKTE / "*" / "*.jsonl")):
        s = _sitzung(pfad)
        if not s or not s[3]:
            continue
        art, zeit, eingabe, ausgabe = s
        if art == "check":
            m = re.search(r"=== ZUSAMMENFASSUNG ===\s*(.*?)\s*=== TRANSKRIPT \(Auszug\) ===", eingabe, re.S)
            j = re.search(r"\{.*\}", ausgabe, re.S)
            if m and j:
                try:
                    daten = (_loads_llm_json(j.group(0)) if _loads_llm_json else json.loads(j.group(0))) or {}
                except Exception:
                    continue
                k = _flach(m.group(1))
                if k not in checks or (zeit and checks[k][0] and zeit > checks[k][0]):
                    checks[k] = (zeit, daten.get("fixes") if isinstance(daten, dict) else None)
            continue
        summary = ausgabe.strip()
        if len(summary) < 200:           # wie die App: Fehlermeldungen/Abbrüche zählen nicht
            continue
        if MARKER not in summary:
            summary += "\n\n" + MARKER
        transkript = eingabe.split("=== TRANSKRIPT ===", 1)[-1].strip()
        kopf = KOPF.match(transkript.split("\n", 1)[0].strip())
        pt, et = (kopf.group(1).strip(), (kopf.group(2) or "").strip()) if kopf else ("", "")
        if kopf and not et:
            pt, et = "", pt
        summen.append({"zeit": zeit, "text": summary, "transkript": transkript,
                       "podcast_titel": pt, "folge_titel": et, "quelle": "cli"})
    n_fix = 0
    for s in summen:
        c = checks.get(_flach(s["text"]))
        if not (c and c[1] and _verify_podcast_fixes):
            continue
        for fx in _verify_podcast_fixes(c[1], s["text"], s["transkript"]):
            pat = r"\s+".join(re.escape(tok) for tok in fx["falsch"].split())
            neu, n = re.subn(pat, lambda _m, _r=fx["richtig"]: _r, s["text"], count=1)
            if n:
                s["text"], n_fix = neu, n_fix + 1
    return summen, n_fix


def entwurf_kandidaten():
    out = []
    for pfad in sorted({p for muster in SCHNAPPSCHUESSE for p in glob.glob(muster)}):
        try:
            d = json.loads(Path(pfad).read_text(encoding="utf-8"))
        except Exception:
            continue
        pt = (d.get("podcast_text") or "") if isinstance(d, dict) else ""
        if not pt.strip():
            continue
        zeit = datetime.datetime.fromtimestamp(os.path.getmtime(pfad))
        for b in split_podcast_summaries(pt):
            if b.lstrip().startswith("**"):   # fertige Zusammenfassung, kein Rohtranskript
                out.append({"zeit": zeit, "text": b, "quelle": "entwurf"})
    return out


def pc_uuids():
    """„Podcast: Folge“[:80] → Pocket-Casts-Episode (Aufräum-Liste der App)."""
    try:
        d = json.loads((APP / ".briefing_pc_archive_frage.json").read_text(encoding="utf-8"))
        return {e["titel"]: e["episode"] for e in d if e.get("titel") and e.get("episode")}
    except Exception:
        return {}


def main(ziel, alle=False):
    verbaut = (pv._lesen().get("folgen") or {})
    summen, n_fix = cli_kandidaten()
    entwuerfe = entwurf_kandidaten()
    nach_fp = {}
    for k in summen + entwuerfe:
        nach_fp.setdefault(pv.fingerabdruck(k["text"]), []).append(k)
    uuids = pc_uuids()
    nie = pv.nie_liste()
    items, statistik = [], {"text_entwurf": 0, "text_cli": 0, "titel_exakt": 0}
    for fp, kands in nach_fp.items():
        if not fp or (fp not in verbaut and (not alle or pv.gesperrt(pv.titel(kands[0]["text"]), nie))):
            continue
        # Feldstand (Entwurf) schlägt CLI-Rohfassung; innerhalb davon die neueste.
        best = max(kands, key=lambda k: (k["quelle"] == "entwurf", k["zeit"] or datetime.datetime.min))
        cli = max((k for k in kands if k["quelle"] == "cli"),
                  key=lambda k: k["zeit"] or datetime.datetime.min, default=None)
        if cli and (cli["podcast_titel"] or cli["folge_titel"]):
            pt, et = cli["podcast_titel"], cli["folge_titel"]
        else:
            pt, et = _titel_teilen(pv.titel(best["text"]))
        if fp in verbaut:
            datum = verbaut[fp].get("datum")
        else:
            datum = min(k["zeit"] for k in kands if k["zeit"]).date().isoformat()
        text = hoerfunk_text(best["text"])
        if not text:
            continue
        # hoerfunk_id: für alte Läufe nirgends gespeichert (Hörfunk ordnet über pc_uuid/Titel zu).
        items.append({"hoerfunk_id": None,
                      "pc_uuid": uuids.get(f"{pt}: {et}"[:80]) if pt else None,
                      "podcast_titel": pt, "folge_titel": et, "datum": datum, "text": text})
        statistik["text_" + best["quelle"]] += 1
        statistik["titel_exakt"] += bool(cli and cli["folge_titel"])
    ohne_text = [v.get("titel") or fp for fp, v in verbaut.items() if fp not in nach_fp]
    items.sort(key=lambda x: (x["datum"], x["podcast_titel"], x["folge_titel"]), reverse=True)
    Path(ziel).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(items)} Einträge → {ziel}")
    print(f"  Briefing-Liste: {len(verbaut)} Folgen, davon mit Text: {len(verbaut) - len(ohne_text)}")
    print(f"  mit hoerfunk_id: {sum(1 for i in items if i['hoerfunk_id'])}, "
          f"mit pc_uuid: {sum(1 for i in items if i['pc_uuid'])}, "
          f"exakte Titel (CLI-Kopf): {statistik['titel_exakt']}")
    print(f"  Text aus Entwurf (Feldstand): {statistik['text_entwurf']}, aus CLI: {statistik['text_cli']}")
    print(f"  Quellen: {len(summen)} CLI-Zusammenfassungen ({n_fix} Faktencheck-Korrekturen nachgezogen), "
          f"{len(entwuerfe)} Entwurfs-Blöcke")
    if ohne_text:
        print(f"  ohne Text ({len(ohne_text)}): " + "; ".join(t[:60] for t in ohne_text[:10]))
    return items


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    main(args[0], alle="--alle" in sys.argv)
