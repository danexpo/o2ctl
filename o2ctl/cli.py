"""o2ctl: configure a Glorious Model O 2 Wireless from the terminal."""

import argparse
import sys
import time

from . import bindings, config
from . import device as D
from . import protocol as P


def _device_or_none(quiet=False):
    dev = D.find_device()
    if dev is None and not quiet:
        if D.bluetooth_connected():
            print("The mouse is connected over Bluetooth, which o2ctl can't configure. "
                  "Switch it to 2.4 GHz or plug in the cable.", file=sys.stderr)
        else:
            print("Model O 2 Wireless not found (no dongle or cable).", file=sys.stderr)
    return dev


def _commit(state, number, blocks, args):
    """Save, then write the changed blocks to the mouse unless --no-apply."""
    config.save(state)
    if args.no_apply:
        return 0
    dev = _device_or_none()
    if dev is None:
        print("Saved; will apply next time (o2ctl apply).", file=sys.stderr)
        return 1
    prof = config.profile(state, number)
    for block in blocks:
        getattr(dev, f"write_{block}")(number, prof)
    if number != state["active_profile"]:
        # The mouse shows whatever was written last, even for another profile.
        dev.activate_profile(state["active_profile"])
        print(f"Saved to profile {number} (active profile is {state['active_profile']}).")
    return 0


def _target(state, args):
    return args.profile or state["active_profile"]


def cmd_status(state, args):
    dev = _device_or_none(quiet=True)
    if dev:
        fw = f", firmware {dev.firmware}" if dev.firmware else ""
        print(f"Connected: {dev.label} ({dev.path}{fw})")
    elif D.bluetooth_connected():
        print("Connected over Bluetooth (not configurable; use the dongle or cable)")
    else:
        print("Not connected")
    battery = state.get("battery")
    if battery:
        age = int(time.time() - battery["time"]) // 60
        charging = ", charging" if battery["charging"] else ""
        print(f"Battery: {battery['level']}%{charging} (reported {age} min ago)")
    number = _target(state, args)
    prof = config.profile(state, number)
    perf, light = prof["performance"], prof["lighting"]
    print(f"\nProfile {number}" + (" (active)" if number == state["active_profile"] else ""))
    stages = []
    for i, s in enumerate(perf["dpi_stages"]):
        mark = "*" if i == perf["active_stage"] else ""
        stages.append(f"{s['dpi']}{mark}")
    print(f"  DPI stages:   {' '.join(stages)}   (* = selected)")
    print(f"  Polling rate: {perf['polling_rate']} Hz")
    print(f"  Lift-off:     {perf['lift_off']} mm")
    print(f"  Debounce:     {perf['debounce']} ms")
    print(f"  Motion sync:  {'on' if perf['motion_sync'] else 'off'}")
    colors = ""
    n = P.EFFECTS[light["effect"]][1]
    if n:
        colors = "  colours " + " ".join(light["colors"][:n])
    print(f"  Lighting:     {P.EFFECT_LABELS[light['effect']]}, brightness {light['brightness']}%, "
          f"speed {light['speed']}%{colors}")
    for button in P.BUTTONS:
        print(f"  {P.BUTTON_LABELS[button] + ':':29}{bindings.describe(prof['buttons'][button])}")
    return 0


def cmd_apply(state, args):
    dev = _device_or_none()
    if dev is None:
        return 1
    numbers = range(1, P.PROFILE_COUNT + 1) if args.all else [_target(state, args)]
    for number in numbers:
        dev.write_profile(number, config.profile(state, number))
        print(f"Wrote profile {number}.")
    dev.activate_profile(state["active_profile"])
    return 0


def cmd_profile(state, args):
    if args.number is None:
        print(state["active_profile"])
        return 0
    state["active_profile"] = args.number
    config.save(state)
    dev = _device_or_none()
    if dev is None:
        return 1
    dev.activate_profile(args.number)
    return 0


