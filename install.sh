#!/usr/bin/env bash
# Install o2ctl for the current user: command on PATH plus a launcher entry.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="$HOME/.local/bin"
apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
rule_src="$repo/data/70-glorious-model-o2.rules"
rule_dst="/etc/udev/rules.d/70-glorious-model-o2.rules"

if ! /usr/bin/python3 -c 'import gi; gi.require_version("Adw", "1")' 2>/dev/null; then
  echo "Missing GTK bindings: sudo pacman -S --needed python-gobject libadwaita" >&2
  exit 1
fi

mkdir -p "$bin_dir" "$apps_dir"
ln -sfn "$repo/bin/o2ctl" "$bin_dir/o2ctl"

cat > "$apps_dir/o2ctl.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Model O 2 Settings
GenericName=Mouse Settings
Comment=DPI, polling rate, lighting and buttons for the Glorious Model O 2 Wireless
Exec=$bin_dir/o2ctl gui
Icon=input-mouse
Terminal=false
Categories=Settings;HardwareSettings;
Keywords=glorious;mouse;dpi;rgb;model o;
StartupWMClass=dev.o2ctl.Settings
EOF
update-desktop-database "$apps_dir" 2>/dev/null || true

echo "Installed: $bin_dir/o2ctl and the \"Model O 2 Settings\" launcher entry."

# Battery watcher: caches the level the mouse reports and warns when it's low.
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
install -m644 "$repo/data/o2ctl-watch.service" "$unit_dir/o2ctl-watch.service"
systemctl --user daemon-reload
systemctl --user enable --now o2ctl-watch.service >/dev/null
systemctl --user restart o2ctl-watch.service
echo "Started the battery watcher (systemctl --user status o2ctl-watch)."

# Omarchy bar: add a battery module once, next to Bluetooth. Remove it from
# the bar layout in ~/.config/omarchy/shell.json and it stays removed.
shell_json="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/shell.json"
if [[ -f $shell_json ]]; then
  state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/o2ctl"
  mkdir -p "$state_dir"
  /usr/bin/python3 - "$shell_json" "$bin_dir/o2ctl" "$state_dir/bar-added" <<'PY'
import json, os, sys, tempfile
path, o2ctl, marker = sys.argv[1:4]
with open(path) as f:
    shell = json.load(f)
layout = shell.setdefault("bar", {}).setdefault("layout", {})
present = any(e.get("id") == "o2ctl-battery" for section in layout.values() for e in section)
if present or os.path.exists(marker):
    sys.exit(0)
entry = {"id": "o2ctl-battery", "type": "command", "exec": f"{o2ctl} battery --bar",
         "interval": 30, "onClick": f"{o2ctl} gui"}
right = layout.setdefault("right", [])
ids = [e.get("id") for e in right]
right.insert(ids.index("omarchy.bluetooth") if "omarchy.bluetooth" in ids else len(right), entry)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".shell-")
with os.fdopen(fd, "w") as f:
    json.dump(shell, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.replace(tmp, path)
open(marker, "w").close()
print("Added the mouse battery to the Omarchy bar.")
PY
fi

if ! cmp -s "$rule_src" "$rule_dst" 2>/dev/null; then
  echo
  echo "To allow access without root (needs sudo, run once):"
  echo "  sudo install -m644 '$rule_src' $rule_dst"
  echo "  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw"
fi
