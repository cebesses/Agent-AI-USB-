"""Agent IA USB voice plugin — talk to Agent IA USB and hear the reply, 100% offline.

STT: whisper.cpp · TTS: Piper · Mic/speaker: sounddevice.
Enable it in the chat with /voz on|continuo.
"""
import re
import sys
import time
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR.parent.parent / "03_AGENT") not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR.parent.parent / "03_AGENT"))

from . import mic, stt, tts  # noqa: E402

# voice commands Agent IA USB understands even when they are dictated
_VOICE_STOP_WORDS = {"voz off", "voz apagar", "apaga la voz", "desactiva la voz"}


class VoiceManager:
    """One instance per session; owns the STT/TTS engines and the config."""

    def __init__(self, agent_ia_usb_root, voice_cfg=None):
        cfg = voice_cfg or {}
        self.enabled = False
        self.mode = cfg.get("mode", "push")          # push | continuo
        self.scratch = Path(agent_ia_usb_root) / "04_WORKSPACE" / "temp" / "voz"
        self.stt = stt.WhisperSTT(agent_ia_usb_root, model=cfg.get("stt_model", "base"),
                                  language=cfg.get("stt_language", "es"))
        self.tts = tts.PiperTTS(agent_ia_usb_root, voice=cfg.get("tts_voice"), scratch_dir=self.scratch)
        self.mic_device = cfg.get("mic_device")      # sounddevice index or None
        self.last_error = ""

    # ------------------------------------------------------------ readiness
    def ok(self):
        return mic.available() and self.stt.available() and self.tts.available()

    def problems(self):
        """Human-readable list of what is missing (empty = ready)."""
        out = []
        if not mic.available():
            out.append("el paquete 'sounddevice' (ejecuta SETUP-USB.bat para instalarlo)")
        out += [f"falta {m}" for m in self.stt.missing()]
        out += [f"falta {m}" for m in self.tts.missing()]
        if not out and not mic.list_microphones():
            out.append("no hay micrófonos conectados")
        return out

    # ---------------------------------------------------------------- listen
    def listen(self, continuous=False):
        """Record from the mic and transcribe. Returns text or ''."""
        try:
            if continuous:
                pcm = mic.record_continuous(device=self.mic_device)
            else:
                pcm = mic.record_push(device=self.mic_device)
        except Exception as e:
            self.last_error = f"error de micrófono: {e}"
            return ""
        if not pcm or len(pcm) < 8000:  # <0.25s of audio
            return ""
        wav = mic.pcm_to_wav(pcm, scratch_dir=self.scratch)
        try:
            return self.stt.transcribe(wav)
        except Exception as e:
            self.last_error = f"error de transcripción: {e}"
            return ""
        finally:
            try:
                Path(wav).unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ talk
    def say(self, text):
        self.tts.say(text, device=None)

    def speak_reply(self, chunks):
        """Speak a streamed reply. `chunks` is an iterator of text pieces.

        Returns (StreamHandle, full_text_getter); call handle.finish() or let
        the iterator end, then handle.wait() before asking the next question.
        """
        handle = self.tts.speak_stream(chunks)
        return handle

    # ---------------------------------------------------------------- config
    def status_line(self):
        if not self.enabled:
            return ""
        return f" · voz {self.mode}"

    def apply_command(self, arg):
        """Handle the /voz command. Returns a message for the user."""
        arg = (arg or "").strip().lower()
        if arg in ("on", "off"):
            return self._toggle(arg == "on")
        if arg in ("continuo", "continuous", "continua", "continúa"):
            msg = self._toggle(True)
            self.mode = "continuo"
            return msg + " Modo continuo: Agent IA USB escucha tras cada respuesta (Ctrl+C vuelve al teclado)."
        if arg in ("push", "manual", "tecla"):
            self.mode = "push"
            return self._toggle(True) + " Modo pulsar: Enter para empezar y Enter para terminar."
        if not arg:
            return self._toggle(not self.enabled)
        return f"No entendí '/voz {arg}'. Usa: /voz on|off|continuo|push"

    def _toggle(self, on):
        if on:
            probs = self.problems()
            if probs:
                self.enabled = False
                return ("No puedo activar la voz: " + "; ".join(probs) +
                        ". Ejecuta SETUP-USB.bat y elige instalar el módulo de voz.")
            self.enabled = True
            voices = ", ".join(self.tts.voices())
            mic_n = next((n for _, n in mic.list_microphones()), "predeterminado")
            return (f"Voz activada (modo {self.mode}). Micro: {mic_n}. "
                    f"Voz TTS: {voices or '?'}.")

        self.enabled = False
        self.stop_speaking()
        return "Voz desactivada; seguimos por teclado."

    def stop_speaking(self):
        stream = getattr(self, "_current_stream", None)
        if stream:
            try:
                stream.stop()
            except Exception:
                pass

    def pick_tts_voice(self, arg):
        voices = self.tts.voices()
        if not voices:
            return "No hay voces instaladas en 02_RUNTIME\\piper\\voices."
        if not arg:
            lines = "\n".join(f"  {i}. {v}" for i, v in enumerate(voices, 1))
            active = self.tts.voice_name if self.tts.voice_name in voices else voices[0]
            return f"Voces TTS:\n{lines}\nActiva: {active}. Cambia con: /voces <nº>"
        if arg.isdigit() and 1 <= int(arg) <= len(voices):
            self.tts.voice_name = voices[int(arg) - 1]
        elif arg in voices:
            self.tts.voice_name = arg
        else:
            return f"No encontré la voz '{arg}'."
        sample = "Hola, soy Agent IA USB. Así suena mi voz."
        try:
            self.tts.say(sample)
        except Exception as e:
            return f"Voz cambiada a {self.tts.voice_name} (no pude reproducir la muestra: {e})"
        return f"Voz cambiada a {self.tts.voice_name}."

    def pick_mic(self, arg):
        mics = mic.list_microphones()
        if not mics:
            return "No encontré micrófonos."
        if not arg:
            lines = "\n".join(f"  {i}. {n}" for i, n in mics)
            return f"Micrófonos:\n{lines}\nUsa: /micro <nº>"
        if arg.isdigit() and any(int(arg) == i for i, _ in mics):
            self.mic_device = int(arg)
            return f"Micrófono: {next(n for i, n in mics if i == int(arg))}."
        return f"'{arg}' no es un nº de la lista."
