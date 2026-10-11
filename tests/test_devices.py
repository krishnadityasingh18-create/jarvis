from types import SimpleNamespace as NS

import pytest

from jarvis import db
from jarvis.config import load_config
from jarvis.tools import TOOLS, devices, run, undo
from tests.test_tools import call, make_ctx

RIG = {"name": "motor rig", "transport": "serial", "port": "auto", "baud": 921600,
       "commands": {"ping": {"send": "PING", "reply": True},
                    "start": {"send": "START", "confirm": True, "undo": "stop"},
                    "stop": {"send": "STOP"},
                    "speed": {"send": "PWM {value}", "confirm": True, "min": 0, "max": 1023}}}
LAMP = {"name": "desk lamp", "transport": "mqtt", "host": "localhost", "topic": "home/lamp/set",
        "commands": {"on": {"send": "ON", "confirm": True, "undo": "off"},
                     "off": {"send": "OFF"}}}


class FakePort:
    def __init__(self, log, reply=b"PONG\n"):
        self.log, self.reply = log, reply

    def write(self, data):
        self.log.append(("write", data))

    def readline(self):
        return self.reply

    def close(self):
        self.log.append(("close",))


@pytest.fixture
def rig(cfg, monkeypatch):
    log = []
    cfg.devices = [RIG, LAMP]
    monkeypatch.setattr(devices, "_ports", lambda: [
        NS(device="COM5", description="Silicon Labs CP210x USB to UART Bridge (COM5)"),
        NS(device="COM1", description="Communications Port (COM1)")])
    monkeypatch.setattr(devices, "_open_serial", lambda port, baud: (log.append(("open", port, baud)),
                                                                      FakePort(log))[1])
    monkeypatch.setattr(devices, "_mqtt_publish", lambda dev, payload: log.append(
        ("mqtt", dev["topic"], payload)))
    return log


async def test_ping_needs_no_confirm_and_reads_reply(cfg, conn, rig):
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("device_command", device="motor", command="ping"))
    assert ctx.asked == [] and "PONG" in r
    assert rig == [("open", "COM5", 921600), ("write", b"PING\n"), ("close",)]


async def test_start_asks_first(cfg, conn, rig):
    ctx = make_ctx(cfg, conn, answer=False)
    r = await run(ctx, call("device_command", device="motor rig", command="start"))
    assert r == "The user declined." and ctx.asked == ["Start the motor rig, sir?"] and rig == []


async def test_stop_never_asks_even_tainted(cfg, conn, rig):
    ctx = make_ctx(cfg, conn, answer=False)
    ctx.tainted = True
    r = await run(ctx, call("device_command", device="motor rig", command="stop"))
    assert ctx.asked == [] and ("write", b"STOP\n") in rig and not r.startswith("Error")


async def test_value_rendered_and_range_checked(cfg, conn, rig):
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("device_command", device="motor rig", command="speed", value=512))
    assert ("write", b"PWM 512\n") in rig and "speed the motor rig to 512" in ctx.asked[0].lower()
    for bad in (2000, -1, None, "fast"):
        r = await run(make_ctx(cfg, conn), call("device_command", device="motor rig",
                                                command="speed", value=bad))
        assert r.startswith("Error"), bad


async def test_unknown_device_and_command_listed(cfg, conn, rig):
    r = await run(make_ctx(cfg, conn), call("device_command", device="toaster", command="on"))
    assert r.startswith("Error") and "motor rig" in r and "desk lamp" in r
    r = await run(make_ctx(cfg, conn), call("device_command", device="motor rig",
                                            command="explode"))
    assert r.startswith("Error") and "ping" in r and "stop" in r and rig == []


async def test_mqtt_device(cfg, conn, rig):
    await run(make_ctx(cfg, conn), call("device_command", device="lamp", command="on"))
    assert ("mqtt", "home/lamp/set", "ON") in rig


async def test_undo_sends_the_safe_pair(cfg, conn, rig):
    await run(make_ctx(cfg, conn), call("device_command", device="motor rig", command="start"))
    undo(conn, db.recent_actions(conn)[0]["id"])
    assert rig[-2] == ("write", b"STOP\n")


async def test_remote_can_stop_but_not_start(cfg, conn, rig):
    ctx = make_ctx(cfg, conn)
    ctx.remote, ctx.tainted = "phone", True
    r = await run(ctx, call("device_command", device="motor rig", command="start"))
    assert "at the desk" in r
    await run(ctx, call("device_command", device="motor rig", command="stop"))
    assert ("write", b"STOP\n") in rig


async def test_auto_port_ambiguous_or_missing(cfg, conn, rig, monkeypatch):
    monkeypatch.setattr(devices, "_ports", lambda: [])
    r = await run(make_ctx(cfg, conn), call("device_command", device="motor rig", command="ping"))
    assert r.startswith("Error") and "port" in r
    monkeypatch.setattr(devices, "_ports", lambda: [NS(device="COM5", description="CP210x"),
                                                    NS(device="COM6", description="CH340")])
    r = await run(make_ctx(cfg, conn), call("device_command", device="motor rig", command="ping"))
    assert r.startswith("Error") and "COM5" in r and "COM6" in r


async def test_busy_port_is_plain(cfg, conn, rig, monkeypatch):
    def busy(port, baud):
        raise PermissionError("Access is denied")
    monkeypatch.setattr(devices, "_open_serial", busy)
    r = await run(make_ctx(cfg, conn), call("device_command", device="motor rig", command="stop"))
    assert r.startswith("Error") and "busy" in r


async def test_list_devices(cfg, conn, rig):
    r = await run(make_ctx(cfg, conn), call("list_devices"))
    assert "motor rig (serial)" in r and "speed <0-1023> (asks first)" in r
    assert TOOLS["list_devices"].readonly


def test_config_has_the_motor_rig_as_data():
    c = load_config()
    rig = next(d for d in c.devices if d["name"] == "motor rig")
    assert rig["commands"]["start"]["confirm"] is True and rig["baud"] == 921600
