"""Pocket-Casts-Transkripte holen fürs Audio-Briefing.

Zweiteiliger Weg (am 21.07.2026 live verifiziert):
1. EPISODEN-LISTE (braucht Login): Playwright-Profil ~/.briefing_pocketcasts_profile,
   einmaliger sichtbarer Login. Lädt pocketcasts.com/new-releases und liest die
   Episoden-UUIDs (podcast-uuid + episode-uuid) aus der eingeloggten Seite.
2. TRANSKRIPT (KEIN Login): öffentliche Datei
   https://shownotes.pocketcasts.com/generated_transcripts/<podcast>/<episode>.vtt
   → HTTP 200 = VTT (Zeitstempel raus → Klartext); 403/404 = kein Transkript → skip.

Kein Cmd+A-Crap: das VTT enthält nur den gesprochenen Text, keine UI/Menüs.
Python 3.9-kompatibel.

28.09.2026: Pocket Casts blockt den unsichtbaren Browser (CloudFront „Request
blocked"). Abos, Filter und Archivieren laufen deshalb über die offizielle
Schnittstelle mit Einmal-Anmeldung (`python3 pocketcasts_fetch.py --login` oder
das Formular in der App) — siehe `api_login`.
"""
import json
import os
import re
import sys
import time
import browser_pfad
import fehlerbuch  # muss VOR jedem Playwright-Import stehen (25.08.)
import urllib.error
import urllib.request

# Ladezeitpunkt: die laufende App lädt das Modul neu, wenn die Datei jünger ist
# (sonst gelten Korrekturen erst nach einem App-Neustart).
_GELADEN_UM = time.time()

PROFILE_DIR = os.path.expanduser("~/.briefing_pocketcasts_profile")
NEW_RELEASES_URL = "https://pocketcasts.com/new-releases"
_TRANSCRIPT_URL = "https://shownotes.pocketcasts.com/generated_transcripts/{podcast}/{episode}.vtt"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
# „Schon geholt"-Gedächtnis: episode-uuid → ISO-Zeitpunkt. Ohne das würde jeder
# Knopfdruck dieselben Folgen NOCHMAL zusammenfassen (das 4-Tage-Fenster hält sie
# ja tagelang in der Liste) → doppelte Beiträge + verschwendetes Kontingent.
_FETCHED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pocketcasts_fetched.json")
_FETCHED_RETENTION_DAYS = 14


def _load_fetched() -> dict:
    import json
    import datetime
    try:
        d = json.loads(open(_FETCHED_PATH, encoding="utf-8").read())
    except Exception:
        return {}
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=_FETCHED_RETENTION_DAYS)).isoformat()
    return {k: v for k, v in d.items() if isinstance(v, str) and v >= cutoff}


def _mark_fetched(episode_uuids) -> None:
    import json
    import datetime
    d = _load_fetched()
    now = datetime.datetime.now().isoformat()
    for u in episode_uuids:
        d[u] = now
    try:
        open(_FETCHED_PATH, "w", encoding="utf-8").write(json.dumps(d))
    except Exception:
        pass


def transcript_url(podcast_uuid: str, episode_uuid: str) -> str:
    return _TRANSCRIPT_URL.format(podcast=podcast_uuid, episode=episode_uuid)


def _vtt_to_text(vtt: str) -> str:
    """Wandelt WebVTT in fortlaufenden Klartext: Zeitstempel-, Cue- und WEBVTT-Zeilen
    raus, direkt aufeinanderfolgende Dubletten (VTT wiederholt Zeilen oft) zusammen."""
    out = []
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith("NOTE"):
            continue
        # Zeitstempel-Zeile: 00:00.280 --> 00:03.000  (auch mit Stunden / Settings)
        if "-->" in line and re.match(r"^\d{1,2}:\d{2}", line):
            continue
        # reine Cue-Nummer
        if line.isdigit():
            continue
        # VTT-Inline-Tags entfernen (<v Speaker>, <00:00.000>, <c> …)
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line:
            continue
        if out and out[-1] == line:  # unmittelbare Dublette
            continue
        out.append(line)
    return " ".join(out)


def _http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ── Feed-Transkripte (Podcasting 2.0) — ZWEITE Quelle neben den generierten ──
# Viele Podcasts (z.B. "Das Thema"/SZ) liefern ihr Transkript SELBST über den
# Feed mit; Pocket Casts listet die URLs im ÖFFENTLICHEN show_notes-JSON
# (Redirect auf shownotes.pocketcasts.com/show_notes/<podcast>/episodes_*.json,
# Feld episodes[].transcripts = [{url, type}]). Ohne diesen Fallback galten
# solche Folgen fälschlich als "kein Transkript" (22.07., Florians Fund).
_SHOW_NOTES_URL = "https://podcast-api.pocketcasts.com/mobile/show_notes/full/{podcast}"
_show_notes_cache = {}  # podcast_uuid -> {episode_uuid: [transcript-dicts]}


def _creator_transcript_urls(podcast_uuid: str, episode_uuid: str) -> list:
    """Feed-Transkript-URLs einer Folge aus dem show_notes-Verzeichnis (per uuid,
    Titel-Fallback bei uuid-Abweichung). WICHTIG: NUR erfolgreiche, nicht-leere
    Antworten cachen. Sonst vergiftet EIN transienter Fehlschlag (Timeout/Rate-
    Limit beim Massenabruf) den Cache dauerhaft → der Podcast gilt bis zum Neustart
    fälschlich als „kein Transkript" (23.07. Florians Aha!-Fund)."""
    import json
    by_uuid, by_title = _show_notes_cache.get(podcast_uuid, (None, None))
    if by_uuid is None:
        try:
            data = json.loads(_http_get(_SHOW_NOTES_URL.format(podcast=podcast_uuid)))
            eps = ((data.get("podcast") or {}).get("episodes")) or []
        except Exception:
            eps = None
        if not eps:
            return []  # Fehlschlag/leer → NICHT cachen, nächstes Mal neu versuchen
        by_uuid = {e.get("uuid"): (e.get("transcripts") or []) for e in eps}
        by_title = {(e.get("title") or "").strip(): (e.get("transcripts") or []) for e in eps if e.get("title")}
        _show_notes_cache[podcast_uuid] = (by_uuid, by_title)
    return by_uuid.get(episode_uuid) or []


def _creator_transcript_urls_by_title(podcast_uuid: str, title: str) -> list:
    """Fallback: Feed-Transkript per Episodentitel (falls die New-Releases-uuid mal
    von der Feed-uuid abweicht). Nutzt den Cache, den _creator_transcript_urls füllt."""
    if podcast_uuid not in _show_notes_cache or not title:
        return []
    _by_uuid, by_title = _show_notes_cache[podcast_uuid]
    return by_title.get(title.strip()) or []


def _json_transcript_to_text(raw: str) -> str:
    """Podcasting-2.0-JSON-Transkript (Liste/segments mit body/text-Feldern)."""
    import json
    try:
        d = json.loads(raw)
    except Exception:
        return ""
    segs = d.get("segments") if isinstance(d, dict) else d
    out = []
    if isinstance(segs, list):
        for s in segs:
            if isinstance(s, dict):
                t = (s.get("body") or s.get("text") or "").strip()
                if t and (not out or out[-1] != t):
                    out.append(t)
    return " ".join(out)


