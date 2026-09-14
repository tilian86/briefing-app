"""ElevenReader-Upload-Automatik fürs Audio-Briefing.

Lädt die Eleven-Reader-TXT nach jedem Briefing-Lauf automatisch in Florians
ElevenReader-Bibliothek hoch (elevenreader.io, Web-Upload) — die synct auf
das iPhone, dort erscheint das Briefing als "Hörbuch bereit".

Technik: Playwright-Chromium mit PERSISTENTEM Profil (~/.briefing_reader_profile).
Einmal-Login im sichtbaren Fenster (--login), danach laufen Uploads headless.
Verifizierter UI-Flow (15.07.2026): Bibliothek → Button "Import" (früher
"Upload your content") → Tab "Upload file" → input[type=file] → "Import"-
Bestätigung → Eintrag erscheint in der Library.

CLI:  python3 reader_upload.py --login     (sichtbares Fenster, einmalig)
      python3 reader_upload.py --status    (angemeldet? Exit 0/1)
      python3 reader_upload.py --upload PFAD --title "Titel"
"""

import os
import re
import shutil
import sys
import tempfile
import time
import browser_pfad  # muss VOR jedem Playwright-Import stehen (25.08.)

PROFILE_DIR = os.path.expanduser("~/.briefing_reader_profile")
READER_LIBRARY_URL = "https://elevenreader.io/reader/library"
# ElevenReader hat den Einstiegsknopf am 15.07.2026 von "Upload your content"
# auf "Import" umbenannt. Der Dialog dahinter (Tabs Paste link / Upload file /
# Write text) ist unverändert. Wird für Klick UND Login-Erkennung genutzt.
_UPLOAD_BUTTON_TEXT = "Import"


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


def _logged_in_now(page) -> bool:
    """Prüft den AKTUELLEN Seitenzustand (ohne Navigation) auf eingeloggte Library.
    Robust gegen deutsche/englische UI: Upload-Button ODER (Library-URL + kein Sign-In)."""
    try:
        if page.locator(f"text={_UPLOAD_BUTTON_TEXT}").count() > 0:
            return True
        url_ok = "/reader/library" in (page.url or "")
        signin = 0
        for marker in ("Sign In", "Sign in", "Anmelden", "Log in"):
            signin += page.locator(f"text={marker}").count()
        return bool(url_ok and signin == 0 and page.locator("text=Library").count() +
                    page.locator("text=Bibliothek").count() > 0)
    except Exception:
        return False


