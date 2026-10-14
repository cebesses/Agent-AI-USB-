"""Speech-to-text with whisper.cpp (local subprocess, no pip deps).

Layout on the USB:
  02_RUNTIME/whisper/whisper-cli.exe   (from whisper-bin-x64.zip)
  02_RUNTIME/whisper/models/ggml-<model>.bin
"""
import re
import subprocess
from pathlib import Path

LANG_NAMES = {"es": "español", "en": "inglés"}


class WhisperSTT:
    def __init__(self, agent_ia_usb_root, model="base", language="es"):
        self.dir = Path(agent_ia_usb_root) / "02_RUNTIME" / "whisper"
        self.model = model
        self.language = language

    @property
    def exe(self):
        """v1.7+ ships whisper-cli.exe; older builds called it main.exe."""
        for name in ("whisper-cli.exe", "main.exe"):
            p = self.dir / name
            if p.exists():
                return p
        return None

    def model_path(self):
        p = self.dir / "models" / f"ggml-{self.model}.bin"
        return p if p.exists() else None

    def available(self):
        return self.exe is not None and self.model_path() is not None

    def missing(self):
        out = []
        if self.exe is None:
            out.append("02_RUNTIME\\whisper\\whisper-cli.exe")
        if self.model_path() is None:
            out.append(f"02_RUNTIME\\whisper\\models\\ggml-{self.model}.bin")
        return out

    def transcribe(self, wav_path):
        """WAV file -> text in the configured language ('' on failure)."""
        exe, model = self.exe, self.model_path()
        if not (exe and model):
            raise RuntimeError("whisper.cpp no está instalado")
        r = subprocess.run(
            [str(exe), "-m", str(model), "-l", self.language, "-nt", "-f", str(wav_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"whisper falló: {(r.stderr or '')[-200:]}")
        # stdout holds the plain transcription (-nt drops timestamps); drop
        # residual blanks and bracketed [audio] markers
        text = " ".join(r.stdout.split())
        text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)
        return " ".join(text.split()).strip()
