"""Packet builders for the Glorious Model O 2 Wireless vendor protocol.

Everything here is pure: settings in, HID feature reports out. The layouts
come from Glorious CORE 2.1.21 (MouseV2DeviceHandler and the
Prepare*Buffers2 helpers in its main-process bundle) and CORE 1/2.1.3.

Over USB every packet is 64-byte feature report 3: [0x03, command, route,
...]. The route byte is 0xFB through the 2.4 GHz dongle and 0xFA over the
cable. The dongle carries small payloads, so larger blocks are split into
numbered chunks. Bluetooth uses 21-byte feature report 5 with the dongle's
packets minus the route byte: [0x05, command, ...].
"""

import struct

VID = 0x093A
PID_WIRED = 0x822A
PID_BLUETOOTH = 0x822B
PID_DONGLE = 0x822D

WIRED = "wired"
DONGLE = "dongle"
BLUETOOTH = "bluetooth"
TRANSPORTS = (WIRED, DONGLE, BLUETOOTH)

REPORT_ID = 0x03
PACKET_SIZE = 64
ROUTE = {DONGLE: 0xFB, WIRED: 0xFA}
BT_REPORT_ID = 0x05
BT_PACKET_SIZE = 21
# Links where the mouse itself answers a battery query. The dongle answers
# from what it last heard, so through it o2ctl waits for the mouse's reports.
BATTERY_QUERY_LINKS = (WIRED, BLUETOOTH)

CMD_PROFILE = 0x01
CMD_LIGHTING = 0x02
CMD_BUTTONS = 0x03
CMD_PERFORMANCE = 0x04
CMD_MACRO = 0x05
CMD_SLEEP = 0x06
CMD_BATTERY = 0x08
BATTERY_QUERY = 0x14

PROFILE_COUNT = 3
DPI_MIN = 100
DPI_MAX = 26000
DPI_STEP = 50
MAX_DPI_STAGES = 6
MAX_COLORS = 6
DEBOUNCE_MAX = 16

POLLING_RATES = {125: 1, 250: 2, 500: 3, 1000: 4}
LIFT_OFF = (1, 2)

# name -> (hardware effect id, how many colours the effect uses)
EFFECTS = {
    "off": (0, 0),
    "glorious": (1, 0),
    "seamless-breathing": (2, 0),
    "breathing": (3, MAX_COLORS),
    "single-color": (4, 1),
    "breathing-single": (5, 1),
    "tail": (6, 0),
    "rave": (7, 2),
    "wave": (8, 0),
}
EFFECT_LABELS = {
    "off": "Off",
    "glorious": "Glorious Mode",
    "seamless-breathing": "Seamless Breathing",
    "breathing": "Breathing (RGB)",
    "single-color": "Single Color",
    "breathing-single": "Breathing (Single Color)",
    "tail": "Tail",
    "rave": "Rave",
    "wave": "Wave",
}
# Effects whose speed setting does anything.
ANIMATED = {"glorious", "seamless-breathing", "breathing", "breathing-single", "tail", "rave", "wave"}

# Physical button -> slot in the binding table.
BUTTONS = ("left", "right", "middle", "back", "forward", "dpi")
BUTTON_SLOT = {"left": 0, "right": 1, "middle": 2, "back": 3, "forward": 4, "dpi": 5}
BUTTON_LABELS = {
    "left": "Left button",
    "right": "Right button",
    "middle": "Wheel click",
    "back": "Back (rear side button)",
    "forward": "Forward (front side button)",
    "dpi": "DPI button",
}

# Action name -> 4-byte slot value, for actions that take no parameter.
MOUSE_ACTIONS = {
    "left-click": (1, 1),
    "right-click": (1, 2),
    "middle-click": (1, 3),
    "back": (1, 4),
    "forward": (1, 5),
    "scroll-up": (1, 160),
    "scroll-down": (1, 161),
    "profile-cycle-up": (1, 176),
    "profile-cycle-down": (1, 177),
    "dpi-stage-up": (102, 1),
    "dpi-stage-down": (102, 2),
    "dpi-cycle-up": (102, 3),
    "dpi-cycle-down": (102, 4),
    "battery-check": (119, 0),
    "disabled": (0, 0),
}
MOUSE_ACTION_LABELS = {
    "left-click": "Left click",
    "right-click": "Right click",
    "middle-click": "Middle click",
    "back": "Back",
    "forward": "Forward",
    "scroll-up": "Scroll up",
    "scroll-down": "Scroll down",
    "profile-cycle-up": "Next profile",
    "profile-cycle-down": "Previous profile",
    "dpi-stage-up": "DPI up",
    "dpi-stage-down": "DPI down",
    "dpi-cycle-up": "DPI cycle",
    "dpi-cycle-down": "DPI cycle (reverse)",
    "battery-check": "Show battery level",
    "disabled": "Disabled",
}