def fetch_transcript(podcast_uuid: str, episode_uuid: str, timeout: int = 30, title: str = None):
    """Holt das Transkript einer Folge: erst Pocket-Casts-generiert (VTT), dann
    Feed-Transkript der Podcast-Macher (VTT/SRT/JSON aus show_notes; per uuid, sonst
    per Titel). Rückgabe: (text, None) bei Erfolg; (None, grund) wenn wirklich keins da."""
    # 1) Pocket-Casts-generiertes Transkript
    try:
        raw = _http_get(transcript_url(podcast_uuid, episode_uuid), timeout)
        # Nur echtes WebVTT akzeptieren — eine 200er-HTML-Seite würde sonst vom
        # Tag-Stripper "entkernt" und als Transkript durchrutschen.
        if raw.lstrip().startswith("WEBVTT"):
            text = _vtt_to_text(raw)
            if len(text) >= 200:
                return text, None
    except urllib.error.HTTPError as _he:
        # 30.08.: Hier wurde JEDER HTTP-Fehler als "kein Transkript vorhanden"
        # gewertet — auch 429 (Drosselung) und 5xx. Solche Folgen bot die App
        # danach zum Archivieren an: sie verschwanden endgültig aus Pocket Casts,
        # ohne je im Briefing gewesen zu sein. Nur 403/404 heissen wirklich
        # "gibt es nicht"; alles andere ist eine Störung und wird gemeldet.
        _code = getattr(_he, "code", 0)
        if _code not in (403, 404):
            fehlerbuch.eintragen("Podcast-Transkript", f"HTTP {_code} von Pocket Casts",
                                 {"titel": (title or "")[:90], "episode": episode_uuid},
                                 "normal")
            return None, (f"Pocket Casts bremst gerade oder ist gestört (HTTP {_code}) — "
                          "später nochmal versuchen. Die Folge bleibt in deiner Liste.")
        pass  # 403/404 = kein generiertes → Feed-Quelle probieren
    except Exception as e:
        return None, ("Abruf-Fehler: %s" % str(e)[:80])

    # 2) Feed-Transkript (vom Podcast selbst): erst per uuid, sonst per Titel
    #    (falls die New-Releases-uuid mal von der Feed-uuid abweicht).
    entries = _creator_transcript_urls(podcast_uuid, episode_uuid)
    if not entries and title:
        entries = _creator_transcript_urls_by_title(podcast_uuid, title)
    def _rank(t):
        ty = (t.get("type") or "").lower()
        return 0 if "vtt" in ty else (1 if "srt" in ty or "subrip" in ty else (2 if "json" in ty else 3))
    for entry in sorted(entries, key=_rank):
        url = entry.get("url")
        ty = (entry.get("type") or "").lower()
        if not url or "html" in ty:
            continue
        try:
            raw = _http_get(url, timeout)
        except Exception:
            continue
        if "json" in ty:
            text = _json_transcript_to_text(raw)
        else:
            text = _vtt_to_text(raw)  # parst VTT und SRT (Zeitstempel-/Cue-Filter)
        if len(text) >= 200:
            return text, None
    return None, ("kein Transkript verfügbar (weder generiert noch im Feed)"
                  if not entries else "Feed-Transkript nicht lesbar")


# ─────────────────────────────────────────────────────────────────────────────
# EPISODEN-LISTE — braucht die eingeloggte Sitzung (Playwright-Profil).
# ─────────────────────────────────────────────────────────────────────────────

# Zieht Episoden-Objekte (uuid + podcastUuid + title) aus dem React-Fiber der
# gerenderten New-Releases-Seite. Am 21.07.2026 live in Florians Chrome verifiziert.
# WICHTIG: Folgen, die Florian schon HÖRT/in der Warteschlange hat, werden
# ausgeschlossen (playingStatus 2/3 oder playedUpTo>0) — das repliziert die
# iPhone-Logik (angespielte Folgen fallen aus New Releases raus; der Mac-Web
# zeigt sie noch). So werden Folgen, die er selbst hören will, nicht zusammengefasst.
_EXTRACT_JS = r"""() => {
  const all = {}, started = new Set();
  for (const el of document.querySelectorAll('*')) {
    for (const k in el) {
      if (!k.startsWith('__reactFiber$')) continue;
      let f = el[k], hops = 0;
      while (f && hops < 6) {
        const p = f.memoizedProps;
        if (p) {
          for (const key of ['episode', 'item', 'ep']) {
            const o = p[key];
            if (o && o.uuid) {
              const pod = o.podcastUuid || o.podcast_uuid || (o.podcast && o.podcast.uuid);
              const ptitle = (o.podcastTitle || (o.podcast && o.podcast.title) || '').slice(0, 100);
              // ⚠️ NUR echte Listen-Zeilen: die tragen podcastTitle UND playingStatus.
              // Objekte OHNE podcastTitle/Status sind die UP-NEXT-WARTESCHLANGE, die
              // die Web-App fürs Player-Handling im Speicher hält — die verfälschte die
              // Liste massiv (67 Queue-Folgen statt 12 echten Zeilen; 24.07. gefunden,
              // erkennbar an „67 Warteschlange" in Florians iOS-Leiste).
              if (pod && ptitle && o.playingStatus !== undefined && o.playingStatus !== null) {
                if (!all[o.uuid]) all[o.uuid] = { episode: o.uuid, podcast: pod, title: (o.title || '').slice(0, 200), published: o.published || '', podcastTitle: ptitle };
                if (o.playingStatus === 2 || o.playingStatus === 3 || (o.playedUpTo && o.playedUpTo > 0)) started.add(o.uuid);
              }
            }
          }
        }
        f = f.return; hops++;
      }
    }
  }
  return Object.values(all).filter(e => !started.has(e.episode));
}"""


def _launch(headless: bool = True):
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    try:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=headless,
            viewport={"width": 1400, "height": 900}, locale="de-DE",
            args=["--disable-blink-features=AutomationControlled"],
        )
    except Exception as exc:
        try:
            p.stop()
        except Exception:
            pass
        # 25.08.: Rohmeldung "Executable doesn't exist at …" sagt niemandem etwas.
        if browser_pfad.ist_browser_fehler(str(exc)):
            raise RuntimeError(browser_pfad.NACHINSTALL_HINWEIS) from exc
        raise
    return p, ctx


def _page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()


def is_logged_in() -> bool:
    """True, wenn das Profil eine gültige Pocket-Casts-Sitzung hat (New Releases
    rendert Episoden statt Login-Seite)."""
    if _token_lesen():
        return bool(list_subscriptions())
    p, ctx = _launch(headless=True)
    try:
        page = _page(ctx)
        page.goto(NEW_RELEASES_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3500)
        if "/user/login" in (page.url or "") or "/podcasts/all" in (page.url or ""):
            return False
        eps = page.evaluate(_EXTRACT_JS)
        return bool(eps)
    except Exception:
        return False
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        p.stop()