def cmd_dpi(state, args):
    number = _target(state, args)
    perf = config.profile(state, number)["performance"]
    if args.values:
        old = perf["dpi_stages"]
        colors = args.colors or [s["color"] for s in old]
        palette = [s["color"] for s in config.default_profile()["performance"]["dpi_stages"]]
        palette += ["#ff00ff", "#00ffff"]
        perf["dpi_stages"] = [
            {"dpi": v, "color": colors[i] if i < len(colors) else palette[i]}
            for i, v in enumerate(args.values)
        ]
        perf["active_stage"] = min(perf["active_stage"], len(args.values) - 1)
    elif args.colors:
        for stage, color in zip(perf["dpi_stages"], args.colors):
            stage["color"] = color
    if args.select is not None:
        perf["active_stage"] = args.select - 1
    P.performance_block(perf, P.DONGLE)  # validate before saving
    return _commit(state, number, ["performance"], args)


def _set_perf(key, convert=lambda v: v):
    def run(state, args):
        number = _target(state, args)
        perf = config.profile(state, number)["performance"]
        perf[key] = convert(args.value)
        P.performance_block(perf, P.DONGLE)
        return _commit(state, number, ["performance"], args)
    return run


def cmd_lighting(state, args):
    number = _target(state, args)
    light = config.profile(state, number)["lighting"]
    if args.effect:
        light["effect"] = args.effect
    if args.brightness is not None:
        light["brightness"] = args.brightness
    if args.speed is not None:
        light["speed"] = args.speed
    for i, color in enumerate(args.colors or []):
        if i < P.MAX_COLORS:
            light["colors"][i] = "#%02x%02x%02x" % P.parse_color(color)
    P.lighting_block(light, P.DONGLE)
    return _commit(state, number, ["lighting"], args)


def cmd_bind(state, args):
    number = _target(state, args)
    buttons = config.profile(state, number)["buttons"]
    if args.action == "default":
        buttons[args.button] = dict(P.DEFAULT_BINDINGS[args.button])
    else:
        buttons[args.button] = bindings.parse(args.action)
    return _commit(state, number, ["buttons"], args)


def cmd_reset(state, args):
    number = _target(state, args)
    state["profiles"][number - 1] = config.default_profile()
    return _commit(state, number, ["performance", "lighting", "buttons"], args)


def cmd_battery(state, args):
    if args.wait:
        dev = _device_or_none()
        if dev is None:
            return 1
        for event in dev.events(timeout=args.wait):
            if event["event"] == "battery":
                record_battery(state, event)
                config.save(state)
                break
        else:
            print("No battery report in that time. The mouse sends one on its own "
                  "schedule; try a longer --wait.", file=sys.stderr)
    battery = state.get("battery")
    if not battery:
        print("No battery reading yet (try: o2ctl battery --wait 600)")
        return 1
    print(f"{battery['level']}%{' charging' if battery['charging'] else ''}")
    return 0


def record_battery(state, event):
    state["battery"] = {"level": event["level"], "charging": event["charging"], "time": time.time()}


def cmd_listen(state, args):
    dev = _device_or_none()
    if dev is None:
        return 1
    print("Listening for status reports (Ctrl+C to stop)...")
    try:
        for event in dev.events(timeout=args.seconds):
            print(time.strftime("%H:%M:%S"), event, flush=True)
            if event["event"] == "battery":
                record_battery(state, event)
                config.save(state)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_gui(state, args):
    from . import gui
    return gui.main(args.page)


def on_off(text):
    if text.lower() in ("on", "1", "true", "yes"):
        return True
    if text.lower() in ("off", "0", "false", "no"):
        return False
    raise argparse.ArgumentTypeError("use on or off")