# Consumer-page usages, sent as [3, low, high, 0].
MEDIA_ACTIONS = {
    "play-pause": 0x00CD,
    "stop": 0x00B7,
    "next-track": 0x00B5,
    "previous-track": 0x00B6,
    "mute": 0x00E2,
    "volume-up": 0x00E9,
    "volume-down": 0x00EA,
    "media-player": 0x0183,
    "email": 0x018A,
    "calculator": 0x0192,
    "browser-home": 0x0223,
    "browser-back": 0x0224,
    "browser-forward": 0x0225,
    "browser-refresh": 0x0227,
    "browser-search": 0x0221,
}
MEDIA_ACTION_LABELS = {
    "play-pause": "Play / pause",
    "stop": "Stop",
    "next-track": "Next track",
    "previous-track": "Previous track",
    "mute": "Mute",
    "volume-up": "Volume up",
    "volume-down": "Volume down",
    "media-player": "Open media player",
    "email": "Open email",
    "calculator": "Open calculator",
    "browser-home": "Browser home",
    "browser-back": "Browser back",
    "browser-forward": "Browser forward",
    "browser-refresh": "Browser refresh",
    "browser-search": "Browser search",
}

MODIFIER_BITS = {"ctrl": 1, "shift": 2, "alt": 4, "super": 8}

# Keyboard-page usages for keystroke bindings.
KEYS = {chr(ord("a") + i): 4 + i for i in range(26)}
KEYS.update({str(n): 29 + n for n in range(1, 10)})
KEYS["0"] = 39
KEYS.update({
    "enter": 40, "escape": 41, "backspace": 42, "tab": 43, "space": 44,
    "minus": 45, "equal": 46, "bracketleft": 47, "bracketright": 48,
    "backslash": 49, "semicolon": 51, "apostrophe": 52, "grave": 53,
    "comma": 54, "period": 55, "slash": 56, "capslock": 57,
    "print": 70, "scrolllock": 71, "pause": 72, "insert": 73, "home": 74,
    "pageup": 75, "delete": 76, "end": 77, "pagedown": 78, "right": 79,
    "left": 80, "down": 81, "up": 82, "menu": 101,
})
KEYS.update({f"f{n}": 57 + n for n in range(1, 13)})
KEYS.update({f"f{n}": 91 + n for n in range(13, 25)})
KEY_NAMES = {code: name for name, code in KEYS.items()}
# Macros press modifiers as keys of their own.
MACRO_KEYS = dict(KEYS, ctrl=224, shift=225, alt=226, super=227, rctrl=228, rshift=229, ralt=230, rsuper=231)

# Macros live outside the profiles: each has an id, and a button in any
# profile can play it. The binding slot is [0x1F + id, mode, 0, 0], so ids
# stop short of 0x66 (DPI actions).
MAX_MACROS = 16
MACRO_MAX_STEPS = 85  # CORE builds each macro in a 256-byte buffer, 3 bytes a step
MACRO_DELAY_MAX = 0x7FFF  # ms; 15 bits, the top bit marks a press
MACRO_MODES = {"once": 0x01, "repeat": 0xE0, "toggle": 0xE1}
MACRO_MODE_LABELS = {
    "once": "Play once",
    "repeat": "Repeat while held",
    "toggle": "Repeat until pressed again",
}
# Mouse buttons a macro can press, as the mouse numbers them in macro steps.
MACRO_BUTTONS = {"left": 0xB7, "right": 0xB8, "middle": 0xB9, "back": 0xBA, "forward": 0xBB}