def login_interactive(max_wait_s: int = 1800) -> bool:
    """Öffnet ein SICHTBARES Fenster für den Einmal-Login. Florian loggt sich ein,
    danach merkt sich das Profil die Sitzung (wie bei ElevenReader)."""
    p, ctx = _launch(headless=False)
    try:
        page = _page(ctx)
        page.goto(NEW_RELEASES_URL, wait_until="domcontentloaded", timeout=30000)
        t0 = time.time()
        while time.time() - t0 < max_wait_s:
            try:
                page.wait_for_timeout(2000)
                if "/user/login" not in (page.url or ""):
                    eps = page.evaluate(_EXTRACT_JS)
                    if eps:
                        return True
            except Exception:
                # Fenster geschlossen (TargetClosed) o.ä. → sauberer Abbruch statt Traceback
                return False
        return False
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        p.stop()


def list_new_releases(max_episodes: int = 80, max_age_days: float = 0) -> list:
    """Wie `_list_new_releases_once`, aber mit RETRY: ein headless-Ladefehler liefert
    manchmal eine leere Liste (Render-/Netz-Aussetzer, am 22.07. beobachtet). Ein
    leeres Ergebnis würde in der App fälschlich als „kein Login/keine Folgen"
    erscheinen → bis zu 3 Versuche, bevor wir aufgeben."""
    eps = []
    for _attempt in range(3):
        eps = _list_new_releases_once(max_episodes, max_age_days)
        if eps:
            return eps
    return eps


def _list_new_releases_once(max_episodes: int = 80, max_age_days: float = 0) -> list:
    """Ein Ladeversuch der New-Releases-Episoden aus der eingeloggten Sitzung.
    Rückgabe: [{'episode':uuid,'podcast':uuid,'title':str,'published':iso}, ...].

    Zwei teuer gelernte Eigenheiten der Web-Liste (21.07. via Screenshot-Abgleich):
    1. VIRTUALISIERT: nur ~8 Zeilen gleichzeitig im DOM; beim Scrollen unmounten
       Zeilen wieder. Grobe Voll-Sprünge VERPASSEN Folgen → in KLEINEN Schritten
       (900px) scrollen und nach jedem Schritt einsammeln.
    2. Die Web-Liste reicht WEITER ZURÜCK als Florians iPhone-Filter → ältere
       Folgen (z.B. Feed-Wiederveröffentlichungen) mit `max_age_days` abschneiden
       (Default 4 Tage ≈ sein New-Releases-Fenster)."""
    import datetime
    p, ctx = _launch(headless=True)
    try:
        page = _page(ctx)
        # 🎯 WARTESCHLANGEN-AUSSCHLUSS (empirisch bestätigt 24.07.): Florians iOS-Smart-
        # Filter blendet eingereihte Folgen AUS, die Web-Seite zeigt sie aber weiter als
        # Zeilen (Baywatch/Conan/Apokalypse lagen in seiner Queue → 15 statt 12). Wir
        # hören die app-eigene up_next-Antwort ab (Token-frei; ein direkter fetch gibt
        # 401, weil der Bearer nur im Speicher liegt) und filtern deren UUIDs raus.
        up_next = set()

        def _on_response(resp):
            try:
                if "up_next" in resp.url and resp.status == 200:
                    for e in ((resp.json() or {}).get("episodes") or []):
                        if e.get("uuid"):
                            up_next.add(e["uuid"])
            except Exception:
                pass

        page.on("response", _on_response)
        page.goto(NEW_RELEASES_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3500)
        seen = {}
        # ⚠️ TEUER GELERNT (24.07.): NICHT abbrechen, wenn ein paar Scroll-Schritte
        # nichts Neues bringen! Die virtualisierte Liste rendert zwischendurch
        # Leerbereiche → ein „stable"-Abbruch stoppte MITTEN in der Liste, und der
        # Alters-Schnitt warf danach fast alles weg (5 statt ~50 Folgen). Abbruch NUR,
        # wenn der Scroll-Container wirklich am ENDE ist (scrollTop bewegt sich nicht
        # mehr) — oder nach der harten Obergrenze.
        _SCROLL_JS = """() => {
            const sc = [...document.querySelectorAll('*')].find(e => {
                const s = getComputedStyle(e);
                return (s.overflowY === 'auto' || s.overflowY === 'scroll') && e.scrollHeight > e.clientHeight + 200;
            });
            if (sc) { const before = sc.scrollTop; sc.scrollBy(0, 500);
                      return {inner: true, moved: sc.scrollTop !== before, top: sc.scrollTop}; }
            const before = window.scrollY; window.scrollBy(0, 900);
            return {inner: false, moved: window.scrollY !== before, top: window.scrollY};
        }"""
        _at_end = 0
        for _ in range(80):  # harte Obergrenze gegen Endlos-Scroll
            for e in (page.evaluate(_EXTRACT_JS) or []):
                if e.get("episode") and e["episode"] not in seen and e.get("podcast"):
                    seen[e["episode"]] = e
            _res = page.evaluate(_SCROLL_JS) or {}
            page.wait_for_timeout(650)
            if not _res.get("moved"):
                _at_end += 1
                if _at_end >= 3:  # dreimal keine Bewegung mehr → wirklich unten
                    break
            else:
                _at_end = 0
            if len(seen) >= max_episodes:
                break
        # letzte Ernte nach dem finalen Scroll-Schritt
        for e in (page.evaluate(_EXTRACT_JS) or []):
            if e.get("episode") and e["episode"] not in seen and e.get("podcast"):
                seen[e["episode"]] = e
        eps = list(seen.values())
        # Eingereihte Folgen raus (siehe Warteschlangen-Ausschluss oben)
        if up_next:
            _before = len(eps)
            eps = [e for e in eps if e.get("episode") not in up_next]
            if len(eps) != _before:
                print("[pocketcasts] %d Folge(n) in der Warteschlange — ausgeschlossen (wie im iOS-Filter)."
                      % (_before - len(eps)), file=sys.stderr)
        # Alters-Schnitt: nur Folgen der letzten max_age_days behalten
        if max_age_days:
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_age_days)
            kept = []
            for e in eps:
                pub = e.get("published") or ""
                try:
                    dt = datetime.datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    if dt >= cutoff:
                        kept.append(e)
                except Exception:
                    kept.append(e)  # kein Datum lesbar → lieber behalten
            eps = kept
        return eps[:max_episodes]
    except Exception:
        return []
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        p.stop()


