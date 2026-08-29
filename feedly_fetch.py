"""Feedly-Merkliste ("Read later") automatisch ins Briefing holen.

Florian kuratiert im Feedly: alles Interessante bekommt das Lesezeichen. Dieses
Modul holt die Merkliste, laedt die Volltexte (auch hinter Paywall, ueber ein
persistentes Browser-Profil mit seinen Zeitungs-Logins) und liefert sie im
gleichen "mmm"-Blockformat wie TabClip — direkt einsetzbar im Paywall-Feld.

Technik: Playwright-Chromium mit PERSISTENTEM Profil (~/.briefing_news_profile),
gleiches Muster wie reader_upload.py. Einmal-Login im sichtbaren Fenster
(--login) bei Feedly, GEA und SWP/Tagblatt, danach laeuft alles headless.

WICHTIG — leises Scheitern ist verboten: Wenn ein Zeitungs-Login abgelaufen ist,
bekommt man statt Volltext nur den Anriss. Solche Treffer werden erkannt
(_detect_paywall_teaser, gleiche Logik wie in der TabClip-Extension), NICHT ins
Briefing uebernommen und laut gemeldet. Sie bleiben ausserdem in der Merkliste
stehen, damit nach dem Neu-Anmelden nichts verloren ist.

Das Feedly-Zugangstoken wird ausschliesslich IM Browser verwendet (fetch im
Seitenkontext) und niemals ausgelesen, geloggt oder gespeichert.

CLI:  python3 feedly_fetch.py --login    (sichtbares Fenster, einmalig)
      python3 feedly_fetch.py --status   (angemeldet? Exit 0/1)
      python3 feedly_fetch.py --list     (Merkliste zeigen, nichts laden)
      python3 feedly_fetch.py --fetch    (Volltexte holen, Blockformat ausgeben)
"""

import html
import json
import os
import re
import sys
import browser_pfad  # muss VOR jedem Playwright-Import stehen (25.08.)
import urllib.parse

PROFILE_DIR = os.path.expanduser("~/.briefing_news_profile")
FEEDLY_URL = "https://feedly.com/i/saved"
BLOCK_SEPARATOR = "\n\nmmm\n\n"

# Ab dieser Laenge gilt der im RSS mitgelieferte Text als vollwertig — dann
# muss die Artikelseite gar nicht erst geoeffnet werden (schneller, keine
# Login-Abhaengigkeit). Deutsche Nachrichten-Feeds liefern meist nur Anrisse,
# deshalb greift das eher selten.
FEED_TEXT_MIN_CHARS = 1500

# Zeitungen, bei denen ein Login noetig ist. Nur fuer die Anzeige/Statistik —
# ob ein Text wirklich abgeschnitten ist, entscheidet die Teaser-Erkennung.
PAYWALL_DOMAINS = (
    "gea.de", "swp.de", "tagblatt.de", "faz.net", "sueddeutsche.de",
    "zeit.de", "handelsblatt.com", "nytimes.com", "spiegel.de",
    "kontextwochenzeitung.de", "athletic.com", "wsj.com", "ft.com",
)

# Zeitungen OHNE Abo — dort kommt zwangslaeufig nur der Anriss. 26.08.: Florian
# hat kein Spiegel-Abo; seine beiden Spiegel-Artikel wurden jedes Mal als
# "Problem — Login pruefen" gemeldet, obwohl es nichts zu pruefen gab. Solche
# Treffer werden jetzt ruhig uebersprungen statt als Handlungsbedarf gemeldet.
KEIN_ZUGANG_DOMAINS = (
    "spiegel.de",
)


def _ohne_zugang(url: str) -> bool:
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in KEIN_ZUGANG_DOMAINS)


# Spiegel der TabClip-Extension (extensions/tabclip/shared.js) — beide Listen
# bei Aenderungen synchron halten.
PAYWALL_MARKERS = (
    "sie haben bereits ein abo", "jetzt weiterlesen", "kennenlernabo", "jahresabo",
    "monatsabo", "probeabo", "kostenlos digitalzugang freischalten", "unbegrenzt lesen auf",
    "swp+", "tagblatt+", "sie wollen mehr", "digital-abo", "digitalabo",
    "anmelden und weiterlesen", "registrieren und weiterlesen", "jetzt registrieren",
    "jetzt anmelden", "bereits abonnent", "schon abonnent", "sind sie bereits abonnent",
    "jetzt kostenlos testen", "angebot auswählen", "zugriff auf alle artikel",
    "alle artikel frei lesen", "premium-artikel", "exklusiv für abonnenten",
    "artikel für abonnenten",
)