# A button bound to this does nothing on the mouse; pressing it sends a
# button event (see parse_event) and CORE launches a program or website.
HOST_SHORTCUT_SLOT = bytes([4, 1, 0, 0])
BUTTON_AT_SLOT = {slot: button for button, slot in BUTTON_SLOT.items()}

DEFAULT_BINDINGS = {
    "left": {"type": "mouse", "action": "left-click"},
    "right": {"type": "mouse", "action": "right-click"},
    "middle": {"type": "mouse", "action": "middle-click"},
    "back": {"type": "mouse", "action": "back"},
    "forward": {"type": "mouse", "action": "forward"},
    "dpi": {"type": "mouse", "action": "dpi-cycle-up"},
}

# Slot values the mouse ships with, including the two wheel slots that only
# the wired layout carries.
DEFAULT_SLOTS = bytes([
    1, 1, 0, 0,
    1, 2, 0, 0,
    1, 3, 0, 0,
    1, 4, 0, 0,
    1, 5, 0, 0,
    102, 3, 0, 0,
    1, 160, 0, 0,
    1, 161, 0, 0,
    0, 0, 0, 0,
    0, 0, 0, 0,
])


class ProtocolError(ValueError):
    pass


def _check_transport(transport):
    if transport not in TRANSPORTS:
        raise ProtocolError(f"unsupported transport: {transport}")


def report_format(transport):
    """(feature report ID, packet size) for `transport`."""
    _check_transport(transport)
    return (BT_REPORT_ID, BT_PACKET_SIZE) if transport == BLUETOOTH else (REPORT_ID, PACKET_SIZE)


def _bluetooth(packet):
    """A dongle packet as Bluetooth sends it: report 5, no route byte, 21 bytes."""
    body = bytes(packet[3:])
    if any(body[BT_PACKET_SIZE - 2 :]):
        raise ProtocolError("packet does not fit a Bluetooth report")
    return bytearray([BT_REPORT_ID, packet[1]]) + body[: BT_PACKET_SIZE - 2]


def _check_profile(profile):
    if not 1 <= profile <= PROFILE_COUNT:
        raise ProtocolError(f"profile must be 1-{PROFILE_COUNT}, got {profile}")


def _packet(*head, size=PACKET_SIZE):
    p = bytearray(size)
    p[: len(head)] = bytes(head)
    return p


def parse_color(value):
    """'#ff8000', 'ff8000', '#f80' or (r, g, b) -> (r, g, b)."""
    if isinstance(value, (tuple, list)):
        r, g, b = value
        return int(r), int(g), int(b)
    s = str(value).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ProtocolError(f"bad colour: {value!r}")
    try:
        return tuple(bytes.fromhex(s))
    except ValueError:
        raise ProtocolError(f"bad colour: {value!r}") from None


# Lights off after this many idle minutes; the range Glorious CORE 1 offers.
SLEEP_MAX = 15
SLEEP_NEVER = 0xFF


def sleep_packet(minutes, transport):
    """Turn every LED off after `minutes` without use (None: never)."""
    _check_transport(transport)
    if minutes is not None and not 1 <= minutes <= SLEEP_MAX:
        raise ProtocolError(f"idle time must be 1-{SLEEP_MAX} minutes")
    if transport == BLUETOOTH:
        return [_bluetooth(p) for p in sleep_packet(minutes, DONGLE)]
    return [_packet(REPORT_ID, CMD_SLEEP, ROUTE[transport], SLEEP_NEVER if minutes is None else minutes)]


def profile_packet(profile, transport):
    """Make `profile` (1-3) the active onboard profile."""
    _check_transport(transport)
    _check_profile(profile)
    if transport == BLUETOOTH:
        # Unlike the other commands this keeps a route byte, the cable's (as
        # CORE sends it); 05 01 <profile> is ignored.
        return [_packet(BT_REPORT_ID, CMD_PROFILE, ROUTE[WIRED], profile, size=BT_PACKET_SIZE)]
    return [_packet(REPORT_ID, CMD_PROFILE, ROUTE[transport], profile)]


