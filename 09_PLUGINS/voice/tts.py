"""Text-to-speech with Piper (local subprocess, no pip deps).

Layout on the USB:
  02_RUNTIME/piper/piper.exe + espeak-ng-data/   (from piper_windows_amd64.zip)
  02_RUNTIME/piper/voices/<name>.onnx + <name>.json

The reply is spoken sentence by sentence while the model is still writing:
speak_stream() consumes text chunks, splits complete sentences into a queue
and a worker thread synthesizes/plays them in order.
"""
import re
import subprocess
import threading
import queue
import os
from pathlib import Path

_SENTENCE_END = re.compile(r"([.!?…]+[\"')\]]*\s+)")


class PiperTTS:
    def __init__(self, agent_ia_usb_root, voice=None, scratch_dir=None):
        self.dir = Path(agent_ia_usb_root) / "02_RUNTIME" / "piper"
        self.voice_name = voice  # None = first installed voice
        # WAV output next to Agent IA USB's workspace (TEMP can be blocked on locked-down PCs)
        self.scratch = Path(scratch_dir) if scratch_dir else self.dir / "temp"

    @property
    def exe(self):
        p = self.dir / "piper.exe"
        return p if p.exists() else None

    def voices(self):
        v = self.dir / "voices"
        if not v.is_dir():
            return []
        return sorted(p.stem for p in v.glob("*.onnx"))

    def voice_path(self):
        names = self.voices()
        if not names:
            return None
        want = self.voice_name if self.voice_name in names else names[0]
        return self.dir / "voices" / f"{want}.onnx"

    def available(self):
        return self.exe is not None and self.voice_path() is not None

    def missing(self):
        out = []
        if self.exe is None:
            out.append("02_RUNTIME\\piper\\piper.exe")
        if not self.voices():
            out.append("una voz en 02_RUNTIME\\piper\\voices\\")
        return out

    def synth(self, text):
        """text -> temp WAV path (blocking, one Piper call)."""
        exe, voice = self.exe, self.voice_path()
        if not (exe and voice):
            raise RuntimeError("Piper no está instalado")
        import uuid
        self.scratch.mkdir(parents=True, exist_ok=True)
        wav = self.scratch / f"agent_ia_usb_tts_{uuid.uuid4().hex[:8]}.wav"
        r = subprocess.run([str(exe), "-m", str(voice.resolve()), "-f", str(wav.resolve())],
                           input=text.encode("utf-8"), capture_output=True, timeout=120)
        if r.returncode != 0 or not wav.exists():
            raise RuntimeError(f"Piper falló: {(r.stderr or b'')[-200:].decode('utf-8', 'replace')}")
        return str(wav)

    def say(self, text, device=None):
        """Synthesize and play one block of text (blocking)."""
        from . import mic
        wav = self.synth(text)
        try:
            mic.play_wav(wav, device=device)
        finally:
            try:
                os.unlink(wav)
            except OSError:
                pass

    def speak_stream(self, chunks=None, device=None, done=None):
        """Speak a streamed reply. Pass an iterator of text chunks to have the
        handle feed itself, or chunks=None to feed it manually with .feed()
        (e.g. from token events) and close it with .finish().

        Returns a StreamHandle; call .stop() to cut the speech off.
        """
        h = StreamHandle(self, device, done)
        if chunks is not None:
            h.start(chunks)
        return h

    def speak_reply(self, reply_text, device=None):
        """Convenience for non-streaming replies."""
        self.say(reply_text, device=device)


class StreamHandle:
    """Feeds sentences to the TTS worker and keeps the reply buffer."""

    def __init__(self, tts, device, done):
        self.tts = tts
        self.device = device
        self.done_cb = done
        self.full_text = ""
        self._q = queue.Queue()
        self._sentinel = object()
        self._thread = None
        self._stop = threading.Event()
        self._pending = ""
        self._thread = threading.Thread(target=self._play_loop, daemon=True)
        self._thread.start()

    def start(self, chunks):
        threading.Thread(target=self._feed, args=(chunks,), daemon=True).start()

    def feed(self, text):
        """Push more text (a token) into the speech buffer; speaks full sentences."""
        if not text:
            return
        self.full_text += text
        self._pending += text
        while True:
            m = _SENTENCE_END.search(self._pending)
            if not m:
                # avoid keeping an over-long fragment unsaid (no punctuation yet)
                if len(self._pending) > 280 and self._pending[-1] in ",;: ":
                    cut = self._pending.rfind(" ")
                    self._enqueue(self._pending[:cut].strip())
                    self._pending = self._pending[cut:]
                break
            sentence = (self._pending[:m.end()]).strip()
            self._pending = self._pending[m.end():]
            if sentence:
                self._enqueue(sentence)

    def finish(self):
        """Call when the reply is complete; speaks the remaining fragment."""
        rest = self._pending.strip()
        self._pending = ""
        if rest:
            self._enqueue(rest)
        self._q.put(self._sentinel)

    def stop(self):
        """Drop everything queued and silence the worker."""
        self._stop.set()
        with self._q.mutex:
            self._q.queue.clear()
        self._q.put(self._sentinel)

    def wait(self, timeout=600):
        if self._thread:
            self._thread.join(timeout)

    def _enqueue(self, sentence):
        self._q.put(sentence)

    def _feed(self, chunks):
        try:
            for c in chunks:
                if self._stop.is_set():
                    break
                self.feed(c)
        finally:
            self.finish()

    def _play_loop(self):
        while True:
            item = self._q.get()
            if item is self._sentinel:
                break
            if self._stop.is_set():
                continue
            try:
                self.tts.say(item, device=self.device)
            except Exception:
                continue  # one failed sentence must not kill the reply
        if self.done_cb:
            try:
                self.done_cb()
            except Exception:
                pass
