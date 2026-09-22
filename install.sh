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

if ! cmp -s "$rule_src" "$rule_dst" 2>/dev/null; then
  echo
  echo "To allow access without root (needs sudo, run once):"
  echo "  sudo install -m644 '$rule_src' $rule_dst"
  echo "  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw"
fi
