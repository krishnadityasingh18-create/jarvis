# JARVIS Phase 5: Hardware Control (Serial and MQTT) — Design

Status: self-approved under the user's standing permission (2026-10-12). Decisions in §7.

## 1. Goal

Control the user's devices by voice: anything that speaks text lines over USB serial
(ESP32/Arduino) or MQTT (ESP32 on Wi-Fi, Raspberry Pi, smart plugs). Adding a device is a
config entry, never a code change. Anything that moves or powers hardware asks first.

## 2. Devices are config

```toml
[[devices]]
name = "motor rig"                   # what the user says
transport = "serial"                 # or "mqtt"
port = "auto"                        # COM port, or "auto": the one ESP32/CH340/CP210x shows up as
baud = 921600
newline = "\n"
[devices.commands]                   # what JARVIS may send; nothing else is ever sent
ping   = { send = "PING",  reply = true }
info   = { send = "INFO",  reply = true }
start  = { send = "START", confirm = true }
stop   = { send = "STOP" }           # stopping is always allowed without asking
speed  = { send = "PWM {value}", confirm = true, min = 0, max = 1023 }

[[devices]]
name = "desk lamp"
transport = "mqtt"
host = "localhost"                   # broker
topic = "home/desk_lamp/set"
[devices.commands]
on  = { send = "ON" }
off = { send = "OFF" }
```

- `send` is a template; `{value}` must be an integer within `min..max` (refused otherwise).
- `confirm = true` → JARVIS reads back "Start the motor rig, sir?" (safety rule: actuating
  hardware that could cause damage asks first). A command named `stop`/`off` never asks.
- `reply = true` (serial) → read one reply line (2 s timeout) and report it.

## 3. Tools

- `list_devices()` — names, transports, commands (read-only).
- `device_command(device, command, value?)` — fuzzy device name, exact command name;
  sends the rendered line; logs the action. Undo: none (hardware state can't be un-sent)
  except where the config pairs commands (`undo = "stop"` on `start`, `undo = "off"` on
  `on`), then Undo sends the pair.
- Remote channels (phone/WhatsApp) can run only commands without `confirm` (e.g. stop,
  status) — confirm-commands need the desk, as everywhere else.

## 4. Transport details

- **Serial:** pyserial; the port is opened per command and closed after (the motor-fault
  desktop app may need it too — "port busy" is reported plainly). `auto` picks the single
  port whose description matches CP210x/CH340/USB-SERIAL/ESP32; several or none → ask the
  user to set `port`.
- **MQTT:** paho-mqtt publish (QoS 1) to the device's topic on its broker; optional
  `username` in config and password in Credential Manager (`keys set mqtt_<device>`).

## 5. Errors

Device not found → list devices. Command not in config → list its commands. Port missing or
busy, broker unreachable, reply timeout → plain message; never retried automatically.

## 6. Testing

Fake serial port and fake MQTT client: rendering and range checks, confirm on dangerous
commands, stop never asks, reply reading, auto-port selection, busy port, remote restriction,
undo pairs, config loading (adding a device needs no code). Live: list real COM ports; if the
ESP32 rig is connected, `ping`/`info` only (no motor commands without the user).

## 7. Decisions made under standing permission

1. Whitelisted commands per device; JARVIS never sends free text to hardware.
2. Start/speed/power-on ask first; stop/off never ask (stopping must always be easy).
3. Serial port opened per command so the motor-fault app can share the rig.
4. The motor-fault rig is the first example device (its firmware protocol: START, STOP,
   PWM 0-1023, PING, INFO at 921600 baud).
5. MQTT broker is the user's own (none is installed yet); no cloud IoT services.
