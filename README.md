# o2ctl

Settings for the **Glorious Model O 2 Wireless** on Linux, with a GTK 4 /
libadwaita window and a CLI. It was built for Omarchy but works on any
distro. It covers what Glorious CORE does for this mouse:

- DPI stages: 1–6 stages, 100–26000 in steps of 50, with an indicator colour each
  and the stage the mouse starts on
- Polling rate (125/250/500/1000 Hz), lift-off distance (1/2 mm), debounce (0–16 ms), motion sync
- Lighting: all 9 onboard effects, with brightness, speed and colours
- Button remapping: mouse functions, DPI up/down/cycle/shift, profile cycling,
  media keys, keystrokes with modifiers, macros, or disabled
- Macros: up to 16, stored on the mouse and shared by all profiles, played
  once, repeated while held, or repeated until pressed again
- The three onboard profiles
- Battery level. The dongle reports it about once a minute.
- Firmware updates, over the cable (see `docs/FIRMWARE.md`)

Connections: the 2.4 GHz dongle (`093a:822d`, tested) and the USB cable
(`093a:822a`, built from the same protocol code but not tried on hardware
yet). Bluetooth isn't supported: switch the mouse to 2.4 GHz or plug it in.

## Install

```sh
./install.sh
```

This puts `o2ctl` in `~/.local/bin`, adds a **Model O 2 Settings** entry
to the app launcher (Super + Space on Omarchy), starts the battery watcher
and adds the mouse battery to the Omarchy bar. You need system
`python-gobject` and `libadwaita`, which Omarchy already ships.

Access without root needs a udev rule. The installer prints the two sudo
commands if the rule is missing or out of date:

```sh
sudo install -m644 data/70-glorious-model-o2.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw
```

## Use

Open **Model O 2 Settings**. Changes go to the mouse about 0.3 s after you
stop adjusting, and the mouse keeps them in its onboard memory. The profile
picker in the header selects the profile you edit and makes it the active one.

```sh
o2ctl status                           # connection, battery, current profile
o2ctl dpi 400 800 1600 3200 -s 3       # stages; start on the 3rd
o2ctl dpi -c '#ff0' '#00f' '#f00' '#0f0'   # indicator colours
o2ctl polling 1000
o2ctl lod 2
o2ctl debounce 4
o2ctl motion-sync on
o2ctl lighting breathing -b 80 -s 40 -c '#ff0000' '#0000ff'
o2ctl lighting single-color -c '#00ff00'
o2ctl bind forward media:volume-up
o2ctl bind back key:ctrl+c
o2ctl bind dpi dpi-shift:400           # sniper button
o2ctl bind forward default
o2ctl macro set copy-paste ctrl+c 100ms ctrl+v
o2ctl bind forward macro:copy-paste    # :repeat or :toggle to repeat it
o2ctl macro list                       # macros and the buttons that play them
o2ctl macro delete copy-paste          # its buttons go back to default
o2ctl profile 2                        # switch the onboard profile
o2ctl -p 3 dpi 800 1600 ...            # edit a profile without switching to it
o2ctl apply --all                      # push everything saved to the mouse
o2ctl battery                          # cached level; --wait 90 for a fresh one
o2ctl gui --page lighting
o2ctl firmware                         # installed and latest versions
o2ctl firmware update                  # flash Glorious' latest; mouse on the cable
```

Run `o2ctl <command> -h` for details. `o2ctl bind -h` lists every action.

## Macros

A macro is a list of steps separated by spaces. Steps are 20 ms apart
unless you say otherwise (`o2ctl macro set -d 50 …` changes the default).

| step | does |
|------|------|
| `a`, `f5`, `enter` | press and release a key |
| `ctrl+shift+t` | press the keys in order, release them in reverse |
| `mouse:left` | click a mouse button: left, right, middle, back, forward |
| `+shift`, `-shift` | only press, or only release, a key |
| `150ms` | wait before the next step |

A macro holds up to 85 presses and releases (`ctrl+c` counts as four).
The mouse plays macros by itself, so they work without o2ctl running and on
other computers. The *Macros* tab in the window does the same as
`o2ctl macro`; pick *Macro…* for a button on the *Buttons* tab to bind one.

## Battery in the bar

The mouse can't be asked for its battery level; it sends a report on its own
every one to few minutes. After a restart the bar shows the last cached
level until the next report. The `o2ctl-watch` user service
(`o2ctl watch`) listens for those reports, caches the latest in
`~/.local/state/o2ctl/battery.json` and sends a notification at 15 % and
again at 5 %.

The installer adds this entry to the right side of the Omarchy bar in
`~/.config/omarchy/shell.json`, once. Delete it there and it stays deleted.

```json
{ "id": "o2ctl-battery", "type": "command", "exec": "~/.local/bin/o2ctl battery --bar",
  "interval": 30, "onClick": "~/.local/bin/o2ctl gui" }
```

It shows a mouse icon and the level, a bolt while charging, and highlights
at 15 % or below. The tooltip says how old the reading is. It hides when
neither the dongle nor the cable is plugged in. The mouse stops reporting
while it charges and can't be asked (Glorious CORE can't either), so while
the cable is plugged into the computer the bar shows only the bolt. On a
wall charger it does the same once the charging report is 15 minutes old.
The real level returns within a minute or two of unplugging. `o2ctl battery --bar`
prints Waybar-style JSON, so the same command works as a Waybar
`custom` module with `"return-type": "json"`.

```sh
systemctl --user status o2ctl-watch
journalctl --user -u o2ctl-watch -f
```

## How the settings are kept

The mouse can't report its settings: the dongle ignores feature-report
reads, and Glorious CORE doesn't read them either. So
`~/.config/o2ctl/config.json` is the source of truth. The first time you open
the tool it shows factory defaults, not what is on the mouse. Run
`o2ctl apply --all` (or use *Write all profiles and macros to mouse* in the
menu) to make the mouse match.

Lighting changes made with the mouse's own button combos don't show up here.
A factory reset of the mouse (hold left + right + wheel click for 5 s) wipes
its profiles and macros; `o2ctl apply --all` restores them.

## Development

```sh
/usr/bin/python3 -m unittest discover -s tests
```

`o2ctl/protocol.py` builds the packets and has no I/O. The tests check its
output against byte layouts taken from Glorious CORE, plus one packet
confirmed on a real mouse. `docs/PROTOCOL.md` describes the wire format.

Not affiliated with Glorious.