# Ab dieser Laenge ist ein Text erkennbar ein Volltext — dann sind Woerter wie
# "Jahresabo" oder "Jetzt anmelden" bloss Inhalt (Newsletter-Kasten, Kommentare,
# oder ein Artikel, der ueber Abos berichtet) und kein Hinweis auf einen Anriss.
VOLLTEXT_AB_WOERTERN = 400


def _detect_paywall_teaser(text: str):
    """Gibt einen Grund zurueck, wenn der Text nach Anriss statt Volltext aussieht.

    11.08.: Die Marker-Suche schlug im ganzen Text zu — und warf damit vier
    vollstaendige Artikel raus: heise (849 W, "Jetzt anmelden" im Newsletter-
    Kasten), Kontext (2142 W, dito), stadt-bremerhaven (2512 W, "Probeabo" war
    das THEMA des Artikels; 1856 W, "Jahresabo" stand in einem Leserkommentar).
    Marker zaehlen deshalb nur noch bei kurzen Texten.
    """
    t = (text or "").strip()
    if not t:
        return "leer"
    lower = t.lower()
    words = len(t.split())
    if words < VOLLTEXT_AB_WOERTERN:
        for marker in PAYWALL_MARKERS:
            if marker in lower:
                return f"Paywall-Hinweis „{marker}“ bei nur {words} Wörtern"
    mid_sentence = t[-1] not in '.!?"\'»)' or t.endswith("…") or t.endswith("...")
    if words < 100 and (mid_sentence or words < 60):
        return f"verdächtig kurz ({words} Wörter) — Teaser statt Volltext?"
    return None


