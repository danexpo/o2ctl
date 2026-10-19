"""o2ctl: configure a Glorious Model O 2 Wireless from the terminal."""

import argparse
import json
import sys
import time

from . import battery as B
from . import bindings, config
from . import macros as M
from . import device as D
from . import firmware as F
from . import protocol as P


def _device_or_none(quiet=False):
    dev = D.find_device()
    if dev is None and not quiet:
        print("Model O 2 Wireless not found (no dongle, cable or Bluetooth).", file=sys.stderr)
    return dev


def _commit(state, number, blocks, args, macros=()):
    """Save, then write the changed blocks (and macros) to the mouse unless --no-apply."""
    config.save(state)
    if args.no_apply:
        return 0
    dev = _device_or_none()
    if dev is None:
        print("Saved; will apply next time (o2ctl apply).", file=sys.stderr)
        return 1
    for macro in macros:
        dev.write_macro(macro)
    if not blocks:
        return 0
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
    else:
        print("Not connected")
    reading = B.load()
    if reading:
        print(f"Battery: {B.describe(reading, wired=bool(dev) and dev.transport == P.WIRED)}")
    print(f"Lights off when idle: {_sleep_text(state['light_sleep'])}")
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
    battery = ""
    if light["battery_brightness"] is not None:
        battery = f" ({light['battery_brightness']}% on battery)"
    print(f"  Lighting:     {P.EFFECT_LABELS[light['effect']]}, brightness {light['brightness']}%{battery}, "
          f"speed {light['speed']}%{colors}")
    for button in P.BUTTONS:
        print(f"  {P.BUTTON_LABELS[button] + ':':29}"
              f"{bindings.describe(prof['buttons'][button], state['macros'])}")
    if state["macros"]:
        print(f"\n{len(state['macros'])} saved macro(s): o2ctl macro list")
    return 0


def cmd_apply(state, args):
    dev = _device_or_none()
    if dev is None:
        return 1
    numbers = range(1, P.PROFILE_COUNT + 1) if args.all else [_target(state, args)]
    if args.all:
        wanted = state["macros"]
    else:
        wanted = M.used_by_profile(state, config.profile(state, numbers[0]))
    for macro in wanted:
        dev.write_macro(macro)
        print(f"Wrote macro {macro['name']}.")
    for number in numbers:
        dev.write_profile(number, config.profile(state, number))
        print(f"Wrote profile {number}.")
    dev.write_sleep(state["light_sleep"])
    dev.activate_profile(state["active_profile"])
    return 0


def _sleep_text(minutes):
    return "never" if minutes is None else f"after {minutes} min"


def cmd_sleep(state, args):
    if args.minutes is None:
        print(_sleep_text(state["light_sleep"]))
        return 0
    minutes = None if args.minutes.lower() in ("off", "never", "0") else int(args.minutes)
    P.sleep_packet(minutes, P.DONGLE)  # validate before saving
    state["light_sleep"] = minutes
    config.save(state)
    if args.no_apply:
        return 0
    dev = _device_or_none()
    if dev is None:
        print("Saved; will apply next time (o2ctl apply).", file=sys.stderr)
        return 1
    dev.write_sleep(minutes)
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
    if args.battery_brightness is not None:
        light["battery_brightness"] = None if args.battery_brightness == "same" else args.battery_brightness
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
        buttons[args.button] = bindings.parse(args.action, state["macros"])
    binding = buttons[args.button]
    # Store the macro too, in case it was saved with --no-apply or the mouse was reset.
    wanted = [M.by_id(state, binding["macro"])] if binding["type"] == "macro" else []
    return _commit(state, number, ["buttons"], args, wanted)


def _macro_or_error(state, ref):
    macro = M.find(state, ref)
    if macro is None:
        raise P.ProtocolError(f"no macro called {ref!r} (see o2ctl macro list)")
    return macro


def _uses(state, macro):
    used = M.users(state, macro["id"])
    return ", ".join(f"profile {n} {P.BUTTON_LABELS[b].split(' (')[0].lower()}" for n, b in used)


def cmd_macro_list(state, args):
    if not state["macros"]:
        print("No macros yet. Make one with, for example:\n"
              "  o2ctl macro set copy-paste ctrl+c 100ms ctrl+v\n"
              "  o2ctl bind forward macro:copy-paste")
        return 0
    for macro in state["macros"]:
        steps = len(macro["steps"])
        print(f"{macro['id']:>2}  {macro['name']}  ({steps} step{'s' if steps != 1 else ''})")
        print(f"    {M.describe(macro['steps'])}")
        uses = _uses(state, macro)
        print(f"    on: {uses}" if uses else "    not bound to a button")
    return 0


def cmd_macro_show(state, args):
    macro = _macro_or_error(state, args.name)
    print(M.format(macro["steps"]))
    return 0


def cmd_macro_set(state, args):
    steps = M.parse(" ".join(args.sequence), args.delay)
    new = M.find(state, args.name) is None
    macro = M.put(state, args.name, steps)
    result = _commit(state, None, [], args, [macro])
    print(f"{'Saved' if new else 'Updated'} macro {macro['name']} ({len(steps)} steps).")
    if not M.users(state, macro["id"]):
        print(f"Bind it to a button with: o2ctl bind forward macro:{macro['name']}")
    return result