def performance_block(perf, transport):
    """The settings block shared by every transport, before chunking."""
    stages = perf["dpi_stages"]
    if not 1 <= len(stages) <= MAX_DPI_STAGES:
        raise ProtocolError(f"need 1-{MAX_DPI_STAGES} DPI stages")
    active = perf["active_stage"]
    if not 0 <= active < len(stages):
        raise ProtocolError("active_stage is outside the stage list")
    rate = perf["polling_rate"]
    if rate not in POLLING_RATES:
        raise ProtocolError(f"polling rate must be one of {sorted(POLLING_RATES)}")
    lod = perf["lift_off"]
    if lod not in LIFT_OFF:
        raise ProtocolError("lift-off distance must be 1 or 2 mm")
    debounce = perf["debounce"]
    if not 0 <= debounce <= DEBOUNCE_MAX:
        raise ProtocolError(f"debounce must be 0-{DEBOUNCE_MAX} ms")

    d = bytearray(256)
    d[0] = active
    d[1] = len(stages)
    d[2] = lod
    d[3] = debounce
    d[4] = POLLING_RATES[rate]
    d[5] = 1 if perf["motion_sync"] else 0
    offset, stride = (12, 8) if transport == WIRED else (6, 5)
    for i, stage in enumerate(stages):
        dpi = stage["dpi"]
        if not DPI_MIN <= dpi <= DPI_MAX:
            raise ProtocolError(f"DPI must be {DPI_MIN}-{DPI_MAX}, got {dpi}")
        at = offset + stride * i
        struct.pack_into("<h", d, at, round(dpi / DPI_STEP))
        d[at + 2 : at + 5] = bytes(parse_color(stage["color"]))
    return d


def performance_packets(profile, perf, transport):
    _check_transport(transport)
    _check_profile(profile)
    if transport == BLUETOOTH:
        return [_bluetooth(p) for p in performance_packets(profile, perf, DONGLE)]
    d = performance_block(perf, transport)
    if transport == WIRED:
        p = _packet(REPORT_ID, CMD_PERFORMANCE, ROUTE[WIRED], profile)
        p[4:64] = d[0:60]
        return [p]
    packets = []
    for index, (start, end) in enumerate(((0, 11), (11, 21), (21, 31), (31, 41))):
        p = _packet(REPORT_ID, CMD_PERFORMANCE, ROUTE[DONGLE], index, profile)
        p[5 : 5 + end - start] = d[start:end]
        packets.append(p)
    return packets


def _speed_level(percent):
    return percent * 20 // 100 if percent > 4 else 1


def _brightness_level(percent):
    if percent == 0:
        return 0
    return percent * 20 // 100 if percent > 4 else 1


def lighting_block(light, transport):
    effect = light["effect"]
    if effect not in EFFECTS:
        raise ProtocolError(f"unknown effect: {effect}")
    for key in ("speed", "brightness"):
        if not 0 <= light[key] <= 100:
            raise ProtocolError(f"{key} must be 0-100")
    battery = light.get("battery_brightness")
    if battery is not None and not 0 <= battery <= 100:
        raise ProtocolError("battery brightness must be 0-100")
    colors = [parse_color(c) for c in light["colors"][: EFFECTS[effect][1]]]

    d = bytearray(256)
    speed = _speed_level(light["speed"])
    brightness = _brightness_level(light["brightness"])
    d[0] = speed
    d[1] = brightness
    d[2] = len(colors)
    d[3] = speed
    # Byte 1 is used on the cable, byte 4 on battery; CORE copies one to the
    # other unless "separate wired and wireless brightness" is on.
    d[4] = brightness if battery is None else _brightness_level(battery)
    offset = 11 if transport == WIRED else 5
    for i, rgb in enumerate(colors):
        d[offset + 3 * i : offset + 3 * i + 3] = bytes(rgb)
    return d


