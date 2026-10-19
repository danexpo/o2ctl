"""Battery level: the cached last report, the watcher that keeps it fresh,
and the status-bar output.

Through the dongle the mouse sends a report on its own every minute or few,
so `o2ctl watch` listens for those. On the cable it sends none, but answers
when asked, so the watcher asks once a minute; the same over Bluetooth. Either way it caches the
latest reading; everything else (bar, `o2ctl battery`, the GUI) reads the
cache. The watcher also runs the commands bound to buttons (shortcuts.py).
"""

import json
import os
import shutil
import subprocess
import tempfile
import time

from . import device as D
from . import protocol as P
from . import shortcuts

LOW = (15, 5)             # notify when the level drops to each of these
STALE = 15 * 60           # a reading this old means the mouse is asleep or off
POLL = 60                 # seconds between questions over the cable, as CORE 1
SWITCH_CHECK = 5          # seconds between looks for a better link while on the dongle
# The mouse stops reporting while it charges, so an old "charging" reading's
# level is long out of date; show that it's charging, not the number.
ICON = "\U000f037d"       # nerd font "mouse"
CHARGING = "\U000f140b"   # nerd font "lightning bolt"


def status_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "o2ctl", "battery.json")


def load(path=None):
    try:
        with open(path or status_path()) as f:
            reading = json.load(f)
        return {"level": int(reading["level"]), "charging": bool(reading["charging"]),
                "time": float(reading["time"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def record(event, path=None, now=None):
    reading = {"level": event["level"], "charging": event["charging"],
               "time": time.time() if now is None else now}
    path = path or status_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".battery-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(reading, f)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return reading


def describe(reading, now=None, wired=False):
    """'47%, charging · 12 min ago' style summary."""
    now = time.time() if now is None else now
    age = int(now - reading["time"])
    if wired and age >= STALE:
        return f"charging on the cable (last report {reading['level']}%, {_ago(age)})"
    if reading["charging"] and age >= STALE:
        return f"charging (was {reading['level']}% {_ago(age)}; no reports until unplugged)"
    text = f"{reading['level']}%" + (", charging" if reading["charging"] else "")
    if age >= STALE:
        text += f" · {_ago(age)}"
    return text


def _ago(seconds):
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def bar_output(reading, connected, now=None, wired=False):
    """Waybar-style JSON for the Omarchy bar's command module.

    Empty text hides the module, which is what happens when the mouse isn't
    connected at all. On the cable the watcher asks for the level; until it
    has a fresh answer, show only the bolt.
    """
    if not connected:
        return {"text": "", "tooltip": ""}
    now = time.time() if now is None else now
    if wired and (reading is None or now - reading["time"] >= STALE):
        tip = f"Model O 2: {describe(reading, now, wired) if reading else 'charging on the cable'}"
        return {"text": f"{ICON} {CHARGING}", "tooltip": tip, "class": ["charging"]}
    if reading is None:
        return {"text": ICON, "tooltip": "Model O 2: waiting for the first battery report (can take a few minutes)",
                "class": "unknown"}
    level = reading["level"]
    stale = now - reading["time"] >= STALE
    if reading["charging"] and stale:
        return {"text": f"{ICON} {CHARGING}", "tooltip": f"Model O 2: {describe(reading, now)}",
                "class": ["charging"]}
    text = f"{ICON} {level}%" + (f" {CHARGING}" if reading["charging"] else "")
    low = level <= LOW[0] and not reading["charging"]
    klass = ["low", "active"] if low else []
    if stale:
        klass.append("stale")
    return {"text": text, "tooltip": f"Model O 2: {describe(reading, now)}",
            "class": klass, "percentage": level}


class LowBatteryAlert:
    """Decides when to warn, once per threshold per discharge."""

    def __init__(self, thresholds=LOW):
        self.thresholds = sorted(thresholds, reverse=True)
        self.warned = set()

    def check(self, level, charging):
        if charging:
            self.warned.clear()
            return None
        # Re-arm thresholds the level has clearly climbed back above.
        self.warned = {t for t in self.warned if level <= t + 5}
        due = [t for t in self.thresholds if level <= t and t not in self.warned]
        if not due:
            return None
        self.warned.update(due)
        return min(due)


def notify(level, threshold):
    if not shutil.which("notify-send"):
        return
    urgency = "critical" if threshold <= min(LOW) else "normal"
    subprocess.run(["notify-send", "-a", "o2ctl", "-u", urgency, "-i", "battery-low-symbolic",
                    "Mouse battery low", f"Model O 2 is at {level}%. Plug in the cable to charge."],
                   check=False)


def _handle(reading, alert, log):
    record(reading)
    log(f"Battery {reading['level']}%{' charging' if reading['charging'] else ''}.")
    threshold = alert.check(reading["level"], reading["charging"])
    if threshold is not None:
        notify(reading["level"], threshold)


def _better_link(dev, every=SWITCH_CHECK, clock=time.monotonic, find=None):
    """A stop() for dev.events(): true once another link should be used."""
    if dev.transport != P.DONGLE:
        return None
    find = find or D.find_device
    last = [clock()]

    def stop():
        if clock() - last[0] < every:
            return False
        last[0] = clock()
        best = find()
        return best is not None and best.path != dev.path
    return stop


def watch(log=print):
    """Cache every battery report and run button commands until killed.
    Survives unplugging."""
    alert = LowBatteryAlert()
    reading = load()
    if reading:
        alert.check(reading["level"], reading["charging"])  # don't re-warn on restart
    announced = None
    while True:
        dev = D.find_device()
        if dev is None:
            if announced is not None:
                log("Mouse disconnected; waiting for it.")
                announced = None
            time.sleep(5)
            continue
        if not os.access(dev.path, os.R_OK):
            if announced != "denied":
                log(f"No permission to read {dev.path}; install the udev rule (see README).")
                announced = "denied"
            time.sleep(30)
            continue
        if announced != dev.path:
            log(f"Listening on {dev.label} ({dev.path}).")
            announced = dev.path
        try:
            if dev.transport in P.BATTERY_QUERY_LINKS:
                reading = dev.query_battery()
                if reading:
                    _handle(reading, alert, log)
            # Come back every POLL seconds: to ask again, or to switch to a
            # better link (the cable) once it's there. The dongle is always
            # present, so while on it look sooner: the mouse may be back on
            # Bluetooth after dozing off.
            for event in dev.events(timeout=POLL, stop=_better_link(dev)):
                if event["event"] == "battery":
                    _handle(event, alert, log)
                else:
                    shortcuts.handle(event, log)
        except D.DeviceError as e:
            log(str(e))
            time.sleep(2)

