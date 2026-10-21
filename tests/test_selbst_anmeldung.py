"""Selbst-Anmeldung (selbst_anmeldung.py) gegen NACHGEBAUTE lokale Seiten.

21.10.2026: Es wird nie eine echte Seite angefasst. Ein kleiner Server auf 127.0.0.1
spielt SWP (Dialog mit Honigtopf und Newsletter-Formular), GEA (Formular im fremden
Iframe, Knopf erst nach Eingabe frei, Zustimmungsbanner), ein zweistufiges Formular,
Captcha-Seiten und eine Zustimmungswand ohne „Ablehnen“. Der Browser ist ein frischer
Wegwerf-Browser, NIE das Profil ~/.briefing_news_profile. Der Schluesselbund wird nicht
angefasst: `security` ist gefaelscht, die Zugangsdaten sind Testwerte. Push: Attrappe.
Merkdatei, Log, Zwischenspeicher und Sitzungssicherung liegen in einem Wegwerf-Ordner.

Aufruf aus dem Repo-Ordner:

    python3 -m unittest tests.test_selbst_anmeldung
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import selbst_anmeldung as SA  # noqa: E402  (importiert browser_pfad VOR Playwright)

EMAIL = "test.user@example.invalid"
PW = "geheimTest123"          # Testwert fuer die Attrappen-Seiten, kein echtes Passwort

STATE = {"posts": [], "gets": []}
_SERVER = {}


# ── Attrappen-Seiten ───────────────────────────────────────────────────────

def _fehler(text):
    return '<p class="error">%s</p>' % text if text else ""


def swp_seite(fehler="", offen=False, wand=False):
    wand_html = ""
    if wand:
        wand_html = ('<div id="sp_message_container_1" style="position:fixed;inset:0;background:#fff;'
                     'z-index:9999"><p>Mit Werbung weiterlesen oder Abo</p>'
                     '<button onclick="fetch(\'/akzeptiert\');this.parentNode.remove()">'
                     'Akzeptieren &amp; schließen</button></div>')
    return ("""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Fake SWP</title></head><body>
<header><button id="header-login-button" type="button"
  onclick="document.getElementById('dlg').hidden=false">Login</button></header>
<form id="nl" action="/swp/newsletter" method="post"><input type="email" name="nlmail" placeholder="Ihre E-Mail">
  <button type="submit">Jetzt abonnieren</button><p>Der Newsletter kommt täglich.</p></form>
<div id="dlg" role="dialog" """ + ("" if offen else "hidden") + """>
 <form id="loginForm" method="post" action="/swp/login">
  <h2>Anmelden</h2>
  <input id="loginUsername" name="username" type="text" autocomplete="username" placeholder="E-Mail">
  <input id="companyWebsite" name="companyWebsite" type="text" tabindex="-1" style="position:absolute;left:-9999px">
  <input id="loginPassword" name="password" type="password" autocomplete="current-password">
  <label><input type="checkbox" id="loginPreserve" name="loginPreserve"
     style="opacity:0;position:absolute;width:1px;height:1px"> <span>Angemeldet bleiben</span></label>
  """ + _fehler(fehler) + """
  <button type="submit">Anmelden</button>
 </form></div>""" + wand_html + "</body></html>")


def gea_seite(port_frame):
    return ("""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Fake GEA</title></head><body>
<div id="cmp" style="position:fixed;inset:0;background:#fff;z-index:9999"><p>Wir nutzen Cookies</p>
 <button onclick="fetch('/akzeptiert');document.getElementById('cmp').remove()">Alle akzeptieren</button>
 <button onclick="fetch('/einstellungen');document.getElementById('cmp').remove()">Einstellungen oder ablehnen</button>
 <button onclick="fetch('/abgelehnt');document.getElementById('cmp').remove()">Alle ablehnen</button></div>
<iframe src="http://localhost:%d/gea-frame" width="500" height="400"></iframe></body></html>""" % port_frame)


def gea_rahmen(fehler=""):
    return ("""<!doctype html><html lang="de"><head><meta charset="utf-8"></head><body>
<form method="post" action="/gea-frame/login">
 <input type="email" name="email" id="email" placeholder="E-Mail-Adresse" oninput="chk()">
 <input type="password" name="password" id="password" oninput="chk()">
 <label><input type="checkbox" name="rememberMe" id="rememberMe"> Angemeldet bleiben</label>
 """ + _fehler(fehler) + """
 <button type="submit" id="go" disabled>Anmelden</button>
</form>
<script>function chk(){document.getElementById('go').disabled =
  !(document.getElementById('email').value.indexOf('@')>0 && document.getElementById('password').value.length>=3);}</script>
