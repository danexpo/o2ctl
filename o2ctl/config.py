"""Saved settings for the mouse.

The mouse cannot report its settings back (the dongle ignores feature-report
reads), so this file is the source of truth, the same way Glorious CORE keeps
its own database. Every change is saved here and then written to the mouse.
"""

import copy
import json
import os
import tempfile

from . import protocol as P

DEFAULT_PALETTE = ["#fff60a", "#0aff7d", "#ff600a", "#0affe5", "#ff0ad4", "#ff0000"]


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "o2ctl")


def config_path():
    return os.path.join(config_dir(), "config.json")


def default_profile():
    return {
        "performance": {
            "polling_rate": 1000,
            "lift_off": 1,
            "debounce": 2,
            "motion_sync": False,
            "active_stage": 2,
            "dpi_stages": [
                {"dpi": 400, "color": "#ffff00"},
                {"dpi": 800, "color": "#0000ff"},
                {"dpi": 1600, "color": "#ff0000"},
                {"dpi": 3200, "color": "#00ff00"},
            ],
        },
        "lighting": {
            "effect": "glorious",
            "speed": 60,
            "brightness": 60,
            "colors": list(DEFAULT_PALETTE),
        },
        "buttons": copy.deepcopy(P.DEFAULT_BINDINGS),
    }


def default_state():
    return {
        "version": 1,
        "active_profile": 1,
        "profiles": [default_profile() for _ in range(P.PROFILE_COUNT)],
        "battery": None,
    }


def _merge(defaults, saved):
    """Fill anything missing from `saved` with `defaults`, recursively."""
    if not isinstance(saved, dict) or not isinstance(defaults, dict):
        return copy.deepcopy(saved) if saved is not None else copy.deepcopy(defaults)
    out = copy.deepcopy(defaults)
    for key, value in saved.items():
        out[key] = _merge(defaults[key], value) if key in defaults else copy.deepcopy(value)
    return out


def normalise(state):
    state = _merge(default_state(), state)
    profiles = list(state.get("profiles") or [])[: P.PROFILE_COUNT]
    while len(profiles) < P.PROFILE_COUNT:
        profiles.append(default_profile())
    state["profiles"] = [_merge(default_profile(), p) for p in profiles]
    for profile in state["profiles"]:
        colors = profile["lighting"]["colors"]
        profile["lighting"]["colors"] = (list(colors) + DEFAULT_PALETTE)[: P.MAX_COLORS]
        # Buttons are a mapping; keep only ones the mouse has, fill the rest.
        profile["buttons"] = {
            b: profile["buttons"].get(b, P.DEFAULT_BINDINGS[b]) for b in P.BUTTONS
        }
        perf = profile["performance"]
        if not perf["dpi_stages"]:
            perf["dpi_stages"] = default_profile()["performance"]["dpi_stages"]
        perf["active_stage"] = min(max(0, perf["active_stage"]), len(perf["dpi_stages"]) - 1)
    if state["active_profile"] not in range(1, P.PROFILE_COUNT + 1):
        state["active_profile"] = 1
    return state


def load(path=None):
    path = path or config_path()
    try:
        with open(path) as f:
            return normalise(json.load(f))
    except FileNotFoundError:
        return default_state()


def save(state, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def profile(state, number):
    if not 1 <= number <= P.PROFILE_COUNT:
        raise ValueError(f"profile must be 1-{P.PROFILE_COUNT}")
    return state["profiles"][number - 1]
