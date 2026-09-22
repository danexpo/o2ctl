"""Text form of button bindings, shared by the CLI and the GUI.

    left-click, dpi-cycle-up, disabled, ...  a mouse action
    dpi-shift:800                            hold for 800 DPI
    media:play-pause                         a media key
    key:ctrl+shift+f5                        a keystroke with modifiers
"""

from . import protocol as P


def parse(text):
    text = text.strip().lower()
    if text in P.MOUSE_ACTIONS:
        return {"type": "mouse", "action": text}
    kind, _, arg = text.partition(":")
    if kind == "dpi-shift":
        try:
            dpi = int(arg or 400)
        except ValueError:
            raise P.ProtocolError(f"bad DPI shift value: {arg}") from None
        binding = {"type": "mouse", "action": "dpi-shift", "dpi": dpi}
    elif kind == "media" or text in P.MEDIA_ACTIONS:
        binding = {"type": "media", "action": arg if kind == "media" else text}
    elif kind == "key":
        binding = parse_keys(arg)
    else:
        raise P.ProtocolError(f"unknown action: {text}")
    P.binding_slot(binding)  # validate
    return binding


def parse_keys(combo):
    parts = [p for p in combo.replace(" ", "").lower().split("+") if p]
    if not parts:
        raise P.ProtocolError("empty key combination")
    aliases = {"control": "ctrl", "meta": "super", "win": "super", "cmd": "super"}
    mods = [aliases.get(p, p) for p in parts[:-1]]
    key = {"esc": "escape", "return": "enter", "del": "delete", "ins": "insert",
           "pgup": "pageup", "pgdn": "pagedown"}.get(parts[-1], parts[-1])
    binding = {"type": "key", "key": key, "modifiers": mods}
    P.binding_slot(binding)
    return binding


def format(binding):
    kind = binding.get("type")
    if kind == "mouse" and binding.get("action") == "dpi-shift":
        return f"dpi-shift:{binding.get('dpi', 400)}"
    if kind == "mouse":
        return binding["action"]
    if kind == "media":
        return f"media:{binding['action']}"
    if kind == "key":
        return "key:" + "+".join(list(binding.get("modifiers", [])) + [str(binding["key"])])
    return "?"


def describe(binding):
    kind = binding.get("type")
    if kind == "mouse" and binding.get("action") == "dpi-shift":
        return f"DPI shift ({binding.get('dpi', 400)} DPI while held)"
    if kind == "mouse":
        return P.MOUSE_ACTION_LABELS.get(binding["action"], binding["action"])
    if kind == "media":
        return P.MEDIA_ACTION_LABELS.get(binding["action"], binding["action"])
    if kind == "key":
        names = [m.capitalize() for m in binding.get("modifiers", [])] + [str(binding["key"]).upper()]
        return "Key " + "+".join(names)
    return "Unknown"
