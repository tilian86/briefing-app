#!/usr/bin/env python3
"""Taeglicher Briefing-Lauf — ohne dass jemand einen Knopf druecken muss.

14.09.: `briefing_scheduled.py` ist ein EINMAL-Job, den Florian in der App
scharf stellt. Besucht er die App nicht, passiert nichts — deshalb lagen
zwischen dem 11.09. und dem 14.09. drei Tage voellig ohne Briefing, und im
Wochenrueckblick standen am Ende zwei Tagestexte vom selben Tag.

Dieser Runner macht die ganze Kette allein, in der Reihenfolge, in der Florian
sie sonst von Hand klickt:
  1. Zeitungs-Anmeldungen pruefen  (sonst kaemen nur Anrisse ins Briefing)
  2. Feedly-Merkliste holen
  3. Artikel in den Entwurf schreiben
  4. briefing_scheduled.main() bauen und in den ElevenReader laden lassen
  5. verbrauchte Quellen aus dem Entwurf raeumen

Punkt 5 ist der unscheinbare, aber entscheidende: ohne ihn stuende der gestrige
Artikelberg morgen noch im Entwurf und wuerde ein zweites Mal vertont.

Podcasts bleiben aussen vor — die wirft Florian selbst ein. Liegt beim Start
schon etwas im Podcast-Feld, wandert es ganz normal mit ins Briefing.

Laeuft komplett ueber das Max-Abo. Von Hand:  python3 briefing_taeglich.py
                                              BRIEFING_TAG_ERZWINGEN=1 python3 …
"""
import datetime
import json
import os
import re
import shutil
import sys
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

DRAFT_PATH = os.path.join(APP_DIR, ".briefing_draft.json")
STATUS_PATH = os.path.join(APP_DIR, ".briefing_job_status.json")
LOG_PATH = os.path.expanduser("~/Library/Logs/briefing-taeglich.log")
SPIEGEL_TEXTE = os.path.expanduser("~/.briefing_meta_mirror/Texte")
SICHERUNG_DIR = os.path.expanduser("~/.briefing_meta_mirror")
ERZWINGEN = bool(os.environ.get("BRIEFING_TAG_ERZWINGEN"))


def _log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def _entwurf_lesen() -> dict:
    try:
        with open(DRAFT_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _entwurf_schreiben(entwurf: dict) -> bool:
    try:
        tmp = DRAFT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entwurf, f, ensure_ascii=False)
        os.replace(tmp, DRAFT_PATH)
        return True
    except Exception as e:
        _log("Entwurf schreiben fehlgeschlagen: %s" % e)
        return False


def _schon_heute_gebaut() -> bool:
    """Liegt fuer heute schon ein Tagestext im Spiegel?"""
    heute = datetime.date.today().strftime("%Y-%m-%d")
    try:
        return any(n.startswith(heute) for n in os.listdir(SPIEGEL_TEXTE))
    except Exception:
        return False


def _fehlerbuch(bereich, meldung, schwere="normal"):
    try:
        import fehlerbuch
        fehlerbuch.eintragen(bereich, meldung, None, schwere)
    except Exception:
        pass


def _artikel_holen() -> str:
    """Feedly-Merkliste holen und als Textblock zurueckgeben ("" = nichts Neues).

    Wirft RuntimeError, wenn der Lauf abgebrochen werden muss."""
    import feedly_fetch as F

    # Erst anklopfen. Genau in dieser Reihenfolge macht es die App auch — ein
    # abgelaufenes Zeitungs-Login liefert Anrisse statt Artikeln, und aus
    # 110-Wort-Anrissen entsteht ein Briefing, das nach Inhalt aussieht und
    # keiner ist (11.09.). Der Zwischenspeicher wird verworfen, sonst
    # antwortet der Test mit einem bis zu drei Stunden alten Ergebnis.
    F.login_check_verwerfen()
    kaputt = [r for r in F.login_check_cached() if r.get("ok") is False]
    if kaputt:
        namen = ", ".join(r.get("name", "?") for r in kaputt)
        raise RuntimeError("Nicht angemeldet bei: %s — es kaemen nur Anrisse." % namen)

    entwurf = _entwurf_lesen()
    schon_drin = re.findall(r"https?://\S+", entwurf.get("paywall_text") or "")
    pending = F.load_pending()
    res = F.fetch_all(progress=lambda m: _log("  %s" % m),
                      skip_urls=schon_drin, skip_ids=pending.get("entry_ids") or [])
    ok = res.get("ok") or []
    probleme = res.get("problems") or []
    if probleme:
        # Nicht bauen. Die Artikel bleiben in der Merkliste, nichts geht verloren.
        raise RuntimeError("%d Artikel kamen nur als Anriss an (%s) — nicht gebaut, "
                           "sie bleiben in der Merkliste."
                           % (len(probleme), probleme[0].get("problem", "?")))
    if not ok:
        return ""
    F.add_pending([i["entry_id"] for i in ok if i.get("entry_id")]
                  + [i["entry_id"] for i in (res.get("skipped") or []) if i.get("entry_id")]
                  + [i["entry_id"] for i in (res.get("ohne_zugang") or []) if i.get("entry_id")],
                  res.get("user_id") or "")
    _log("%d Artikel geholt (%d uebersprungen, %d ohne Abo)."
         % (len(ok), len(res.get("skipped") or []), len(res.get("ohne_zugang") or [])))
    return F.to_blocks(ok)


