import tomllib
from dataclasses import dataclass, field
from datetime import time
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
    extra_body: dict = field(default_factory=dict)   # provider-specific request options


@dataclass
class Agent:
    name: str
    route: str
    fallback: bool
    tools: list[str]
    prompt: str


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
    agents: dict[str, Agent]
    file_roots: list[Path]
    file_exclude: list[str]
    content_exclude: list[str] = field(default_factory=list)
    content_private: bool = True
    briefing_enabled: bool = True
    briefing_time: time = time(7, 30)
    latitude: float = 12.82     # SRM Kattankulathur, for the weather
    longitude: float = 80.04
    remote: dict = field(default_factory=dict)   # [remote]: numbers, Vapi ids, n8n webhook
    devices: list = field(default_factory=list)  # [[devices]]: hardware, as data
    app_dir: Path = Path(__file__).resolve().parents[1]   # the JARVIS folder: never written to


def _devices(raw: list) -> list[dict]:
    """Check [[devices]] entries once, at load, and name the broken one."""
    out = []
    for i, d in enumerate(raw):
        who = d.get("name") or f"device #{i + 1}"
        cmds = d.get("commands")
        if not d.get("name") or not isinstance(cmds, dict) or not cmds:
            raise ValueError(f"[[devices]] {who}: needs a name and [devices.commands]")
        if d.get("transport", "serial") == "mqtt" and not d.get("topic"):
            raise ValueError(f"[[devices]] {who}: an MQTT device needs a topic")
        fixed = {}
        for n, c in cmds.items():
            if not isinstance(c, dict) or not c.get("send"):
                raise ValueError(f"[[devices]] {who}: command '{n}' needs send = \"...\"")
            if "{value}" in c["send"] and ("min" not in c or "max" not in c):
                raise ValueError(f"[[devices]] {who}: command '{n}' takes a value, so it "
                                 "needs min and max")
            fixed[n.lower()] = {**c, **({"undo": c["undo"].lower()} if c.get("undo") else {})}
        out.append({**d, "commands": fixed})
    return out


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
        db_path=Path(t["data"]["db_path"]).resolve(),   # stored file paths must not depend on the cwd
        agents={n: Agent(name=n, **{**a, "prompt": a["prompt"].strip()})
                for n, a in t["agents"].items()},
        file_roots=[Path(r).expanduser() for r in t["files"]["roots"]],
        file_exclude=t["files"]["exclude"],
        content_exclude=t["files"].get("content_exclude", []),
        content_private=t["files"].get("content_private", True),
        briefing_enabled=t.get("briefing", {}).get("enabled", True),
        briefing_time=time.fromisoformat(t.get("briefing", {}).get("time", "07:30")),
        latitude=t.get("briefing", {}).get("latitude", 12.82),
        longitude=t.get("briefing", {}).get("longitude", 80.04),
        remote=t.get("remote", {}),
        devices=_devices(t.get("devices", [])),
    )


def get_key(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def set_key(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)