def _domain(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return ""


def _is_paywall_domain(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in PAYWALL_DOMAINS)


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── Browser ────────────────────────────────────────────────────────────────

def _launch(headless: bool = True):
    """Startet den Browser mit dem persistenten Profil.

    Wichtig: Chromium sperrt ein Profil — es kann immer nur EIN Prozess damit
    arbeiten. Lief parallel schon ein Abruf, scheiterte der zweite frueher mit
    einer kryptischen Playwright-Meldung (05.08.: ein Parallel-Testlauf hat
    Florians laufenden Abruf abgeschossen, ohne dass es jemand gemerkt hat).
    Jetzt gibt es dafuer eine klare Ansage.
    """
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
        text = str(exc)
        if browser_pfad.ist_browser_fehler(text):
            raise RuntimeError(browser_pfad.NACHINSTALL_HINWEIS) from exc
        if "ProcessSingleton" in text or "SingletonLock" in text or "already in use" in text.lower():
            raise RuntimeError(
                "Das Browser-Profil ist gerade belegt — es läuft schon ein Feedly-Abruf "
                "oder Login-Test. Bitte warten, bis der fertig ist, dann erneut versuchen."
            ) from exc
        raise
    return p, ctx


def _page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()


# JS laeuft IM Seitenkontext von feedly.com. Das Zugangstoken bleibt dadurch im
# Browser — es wird nie nach Python zurueckgegeben, geloggt oder gespeichert.
_API_JS = """
async ([path, method]) => {
  const findToken = () => {
    const fields = ["feedlyToken", "access_token", "accessToken", "oauthToken", "token"];
    for (let i = 0; i < localStorage.length; i++) {
      const raw = localStorage.getItem(localStorage.key(i)) || "";
      if (raw.length < 20 || raw.indexOf("{") === -1) continue;
      try {
        const obj = JSON.parse(raw);
        for (const f of fields) {
          if (typeof obj?.[f] === "string" && obj[f].length > 20) return obj[f];
        }
      } catch (e) { /* kein JSON */ }
    }
    return null;
  };

  const url = "https://api.feedly.com" + path;
  const attempt = async (headers) => {
    const res = await fetch(url, { method, headers, credentials: "include" });
    const body = await res.text();
    return { status: res.status, body };
  };

  let out = await attempt({});
  if (out.status === 401 || out.status === 403) {
    const token = findToken();
    if (token) out = await attempt({ Authorization: "OAuth " + token });
  }
  return out;
}
"""


def _api(page, path: str, method: str = "GET", versuche: int = 3):
    """Feedly-API-Aufruf im Seitenkontext. Gibt geparstes JSON zurueck.

    Mit Wiederholung: nach vielen Aufrufen kurz hintereinander (z. B. 16 mal
    Loeschen) antwortet Feedly zeitweise gar nicht — beobachtet am 01.08. Ein
    einzelner Aussetzer soll den ganzen Abruf nicht kippen.
    """
    letzter = None
    out = None
    for versuch in range(1, max(1, versuche) + 1):
        try:
            out = page.evaluate(_API_JS, [path, method])
            break
        except Exception as exc:
            letzter = exc
            if "Failed to fetch" not in str(exc) or versuch == versuche:
                raise RuntimeError(
                    f"Feedly antwortet gerade nicht ({path}). "
                    f"Kurz warten und nochmal versuchen.") from exc
            page.wait_for_timeout(1500 * versuch)
    if out is None:
        raise RuntimeError(f"Feedly antwortet gerade nicht ({path}).") from letzter

    status = out.get("status")
    body = out.get("body") or ""
    if status in (429, 503):
        raise RuntimeError("Feedly bremst gerade ab (zu viele Anfragen). "
                           "In ein paar Minuten nochmal versuchen.")
    if status != 200:
        raise RuntimeError(f"Feedly-API {path} antwortete mit HTTP {status}.")
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Feedly-API {path}: Antwort nicht lesbar ({exc}).") from exc


def _open_feedly(page) -> None:
    """Feedly oeffnen und warten, bis die Seite WIRKLICH bereit ist.

    Wichtig: Die API-Aufrufe laufen im Seitenkontext. Steht die Seite noch auf
    about:blank oder einer Fehlerseite, scheitert jedes fetch() mit „Failed to
    fetch" — und das sah dann faelschlich nach „nicht angemeldet" aus (01.08.).
    """
    letzter = None
    for versuch in range(3):
        try:
            page.goto(FEEDLY_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            letzter = exc
            page.wait_for_timeout(2000 * (versuch + 1))
            continue
        try:
            page.wait_for_function(
                "() => location.hostname.endsWith('feedly.com') "
                "&& document.readyState !== 'loading'",
                timeout=20000)
            page.wait_for_timeout(2500)
            # Feedly liefert bei Drosselung eine nackte Seite „Too Many
            # Requests (HAP429)" aus. Ohne diese Erkennung sah das aus wie
            # „nicht angemeldet" — mit falscher Login-Aufforderung (01.08.).
            try:
                if "HAP429" in (page.inner_text("body") or "")[:400]:
                    raise RuntimeError(
                        "Feedly bremst gerade ab (zu viele Anfragen in kurzer Zeit). "
                        "Deine Anmeldung ist in Ordnung — bitte 10-15 Minuten warten.")
            except RuntimeError:
                raise
            except Exception:
                pass
            return
        except RuntimeError:
            # Eigene, aussagekräftige Meldung (z. B. Drosselung) — nicht
            # wiederholen und nicht hinter einer Sammelmeldung verstecken.
            raise
        except Exception as exc:
            letzter = exc
            page.wait_for_timeout(2000 * (versuch + 1))
    if letzter:
        raise RuntimeError(f"Feedly-Seite lädt nicht ({letzter.__class__.__name__}).")


def _login_state(page) -> tuple:
    """('ok'|'anonym'|'netz', detail) — trennt „nicht angemeldet" von „Netz kaputt".

    Ohne diese Unterscheidung meldete ein Netzaussetzer „Nicht bei Feedly
    angemeldet" und schickte einen zum Login, obwohl die Anmeldung stand (01.08.).
    """
    try:
        profile = _api(page, "/v3/profile")
        return ("ok", profile.get("id") or "") if profile.get("id") else ("anonym", "")
    except RuntimeError as exc:
        text = str(exc)
        if "antwortet gerade nicht" in text or "bremst" in text:
            return ("netz", text)
        return ("anonym", text)
    except Exception as exc:
        return ("netz", str(exc))


def _logged_in_now(page) -> bool:
    return _login_state(page)[0] == "ok"


# ── Merkliste ──────────────────────────────────────────────────────────────

def _entry_url(entry: dict) -> str:
    for alt in entry.get("alternate") or []:
        href = (alt or {}).get("href")
        if href and href.startswith("http"):
            return href
    for key in ("canonicalUrl", "originId"):
        val = entry.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    return ""


def list_saved(page, limit: int = 2000) -> list:
    """Liest die Read-later-Liste. Ergebnis: Liste von dicts."""
    profile = _api(page, "/v3/profile")
    user_id = profile.get("id")
    if not user_id:
        raise RuntimeError("Feedly-Profil nicht lesbar — bist du angemeldet?")

    stream_id = f"user/{user_id}/tag/global.saved"
    # 10.08.: Kein Deckel mehr — Feedly liefert die Liste seitenweise, also wird
    # geblaettert, bis nichts mehr kommt. Vorher holten wir stur die neuesten 100;
    # bei ~120 markierten fielen die 20 aeltesten unbemerkt hinten runter.
    # `limit` ist nur noch eine Notbremse gegen Endlosschleifen.
    basis = ("/v3/streams/contents?streamId=" + urllib.parse.quote(stream_id, safe="")
             + "&count=250&ranked=newest")
    roh, fortsetzung = [], None
    while True:
        pfad = basis + (f"&continuation={urllib.parse.quote(fortsetzung, safe='')}" if fortsetzung else "")
        data = _api(page, pfad)
        seite = data.get("items") or []
        roh.extend(seite)
        fortsetzung = data.get("continuation")
        if not fortsetzung or not seite or len(roh) >= max(int(limit), 250):
            break

    items = []
    for entry in roh:
        url = _entry_url(entry)
        if not url:
            continue
        feed_html = ((entry.get("content") or {}).get("content")
                     or (entry.get("summary") or {}).get("content") or "")
        items.append({
            "entry_id": entry.get("id") or "",
            "title": (entry.get("title") or "").strip() or url,
            "url": url,
            "feed": ((entry.get("origin") or {}).get("title") or "").strip(),
            "published": entry.get("published") or entry.get("crawled") or 0,
            "feed_text": _strip_html(feed_html),
            "text": "",
            "source": "",
            "problem": "",
        })
    return {"user_id": user_id, "items": items}


# ── Volltexte ──────────────────────────────────────────────────────────────

# Gleiche Strategie wie die TabClip-Extension: bevorzugt den Artikel-Container,
# faellt aber auf den ganzen Body zurueck, damit nie Inhalt verloren geht.
_EXTRACT_JS = """
() => {
  const bodyText = (document.body?.innerText || "").trim();
  let best = "";
  for (const sel of ["article", "main", '[role="main"]']) {
    for (const el of document.querySelectorAll(sel)) {
      const t = (el.innerText || "").trim();
      if (t.length > best.length) best = t;
    }
  }
  if (best.length >= 500 && best.length >= bodyText.length * 0.5) return best;
  return bodyText;
}
"""


def _fetch_article_text(page, url: str) -> str:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1800)
    # Cookie-Banner einmalig wegklicken (Profil merkt sich die Entscheidung).
    for label in ("Alle ablehnen", "Ablehnen", "Nur notwendige", "Reject all"):
        try:
            btn = page.locator(f"text={label}")
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=2500)
                page.wait_for_timeout(800)
                break
        except Exception:
            pass
    return (page.evaluate(_EXTRACT_JS) or "").strip()


