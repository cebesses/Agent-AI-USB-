"""Microphone capture and WAV playback, with no numpy dependency.

Recording uses sounddevice.RawInputStream (int16 PCM) and the stdlib wave
module; playback feeds the same raw API. Agent IA USB's voice stack therefore needs
only one small pip package (sounddevice).
"""
import array
import queue
import threading
import wave
from pathlib import Path

SAMPLE_RATE = 16000  # whisper.cpp works at 16 kHz mono
BLOCK_FRAMES = 1024  # ~64 ms per block

try:
    import sounddevice as _sd
except Exception:  # sounddevice missing (run SETUP-USB.bat) or no audio stack
    _sd = None


def available():
    return _sd is not None


def list_microphones():
    if _sd is None:
        return []
    out = []
    try:
        for i, d in enumerate(_sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                out.append((i, d["name"]))
    except Exception:
        pass
    return out


def _rms(block):
    """Loudness of an int16 block (0 = silence). Cheap loop over 'h' array."""
    a = array.array("h", block)
    if not a:
        return 0
    return (sum(x * x for x in a) // len(a)) ** 0.5


def _record(device, samplerate, should_stop, speech_started, silence_ms, max_ms, on_level):
    """Shared capture loop. Returns raw int16 PCM bytes of the utterance."""
    frames_q = queue.Queue(maxsize=4096)

    def callback(indata, frames, time_info, status):
        frames_q.put(bytes(indata))

    stream = _sd.RawInputStream(samplerate=samplerate, blocksize=BLOCK_FRAMES,
                                dtype="int16", channels=1, device=device, callback=callback)
    silence_limit = int(silence_ms * samplerate / 1000 / BLOCK_FRAMES)
    max_blocks = int(max_ms * samplerate / 1000 / BLOCK_FRAMES)
    pcm, silence_run, blocks, started_at = b"", 0, 0, None
    stream.start()
    try:
        while not should_stop.is_set():
            try:
                block = frames_q.get(timeout=0.1)
            except queue.Empty:
                if started_at and blocks >= max_blocks:
                    break
                continue
            blocks += 1
            level = _rms(block)
            if on_level:
                on_level(level)
            # in continuous mode, ignore leading silence until speech starts
            if speech_started and started_at is None and level < 400:
                continue
            if started_at is None:
                started_at = blocks
            pcm += block
            if level < 400:
                silence_run += 1
                if speech_started and silence_run >= silence_limit:
                    break
            else:
                silence_run = 0
            if blocks - started_at >= max_blocks:
                break
    finally:
        stream.stop()
        stream.close()
    return pcm


def record_push(device=None, samplerate=SAMPLE_RATE, on_level=None):
    """Push-to-talk: records until the user presses Enter again. The Enter
    key is polled with msvcrt so it never mixes with the main input() loop."""
    stop = threading.Event()

    def wait_enter():
        try:
            import msvcrt
            while not stop.is_set():
                if msvcrt.kbhit():
                    if msvcrt.getwch() in ("\r", "\n"):
                        stop.set()
                        break
                stop.wait(0.05)
        except Exception:
            stop.wait(120)  # console API unavailable: cap the recording

    t = threading.Thread(target=wait_enter, daemon=True)
    t.start()
    try:
        return _record(device, samplerate, stop, speech_started=False,
                       silence_ms=10_000, max_ms=120_000, on_level=on_level)
    finally:
        stop.set()
        t.join(timeout=1)


def record_continuous(device=None, samplerate=SAMPLE_RATE, silence_ms=1200,
                      max_ms=20000, on_level=None):
    """Waits for speech, records the utterance, returns when the user goes
    quiet again (energy-based VAD). Returns b"" if nothing was said."""
    return _record(device, samplerate, threading.Event(), speech_started=True,
                   silence_ms=silence_ms, max_ms=max_ms, on_level=on_level)


def pcm_to_wav(pcm, samplerate=SAMPLE_RATE, scratch_dir=None):
    """Wrap raw int16 PCM in a WAV file; returns the path. Prefers Agent IA USB's own
    scratch folder over the system TEMP (some locked-down PCs block it)."""
    if scratch_dir:
        import uuid
        d = Path(scratch_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = str(d / f"agent_ia_usb_voz_{uuid.uuid4().hex[:8]}.wav")
    else:
        import tempfile
        fd, path = tempfile.mkstemp(prefix="agent_ia_usb_voz_", suffix=".wav")
        import os
        os.close(fd)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm)
    return path


def play_wav(path, device=None):
    """Play a 16-bit PCM WAV file (what Piper produces). Blocks until done."""
    if _sd is None:
        raise RuntimeError("sounddevice no está instalado")
    with wave.open(str(path), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if sw != 2:
            raise RuntimeError(f"WAV de {sw * 8} bits no soportado")
        stream = _sd.RawOutputStream(samplerate=sr, channels=ch, dtype="int16", device=device)
        stream.start()
        try:
            while True:
                data = w.readframes(4096)
                if not data:
                    break
                stream.write(data)
        finally:
            stream.stop()
            stream.close()
