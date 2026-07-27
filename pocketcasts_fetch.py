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
"""
import os
import re
import sys
import time
import urllib.request

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
    except urllib.error.HTTPError:
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
    ctx = p.chromium.launch_persistent_context(
        PROFILE_DIR, headless=headless,
        viewport={"width": 1400, "height": 900}, locale="de-DE",
        args=["--disable-blink-features=AutomationControlled"],
    )
    return p, ctx


def _page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()


def is_logged_in() -> bool:
    """True, wenn das Profil eine gültige Pocket-Casts-Sitzung hat (New Releases
    rendert Episoden statt Login-Seite)."""
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
    if transcripts:
        _mark_fetched([t["episode"] for t in transcripts])
    return transcripts, skipped, "ok"


def preview_new_releases(progress=None):
    """VORSCHAU ohne Zusammenfassen (kostet KEIN 5-Std-Limit — nur HTTP): holt die
    Liste + prüft je Folge, ob ein Transkript da ist, und hält den Volltext gleich
    bereit (kein Zweit-Abruf beim späteren Zusammenfassen).
    Rückgabe: (items, status). items = [{title, podcast_title, podcast, episode,
    text (oder None), has_transcript (bool)}, ...] in Listen-Reihenfolge.
    Überspringt bewusst NICHTS (auch nicht schon Geholtes) — die App klassifiziert
    „schon im Feld" selbst gegen podcast_text."""
    episodes = list_new_releases()
    if not episodes:
        return [], "no_login_or_empty"
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


def summarize_selection_mark(episode_uuids):
    """Merkt ausgewählte Episoden als „geholt" (nach dem Absenden zur Zusammenfassung),
    damit sie beim nächsten Mal nicht doppelt kommen — genutzt vom Vorschau-Flow."""
    if episode_uuids:
        _mark_fetched(list(episode_uuids))


PODCASTS_ALL_URL = "https://pocketcasts.com/podcasts/all"


def list_subscriptions(timeout_s: int = 45) -> list:
    """Die abonnierten Podcasts direkt aus dem eingeloggten Konto.

    Wir rufen die API nicht selbst auf — sie verlangt einen JWT, den sich die
    Web-App erst über /user/token holt. Stattdessen laden wir die Abo-Seite und
    fangen die Antwort ab, die die App ohnehin lädt. Das ist unabhängig davon,
    wie Pocket Casts seine Anmeldung intern regelt.

    Rückgabe: Liste aus {uuid, title, author, site}. Leere Liste heisst
    „nicht ermittelbar" (nicht angemeldet, offline) — NIE „keine Abos".
    """
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

    data = box.get("data") or {}
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


_API_EPISODES_URL = "https://api.pocketcasts.com/user/podcast/episodes/bookmarks"
_API_PODCAST_FULL = "https://podcast-api.pocketcasts.com/podcast/full/{uuid}"


def get_api_token(timeout_s: int = 45):
    """Greift den API-Schlüssel aus der angemeldeten Sitzung ab.

    Die Web-App holt sich den Schlüssel selbst über /user/token; wir lesen ihn
    aus dem Authorization-Header eines Aufrufs mit, den sie ohnehin macht. Damit
    lassen sich die Statusabfragen danach direkt und parallel stellen, statt für
    jeden der ~160 Podcasts eine Seite zu laden (Sekunden statt 20 Minuten).
    """
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


def list_curated_episodes(podcasts=None, token: str = None, max_workers: int = 12,
                          progress=None) -> dict:
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

    done = {"n": 0}

    def _one(sub):
        state = _episode_user_state(sub["uuid"], token)
        catalogue = _podcast_catalogue(sub["uuid"])
        open_eps = []
        for ep in catalogue:
            st = state.get(ep.get("uuid"))
            if st is None:
                pass                                   # nie angefasst -> offen
            elif st.get("isDeleted"):
                continue                               # archiviert -> raus
            elif st.get("playingStatus") in (2, 3) or (st.get("playedUpTo") or 0) > 0:
                continue                               # angefangen/gehört -> raus
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
    return {"episodes": episodes, "scanned": len(subs), "errors": []}


def preview_curated(limit: int = 250, progress=None):
    """Wie preview_new_releases, aber Quelle ist die eigene Auswahl (siehe
    list_curated_episodes) statt der zeitlich begrenzten New-Releases-Liste.

    Transkripte werden parallel geprüft — bei ~200 Folgen wäre seriell zu langsam.
    `limit` deckelt die Prüfung auf die neuesten N Folgen; der Rest bleibt in der
    Liste, nur ohne vorab geladenen Volltext. Standard ist bewusst hoch: die
    Auswahl enthält gezielt auch ältere Folgen, die sonst aus der Prüfung fielen.
    Rückgabe wie preview_new_releases: (items, status).
    """
    from concurrent.futures import ThreadPoolExecutor

    data = list_curated_episodes()
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
        ok = login_interactive()
        print("LOGIN_OK" if ok else "LOGIN_ABGEBROCHEN")
        sys.exit(0 if ok else 1)
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