def lighting_packets(profile, light, transport):
    _check_transport(transport)
    _check_profile(profile)
    d = lighting_block(light, transport)
    effect_id = EFFECTS[light["effect"]][0]
    if transport == WIRED:
        p = _packet(REPORT_ID, CMD_LIGHTING, ROUTE[WIRED], profile, effect_id)
        p[5:34] = d[0:29]
        return [p]
    if transport == BLUETOOTH:
        # Split differently from the dongle: 11 then 12 bytes.
        packets = []
        for index, (start, end) in enumerate(((0, 11), (11, 23))):
            p = _packet(BT_REPORT_ID, CMD_LIGHTING, index, profile, effect_id, size=BT_PACKET_SIZE)
            p[5 : 5 + end - start] = d[start:end]
            packets.append(p)
        return packets
    packets = []
    for index, (start, end) in enumerate(((0, 8), (8, 17), (17, 26))):
        p = _packet(REPORT_ID, CMD_LIGHTING, ROUTE[DONGLE], index, profile, effect_id)
        p[6 : 6 + end - start] = d[start:end]
        packets.append(p)
    return packets


def binding_slot(binding):
    """One binding dict -> its 4 bytes in the button table."""
    kind = binding.get("type")
    if kind == "mouse":
        action = binding.get("action")
        if action == "dpi-shift":
            dpi = int(binding.get("dpi", 400))
            if not DPI_MIN <= dpi <= DPI_MAX:
                raise ProtocolError(f"DPI shift must be {DPI_MIN}-{DPI_MAX}")
            return bytes([102, 5]) + struct.pack("<H", dpi // DPI_STEP)
        if action not in MOUSE_ACTIONS:
            raise ProtocolError(f"unknown mouse action: {action}")
        a, b = MOUSE_ACTIONS[action]
        return bytes([a, b, 0, 0])
    if kind == "key":
        key = binding.get("key")
        code = key if isinstance(key, int) else KEYS.get(str(key).lower())
        if code is None or not 0 < code < 256:
            raise ProtocolError(f"unknown key: {key}")
        mask = 0
        for mod in binding.get("modifiers", []):
            if mod not in MODIFIER_BITS:
                raise ProtocolError(f"unknown modifier: {mod}")
            mask |= MODIFIER_BITS[mod]
        return bytes([2, mask, code, 0])
    if kind == "media":
        action = binding.get("action")
        if action not in MEDIA_ACTIONS:
            raise ProtocolError(f"unknown media action: {action}")
        usage = MEDIA_ACTIONS[action]
        return bytes([3, usage & 0xFF, usage >> 8, 0])
    if kind == "command":
        if not str(binding.get("command", "")).strip():
            raise ProtocolError("the command is empty")
        # The mouse only reports the press; the host runs the command.
        return HOST_SHORTCUT_SLOT
    if kind == "macro":
        macro_id = binding.get("macro")
        if not isinstance(macro_id, int) or not 1 <= macro_id <= MAX_MACROS:
            raise ProtocolError(f"macro id must be 1-{MAX_MACROS}")
        mode = binding.get("mode", "once")
        if mode not in MACRO_MODES:
            raise ProtocolError(f"unknown macro mode: {mode}")
        return bytes([0x1F + macro_id, MACRO_MODES[mode], 0, 0])
    raise ProtocolError(f"unknown binding type: {kind}")


def buttons_block(bindings):
    d = bytearray(DEFAULT_SLOTS)
    for button, binding in bindings.items():
        if button not in BUTTON_SLOT:
            raise ProtocolError(f"unknown button: {button}")
        at = BUTTON_SLOT[button] * 4
        d[at : at + 4] = binding_slot(binding)
    return d


def buttons_packets(profile, bindings, transport):
    _check_transport(transport)
    _check_profile(profile)
    if transport == BLUETOOTH:
        return [_bluetooth(p) for p in buttons_packets(profile, bindings, DONGLE)]
    d = buttons_block(bindings)
    if transport == WIRED:
        p = _packet(REPORT_ID, CMD_BUTTONS, ROUTE[WIRED], profile)
        p[8:44] = d[0:36]
        return [p]
    packets = []
    for index, (start, end) in enumerate(((0, 12), (12, 24))):
        p = _packet(REPORT_ID, CMD_BUTTONS, ROUTE[DONGLE], index, profile)
        p[5 : 5 + end - start] = d[start:end]
        packets.append(p)
    return packets


def macro_step_code(key):
    """A macro step's key name ('a', 'ctrl', 'mouse:left') -> its code."""
    name = str(key).lower()
    if name.startswith("mouse:"):
        code = MACRO_BUTTONS.get(name[6:])
    else:
        code = MACRO_KEYS.get(name)
    if code is None:
        raise ProtocolError(f"unknown key in macro: {key}")
    return code


def macro_block(steps):
    """Macro steps -> 3 bytes each: [press << 7 | delay >> 8, delay & 0xFF, code].

    A step is {"key": name, "down": bool, "delay": ms}; the delay is the
    wait after the step, before the next one.
    """
    if not steps:
        raise ProtocolError("a macro needs at least one step")
    if len(steps) > MACRO_MAX_STEPS:
        raise ProtocolError(f"a macro holds at most {MACRO_MAX_STEPS} steps, got {len(steps)}")
    d = bytearray()
    for step in steps:
        delay = int(step.get("delay", 1))
        if not 1 <= delay <= MACRO_DELAY_MAX:
            raise ProtocolError(f"macro delays must be 1-{MACRO_DELAY_MAX} ms, got {delay}")
        flag = 0x80 if step["down"] else 0
        d += bytes([flag | delay >> 8, delay & 0xFF, macro_step_code(step["key"])])
    return d


def macro_packets(macro_id, steps, transport):
    """Store macro `macro_id` (1-based) on the mouse. Macros aren't per profile."""
    _check_transport(transport)
    if not 1 <= macro_id <= MAX_MACROS:
        raise ProtocolError(f"macro id must be 1-{MAX_MACROS}")
    if transport == BLUETOOTH:
        return [_bluetooth(p) for p in macro_packets(macro_id, steps, DONGLE)]
    d = macro_block(steps)
    # Dongle: 3 steps per packet from byte 7. Cable: 18 steps per packet from byte 8.
    per, at = (18, 8) if transport == WIRED else (3, 7)
    count = len(steps)
    chunks = -(-count // per)
    packets = []
    for index in range(chunks):
        p = _packet(REPORT_ID, CMD_MACRO, ROUTE[transport], macro_id - 1, index, chunks, count)
        part = d[index * per * 3 : (index + 1) * per * 3]
        p[at : at + len(part)] = part
        packets.append(p)
    return packets


def battery_query_packet(transport):
    """Ask for the battery level; the answer is read back from the same feature report."""
    _check_transport(transport)
    if transport == BLUETOOTH:
        return _bluetooth(battery_query_packet(DONGLE))
    return _packet(REPORT_ID, CMD_BATTERY, ROUTE[transport], BATTERY_QUERY)


def parse_battery_reply(reply):
    """The feature report (ID removed) after a battery query: 08 14 level charging.

    None when it isn't an answer, or the mouse hasn't answered yet (level FF).
    """
    if len(reply) < 4 or reply[0] != CMD_BATTERY or reply[1] != BATTERY_QUERY or reply[2] > 100:
        return None
    # CORE 1 shows 0 as 1, presumably so an almost empty mouse isn't shown as off.
    return {"event": "battery", "level": max(reply[2], 1), "charging": reply[3] != 0}


def parse_event(report):
    """Decode an input report from the vendor interface, or return None.

    Report 6 carries status: 0xFB is a battery update (the dongle sends one
    about once a minute), 0xF6 means the mouse switched profile by itself
    (index 0-2). Report 4 with 0xF1 is the wired profile change (1-3). The
    mouse sends nothing when the host switches profile. A press of a button
    bound to HOST_SHORTCUT_SLOT is report 6 0xF9 (dongle) or report 4 0xF7
    (cable), then a byte of 0x80 or more, then the button's slot.
    """
    if len(report) < 3:
        return None
    rid, kind = report[0], report[1]
    if rid == 6 and kind == 0xFB and len(report) >= 4:
        level = report[2]
        if level > 100:
            return None
        return {"event": "battery", "level": level, "charging": report[3] != 0}
    if rid == 6 and kind == 0xF6 and report[2] < PROFILE_COUNT:
        return {"event": "profile", "profile": report[2] + 1}
    if rid == 4 and kind == 0xF1 and 1 <= report[2] <= PROFILE_COUNT:
        return {"event": "profile", "profile": report[2]}
    if (rid, kind) in ((6, 0xF9), (4, 0xF7)) and len(report) >= 4 and report[2] >= 0x80:
        button = BUTTON_AT_SLOT.get(report[3])
        if button:
            return {"event": "button", "button": button}
    return None
