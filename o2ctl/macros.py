"""Macros: their text form and where they live in the saved settings.

A macro is written as a sequence of tokens separated by spaces:

    ctrl+c              press ctrl, press c, release c, release ctrl
    a                   press and release a
    mouse:left          click a mouse button (left, right, middle, back, forward)
    +shift  -shift      only press, or only release, a key
    150ms               wait 150 ms before the next step (instead of the default)

so "ctrl+a ctrl+c 100ms alt+tab" selects all, copies, waits and switches
window. Steps are stored as {"key", "down", "delay"}, where delay is the
wait in ms after that step.

Macros are kept in state["macros"] as {"id", "name", "steps"}. They are not
part of a profile: every profile can bind a button to any macro by its id.
"""

import re

from . import bindings
from . import protocol as P

DEFAULT_DELAY = 20  # ms between steps when the text doesn't say otherwise

_PAUSE = re.compile(r"^(\d+)ms$")


def _key_name(text):
    name = text.lower()
    if name.startswith("mouse:"):
        button = name[6:]
        if button not in P.MACRO_BUTTONS:
            raise P.ProtocolError(f"unknown mouse button in macro: {button} "
                                  f"(use {', '.join(P.MACRO_BUTTONS)})")
        return name
    name = bindings.MODIFIER_ALIASES.get(name, name)
    name = bindings.KEY_ALIASES.get(name, name)
    if name not in P.MACRO_KEYS:
        raise P.ProtocolError(f"unknown key in macro: {text}")
    return name


def parse(text, delay=DEFAULT_DELAY):
    """Macro text -> list of steps. Raises ProtocolError on bad input."""
    steps = []
    held = []
    for token in text.replace(",", " ").split():
        pause = _PAUSE.match(token.lower())
        if pause:
            if not steps:
                raise P.ProtocolError("a macro can't start with a pause")
            ms = int(pause.group(1))
            if ms == 0:
                raise P.ProtocolError("pauses must be at least 1 ms")
            # A pause replaces the default wait; two in a row add up.
            last = steps[-1]
            last["delay"] = ms if not last.pop("_paused", False) else last["delay"] + ms
            last["_paused"] = True
            continue
        if token[0] in "+-" and len(token) > 1:
            key = _key_name(token[1:])
            down = token[0] == "+"
            if down and key not in held:
                held.append(key)
            elif not down and key in held:
                held.remove(key)
            steps.append({"key": key, "down": down, "delay": delay})
            continue
        parts = token.split("+")
        if not all(parts):
            raise P.ProtocolError(f"bad macro step: {token}")
        keys = [_key_name(k) for k in parts]
        steps += [{"key": k, "down": True, "delay": delay} for k in keys]
        steps += [{"key": k, "down": False, "delay": delay} for k in reversed(keys)]
    for step in steps:
        step.pop("_paused", None)
    if held:
        raise P.ProtocolError(f"{', '.join(held)} is pressed but never released (add -{held[0]})")
    P.macro_block(steps)  # validate length and delays
    return steps


def format(steps, delay=DEFAULT_DELAY):
    """Steps -> text that parse() turns back into the same steps."""
    tokens = []
    i = 0
    while i < len(steps):
        n = _combo_length(steps, i, delay)
        if n:
            keys = [s["key"] for s in steps[i : i + n]]
            tokens.append("+".join(keys))
            i += 2 * n
        else:
            step = steps[i]
            tokens.append(("+" if step["down"] else "-") + step["key"])
            i += 1
        last = steps[i - 1]["delay"]
        if last != delay:
            tokens.append(f"{last}ms")
    return " ".join(tokens)


def _combo_length(steps, i, delay):
    """How many keys a press-all/release-all run starting at i has, or 0."""
    best = 0
    for n in range(1, (len(steps) - i) // 2 + 1):
        run = steps[i : i + 2 * n]
        downs, ups = run[:n], run[n:]
        if not all(s["down"] for s in downs):
            break
        if any(s["down"] for s in ups):
            continue
        keys = [s["key"] for s in downs]
        if len(set(keys)) != n or [s["key"] for s in ups] != keys[::-1]:
            continue
        # Every wait inside the run must be the default; the last one may be anything.
        if all(s["delay"] == delay for s in run[:-1]):
            best = n
    return best


def describe(steps):
    """Short summary for lists: the text form, cut down if long."""
    text = format(steps)
    return text if len(text) <= 60 else text[:57] + "…"


# ----- saved macros -----------------------------------------------------------


def all_macros(state):
    return state.setdefault("macros", [])


def find(state, ref):
    """A macro by name or id (int or digit string), or None."""
    for macro in all_macros(state):
        if macro["name"] == ref or str(macro["id"]) == str(ref):
            return macro
    return None


def by_id(state, macro_id):
    return next((m for m in all_macros(state) if m["id"] == macro_id), None)


def check_name(name):
    name = name.strip()
    if not name:
        raise P.ProtocolError("a macro needs a name")
    if name.isdigit():
        raise P.ProtocolError("a macro name can't be just a number (numbers are macro ids)")
    if ":" in name:
        raise P.ProtocolError("a macro name can't contain ':'")
    return name


def put(state, name, steps):
    """Create macro `name`, or replace its steps. Returns the macro."""
    name = check_name(name)
    P.macro_block(steps)
    macro = find(state, name)
    if macro is None:
        used = {m["id"] for m in all_macros(state)}
        free = [n for n in range(1, P.MAX_MACROS + 1) if n not in used]
        if not free:
            raise P.ProtocolError(f"the mouse holds at most {P.MAX_MACROS} macros; delete one first")
        macro = {"id": free[0], "name": name, "steps": steps}
        all_macros(state).append(macro)
        all_macros(state).sort(key=lambda m: m["id"])
    else:
        macro["steps"] = steps
    return macro


def users(state, macro_id):
    """(profile number, button) pairs bound to the macro."""
    found = []
    for number, prof in enumerate(state["profiles"], 1):
        for button in P.BUTTONS:
            b = prof["buttons"][button]
            if b.get("type") == "macro" and b.get("macro") == macro_id:
                found.append((number, button))
    return found


def delete(state, macro):
    """Remove a macro and put any button bound to it back to its default.

    Returns the profile numbers whose buttons changed.
    """
    all_macros(state).remove(macro)
    changed = []
    for number, button in users(state, macro["id"]):
        state["profiles"][number - 1]["buttons"][button] = dict(P.DEFAULT_BINDINGS[button])
        if number not in changed:
            changed.append(number)
    return changed


def used_by_profile(state, prof):
    """Macros that a profile's buttons play, in id order."""
    ids = sorted({b["macro"] for b in prof["buttons"].values() if b.get("type") == "macro"})
    return [m for m in (by_id(state, i) for i in ids) if m]
