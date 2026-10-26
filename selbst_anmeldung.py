"""Selbst-Anmeldung bei SWP/Tagblatt (swp.de) und GEA (gea.de).

21.10.2026: Florian will nicht mehr, dass ein Briefing an einem abgelaufenen
Zeitungs-Login haengt. Vor dem Artikel-Abruf prueft die App kurz, ob die Logins
tragen, und meldet sich bei Bedarf SELBST neu an — im selben persistenten
Browser-Profil, in dem auch der Abruf laeuft (~/.briefing_news_profile).

Zugangsdaten
    Florian legt sie selbst im macOS-Schluesselbund ab (siehe README, Abschnitt
    "Selbst-Anmeldung"): Dienst `briefing-login-swp` bzw. `briefing-login-gea`,
    Konto = E-Mail. Dieses Modul legt NIE Eintraege an und aendert keine. Es liest
    sie nur ueber `security find-generic-password` und haelt sie ausschliesslich im
    Arbeitsspeicher: nie in Logs, Dateien, Meldungen oder Commits — auch nicht
    gekuerzt. Texte, die aus Seitenmeldungen stammen, werden vorher von Konto und
    Passwort gereinigt.

Grenzen (bewusst)
    · Bot-Schutz (Captcha, Turnstile, reCAPTCHA, hCaptcha, DataDome ...) wird nicht
      umgangen: erkannt -> abbrechen und melden.
    · Zustimmungsbanner: nur "ablehnen"/"nur notwendige" wird geklickt, NIE
      "akzeptieren". Hat ein Banner keine solche Wahl (SWP zeigt in einem frischen
      Profil eine Pay-or-Consent-Wand), bleibt es stehen — die Anmeldung scheitert
      dann mit klarer Meldung, Florian entscheidet einmal im Anmeldefenster.
    · Falsches Passwort: nach EINER Ablehnung wird 6 Stunden lang nicht mehr
      automatisch angemeldet (Konto-Sperre vermeiden), hoechstens 4 Anlaeufe pro
      Zeitung und Tag. Der Knopf "Selbst-Anmeldung jetzt testen" setzt das ausser
      Kraft (Florian hat dann vermutlich das Passwort berichtigt).
    · Hartnaeckig nur 1 Push pro Zeitung und Tag, wenn ein Login fehlt bleibt.

Aufbau beider Login-Seiten (Stand 21.10.2026, anonym und nur lesend angesehen)
    GEA   https://www.gea.de/login.html — EINSTUFIG, das Formular steckt in einem
          Iframe von sso-v3.frisbii-media.com (Felder email, password, Haken
          rememberMe; der Knopf "Anmelden" ist deaktiviert, bis beide Felder
          gueltig sind). Zustimmungsbanner: OpenCMP ("Auswahl bestaetigen" =
          nur notwendige). Kein Bot-Schutz im Quelltext, auch nicht im Iframe.
    SWP   https://www.swp.de/ — EINSTUFIG, aber ein Dialog: erst der Knopf
          #header-login-button, dann #loginUsername (autocomplete=username),
          #loginPassword, Schalter loginPreserve ("Angemeldet bleiben", im Quelltext
          unsichtbar -> ueber sein Label klicken) und "Anmelden". Achtung: Das
          Honigtopf-Feld #companyWebsite muss LEER bleiben. Zustimmungsbanner:
          Sourcepoint — im frischen Profil nur "Akzeptieren & schliessen".
          Kein Bot-Schutz im Quelltext. (/mein-konto liefert anonym 404.)
    Der Code beherrscht zusaetzlich zweistufige Formulare (erst E-Mail, dann
    Passwort), falls eine der Seiten sie einmal einfuehrt.

CLI:  python3 selbst_anmeldung.py              (Status: Eintraege vorhanden?)
      python3 selbst_anmeldung.py --pruefen    (wie vor dem Abruf: pruefen, bei Bedarf anmelden)
      python3 selbst_anmeldung.py --testen [swp|gea]   (Probe: abmelden, selbst anmelden, nachpruefen;
                                                       bei Misserfolg Rollback der Cookies)
      Zusatz: --sichtbar (statt kopflos), --push (Push senden, sonst keine), --dry (Push nur ausgeben)
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import time

import browser_pfad  # noqa: F401  muss VOR jedem Playwright-Import stehen
import feedly_fetch as ff

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_URL = os.environ.get("BRIEFING_APP_URL", "http://macbook-pro:8501")
PUSHFUNK = os.path.expanduser("~/bin/pushfunk")
SECURITY = "/usr/bin/security"

# Rueckgabe von selbst_anmelden()["status"]
OK = "ok"
KEIN_EINTRAG = "kein_eintrag"
CAPTCHA = "captcha"
FEHLGESCHLAGEN = "fehlgeschlagen"

SPERRE_STUNDEN = 6          # nach abgelehnten Zugangsdaten so lange NICHT automatisch erneut
MAX_VERSUCHE_PRO_TAG = 4    # Anmelde-Anlaeufe je Zeitung und Tag (ohne erzwingen)
NACH_ABSENDEN_S = 25        # so lange auf die Antwort der Seite warten
FORMULAR_SUCHE_S = 14       # so lange auf das Anmeldeformular warten
FRUEHWARNUNG_TAGE = 3       # Push, wenn die Sitzung einer Zeitung nur noch so lange haelt

# Reihenfolge = Anzeigereihenfolge. "name" ist der Name im Login-Test
# (feedly_fetch.NEWSPAPER_PROBES), "domains" werden beim Test-Rollback angefasst.
ZEITUNGEN = {
    "swp": {
        "name": "SWP/Tagblatt", "kurz": "SWP", "dienst": "briefing-login-swp",
        "login_url": "https://www.swp.de/",
        "oeffnen": ("#header-login-button",),
        "domains": r"(^|\.)swp\.de$",
        # SWP laedt abgemeldet Googles reCAPTCHA -> Selbst-Anmeldung bricht dort ab. Darum
        # warnt die App vorher, wenn diese Sitzungs-Cookies (10 Tage) bald ablaufen.
        "sitzung_cookies": ("plenigoSession", "appSession"),
    },
    "gea": {
        "name": "GEA", "kurz": "GEA", "dienst": "briefing-login-gea",
        "login_url": "https://www.gea.de/login.html",
        "oeffnen": (),
        "domains": r"(^|\.)(gea\.de|frisbii-media\.com)$",
    },
}


# ── Kleinkram ──────────────────────────────────────────────────────────────

def _heute() -> str:
    return datetime.date.today().isoformat()


def _sage(progress):
    def say(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass
    return say


def _bereinigen(text, *geheim) -> str:
    """Entfernt Konto und Passwort (und den Teil der E-Mail vor dem @) aus einem Text,
    bevor er in eine Meldung, ein Log oder eine Push wandert."""
    t = str(text or "")
    teile = []
    for g in geheim:
        if g:
            teile.append(str(g))
            if "@" in str(g):
                lokal = str(g).split("@", 1)[0]
                if len(lokal) >= 3:
                    teile.append(lokal)
    for g in sorted(set(teile), key=len, reverse=True):
        t = t.replace(g, "***")
    return re.sub(r"\s+", " ", t).strip()


def _abgeschaltet() -> bool:
    return os.environ.get("BRIEFING_SELBST_ANMELDUNG", "1").strip() == "0"


def ziel_zu_name(name: str):
    """Name aus dem Login-Test ("GEA", "SWP/Tagblatt") -> Schluessel ("gea", "swp")."""
    for ziel, cfg in ZEITUNGEN.items():
        if cfg["name"] == name:
            return ziel
    return None


def _log(msg: str) -> None:
    """Eine Zeile ins eigene Log. Enthaelt nur Status und bereinigte Gruende."""
    pfad = os.environ.get("BRIEFING_SELBST_ANMELDUNG_LOG") or os.path.expanduser(
        "~/Library/Logs/briefing-selbst-anmeldung.log")
    try:
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        with open(pfad, "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# ── Schluesselbund (nur LESEN) ─────────────────────────────────────────────

def _security_aufrufen(args, timeout: int = 15):
    try:
        return subprocess.run([SECURITY] + list(args), capture_output=True,
                              text=True, timeout=timeout)
    except Exception:
        return None


_ACCT_RE = re.compile(r'"acct"<blob>=(?:0x([0-9A-Fa-f]+)\s+)?(?:"(.*)"|<NULL>)')


def _konto_parsen(text: str):
    """Liest das Konto (acct) aus der Attributausgabe von `security find-generic-password`."""
    m = _ACCT_RE.search(text or "")
    if not m:
        return None
    if m.group(1):
        try:
            return bytes.fromhex(m.group(1)).decode("utf-8") or None
        except Exception:
            pass
    return m.group(2) or None


def eintrag_vorhanden(ziel: str) -> bool:
    """Gibt es den Schluesselbund-Eintrag? Liest NUR die Attribute, nie das Passwort."""
    cfg = ZEITUNGEN.get(ziel)
    if not cfg:
        return False
    r = _security_aufrufen(["find-generic-password", "-s", cfg["dienst"]], timeout=6)
    return bool(r) and r.returncode == 0


def zugangsdaten_lesen(ziel: str):
    """(konto, passwort) aus dem Schluesselbund — oder None, wenn der Eintrag fehlt oder
    nicht lesbar ist (gesperrter Schluesselbund, verweigerter Zugriff). Nie loggen."""
    cfg = ZEITUNGEN.get(ziel)
    if not cfg:
        return None
    r1 = _security_aufrufen(["find-generic-password", "-s", cfg["dienst"]])
    if not r1 or r1.returncode != 0:
        return None
    konto = _konto_parsen(r1.stdout)
    if not konto:
        return None
    r2 = _security_aufrufen(["find-generic-password", "-s", cfg["dienst"], "-a", konto, "-w"])
    if not r2 or r2.returncode != 0:
        return None
    passwort = (r2.stdout or "").rstrip("\n")
    if not passwort:
        return None
    return konto, passwort


def _kein_eintrag_grund(ziel: str) -> str:
    cfg = ZEITUNGEN[ziel]
    if eintrag_vorhanden(ziel):
        return ("Schlüsselbund-Eintrag „%s“ ist da, aber nicht lesbar (Schlüsselbund gesperrt "
                "oder Zugriff verweigert)" % cfg["dienst"])
    return ("kein Schlüsselbund-Eintrag „%s“ — einrichten: im Terminal `briefing-logins`"
            % cfg["dienst"])


_status_cache = {"zeit": 0.0, "wert": None}


def status_anzeige(max_alter_s: int = 30) -> dict:
    """Fuer die Statuszeile in der App: {"swp": bool, "gea": bool, "letzter": {...}}.
    Liest nur Attribute (nie ein Passwort) und merkt sich das Ergebnis kurz, weil
    Streamlit die Seite bei jedem Klick neu zeichnet."""
    jetzt = time.time()
    if _status_cache["wert"] is not None and jetzt - _status_cache["zeit"] < max_alter_s:
        wert = dict(_status_cache["wert"])
    else:
        wert = {z: eintrag_vorhanden(z) for z in ZEITUNGEN}
        _status_cache.update(zeit=jetzt, wert=dict(wert))
    wert["letzter"] = _zustand_laden().get("letzter") or {}
    return wert


# ── Merkdatei: Push-Drossel, Sperre, Anlaeufe, letztes Ergebnis ────────────
# Liegt im App-Ordner; .gitignore faengt sie ueber ".briefing_*.json" ab.

def _zustand_pfad() -> str:
    return (os.environ.get("BRIEFING_SELBST_ANMELDUNG_ZUSTAND")
            or os.path.join(APP_DIR, ".briefing_selbst_anmeldung.json"))


def _zustand_laden() -> dict:
    try:
        with open(_zustand_pfad(), encoding="utf-8") as fh:
            z = json.load(fh)
        return z if isinstance(z, dict) else {}
    except Exception:
        return {}


def _zustand_aendern(fn) -> None:
    z = _zustand_laden()
    try:
        fn(z)
        pfad = _zustand_pfad()
        tmp = pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(z, fh, ensure_ascii=False)
        os.replace(tmp, pfad)
    except Exception:
        pass


def _push_erlaubt(ziel: str) -> bool:
    return (_zustand_laden().get("push") or {}).get(ziel) != _heute()


def _push_vermerken(ziel: str) -> None:
    _zustand_aendern(lambda z: z.setdefault("push", {}).__setitem__(ziel, _heute()))


def _sperre_grund(ziel: str):
    """Text, wenn nach abgelehnten Zugangsdaten noch gesperrt ist — sonst None."""
    ab = (_zustand_laden().get("abgelehnt") or {}).get(ziel)
    if ab and time.time() - float(ab) < SPERRE_STUNDEN * 3600:
        vor = int((time.time() - float(ab)) / 60)
        return ("Zugangsdaten wurden vor %d Min. abgelehnt — kein neuer Versuch, bevor das "
                "Passwort im Schlüsselbund berichtigt ist (Sperre %d Std.; der Knopf "
                "„Selbst-Anmeldung jetzt testen“ hebt sie auf)" % (vor, SPERRE_STUNDEN))
    return None


def _abgelehnt_vermerken(ziel: str) -> None:
    _zustand_aendern(lambda z: z.setdefault("abgelehnt", {}).__setitem__(ziel, time.time()))


def _abgelehnt_loeschen(ziel: str) -> None:
    _zustand_aendern(lambda z: (z.get("abgelehnt") or {}).pop(ziel, None))


def _anlauf_zaehlen(ziel: str, erzwingen: bool):
    """Zaehlt einen Anmelde-Anlauf. Rueckgabe: Text, wenn das Tageslimit erreicht ist."""
    z = _zustand_laden()
    eintrag = (z.get("anlaeufe") or {}).get(ziel) or {}
    n = eintrag.get("n", 0) if eintrag.get("tag") == _heute() else 0
    if n >= MAX_VERSUCHE_PRO_TAG and not erzwingen:
        return "heute schon %d Anmelde-Anläufe — erst morgen wieder (oder Knopf „Selbst-Anmeldung jetzt testen“)" % n

    def setze(zz):
        zz.setdefault("anlaeufe", {})[ziel] = {"tag": _heute(), "n": n + 1}
    _zustand_aendern(setze)
    return None


def _letztes_merken(ziel: str, status: str, grund: str) -> None:
    def setze(z):
        z.setdefault("letzter", {})[ziel] = {
            "zeit": datetime.datetime.now().isoformat(timespec="minutes"),
            "status": status, "grund": (grund or "")[:200]}
    _zustand_aendern(setze)


# ── Browser-Bausteine ──────────────────────────────────────────────────────

# Zustimmungsbanner: NUR Ablehnen-Knoepfe. Volltreffer auf den Knopftext, damit z. B.
# „EINSTELLUNGEN ODER ABLEHNEN“ (OpenCMP, oeffnet nur die Einstellungen) nicht
# versehentlich mitspielt. Nie etwas mit „akzeptieren“, „zustimmen“, „einverstanden“.
# „Auswahl bestaetigen“ (OpenCMP) steht zuletzt: es uebernimmt die Voreinstellung, und
# die ist dort „nur notwendige“.
_ABLEHNEN = (
    r"alle ablehnen", r"nur notwendige( cookies)?", r"nur erforderliche( cookies)?",
    r"nur technisch notwendige( cookies)?", r"nur essenzielle( cookies)?",
    r"ablehnen", r"ablehnen und schlie(ß|ss)en", r"reject all", r"decline( all)?",
    r"auswahl bestätigen",
)

_BOT_RAHMEN_RE = re.compile(
    r"recaptcha|hcaptcha|turnstile|challenges\.cloudflare|captcha-delivery|datadome|"
    r"arkoselabs|funcaptcha|geetest|perimeterx|px-cloud|altcha|friendlycaptcha|/captcha",
    re.I)

_BOT_TITEL_RE = re.compile(
    r"just a moment|attention required|access denied|pardon our interruption|"
    r"are you a robot|bist du ein roboter|sicherheitsüberprüfung", re.I)

# Sucht im DOM nach Bot-Schutz: Elemente mit "captcha" in Klasse/Id/Name/Quelle, Widgets,
# Skripte der bekannten Anbieter — und die typischen Aufforderungssaetze im Text.
_BOT_DOM_JS = r"""
() => {
  const sel = '[class*="captcha" i], [id*="captcha" i], [name*="captcha" i], [src*="captcha" i], '
    + '[data-sitekey], [class*="turnstile" i], [id*="px-captcha" i], '
    + 'iframe[src*="turnstile" i], iframe[src*="challenges.cloudflare" i], iframe[src*="hcaptcha" i], '
    + 'iframe[src*="recaptcha" i], iframe[src*="datadome" i], script[src*="recaptcha" i], '
    + 'script[src*="turnstile" i], script[src*="hcaptcha" i], script[src*="datadome" i]';
  const el = document.querySelector(sel);
  if (el) {
    const k = (el.className && el.className.toString) ? el.className.toString() : '';
    return (el.tagName + (el.id ? '#' + el.id : '') + (k ? '.' + k : '')).slice(0, 70);
  }
  const t = ((document.body && document.body.innerText) || '').toLowerCase();
  const m = t.match(/ich bin kein roboter|i'm not a robot|i am not a robot|verify you are human|sicherheitsabfrage|sicherheitsüberprüfung|ungewöhnlicher datenverkehr|unusual traffic|bestätigen sie, dass sie (ein )?mensch/);
  return m ? ('Text: ' + m[0]) : '';
}
"""

_FEHLER_RE = re.compile(
    r"falsch|ungültig|ungueltig|nicht korrekt|stimmen nicht|nicht übereinstimmen|"
    r"fehlgeschlagen|gesperrt|zu viele|nicht erkannt|nicht bekannt|unbekannt|"
    r"nicht gefunden|incorrect|invalid|wrong|failed|locked|not found|fehler", re.I)

_BENUTZER_RE = re.compile(r"mail|user|login|benutzer|konto|kennung", re.I)
_HONIGTOPF_RE = re.compile(r"website|company|honey|homepage|fax|trap|search|suche", re.I)
_MERKEN_RE = re.compile(r"remember|preserve|stay|angemeldet|eingeloggt|merken|keep", re.I)
_KNOPF_TEXT_RE = re.compile(
    r"^\s*(anmelden|einloggen|login|log in|sign in|weiter|continue|absenden)\s*$", re.I)
_REGISTRIERUNG_RE = re.compile(r"registr|newsletter|vorname|nachname|konto erstellen", re.I)
_LOGIN_WORT_RE = re.compile(r"anmeld|login|log in|einlogg|sign in|zugang|mein konto", re.I)

# JS: vom Passwortfeld aus aufwaerts bis zum Formular — oder bis zum ersten Vorfahren, der
# einen Absende-Knopf enthaelt (der SWP-Dialog muss kein <form> sein). Liefert die Zahl der
# Ebenen (1 = direkter Elternknoten) oder 0.
_BEREICH_JS = """
el => {
  let n = 0, p = el;
  while (p.parentElement && n < 10) {
    p = p.parentElement; n++;
    if (p.tagName === 'FORM') return n;
    if (p.querySelector('button[type=submit], input[type=submit]')) return n;
  }
  return 0;
}
"""


def _erste_sichtbare(loc, maximal: int = 6):
    try:
        for i in range(min(loc.count(), maximal)):
            el = loc.nth(i)
            if el.is_visible():
                return el
    except Exception:
        pass
    return None


def _banner_wegklicken(page) -> str:
    """Klickt ein Zustimmungsbanner mit „ablehnen“/„nur notwendige“ weg. Rueckgabe: das
    benutzte Muster ("" = nichts geklickt). Klickt nie „akzeptieren“."""
    for muster in _ABLEHNEN:
        rx = re.compile(r"^\s*(?:" + muster + r")\s*$", re.I)
        for fr in list(page.frames):
            try:
                el = _erste_sichtbare(fr.get_by_text(rx), 4)
                if el is not None:
                    el.click(timeout=2500)
                    page.wait_for_timeout(800)
                    return muster
            except Exception:
                continue
    return ""


def _captcha_erkannt(page) -> str:
    """Beschreibung des Bot-Schutzes — oder "" , wenn keiner zu sehen ist."""
    try:
        if _BOT_TITEL_RE.search(page.title() or ""):
            return "Seitentitel: " + (page.title() or "")[:40]
    except Exception:
        pass
    for fr in list(page.frames):
        try:
            if fr != page.main_frame and _BOT_RAHMEN_RE.search(fr.url or ""):
                host = re.sub(r"^https?://([^/]+).*$", r"\1", fr.url or "")
                return "Rahmen von " + host[:50]
            treffer = fr.evaluate(_BOT_DOM_JS)
            if treffer:
                return treffer
        except Exception:
            continue
    return ""


def _passwortfeld(page):
    """(frame, locator) des sichtbaren Passwortfelds — in der Seite oder einem Iframe."""
    for fr in list(page.frames):
        try:
            el = _erste_sichtbare(fr.locator("input[type=password]"), 3)
            if el is not None:
                return fr, el
        except Exception:
            continue
    return None


def _bereich(feld):
    """Locator des Formularbereichs um ein Feld (Formular oder naechster Vorfahr mit Knopf)."""
    try:
        n = feld.evaluate(_BEREICH_JS)
        if n:
            return feld.locator("xpath=ancestor::*[%d]" % int(n))
    except Exception:
        pass
    return None


def _benutzerfeld(scope):
    """Das E-Mail-/Benutzerfeld im Bereich `scope` (Frame oder Locator). Nimmt nur Felder,
    die erkennbar dafuer da sind; Honigtopf-Felder (z. B. SWP #companyWebsite) und
    Suchfelder bleiben unberuehrt."""
    beste = None
    try:
        felder = scope.locator("input")
        for i in range(min(felder.count(), 30)):
            el = felder.nth(i)
            try:
                if not el.is_visible():
                    continue
                a = el.evaluate(
                    "e => ({t:(e.type||'text').toLowerCase(), n:e.name||'', i:e.id||'', "
                    "ac:(e.getAttribute('autocomplete')||'').toLowerCase(), ph:e.placeholder||'', "
                    "al:e.getAttribute('aria-label')||'', ro:!!(e.readOnly||e.disabled)})")
            except Exception:
                continue
            if a["t"] not in ("email", "text", "tel", "") or a["ro"]:
                continue
            kenn = " ".join((a["n"], a["i"], a["ph"], a["al"]))
            if _HONIGTOPF_RE.search(kenn):
                continue
            if a["ac"] in ("username", "email"):
                rang = 0
            elif a["t"] == "email":
                rang = 1
            elif _BENUTZER_RE.search(kenn):
                rang = 2
            else:
                continue
            if beste is None or rang < beste[0]:
                beste = (rang, el)
    except Exception:
        pass
    return beste[1] if beste else None


def _zweistufig_kandidat(page):
    """Erstes Feld einer zweistufigen Anmeldung (E-Mail ohne Passwortfeld) — streng:
    nur ein Feld, das sich als Benutzername/E-Mail ausweist, in einem Bereich mit
    Absende-Knopf, dessen Text nach Anmeldung klingt und nicht nach Registrierung/
    Newsletter. Sonst koennte die E-Mail in ein Newsletter-Formular wandern (die
    SWP-Startseite hat eines)."""
    for fr in list(page.frames):
        try:
            el = _benutzerfeld(fr)
            if el is None:
                continue
            a = el.evaluate("e => ({t:(e.type||'').toLowerCase(), ac:(e.getAttribute('autocomplete')||'').toLowerCase()})")
            if not (a["t"] == "email" or a["ac"] in ("username", "email")):
                continue
            bereich = _bereich(el)
            if bereich is None:
                continue
            text = bereich.inner_text() or ""
            if _REGISTRIERUNG_RE.search(text) or not _LOGIN_WORT_RE.search(text):
                continue
            return fr, el, bereich
        except Exception:
            continue
    return None


def _tippen(feld, wert: str) -> None:
    """Klickt ins Feld (scheitert, wenn ein Banner es verdeckt) und tippt Zeichen fuer
    Zeichen, damit Eingabepruefungen der Seite anspringen. Der Wert wird nie geloggt."""
    feld.click(timeout=4000)
    try:
        feld.fill("")
    except Exception:
        pass
    feld.press_sequentially(wert, delay=20)
    try:
        if feld.input_value() != wert:
            feld.fill(wert)
    except Exception:
        pass


def _angemeldet_bleiben(bereich) -> object:
    """Hakt „angemeldet bleiben“ an, falls es das gibt. True/False = Zustand danach,
    None = kein solcher Schalter."""
    if bereich is None:
        return None
    try:
        boxen = bereich.locator("input[type=checkbox]")
        for i in range(min(boxen.count(), 10)):
            el = boxen.nth(i)
            info = el.evaluate(
                "e => ({n:e.name||'', i:e.id||'', "
                "l:((e.labels && e.labels[0]) ? e.labels[0].innerText : "
                "(e.closest('label') ? e.closest('label').innerText : ''))})")
            if not _MERKEN_RE.search(" ".join((info["n"], info["i"], info["l"]))):
                continue
            if el.is_checked():
                return True
            try:
                el.check(timeout=1500)
            except Exception:
                pass
            if not el.is_checked():
                # Unsichtbarer Schalter (SWP: u-switch__input): ueber sein Label klicken.
                try:
                    el.evaluate("e => { const l = (e.labels && e.labels[0]) || e.closest('label') "
                                "|| e.parentElement; l.click(); }")
                except Exception:
                    pass
            return bool(el.is_checked())
    except Exception:
        pass
    return None


def _absenden(bereich, passwortfeld=None, nur_weiter: bool = False) -> bool:
    """Klickt den Absende-Knopf im Bereich. Bevorzugt Knoepfe mit Anmelde-/Weiter-Text;
    wartet kurz, bis ein anfangs deaktivierter Knopf (GEA) frei wird. Ohne brauchbaren
    Knopf: Enter im Passwortfeld."""
    knoepfe = None
    if bereich is not None:
        knoepfe = bereich.locator("button, input[type=submit]")
    ende = time.time() + 4
    while knoepfe is not None and time.time() < ende:
        try:
            kandidaten = []
            for i in range(min(knoepfe.count(), 12)):
                k = knoepfe.nth(i)
                if not k.is_visible():
                    continue
                text = (k.inner_text() or k.get_attribute("value") or "").strip()
                typ = (k.get_attribute("type") or "").lower()
                if _KNOPF_TEXT_RE.match(text) or typ == "submit":
                    kandidaten.append((0 if _KNOPF_TEXT_RE.match(text) else 1, i, k))
            kandidaten.sort(key=lambda x: (x[0], x[1]))
            for _rang, _i, k in kandidaten:
                if k.is_enabled():
                    k.click(timeout=4000)
                    return True
        except Exception:
            pass
        time.sleep(0.4)
    if passwortfeld is not None:
        try:
            passwortfeld.press("Enter")
            return True
        except Exception:
            pass
    return False


def _fehlerzeilen(page) -> set:
    """Zeilen im Formularbereich (und in Hinweis-Kaesten), die nach einer Fehlermeldung
    klingen. Wird vor und nach dem Absenden verglichen: nur NEUE Zeilen zaehlen."""
    zeilen = set()
    try:
        gefunden = _passwortfeld(page)
        if gefunden is None:
            return zeilen
        fr, pw = gefunden
        texte = []
        bereich = _bereich(pw)
        if bereich is not None:
            texte.append(bereich.inner_text() or "")
        kaesten = fr.locator('[role="alert"], [class*="error" i], [class*="fehler" i], '
                             '[class*="alert" i], [class*="invalid" i]')
        for i in range(min(kaesten.count(), 8)):
            k = kaesten.nth(i)
            if k.is_visible():
                texte.append(k.inner_text() or "")
        for t in texte:
            for zeile in t.splitlines():
                zeile = zeile.strip()
                if zeile and _FEHLER_RE.search(zeile):
                    zeilen.add(zeile[:160])
    except Exception:
        pass
    return zeilen


def _ergebnis_abwarten(page, fehler_vorher: set, frist_s: int):
    """Wartet auf die Antwort der Seite. Rueckgabe (art, info):
    captcha | abgelehnt (info = Meldung der Seite) | weg (Formular verschwunden) |
    keine_reaktion."""
    ende = time.time() + frist_s
    while time.time() < ende:
        try:
            c = _captcha_erkannt(page)
            if c:
                return "captcha", c
            if _passwortfeld(page) is None:
                return "weg", ""
            neu = sorted(_fehlerzeilen(page) - fehler_vorher)
            if neu:
                return "abgelehnt", " / ".join(neu[:2])
        except Exception:
            pass   # Seite laedt gerade um
        page.wait_for_timeout(600)
    return "keine_reaktion", ""


def _formular_finden(page, cfg: dict):
    """Wartet auf das Anmeldeformular und oeffnet bei Bedarf den Dialog (SWP).
    Rueckgabe: ("einstufig", frame, passwortfeld) | ("zweistufig", frame, benutzerfeld) |
    (None, grund, None)."""
    start = time.time()
    ende = start + FORMULAR_SUCHE_S
    oeffner_versucht = False
    blockiert = False
    while time.time() < ende:
        pw = _passwortfeld(page)
        if pw:
            return "einstufig", pw[0], pw[1]
        vergangen = time.time() - start
        if not oeffner_versucht and vergangen >= 2:
            oeffner_versucht = True
            # Erst die bekannten Knoepfe, sonst ein Knopf, der nur „Login“/„Anmelden“ heisst.
            kandidaten = [page.locator(s) for s in cfg.get("oeffnen") or ()]
            kandidaten.append(page.get_by_role("button", name=re.compile(
                r"^\s*(login|anmelden|einloggen)\s*$", re.I)))
            for loc in kandidaten:
                knopf = _erste_sichtbare(loc, 3)
                if knopf is None:
                    continue
                try:
                    knopf.click(timeout=4000)
                    page.wait_for_timeout(1200)
                    break
                except Exception as exc:
                    if "intercepts pointer events" in str(exc):
                        blockiert = True
        if oeffner_versucht and vergangen >= 5:
            z = _zweistufig_kandidat(page)
            if z:
                return "zweistufig", z[0], z[1]
        page.wait_for_timeout(500)
    if blockiert or _banner_blockiert(page):
        return None, ("ein Zustimmungsbanner ohne „Ablehnen“-Möglichkeit verdeckt die Seite — "
                      "einmal im Anmeldefenster entscheiden"), None
    return None, ("kein Anmeldeformular gefunden (Seitenaufbau geändert oder die Seite "
                  "kam nicht richtig an)"), None


_BANNER_RAHMEN_RE = re.compile(
    r"privacy-mgmt|sourcepoint|sp-prod|consent|usercentrics|didomi|onetrust|cookiebot|opencmp|quantcast",
    re.I)


def _banner_blockiert(page) -> bool:
    """Steht ein Zustimmungsbanner (Rahmen eines bekannten Anbieters oder CMP-Container) da?"""
    try:
        for fr in list(page.frames):
            if fr != page.main_frame and _BANNER_RAHMEN_RE.search(fr.url or ""):
                return True
        return bool(page.evaluate(
            "() => !!document.querySelector('[class*=\"cmp-root\" i], [id*=\"sp_message\" i], "
            "[id*=\"usercentrics\" i], [id*=\"onetrust\" i], [class*=\"cookie-banner\" i]')"))
    except Exception:
        return False


def _anmelde_ablauf(page, cfg: dict, konto: str, passwort: str):
    """Der Browser-Teil: Seite oeffnen, Banner ablehnen, Formular ausfuellen, absenden,
    Antwort abwarten. Rueckgabe (art, info) wie _ergebnis_abwarten, dazu "kein_formular"
    und "technisch". KEINE Nachpruefung hier."""
    ff._login_seite_laden(page, cfg["login_url"])
    page.wait_for_timeout(2500)
    _banner_wegklicken(page)   # lehnt nur ab; kommt ein Banner spaeter, faengt die Schleife unten ihn
    c = _captcha_erkannt(page)
    if c:
        return "captcha", c

    art, frame, feld = _formular_finden(page, cfg)
    if art is None:
        if _banner_wegklicken(page):   # Banner kam verzoegert — noch ein Versuch
            art, frame, feld = _formular_finden(page, cfg)
    if art is None:
        return "kein_formular", frame   # `frame` traegt hier den Grund
    c = _captcha_erkannt(page)
    if c:
        return "captcha", c

    try:
        if art == "zweistufig":
            bereich1 = _bereich(feld)
            _tippen(feld, konto)
            if not _absenden(bereich1):
                return "technisch", "Weiter-Knopf der ersten Stufe nicht gefunden"
            ende = time.time() + 12
            while time.time() < ende and _passwortfeld(page) is None:
                c = _captcha_erkannt(page)
                if c:
                    return "captcha", c
                page.wait_for_timeout(500)
            gefunden = _passwortfeld(page)
            if gefunden is None:
                neu = sorted(_fehlerzeilen(page))
                return ("abgelehnt", neu[0]) if neu else ("technisch", "zweite Stufe (Passwort) erschien nicht")
            frame, pwfeld = gefunden
            bereich = _bereich(pwfeld)
        else:
            pwfeld = feld
            bereich = _bereich(pwfeld)
            benutzer = _benutzerfeld(bereich if bereich is not None else frame)
            if benutzer is None:
                return "technisch", "kein E-Mail-/Benutzerfeld im Anmeldeformular gefunden"
            _tippen(benutzer, konto)

        _tippen(pwfeld, passwort)
        _angemeldet_bleiben(bereich)
        vorher = _fehlerzeilen(page)
        c = _captcha_erkannt(page)   # manche Seiten laden den Schutz erst beim Tippen
        if c:
            return "captcha", c
        if not _absenden(bereich, pwfeld):
            return "technisch", "Absende-Knopf nicht gefunden"
    except Exception as exc:
        text = str(exc)
        if "intercepts pointer events" in text:
            return "kein_formular", ("ein Zustimmungsbanner ohne „Ablehnen“-Möglichkeit verdeckt "
                                     "das Formular — einmal im Anmeldefenster entscheiden")
        return "technisch", "%s: %s" % (exc.__class__.__name__, _bereinigen(text, konto, passwort)[:160])

    return _ergebnis_abwarten(page, vorher, NACH_ABSENDEN_S)


# ── Selbst-Anmeldung einer Zeitung ─────────────────────────────────────────

def _pruef_url(cfg: dict):
    return dict(ff.NEWSPAPER_PROBES).get(cfg["name"])


# Cookies, die eine fruehere Zustimmungsentscheidung merken (Sourcepoint, OpenCMP,
# Usercentrics, OneTrust ...). Beim Test-Abmelden bleiben sie stehen, sonst stuende die
# Zustimmungswand (SWP: ohne „Ablehnen“) wieder da, und der Test pruefte nur das Banner.
_CONSENT_COOKIE_RE = re.compile(
    r"consent|euconsent|^_?sp_|^sp_|cmp|gdpr|usprivacy|usnat|optanon|opencmp|usercentrics|"
    r"cookie_?(notice|banner|policy|optin|accept)|didomi|cookiebot|ccpa|__gads_consent", re.I)


def _zeitung_cookies_loeschen(ctx, cfg: dict) -> int:
    """Meldet die Zeitung im Profil ab (Test): loescht ihre Cookies, ausser denen, die nur
    die Zustimmungsentscheidung merken. Rueckgabe: Anzahl geloeschter Cookies."""
    dom = re.compile(cfg["domains"])
    alle = ctx.cookies()
    weg = [c for c in alle
           if dom.search((c.get("domain") or "").lstrip("."))
           and not _CONSENT_COOKIE_RE.search(c.get("name") or "")]
    if not weg:
        return 0
    behalten = [c for c in alle if c not in weg]
    ctx.clear_cookies()
    if behalten:
        ctx.add_cookies(behalten)
    return len(weg)


def selbst_anmelden(ziel: str, page_or_ctx=None, *, erzwingen: bool = False,
                    headless: bool = True, nachpruefen: bool = True,
                    neu: bool = False, zugang=None, progress=None) -> dict:
    """Meldet sich bei EINER Zeitung selbst an — im Profil, das `page_or_ctx` gehoert.

    ziel         "swp" oder "gea".
    page_or_ctx  Playwright-Page oder -BrowserContext (das persistente Profil). Ohne Angabe
                 startet die Funktion das Profil selbst (kopflos) und schliesst es wieder.
    erzwingen    Sperre nach abgelehnten Zugangsdaten und Tageslimit ignorieren.
    neu          Test mit Rollback: loescht zuerst die Cookies dieser Zeitung (Abmelden),
                 meldet sich an, und stellt sie wieder her, falls das nicht klappt.
    zugang       (konto, passwort) schon gelesen — sonst liest die Funktion den Schluesselbund.

    Rueckgabe: {"ziel", "name", "status": ok|kein_eintrag|captcha|fehlgeschlagen,
                "grund": str, "pruefung": Eintrag des Login-Tests oder None}.
    Passwoerter erscheinen nirgends im Ergebnis."""
    cfg = ZEITUNGEN.get(ziel)
    if cfg is None:
        raise ValueError("unbekannte Zeitung: %r" % (ziel,))
    say = _sage(progress)

    def fertig(status, grund, pruefung=None, konto=None, passwort=None):
        grund = _bereinigen(grund, konto, passwort)
        _log("%s: %s — %s" % (cfg["kurz"], status, grund))
        _letztes_merken(ziel, status, grund)
        return {"ziel": ziel, "name": cfg["name"], "status": status, "grund": grund,
                "pruefung": pruefung}

    if zugang is None:
        zugang = zugangsdaten_lesen(ziel)
    if not zugang:
        return fertig(KEIN_EINTRAG, _kein_eintrag_grund(ziel))
    konto, passwort = zugang

    if not erzwingen:
        sperre = _sperre_grund(ziel)
        if sperre:
            return fertig(FEHLGESCHLAGEN, sperre, konto=konto, passwort=passwort)
    limit = _anlauf_zaehlen(ziel, erzwingen)
    if limit:
        return fertig(FEHLGESCHLAGEN, limit, konto=konto, passwort=passwort)

    p = eigener_ctx = None
    ctx = None
    schnappschuss = None
    ergebnis = None
    try:
        if page_or_ctx is None:
            p, eigener_ctx = ff._launch(headless=headless)
            ctx = eigener_ctx
            page = ff._page(ctx)
        elif hasattr(page_or_ctx, "new_page"):
            ctx = page_or_ctx
            page = ff._page(ctx)
        else:
            page = page_or_ctx
            ctx = getattr(page, "context", None)

        if neu and ctx is not None:
            schnappschuss = ctx.cookies()
            _zeitung_cookies_loeschen(ctx, cfg)

        say("%s: melde mich selbst an…" % cfg["kurz"])
        art, info = _anmelde_ablauf(page, cfg, konto, passwort)
        info = _bereinigen(info, konto, passwort)

        if art == "captcha":
            ergebnis = fertig(CAPTCHA, "Bot-Schutz erkannt (%s) — nicht umgangen, "
                              "bitte von Hand anmelden" % info, konto=konto, passwort=passwort)
        elif art == "abgelehnt":
            _abgelehnt_vermerken(ziel)
            ergebnis = fertig(FEHLGESCHLAGEN, "Zugangsdaten abgelehnt — die Seite meldet: „%s“. "
                              "Passwort im Schlüsselbund prüfen" % info, konto=konto, passwort=passwort)
        elif art in ("kein_formular", "technisch"):
            ergebnis = fertig(FEHLGESCHLAGEN, info, konto=konto, passwort=passwort)
        else:
            # "weg" (Formular verschwunden) oder "keine_reaktion": entscheidend ist,
            # ob ein ECHTER Abo-Artikel wieder vollstaendig kommt.
            pruefung = None
            if nachpruefen:
                say("%s: prüfe mit echten Abo-Artikeln…" % cfg["kurz"])
                url = _pruef_url(cfg)
                pruefung = ff._zeitung_pruefen(page, cfg["name"], url) if url else None
            if pruefung is None and not nachpruefen:
                ergebnis = fertig(OK, "Formular abgeschickt (ohne Nachprüfung)", konto=konto, passwort=passwort)
            elif pruefung is not None and pruefung.get("ok") is True:
                _abgelehnt_loeschen(ziel)
                ergebnis = fertig(OK, "angemeldet — %s" % pruefung.get("detail", ""),
                                  pruefung, konto=konto, passwort=passwort)
            else:
                detail = (pruefung or {}).get("detail") or "keine Nachprüfung möglich"
                if art == "keine_reaktion":
                    grund = ("das Formular reagierte nicht innerhalb von %d s — %s"
                             % (NACH_ABSENDEN_S, detail))
                else:
                    grund = "Formular abgeschickt, aber %s" % detail
                ergebnis = fertig(FEHLGESCHLAGEN, grund, pruefung, konto=konto, passwort=passwort)
    except Exception as exc:
        ergebnis = fertig(FEHLGESCHLAGEN, "%s: %s" % (exc.__class__.__name__, str(exc)[:160]),
                          konto=konto, passwort=passwort)
    finally:
        ok = bool(ergebnis and ergebnis["status"] == OK)
        if ctx is not None and neu and not ok and schnappschuss is not None:
            # Rollback: das Profil darf durch den Test nicht schlechter werden.
            try:
                ctx.clear_cookies()
                ctx.add_cookies(schnappschuss)
            except Exception:
                pass
        if ctx is not None and ok:
            try:
                ff._sitzung_sichern(ctx, erzwingen=True)   # GEA: Sitzungs-Cookies sichern
            except Exception:
                pass
        if eigener_ctx is not None:
            try:
                eigener_ctx.close()
            except Exception:
                pass
        if p is not None:
            try:
                p.stop()
            except Exception:
                pass
    return ergebnis


# ── Push (hoechstens 1 pro Zeitung und Tag) ────────────────────────────────

def _push_senden(titel: str, text: str, url: str = "") -> bool:
    """Eine Push ueber Pushfunk. BRIEFING_PUSH_DRY=1: nichts senden, nur ausgeben.
    Rueckgabe True, wenn der Server sie angenommen hat."""
    if os.environ.get("BRIEFING_PUSH_DRY", "").strip() not in ("", "0"):
        print("[dry] Push: %s | %s | %s" % (titel, text, url), file=sys.stderr)
        return True
    args = [PUSHFUNK, "pushfunk", titel, text]
    if url:
        args += ["--url", url]
    for lauf in range(2):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=40)
            if r.returncode == 0:
                return True
        except Exception:
            pass
        if lauf == 0:
            time.sleep(5)
    return False


def _push_wenn_neu(ziel: str, grund: str) -> bool:
    """Schickt die Push „🔐 Briefing: SWP-Login klappt nicht – bitte anmelden“, wenn es
    fuer diese Zeitung heute noch keine gab. Merkt sich den Tag nur nach Erfolg
    (und nicht im Probelauf BRIEFING_PUSH_DRY)."""
    if not _push_erlaubt(ziel):
        return False
    cfg = ZEITUNGEN[ziel]
    titel = "🔐 Briefing: %s-Login klappt nicht – bitte anmelden" % cfg["kurz"]
    text = "%s\nIn der Briefing-App „🔓 Anmeldefenster öffnen“ drücken." % (grund or "Grund unbekannt")
    if _push_senden(titel, text[:300], APP_URL):
        if os.environ.get("BRIEFING_PUSH_DRY", "").strip() in ("", "0"):
            _push_vermerken(ziel)
        return True
    return False


# ── Fruehwarnung: Sitzung laeuft bald ab ───────────────────────────────────

def sitzung_rest_tage(ziel: str):
    """Tage, die die Sitzungs-Cookies der Zeitung noch gelten (das kleinste der genannten),
    oder None (keine Angabe / nicht angemeldet). Liest nur Name und Ablauf aus einer Kopie
    der Cookie-Datenbank des Profils — nie die (verschluesselten) Werte."""
    cfg = ZEITUNGEN.get(ziel) or {}
    namen = cfg.get("sitzung_cookies")
    db = os.path.join(ff.PROFILE_DIR, "Default", "Cookies")
    if not namen or not os.path.exists(db):
        return None
    import shutil
    import sqlite3
    import tempfile
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        shutil.copyfile(db, tmp)
        con = sqlite3.connect(tmp)
        try:
            zeilen = con.execute(
                "SELECT host_key, expires_utc FROM cookies WHERE name IN (%s)"
                % ",".join("?" * len(namen)), tuple(namen)).fetchall()
        finally:
            con.close()
    except Exception:
        return None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass
    dom = re.compile(cfg["domains"])
    # expires_utc: Mikrosekunden seit 1601-01-01; 0 = Sitzungs-Cookie ohne Ablaufdatum
    enden = [int(e) / 1e6 - 11644473600 for h, e in zeilen
             if dom.search((h or "").lstrip(".")) and int(e or 0) > 0]
    if not enden:
        return None
    return (min(enden) - time.time()) / 86400


def _fruehwarnung(res: dict, push: bool) -> None:
    """Haelt die Sitzung einer Zeitung nur noch < FRUEHWARNUNG_TAGE und ist sie gerade noch
    angemeldet: eine Push je Zeitung und Tag, damit Florian sich in Ruhe von Hand anmeldet,
    bevor ein Briefing ohne sie entsteht. Ergebnis in res["fruehwarnung"] = {ziel: tage}."""
    for z, cfg in ZEITUNGEN.items():
        if not cfg.get("sitzung_cookies"):
            continue
        tage = sitzung_rest_tage(z)
        if tage is None or tage <= 0 or tage >= FRUEHWARNUNG_TAGE:
            continue
        res.setdefault("fruehwarnung", {})[z] = round(tage, 1)
        if not push:
            continue
        stand = _zustand_laden().get("fruehwarnung") or {}
        if stand.get(z) == _heute():
            continue
        wann = "heute" if tage < 1 else ("morgen" if tage < 2 else "in %d Tagen" % int(tage))
        titel = "⏳ Briefing: %s-Anmeldung läuft %s ab" % (cfg["kurz"], wann)
        text = ("%s hat einen Bot-Schutz, dort kann die App sich nicht selbst anmelden. "
                "Bitte einmal in der Briefing-App „🔓 Anmeldefenster öffnen“ und anmelden "
                "(Bitwarden füllt aus)." % cfg["kurz"])
        if _push_senden(titel, text, APP_URL):
            _log("%s: Frühwarnung gesendet (Sitzung noch %.1f Tage)" % (cfg["kurz"], tage))
            if os.environ.get("BRIEFING_PUSH_DRY", "").strip() in ("", "0"):
                _zustand_aendern(lambda zu: zu.setdefault("fruehwarnung", {}).__setitem__(z, _heute()))


# ── Alles zusammen: vor jedem Abruf ────────────────────────────────────────

def logins_sicherstellen(progress=None, headless: bool = True, max_alter_min: int = 180,
                         erzwingen: bool = False, neu_anmelden: bool = False,
                         push: bool = True) -> dict:
    """Prueft die Zeitungs-Logins und meldet sich bei Bedarf selbst neu an.

    Ablauf: Zwischenspeicher (frisch und alles ok -> fertig, ohne Browser) · sonst FRISCH
    pruefen (feedly_fetch.fehlende_logins) · fuer jede fehlende Zeitung selbst_anmelden
    (das prueft am Ende erneut an echten Abo-Artikeln) · Zwischenspeicher aktualisieren ·
    bleibt etwas fehlend: eine Push je Zeitung und Tag.

    erzwingen     Zwischenspeicher, Sperre und Tageslimit ignorieren.
    neu_anmelden  Test (Knopf in der App): ALLE Zeitungen mit Zugangsdaten abmelden und neu
                  anmelden, bei Misserfolg Rollback auf die bisherigen Cookies.
    push          False: nie eine Push senden.

    Fehlt ein Schluesselbund-Eintrag, ist das kein Fehler: die Zeitung bleibt „fehlend“,
    der Aufrufer geht den alten Weg (Hinweis plus Anmeldefenster).
    Wirft RuntimeError nur, wenn schon die Pruefung nicht starten kann (z. B. Profil belegt).

    Rueckgabe: {"ergebnis": [Login-Test-Eintraege], "fehlende": [Eintraege mit ok False],
                "selbst": {ziel: Ergebnis von selbst_anmelden}, "push": [ziele],
                "frisch": bool, "fehler": str}"""
    say = _sage(progress)
    res = {"ergebnis": [], "fehlende": [], "selbst": {}, "push": [], "frisch": False, "fehler": ""}

    def abschluss(erg):
        res["ergebnis"] = erg
        res["fehlende"] = [e for e in erg if e.get("ok") is False]
        try:
            _fruehwarnung(res, push)
        except Exception as exc:   # die Warnung darf den Abruf nie aufhalten
            _log("Frühwarnung: %s" % str(exc)[:160])
        return res

    if _abgeschaltet():   # Notschalter BRIEFING_SELBST_ANMELDUNG=0: alter Weg
        return abschluss(ff.login_check_cached(max_alter_min))

    if not (erzwingen or neu_anmelden):
        erg = ff.login_check_zwischenstand(max_alter_min)
        if erg is not None and not any(e.get("ok") is False for e in erg):
            return abschluss(erg)

    say("Prüfe Anmeldungen…")
    erg, fehlende = ff.fehlende_logins(headless=headless)
    res["frisch"] = True

    zu_tun = []
    for e in fehlende:
        z = ziel_zu_name(e.get("name"))
        if z and z not in zu_tun:
            zu_tun.append(z)
    if neu_anmelden:
        zu_tun = list(ZEITUNGEN)
    if not zu_tun:
        return abschluss(erg)

    # Zugangsdaten zuerst (ohne Browser): Wer keinen Eintrag hat, braucht keinen.
    mit_zugang = {}
    for z in zu_tun:
        zug = zugangsdaten_lesen(z)
        if zug:
            mit_zugang[z] = zug
        else:
            cfg = ZEITUNGEN[z]
            grund = _kein_eintrag_grund(z)
            _log("%s: %s — %s" % (cfg["kurz"], KEIN_EINTRAG, grund))
            _letztes_merken(z, KEIN_EINTRAG, grund)
            res["selbst"][z] = {"ziel": z, "name": cfg["name"], "status": KEIN_EINTRAG,
                                "grund": grund, "pruefung": None}

    if mit_zugang:
        p = ctx = None
        try:
            p, ctx = ff._launch(headless=headless)
            for z, zug in mit_zugang.items():
                try:
                    res["selbst"][z] = selbst_anmelden(
                        z, ctx, zugang=zug, erzwingen=(erzwingen or neu_anmelden),
                        neu=neu_anmelden, progress=progress)
                except Exception as exc:
                    cfg = ZEITUNGEN[z]
                    grund = _bereinigen("%s: %s" % (exc.__class__.__name__, str(exc)[:160]), *zug)
                    res["selbst"][z] = {"ziel": z, "name": cfg["name"], "status": FEHLGESCHLAGEN,
                                        "grund": grund, "pruefung": None}
        except RuntimeError as exc:   # Profil belegt / Browser fehlt: kein Login-Problem
            res["fehler"] = str(exc)
            _log("Browser nicht startbar: %s" % str(exc)[:160])
        finally:
            for obj, aktion in ((ctx, "close"), (p, "stop")):
                if obj is not None:
                    try:
                        getattr(obj, aktion)()
                    except Exception:
                        pass

    # Ergebnisse in die Liste des Login-Tests einarbeiten und merken
    for z, r in res["selbst"].items():
        eintrag = next((e for e in erg if ziel_zu_name(e.get("name")) == z), None)
        if eintrag is None:
            continue
        if r["status"] == OK:
            pr = r.get("pruefung") or {}
            eintrag["ok"] = True
            eintrag["detail"] = ("selbst angemeldet — " + str(pr.get("detail", ""))).strip(" —")[:300]
            if pr.get("url"):
                eintrag["url"] = pr["url"]
            eintrag.pop("ziel", None)
        else:
            eintrag["detail"] = ("%s — Selbst-Anmeldung: %s" % (eintrag.get("detail", ""), r["grund"]))[:400]
    ff._login_check_speichern(erg)

    # Bleibt etwas fehlend, ohne dass der Browser selbst der Grund war: eine Push je Zeitung/Tag
    if push:
        for z, r in res["selbst"].items():
            eintrag = next((e for e in erg if ziel_zu_name(e.get("name")) == z), None)
            if eintrag is not None and eintrag.get("ok") is False and r["status"] != OK:
                if _push_wenn_neu(z, r["grund"]):
                    res["push"].append(z)
    return abschluss(erg)


def kurzbericht(res: dict) -> list:
    """Anzeigezeilen zu einem Ergebnis von logins_sicherstellen (fuer App und CLI)."""
    zeilen = []
    for z, cfg in ZEITUNGEN.items():
        r = (res.get("selbst") or {}).get(z)
        eintrag = next((e for e in res.get("ergebnis") or [] if ziel_zu_name(e.get("name")) == z), None)
        if r:
            sym = {OK: "✅", KEIN_EINTRAG: "🔑", CAPTCHA: "🤖", FEHLGESCHLAGEN: "⛔"}.get(r["status"], "❓")
            zeilen.append("%s %s: %s" % (sym, cfg["kurz"], r["grund"]))
        elif eintrag is not None:
            sym = {True: "✅", False: "⛔"}.get(eintrag.get("ok"), "❓")
            zeilen.append("%s %s: %s" % (sym, cfg["kurz"], eintrag.get("detail", "")))
    for z, tage in (res.get("fruehwarnung") or {}).items():
        zeilen.append("⏳ %s: Anmeldung läuft in %.1f Tagen ab – bitte von Hand erneuern"
                      % (ZEITUNGEN[z]["kurz"], tage))
    if res.get("fehler"):
        zeilen.append("⚠️ %s" % res["fehler"])
    return zeilen


# ── CLI ────────────────────────────────────────────────────────────────────

def main(argv) -> int:
    sichtbar = "--sichtbar" in argv
    if "--dry" in argv:   # Push nur ausgeben, nicht senden (und nicht als „heute gesendet“ vermerken)
        os.environ["BRIEFING_PUSH_DRY"] = "1"
    ziele = [a for a in argv if a in ZEITUNGEN]
    if "--pruefen" in argv or "--testen" in argv:
        testen = "--testen" in argv
        if ziele and testen:
            # Probe: Zeitung im Profil abmelden, selbst anmelden, nachpruefen; bei Misserfolg Rollback.
            ergebnisse = [selbst_anmelden(z, None, erzwingen=True, neu=True, headless=not sichtbar,
                                          progress=lambda m: print("  " + m)) for z in ziele]
            for r in ergebnisse:
                print("%s %s: %s" % ({OK: "✅"}.get(r["status"], "⛔"), ZEITUNGEN[r["ziel"]]["kurz"], r["grund"]))
            return 0 if all(r["status"] == OK for r in ergebnisse) else 1
        # Eine Push gibt es von der Kommandozeile nur auf ausdruecklichen Wunsch (--push oder --dry).
        res = logins_sicherstellen(progress=lambda m: print("  " + m), headless=not sichtbar,
                                   erzwingen=testen, neu_anmelden=testen,
                                   push=("--push" in argv or "--dry" in argv) and not testen)
        for zeile in kurzbericht(res):
            print(zeile)
        return 1 if res["fehlende"] else 0
    st = status_anzeige()
    for z, cfg in ZEITUNGEN.items():
        print("%s Schlüsselbund-Eintrag %s" % ("✓" if st.get(z) else "✗ fehlt:", cfg["dienst"]))
    return 0 if all(st.get(z) for z in ZEITUNGEN) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except RuntimeError as _exc:
        print("⚠️  %s" % _exc, file=sys.stderr)
        raise SystemExit(2)