def collect_new_releases(progress=None):
    """Voller Ablauf: New-Releases-Liste holen → je Episode das öffentliche
    Transkript ziehen. Rückgabe: (transcripts, skipped, status).
      transcripts = [{'title','podcast','episode','text'}, ...]  (mit Transkript)
      skipped     = [{'title','reason'}, ...]                    (ohne / Fehler / schon geholt)
      status      = 'ok' | 'no_login_or_empty'

    Schon in einem früheren Lauf geholte Folgen werden übersprungen (Gedächtnis
    `.pocketcasts_fetched.json`) — sonst gäbe es bei jedem Knopfdruck Dubletten.
    Markiert wird beim erfolgreichen Holen; scheitert die spätere Zusammenfassung,
    legt die App das Transkript ohnehin zurück ins Einwurf-Feld (nichts verloren)."""
    episodes = list_new_releases()
    transcripts, skipped = [], []
    if not episodes:
        return transcripts, skipped, "no_login_or_empty"
    fetched = _load_fetched()
    for i, ep in enumerate(episodes):
        title = ep.get("title") or "?"
        if progress:
            try:
                progress(i + 1, len(episodes), title)
            except Exception:
                pass
        pt = (ep.get("podcastTitle") or "").strip()
        if ep.get("episode") in fetched:
            skipped.append({"title": title, "podcast_title": pt, "reason": "schon in früherem Lauf geholt"})
            continue
        text, err = fetch_transcript(ep["podcast"], ep["episode"], title=title)
        if text:
            transcripts.append({"title": title, "podcast_title": pt, "podcast": ep["podcast"],
                                "episode": ep["episode"], "text": text})
        else:
            skipped.append({"title": title, "podcast_title": pt, "reason": err})
    # 27.08.: Frueher wurde hier markiert — also schon beim TRANSKRIPT-Download,
    # lange vor der Zusammenfassung. Brach der Lauf danach ab (Limit, Netz,
    # geschlossene Sitzung), galt die Folge als erledigt und verschwand fuer
    # immer aus der Liste, ohne je im Briefing gelandet zu sein. Markiert wird
    # jetzt ausschliesslich nach erfolgreicher Zusammenfassung, ueber
    # summarize_selection_mark() — so wie es der Vorschau-Weg laengst macht.
    return transcripts, skipped, "ok"


# Florians intelligente Playlist auf dem iPhone (Leerzeichen am Ende gehoert dazu!).
IOS_FILTER_TITLE = "All Together "
# Ohne Altersgrenze liefert der Filter den kompletten Rueckstand (369 Folgen).
# 29.09.2026: 4 → 7 Tage. Florian sortiert seine Liste über die Woche auf dem
# iPhone (Uninteressantes archiviert er) — was drinbleibt, will er im Briefing.
# Schon Zusammengefasstes fängt das „Schon geholt"-Gedächtnis ab.
IOS_FILTER_MAX_AGE_DAYS = 7


def _neueste_zuerst(episoden: list, max_alter_tage: float) -> list:
    """Nur Folgen aus dem Zeitfenster, neueste zuerst."""
    import datetime as _dt
    jetzt = _dt.datetime.now(_dt.timezone.utc)

    def _alter(e):
        try:
            d = _dt.datetime.fromisoformat((e.get("published") or "").replace("Z", "+00:00"))
            if not d.tzinfo:
                d = d.replace(tzinfo=_dt.timezone.utc)
            return (jetzt - d).total_seconds() / 86400.0
        except Exception:
            return 1e9

    frisch = [e for e in episoden if _alter(e) <= max_alter_tage]
    return sorted(frisch, key=_alter)


def _aktuelle_folgen():
    """Wie _aktuelle_folgen_roh, aber ohne gesperrte Podcasts.

    05.10.2026: Der Einschlafen-Podcast steht in Florians Liste, gehört aber nie
    ins Briefing. Die Sperrliste pflegt podcast_verbaut.py."""
    eps, status = _aktuelle_folgen_roh()
    try:
        import podcast_verbaut as _pv
        liste = _pv.nie_liste()
        eps = [e for e in eps if not _pv.gesperrt(e.get("podcastTitle") or "", liste)]
    except Exception:
        return eps, status
    if not eps and status == "ok":
        status = "leer"
    return eps, status


def _aktuelle_folgen_roh():
    """Offene Folgen aus Florians iOS-Liste im Zeitfenster, neueste zuerst.

    Rückgabe (episoden, status): "ok" | "leer" (angemeldet, aber nichts offen) |
    "no_login_or_empty". Mit Einmal-Anmeldung NUR über die Schnittstelle — den
    alten Browser-Weg blockt Pocket Casts seit 28.09.2026, er kostete dann nur
    Zeit (drei Anläufe) und lieferte nichts."""
    if _token_lesen():
        if not _api_token():
            return [], "no_login_or_empty"
        res = list_curated_episodes(filter_title=IOS_FILTER_TITLE)
        if res.get("errors"):
            return [], "no_login_or_empty"
        eps = _neueste_zuerst(res.get("episodes") or [], IOS_FILTER_MAX_AGE_DAYS)
        return eps, ("ok" if eps else "leer")
    episodes = []
    try:
        _res = list_curated_episodes(filter_title=IOS_FILTER_TITLE)
        episodes = _neueste_zuerst(_res.get("episodes") or [], IOS_FILTER_MAX_AGE_DAYS)
    except Exception:
        episodes = []
    if not episodes:
        # Rueckfallebene: der bisherige Weg ueber die New-Releases-Seite.
        episodes = list_new_releases()
    return episodes, ("ok" if episodes else "no_login_or_empty")


def preview_new_releases(progress=None):
    """VORSCHAU ohne Zusammenfassen (kostet KEIN 5-Std-Limit — nur HTTP): holt die
    Liste + prüft je Folge, ob ein Transkript da ist, und hält den Volltext gleich
    bereit (kein Zweit-Abruf beim späteren Zusammenfassen).
    Rückgabe: (items, status). items = [{title, podcast_title, podcast, episode,
    text (oder None), has_transcript (bool)}, ...] in Listen-Reihenfolge.
    Überspringt bewusst NICHTS (auch nicht schon Geholtes) — die App klassifiziert
    „schon im Feld" selbst gegen podcast_text."""
    # 27.08.: Erste Wahl ist Florians ECHTE intelligente Playlist. Bisher wurde
    # die Seite "New Releases" abgeschabt — eine ANDERE Liste mit anderen Regeln:
    # sie zeigt angefangene Folgen weiter an, seine iOS-Liste blendet sie aus.
    # Daher standen online regelmaessig mehr Folgen als auf dem iPhone, und
    # aussortierte tauchten wieder auf. Der Filter kommt live aus dem Konto, ist
    # also auch beim Aussortieren sofort aktuell.
    episodes, status = _aktuelle_folgen()
    if not episodes:
        return [], status
    items = []
    for i, ep in enumerate(episodes):
        title = ep.get("title") or "?"
        if progress:
            try:
                progress(i + 1, len(episodes), title)
            except Exception:
                pass
        text, _err = fetch_transcript(ep["podcast"], ep["episode"], title=title)
        items.append({"title": title, "podcast_title": ep.get("podcastTitle") or "",
                      "podcast": ep["podcast"], "episode": ep["episode"],
                      "text": text, "has_transcript": bool(text)})
    return items, "ok"


# 11.08.: Endpunkt abgetastet — "update_episode_archive" (Einzahl) gibt 404,
# richtig ist die MEHRZAHL. Der 404 liess das Archivieren scheitern.
_API_ARCHIVE_URL = "https://api.pocketcasts.com/sync/update_episodes_archive"


BEHALTEN_PATH = os.path.expanduser("~/.briefing_podcasts_behalten.json")


