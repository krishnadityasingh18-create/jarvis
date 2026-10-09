import asyncio
import glob
import os
import queue
import sys
import threading
import time
from collections.abc import Callable
from enum import Enum

import numpy as np


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


I, L, T, S = State.IDLE, State.LISTENING, State.THINKING, State.SPEAKING
LISTEN = ["chime", "start_buffer"]

# (state, event) -> (next state, actions). Anything missing is ignored.
_TABLE = {
    (I, "wake"): (L, LISTEN),
    (I, "hotkey_down"): (L, LISTEN),
    (I, "text_input"): (T, []),
    (L, "vad_end"): (T, ["transcribe"]),
    (L, "hotkey_up"): (T, ["transcribe"]),
    (L, "timeout"): (T, ["transcribe"]),
    (T, "transcribed"): (T, ["start_turn"]),
    (T, "transcribed_empty"): (S, ["say_didnt_catch"]),
    (T, "speak_start"): (S, []),
    (T, "turn_done"): (I, []),
    (S, "turn_done"): (I, []),
    (S, "wake"): (L, ["cancel_turn"] + LISTEN),
    (S, "hotkey_down"): (L, ["cancel_turn"] + LISTEN),
}


class VoiceFSM:
    def __init__(self, barge_in: str):
        self.barge_in = barge_in
        self.state = State.IDLE

    def handle(self, event: str) -> list[str]:
        if (self.state, event) in _TABLE:
            self.state, actions = _TABLE[(self.state, event)]
            return list(actions)
        if event == "speech_300ms" and self.state is S and self.barge_in == "voice":
            self.state = L
            return ["cancel_turn", "start_buffer"]
        if event == "cancel" and self.state is not I:
            self.state = I
            return ["cancel_turn"]
        if event == "text_input" and self.state is not I:
            self.state = T
            return ["cancel_turn"]
        return []


# ---------------------------------------------------------------- hardware part

RATE = 16000
FRAME = 1280                 # 80 ms at 16 kHz, what openWakeWord wants
FRAME_MS = 80
LISTEN_CAP_S = 15
BARGE_MS = 300
DIDNT_CATCH = "Sorry sir, I didn't catch that."
NO_MIC = "Voice off, text only"


def resample_to_16k(x: np.ndarray, sr: int) -> np.ndarray:
    if sr == RATE:
        return x
    n = round(len(x) * RATE / sr)
    # ponytail: linear interpolation, fine for speech; use soxr if Whisper accuracy suffers
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _add_cuda_dlls() -> None:
    """ctranslate2 needs cuBLAS/cuDNN from the nvidia-* wheels on Windows."""
    for d in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


def _chime() -> np.ndarray:
    t = np.linspace(0, 0.12, int(24000 * 0.12), endpoint=False)
    return (0.2 * np.sin(2 * np.pi * 880 * t) * np.hanning(len(t))).astype(np.float32)


