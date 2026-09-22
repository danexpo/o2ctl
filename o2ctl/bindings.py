"""Text form of button bindings, shared by the CLI and the GUI.

    left-click, dpi-cycle-up, disabled, ...  a mouse action
    dpi-shift:800                            hold for 800 DPI
    media:play-pause                         a media key
    key:ctrl+shift+f5                        a keystroke with modifiers
    macro:NAME, macro:NAME:repeat            a saved macro (once, repeat, toggle)

Macro bindings refer to the macro by id; `macros` is the saved macro list
(state["macros"]) used to turn names into ids and back.
"""

from . import protocol as P

MODIFIER_ALIASES = {"control": "ctrl", "meta": "super", "win": "super", "cmd": "super"}
KEY_ALIASES = {"esc": "escape", "return": "enter", "del": "delete", "ins": "insert",
               "pgup": "pageup", "pgdn": "pagedown"}


def parse(text, macros=()):
    text = text.strip()
    if text.lower().startswith("macro:"):
        return parse_macro(text[6:], macros)
    text = text.lower()
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
    mods = [MODIFIER_ALIASES.get(p, p) for p in parts[:-1]]
    key = KEY_ALIASES.get(parts[-1], parts[-1])
    binding = {"type": "key", "key": key, "modifiers": mods}
    P.binding_slot(binding)
    return binding


def parse_macro(text, macros=()):
    """'NAME' or 'NAME:mode' (or an id instead of the name) -> binding."""
    ref, mode = text, "once"
    head, sep, tail = text.rpartition(":")
    if sep and tail.lower() in P.MACRO_MODES:
        ref, mode = head, tail.lower()
    macro = next((m for m in macros if m["name"] == ref or str(m["id"]) == ref), None)
    if macro is None:
        raise P.ProtocolError(f"no macro called {ref!r} (see o2ctl macro list)")
    binding = {"type": "macro", "macro": macro["id"], "mode": mode}
    P.binding_slot(binding)
    return binding


def _macro_name(binding, macros):
    macro = next((m for m in macros if m["id"] == binding.get("macro")), None)
    return macro["name"] if macro else f"#{binding.get('macro')}"


def format(binding, macros=()):
    kind = binding.get("type")
    if kind == "macro":
        mode = binding.get("mode", "once")
        return f"macro:{_macro_name(binding, macros)}" + ("" if mode == "once" else f":{mode}")
    if kind == "mouse" and binding.get("action") == "dpi-shift":
        return f"dpi-shift:{binding.get('dpi', 400)}"
    if kind == "mouse":
        return binding["action"]
    if kind == "media":
        return f"media:{binding['action']}"
    if kind == "key":
        return "key:" + "+".join(list(binding.get("modifiers", [])) + [str(binding["key"])])
    return "?"


def describe(binding, macros=()):
    kind = binding.get("type")
    if kind == "macro":
        mode = P.MACRO_MODE_LABELS.get(binding.get("mode", "once"), "?").lower()
        return f"Macro {_macro_name(binding, macros)} ({mode})"
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