def _norm(x):
    return " ".join(str(x or "").split()).lower()


def _quellen_raeumen(status):
    """Verbrauchte Quellen aus dem Entwurf nehmen — Ausgefallene bleiben liegen.

    Ohne diesen Schritt stuende der heutige Artikelberg morgen noch da und
    wuerde ein zweites Mal vertont. In der App leert Florian von Hand ueber
    „Neues Briefing"; hier muss es der Lauf selbst tun.

    Heikel ist nur der Teilausfall: schafft eine Quelle es nicht ins Briefing
    (Limit, Modellfehler), meldet der Lauf sie als „uncovered". Aus der
    Feedly-Merkliste wird sie dann absichtlich NICHT ausgetragen — sie steht
    aber auch auf der Vormerkliste und wuerde beim naechsten Holen
    uebersprungen. Einziger Ort, an dem sie ueberlebt, ist also der Entwurf.
    Deshalb: abgedeckte Bloecke raus, ausgefallene stehen lassen."""
    entwurf = _entwurf_lesen()
    if not entwurf:
        return
    try:
        os.makedirs(SICHERUNG_DIR, exist_ok=True)
        shutil.copy2(DRAFT_PATH, os.path.join(
            SICHERUNG_DIR, "draft_backup_%s.json" % datetime.date.today().strftime("%Y%m%d")))
    except Exception as e:
        _log("Sicherung des Entwurfs fehlgeschlagen (%s) — raeume lieber nicht." % e)
        return

    offen = status.get("uncovered_sources") or []
    arten = set(u.get("kind") or "article" for u in offen)

    # --- Paywall-Artikel: blockweise ---
    paywall = entwurf.get("paywall_text") or ""
    nadeln = [_norm(u.get("title")) for u in offen
              if (u.get("kind") or "article") != "podcast" and _norm(u.get("title"))]
    if not paywall.strip():
        entwurf["paywall_text"] = ""
    elif not nadeln:
        entwurf["paywall_text"] = ""
    else:
        try:
            import briefing_core as core
            bloecke = core.split_paywall_articles(paywall)
            norm = [_norm(b) for b in bloecke]
            behalten, unklar = [], False
            for nadel in nadeln:
                treffer = [i for i, nb in enumerate(norm) if nadel in nb]
                if len(treffer) != 1:
                    unklar = True
                    break
                behalten.append(treffer[0])
            if unklar:
                # Lieber ein Thema doppelt als eines verloren — aber sichtbar.
                _log("Ausfaelle nicht eindeutig zuzuordnen — Artikelfeld bleibt "
                     "unveraendert. Morgen kaeme es sonst doppelt.")
                _fehlerbuch("Tagesbriefing automatisch",
                            "Entwurf nicht geleert: %d ausgefallene Quelle(n) liessen sich "
                            "den Artikelbloecken nicht zuordnen. Bitte einmal von Hand "
                            "nachsehen." % len(nadeln), "kritisch")
            else:
                rest = [b for i, b in enumerate(bloecke) if i in set(behalten)]
                entwurf["paywall_text"] = "\n\nmmm\n\n".join(rest)
                _log("%d von %d Artikelbloecken bleiben stehen (nicht im Briefing gelandet)."
                     % (len(rest), len(bloecke)))
        except Exception as e:
            _log("Artikelfeld nicht aufgeraeumt (%s) — bleibt stehen." % e)

    # --- Links und Podcasts: ganz oder gar nicht ---
    # Fuer einzelne Links gibt es keine verlaessliche Zuordnung zum Ausfall
    # (der gemeldete Titel ist ein Textanfang, keine URL). Also: faellt ueberhaupt
    # ein Link-Beitrag aus, bleibt das Linkfeld stehen. Das kostet im seltenen
    # Fall eine Wiederholung und rettet dafuer sicher jeden Link.
    if "article" not in arten:
        entwurf["urls_text"] = ""
    else:
        _log("Linkfeld bleibt stehen — mindestens ein Link-Beitrag ist ausgefallen.")
    if "podcast" not in arten:
        entwurf["podcast_text"] = ""
    else:
        _log("Podcastfeld bleibt stehen — der Beitrag hat es nicht ins Briefing geschafft.")
    entwurf["raw_transcript_inbox"] = ""

    if _entwurf_schreiben(entwurf):
        _log("Entwurf aufgeraeumt (Sicherung liegt im Spiegel).")