def cmd_macro_delete(state, args):
    macro = _macro_or_error(state, args.name)
    uses = _uses(state, macro)
    changed = M.delete(state, macro)
    config.save(state)
    print(f"Deleted macro {macro['name']}." + (f" Reset to default: {uses}." if uses else ""))
    if not changed or args.no_apply:
        return 0
    dev = _device_or_none()
    if dev is None:
        print("Saved; will apply next time (o2ctl apply --all).", file=sys.stderr)
        return 1
    for number in changed:
        dev.write_buttons(number, config.profile(state, number))
    if any(n != state["active_profile"] for n in changed):
        dev.activate_profile(state["active_profile"])
    return 0


def cmd_reset(state, args):
    number = _target(state, args)
    state["profiles"][number - 1] = config.default_profile()
    return _commit(state, number, ["performance", "lighting", "buttons"], args)


def cmd_battery(state, args):
    if args.bar:
        devices = D.find_devices()
        out = B.bar_output(B.load(), bool(devices), wired=any(d.transport == P.WIRED for d in devices))
        if out["text"]:  # print nothing to hide: the Omarchy bar shows raw output for empty "text"
            print(json.dumps(out, ensure_ascii=False))
        return 0
    dev = D.find_device()
    if dev is not None and dev.transport in P.BATTERY_QUERY_LINKS:
        reading = dev.query_battery()
        if reading:
            B.record(reading)
    elif args.wait:
        dev = _device_or_none()
        if dev is None:
            return 1
        for event in dev.events(timeout=args.wait):
            if event["event"] == "battery":
                B.record(event)
                break
        else:
            print("No battery report in that time. The mouse sends one on its own "
                  "schedule; try a longer --wait.", file=sys.stderr)
    reading = B.load()
    if not reading:
        print("No battery reading yet (run o2ctl watch, or: o2ctl battery --wait 90)")
        return 1
    print(B.describe(reading, wired=dev is not None and dev.transport == P.WIRED))
    return 0


def cmd_watch(state, args):
    try:
        B.watch(log=lambda msg: print(msg, flush=True))
    except KeyboardInterrupt:
        pass
    return 0


def cmd_listen(state, args):
    dev = _device_or_none()
    if dev is None:
        return 1
    print("Listening for status reports (Ctrl+C to stop)...")
    try:
        for event in dev.events(timeout=args.seconds):
            print(time.strftime("%H:%M:%S"), event, flush=True)
            if event["event"] == "battery":
                B.record(event)
    except KeyboardInterrupt:
        pass
    return 0


def _firmware_target(args):
    """The device to update: the mouse over the cable, or the dongle."""
    devices = D.find_devices()
    want = P.DONGLE if args.dongle else P.WIRED
    for dev in devices:
        if dev.transport == want:
            return dev
    if want == P.WIRED:
        raise D.DeviceError("Plug the mouse in with the USB cable; it can only be updated over the cable. "
                            "(Use --dongle to update the dongle.)")
    raise D.DeviceError("The dongle is not plugged in.")


def _part(dev):
    return "dongle" if dev.transport == P.DONGLE else "mouse"


def cmd_firmware(state, args):
    devices = D.find_devices()
    if not devices:
        print("Model O 2 Wireless not found (no dongle or cable).", file=sys.stderr)
        return 1
    for dev in devices:
        print(f"{_part(dev).capitalize()}: {dev.firmware or 'unknown'} ({dev.label})")
    if not any(d.transport == P.WIRED for d in devices):
        print("(The mouse's own version shows when it is plugged in with the cable.)")
    print(f"Latest from Glorious: {F.LATEST}")
    return 0


def cmd_firmware_check(state, args):
    dev = _firmware_target(args)
    with dev.raw() as hid:
        ota, version = F.Flasher(hid).check()
    print(f"{_part(dev).capitalize()} answered: version bytes {version.hex(' ')} ({version!r})")
    print("Update parameters: " + ", ".join(f"{k} {v}" for k, v in ota.items()))
    return 0


def cmd_firmware_update(state, args):
    dev = _firmware_target(args)
    if args.file:
        image = F.load_image(args.file, dev.transport)
    else:
        print("Downloading the firmware from Glorious...")
        image = F.official_image(dev.transport)
    known = F.image_info(image)
    if known is None and not args.force:
        raise F.FirmwareError(f"{args.file} is not a firmware image o2ctl knows; use --force to flash it anyway")
    if known and known[0] != dev.transport:
        raise F.FirmwareError(f"that image is for the {'dongle' if known[0] == P.DONGLE else 'mouse'}, "
                              f"not the {_part(dev)}")
    version = known[1] if known else "unknown version"
    print(f"Flashing {version} ({len(image)} bytes) onto the {_part(dev)}, now on {dev.firmware or 'unknown'}.")
    print(f"If this fails partway the {_part(dev)} may stop working, with no known way to recover it.")
    if not args.yes:
        if input("Type 'flash' to continue: ").strip() != "flash":
            print("Cancelled.")
            return 1
    print("Don't unplug it or press its buttons until this finishes.")
    try:
        F.flash(dev, image, progress=lambda pct: print(f"\r  {pct:3d}%", end="", flush=True))
    finally:
        print()
    print(f"Done. The {_part(dev)} is restarting.")
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


