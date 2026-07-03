"""Lokale Podcast-Transkription auf dem M1 Pro (mlx-whisper, large-v3-turbo).

Das Endgame der Podcast-Automatik: MP3 direkt aus dem RSS-Feed laden und LOKAL
transkribieren — keine Apple-Podcasts-Tipperei, keine macOS-Berechtigungen,
komplett kostenlos. Tempo auf M1 Pro: grob 8-12x Echtzeit (50-min-Folge ≈ 5 min).

Voraussetzungen: pip3 install --user mlx-whisper; ffmpeg (liegt in ~/bin);
Modell wird beim ersten Lauf aus dem HuggingFace-Cache geladen (~1.6 GB, vorgeladen).
"""

import os
import ssl
import tempfile
import time
import urllib.request

WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"


def _ensure_ffmpeg_path() -> None:
    # ffmpeg liegt in ~/bin — der launchd-Dienst hat das nicht im PATH
    home_bin = os.path.expanduser("~/bin")
    if home_bin not in (os.environ.get("PATH") or ""):
        os.environ["PATH"] = home_bin + ":" + (os.environ.get("PATH") or "")


def transcribe_audio_url(audio_url: str, progress_callback=None,
                         max_mb: int = 400) -> dict:
    """Lädt die Episode (MP3/M4A) und transkribiert sie lokal.

    Returns: {"ok", "text", "error", "elapsed_seconds", "audio_mb"}
    """
    t0 = time.time()

    def _report(msg):
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    _ensure_ffmpeg_path()
    try:
        import mlx_whisper  # lazy — Fehlermeldung soll in der UI landen, nicht beim App-Start
    except ImportError:
        return {"ok": False, "text": "", "elapsed_seconds": 0.0, "audio_mb": 0,
                "error": "mlx-whisper nicht installiert (pip3 install --user mlx-whisper)."}

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    suffix = ".m4a" if ".m4a" in (audio_url or "").lower() else ".mp3"
    tmp = tempfile.mktemp(suffix=suffix, prefix="briefing_ep_")
    try:
        _report("Lade Audio herunter…")
        req = urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0 (BriefingApp)"})
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r, open(tmp, "wb") as f:
            total = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_mb * (1 << 20):
                    return {"ok": False, "text": "", "elapsed_seconds": time.time() - t0,
                            "audio_mb": total >> 20, "error": f"Audio größer als {max_mb} MB — übersprungen."}
                f.write(chunk)
                if total % (10 << 20) < (1 << 20):
                    _report(f"Lade Audio… {total >> 20} MB")
        audio_mb = total >> 20
        _report(f"🎙️ Transkribiere lokal ({audio_mb} MB — grob {max(1, audio_mb // 10)}-{max(2, audio_mb // 5)} Min auf dem M1)…")
        result = mlx_whisper.transcribe(tmp, path_or_hf_repo=WHISPER_MODEL, verbose=None)
        text = (result.get("text") or "").strip()
        if len(text) < 300:
            return {"ok": False, "text": text, "elapsed_seconds": time.time() - t0,
                    "audio_mb": audio_mb, "error": "Transkript verdächtig kurz — Audio defekt?"}
        return {"ok": True, "text": text, "error": None,
                "elapsed_seconds": time.time() - t0, "audio_mb": audio_mb}
    except Exception as exc:
        return {"ok": False, "text": "", "elapsed_seconds": time.time() - t0,
                "audio_mb": 0, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    r = transcribe_audio_url(sys.argv[1], progress_callback=print)
    print("ok:", r["ok"], "| Fehler:", r.get("error"), "| Dauer:", f"{r['elapsed_seconds']:.0f}s")
    print((r.get("text") or "")[:400])
