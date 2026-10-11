"""Hardware over USB serial or MQTT. Devices and their commands are config ([[devices]]);
JARVIS only ever sends a command listed there."""
import difflib
import re

from jarvis.config import get_key
from jarvis.tools import tool

SAFE = {"stop", "off"}            # stopping or switching off never waits for a yes
USB_SERIAL = re.compile(r"CP210|CH34|USB-SERIAL|USB Serial|ESP32|Silicon Labs|FTDI|Arduino",
                        re.IGNORECASE)


def _ports():
    from serial.tools import list_ports
    return list_ports.comports()


def _open_serial(port: str, baud: int):
    import serial
    return serial.Serial(port, baud, timeout=2, write_timeout=2)


def _mqtt_publish(dev: dict, payload: str) -> None:
    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if dev.get("username"):
        c.username_pw_set(dev["username"], get_key(f"mqtt_{dev['name']}"))
    c.connect(dev.get("host", "localhost"), int(dev.get("port", 1883)), keepalive=10)
    c.loop_start()
    try:
        c.publish(dev["topic"], payload, qos=1).wait_for_publish(timeout=5)
    finally:
        c.loop_stop()
        c.disconnect()


def _device(cfg, name: str) -> dict:
    devs = getattr(cfg, "devices", []) or []
    names = {d["name"].lower(): d for d in devs}
    q = str(name).lower().strip()
    hit = names.get(q) or next((d for n, d in names.items() if q and q in n), None)
    if hit is None and (close := difflib.get_close_matches(q, names, n=1, cutoff=0.6)):
        hit = names[close[0]]
    if hit is None:
        raise ValueError(f"no device called '{name}'; devices: "
                         + (", ".join(d["name"] for d in devs) or "none set up yet"))
    return hit


def _command(dev: dict, command: str) -> tuple[str, dict]:
    cmds = dev.get("commands", {})
    key = str(command).lower().strip()
    if key not in cmds:
        raise ValueError(f"{dev['name']} has no command '{command}'; it has: "
                         + ", ".join(cmds))
    return key, cmds[key]


def _render(spec: dict, value) -> str:
    send = spec["send"]
    if "{value}" not in send:
        return send
    lo, hi = spec.get("min", 0), spec.get("max", 100)
    try:
        v = int(value)
        assert str(value).strip().lstrip("-").isdigit() or isinstance(value, int)
    except (TypeError, ValueError, AssertionError):
        raise ValueError(f"that command needs a whole number from {lo} to {hi}") from None
    if not lo <= v <= hi:
        raise ValueError(f"{v} is outside {lo} to {hi}")
    return send.replace("{value}", str(v))


def _port(dev: dict) -> str:
    if dev.get("port", "auto") != "auto":
        return dev["port"]
    found = [p for p in _ports() if USB_SERIAL.search(p.description or "")]
    if len(found) != 1:
        raise ValueError(
            (f"several USB serial ports ({', '.join(p.device for p in found)}); " if found
             else "no USB serial device is plugged in; ")
            + f"set port = \"COMx\" for {dev['name']} in config.toml")
    return found[0].device


def send(dev: dict, line: str, reply: bool = False) -> str:
    if dev.get("transport") == "mqtt":
        _mqtt_publish(dev, line)
        return f"Sent {line} to {dev['name']}."
    port = _port(dev)
    try:
        s = _open_serial(port, int(dev.get("baud", 115200)))
    except (PermissionError, OSError) as e:
        raise ValueError(f"{port} is busy or unavailable (is another app, like the motor-fault "
                         f"dashboard, using it?): {e}") from None
    try:
        s.write((line + dev.get("newline", "\n")).encode())
        answer = s.readline().decode("utf-8", errors="replace").strip() if reply else ""
    finally:
        s.close()
    return f"Sent {line} to {dev['name']}." + (f" It replied: {answer or '(nothing)'}"
                                               if reply else "")


def _needs_confirm(ctx, a: dict) -> bool:
    key, spec = _command(_device(ctx.cfg, a.get("device", "")), a.get("command", ""))
    return bool(spec.get("confirm")) and key not in SAFE


def _remote_ok(ctx, a: dict) -> bool:
    return not _needs_confirm(ctx, a)   # from the phone: only commands that never ask


def _describe(a: dict) -> str:
    v = a.get("value")
    return f"{a.get('command', '')} the {a.get('device', '')}" + (f" to {v}" if v is not None
                                                                  else "")


@tool("Control one of the user's devices (see list_devices). Only commands listed for the "
      "device exist.", needs_confirm=_needs_confirm, remote_ok=_remote_ok, describe=_describe,
      device="string: the device name", command="string: the command name",
      value="?integer: the number, for commands that take one")
def device_command(ctx, device, command, value=None):
    dev = _device(ctx.cfg, device)
    key, spec = _command(dev, command)
    result = send(dev, _render(spec, value), bool(spec.get("reply")))
    pair = spec.get("undo")
    if pair and pair in dev["commands"] and not dev["commands"][pair].get("confirm"):
        # only a safe pair (start → stop, on → off) can be sent back without asking
        return result, {"kind": "device_send", "device": dev,
                        "line": dev["commands"][pair]["send"]}
    return result, None


@tool("List the user's devices and their commands.", readonly=True)
def list_devices(ctx):
    out = []
    for d in getattr(ctx.cfg, "devices", []) or []:
        cmds = []
        for n, s in d.get("commands", {}).items():
            arg = f" <{s.get('min', 0)}-{s.get('max', 100)}>" if "{value}" in s["send"] else ""
            ask = " (asks first)" if s.get("confirm") and n not in SAFE else ""
            cmds.append(f"{n}{arg}{ask}")
        out.append(f"- {d['name']} ({d.get('transport', 'serial')}): " + ", ".join(cmds))
    return "\n".join(out) or "No devices are set up (add [[devices]] to config.toml).", None


def undo(u: dict) -> None:
    send(u["device"], u["line"])


KINDS = {"device_send"}