def percent_or_same(text):
    if text.lower() == "same":
        return "same"
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("use 0-100 or same") from None


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
    s.add_argument("--battery-brightness", type=percent_or_same, metavar="0-100|same",
                   help="brightness while on battery; 'same' follows --brightness")
    s.add_argument("-s", "--speed", type=int, metavar="0-100")
    s.add_argument("-c", "--colors", nargs="+", metavar="HEX")
    s.set_defaults(func=cmd_lighting)

    s = sub.add_parser("sleep", parents=[common], help="turn all lights off after idle minutes",
                       description=f"Turn every LED off after 1-{P.SLEEP_MAX} minutes without use, or "
                                   "'off' to keep them on. Moving the mouse turns them back on.")
    s.add_argument("minutes", nargs="?", metavar=f"1-{P.SLEEP_MAX}|off")
    s.set_defaults(func=cmd_sleep)

    s = sub.add_parser("bind", parents=[common], help="remap a button",
                       description="Actions: " + ", ".join(P.MOUSE_ACTIONS) +
                       ", dpi-shift:DPI, media:" + "|".join(P.MEDIA_ACTIONS) +
                       ", key:COMBO (e.g. key:ctrl+c), macro:NAME[:once|repeat|toggle], "
                       "command:CMD (run by o2ctl watch on this computer), default")
    s.add_argument("button", choices=P.BUTTONS)
    s.add_argument("action")
    s.set_defaults(func=cmd_bind)

    s = sub.add_parser("macro", help="make, list and delete macros",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       description="\n\n".join(M.__doc__.split("\n\n")[1:3]) +
                       "\n\nBind a macro with: o2ctl bind BUTTON macro:NAME[:repeat|toggle]")
    s.set_defaults(func=cmd_macro_list)
    msub = s.add_subparsers(dest="macro_command", metavar="action")
    m = msub.add_parser("list", help="saved macros and the buttons that play them")
    m.set_defaults(func=cmd_macro_list)
    m = msub.add_parser("show", help="print a macro's steps")
    m.add_argument("name")
    m.set_defaults(func=cmd_macro_show)
    m = msub.add_parser("set", parents=[common], help="create or replace a macro",
                        description="Steps: a, ctrl+c, mouse:left, +shift / -shift (press / release only), "
                                    "150ms (wait). Example: o2ctl macro set paste-enter ctrl+v 50ms enter")
    m.add_argument("name")
    m.add_argument("sequence", nargs="+", help="steps, e.g. ctrl+c 100ms ctrl+v")
    m.add_argument("-d", "--delay", type=int, default=M.DEFAULT_DELAY, metavar="MS",
                   help=f"wait between steps (default {M.DEFAULT_DELAY} ms)")
    m.set_defaults(func=cmd_macro_set)
    m = msub.add_parser("delete", parents=[common], help="delete a macro; its buttons go back to default")
    m.add_argument("name")
    m.set_defaults(func=cmd_macro_delete)

    s = sub.add_parser("reset", parents=[common], help="restore a profile to factory settings")
    s.set_defaults(func=cmd_reset)

    s = sub.add_parser("battery", help="last reported battery level")
    s.add_argument("-w", "--wait", type=float, metavar="SECONDS", help="wait for a fresh report")
    s.add_argument("--bar", action="store_true", help="print JSON for the Omarchy (or Waybar) status bar")
    s.set_defaults(func=cmd_battery)

    s = sub.add_parser("watch", help="keep the battery level cached and warn when it runs low")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("listen", help="print status reports from the mouse")
    s.add_argument("seconds", nargs="?", type=float)
    s.set_defaults(func=cmd_listen)

    s = sub.add_parser("firmware", help="show, check or update the firmware")
    s.set_defaults(func=cmd_firmware)
    fsub = s.add_subparsers(dest="firmware_command", metavar="action")
    target = argparse.ArgumentParser(add_help=False)
    target.add_argument("--dongle", action="store_true", help="the dongle instead of the mouse (cable)")
    f = fsub.add_parser("check", parents=[target], help="read the version and update parameters; sends no firmware")
    f.set_defaults(func=cmd_firmware_check)
    f = fsub.add_parser("update", parents=[target], help="flash firmware (Glorious' latest unless FILE is given)")
    f.add_argument("file", nargs="?", help="Glorious' firmware .zip or a .bin")
    f.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    f.add_argument("--force", action="store_true", help="flash an image o2ctl doesn't recognise")
    f.set_defaults(func=cmd_firmware_update)

    s = sub.add_parser("gui", help="open the settings window")
    s.add_argument("--page", choices=["performance", "lighting", "buttons", "macros"], help="tab to open on")
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
    except (P.ProtocolError, D.DeviceError, F.FirmwareError, ValueError, OSError) as e:
        print(f"o2ctl: {e}", file=sys.stderr)
        return 1