def build_parser():
    p = argparse.ArgumentParser(prog="o2ctl", description=__doc__)
    # -p/-n work before or after the command; the global copies use their own dests
    # so a subcommand's default can't overwrite them.
    p.add_argument("-p", "--profile", dest="global_profile", type=int,
                   choices=range(1, P.PROFILE_COUNT + 1), help=argparse.SUPPRESS)
    p.add_argument("-n", "--no-apply", dest="global_no_apply", action="store_true", help=argparse.SUPPRESS)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-p", "--profile", type=int, choices=range(1, P.PROFILE_COUNT + 1),
                        help="profile to change (default: the active one)")
    common.add_argument("-n", "--no-apply", action="store_true",
                        help="only save the setting; don't write it to the mouse")
    sub = p.add_subparsers(dest="command", metavar="command")

    s = sub.add_parser("status", parents=[common], help="show connection and saved settings")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("apply", parents=[common], help="write saved settings to the mouse")
    s.add_argument("--all", action="store_true", help="write all three profiles")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("profile", help="show or switch the active onboard profile")
    s.add_argument("number", nargs="?", type=int, choices=range(1, P.PROFILE_COUNT + 1))
    s.set_defaults(func=cmd_profile)

    s = sub.add_parser("dpi", parents=[common], help="set DPI stages, e.g. dpi 400 800 1600")
    s.add_argument("values", nargs="*", type=int, help=f"1-{P.MAX_DPI_STAGES} values, {P.DPI_MIN}-{P.DPI_MAX}")
    s.add_argument("-s", "--select", type=int, help="stage the mouse starts on (1-based)")
    s.add_argument("-c", "--colors", nargs="+", metavar="HEX", help="indicator colour per stage")
    s.set_defaults(func=cmd_dpi)

    s = sub.add_parser("polling", parents=[common], help="polling rate in Hz")
    s.add_argument("value", type=int, choices=sorted(P.POLLING_RATES))
    s.set_defaults(func=_set_perf("polling_rate"))

    s = sub.add_parser("lod", parents=[common], help="lift-off distance in mm")
    s.add_argument("value", type=int, choices=P.LIFT_OFF)
    s.set_defaults(func=_set_perf("lift_off"))

    s = sub.add_parser("debounce", parents=[common], help=f"switch debounce in ms (0-{P.DEBOUNCE_MAX})")
    s.add_argument("value", type=int)
    s.set_defaults(func=_set_perf("debounce"))

    s = sub.add_parser("motion-sync", parents=[common], help="motion sync on/off")
    s.add_argument("value", type=on_off)
    s.set_defaults(func=_set_perf("motion_sync"))

    s = sub.add_parser("lighting", parents=[common], help="RGB effect, brightness, speed, colours")
    s.add_argument("effect", nargs="?", choices=list(P.EFFECTS))
    s.add_argument("-b", "--brightness", type=int, metavar="0-100")
    s.add_argument("-s", "--speed", type=int, metavar="0-100")
    s.add_argument("-c", "--colors", nargs="+", metavar="HEX")
    s.set_defaults(func=cmd_lighting)

    s = sub.add_parser("bind", parents=[common], help="remap a button",
                       description="Actions: " + ", ".join(P.MOUSE_ACTIONS) +
                       ", dpi-shift:DPI, media:" + "|".join(P.MEDIA_ACTIONS) +
                       ", key:COMBO (e.g. key:ctrl+c), default")
    s.add_argument("button", choices=P.BUTTONS)
    s.add_argument("action")
    s.set_defaults(func=cmd_bind)

    s = sub.add_parser("reset", parents=[common], help="restore a profile to factory settings")
    s.set_defaults(func=cmd_reset)

    s = sub.add_parser("battery", help="last reported battery level")
    s.add_argument("-w", "--wait", type=float, metavar="SECONDS", help="wait for a fresh report")
    s.set_defaults(func=cmd_battery)

    s = sub.add_parser("listen", help="print status reports from the mouse")
    s.add_argument("seconds", nargs="?", type=float)
    s.set_defaults(func=cmd_listen)

    s = sub.add_parser("gui", help="open the settings window")
    s.add_argument("--page", choices=["performance", "lighting", "buttons"], help="tab to open on")
    s.set_defaults(func=cmd_gui)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args((argv or sys.argv[1:]) + ["status"])
    args.profile = getattr(args, "profile", None) or args.global_profile
    args.no_apply = getattr(args, "no_apply", False) or args.global_no_apply
    state = config.load()
    try:
        return args.func(state, args)
    except (P.ProtocolError, D.DeviceError, ValueError) as e:
        print(f"o2ctl: {e}", file=sys.stderr)
        return 1
