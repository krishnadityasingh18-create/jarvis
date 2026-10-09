import tomllib
from dataclasses import dataclass
from pathlib import Path

import keyring

SERVICE = "jarvis"


@dataclass
class Route:
    name: str
    base_url: str
    model: str
    key: str
    price_in: float = 0
    price_out: float = 0


@dataclass
class Config:
    routes: dict[str, Route]
    default_route: str
    fallback: list[str]
    monthly_inr: float
    persona: str
    barge_in: str
    wake_threshold: float
    end_silence_ms: int
    ptt_key: str
    voice: str
    voice_speed: float
    whisper_model: str
    db_path: Path


def load_config(path: Path = Path("config.toml")) -> Config:
    t = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    v = t["voice"]
    return Config(
        routes={n: Route(name=n, **r) for n, r in t["routes"].items()},
        default_route=t["routing"]["default"],
        fallback=t["routing"]["fallback"],
        monthly_inr=t["budget"]["monthly_inr"],
        persona=t["persona"]["prompt"].strip(),
        barge_in=v["barge_in"],
        wake_threshold=v["wake_threshold"],
        end_silence_ms=v["end_silence_ms"],
        ptt_key=v["ptt_key"],
        voice=v["voice"],
        voice_speed=v["speed"],
        whisper_model=v["whisper_model"],
        db_path=Path(t["data"]["db_path"]),
    )


def get_key(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def set_key(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)