def load_behalten() -> list:
    """Podcasts, die NIE zum Archivieren vorgeschlagen werden.

    22.09.: Florian will nicht jedes Mal neu klicken — manche Podcasts hoert er
    grundsaetzlich selbst ganz (Baywatch Berlin, Lanz + Precht), der Rest kann
    nach der Zusammenfassung weg. Diese Liste haelt das fest.
    """
    try:
        d = json.loads(open(BEHALTEN_PATH, encoding="utf-8").read())
        return sorted({str(x).strip() for x in (d.get("podcasts") or []) if str(x).strip()})
    except Exception:
        return []


def save_behalten(namen) -> list:
    """Behalten-Liste sichern. Gibt die gespeicherte Liste zurueck."""
    sauber = sorted({str(x).strip() for x in (namen or []) if str(x).strip()})
    try:
        with open(BEHALTEN_PATH, "w", encoding="utf-8") as fh:
            json.dump({"podcasts": sauber}, fh, ensure_ascii=False)
    except Exception as exc:
        print(f"Pocket Casts: Behalten-Liste nicht speicherbar: {exc}", file=sys.stderr)
    return sauber


def _behalten_schluessel(text: str) -> str:
    return "".join(c for c in (text or "").lower() if c.isalnum())


def podcast_aus_titel(titel: str) -> str:
    """Holt den Podcast-Namen aus einem Eintrag 'Podcast: Episode'.

    Die Archiv-Frage speichert nur diesen zusammengesetzten Titel — ohne
    Doppelpunkt gibt es keinen Podcast-Namen, dann bleibt der Eintrag leer
    (und faellt damit NIE unter die Behalten-Liste, wird also normal gefragt).
    """
    t = (titel or "")
    return t.split(":", 1)[0].strip() if ":" in t else ""


def auf_behalten_liste(podcast_oder_titel: str, liste=None) -> bool:
    """True, wenn dieser Podcast auf der Behalten-Liste steht.

    Nimmt entweder den reinen Podcast-Namen oder einen 'Podcast: Episode'-Titel.
    Vergleicht ohne Gross-/Kleinschreibung und ohne Sonderzeichen, damit
    'Lanz + Precht' auch 'Lanz+Precht' trifft.
    """
    liste = load_behalten() if liste is None else liste
    if not liste:
        return False
    kand = {_behalten_schluessel(podcast_oder_titel),
            _behalten_schluessel(podcast_aus_titel(podcast_oder_titel))}
    kand.discard("")
    if not kand:
        return False
    merk = {_behalten_schluessel(n) for n in liste}
    return bool(kand & merk)


def archive_episodes(eps, token: str = None) -> int:
    """Archiviert Folgen in Pocket Casts (= aus der Liste nehmen, wie am Handy).

    eps: Liste von {"episode": uuid, "podcast": uuid}. Gibt die Anzahl zurueck.
    10.08.: Florians Wunsch — nach den Zusammenfassungen fragt die App, welche
    Folgen aus der Liste sollen; manche will er ja noch selbst ganz hoeren.
    """
    eintraege = [{"uuid": e["episode"], "podcast": e["podcast"]}
                 for e in (eps or []) if e.get("episode") and e.get("podcast")]
    if not eintraege:
        return 0
    token = token or get_api_token()
    if not token:
        raise RuntimeError("Kein Pocket-Casts-Login — bitte einmal neu anmelden.")
    import urllib.request as _ur
    req = _ur.Request(_API_ARCHIVE_URL,
                      data=json.dumps({"episodes": eintraege, "archive": True}).encode("utf-8"),
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"},
                      method="POST")
    with _ur.urlopen(req, timeout=30) as r:
        if r.status not in (200, 204):
            raise RuntimeError(f"Pocket Casts antwortete mit HTTP {r.status}.")
    return len(eintraege)


def summarize_selection_mark(episode_uuids):
    """Merkt ausgewählte Episoden als „geholt" (nach dem Absenden zur Zusammenfassung),
    damit sie beim nächsten Mal nicht doppelt kommen — genutzt vom Vorschau-Flow."""
    if episode_uuids:
        _mark_fetched(list(episode_uuids))


PODCASTS_ALL_URL = "https://pocketcasts.com/podcasts/all"


# ── Offizielle Anmeldung (28.09.2026) ───────────────────────────────────────
# Seit Ende September blockt Pocket Casts den unsichtbaren Browser (CloudFront
# „403 Request blocked"), der Schlüssel liess sich nicht mehr aus der Web-App
# mitlesen. Die Schnittstelle, über die auch die Handy-App läuft, antwortet
# normal. Florian meldet sich EINMAL selbst an; gespeichert werden nur die zwei
# Schlüssel aus der Antwort, NIE das Passwort. Den kurzlebigen erneuert der
# langlebige von selbst. Die Datei liegt bewusst ausserhalb des (öffentlichen)
# Repos und ist nur für Florian lesbar.
_TOKEN_PATH = os.path.expanduser("~/.briefing_pocketcasts_token.json")
_API_LOGIN_URL = "https://api.pocketcasts.com/user/login_pocket_casts"
_API_REFRESH_URL = "https://api.pocketcasts.com/user/token"
_API_SUBS_URL = "https://api.pocketcasts.com/user/podcast/list"
_API_SCOPE = "webplayer"


def _token_lesen() -> dict:
    try:
        with open(_TOKEN_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _token_schreiben(d: dict) -> None:
    tmp = _TOKEN_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _TOKEN_PATH)


def _api_post(url: str, body: dict, token: str = None, timeout: int = 25):
    """POST an die Pocket-Casts-Schnittstelle → (HTTP-Status, JSON-Objekt).
    Status 0 heisst: keine Verbindung."""
    headers = {"Content-Type": "application/json", "User-Agent": _UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, (exc.read() or b"")
    except Exception:
        return 0, {}
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    return status, (data if isinstance(data, dict) else {})


def _token_aus_antwort(data: dict, alt: dict) -> dict:
    """Speicherbarer Eintrag aus einer Anmelde- oder Erneuerungs-Antwort."""
    zugang = data.get("accessToken") or data.get("token")
    if not zugang:
        return {}
    try:
        dauer = int(data.get("expiresIn") or 0)
    except (TypeError, ValueError):
        dauer = 0
    jetzt = time.strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "access_token": zugang,
        "refresh_token": data.get("refreshToken") or alt.get("refresh_token") or "",
        "expires_at": (time.time() + dauer) if dauer > 0 else 0,
        "email": data.get("email") or alt.get("email") or "",
        "scope": alt.get("scope") or _API_SCOPE,
        "seit": alt.get("seit") or jetzt,
        "erneuert": jetzt,
    }


def api_login(email: str, password: str) -> dict:
    """Einmal-Anmeldung über die offizielle Schnittstelle.

    Gibt {"email": …} zurück oder wirft RuntimeError mit einer Meldung, die man
    so anzeigen kann. Das Passwort geht nur an Pocket Casts und wird nirgends
    abgelegt."""
    email = (email or "").strip()
    if not email or not password:
        raise RuntimeError("Bitte E-Mail und Passwort eingeben.")
    status, data = _api_post(_API_LOGIN_URL, {"email": email, "password": password,
                                              "scope": _API_SCOPE})
    if status == 0:
        raise RuntimeError("Pocket Casts ist gerade nicht erreichbar — Internet prüfen.")
    tok = _token_aus_antwort(data, {"email": email}) if status == 200 else {}
    if not tok:
        kennung = str(data.get("errorMessageId") or "")
        if "email" in kennung:
            grund = "Diese E-Mail-Adresse kennt Pocket Casts nicht."
        elif "password" in kennung:
            grund = "Das Passwort stimmt nicht."
        else:
            grund = data.get("errorMessage") or data.get("error_description") or f"HTTP {status}"
        raise RuntimeError(f"Anmeldung abgelehnt: {grund}")
    _token_schreiben(tok)
    return {"email": tok["email"]}