class VoiceLoop:
    def __init__(self, cfg, on_event: Callable[[str, dict], None]):
        self.cfg = cfg
        self.on_event = on_event
        self.fsm = VoiceFSM(cfg.barge_in)
        self.lock = threading.RLock()
        self.available = False
        self.cuda = False
        self.whisper = self.kokoro = self.oww = self.vad = None
        self._held = False
        self._reset_listen()
        self._speech_ms = 0
        self._last_text = ""

    # -------- models
    def load(self) -> None:
        _add_cuda_dlls()
        from faster_whisper import WhisperModel
        from kokoro_onnx import Kokoro
        from openwakeword.model import Model
        from openwakeword.utils import download_models
        from openwakeword.vad import VAD

        download_models(["hey_jarvis"])
        self.oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.vad = VAD()
        try:
            self.whisper = WhisperModel(self.cfg.whisper_model, device="cuda",
                                        compute_type="int8_float16")
            self.cuda = True
        except Exception as e:
            self.on_event("banner", {"text": f"No CUDA ({e.__class__.__name__}); "
                                             "speech recognition on CPU, slower"})
            self.whisper = WhisperModel(self.cfg.whisper_model, device="cpu", compute_type="int8")
        # TensorRT is listed by onnxruntime-gpu but not installed; pin CUDA (falls back to CPU).
        os.environ.setdefault("ONNX_PROVIDER", "CUDAExecutionProvider" if self.cuda
                              else "CPUExecutionProvider")
        self.kokoro = Kokoro("models/kokoro-v1.0.onnx", "models/voices-v1.0.bin")
        # First CUDA call is ~2 s slower; pay it at startup, not on the first reply.
        self.kokoro.create("Ready.", voice=self.cfg.voice, speed=self.cfg.voice_speed, lang="en-gb")
        list(self.whisper.transcribe(np.zeros(RATE, np.float32), language="en")[0])

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    # -------- mic thread
    def _run(self) -> None:
        import sounddevice as sd
        try:
            self.load()
        except Exception as e:
            self.on_event("banner", {"text": f"{NO_MIC} (models failed: {e})"})
            return
        while True:
            try:
                sr = int(sd.query_devices(kind="input")["default_samplerate"])
                blocks: queue.Queue = queue.Queue()
                with sd.InputStream(samplerate=sr, channels=1, dtype="float32",
                                    blocksize=int(sr * FRAME_MS / 1000),
                                    callback=lambda d, *_: blocks.put(d[:, 0].copy())):
                    self.available = True
                    pending = np.zeros(0, dtype=np.float32)
                    while True:
                        pending = np.concatenate([pending, resample_to_16k(blocks.get(), sr)])
                        while len(pending) >= FRAME:
                            self._frame(pending[:FRAME])
                            pending = pending[FRAME:]
            except Exception as e:
                self.available = False
                self.on_event("banner", {"text": f"{NO_MIC} ({e.__class__.__name__})"})
                time.sleep(10)

    def _frame(self, f32: np.ndarray) -> None:
        pcm = (np.clip(f32, -1, 1) * 32767).astype(np.int16)
        state = self.fsm.state
        if state in (State.IDLE, State.SPEAKING):
            if max(self.oww.predict(pcm).values()) > self.cfg.wake_threshold:
                self.oww.reset()
                self.event("wake")
                return
        speech = self.vad.predict(pcm, frame_size=640) > 0.5
        if state is State.LISTENING:
            self._buf.append(f32)
            if speech:
                self._heard, self._silence = True, 0
            else:
                self._silence += FRAME_MS
            if self._heard and self._silence >= self.cfg.end_silence_ms and not self._held:
                self.event("vad_end")
            elif time.monotonic() - self._t0 > LISTEN_CAP_S:
                self.event("timeout")
        elif state is State.SPEAKING:
            self._speech_ms = self._speech_ms + FRAME_MS if speech else 0
            if self._speech_ms >= BARGE_MS:
                self._speech_ms = 0
                self.event("speech_300ms")

    def _reset_listen(self) -> None:
        self._buf, self._heard, self._silence = [], False, 0
        self._t0 = time.monotonic()

    # -------- events and actions
    def event(self, name: str) -> None:
        with self.lock:
            before = self.fsm.state
            actions = self.fsm.handle(name)
            if name == "hotkey_down":
                self._held = "start_buffer" in actions
            elif name == "hotkey_up":
                self._held = False
            if self.fsm.state is not before:
                self.on_event("state", {"state": self.fsm.state.value})
            for a in actions:
                getattr(self, "_do_" + a)()

    def _do_chime(self) -> None:
        import sounddevice as sd
        sd.play(_chime(), 24000)

    def _do_start_buffer(self) -> None:
        self._reset_listen()

    def _do_transcribe(self) -> None:
        audio = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio: np.ndarray) -> None:
        text = self.transcribe(audio)
        if text:
            self._last_text = text
            self.event("transcribed")
        else:
            self.event("transcribed_empty")

    def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < RATE // 4:
            return ""
        segments, _ = self.whisper.transcribe(audio, language="en", beam_size=1)
        return " ".join(s.text.strip() for s in segments).strip()

    def _do_start_turn(self) -> None:
        self.on_event("transcript", {"text": self._last_text})

    def _do_say_didnt_catch(self) -> None:
        def run():
            self._speak_blocking(DIDNT_CATCH)
            self.event("turn_done")
        threading.Thread(target=run, daemon=True).start()

    def _do_cancel_turn(self) -> None:
        import sounddevice as sd
        sd.stop()
        self.on_event("cancel_turn", {})

    # -------- speech out
    def _speak_blocking(self, text: str) -> None:
        import sounddevice as sd
        try:
            samples, sr = self.kokoro.create(text, voice=self.cfg.voice,
                                             speed=self.cfg.voice_speed, lang="en-gb")
            sd.play(samples, sr)
            sd.wait()
        except Exception as e:
            self.on_event("banner", {"text": "Voice output failed, showing text "
                                             f"({e.__class__.__name__})"})

    async def speak(self, text: str) -> None:
        if self.kokoro is None:
            return
        try:
            await asyncio.to_thread(self._speak_blocking, text)
        except asyncio.CancelledError:
            import sounddevice as sd
            sd.stop()
            raise


def _selftest() -> None:
    import sounddevice as sd

    from jarvis.config import load_config
    v = VoiceLoop(load_config(), lambda n, p: print("event:", n, p))
    t = time.perf_counter()
    v.load()
    print(f"models loaded in {time.perf_counter() - t:.1f}s, CUDA: {'yes' if v.cuda else 'no'}")
    t = time.perf_counter()
    samples, sr = v.kokoro.create("Good evening, sir. Systems are online.", voice=v.cfg.voice,
                                  speed=v.cfg.voice_speed, lang="en-gb")
    print(f"kokoro synth: {time.perf_counter() - t:.2f}s for {len(samples) / sr:.1f}s of audio")
    sd.play(samples, sr)
    sd.wait()
    print("Speak now (4 s)...", flush=True)
    v._do_chime()
    sd.wait()
    mic_sr = int(sd.query_devices(kind="input")["default_samplerate"])
    rec = sd.rec(int(4 * mic_sr), samplerate=mic_sr, channels=1, dtype="float32")
    sd.wait()
    t = time.perf_counter()
    text = v.transcribe(resample_to_16k(rec[:, 0], mic_sr))
    print(f"whisper: {time.perf_counter() - t:.2f}s -> {text!r}")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
