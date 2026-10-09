from enum import Enum


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
