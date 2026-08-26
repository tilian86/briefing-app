"""Playwright-Browser an einen Ort legen, den Putz-Apps in Ruhe lassen.

25.08.2026: CleanupBuddy hat ~/Library/Caches/ms-playwright geleert — mitten
im Betrieb. Danach scheiterten Pocket-Casts-Abruf, Archivieren, Feedly und
Reader-Upload alle mit "Executable doesn't exist at …/ms-playwright/chromium_".
Der Ordner lag im Cache-Verzeichnis, das jede Reinigungs-App als Freiwild
behandelt (Platzmangel war es nicht: 122 GB frei).

Dieses Modul MUSS vor dem ersten Playwright-Import geladen werden — es setzt
nur eine Umgebungsvariable, sonst nichts.
"""

import os

BROWSER_DIR = os.path.expanduser("~/.playwright-browsers")

# setdefault: eine bewusst gesetzte Variable von aussen gewinnt weiterhin.
if os.path.isdir(BROWSER_DIR):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", BROWSER_DIR)


NACHINSTALL_HINWEIS = (
    "Der Browser für die Automatik fehlt (von einer Reinigungs-App entfernt). "
    "Einmal im Terminal nachinstallieren:\n\n"
    "    cd ~/Projects/apps/briefing-app && python3 -m playwright install chromium"
)


def ist_browser_fehler(text: str) -> bool:
    """Erkennt genau den Fehler, der beim fehlenden Browser entsteht."""
    t = (text or "").lower()
    return ("executable doesn't exist" in t
            or "please run the following command to download new browsers" in t)