def _norm_url(url: str) -> str:
    """URL-Form fuer den Dublettenvergleich: ohne Schema, www, Tracking und Slash."""
    u = (url or "").strip()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("#")[0]
    u = re.sub(r"[?&](utm_[^&]*|fbclid|gclid|ref)=[^&]*", "", u)
    return u.rstrip("/?&").lower()


def fetch_all(limit: int = 2000, progress=None, headless: bool = True,
              skip_urls=None, skip_ids=None) -> dict:
    """Holt Merkliste + Volltexte.

    skip_urls / skip_ids: was schon im Briefing-Feld steht bzw. schon auf der
    Aufraeum-Liste vorgemerkt ist, wird uebersprungen — sonst landet derselbe
    Artikel beim zweiten Knopfdruck ein zweites Mal im Feld.

    Rueckgabe: {"ok": [...], "problems": [...], "skipped": [...], "user_id": str}
    "ok" enthaelt nur Eintraege mit brauchbarem Volltext.
    """
    def say(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    p, ctx = _launch(headless=headless)
    try:
        page = _page(ctx)
        _open_feedly(page)
        _zustand, _detail = _login_state(page)
        if _zustand == "netz":
            raise RuntimeError(f"Feedly ist gerade nicht erreichbar. {_detail}")
        if _zustand != "ok":
            raise RuntimeError(
                "Nicht bei Feedly angemeldet. Einmalig einrichten:\n"
                "    python3 feedly_fetch.py --login"
            )

        listing = list_saved(page, limit=limit)
        items = listing["items"]
        say(f"{len(items)} Artikel in der Merkliste.")
        # 09.08.: Bei limit=100 fielen 20 aeltere Artikel unbemerkt hinten runter
        # ("ranked=newest" holt die NEUESTEN zuerst) — deshalb Deckel auf 250 und
        # eine laute Warnung, falls er doch erreicht wird.
        if len(items) >= limit:
            say(f"⚠️ Notbremse bei {limit} Einträgen gegriffen — das wäre höchst ungewöhnlich, bitte melden!")

        # Dubletten aussortieren, BEVOR die Volltexte geladen werden — spart
        # Zeit und verhindert, dass derselbe Artikel zweimal im Feld landet.
        _skip_urls = {_norm_url(u) for u in (skip_urls or []) if u}
        _skip_ids = set(skip_ids or [])
        skipped = [i for i in items
                   if _norm_url(i["url"]) in _skip_urls or i["entry_id"] in _skip_ids]
        if skipped:
            _skip_set = {id(i) for i in skipped}
            items = [i for i in items if id(i) not in _skip_set]
            say(f"{len(skipped)} bereits im Briefing — übersprungen.")

        ok, problems, ohne_zugang = [], [], []
        for idx, item in enumerate(items, 1):
            title = item["title"][:55]
            # 1) Reicht der Text aus dem Feed schon?
            if len(item["feed_text"]) >= FEED_TEXT_MIN_CHARS:
                item["text"] = item["feed_text"]
                item["source"] = "Feed"
            else:
                say(f"[{idx}/{len(items)}] lade: {title}")
                try:
                    item["text"] = _fetch_article_text(page, item["url"])
                    item["source"] = "Seite"
                except Exception as exc:
                    item["problem"] = f"Seite nicht ladbar ({exc.__class__.__name__})"
                    problems.append(item)
                    continue

            reason = _detect_paywall_teaser(item["text"])
            # Der laengere von beiden gewinnt — manchmal hat der Feed mehr als
            # die Seite (z. B. wenn die Seite hinter einer Huerde steht).
            if reason and len(item["feed_text"]) > len(item["text"]):
                item["text"] = item["feed_text"]
                item["source"] = "Feed"
                reason = _detect_paywall_teaser(item["text"])

            if reason and _ohne_zugang(item.get("url")):
                # Kein Abo, kein Handlungsbedarf: still uebergehen und aus der
                # Merkliste nehmen, damit er nicht immer wieder auftaucht.
                item["problem"] = "kein Abo bei dieser Zeitung — übersprungen"
                ohne_zugang.append(item)
            elif reason:
                item["problem"] = reason
                problems.append(item)
            else:
                ok.append(item)

        _zusatz = f", {len(ohne_zugang)} ohne Abo übersprungen" if ohne_zugang else ""
        say(f"Fertig: {len(ok)} vollständig, {len(problems)} problematisch{_zusatz}.")
        return {"ok": ok, "problems": problems, "skipped": skipped,
                "ohne_zugang": ohne_zugang, "user_id": listing["user_id"]}
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass


def mark_done(entry_ids, user_id: str = "", headless: bool = True) -> int:
    """Entfernt Eintraege aus der Feedly-Merkliste. Gibt die Anzahl zurueck.

    Wird bewusst ERST nach einem erfolgreichen Briefing aufgerufen — bricht der
    Lauf ab, bleibt die Merkliste vollstaendig erhalten.
    """
    entry_ids = [e for e in (entry_ids or []) if e]
    if not entry_ids:
        return 0

    p, ctx = _launch(headless=headless)
    try:
        page = _page(ctx)
        _open_feedly(page)
        if not user_id:
            user_id = (_api(page, "/v3/profile") or {}).get("id") or ""
        if not user_id:
            raise RuntimeError("Feedly-Profil nicht lesbar — nichts entfernt.")

        tag_id = urllib.parse.quote(f"user/{user_id}/tag/global.saved", safe="")
        # Feedly nimmt mehrere Eintraege pro Aufruf (kommagetrennt). 16 einzelne
        # Loeschungen haben am 01.08. eine Sperre ausgeloest (HAP429) — in
        # Zehnergruppen sind daraus zwei Aufrufe statt sechzehn.
        removed = 0
        gruppen = [entry_ids[i:i + 10] for i in range(0, len(entry_ids), 10)]
        for nr, gruppe in enumerate(gruppen):
            ids = ",".join(urllib.parse.quote(e, safe="") for e in gruppe)
            try:
                _api(page, f"/v3/tags/{tag_id}/{ids}", method="DELETE")
                removed += len(gruppe)
            except Exception as exc:
                print(f"  ! Gruppe {nr + 1} nicht entfernt: {exc}", file=sys.stderr)
            if nr + 1 < len(gruppen):
                page.wait_for_timeout(2000)   # freundlich zum Server bleiben
        return removed
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass


# ── Zeitungs-Login-Test ────────────────────────────────────────────────────
# Startseite je Zeitung → erster Artikel-Link → Volltext holen → Anriss-Pruefung.
# Damit prueft man VOR dem Briefing, ob die Logins noch tragen (Tagblatt wirft
# einen gelegentlich raus). Hinweis: Erwischt der Test zufaellig einen freien
# Artikel, kann er "ok" melden, obwohl das Login weg ist — deshalb prueft der
# eigentliche Abruf trotzdem weiterhin jeden einzelnen Artikel.
# Kontoseiten mit eindeutigem Anmelde-Zeichen. Das ist die VERLAESSLICHE Pruefung:
# kein Raten an zufaelligen Artikeln, sondern der Kontostatus selbst.
# 16.08.: SWP zeigt auf /mein-konto ein "Abmelden", wenn die Sitzung gilt. GEA
# gibt so etwas nicht her — dort bleibt es beim Artikel-Test.
KONTO_PROBEN = {
    "SWP/Tagblatt": ("https://www.swp.de/mein-konto", "abmelden"),
}


NEWSPAPER_PROBES = (
    # GEA-Ressortseiten enden auf .html — ohne das kommt eine Fehlerseite (03.08. getestet).
    ("GEA", "https://www.gea.de/reutlingen.html"),
    ("SWP/Tagblatt", "https://www.swp.de/lokales/tuebingen/"),
)

_FIND_ARTICLE_LINKS_JS = """
() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.href || "";
    if ((/_arid,\\d+\\.html/.test(h) || /-\\d{6,}\\.html/.test(h)) && !out.includes(h)) out.push(h);
    if (out.length >= 6) break;
  }
  return out;
}
"""


# Eindeutige Beweise fuer "nicht angemeldet" — diese Saetze stehen NUR in der
# Anmelde-Aufforderung, nicht im Artikeltext. 14.08.: Der alte Test nahm einen
# zufaelligen Artikel von der Startseite; erwischte er einen freien, meldete er
# faelschlich "angemeldet", waehrend neun Premium-Artikel als Anriss zurueckkamen.
LOGIN_AUFFORDERUNG = (
    "sie haben bereits ein abo", "hier einloggen", "jetzt weiterlesen mit",
    "kennenlernabo", "anmelden und weiterlesen",
)


LOGIN_CHECK_CACHE = os.path.expanduser("~/.briefing_login_check.json")


def login_check_cached(max_alter_min: int = 180) -> list:
    """Login-Ergebnis mit Zwischenspeicher — damit der Abruf nicht jedes Mal
    25 Sekunden extra braucht. 16.08.: Florian soll VOR dem Holen erfahren,
    dass ein Login weg ist, statt hinterher Anrisse im Feld zu finden."""
    import time as _t
    try:
        daten = json.loads(open(LOGIN_CHECK_CACHE, encoding="utf-8").read())
        if (_t.time() - daten.get("stand", 0)) < max_alter_min * 60:
            return daten.get("ergebnis") or []
    except Exception:
        pass
    ergebnis = check_newspaper_logins()
    try:
        with open(LOGIN_CHECK_CACHE, "w", encoding="utf-8") as fh:
            json.dump({"stand": _t.time(), "ergebnis": ergebnis}, fh, ensure_ascii=False)
    except Exception:
        pass
    return ergebnis


def login_check_verwerfen() -> None:
    """Zwischenspeicher wegwerfen — nach einem Login-Fenster oder auf Zuruf."""
    try:
        if os.path.exists(LOGIN_CHECK_CACHE):
            os.remove(LOGIN_CHECK_CACHE)
    except Exception:
        pass


def open_login_window() -> None:
    """Oeffnet das sichtbare Anmeldefenster und wartet, bis Florian es schliesst.
    Passwoerter tippt er selbst — die App speichert und sieht keine Zugangsdaten."""
    p, ctx = _launch(headless=False)
    try:
        page = _page(ctx)
        page.goto(FEEDLY_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            # 30.08.: timeout=0 hiess "ewig warten". Blieb das Fenster offen
            # (minimiert, vergessen), hielt Chromium das Profil dauerhaft belegt
            # und JEDER Feedly-Abruf scheiterte mit "Profil belegt" — bis zum
            # App-Neustart. 30 Minuten sind reichlich fuer eine Anmeldung.
            page.wait_for_event("close", timeout=30 * 60 * 1000)
        except Exception:
            pass
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
    login_check_verwerfen()


def check_newspaper_logins(headless: bool = True) -> list:
    """Prueft alle Anmeldungen: Feedly UND die Zeitungen.

    Rueckgabe je Eintrag: {"name", "ok" (True/False/None), "detail", "url"}.
    ok=None heisst: Test nicht moeglich (Seite nicht ladbar o.ae.).
    """
    results = []
    p, ctx = _launch(headless=headless)
    try:
        page = _page(ctx)

        # 1) Feedly — ohne das laeuft gar nichts (14.08. von Florian vermisst).
        feedly = {"name": "Feedly", "ok": None, "detail": "", "url": FEEDLY_URL}
        try:
            _open_feedly(page)
            zustand, detail = _login_state(page)
            if zustand == "ok":
                feedly["ok"] = True
                feedly["detail"] = "angemeldet"
            elif zustand == "anonym":
                feedly["ok"] = False
                feedly["detail"] = "nicht angemeldet — Merkliste nicht abrufbar"
            else:
                feedly["detail"] = detail[:80]
        except Exception as exc:
            feedly["detail"] = f"nicht prüfbar ({exc.__class__.__name__})"
        results.append(feedly)

        for name, start_url in NEWSPAPER_PROBES:
            eintrag = {"name": name, "ok": None, "detail": "", "url": ""}
            # Erst die Kontoseite — schnell und eindeutig, wo es sie gibt.
            konto = KONTO_PROBEN.get(name)
            if konto:
                try:
                    page.goto(konto[0], wait_until="domcontentloaded", timeout=40000)
                    page.wait_for_timeout(2500)
                    seite = (page.inner_text("body") or "").lower()
                    if konto[1] in seite:
                        eintrag["ok"] = True
                        eintrag["detail"] = "angemeldet (Kontoseite)"
                        eintrag["url"] = konto[0]
                        results.append(eintrag)
                        continue
                    if "anmelden" in seite or "einloggen" in seite:
                        eintrag["ok"] = False
                        eintrag["detail"] = "NICHT angemeldet (Kontoseite zeigt Anmelde-Formular)"
                        eintrag["url"] = konto[0]
                        results.append(eintrag)
                        continue
                except Exception:
                    pass   # Kontoseite nicht erreichbar → Artikel-Test unten
            try:
                page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1500)
                for label in ("Alle ablehnen", "Ablehnen", "Nur notwendige", "Reject all"):
                    try:
                        btn = page.locator(f"text={label}")
                        if btn.count() and btn.first.is_visible():
                            btn.first.click(timeout=2500)
                            page.wait_for_timeout(600)
                            break
                    except Exception:
                        pass
                hrefs = page.evaluate(_FIND_ARTICLE_LINKS_JS)
                if not hrefs:
                    eintrag["detail"] = "kein Artikel-Link auf der Startseite gefunden"
                    results.append(eintrag)
                    continue
                # 14.08.: EIN Artikel reicht nicht — erwischt der Test zufaellig
                # einen freien, meldet er "angemeldet", waehrend die Bezahl-
                # Artikel als Anriss zurueckkommen. Genau so passierte es mit
                # neun SWP-Artikeln. Deshalb bis zu drei pruefen: EIN Treffer
                # mit Anmelde-Aufforderung genuegt fuer "nicht angemeldet".
                bester = None
                for href in hrefs[:3]:
                    eintrag["url"] = href
                    try:
                        text = _fetch_article_text(page, href)
                    except Exception:
                        continue
                    unten = text.lower()
                    aufforderung = next((m for m in LOGIN_AUFFORDERUNG if m in unten), None)
                    if aufforderung:
                        eintrag["ok"] = False
                        eintrag["detail"] = f"NICHT angemeldet — „{aufforderung}“ im Artikel"
                        bester = None
                        break
                    if not _detect_paywall_teaser(text):
                        bester = f"Volltext ({len(text.split())} Wörter, {hrefs.index(href)+1} von 3 geprüft)"
                if bester:
                    eintrag["ok"] = True
                    eintrag["detail"] = bester
            except Exception as exc:
                eintrag["detail"] = f"nicht prüfbar ({exc.__class__.__name__})"
            results.append(eintrag)
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
    return results


# ── Offene Aufraeum-Liste ──────────────────────────────────────────────────
# Welche Artikel wurden geholt, aber noch nicht aus der Merkliste entfernt?
# Bewusst als DATEI, nicht im Streamlit-Sitzungsspeicher: ein App-Neustart oder
# ein geschlossener Browser-Tab wuerde die Liste sonst verlieren, und die
# Artikel blieben fuer immer in der Merkliste stehen.
PENDING_PATH = os.path.expanduser("~/.briefing_feedly_pending.json")


def load_pending() -> dict:
    try:
        with open(PENDING_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return {"user_id": data.get("user_id") or "",
                "entry_ids": [e for e in (data.get("entry_ids") or []) if e]}
    except Exception:
        return {"user_id": "", "entry_ids": []}


def add_pending(entry_ids, user_id: str = "") -> int:
    """Merkt geholte Artikel fuer das spaetere Aufraeumen vor. Gibt die Gesamtzahl zurueck."""
    current = load_pending()
    seen = set(current["entry_ids"])
    for entry_id in entry_ids or []:
        if entry_id and entry_id not in seen:
            seen.add(entry_id)
            current["entry_ids"].append(entry_id)
    if user_id:
        current["user_id"] = user_id
    try:
        with open(PENDING_PATH, "w", encoding="utf-8") as fh:
            json.dump(current, fh, ensure_ascii=False)
    except Exception as exc:
        print(f"TabClip/Feedly: Aufräum-Liste nicht speicherbar: {exc}", file=sys.stderr)
    return len(current["entry_ids"])


def remove_pending(entry_ids) -> None:
    """Traegt NUR die uebergebenen IDs aus der Vormerkliste aus.

    24.08.: clear_pending() loeschte die ganze Datei — kamen waehrend eines
    laufenden Briefings neue Artikel an, verschwanden deren Vormerkungen mit,
    obwohl sie in keinem Briefing waren. Jetzt bleibt Nachzuegler-Vormerkung
    erhalten und wird erst mit dem NAECHSTEN erfolgreichen Briefing abgeraeumt."""
    weg = set(entry_ids or [])
    if not weg:
        return
    daten = load_pending()
    rest = [e for e in daten["entry_ids"] if e not in weg]
    try:
        if rest:
            with open(PENDING_PATH, "w", encoding="utf-8") as fh:
                json.dump({"user_id": daten["user_id"], "entry_ids": rest}, fh)
        elif os.path.exists(PENDING_PATH):
            os.remove(PENDING_PATH)
    except Exception:
        pass


def clear_pending() -> None:
    try:
        if os.path.exists(PENDING_PATH):
            os.remove(PENDING_PATH)
    except Exception:
        pass


def to_blocks(items) -> str:
    """Formatiert Artikel als 'mmm'-getrennte Bloecke — wie TabClip sie liefert."""
    blocks = []
    for item in items:
        head = item["title"]
        meta = " · ".join(x for x in (item.get("feed"), _domain(item["url"])) if x)
        blocks.append(f"{head}\n{item['url']}\n\n{meta}\n\n{item['text']}".strip())
    return BLOCK_SEPARATOR.join(blocks)


def split_free_and_paywall(items):
    """Teilt in (frei, hinter Paywall) — nur fuer die Anzeige in der App."""
    free = [i for i in items if not _is_paywall_domain(i["url"])]
    walled = [i for i in items if _is_paywall_domain(i["url"])]
    return free, walled


# ── CLI ────────────────────────────────────────────────────────────────────

def _cli_login() -> int:
    print("Sichtbares Fenster öffnet sich. Bitte anmelden bei:")
    print("  1. feedly.com")
    print("  2. gea.de")
    print("  3. swp.de / tagblatt.de")
    print("Danach das Fenster einfach schließen.\n")
    p, ctx = _launch(headless=False)
    try:
        page = _page(ctx)
        page.goto(FEEDLY_URL, wait_until="domcontentloaded", timeout=60000)
        print("Warte, bis du das Fenster schließt …")
        try:
            # 30.08.: timeout=0 hiess "ewig warten". Blieb das Fenster offen
            # (minimiert, vergessen), hielt Chromium das Profil dauerhaft belegt
            # und JEDER Feedly-Abruf scheiterte mit "Profil belegt" — bis zum
            # App-Neustart. 30 Minuten sind reichlich fuer eine Anmeldung.
            page.wait_for_event("close", timeout=30 * 60 * 1000)
        except Exception:
            pass
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
    return 0


def _cli_status() -> int:
    p, ctx = _launch(headless=True)
    try:
        page = _page(ctx)
        _open_feedly(page)
        zustand, detail = _login_state(page)
        if zustand == "ok":
            print("✅ Bei Feedly angemeldet.")
            return 0
        if zustand == "netz":
            print(f"⚠️  Feedly gerade nicht erreichbar — {detail}")
            print("   (Das heißt NICHT, dass die Anmeldung weg ist.)")
            return 2
        print("❌ Nicht angemeldet — 'python3 feedly_fetch.py --login' ausführen.")
        return 1
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass


def _cli_list() -> int:
    p, ctx = _launch(headless=True)
    try:
        page = _page(ctx)
        _open_feedly(page)
        if not _logged_in_now(page):
            print("❌ Nicht angemeldet — erst '--login'.")
            return 1
        listing = list_saved(page)
        items = listing["items"]
        print(f"{len(items)} Artikel in der Merkliste:\n")
        for i, item in enumerate(items, 1):
            tag = "🔒" if _is_paywall_domain(item["url"]) else "🌐"
            print(f"{i:2}. {tag} {item['title'][:70]}")
            print(f"       {item['feed']} · {_domain(item['url'])}")
        return 0
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass


def _cli_fetch() -> int:
    result = fetch_all(progress=lambda m: print(f"  {m}", file=sys.stderr))
    ok, problems = result["ok"], result["problems"]
    if problems:
        print(f"\n⚠️  {len(problems)} Artikel unvollständig (bleiben in der Merkliste):",
              file=sys.stderr)
        for item in problems:
            print(f"   · {item['title'][:60]} — {item['problem']}", file=sys.stderr)
    print(to_blocks(ok))
    return 0


def main(argv) -> int:
    aktionen = {"--login": _cli_login, "--status": _cli_status,
                "--list": _cli_list, "--fetch": _cli_fetch}
    for flag, aktion in aktionen.items():
        if flag in argv:
            try:
                return aktion()
            except RuntimeError as exc:
                # Verständliche Meldung statt Python-Rückverfolgung.
                print(f"⚠️  {exc}", file=sys.stderr)
                return 2
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