def api_abmelden() -> None:
    try:
        os.remove(_TOKEN_PATH)
    except FileNotFoundError:
        pass


def api_status() -> dict:
    """Anmeldestand nur aus der Datei (ohne Netz): {angemeldet, email, hinweis}."""
    d = _token_lesen()
    if not d.get("access_token"):
        return {"angemeldet": False, "email": "", "hinweis": "Noch nicht angemeldet"}
    if d.get("verfallen"):
        return {"angemeldet": False, "email": d.get("email") or "",
                "hinweis": "Anmeldung abgelaufen — bitte neu anmelden"}
    return {"angemeldet": True, "email": d.get("email") or "", "hinweis": ""}


def _api_token() -> str:
    """Gültiger Zugangsschlüssel aus der Einmal-Anmeldung, bei Bedarf erneuert.
    Leer, wenn nicht angemeldet oder die Anmeldung verfallen ist."""
    d = _token_lesen()
    if not d.get("access_token") or d.get("verfallen"):
        return ""
    ablauf = float(d.get("expires_at") or 0)
    if not ablauf or ablauf - 120 > time.time():
        return d["access_token"]
    import fcntl
    # Sperre: App und Nachtlauf könnten gleichzeitig erneuern — ein bereits
    # eingelöster Erneuerungs-Schlüssel gilt danach womöglich nicht mehr.
    with open(_TOKEN_PATH + ".lock", "w") as sperre:
        fcntl.flock(sperre, fcntl.LOCK_EX)
        d = _token_lesen()
        ablauf = float(d.get("expires_at") or 0)
        if d.get("access_token") and not d.get("verfallen") and ablauf - 120 > time.time():
            return d["access_token"]  # ein anderer Lauf hat schon erneuert
        if not d.get("refresh_token"):
            d["verfallen"] = "abgelaufen, kein Erneuerungs-Schlüssel"
            _token_schreiben(d)
            return ""
        status, data = _api_post(_API_REFRESH_URL, {
            "grant_type": "refresh_token", "refresh_token": d["refresh_token"],
            "scope": d.get("scope") or _API_SCOPE})
        neu = _token_aus_antwort(data, d) if status == 200 else {}
        if neu:
            _token_schreiben(neu)
            return neu["access_token"]
        if status in (400, 401, 403):
            d["verfallen"] = data.get("error_description") or data.get("error") or f"HTTP {status}"
            _token_schreiben(d)
            return ""
        return ""  # Netzaussetzer: nächster Aufruf versucht es erneut


def _abos_aus_antwort(data) -> list:
    pods = data.get("podcasts") if isinstance(data, dict) else None
    if not isinstance(pods, list):
        return []
    out = []
    for entry in pods:
        if not isinstance(entry, dict):
            continue
        uuid = (entry.get("uuid") or "").strip()
        title = (entry.get("title") or "").strip()
        if uuid and title:
            out.append({
                "uuid": uuid,
                "title": title,
                "author": (entry.get("author") or "").strip(),
                "site": (entry.get("url") or "").strip(),
            })
    return out


def list_subscriptions(timeout_s: int = 45) -> list:
    """Die abonnierten Podcasts direkt aus dem eingeloggten Konto.

    Erste Wahl ist die offizielle Schnittstelle mit der Einmal-Anmeldung. Nur
    ohne sie fällt die Funktion auf den alten Weg zurück: Abo-Seite im
    unsichtbaren Browser laden und die Antwort abfangen, die die Web-App ohnehin
    lädt (seit 28.09.2026 von Pocket Casts geblockt).

    Rückgabe: Liste aus {uuid, title, author, site}. Leere Liste heisst
    „nicht ermittelbar" (nicht angemeldet, offline) — NIE „keine Abos".
    """
    if _token_lesen():
        tok = _api_token()
        if not tok:
            return []
        status, data = _api_post(_API_SUBS_URL, {"v": 1}, token=tok, timeout=timeout_s)
        return _abos_aus_antwort(data) if status == 200 else []

    box = {}

    def _on_resp(resp):
        if "/user/podcast/list" in (resp.url or ""):
            try:
                box["data"] = resp.json()
            except Exception:
                pass

    p, ctx = _launch(headless=True)
    try:
        page = _page(ctx)
        page.on("response", _on_resp)
        page.goto(PODCASTS_ALL_URL, wait_until="domcontentloaded", timeout=timeout_s * 1000)
        deadline = time.time() + 20
        while "data" not in box and time.time() < deadline:
            page.wait_for_timeout(500)
    except Exception:
        return []
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        p.stop()

    return _abos_aus_antwort(box.get("data") or {})


_API_EPISODES_URL = "https://api.pocketcasts.com/user/podcast/episodes/bookmarks"
_API_PODCAST_FULL = "https://podcast-api.pocketcasts.com/podcast/full/{uuid}"


def get_api_token(timeout_s: int = 45):
    """Greift den API-Schlüssel aus der angemeldeten Sitzung ab.

    Die Web-App holt sich den Schlüssel selbst über /user/token; wir lesen ihn
    aus dem Authorization-Header eines Aufrufs mit, den sie ohnehin macht. Damit
    lassen sich die Statusabfragen danach direkt und parallel stellen, statt für
    jeden der ~160 Podcasts eine Seite zu laden (Sekunden statt 20 Minuten).

    28.09.2026: Zuerst die Einmal-Anmeldung. Gibt es sie, aber sie ist
    verfallen, gar nicht erst den (geblockten) Browser starten.
    """
    if _token_lesen():
        return _api_token() or None
    box = {}

    def _cap(req):
        auth = req.headers.get("authorization", "")
        if auth.startswith("Bearer ") and "token" not in box:
            box["token"] = auth[7:]

    p, ctx = _launch(headless=True)
    try:
        page = _page(ctx)
        page.on("request", _cap)
        page.goto(PODCASTS_ALL_URL, wait_until="domcontentloaded", timeout=timeout_s * 1000)
        deadline = time.time() + 20
        while "token" not in box and time.time() < deadline:
            page.wait_for_timeout(400)
    except Exception:
        return None
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        p.stop()
    return box.get("token")


def _episode_user_state(podcast_uuid: str, token: str, timeout: int = 25) -> dict:
    """Nutzerstatus je Episode: {episode_uuid: {...}}. Nur Folgen, die angefasst
    wurden, haben einen Eintrag — unberührte fehlen hier (und sind damit offen)."""
    import json as _json
    body = _json.dumps({"uuid": podcast_uuid}).encode()
    req = urllib.request.Request(_API_EPISODES_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read())
    except Exception:
        return {}
    return {e["uuid"]: e for e in (data.get("episodes") or []) if isinstance(e, dict) and e.get("uuid")}