def main():
    _log("=== Taeglicher Lauf gestartet ===")
    if not ERZWINGEN and _schon_heute_gebaut():
        _log("Heute liegt schon ein Briefing vor — nichts zu tun.")
        return
    # Hat Florian in der App selbst einen Termin fuer heute gestellt, gehoert
    # ihm der Tag. Sonst baute dieser Lauf um 04:45 alles weg, und sein Termin
    # um 07:00 faende nur noch einen leeren Entwurf.
    if not ERZWINGEN:
        try:
            import briefing_schedule
            m = briefing_schedule.current() or {}
            start = datetime.datetime.fromisoformat(m["start"]) if m.get("start") else None
            if start and start.date() == datetime.date.today() and start > datetime.datetime.now():
                _log("Eigener Termin um %s steht — der hat Vorrang." % start.strftime("%H:%M"))
                return
        except Exception:
            pass

    try:
        neu = _artikel_holen()
    except Exception as e:
        _log("Abbruch vor dem Bauen: %s" % e)
        _fehlerbuch("Tagesbriefing automatisch", str(e)[:300], "kritisch")
        return

    entwurf = _entwurf_lesen()
    if neu:
        alt = (entwurf.get("paywall_text") or "").rstrip()
        entwurf["paywall_text"] = (alt + "\n\nmmm\n\n" + neu) if alt else neu
        if not _entwurf_schreiben(entwurf):
            _fehlerbuch("Tagesbriefing automatisch",
                        "Artikel geholt, aber der Entwurf liess sich nicht schreiben.", "kritisch")
            return

    entwurf = _entwurf_lesen()
    if not any((entwurf.get(f) or "").strip()
               for f in ("paywall_text", "urls_text", "podcast_text")):
        # Kein Fehler, sondern ein ruhiger Tag: nichts in der Merkliste, nichts
        # im Entwurf. Dafuer muss niemand geweckt werden.
        _log("Nichts zu vertonen — Merkliste leer und Entwurf leer. Feierabend.")
        return

    # Ab hier baut der bewaehrte Runner. MANUELL, damit er keinen echten
    # Termin-Job von Florian mit abraeumt.
    os.environ["BRIEFING_MANUELL"] = "1"
    import briefing_scheduled
    briefing_scheduled.main()

    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            status = json.load(f) or {}
    except Exception:
        status = {}
    if status.get("done") and not status.get("failed"):
        _log("Gebaut: %s" % (status.get("step") or "fertig"))
        _quellen_raeumen(status)
    else:
        _log("Nicht gebaut: %s" % (status.get("step") or "unbekannt"))
        _fehlerbuch("Tagesbriefing automatisch",
                    "Lauf nicht erfolgreich: %s" % str(status.get("step"))[:250],
                    "kritisch" if not status.get("limit_hit") else "normal")
    _log("=== Fertig ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as _fatal:
        _log("Fataler Fehler:\n" + traceback.format_exc())
        _fehlerbuch("Tagesbriefing automatisch", "Runner abgestuerzt: %s" % str(_fatal)[:200], "kritisch")
