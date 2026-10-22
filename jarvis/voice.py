import asyncio
import glob
import math
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
    CONFIRMING = "confirming"   # listening for "yes"/"no" to a confirmation question


I, L, T, S, C = (State.IDLE, State.LISTENING, State.THINKING, State.SPEAKING,
                 State.CONFIRMING)
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
    (T, "wake"): (L, ["cancel_turn"] + LISTEN),
    (T, "hotkey_down"): (L, ["cancel_turn"] + LISTEN),
    (S, "wake"): (L, ["cancel_turn"] + LISTEN),
    (S, "hotkey_down"): (L, ["cancel_turn"] + LISTEN),
    # confirmation: the hub has spoken the question; listen for the answer, no wake word
    (I, "confirm_listen"): (C, LISTEN),
    (T, "confirm_listen"): (C, LISTEN),
    (S, "confirm_listen"): (C, LISTEN),
    (C, "vad_end"): (T, ["transcribe_confirm"]),
    (C, "hotkey_up"): (T, ["transcribe_confirm"]),
    (C, "timeout"): (T, ["transcribe_confirm"]),
    (C, "hotkey_down"): (C, ["start_buffer"]),
    (C, "confirm_silent"): (T, ["confirm_silent"]),
    (C, "confirm_done"): (T, ["stop_audio"]),   # answered by button: drop any half-heard reply
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
        # cancel and text_input come from the hub, which has already stopped its own
        # turn: only stop local audio here, never send cancel_turn back to the hub.
        if event == "cancel" and self.state is not I:
            self.state = I
            return ["stop_audio"]
        if event == "text_input" and self.state is not I:
            self.state = T
            return ["stop_audio"]
        return []


# ---------------------------------------------------------------- hardware part

RATE = 16000
FRAME = 1280                 # 80 ms at 16 kHz, what openWakeWord wants
FRAME_MS = 80
LISTEN_CAP_S = 15
CONFIRM_CAP_S = 8
BARGE_MS = 300
DIDNT_CATCH = "Sorry sir, I didn't catch that."
NO_MIC = "Voice off, text only"