def _podcast_catalogue(podcast_uuid: str, timeout: int = 25) -> list:
    """Alle Folgen eines Podcasts (öffentlich, ohne Anmeldung)."""
    import gzip as _gzip
    import json as _json
    req = urllib.request.Request(_API_PODCAST_FULL.format(uuid=podcast_uuid),
                                 headers={"User-Agent": _UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            raw = _gzip.decompress(raw)
        return _json.loads(raw).get("podcast", {}).get("episodes") or []
    except Exception:
        return []


_API_PLAYLIST_URL = "https://api.pocketcasts.com/user/playlist/list"


def list_filters(token: str = None) -> list:
    """Die in Pocket Casts angelegten Filter (intelligente Playlisten), so wie sie
    auf dem iPhone stehen — inklusive ihrer Regeln."""
    import json as _json
    token = token or get_api_token()
    if not token:
        return []
    req = urllib.request.Request(_API_PLAYLIST_URL, data=_json.dumps({"v": 1}).encode(),
                                 method="POST", headers={
                                     "Authorization": f"Bearer {token}",
                                     "Content-Type": "application/json", "User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = _json.loads(resp.read())
    except Exception:
        return []
    return [p for p in (data.get("playlists") or [])
            if isinstance(p, dict) and not p.get("isDeleted") and p.get("title")]


def _episode_matches_filter(state: dict, published: str, flt: dict, now_iso: str) -> bool:
    """Wendet die Regeln EINES Pocket-Casts-Filters auf eine Episode an.

    state ist der gespeicherte Nutzerstatus (oder None = nie angefasst, gilt als
    ungespielt). Bewusst nur die Regeln, die fürs Briefing zählen — Download-
    Status (downloaded/notDownloaded) ist hier bedeutungslos, weil wir nicht
    herunterladen, sondern Transkripte holen.
    """
    if state and state.get("isDeleted"):
        return False                                   # archiviert -> nie

    status = (state or {}).get("playingStatus") or 1   # 1 ungespielt, 2 angefangen, 3 fertig
    if status == 1 and not flt.get("unplayed", True):
        return False
    if status == 2 and not flt.get("partiallyPlayed", True):
        return False
    if status == 3 and not flt.get("finished", False):
        return False

    if flt.get("starred") and not (state or {}).get("starred"):
        return False

    hours = flt.get("filterHours") or 0                # 0 = kein Zeitlimit
    if hours and published:
        import datetime as _dt
        try:
            pub = _dt.datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=_dt.timezone.utc)
            age_h = (_dt.datetime.now(_dt.timezone.utc) - pub).total_seconds() / 3600.0
            if age_h > hours:
                return False
        except Exception:
            pass

    if flt.get("filterDuration"):
        secs = (state or {}).get("duration") or 0
        longer, shorter = flt.get("longerThan") or 0, flt.get("shorterThan") or 0
        if longer and secs and secs < longer * 60:
            return False
        if shorter and secs and secs > shorter * 60:
            return False
    return True


def list_curated_episodes(podcasts=None, token: str = None, max_workers: int = 12,
                          progress=None, filter_title: str = "New Releases") -> dict:
    """Die Folgen, die Florian auf dem Handy stehen gelassen hat — altersunabhängig.

    Rechnung je Podcast: alles im Katalog, minus archiviert, minus angefangen.
    Unberührte Folgen haben gar keinen Statuseintrag und gelten damit als offen —
    genau das ist die Auswahl, die in der intelligenten Playlist auftaucht.

    Returns {"episodes": [...], "scanned": int, "errors": [...]}.
    """
    from concurrent.futures import ThreadPoolExecutor

    subs = podcasts if podcasts is not None else list_subscriptions()
    if not subs:
        return {"episodes": [], "scanned": 0, "errors": ["keine Abo-Liste (nicht angemeldet?)"]}

    token = token or get_api_token()
    if not token:
        return {"episodes": [], "scanned": 0, "errors": ["kein API-Schlüssel aus der Sitzung"]}

    # Die Regeln kommen aus dem echten Filter im Pocket-Casts-Konto, nicht aus
    # einer nachgebauten Annahme. Fehlt der Filter, gilt eine konservative
    # Ersatzregel (ungespielt + angefangen, nicht beendet, kein Zeitlimit).
    flt = next((f for f in list_filters(token) if f.get("title") == filter_title), None)
    if flt is None:
        flt = {"unplayed": True, "partiallyPlayed": True, "finished": False,
               "filterHours": 0, "allPodcasts": True}
        used_filter = f"{filter_title} (nicht gefunden — Ersatzregel)"
    else:
        used_filter = flt.get("title")
        if not flt.get("allPodcasts") and flt.get("podcastUuids"):
            # 27.08.: podcastUuids kommt als KOMMA-ZEICHENKETTE, nicht als Liste.
            # set() darauf ergibt eine Menge einzelner Buchstaben — es passte kein
            # einziges Abo, die Funktion lieferte immer 0 Folgen und wurde deshalb
            # nie benutzt. Beide Formen werden jetzt akzeptiert.
            _roh = flt["podcastUuids"]
            allowed = ({x.strip() for x in _roh.split(",") if x.strip()}
                       if isinstance(_roh, str) else set(_roh))
            subs = [s for s in subs if s["uuid"] in allowed]

    done = {"n": 0}

    def _one(sub):
        state = _episode_user_state(sub["uuid"], token)
        catalogue = _podcast_catalogue(sub["uuid"])
        open_eps = []
        for ep in catalogue:
            st = state.get(ep.get("uuid"))
            if not _episode_matches_filter(st, ep.get("published"), flt, ""):
                continue
            # Feldnamen bewusst wie bei list_new_releases — dann kann die
            # bestehende Transkript-/Zusammenfassungs-Pipeline unverändert damit
            # arbeiten (podcast = Podcast-UUID, episode = Episoden-UUID).
            open_eps.append({
                "podcast": sub["uuid"], "episode": ep.get("uuid"),
                "podcastTitle": sub["title"], "title": ep.get("title") or "",
                "published": ep.get("published") or "", "duration": ep.get("duration"),
            })
        done["n"] += 1
        if progress:
            try:
                progress(done["n"], len(subs), sub["title"])
            except Exception:
                pass
        return open_eps

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_one, subs))

    episodes = [e for group in results for e in group]
    episodes.sort(key=lambda e: e.get("published") or "", reverse=True)
    return {"episodes": episodes, "scanned": len(subs), "filter": used_filter, "errors": []}


DEEP_PODCASTS_PATH = os.path.expanduser("~/.briefing_pocketcasts_deep.json")


def load_deep_podcasts() -> list:
    """Podcasts, für die zusätzlich der Rückkatalog durchsucht wird."""
    import json as _json
    try:
        d = _json.loads(open(DEEP_PODCASTS_PATH, encoding="utf-8").read())
        return [t for t in d.get("titles", []) if isinstance(t, str)]
    except Exception:
        return []


def save_deep_podcasts(titles) -> None:
    import json as _json
    try:
        with open(DEEP_PODCASTS_PATH, "w", encoding="utf-8") as fh:
            _json.dump({"titles": sorted(set(titles))}, fh, ensure_ascii=False)
    except Exception:
        pass


def preview_selection(deep_titles=None, progress=None):
    """Der praktikable Mittelweg zwischen "zu wenig" und "viel zu viel".

    Warum nicht einfach der Filter? Pocket Casts rechnet Filter auf dem GERÄT
    aus, über die dort lokal bekannten Folgen. Der Server gibt die Regeln her,
    aber nicht das Ergebnis — und er kennt auch nicht, welche alten Folgen die
    App lokal führt. Ein serverseitiger Nachbau sammelt darum zwangsläufig zu
    viel (gemessen: 219 statt 19).

    Deshalb zweigleisig:
      - Aktuelles kommt aus Pocket Casts' eigener New-Releases-Liste (exakt).
      - Für benannte Podcasts (selten sendend, aber wichtig) wird zusätzlich der
        Rückkatalog nach offenen Folgen durchsucht.
    Rückgabe wie preview_new_releases: (items, status).
    """
    from concurrent.futures import ThreadPoolExecutor

    deep_titles = list(deep_titles if deep_titles is not None else load_deep_podcasts())
    # 29.09.2026: Statt der (geblockten) New-Releases-Seite dieselbe Quelle wie
    # „Pocket Casts prüfen" — sonst meldete dieser Knopf trotz Anmeldung
    # „Kein Pocket-Casts-Login".
    episodes, status = _aktuelle_folgen()
    seen = {e.get("episode") for e in episodes}

    if deep_titles:
        subs = list_subscriptions()
        token = get_api_token()
        wanted = [s for s in subs if s["title"] in deep_titles]
        if token and wanted:
            data = list_curated_episodes(podcasts=wanted, token=token)
            for e in data.get("episodes") or []:
                if e.get("episode") not in seen:
                    seen.add(e.get("episode"))
                    episodes.append(e)

    if not episodes:
        return [], status

    done = {"n": 0}

    def _one(ep):
        title = ep.get("title") or "?"
        text, _err = fetch_transcript(ep["podcast"], ep["episode"], title=title)
        done["n"] += 1
        if progress:
            try:
                progress(done["n"], len(episodes), title)
            except Exception:
                pass
        return {"title": title, "podcast_title": ep.get("podcastTitle") or "",
                "podcast": ep["podcast"], "episode": ep["episode"],
                "published": ep.get("published") or "",
                "text": text, "has_transcript": bool(text)}

    with ThreadPoolExecutor(max_workers=10) as ex:
        items = list(ex.map(_one, episodes))
    return items, "ok"


def preview_curated(limit: int = 250, progress=None, filter_title: str = "New Releases"):
    """Wie preview_new_releases, aber Quelle ist die eigene Auswahl (siehe
    list_curated_episodes) statt der zeitlich begrenzten New-Releases-Liste.

    Transkripte werden parallel geprüft — bei ~200 Folgen wäre seriell zu langsam.
    `limit` deckelt die Prüfung auf die neuesten N Folgen; der Rest bleibt in der
    Liste, nur ohne vorab geladenen Volltext. Standard ist bewusst hoch: die
    Auswahl enthält gezielt auch ältere Folgen, die sonst aus der Prüfung fielen.
    Rückgabe wie preview_new_releases: (items, status).
    """
    from concurrent.futures import ThreadPoolExecutor

    data = list_curated_episodes(filter_title=filter_title)
    episodes = data.get("episodes") or []
    if not episodes:
        return [], (data.get("errors") or ["no_login_or_empty"])[0]

    head, tail = episodes[:limit], episodes[limit:]
    done = {"n": 0}

    def _one(ep):
        text, _err = fetch_transcript(ep["podcast"], ep["episode"], title=ep.get("title"))
        done["n"] += 1
        if progress:
            try:
                progress(done["n"], len(head), ep.get("title") or "?")
            except Exception:
                pass
        return {"title": ep.get("title") or "?", "podcast_title": ep.get("podcastTitle") or "",
                "podcast": ep["podcast"], "episode": ep["episode"],
                "published": ep.get("published") or "",
                "text": text, "has_transcript": bool(text)}

    with ThreadPoolExecutor(max_workers=10) as ex:
        items = list(ex.map(_one, head))

    for ep in tail:
        items.append({"title": ep.get("title") or "?", "podcast_title": ep.get("podcastTitle") or "",
                      "podcast": ep["podcast"], "episode": ep["episode"],
                      "published": ep.get("published") or "",
                      "text": None, "has_transcript": False})
    return items, "ok"


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "--curated":
        r = list_curated_episodes(progress=lambda i, n, t: print(f"  {i}/{n} {t[:40]}", file=sys.stderr))
        print(f"{len(r['episodes'])} offene Folgen aus {r['scanned']} Podcasts")
        for e in r["episodes"][:40]:
            print(" ", str(e["published"])[:10], "|", e["podcast"][:26], "|", e["title"][:48])
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--subs":
        for s in list_subscriptions():
            print(s["uuid"], "|", s["title"][:45], "|", s["author"][:30])
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--login":
        # 28.09.2026: Einmal-Anmeldung über die offizielle Schnittstelle. Das
        # Passwort wird beim Tippen nicht angezeigt und nicht gespeichert.
        import getpass
        print("\n🎧 Pocket-Casts-Anmeldung für die Briefing-App")
        print("   (Passwort erscheint beim Tippen nicht und wird nicht gespeichert.)\n")
        try:
            _mail = input("E-Mail: ").strip()
            _r = api_login(_mail, getpass.getpass("Passwort: "))
        except (KeyboardInterrupt, EOFError):
            print("\nAbgebrochen.")
            sys.exit(1)
        except RuntimeError as exc:
            print(f"\n❌ {exc}")
            sys.exit(1)
        _abos = list_subscriptions()
        _filter = [f.get("title") for f in list_filters()]
        print(f"\n✅ Angemeldet als {_r['email']} — {len(_abos)} Abos gefunden.")
        if IOS_FILTER_TITLE in _filter:
            print(f"   Deine Liste „{IOS_FILTER_TITLE.strip()}“ ist da. Du kannst das Fenster schließen.")
        else:
            print("   Listen im Konto: " + ", ".join(f"„{t.strip()}“" for t in _filter))
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--login-browser":
        ok = login_interactive()
        print("LOGIN_OK" if ok else "LOGIN_ABGEBROCHEN")
        sys.exit(0 if ok else 1)
    if len(sys.argv) >= 2 and sys.argv[1] == "--logout":
        api_abmelden()
        print("Abgemeldet.")
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--status":
        print("ANGEMELDET" if is_logged_in() else "NICHT_ANGEMELDET")
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--list":
        for e in list_new_releases():
            print(e["podcast"], e["episode"], (e.get("title") or "")[:50])
        sys.exit(0)
    if len(sys.argv) == 3:
        t, err = fetch_transcript(sys.argv[1], sys.argv[2])
        if t:
            print("OK, %d Zeichen, ~%d Wörter\n---\n%s..." % (len(t), len(t.split()), t[:400]))
        else:
            print("KEIN TRANSKRIPT:", err)