def _open_library(page) -> None:
    """Navigiert zur Bibliothek, lehnt den Cookie-Banner ab (einmalig, Profil merkt
    sich das) und wartet, bis die Einträge wirklich gerendert sind."""
    page.goto(READER_LIBRARY_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    try:
        deny = page.locator("text=Alle ablehnen")
        if deny.count() > 0 and deny.first.is_visible():
            deny.first.click(timeout=4000)
            page.wait_for_timeout(800)
    except Exception:
        pass
    try:
        page.wait_for_selector(f"text={_UPLOAD_BUTTON_TEXT}", timeout=12000)
    except Exception:
        pass
    _dismiss_overlays(page)
    page.wait_for_timeout(2500)  # Liste rendert asynchron nach


def _dismiss_overlays(page) -> None:
    """Toasts + Promo-Banner (Dismiss-Button) wegklicken — legen sich sonst über
    Buttons und fangen Klicks ab (Fehlerquelle 03.07.; erneut 16.07.: Banner
    erschien VERZÖGERT nach dem frühen Sweep und blockte den Import-Klick —
    deshalb vor jedem kritischen Klick erneut aufrufen)."""
    for _ in range(5):
        try:
            d = page.get_by_role("button", name="Dismiss")
            if d.count() == 0:
                break
            d.first.click(timeout=2000)
            page.wait_for_timeout(300)
        except Exception:
            break


def _looks_logged_in(page) -> bool:
    try:
        _open_library(page)
        return _logged_in_now(page)
    except Exception:
        return False


def is_logged_in_fast():
    """Schneller Cookie-Vorcheck (~Millisekunden) ohne Browserstart. Returns:
    False = definitiv abgemeldet (Auth-Cookie fehlt/abgelaufen) → früh warnen;
    True  = Cookie da (wahrscheinlich angemeldet, der echte Upload prüft autoritativ);
    None  = unklar (Cookie-DB nicht lesbar) → Aufrufer soll nicht warnen."""
    import sqlite3, time as _t
    db = os.path.join(PROFILE_DIR, "Default", "Cookies")
    if not os.path.exists(db):
        return False
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        try:
            row = con.execute(
                "SELECT CAST((expires_utc/1000000 - 11644473600) AS INT) FROM cookies "
                "WHERE host_key LIKE '%elevenreader%' AND name='xi_website_auth_hint' LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        if not row:
            return False
        return row[0] > _t.time()
    except Exception:
        return None


def is_logged_in() -> bool:
    """Headless-Check, ob das Automatik-Profil angemeldet ist (~10-15s)."""
    p, ctx = _launch(headless=True)
    try:
        return _looks_logged_in(_page(ctx))
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        p.stop()


def login_interactive(max_wait_s: int = 1800) -> bool:
    """Öffnet ein sichtbares Fenster für den Einmal-Login. Beendet sich selbst,
    sobald der Login erkannt wurde (oder das Fenster geschlossen wird)."""
    p, ctx = _launch(headless=False)
    try:
        page = _page(ctx)
        try:
            page.goto(READER_LIBRARY_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        deadline = time.time() + max_wait_s
        while time.time() < deadline:
            if not ctx.pages:  # Fenster zugemacht
                return False
            try:
                if _logged_in_now(_page(ctx)):
                    print("Login erkannt — Profil gespeichert. Fenster schließt sich.")
                    time.sleep(1.5)
                    return True
            except Exception:
                pass
            time.sleep(2)
        return False
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        p.stop()


def _sanitize_title(title: str) -> str:
    t = re.sub(r'[\\/:*?"<>|]+', "-", (title or "Tagesbriefing")).strip()
    return t[:80] or "Tagesbriefing"


def upload_briefing_txt(txt_path: str, title: str, timeout_s: int = 120,
                        chapterize: bool = False) -> dict:
    """Lädt die TXT als schön benanntes Hörbuch in die ElevenReader-Bibliothek.

    Returns: {"ok": bool, "error": str|None, "elapsed_seconds": float}
    """
    t0 = time.time()
    if not os.path.exists(txt_path):
        return {"ok": False, "error": f"TXT nicht gefunden: {txt_path}", "elapsed_seconds": 0.0}
    if not os.path.isdir(PROFILE_DIR):
        return {"ok": False, "error": "ElevenReader nicht verbunden — Einmal-Login fehlt (Knopf in der App).",
                "elapsed_seconds": 0.0}

    nice = _sanitize_title(title)
    tmp_dir = tempfile.mkdtemp(prefix="reader_up_")
    nice_path = os.path.join(tmp_dir, f"{nice}.txt")
    # ElevenReader nimmt den Titel aus der ERSTEN ZEILE des Textes (nicht dem
    # Dateinamen) — daher erste Zeile durch den schönen Titel ersetzen (verifiziert 03.07.).
    with open(txt_path, encoding="utf-8") as _f:
        _content = _f.read()
    _lines = _content.splitlines()
    if _lines and _lines[0].strip().lower().startswith("audio-briefing"):
        _lines[0] = nice
    else:
        _lines.insert(0, nice)
    _out_text = "\n".join(_lines)
    with open(nice_path, "w", encoding="utf-8") as _f:
        _f.write(_out_text)
    try:
        return _do_upload(nice_path, nice, timeout_s, t0)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def upload_briefing_epub(txt_path: str, title: str, timeout_s: int = 120) -> dict:
    """Wie upload_briefing_txt, aber als ePub mit echter Kapitel-Navigation
    (ein Kapitel pro Ressort). Empfohlen für die tägliche Automatik."""
    t0 = time.time()
    if not os.path.exists(txt_path):
        return {"ok": False, "error": f"TXT nicht gefunden: {txt_path}", "elapsed_seconds": 0.0}
    if not os.path.isdir(PROFILE_DIR):
        return {"ok": False, "error": "ElevenReader nicht verbunden — Einmal-Login fehlt (Knopf in der App).",
                "elapsed_seconds": 0.0}
    nice = _sanitize_title(title)
    tmp_dir = tempfile.mkdtemp(prefix="reader_up_")
    try:
        with open(txt_path, encoding="utf-8") as _f:
            _content = _f.read()
        _lines = _content.splitlines()
        if _lines and _lines[0].strip().lower().startswith("audio-briefing"):
            _lines = _lines[1:]  # redundante Kopfzeile — Titel steckt im ePub-Metadatum
        epub_path = os.path.join(tmp_dir, f"{nice}.epub")
        build_briefing_epub("\n".join(_lines), nice, epub_path)
        return _do_upload(epub_path, nice, timeout_s, t0)
    except Exception as exc:
        return {"ok": False, "error": f"ePub-Bau fehlgeschlagen: {str(exc)[:160]}",
                "elapsed_seconds": time.time() - t0}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _do_upload(file_path: str, expect_title: str, timeout_s: int, t0: float) -> dict:
    p, ctx = _launch(headless=True)
    try:
        page = _page(ctx)
        if not _looks_logged_in(page):
            return {"ok": False, "error": "ElevenReader-Login abgelaufen — bitte Einmal-Login in der App wiederholen.",
                    "elapsed_seconds": time.time() - t0}
        # Einstiegsknopf "Import" (exakt, sonst greift "Import from URL" /
        # "Import content in one click" mit). Öffnet den Upload-Dialog.
        # Vorher Overlays räumen; bei Timeout: nochmal räumen + EIN Retry
        # (Promo-Banner erscheinen teils erst nach Sekunden — 16.07.).
        _dismiss_overlays(page)
        try:
            page.get_by_role("button", name="Import", exact=True).first.click(timeout=10000)
        except Exception:
            _dismiss_overlays(page)
            page.get_by_role("button", name="Import", exact=True).first.click(timeout=10000)
        page.wait_for_timeout(600)
        _dismiss_overlays(page)
        page.get_by_role("tab", name="Upload file").click(timeout=10000)
        page.wait_for_timeout(400)
        file_input = page.locator('input[type="file"]').first
        file_input.set_input_files(file_path, timeout=10000)
        page.wait_for_timeout(800)
        # Import bestätigen: Der Dialog hat einen zweiten "Import"-Button. Da jetzt
        # mehrere existieren (Library-Knopf + Dialog-Bestätigung), den LETZTEN
        # sichtbaren klicken — das ist die Dialog-Bestätigung.
        try:
            imp = page.get_by_role("button", name="Import", exact=True)
            for j in range(imp.count() - 1, -1, -1):
                if imp.nth(j).is_visible():
                    imp.nth(j).click(timeout=5000)
                    break
        except Exception:
            pass
        # Erfolg heisst: der Eintrag steht in der BIBLIOTHEK. Nicht irgendwo
        # auf der Seite — 13.09.: der alte Test suchte den Titel per
        # wait_for_selector im gerade offenen Hochlade-Dialog, und DER zeigt
        # den Dateinamen selbst an. Ergebnis: "Upload: ok" im Protokoll,
        # waehrend Teil 1 des Nachhol-Briefings vom 11.09. nie ankam und
        # Florian zwei Tage lang ein halbes Briefing hatte. Deshalb jetzt:
        # Bibliothek frisch laden und dort nachsehen, mehrfach.
        frist = time.time() + max(60, timeout_s)
        letzter_fehler = ""
        while time.time() < frist:
            page.wait_for_timeout(4000)
            try:
                page.goto(READER_LIBRARY_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                _dismiss_overlays(page)
                if expect_title in (page.inner_text("body") or ""):
                    return {"ok": True, "error": None, "elapsed_seconds": time.time() - t0}
            except Exception as exc:
                letzter_fehler = exc.__class__.__name__
        return {"ok": False,
                "error": (f"'{expect_title}' ist nach {int(time.time() - t0)} Sekunden "
                          "nicht in der ElevenReader-Bibliothek aufgetaucht — der Import "
                          "hat nicht geklappt."
                          + (f" ({letzter_fehler})" if letzter_fehler else "")),
                "elapsed_seconds": time.time() - t0}
    except Exception as exc:
        return {"ok": False, "error": f"Upload fehlgeschlagen: {str(exc)[:180]}",
                "elapsed_seconds": time.time() - t0}
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        p.stop()


_CHAPTER_LINES = {"Top 3", "Regional", "Politik & International", "Wirtschaft",
                  "Tech & Wissenschaft", "Gericht & Recht", "Weitere Themen", "Podcasts"}


def _split_into_chapters(text: str, title: str) -> list:
    """Zerlegt die Briefing-TXT an den Ressort-Zeilen in (Kapiteltitel, Text)-Paare.
    Experiment 03.07.: Markdown-# in TXT wird von ElevenReader NICHT als Kapitel
    erkannt (und würde vorgelesen) — echte Kapitel gehen nur über ePub."""
    chapters = []
    cur_title, cur_lines = title, []
    for ln in text.splitlines():
        s = ln.strip()
        if s in _CHAPTER_LINES or s.startswith("Wetter für") or s.startswith("Rückblick"):
            if cur_lines and any(x.strip() for x in cur_lines):
                chapters.append((cur_title, "\n".join(cur_lines).strip()))
            cur_title, cur_lines = s, []
        else:
            cur_lines.append(ln)
    if cur_lines and any(x.strip() for x in cur_lines):
        chapters.append((cur_title, "\n".join(cur_lines).strip()))
    return chapters or [(title, text)]


def _make_cover_png(title: str) -> bytes:
    """Erzeugt ein schlichtes Cover (Titel auf dunklem Grund) → ElevenReader zeigt wieder
    eine Vorschau/Thumbnail wie früher beim PDF. Fällt bei Fehler auf None-Bytes zurück."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io as _io
        W, H = 720, 960
        img = Image.new("RGB", (W, H), (24, 28, 38))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 12], fill=(210, 90, 60))          # Akzentbalken oben
        def _font(sz):
            for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                      "/System/Library/Fonts/Helvetica.ttc",
                      "/Library/Fonts/Arial.ttf"):
                try:
                    return ImageFont.truetype(p, sz)
                except Exception:
                    continue
            return ImageFont.load_default()
        d.text((56, 90), "AUDIO-BRIEFING", font=_font(30), fill=(210, 90, 60))
        # Titel umbrechen (grob nach ~16 Zeichen/Wortgrenze)
        words = title.replace(" 🧵", "").split()
        lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 > 16:
                lines.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            lines.append(cur)
        y = 200
        big = _font(58)
        for ln in lines[:6]:
            d.text((56, y), ln, font=big, fill=(240, 242, 248))
            y += 78
        d.text((56, H - 90), "🧵 Themen-Synthese", font=_font(28), fill=(150, 156, 170))
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return b""


def build_briefing_epub(text: str, title: str, out_path: str) -> str:
    """Baut ein minimales, valides ePub (Standardbibliothek) mit einem Kapitel pro
    Ressort — damit zeigt ElevenReader eine echte Kapitel-Navigation."""
    import zipfile
    import html as _h
    import uuid
    chapters = _split_into_chapters(text, title)
    # "&" in Kapiteltiteln: Reader zeigt XML-Escapes doppelt an — "und" liest sich eh besser
    chapters = [(ct.replace(" & ", " und "), ctext) for ct, ctext in chapters]
    uid = str(uuid.uuid4())

    def _xhtml(ch_title, ch_text):
        paras = "".join(f"<p>{_h.escape(p.strip())}</p>\n"
                        for p in ch_text.split("\n\n") if p.strip())
        return (f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{_h.escape(ch_title)}</title></head>'
                f'<body><h1>{_h.escape(ch_title)}</h1>\n{paras}</body></html>')

    manifest, spine, navlis = [], [], []
    files = []
    for i, (ct, ctext) in enumerate(chapters):
        fn = f"chap_{i:02d}.xhtml"
        files.append((f"OEBPS/{fn}", _xhtml(ct, ctext)))
        manifest.append(f'<item id="c{i}" href="{fn}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')
        navlis.append(f'<li><a href="{fn}">{__import__("html").escape(ct)}</a></li>')

    nav = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
           '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
           '<head><title>Inhalt</title></head><body><nav epub:type="toc"><h1>Inhalt</h1><ol>'
           + "".join(navlis) + '</ol></nav></body></html>')
    _esc = __import__("html").escape
    # Beschreibung: erste inhaltsstarke Zeile (z.B. Top-3-Vorschau/erster Beitrag) als Vorschau-Text.
    _desc = ""
    for _ln in text.splitlines():
        _s = _ln.strip()
        if len(_s) > 40 and not _s.startswith(("Tagesbriefing", "Audio-Briefing")) and "Uhr" not in _s[:30]:
            _desc = _s[:280]
            break
    _cover_png = _make_cover_png(title)
    _cover_manifest = ('<item id="cover-img" href="cover.png" media-type="image/png" properties="cover-image"/>'
                       if _cover_png else "")
    _cover_meta = '<meta name="cover" content="cover-img"/>' if _cover_png else ""
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
           f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="uid">urn:uuid:{uid}</dc:identifier>'
           f'<dc:title>{_esc(title)}</dc:title>'
           '<dc:creator>Tägliches Audio-Briefing</dc:creator>'
           + (f'<dc:description>{_esc(_desc)}</dc:description>' if _desc else '')
           + '<dc:language>de</dc:language>'
           + _cover_meta
           + '<meta xmlns="http://www.idpf.org/2007/opf" property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
           '</metadata><manifest>'
           '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           + _cover_manifest
           + "".join(manifest) + '</manifest><spine>' + "".join(spine) + '</spine></package>')
    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')

    with zipfile.ZipFile(out_path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        if _cover_png:
            z.writestr("OEBPS/cover.png", _cover_png)
        for path, content in files:
            z.writestr(path, content)
    return out_path


# 14.09.: Nur "Tagesbriefing …" war zu eng. Die Nachhol-Briefings vom 11.09.
# hiessen "Briefing Aktuell 11.09." bzw. "Briefing Rueckblick 04.09.–08.09." —
# die waeren nie wieder aus der Bibliothek verschwunden.
_TITEL_TAGESBRIEFING = r"\b(?:Tagesbriefing|Briefing) [^\n]{0,60}?\d{2}\.\d{2}\.[^\n]{0,20}"
# Wochenbriefings brauchen ein EIGENES Muster, nicht das obige: seit dem
# Sonntags-Automatismus (wochenbriefing_lauf.py) kommt jede Woche eins dazu,
# und ohne Aufraeumen waeren das nach einem Quartal dreizehn Eintraege.
_TITEL_WOCHENBRIEFING = r"\bWochenbriefing [^\n]{0,40}?\d{2}\.\d{2}\.[^\n]{0,20}"


def _bibliothek_aufraeumen(muster: str, days: int, today=None) -> dict:
    """Loescht Bibliotheks-Eintraege, deren Titel auf `muster` passt und deren
    Datum aelter als `days` Tage ist. Buecher bleiben immer unberuehrt.

    Returns: {"ok", "deleted": [titel…], "errors": [...], "checked": int}
    """
    import datetime as _dt
    today = today or _dt.date.today()
    p, ctx = _launch(headless=True)
    titles = []
    gelesen = False
    try:
        page = _page(ctx)
        if not _looks_logged_in(page):
            return {"ok": False, "deleted": [], "errors": ["Nicht angemeldet."], "checked": 0}
        # 14.09.: Hier stand frueher ein einzelnes inner_text("body") direkt nach
        # dem Oeffnen. Beim Test lief genau das ins Leere — die Bibliothek rendert
        # asynchron nach, das Muster fand NICHTS, und die Funktion meldete
        # zufrieden "ok, nichts zu loeschen". Das ist dieselbe Luege wie beim
        # Upload, der den Dateinamen im eigenen Dialog wiedererkannte: ein
        # Nicht-Sehen als Nichts-Da ausgeben. Jetzt wird gewartet, bis entweder
        # ein Treffer da ist oder die Seite zwei Messungen lang unveraendert
        # steht — und wenn sie gar nicht auftaucht, sagt die Funktion das.
        frist = time.time() + 40
        letzte_laenge, stabil = -1, 0
        while time.time() < frist:
            body = page.inner_text("body") or ""
            treffer = sorted(set(re.findall(muster, body)))
            if treffer:
                titles, gelesen = treffer, True
                break
            if len(body) > 1500 and len(body) == letzte_laenge:
                stabil += 1
                if stabil >= 2:      # Liste steht, es gibt nur nichts zu finden
                    gelesen = True
                    break
            else:
                stabil, letzte_laenge = 0, len(body)
            page.wait_for_timeout(2500)
            _dismiss_overlays(page)
        if not gelesen:
            return {"ok": False, "deleted": [],
                    "errors": ["Bibliothek war nach 40 Sekunden nicht lesbar — "
                               "nichts geloescht (lieber nichts als blind)."],
                    "checked": 0}
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        p.stop()

    to_delete = []
    for t in titles:
        m = re.search(r"(\d{2})\.(\d{2})\.", t)
        if not m:
            continue
        try:
            d = _dt.date(today.year, int(m.group(2)), int(m.group(1)))
            if d > today + _dt.timedelta(days=2):  # Jahreswechsel
                d = d.replace(year=today.year - 1)
        except ValueError:
            continue
        if (today - d).days > days:
            to_delete.append(t.strip())

    deleted, errors = [], []
    for t in to_delete:
        r = delete_briefing_by_title(t)
        (deleted if r.get("ok") else errors).append(t if r.get("ok") else f"{t}: {r.get('error')}")
    return {"ok": True, "deleted": deleted, "errors": errors, "checked": len(titles)}


def cleanup_old_briefings(days: int, today=None) -> dict:
    """Loescht alte TAGES-Briefings aus der Bibliothek (Buecher und
    Wochenbriefings bleiben — deren Titel passt nicht auf das Muster)."""
    return _bibliothek_aufraeumen(_TITEL_TAGESBRIEFING, days, today)


def cleanup_old_wochenbriefings(days: int = 21, today=None) -> dict:
    """Loescht alte WOCHEN-Briefings. 21 Tage heisst: die letzten drei bleiben
    liegen — genug Luft, um eins nachzuhoeren, ohne dass die Liste zuwaechst."""
    return _bibliothek_aufraeumen(_TITEL_WOCHENBRIEFING, days, today)


def delete_briefing_by_title(title: str, timeout_s: int = 60) -> dict:
    """Löscht einen Bibliotheks-Eintrag anhand seines Titels (für Auto-Aufräumen
    alter Tages-Briefings und Tests). Nutzt den verifizierten UI-Flow:
    Eintrag öffnen → Menü (…) → Delete → Delete item."""
    t0 = time.time()
    p, ctx = _launch(headless=True)
    try:
        page = _page(ctx)
        if not _looks_logged_in(page):
            return {"ok": False, "error": "Nicht angemeldet.", "elapsed_seconds": time.time() - t0}
        try:
            page.wait_for_selector(f"text={title}", timeout=15000)
        except Exception:
            return {"ok": False, "error": f"Kein Eintrag mit Titel: {title}", "elapsed_seconds": time.time() - t0}
        page.locator(f"text={title}").first.click(timeout=10000)
        page.wait_for_timeout(2000)
        # Menü-Trigger (Radix: aria-haspopup) von hinten durchprobieren, bis das
        # Menü mit "Delete" erscheint — es gibt je nach Zustand 2-3 Trigger
        # (Eintrags-Menü, Player-Menü, Account).
        triggers = page.locator('button[aria-haspopup="menu"]')
        menu_found = False
        for idx in range(triggers.count() - 1, -1, -1):
            try:
                try:
                    triggers.nth(idx).scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass
                triggers.nth(idx).click(timeout=5000)
                page.wait_for_timeout(500)
                if page.get_by_role("menuitem", name="Delete").count() > 0:
                    menu_found = True
                    break
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                continue
        if not menu_found:
            return {"ok": False, "error": "Eintrags-Menü mit Delete nicht gefunden.",
                    "elapsed_seconds": time.time() - t0}
        # 14.09.: Das Menue klappt bei langen Bibliotheken ausserhalb des
        # Sichtbereichs auf — der Klick lief in einen Timeout ("element is
        # outside of the viewport"). Erst hinscrollen, notfalls erzwingen.
        _del = page.get_by_role("menuitem", name="Delete").first
        try:
            _del.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            pass
        try:
            _del.click(timeout=8000)
        except Exception:
            # Die Bibliothek ist eine virtualisierte Liste: das Menue haengt
            # teils 100.000 Pixel ausserhalb des Bildes, da hilft auch
            # force=True nicht (echte Maus braucht den Sichtbereich).
            # dispatch_event schickt das Klick-Ereignis direkt ans Element.
            try:
                _del.click(timeout=4000, force=True)
            except Exception:
                _del.dispatch_event("click")
        # Auf den Bestaetigungs-Dialog warten, statt blind zu klicken.
        try:
            page.wait_for_selector('[role=dialog]', timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(900)
        # Im Dialog den Knopf nehmen, der loescht — NICHT die Ueberschrift
        # <h4>Delete item</h4> (darauf lief der alte Text-Fallback) und nicht
        # "Cancel". Rolle allein reicht nicht: der Knopf traegt je nach
        # Oberflaechen-Version einen anderen zugaenglichen Namen.
        bestaetigt = False
        for _versuch in range(3):
            dlg = page.locator('[role=dialog]')
            bereich = dlg.last if dlg.count() else page
            kn = bereich.locator("button")
            for j in range(kn.count()):
                try:
                    b = kn.nth(j)
                    txt = " ".join((b.inner_text() or "").split()).lower()
                    if not txt or "cancel" in txt or "abbrech" in txt:
                        continue
                    if "delete" in txt or "loesch" in txt or "lösch" in txt or "entfern" in txt:
                        try:
                            b.click(timeout=5000)
                        except Exception:
                            b.dispatch_event("click")
                        bestaetigt = True
                        break
                except Exception:
                    continue
            if bestaetigt:
                break
            page.wait_for_timeout(1200)
        if not bestaetigt:
            return {"ok": False, "error": "Bestaetigungsknopf im Loesch-Dialog nicht gefunden.",
                    "elapsed_seconds": time.time() - t0}
        # ERFOLG NUR VERIFIZIERT: zurück zur Bibliothek und prüfen, dass der
        # Titel wirklich verschwunden ist (Schein-Erfolge gab es schon…).
        page.wait_for_timeout(2500)
        _open_library(page)
        for _ in range(6):
            if page.locator(f"text={title}").count() == 0:
                return {"ok": True, "error": None, "elapsed_seconds": time.time() - t0}
            page.wait_for_timeout(2000)
        return {"ok": False, "error": "Eintrag nach Löschversuch weiterhin vorhanden.",
                "elapsed_seconds": time.time() - t0}
    except Exception as exc:
        return {"ok": False, "error": f"Löschen fehlgeschlagen: {str(exc)[:160]}",
                "elapsed_seconds": time.time() - t0}
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        p.stop()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--upload")
    ap.add_argument("--title", default="Tagesbriefing")
    a = ap.parse_args()
    if a.login:
        ok = login_interactive()
        print("LOGIN_OK" if ok else "LOGIN_ABGEBROCHEN")
        sys.exit(0 if ok else 1)
    if a.status:
        ok = is_logged_in()
        print("ANGEMELDET" if ok else "NICHT_ANGEMELDET")
        sys.exit(0 if ok else 1)
    if a.upload:
        r = upload_briefing_txt(a.upload, a.title)
        print(r)
        sys.exit(0 if r.get("ok") else 1)
    ap.print_help()
