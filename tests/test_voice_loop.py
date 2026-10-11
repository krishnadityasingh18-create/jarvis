"""VoiceLoop behaviour without models or audio hardware."""
import asyncio
import time

import numpy as np
import pytest
import sounddevice

from jarvis.voice import State, VoiceLoop


class FakeSD:
    def __init__(self, log):
        self.log = log

    def play(self, samples, sr):
        self.log.append(("play", int(samples[0])))

    def wait(self):
        time.sleep(0.2)
        self.log.append(("played",))

    def stop(self):
        self.log.append(("stop",))


@pytest.fixture
def audio_log(monkeypatch):
    log = []
    fake = FakeSD(log)
    for name in ("play", "wait", "stop"):
        monkeypatch.setattr(sounddevice, name, getattr(fake, name))
    return log


@pytest.fixture
def vl(cfg, audio_log):
    events = []
    v = VoiceLoop(cfg, lambda n, p: events.append((n, p)))
    v._do_chime = lambda: None
    v.events = events
    return v


def test_stale_transcript_dropped_after_text_input(vl):
    vl.event("wake")
    gen = vl._gen
    vl.event("text_input")
    vl.transcribe = lambda audio: "hello"
    vl._transcribe(np.zeros(16000, np.float32), gen)
    assert not [e for e in vl.events if e[0] == "transcript"]
    assert vl.fsm.state is State.THINKING


def test_hotkey_tap_without_speech_goes_idle_silently(vl):
    called = []
    vl._do_transcribe = lambda: called.append(1)
    vl.event("hotkey_down")
    vl.event("hotkey_up")
    assert vl.fsm.state is State.IDLE and called == []


def test_silent_timeout_goes_idle_without_transcribing(vl):
    called = []
    vl._do_transcribe = lambda: called.append(1)
    vl.vad = type("V", (), {"predict": lambda self, x, frame_size=640: 0.0})()
    vl.event("wake")
    vl._t0 -= 16
    vl._frame(np.zeros(1280, np.float32))
    assert vl.fsm.state is State.IDLE and called == []


class FakeKokoro:
    def __init__(self, log, on_create=None):
        self.log, self.on_create = log, on_create

    def create(self, text, **kw):
        self.log.append(("synth", text))
        time.sleep(0.05)
        if self.on_create:
            self.on_create()
        return np.array([len(text)], np.float32), 24000


def test_sentence_cancelled_during_synthesis_is_not_played(vl, audio_log):
    vl.kokoro = FakeKokoro(audio_log, on_create=lambda: vl.event("cancel"))
    vl.fsm.state = State.SPEAKING
    asyncio.run(vl.speak("hello"))
    assert not [e for e in audio_log if e[0] == "play"]


def test_next_sentence_synthesised_while_current_plays(vl, audio_log):
    vl.kokoro = FakeKokoro(audio_log)

    async def go():
        vl.prepare("one")
        vl.prepare("three")
        await vl.speak("one")
        await vl.speak("three")
    asyncio.run(go())
    assert audio_log.index(("synth", "three")) < audio_log.index(("played",))
    assert [e for e in audio_log if e[0] == "play"] == [("play", 3), ("play", 5)]


def test_confirm_silence_after_8s_answers_empty(vl):
    vl.vad = type("V", (), {"predict": lambda self, x, frame_size=640: 0.0})()
    vl.oww = type("O", (), {"predict": lambda self, x: (_ for _ in ()).throw(AssertionError)})()
    vl.fsm.state = State.THINKING
    vl.confirm_id = "q1"
    vl.event("confirm_listen")
    vl._t0 -= 9
    vl._frame(np.zeros(1280, np.float32))       # no wake-word check while confirming
    assert ("confirm_heard", {"text": "", "id": "q1"}) in vl.events and vl.fsm.state is State.THINKING


def test_confirm_answer_transcribed_to_confirm_heard(vl):
    vl.fsm.state = State.THINKING
    vl.confirm_id = "q1"
    vl.event("confirm_listen")
    vl.transcribe = lambda audio: "Yes."
    vl._buf = [np.zeros(16000, np.float32)]
    vl.event("vad_end")
    time.sleep(0.2)
    assert ("confirm_heard", {"text": "Yes.", "id": "q1"}) in vl.events
    assert not [e for e in vl.events if e[0] == "transcript"]
