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



HEATER = {"name": "desk heater", "transport": "mqtt", "topic": "h", "commands": {
    "on": {"send": "ON", "confirm": True}, "off": {"send": "OFF"}}}
FAN = {"name": "room fan", "transport": "mqtt", "topic": "f", "commands": {
    "on": {"send": "ON"}, "off": {"send": "OFF"}}}


async def test_wrong_or_ambiguous_device_is_never_guessed(cfg, conn, rig):
    cfg.devices = [HEATER, FAN]
    for name in ("desk fan", "desk heatr"):           # mix of two, or a misspelling
        ctx = make_ctx(cfg, conn)
        r = await run(ctx, call("device_command", device=name, command="on"))
        assert r.startswith("Error") and "did you mean" in r and ctx.asked == [], name
    assert rig == []


async def test_question_names_the_device_that_will_run(cfg, conn, rig):
    cfg.devices = [HEATER, FAN]
    ctx = make_ctx(cfg, conn, answer=False)
    await run(ctx, call("device_command", device="heater", command="on"))
    assert ctx.asked == ["On the desk heater, sir?"]


async def test_untrusted_text_cannot_switch_anything_on_silently(cfg, conn, rig):
    cfg.devices = [HEATER, FAN]
    ctx = make_ctx(cfg, conn, answer=False)
    ctx.tainted = True
    r = await run(ctx, call("device_command", device="room fan", command="on"))
    assert r == "The user declined." and rig == []


async def test_remote_only_stops(cfg, conn, rig):
    cfg.devices = [HEATER, FAN]
    ctx = make_ctx(cfg, conn)
    ctx.remote, ctx.tainted = "whatsapp", True
    r = await run(ctx, call("device_command", device="room fan", command="on"))
    assert "at the desk" in r and rig == []
    await run(ctx, call("device_command", device="room fan", command="off"))
    assert ("mqtt", "f", "OFF") in rig


async def test_bad_value_refused_before_asking(cfg, conn, rig):
    for bad in (2000, True, 512.7):
        ctx = make_ctx(cfg, conn)
        r = await run(ctx, call("device_command", device="motor rig", command="speed",
                                value=bad))
        assert r.startswith("Error") and ctx.asked == [], bad


async def test_value_not_shown_for_commands_without_one(cfg, conn, rig):
    ctx = make_ctx(cfg, conn, answer=False)
    await run(ctx, call("device_command", device="motor rig", command="start", value=999))
    assert ctx.asked == ["Start the motor rig, sir?"]


async def test_several_auto_devices_need_a_match(cfg, conn, rig):
    other = {"name": "plant monitor", "transport": "serial", "port": "auto", "baud": 115200,
             "commands": {"stop": {"send": "STOP"}}}
    cfg.devices = [RIG, other]
    r = await run(make_ctx(cfg, conn), call("device_command", device="motor rig", command="stop"))
    assert r.startswith("Error") and "match" in r and rig == []
    cfg.devices = [{**RIG, "match": "CP210"}, {**other, "match": "CH340"}]
    await run(make_ctx(cfg, conn), call("device_command", device="motor rig", command="stop"))
    assert ("open", "COM5", 921600) in rig


def test_mqtt_unconfirmed_publish_is_an_error(monkeypatch):
    class Info:
        def wait_for_publish(self, timeout):
            pass

        def is_published(self):
            return False

    class Client:
        def __init__(self, *a):
            pass

        def username_pw_set(self, *a):
            pass

        def connect(self, *a, **k):
            pass

        def loop_start(self):
            pass

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

        def publish(self, *a, **k):
            return Info()
    import paho.mqtt.client as mqtt
    monkeypatch.setattr(mqtt, "Client", Client)
    with pytest.raises(ValueError, match="didn't confirm"):
        devices._mqtt_publish({"name": "desk lamp", "topic": "t"}, "ON")


def test_bad_device_config_names_the_entry(tmp_path):
    from pathlib import Path as P
    text = P("config.toml").read_text(encoding="utf-8") + (
        '\n[[devices]]\nname = "broken"\ntransport = "mqtt"\n[devices.commands]\n'
        'on = { send = "ON" }\n')
    f = tmp_path / "c.toml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="broken"):
        load_config(f)                                  # mqtt device without a topic


def test_command_names_case_insensitive_in_config(tmp_path):
    from pathlib import Path as P
    text = P("config.toml").read_text(encoding="utf-8") + (
        '\n[[devices]]\nname = "Lamp"\ntransport = "mqtt"\ntopic = "l"\n[devices.commands]\n'
        'On = { send = "ON", undo = "Off" }\nOff = { send = "OFF" }\n')
    f = tmp_path / "c.toml"
    f.write_text(text, encoding="utf-8")
    lamp = next(d for d in load_config(f).devices if d["name"] == "Lamp")
    assert set(lamp["commands"]) == {"on", "off"} and lamp["commands"]["on"]["undo"] == "off"
