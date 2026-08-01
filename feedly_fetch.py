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


def _detect_paywall_teaser(text: str):
    """Gibt einen Grund zurueck, wenn der Text nach Anriss statt Volltext aussieht."""
    t = (text or "").strip()
    if not t:
        return "leer"
    lower = t.lower()
    for marker in PAYWALL_MARKERS:
        if marker in lower:
            return f"Paywall-Hinweis „{marker}“ im Text"
    words = len(t.split())
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


def _api(page, path: str, method: str = "GET"):
    """Feedly-API-Aufruf im Seitenkontext. Gibt geparstes JSON zurueck."""
    out = page.evaluate(_API_JS, [path, method])
    status = out.get("status")
    body = out.get("body") or ""
    if status != 200:
        raise RuntimeError(f"Feedly-API {path} antwortete mit HTTP {status}.")
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Feedly-API {path}: Antwort nicht lesbar ({exc}).") from exc


def _open_feedly(page) -> None:
    page.goto(FEEDLY_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)


def _logged_in_now(page) -> bool:
    try:
        profile = _api(page, "/v3/profile")
        return bool(profile.get("id"))
    except Exception:
        return False


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


def list_saved(page, limit: int = 100) -> list:
    """Liest die Read-later-Liste. Ergebnis: Liste von dicts."""
    profile = _api(page, "/v3/profile")
    user_id = profile.get("id")
    if not user_id:
        raise RuntimeError("Feedly-Profil nicht lesbar — bist du angemeldet?")

    stream_id = f"user/{user_id}/tag/global.saved"
    path = (
        "/v3/streams/contents?streamId=" + urllib.parse.quote(stream_id, safe="")
        + f"&count={int(limit)}&ranked=newest"
    )
    data = _api(page, path)

    items = []
    for entry in data.get("items") or []:
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


def fetch_all(limit: int = 100, progress=None, headless: bool = True) -> dict:
    """Holt Merkliste + Volltexte.

    Rueckgabe: {"ok": [...], "problems": [...], "user_id": str}
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
        if not _logged_in_now(page):
            raise RuntimeError(
                "Nicht bei Feedly angemeldet. Einmalig einrichten:\n"
                "    python3 feedly_fetch.py --login"
            )

        listing = list_saved(page, limit=limit)
        items = listing["items"]
        say(f"{len(items)} Artikel in der Merkliste.")

        ok, problems = [], []
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

            if reason:
                item["problem"] = reason
                problems.append(item)
            else:
                ok.append(item)

        say(f"Fertig: {len(ok)} vollständig, {len(problems)} problematisch.")
        return {"ok": ok, "problems": problems, "user_id": listing["user_id"]}
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
        removed = 0
        for entry_id in entry_ids:
            try:
                _api(page, f"/v3/tags/{tag_id}/{urllib.parse.quote(entry_id, safe='')}",
                     method="DELETE")
                removed += 1
            except Exception as exc:
                print(f"  ! konnte nicht entfernt werden: {exc}", file=sys.stderr)
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
            page.wait_for_event("close", timeout=0)
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
        if _logged_in_now(page):
            print("✅ Bei Feedly angemeldet.")
            return 0
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
    if "--login" in argv:
        return _cli_login()
    if "--status" in argv:
        return _cli_status()
    if "--list" in argv:
        return _cli_list()
    if "--fetch" in argv:
        return _cli_fetch()
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