def resample_to_16k(x: np.ndarray, sr: int) -> np.ndarray:
    if sr == RATE:
        return x
    from scipy.signal import resample_poly   # scipy comes with openwakeword
    g = math.gcd(RATE, sr)
    # ponytail: filter state isn't carried across 80 ms mic blocks (small edge
    # transients); carry it (scipy upfirdn/lfilter zi) if wakeword misfires
    return resample_poly(x, RATE // g, sr // g).astype(np.float32)


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
        self.muted = False    # the UI's mic button: frames are dropped, nothing is heard
        self.loaded = False   # models loaded, or loading failed: either way, done trying
        self.cuda = False
        self.whisper = self.kokoro = self.oww = self.vad = None
        self._held = False
        self._reset_listen()
        self._speech_ms = 0
        self._last_text = ""
        # Bumped whenever in-flight audio work (a transcript, a synthesised sentence)
        # becomes stale; work captures it at start and is dropped if it changed.
        self._gen = 0
        self._pending: list[tuple[str, asyncio.Future]] = []
        self.confirm_id: str | None = None   # the question being answered; set by the hub

    # -------- models
    def load(self) -> None:
        _add_cuda_dlls()
        import onnxruntime
        onnxruntime.set_default_logger_severity(3)  # errors only; CUDA graph notices are noise
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
        finally:
            self.loaded = True
        while True:
            try:
                sr = int(sd.query_devices(kind="input")["default_samplerate"])
                blocks: queue.Queue = queue.Queue()
                with sd.InputStream(samplerate=sr, channels=1, dtype="float32",
                                    blocksize=int(sr * FRAME_MS / 1000),
                                    callback=lambda d, *_: blocks.put(d[:, 0].copy())):
                    self.available = True
                    self.on_event("mic", {"muted": self.muted, "available": True})   # tell the UI
                    pending = np.zeros(0, dtype=np.float32)
                    while True:
                        try:  # an unplugged mic stops the callbacks without raising
                            block = blocks.get(timeout=2)
                        except queue.Empty:
                            raise OSError("mic stopped delivering audio") from None
                        pending = np.concatenate([pending, resample_to_16k(block, sr)])
                        while len(pending) >= FRAME:
                            self._frame(pending[:FRAME])
                            pending = pending[FRAME:]
            except Exception as e:
                self.available = False
                self.on_event("mic", {"muted": self.muted, "available": False})
                self.on_event("banner", {"text": f"{NO_MIC} ({e.__class__.__name__})"})
                time.sleep(10)

    def _frame(self, f32: np.ndarray) -> None:
        if self.muted:
            return
        pcm = (np.clip(f32, -1, 1) * 32767).astype(np.int16)
        state = self.fsm.state
        if state in (State.IDLE, State.THINKING, State.SPEAKING):
            if max(self.oww.predict(pcm).values()) > self.cfg.wake_threshold:
                self.oww.reset()
                self.event("wake")
                return
        speech = self.vad.predict(pcm, frame_size=640) > 0.5
        if state in (State.LISTENING, State.CONFIRMING):
            self._buf.append(f32)
            if speech:
                self._heard, self._silence = True, 0
            else:
                self._silence += FRAME_MS
            confirming = state is State.CONFIRMING
            if self._heard and self._silence >= self.cfg.end_silence_ms and not self._held:
                self.event("vad_end")
            elif time.monotonic() - self._t0 > (CONFIRM_CAP_S if confirming else LISTEN_CAP_S):
                # nothing said after a (false) wake: don't feed 15 s of room noise to Whisper
                self.event("timeout" if self._heard else
                           "confirm_silent" if confirming else "cancel")
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
            if name == "hotkey_up" and self.fsm.state is State.LISTENING and not self._heard:
                name = "cancel"  # a quick tap (e.g. a Right-Ctrl shortcut), not a question
            before = self.fsm.state
            actions = self.fsm.handle(name)
            if name == "hotkey_down":
                self._held = "start_buffer" in actions
            elif name in ("hotkey_up", "cancel"):
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
        threading.Thread(target=self._transcribe, args=(audio, self._gen), daemon=True).start()

    def _do_transcribe_confirm(self) -> None:
        audio = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
        gen, cid = self._gen, self.confirm_id

        def run():
            text = self.transcribe(audio)
            with self.lock:
                if gen == self._gen:  # not answered by button or cancelled meanwhile
                    self.on_event("confirm_heard", {"text": text, "id": cid})
        threading.Thread(target=run, daemon=True).start()

    def _do_confirm_silent(self) -> None:
        self.on_event("confirm_heard", {"text": "", "id": self.confirm_id})

    def _transcribe(self, audio: np.ndarray, gen: int) -> None:
        text = self.transcribe(audio)
        with self.lock:
            if gen != self._gen:  # typed input, cancel or a new wake arrived meanwhile
                return
            if text:
                self._last_text = text
                self.event("transcribed")
            else:
                self.event("transcribed_empty")

    def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < RATE // 4:
            return ""
        segments, _ = self.whisper.transcribe(audio, language="en", beam_size=1,
                                              vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    def _do_start_turn(self) -> None:
        self.on_event("transcript", {"text": self._last_text})

    def _do_say_didnt_catch(self) -> None:
        def run():
            self._speak_blocking(DIDNT_CATCH)
            self.event("turn_done")
        threading.Thread(target=run, daemon=True).start()

    def _do_stop_audio(self) -> None:
        import sounddevice as sd
        with self.lock:
            self._gen += 1
            self._pending.clear()
            sd.stop()

    def _do_cancel_turn(self) -> None:
        self._do_stop_audio()
        self.on_event("cancel_turn", {})

    # -------- speech out
    def _synth(self, text: str) -> tuple[np.ndarray, int]:
        return self.kokoro.create(text, voice=self.cfg.voice, speed=self.cfg.voice_speed,
                                  lang="en-gb")

    def _play(self, samples: np.ndarray, sr: int, gen: int) -> None:
        import sounddevice as sd
        with self.lock:
            if gen != self._gen:  # cancelled while this sentence was being synthesised
                return
            sd.play(samples, sr)
        sd.wait()

    def _voice_failed(self, e: Exception) -> None:
        self.on_event("banner", {"text": f"Voice output failed, showing text "
                                         f"({e.__class__.__name__})"})

    def _speak_blocking(self, text: str) -> None:
        gen = self._gen
        try:
            self._play(*self._synth(text), gen)
        except Exception as e:
            self._voice_failed(e)

    def prepare(self, text: str) -> None:
        """Start synthesising a sentence now, so it is ready when its turn to play comes."""
        if self.kokoro is not None:
            fut = asyncio.get_running_loop().run_in_executor(None, self._synth, text)
            self._pending.append((text, fut))

    async def speak(self, text: str) -> None:
        if self.kokoro is None:
            return
        gen = self._gen
        fut = next((f for t, f in self._pending if t == text), None)
        if fut is not None:
            self._pending = [(t, f) for t, f in self._pending if f is not fut]
        else:
            fut = asyncio.get_running_loop().run_in_executor(None, self._synth, text)
        try:
            try:
                samples, sr = await fut
            except Exception as e:
                self._voice_failed(e)
                return
            await asyncio.to_thread(self._play, samples, sr, gen)
        except asyncio.CancelledError:
            self._do_stop_audio()
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
