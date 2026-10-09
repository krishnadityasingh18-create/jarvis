from jarvis.voice import State, VoiceFSM


def fsm(state, mode="wake"):
    f = VoiceFSM(mode)
    f.state = state
    return f


def test_idle_hotkey_starts_listening():
    f = fsm(State.IDLE)
    assert f.handle("hotkey_down") == ["chime", "start_buffer"] and f.state is State.LISTENING


def test_idle_wake_starts_listening():
    f = fsm(State.IDLE)
    assert f.handle("wake") == ["chime", "start_buffer"] and f.state is State.LISTENING


def test_listening_vad_end_transcribes():
    f = fsm(State.LISTENING)
    assert f.handle("vad_end") == ["transcribe"] and f.state is State.THINKING


def test_listening_hotkey_up_and_timeout_transcribe():
    for ev in ("hotkey_up", "timeout"):
        f = fsm(State.LISTENING)
        assert f.handle(ev) == ["transcribe"] and f.state is State.THINKING


def test_transcribed_starts_turn():
    f = fsm(State.THINKING)
    assert f.handle("transcribed") == ["start_turn"] and f.state is State.THINKING


def test_empty_transcript_says_didnt_catch():
    f = fsm(State.THINKING)
    assert f.handle("transcribed_empty") == ["say_didnt_catch"] and f.state is State.SPEAKING


def test_speak_start_and_turn_done():
    f = fsm(State.THINKING)
    assert f.handle("speak_start") == [] and f.state is State.SPEAKING
    assert f.handle("turn_done") == [] and f.state is State.IDLE


def test_wake_barge_in_during_speech():
    f = fsm(State.SPEAKING)
    assert f.handle("wake") == ["cancel_turn", "chime", "start_buffer"] and f.state is State.LISTENING


def test_voice_barge_in_ignored_in_wake_mode():
    f = fsm(State.SPEAKING)
    assert f.handle("speech_300ms") == [] and f.state is State.SPEAKING


def test_voice_barge_in_in_voice_mode():
    f = fsm(State.SPEAKING, "voice")
    assert f.handle("speech_300ms") == ["cancel_turn", "start_buffer"] and f.state is State.LISTENING


def test_cancel_from_thinking():
    f = fsm(State.THINKING)
    assert f.handle("cancel") == ["cancel_turn"] and f.state is State.IDLE


def test_cancel_in_idle_ignored():
    f = fsm(State.IDLE)
    assert f.handle("cancel") == [] and f.state is State.IDLE


def test_wake_while_thinking_ignored():
    f = fsm(State.THINKING)
    assert f.handle("wake") == [] and f.state is State.THINKING


def test_text_input_idle_and_busy():
    f = fsm(State.IDLE)
    assert f.handle("text_input") == [] and f.state is State.THINKING
    f = fsm(State.SPEAKING)
    assert f.handle("text_input") == ["cancel_turn"] and f.state is State.THINKING