</body></html>""")


ZWEI = """<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Fake zweistufig</title></head><body>
<div id="s1"><form onsubmit="return false"><h2>Anmelden</h2>
 <input type="email" name="email" id="mail" autocomplete="username" placeholder="E-Mail">
 <button type="button" onclick="document.getElementById('h').value=document.getElementById('mail').value;
   document.getElementById('s1').hidden=true;document.getElementById('s2').hidden=false">Weiter</button></form></div>
<div id="s2" hidden><form method="post" action="/zwei/login"><h2>Passwort</h2>
 <input type="hidden" name="email2" id="h"><input type="password" name="password">
 <button type="submit">Anmelden</button></form></div></body></html>"""

CAPTCHA = """<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Fake Captcha</title></head><body>
<form method="post" action="/captcha/login"><h2>Anmelden</h2>
 <input type="email" name="email" autocomplete="username"><input type="password" name="password">
 <div class="g-recaptcha" data-sitekey="test"></div><button type="submit">Anmelden</button></form></body></html>"""

CAPTCHA_RAHMEN = """<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Fake Rahmen</title></head><body>
<form method="post" action="/captcha/login"><h2>Anmelden</h2>
 <input type="email" name="email" autocomplete="username"><input type="password" name="password">
 <iframe src="/recaptcha/anchor" width="300" height="80"></iframe>
 <button type="submit">Anmelden</button></form></body></html>"""


def _erwartet(daten):
    return (daten.get("username") or daten.get("email") or daten.get("email2") or [""])[0] == EMAIL \
        and (daten.get("password") or [""])[0] == PW


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _antwort(self, html, status=200, cookie=None):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        pfad = urlparse(self.path).path
        STATE["gets"].append(pfad)
        if pfad == "/swp":
            self._antwort(swp_seite())
        elif pfad == "/swp-offen":
            self._antwort(swp_seite(offen=True))
        elif pfad == "/swp-wand":
            self._antwort(swp_seite(wand=True))
        elif pfad == "/gea":
            self._antwort(gea_seite(_SERVER["port"]))
        elif pfad == "/gea-frame":
            self._antwort(gea_rahmen())
        elif pfad == "/zwei":
            self._antwort(ZWEI)
        elif pfad == "/captcha":
            self._antwort(CAPTCHA)
        elif pfad == "/captcha-rahmen":
            self._antwort(CAPTCHA_RAHMEN)
        elif pfad.startswith("/recaptcha/"):
            self._antwort("<html><body>reCAPTCHA</body></html>")
        elif pfad == "/ok":
            self._antwort("<html><body>Willkommen</body></html>")
        else:
            self._antwort("<html><body>nichts</body></html>", 200 if pfad in ("/akzeptiert", "/abgelehnt",
                                                                            "/einstellungen") else 404)

    def do_POST(self):
        pfad = urlparse(self.path).path
        laenge = int(self.headers.get("Content-Length") or 0)
        daten = parse_qs(self.rfile.read(laenge).decode("utf-8"), keep_blank_values=True)
        STATE["posts"].append((pfad, daten))
        gut = _erwartet(daten)
        cookie = "auth=1; Path=/"
        if pfad == "/swp/login":
            if gut:
                self._antwort("<html><body>Willkommen</body></html>", cookie=cookie)
            else:   # die Seite wiederholt das Passwort im Fehlertext (Gegenprobe fuers Bereinigen)
                self._antwort(swp_seite(
                    fehler="Passwort %s ist falsch." % (daten.get("password") or [""])[0], offen=True))
        elif pfad == "/gea-frame/login":
            if gut:
                self._antwort("<html><body><p>Eingeloggt</p></body></html>", cookie=cookie)
            else:
                self._antwort(gea_rahmen("E-Mail oder Passwort stimmen nicht überein."))
        elif pfad == "/zwei/login":
            self._antwort("<html><body>Willkommen</body></html>" if gut else ZWEI, cookie=cookie if gut else None)
        else:
            self._antwort("<html><body>danke</body></html>")


def setUpModule():
    for name in ("haupt", "rahmen"):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        _SERVER[name] = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    _SERVER["port"] = _SERVER["rahmen"].server_address[1]
    _SERVER["base"] = "http://127.0.0.1:%d" % _SERVER["haupt"].server_address[1]
    try:
        from playwright.sync_api import sync_playwright
        _SERVER["pw"] = sync_playwright().start()
        _SERVER["browser"] = _SERVER["pw"].chromium.launch(headless=True)
    except Exception as exc:   # kein Chromium: Browser-Tests entfallen
        _SERVER["browser_fehler"] = str(exc)[:200]


def tearDownModule():
    for k in ("browser",):
        try:
            _SERVER[k].close()
        except Exception:
            pass
    try:
        _SERVER["pw"].stop()
    except Exception:
        pass
    for name in ("haupt", "rahmen"):
        _SERVER[name].shutdown()


# ── Gemeinsame Grundlage ───────────────────────────────────────────────────

class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="selbst_anm_test_")
        self.zustand = os.path.join(self.tmp, "zustand.json")
        self.logdatei = os.path.join(self.tmp, "log.txt")
        self._env = mock.patch.dict(os.environ, {
            "BRIEFING_SELBST_ANMELDUNG_ZUSTAND": self.zustand,
            "BRIEFING_SELBST_ANMELDUNG_LOG": self.logdatei,
        })
        self._env.start()
        os.environ.pop("BRIEFING_SELBST_ANMELDUNG", None)
        os.environ.pop("BRIEFING_PUSH_DRY", None)
        # Nichts davon darf in echte Dateien schreiben:
        self._ff = [
            mock.patch.object(SA.ff, "LOGIN_CHECK_CACHE", os.path.join(self.tmp, "login_check.json")),
            mock.patch.object(SA.ff, "SITZUNG_PATH", os.path.join(self.tmp, "sitzung.json")),
        ]
        for m in self._ff:
            m.start()
        SA._status_cache.update(zeit=0.0, wert=None)
        # Sicherheitsnetz: weder echter Schluesselbund noch echte Push in diesen Tests.
        # (Tests, die beides pruefen, ersetzen die Funktionen selbst.)
        self._netz = [
            mock.patch.object(SA, "_security_aufrufen", side_effect=AssertionError("kein echter Schluesselbund")),
            mock.patch.object(SA, "PUSHFUNK", "/nicht/vorhanden/pushfunk"),
        ]
        for m in self._netz:
            m.start()

    def tearDown(self):
        for m in self._netz + self._ff:
            m.stop()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def alles_text(self):
        """Alles, was die Funktion auf die Platte oder ins Log geschrieben hat."""
        teile = []
        for pfad in (self.zustand, self.logdatei):
            if os.path.exists(pfad):
                with open(pfad, encoding="utf-8") as fh:
                    teile.append(fh.read())
        return "\n".join(teile)

    def setze(self, seitenweg, ziel="swp", oeffnen=None):
        STATE["posts"].clear()
        STATE["gets"].clear()
        cfg = {"login_url": _SERVER["base"] + seitenweg, "domains": r"^127\.0\.0\.1$"}
        if oeffnen is not None:
            cfg["oeffnen"] = oeffnen
        p = mock.patch.dict(SA.ZEITUNGEN[ziel], cfg)
        p.start()
        self.addCleanup(p.stop)


class BrowserBasis(Basis):
    def setUp(self):
        super().setUp()
        if "browser" not in _SERVER:
            self.skipTest("kein Chromium: %s" % _SERVER.get("browser_fehler", "?"))
        self.ctx = _SERVER["browser"].new_context(locale="de-DE", viewport={"width": 1200, "height": 900})
        self.page = self.ctx.new_page()

    def tearDown(self):
        try:
            self.ctx.close()
        except Exception:
            pass
        super().tearDown()

    def anmelden(self, ziel="swp", passwort=PW, **kw):
        kw.setdefault("nachpruefen", False)
        return SA.selbst_anmelden(ziel, self.page, zugang=(EMAIL, passwort), **kw)


# ── Browser-Tests ──────────────────────────────────────────────────────────

class Formulare(BrowserBasis):
    def test_einstufig_dialog_swp_stil(self):
        """Dialog per Knopf, Honigtopf bleibt leer, Newsletter-Feld unberuehrt, Haken gesetzt."""
        self.setze("/swp", "swp")
        r = self.anmelden("swp")
        self.assertEqual(r["status"], SA.OK, r)
        pfad, daten = STATE["posts"][-1]
        self.assertEqual(pfad, "/swp/login")
        self.assertEqual(daten["username"], [EMAIL])
        self.assertEqual(daten["password"], [PW])
        self.assertEqual(daten.get("companyWebsite"), [""], "Honigtopf muss leer bleiben")
        self.assertEqual(daten.get("loginPreserve"), ["on"], "„angemeldet bleiben“ nicht angehakt")
        self.assertFalse([p for p, _ in STATE["posts"] if p == "/swp/newsletter"])

    def test_einstufig_iframe_gea_stil_mit_banner(self):
        """Formular im fremden Iframe, Knopf erst nach Eingabe frei; Banner: NUR „Alle ablehnen“."""
        self.setze("/gea", "gea", oeffnen=())
        r = self.anmelden("gea")
        self.assertEqual(r["status"], SA.OK, r)
        pfad, daten = STATE["posts"][-1]
        self.assertEqual(pfad, "/gea-frame/login")
        self.assertEqual(daten["email"], [EMAIL])
        self.assertEqual(daten["rememberMe"], ["on"])
        self.assertIn("/abgelehnt", STATE["gets"])
        self.assertNotIn("/akzeptiert", STATE["gets"])
        self.assertNotIn("/einstellungen", STATE["gets"])

    def test_zweistufig(self):
        """Erst E-Mail + „Weiter“, dann Passwort."""
        self.setze("/zwei", "gea", oeffnen=())
        r = self.anmelden("gea")
        self.assertEqual(r["status"], SA.OK, r)
        pfad, daten = STATE["posts"][-1]
        self.assertEqual(pfad, "/zwei/login")
        self.assertEqual(daten["email2"], [EMAIL])
        self.assertEqual(daten["password"], [PW])


class Schutz(BrowserBasis):
    def test_captcha_erkannt_nichts_abgeschickt(self):
        self.setze("/captcha", "swp", oeffnen=())
        r = self.anmelden("swp")
        self.assertEqual(r["status"], SA.CAPTCHA, r)
        self.assertIn("nicht umgangen", r["grund"])
        self.assertEqual(STATE["posts"], [], "bei Captcha darf nichts abgeschickt werden")

    def test_captcha_im_fremden_rahmen(self):
        self.setze("/captcha-rahmen", "swp", oeffnen=())
        r = self.anmelden("swp")
        self.assertEqual(r["status"], SA.CAPTCHA, r)
        self.assertEqual(STATE["posts"], [])

    def test_falsches_passwort_dann_sperre(self):
        self.setze("/swp", "swp")
        r = self.anmelden("swp", passwort="falsch-" + PW)
        self.assertEqual(r["status"], SA.FEHLGESCHLAGEN, r)
        self.assertIn("abgelehnt", r["grund"])
        self.assertEqual(len(STATE["posts"]), 1)
        # Die Seite wiederholt das Passwort im Fehlertext: es darf nirgends auftauchen.
        self.assertNotIn("falsch-" + PW, json.dumps(r))
        self.assertNotIn("falsch-" + PW, self.alles_text())
        # Zweiter Anlauf: gesperrt, es geht NICHTS mehr an die Seite.
        r2 = self.anmelden("swp", passwort=PW)
        self.assertEqual(r2["status"], SA.FEHLGESCHLAGEN)
        self.assertIn("kein neuer Versuch", r2["grund"])
        self.assertEqual(len(STATE["posts"]), 1)
        # Der Test-Knopf (erzwingen) hebt die Sperre auf; mit dem richtigen Passwort klappt es.
        ja = {"name": "SWP/Tagblatt", "ok": True, "detail": "3 Abo-Artikel vollständig", "url": ""}
        with mock.patch.object(SA.ff, "_zeitung_pruefen", return_value=ja):
            r3 = self.anmelden("swp", passwort=PW, erzwingen=True, nachpruefen=True)
        self.assertEqual(r3["status"], SA.OK, r3)
        self.assertEqual(len(STATE["posts"]), 2)
        self.assertIsNone(SA._sperre_grund("swp"), "Erfolg muss die Sperre loeschen")

    def test_falsches_passwort_gea_im_iframe(self):
        self.setze("/gea", "gea", oeffnen=())
        r = self.anmelden("gea", passwort="falsch-" + PW)
        self.assertEqual(r["status"], SA.FEHLGESCHLAGEN, r)
        self.assertIn("stimmen nicht", r["grund"])

    def test_wand_ohne_ablehnen_wird_nie_akzeptiert(self):
        """SWP-Zustimmungswand ohne „Ablehnen“: klare Meldung, „Akzeptieren“ bleibt ungeklickt."""
        self.setze("/swp-wand", "swp")
        r = self.anmelden("swp")
        self.assertEqual(r["status"], SA.FEHLGESCHLAGEN, r)
        self.assertIn("Zustimmungsbanner", r["grund"])
        self.assertNotIn("/akzeptiert", STATE["gets"])
        self.assertEqual(STATE["posts"], [])

    def test_newsletter_feld_wird_nie_gefuellt(self):
        """Ohne Login-Knopf-Treffer darf die E-Mail NICHT im Newsletter-Formular landen."""
        self.setze("/swp", "swp", oeffnen=("#gibt-es-nicht",))
        with mock.patch.object(SA, "FORMULAR_SUCHE_S", 7):
            r = self.anmelden("swp")
        # „Login“ heisst der Knopf trotzdem — der Ersatz-Weg findet ihn, also klappt es;
        # entscheidend: nirgends ein Newsletter-POST.
        self.assertFalse([p for p, _ in STATE["posts"] if p == "/swp/newsletter"], r)

    def test_nachpruefung_entscheidet(self):
        """Formular weg heisst NICHT automatisch „angemeldet“: erst die Gegenprobe zaehlt."""
        self.setze("/swp", "swp")
        nein = {"name": "SWP/Tagblatt", "ok": False, "detail": "Artikel kommt als Anriss", "url": ""}
        with mock.patch.object(SA.ff, "_zeitung_pruefen", return_value=nein):
            r = self.anmelden("swp", nachpruefen=True)
        self.assertEqual(r["status"], SA.FEHLGESCHLAGEN, r)
        self.assertIn("Anriss", r["grund"])
        ja = {"name": "SWP/Tagblatt", "ok": True, "detail": "3 Abo-Artikel vollständig", "url": ""}
        STATE["posts"].clear()
        with mock.patch.object(SA.ff, "_zeitung_pruefen", return_value=ja):
            r = self.anmelden("swp", nachpruefen=True, erzwingen=True)
        self.assertEqual(r["status"], SA.OK, r)
        self.assertEqual(r["pruefung"]["ok"], True)

    def test_test_modus_rollback_bei_misserfolg(self):
        """„Jetzt testen“ (neu=True): bei Misserfolg sind die bisherigen Cookies wieder da."""
        self.setze("/swp", "swp")
        basis = _SERVER["base"]
        self.ctx.add_cookies([{"name": "sessionid", "value": "alt", "url": basis},
                              {"name": "consent_choice", "value": "1", "url": basis}])
        r = self.anmelden("swp", passwort="falsch-" + PW, neu=True, erzwingen=True)
        self.assertEqual(r["status"], SA.FEHLGESCHLAGEN, r)
        namen = {c["name"]: c["value"] for c in self.ctx.cookies()}
        self.assertEqual(namen.get("sessionid"), "alt", "Rollback hat die alte Sitzung nicht zurueckgebracht")
        self.assertEqual(namen.get("consent_choice"), "1")
        self.assertNotIn("auth", namen)

    def test_test_modus_erfolg_meldet_vorher_ab(self):
        self.setze("/swp", "swp")
        basis = _SERVER["base"]
        self.ctx.add_cookies([{"name": "sessionid", "value": "alt", "url": basis},
                              {"name": "consent_choice", "value": "1", "url": basis}])
        r = self.anmelden("swp", neu=True, erzwingen=True)
        self.assertEqual(r["status"], SA.OK, r)
        namen = {c["name"]: c["value"] for c in self.ctx.cookies()}
        self.assertNotIn("sessionid", namen, "alte Sitzung muss beim Test weg sein")
        self.assertEqual(namen.get("consent_choice"), "1", "Zustimmungs-Cookie muss bleiben")
        self.assertEqual(namen.get("auth"), "1")


# ── Ohne Browser ───────────────────────────────────────────────────────────

class KeinEintrag(Basis):
    def test_fehlender_eintrag_ist_kein_fehler_und_kein_browser(self):
        with mock.patch.object(SA, "zugangsdaten_lesen", return_value=None), \
                mock.patch.object(SA, "eintrag_vorhanden", return_value=False), \
                mock.patch.object(SA.ff, "_launch", side_effect=AssertionError("Browser darf nicht starten")):
            r = SA.selbst_anmelden("swp")
        self.assertEqual(r["status"], SA.KEIN_EINTRAG)
        self.assertIn("briefing-login-swp", r["grund"])
        self.assertIn("README", r["grund"])

    def test_eintrag_da_aber_nicht_lesbar(self):
        with mock.patch.object(SA, "zugangsdaten_lesen", return_value=None), \
                mock.patch.object(SA, "eintrag_vorhanden", return_value=True):
            r = SA.selbst_anmelden("gea")
        self.assertEqual(r["status"], SA.KEIN_EINTRAG)
        self.assertIn("nicht lesbar", r["grund"])

    def test_unbekannte_zeitung(self):
        with self.assertRaises(ValueError):
            SA.selbst_anmelden("faz")

    def _fake_security(self, konto_zeile, passwort):
        calls = []

        def fake(args, timeout=15):
            calls.append(list(args))
            if "-w" in args:
                return subprocess.CompletedProcess(args, 0, passwort + "\n", "")
            ausgabe = ('keychain: "/x/login.keychain-db"\nclass: "genp"\nattributes:\n'
                       '    0x00000007 <blob>="briefing-login-swp"\n    ' + konto_zeile +
                       '\n    "svce"<blob>="briefing-login-swp"\n')
            return subprocess.CompletedProcess(args, 0, ausgabe, "")
        return fake, calls

    def test_schluesselbund_lesen_ueber_attribute_und_w(self):
        fake, calls = self._fake_security('"acct"<blob>="a.b@example.invalid"', "pw-ä-1")
        with mock.patch.object(SA, "_security_aufrufen", fake):
            self.assertEqual(SA.zugangsdaten_lesen("swp"), ("a.b@example.invalid", "pw-ä-1"))
        self.assertEqual(calls[0], ["find-generic-password", "-s", "briefing-login-swp"])
        self.assertEqual(calls[1], ["find-generic-password", "-s", "briefing-login-swp",
                                    "-a", "a.b@example.invalid", "-w"])

    def test_schluesselbund_konto_als_hex(self):
        hexa = "a.b@example.invalid".encode().hex().upper()
        fake, _ = self._fake_security('"acct"<blob>=0x%s  "a.b@example.invalid"' % hexa, "x")
        with mock.patch.object(SA, "_security_aufrufen", fake):
            self.assertEqual(SA.zugangsdaten_lesen("gea")[0], "a.b@example.invalid")

    def test_schluesselbund_ohne_konto_oder_ohne_passwort(self):
        fake, _ = self._fake_security('"acct"<blob>=<NULL>', "x")
        with mock.patch.object(SA, "_security_aufrufen", fake):
            self.assertIsNone(SA.zugangsdaten_lesen("swp"))
        fake, _ = self._fake_security('"acct"<blob>="a@b.de"', "")
        with mock.patch.object(SA, "_security_aufrufen", fake):
            self.assertIsNone(SA.zugangsdaten_lesen("swp"))
        with mock.patch.object(SA, "_security_aufrufen", lambda *a, **k: None):
            self.assertIsNone(SA.zugangsdaten_lesen("swp"))

    def test_bereinigen(self):
        t = SA._bereinigen("Fehler bei test.user@example.invalid mit geheimTest123 und test.user", EMAIL, PW)
        self.assertNotIn(PW, t)
        self.assertNotIn("test.user", t)

    def test_status_anzeige(self):
        with mock.patch.object(SA, "eintrag_vorhanden", side_effect=lambda z: z == "swp"):
            st = SA.status_anzeige(max_alter_s=0)
        self.assertTrue(st["swp"])
        self.assertFalse(st["gea"])


class Limits(Basis):
    def test_tageslimit(self):
        for _ in range(SA.MAX_VERSUCHE_PRO_TAG):
            self.assertIsNone(SA._anlauf_zaehlen("gea", False))
        self.assertIn("Anmelde-Anläufe", SA._anlauf_zaehlen("gea", False))
        self.assertIsNone(SA._anlauf_zaehlen("gea", True), "erzwingen hebt das Limit auf")
        self.assertIsNone(SA._anlauf_zaehlen("swp", False), "Limit gilt je Zeitung")

    def test_push_drossel_eine_je_zeitung_und_tag(self):
        gesendet = []
        with mock.patch.object(SA, "_push_senden", lambda t, x, u="": gesendet.append((t, x, u)) or True):
            self.assertTrue(SA._push_wenn_neu("swp", "Konto abgelehnt"))
            self.assertFalse(SA._push_wenn_neu("swp", "nochmal"))
            self.assertTrue(SA._push_wenn_neu("gea", "kein Eintrag"))
            self.assertFalse(SA._push_wenn_neu("gea", "nochmal"))
        self.assertEqual(len(gesendet), 2)
        self.assertEqual(gesendet[0][0], "🔐 Briefing: SWP-Login klappt nicht – bitte anmelden")
        self.assertEqual(gesendet[1][0], "🔐 Briefing: GEA-Login klappt nicht – bitte anmelden")
        self.assertIn("Konto abgelehnt", gesendet[0][1])
        self.assertEqual(gesendet[0][2], SA.APP_URL)
        # am naechsten Tag wieder
        with open(self.zustand, encoding="utf-8") as fh:
            z = json.load(fh)
        z["push"]["swp"] = "2000-01-01"
        with open(self.zustand, "w", encoding="utf-8") as fh:
            json.dump(z, fh)
        with mock.patch.object(SA, "_push_senden", lambda *a, **k: gesendet.append(a) or True):
            self.assertTrue(SA._push_wenn_neu("swp", "neuer Tag"))

    def test_push_fehlgeschlagen_wird_nicht_vermerkt(self):
        with mock.patch.object(SA, "_push_senden", return_value=False):
            self.assertFalse(SA._push_wenn_neu("swp", "x"))
        with mock.patch.object(SA, "_push_senden", return_value=True):
            self.assertTrue(SA._push_wenn_neu("swp", "x"))

    def test_push_trockenlauf_sendet_nichts_und_merkt_nichts(self):
        with mock.patch.dict(os.environ, {"BRIEFING_PUSH_DRY": "1"}), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("kein echter Push")):
            self.assertTrue(SA._push_wenn_neu("swp", "x"))
            self.assertTrue(SA._push_wenn_neu("swp", "x"))   # nichts vermerkt -> wieder erlaubt


class Ablauf(Basis):
    """logins_sicherstellen mit Attrappen statt Browser."""

    def setUp(self):
        super().setUp()
        self.erg = [{"name": "SWP/Tagblatt", "ok": False, "detail": "Anriss", "url": ""},
                    {"name": "GEA", "ok": True, "detail": "voll", "url": ""}]
        self.gespeichert = []
        self.push = []
        self.push_text = []
        self.patches = [
            mock.patch.object(SA.ff, "login_check_zwischenstand", return_value=None),
            mock.patch.object(SA.ff, "fehlende_logins",
                              side_effect=lambda headless=True: ([dict(e) for e in self.erg],
                                                                 [dict(self.erg[0])])),
            mock.patch.object(SA.ff, "_login_check_speichern", side_effect=self.gespeichert.append),
            mock.patch.object(SA, "_push_senden",
                              side_effect=lambda t, x, u="": self.push.append(t) or self.push_text.append(x) or True),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_ohne_eintrag_alter_weg_und_eine_push(self):
        with mock.patch.object(SA, "zugangsdaten_lesen", return_value=None), \
                mock.patch.object(SA, "eintrag_vorhanden", return_value=False), \
                mock.patch.object(SA.ff, "_launch", side_effect=AssertionError("kein Browser")):
            res = SA.logins_sicherstellen()
            self.assertEqual([e["name"] for e in res["fehlende"]], ["SWP/Tagblatt"])
            self.assertEqual(res["selbst"]["swp"]["status"], SA.KEIN_EINTRAG)
            self.assertEqual(res["push"], ["swp"])
            res2 = SA.logins_sicherstellen()   # selber Tag: keine zweite Push
            self.assertEqual(res2["push"], [])
        self.assertEqual(self.push, ["🔐 Briefing: SWP-Login klappt nicht – bitte anmelden"])

    def test_erfolg_dreht_eintrag_um_und_speichert(self):
        class P:
            def stop(self): pass

        class C:
            def close(self): pass
        ok = {"ziel": "swp", "name": "SWP/Tagblatt", "status": SA.OK, "grund": "angemeldet",
              "pruefung": {"ok": True, "detail": "3 Abo-Artikel vollständig", "url": "https://x.invalid/a"}}
        with mock.patch.object(SA, "zugangsdaten_lesen", return_value=(EMAIL, PW)), \
                mock.patch.object(SA.ff, "_launch", return_value=(P(), C())), \
                mock.patch.object(SA, "selbst_anmelden", return_value=ok) as sa:
            res = SA.logins_sicherstellen()
        self.assertEqual(res["fehlende"], [])
        self.assertEqual(res["push"], [])
        self.assertEqual(self.push, [])
        self.assertTrue(all(e["ok"] for e in res["ergebnis"]))
        self.assertEqual(sa.call_args[0][0], "swp")
        self.assertEqual(len(self.gespeichert), 1)
        self.assertTrue(all(e["ok"] for e in self.gespeichert[0]))

    def test_misserfolg_bleibt_fehlend_mit_grund_und_push(self):
        class P:
            def stop(self): pass

        class C:
            def close(self): pass
        schlecht = {"ziel": "swp", "name": "SWP/Tagblatt", "status": SA.FEHLGESCHLAGEN,
                    "grund": "Zugangsdaten abgelehnt", "pruefung": None}
        with mock.patch.object(SA, "zugangsdaten_lesen", return_value=(EMAIL, PW)), \
                mock.patch.object(SA.ff, "_launch", return_value=(P(), C())), \
                mock.patch.object(SA, "selbst_anmelden", return_value=schlecht):
            res = SA.logins_sicherstellen()
        self.assertEqual([e["name"] for e in res["fehlende"]], ["SWP/Tagblatt"])
        self.assertIn("Zugangsdaten abgelehnt", res["fehlende"][0]["detail"])
        self.assertEqual(res["push"], ["swp"])
        self.assertEqual(len(self.push_text), 1)
        self.assertIn("Zugangsdaten abgelehnt", self.push_text[0])

    def test_captcha_wird_gemeldet_nicht_umgangen(self):
        class P:
            def stop(self): pass

        class C:
            def close(self): pass
        cap = {"ziel": "swp", "name": "SWP/Tagblatt", "status": SA.CAPTCHA, "grund": "Bot-Schutz erkannt",
               "pruefung": None}
        with mock.patch.object(SA, "zugangsdaten_lesen", return_value=(EMAIL, PW)), \
                mock.patch.object(SA.ff, "_launch", return_value=(P(), C())), \
                mock.patch.object(SA, "selbst_anmelden", return_value=cap):
            res = SA.logins_sicherstellen()
        self.assertEqual(res["selbst"]["swp"]["status"], SA.CAPTCHA)
        self.assertEqual(res["push"], ["swp"])

    def test_frischer_zwischenstand_ohne_browser(self):
        frisch = [{"name": "SWP/Tagblatt", "ok": True, "detail": "", "url": ""},
                  {"name": "GEA", "ok": True, "detail": "", "url": ""}]
        with mock.patch.object(SA.ff, "login_check_zwischenstand", return_value=frisch), \
                mock.patch.object(SA.ff, "fehlende_logins", side_effect=AssertionError("kein Browser")):
            res = SA.logins_sicherstellen()
        self.assertEqual(res["fehlende"], [])
        self.assertFalse(res["frisch"])

    def test_notschalter(self):
        with mock.patch.dict(os.environ, {"BRIEFING_SELBST_ANMELDUNG": "0"}), \
                mock.patch.object(SA.ff, "login_check_cached", return_value=self.erg) as alt, \
                mock.patch.object(SA, "zugangsdaten_lesen", side_effect=AssertionError("kein Schluesselbund")):
            res = SA.logins_sicherstellen()
        alt.assert_called_once()
        self.assertEqual(len(res["fehlende"]), 1)

    def test_push_nie_wenn_push_false(self):
        with mock.patch.object(SA, "zugangsdaten_lesen", return_value=None), \
                mock.patch.object(SA, "eintrag_vorhanden", return_value=False):
            res = SA.logins_sicherstellen(push=False)
        self.assertEqual(res["push"], [])
        self.assertEqual(self.push, [])

    def test_kein_passwort_in_dateien(self):
        class P:
            def stop(self): pass

        class C:
            def close(self): pass
        schlecht = {"ziel": "swp", "name": "SWP/Tagblatt", "status": SA.FEHLGESCHLAGEN,
                    "grund": "abgelehnt", "pruefung": None}
        with mock.patch.object(SA, "zugangsdaten_lesen", return_value=(EMAIL, PW)), \
                mock.patch.object(SA.ff, "_launch", return_value=(P(), C())), \
                mock.patch.object(SA, "selbst_anmelden", return_value=schlecht):
            SA.logins_sicherstellen()
        text = self.alles_text() + json.dumps(self.gespeichert) + json.dumps(self.push)
        self.assertNotIn(PW, text)


class Anbindung(unittest.TestCase):
    def test_zwischenstand_aus_cache_ohne_browser(self):
        tmp = tempfile.mkdtemp(prefix="selbst_anm_test_")
        try:
            cache = os.path.join(tmp, "c.json")
            with mock.patch.object(SA.ff, "LOGIN_CHECK_CACHE", cache):
                self.assertIsNone(SA.ff.login_check_zwischenstand())
                SA.ff._login_check_speichern([{"name": "GEA", "ok": True, "detail": "x", "url": ""}])
                self.assertEqual(SA.ff.login_check_zwischenstand()[0]["name"], "GEA")
                self.assertIsNone(SA.ff.login_check_zwischenstand(max_alter_min=0))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_zielnamen(self):
        self.assertEqual(SA.ziel_zu_name("SWP/Tagblatt"), "swp")
        self.assertEqual(SA.ziel_zu_name("GEA"), "gea")
        self.assertIsNone(SA.ziel_zu_name("Feedly"))

    def test_gitignore_deckt_merkdatei(self):
        with open(os.path.join(SA.APP_DIR, ".gitignore"), encoding="utf-8") as fh:
            gi = fh.read().split()
        self.assertIn(".briefing_*.json", gi)
        self.assertTrue(os.path.basename(SA._zustand_pfad()).startswith(".briefing_"))


if __name__ == "__main__":
    unittest.main()
