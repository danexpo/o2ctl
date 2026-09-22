"""Settings window (GTK 4 + libadwaita)."""

import copy
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__, bindings, config  # noqa: E402
from . import device as D  # noqa: E402
from . import protocol as P  # noqa: E402

APP_ID = "dev.o2ctl.Settings"
WRITE_DELAY = 0.3  # seconds of quiet before a changed block is written


def rgba(hex_color):
    c = Gdk.RGBA()
    c.parse(hex_color)
    return c


def hex_of(color):
    return "#%02x%02x%02x" % tuple(round(max(0.0, min(1.0, v)) * 255) for v in (color.red, color.green, color.blue))


class Writer:
    """Background writer that coalesces bursts of changes (e.g. slider drags)."""

    def __init__(self, on_error):
        self.pending = {}
        self.cond = threading.Condition()
        self.on_error = on_error
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, key, job):
        with self.cond:
            self.pending[key] = (time.monotonic(), job)
            self.cond.notify()

    def _run(self):
        while True:
            with self.cond:
                while not self.pending:
                    self.cond.wait()
                newest = max(t for t, _ in self.pending.values())
                wait = newest + WRITE_DELAY - time.monotonic()
                if wait > 0:
                    self.cond.wait(wait)
                    continue
                jobs = [job for _, job in self.pending.values()]
                self.pending.clear()
            for job in jobs:
                try:
                    job()
                except (D.DeviceError, P.ProtocolError, OSError) as e:
                    GLib.idle_add(self.on_error, str(e))
                    break


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Model O 2 Settings")
        self.set_default_size(600, 780)
        self.state = config.load()
        self.device = None
        self.loading = False
        self.writer = Writer(self.show_error)
        self.closing = False
        self.unsent = set()  # (profile, block) changed while the mouse was away

        self.stack = Adw.ViewStack()
        self.toasts = Adw.ToastOverlay(child=self.stack)
        self.banner = Adw.Banner(revealed=False)

        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        self.profile_dropdown = Gtk.DropDown.new_from_strings([f"Profile {n}" for n in range(1, P.PROFILE_COUNT + 1)])
        self.profile_dropdown.set_tooltip_text("Onboard profile being edited and used")
        self.profile_dropdown.connect("notify::selected", self.on_profile_selected)
        header.pack_start(self.profile_dropdown)
        header.pack_end(self.build_menu())

        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        self.set_content(view)

        self.stack.add_titled_with_icon(self.build_performance(), "performance", "Performance", "input-mouse-symbolic")
        self.stack.add_titled_with_icon(self.build_lighting(), "lighting", "Lighting", "display-brightness-symbolic")
        self.stack.add_titled_with_icon(self.build_buttons(), "buttons", "Buttons", "input-keyboard-symbolic")

        self.load_profile()
        self.refresh_connection()
        GLib.timeout_add_seconds(2, self.refresh_connection)
        threading.Thread(target=self.listen, daemon=True).start()
        self.connect("close-request", self.on_close)

    # ----- state helpers -------------------------------------------------

    @property
    def number(self):
        return self.state["active_profile"]

    @property
    def prof(self):
        return config.profile(self.state, self.number)

    def changed(self, block):
        """Save and schedule a write of one settings block for the current profile."""
        if self.loading:
            return
        config.save(self.state)
        self.send(self.number, block)

    def send(self, number, block):
        dev = self.device
        if dev is None:
            self.unsent.add((number, block))
            return
        snapshot = {block: _copy(config.profile(self.state, number)[block])}
        self.writer.submit((number, block), lambda: getattr(dev, f"write_{block}")(number, snapshot))

    def show_error(self, message):
        self.toasts.add_toast(Adw.Toast(title=message.splitlines()[0], timeout=6))
        return False

    # ----- menu ----------------------------------------------------------

    def build_menu(self):
        menu = Gio.Menu()
        menu.append("Write all profiles to mouse", "win.apply-all")
        menu.append("Reset this profile to defaults", "win.reset")
        menu.append("About", "win.about")
        for name, handler in (("apply-all", self.on_apply_all), ("reset", self.on_reset), ("about", self.on_about)):
            action = Gio.SimpleAction(name=name)
            action.connect("activate", handler)
            self.add_action(action)
        return Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu")

    def on_apply_all(self, *_):
        dev = self.device
        if dev is None:
            self.show_error("The mouse isn't connected.")
            return
        state = _copy(self.state)

        def job():
            for n in range(1, P.PROFILE_COUNT + 1):
                dev.write_profile(n, config.profile(state, n))
            dev.activate_profile(state["active_profile"])
            GLib.idle_add(self.show_error, "All three profiles written to the mouse.")

        self.writer.submit("all", job)

    def on_reset(self, *_):
        dialog = Adw.AlertDialog(
            heading=f"Reset profile {self.number}?",
            body="DPI, sensor, lighting and button settings go back to factory values.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(d, response):
            if response != "reset":
                return
            self.state["profiles"][self.number - 1] = config.default_profile()
            self.load_profile()
            for block in ("performance", "lighting", "buttons"):
                self.changed(block)

        dialog.connect("response", done)
        dialog.present(self)

    def on_about(self, *_):
        about = Adw.AboutDialog(
            application_name="Model O 2 Settings",
            application_icon="input-mouse",
            version=__version__,
            comments="Settings for the Glorious Model O 2 Wireless on Linux.\n"
                     "Not affiliated with Glorious.",
        )
        about.present(self)

    # ----- performance page ---------------------------------------------

    def build_performance(self):
        page = Adw.PreferencesPage()

        status = Adw.PreferencesGroup(title="Mouse")
        self.connection_row = Adw.ActionRow(title="Connection", subtitle="Looking for the mouse…")
        self.connection_row.add_prefix(Gtk.Image(icon_name="network-wireless-symbolic"))
        self.battery_row = Adw.ActionRow(title="Battery", subtitle="Waiting for a report (about a minute)")
        self.battery_row.add_prefix(Gtk.Image(icon_name="battery-symbolic"))
        self.battery_bar = Gtk.LevelBar(min_value=0, max_value=100, valign=Gtk.Align.CENTER, width_request=120)
        self.battery_bar.add_offset_value("low", 20)
        self.battery_bar.add_offset_value("high", 60)
        self.battery_bar.set_visible(False)
        self.battery_row.add_suffix(self.battery_bar)
        status.add(self.connection_row)
        status.add(self.battery_row)
        page.add(status)

        self.dpi_group = Adw.PreferencesGroup(
            title="DPI stages",
            description="The DPI button steps through these. The ticked stage is where the mouse starts.",
        )
        self.add_stage_button = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add a stage",
                                           valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.add_stage_button.connect("clicked", self.on_add_stage)
        self.dpi_group.set_header_suffix(self.add_stage_button)
        self.stage_rows = []
        page.add(self.dpi_group)

        sensor = Adw.PreferencesGroup(title="Sensor")
        self.polling_row = Adw.ComboRow(title="Polling rate",
                                        model=Gtk.StringList.new([f"{r} Hz" for r in sorted(P.POLLING_RATES)]))
        self.polling_row.connect("notify::selected", self.on_perf_combo, "polling_rate", sorted(P.POLLING_RATES))
        self.lod_row = Adw.ComboRow(title="Lift-off distance", model=Gtk.StringList.new(["1 mm", "2 mm"]))
        self.lod_row.connect("notify::selected", self.on_perf_combo, "lift_off", list(P.LIFT_OFF))
        self.debounce_row = Adw.SpinRow.new_with_range(0, P.DEBOUNCE_MAX, 1)
        self.debounce_row.set_title("Debounce time")
        self.debounce_row.set_subtitle("Milliseconds. Raise it if clicks register twice.")
        self.debounce_row.connect("notify::value", self.on_debounce)
        self.motion_row = Adw.SwitchRow(title="Motion sync",
                                        subtitle="Lines sensor reads up with polling; adds a little latency")
        self.motion_row.connect("notify::active", self.on_motion_sync)
        for row in (self.polling_row, self.lod_row, self.debounce_row, self.motion_row):
            sensor.add(row)
        page.add(sensor)
        return page

    def rebuild_stages(self):
        for row in self.stage_rows:
            self.dpi_group.remove(row)
        self.stage_rows = []
        perf = self.prof["performance"]
        stages = perf["dpi_stages"]
        first_check = None
        for i, stage in enumerate(stages):
            row = Adw.ActionRow(title=f"Stage {i + 1}")
            check = Gtk.CheckButton(active=i == perf["active_stage"], valign=Gtk.Align.CENTER,
                                    tooltip_text="Start on this stage")
            if first_check is None:
                first_check = check
            else:
                check.set_group(first_check)
            check.connect("toggled", self.on_stage_selected, i)
            row.add_prefix(check)

            spin = Gtk.SpinButton.new_with_range(P.DPI_MIN, P.DPI_MAX, P.DPI_STEP)
            spin.set_value(stage["dpi"])
            spin.set_valign(Gtk.Align.CENTER)
            spin.set_width_chars(6)
            spin.connect("value-changed", self.on_stage_dpi, i)
            row.add_suffix(spin)

            color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), rgba=rgba(stage["color"]),
                                          valign=Gtk.Align.CENTER, tooltip_text="Indicator colour")
            color.connect("notify::rgba", self.on_stage_color, i)
            row.add_suffix(color)

            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                css_classes=["flat"], tooltip_text="Remove stage",
                                sensitive=len(stages) > 1)
            remove.connect("clicked", self.on_remove_stage, i)
            row.add_suffix(remove)
            self.dpi_group.add(row)
            self.stage_rows.append(row)
        self.add_stage_button.set_sensitive(len(stages) < P.MAX_DPI_STAGES)

    def on_stage_selected(self, check, index):
        if check.get_active() and not self.loading:
            self.prof["performance"]["active_stage"] = index
            self.changed("performance")

    def on_stage_dpi(self, spin, index):
        value = int(round(spin.get_value() / P.DPI_STEP) * P.DPI_STEP)
        self.prof["performance"]["dpi_stages"][index]["dpi"] = value
        self.changed("performance")

    def on_stage_color(self, button, _pspec, index):
        self.prof["performance"]["dpi_stages"][index]["color"] = hex_of(button.get_rgba())
        self.changed("performance")

    def on_add_stage(self, _button):
        stages = self.prof["performance"]["dpi_stages"]
        if len(stages) >= P.MAX_DPI_STAGES:
            return
        last = stages[-1]["dpi"]
        stages.append({"dpi": min(P.DPI_MAX, last * 2), "color": "#ff00ff"})
        self.with_loading(self.rebuild_stages)
        self.changed("performance")

    def on_remove_stage(self, _button, index):
        perf = self.prof["performance"]
        if len(perf["dpi_stages"]) <= 1:
            return
        del perf["dpi_stages"][index]
        if perf["active_stage"] >= index and perf["active_stage"] > 0:
            perf["active_stage"] -= 1
        # Rebuild after the click handler returns; the button belongs to a row we remove.
        GLib.idle_add(lambda: self.with_loading(self.rebuild_stages) or False)
        self.changed("performance")

    def on_perf_combo(self, row, _pspec, key, values):
        if self.loading:
            return
        self.prof["performance"][key] = values[row.get_selected()]
        self.changed("performance")

    def on_debounce(self, row, _pspec):
        if self.loading:
            return
        self.prof["performance"]["debounce"] = int(row.get_value())
        self.changed("performance")

    def on_motion_sync(self, row, _pspec):
        if self.loading:
            return
        self.prof["performance"]["motion_sync"] = row.get_active()
        self.changed("performance")

    # ----- lighting page -------------------------------------------------

    def build_lighting(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Effect")
        self.effect_names = list(P.EFFECTS)
        self.effect_row = Adw.ComboRow(title="Effect",
                                       model=Gtk.StringList.new([P.EFFECT_LABELS[e] for e in self.effect_names]))
        self.effect_row.connect("notify::selected", self.on_effect)
        group.add(self.effect_row)

        self.brightness_scale = self.make_scale(group, "Brightness", "brightness")
        self.speed_row, self.speed_scale = self.make_scale(group, "Speed", "speed", with_row=True)
        page.add(group)

        self.colors_group = Adw.PreferencesGroup(title="Colours")
        self.color_buttons = []
        box = Gtk.Box(spacing=12, halign=Gtk.Align.START, margin_top=6, margin_bottom=6)
        for i in range(P.MAX_COLORS):
            button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
            button.connect("notify::rgba", self.on_light_color, i)
            box.append(button)
            self.color_buttons.append(button)
        self.colors_group.add(box)
        page.add(self.colors_group)

        note = Adw.PreferencesGroup(
            description="You can also change lighting on the mouse itself: hold wheel click + DPI and press "
                        "forward (effect), right (brightness), back (speed) or left (colour). Those changes "
                        "aren't seen here.")
        page.add(note)
        return page

    def make_scale(self, group, title, key, with_row=False):
        row = Adw.ActionRow(title=title)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_format_value_func(lambda _s, v: f"{int(v)}%")
        scale.set_size_request(320, -1)
        scale.connect("value-changed", self.on_light_scale, key)
        row.add_suffix(scale)
        group.add(row)
        return (row, scale) if with_row else scale

    def on_effect(self, row, _pspec):
        effect = self.effect_names[row.get_selected()]
        self.update_lighting_sensitivity(effect)
        if self.loading:
            return
        self.prof["lighting"]["effect"] = effect
        self.changed("lighting")

    def update_lighting_sensitivity(self, effect):
        count = P.EFFECTS[effect][1]
        self.colors_group.set_visible(count > 0)
        for i, button in enumerate(self.color_buttons):
            button.set_visible(i < count)
        self.speed_row.set_sensitive(effect in P.ANIMATED)
        self.brightness_scale.set_sensitive(effect != "off")

    def on_light_scale(self, scale, key):
        if self.loading:
            return
        self.prof["lighting"][key] = int(round(scale.get_value() / 5) * 5)
        self.changed("lighting")

    def on_light_color(self, button, _pspec, index):
        if self.loading:
            return
        self.prof["lighting"]["colors"][index] = hex_of(button.get_rgba())
        self.changed("lighting")

    # ----- buttons page ---------------------------------------------------

    def build_buttons(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Buttons",
            description="Keystrokes use key names like a, f5, space, enter, with modifiers ctrl, shift, alt, super "
                        "(e.g. ctrl+shift+t).")
        # One flat choice list: mouse actions, DPI shift, media keys, keystroke.
        self.choices = [("mouse", a) for a in P.MOUSE_ACTIONS]
        self.choices.append(("dpi-shift", None))
        self.choices += [("media", a) for a in P.MEDIA_ACTIONS]
        self.choices.append(("key", None))
        labels = [P.MOUSE_ACTION_LABELS[a] for a in P.MOUSE_ACTIONS] + ["DPI shift (hold)…"]
        labels += [P.MEDIA_ACTION_LABELS[a] for a in P.MEDIA_ACTIONS] + ["Keystroke…"]

        self.button_rows = {}
        for button in P.BUTTONS:
            combo = Adw.ComboRow(title=P.BUTTON_LABELS[button], model=Gtk.StringList.new(labels),
                                 enable_search=True)
            combo.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
            keys = Adw.EntryRow(title="Key combination", show_apply_button=True, visible=False)
            shift = Adw.SpinRow.new_with_range(P.DPI_MIN, P.DPI_MAX, P.DPI_STEP)
            shift.set_title("DPI while held")
            shift.set_visible(False)
            combo.connect("notify::selected", self.on_binding_choice, button)
            keys.connect("apply", self.on_binding_keys, button)
            shift.connect("notify::value", self.on_binding_shift, button)
            for row in (combo, keys, shift):
                group.add(row)
            self.button_rows[button] = (combo, keys, shift)
        page.add(group)
        return page

    def show_binding(self, button):
        combo, keys, shift = self.button_rows[button]
        binding = self.prof["buttons"][button]
        kind = binding["type"]
        if kind == "mouse" and binding["action"] == "dpi-shift":
            choice = ("dpi-shift", None)
            shift.set_value(binding.get("dpi", 400))
        elif kind == "key":
            choice = ("key", None)
            keys.set_text(bindings.format(binding).removeprefix("key:"))
        else:
            choice = (kind, binding["action"])
        combo.set_selected(self.choices.index(choice) if choice in self.choices else 0)
        keys.set_visible(choice[0] == "key")
        shift.set_visible(choice[0] == "dpi-shift")

    def on_binding_choice(self, combo, _pspec, button):
        kind, action = self.choices[combo.get_selected()]
        _, keys, shift = self.button_rows[button]
        keys.set_visible(kind == "key")
        shift.set_visible(kind == "dpi-shift")
        if self.loading:
            return
        if kind == "key":
            keys.grab_focus()
            return  # written once a combination is applied
        if kind == "dpi-shift":
            binding = {"type": "mouse", "action": "dpi-shift", "dpi": int(shift.get_value())}
        else:
            binding = {"type": kind, "action": action}
        self.prof["buttons"][button] = binding
        self.changed("buttons")

    def on_binding_keys(self, entry, button):
        try:
            binding = bindings.parse_keys(entry.get_text())
        except P.ProtocolError as e:
            entry.add_css_class("error")
            self.show_error(str(e))
            return
        entry.remove_css_class("error")
        self.prof["buttons"][button] = binding
        self.changed("buttons")

    def on_binding_shift(self, row, _pspec, button):
        if self.loading:
            return
        dpi = int(round(row.get_value() / P.DPI_STEP) * P.DPI_STEP)
        self.prof["buttons"][button] = {"type": "mouse", "action": "dpi-shift", "dpi": dpi}
        self.changed("buttons")

    # ----- loading -----------------------------------------------------------

    def with_loading(self, fn):
        self.loading = True
        try:
            fn()
        finally:
            self.loading = False

    def load_profile(self):
        def fill():
            self.profile_dropdown.set_selected(self.number - 1)
            perf, light = self.prof["performance"], self.prof["lighting"]
            self.rebuild_stages()
            self.polling_row.set_selected(sorted(P.POLLING_RATES).index(perf["polling_rate"]))
            self.lod_row.set_selected(list(P.LIFT_OFF).index(perf["lift_off"]))
            self.debounce_row.set_value(perf["debounce"])
            self.motion_row.set_active(perf["motion_sync"])
            self.effect_row.set_selected(self.effect_names.index(light["effect"]))
            self.update_lighting_sensitivity(light["effect"])
            self.brightness_scale.set_value(light["brightness"])
            self.speed_scale.set_value(light["speed"])
            for button, color in zip(self.color_buttons, light["colors"]):
                button.set_rgba(rgba(color))
            for button in P.BUTTONS:
                self.show_binding(button)
        self.with_loading(fill)

    def on_profile_selected(self, dropdown, _pspec):
        if self.loading:
            return
        number = dropdown.get_selected() + 1
        if number == self.number:
            return
        self.state["active_profile"] = number
        config.save(self.state)
        self.load_profile()
        dev = self.device
        if dev is not None:
            self.writer.submit("profile", lambda: dev.activate_profile(number))

    # ----- device status ---------------------------------------------------

    def refresh_connection(self):
        if self.closing:
            return False
        dev = D.find_device()
        was = self.device
        self.device = dev
        if dev is None:
            bt = D.bluetooth_connected()
            self.connection_row.set_subtitle("Bluetooth (not configurable)" if bt else "Not connected")
            self.banner.set_title(
                "The mouse is on Bluetooth. Switch it to 2.4 GHz or plug in the cable to change settings."
                if bt else "Plug in the dongle or cable. Changes are saved and sent once the mouse is back.")
            self.banner.set_revealed(True)
        else:
            what = "Receiver" if dev.transport == P.DONGLE else "Firmware"
            fw = f" · {what} firmware {dev.firmware}" if dev.firmware else ""
            self.connection_row.set_subtitle(f"{dev.label}{fw}")
            self.banner.set_revealed(False)
            if was is None or was.path != dev.path:
                self.check_access(dev)
                pending, self.unsent = sorted(self.unsent), set()
                for number, block in pending:
                    self.send(number, block)
        self.show_battery()
        return True

    def check_access(self, dev):
        try:
            with open(dev.path, "rb"):
                pass
        except PermissionError:
            self.banner.set_title("No permission to talk to the mouse. Install the udev rule (see README).")
            self.banner.set_revealed(True)

    def show_battery(self):
        battery = self.state.get("battery")
        if not battery:
            return
        age = int(time.time() - battery["time"])
        stale = "" if age < 180 else f" · {age // 60} min ago"
        charging = ", charging" if battery["charging"] else ""
        self.battery_row.set_subtitle(f"{battery['level']}%{charging}{stale}")
        self.battery_bar.set_value(battery["level"])
        self.battery_bar.set_visible(True)

    def listen(self):
        while not self.closing:
            dev = self.device
            if dev is None:
                time.sleep(2)
                continue
            try:
                for event in dev.events(stop=lambda: self.closing or self.device is not dev):
                    GLib.idle_add(self.on_event, event)
            except D.DeviceError:
                time.sleep(2)

    def on_event(self, event):
        if event["event"] == "battery":
            self.state["battery"] = {"level": event["level"], "charging": event["charging"], "time": time.time()}
            config.save(self.state)
            self.show_battery()
        elif event["event"] == "profile" and event["profile"] != self.number:
            self.state["active_profile"] = event["profile"]
            config.save(self.state)
            self.load_profile()
            self.toasts.add_toast(Adw.Toast(title=f"Mouse switched to profile {event['profile']}", timeout=3))
        return False

    def on_close(self, *_):
        self.closing = True
        return False


def _copy(value):
    return copy.deepcopy(value)


class App(Adw.Application):
    def __init__(self, page=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.page = page

    def do_activate(self):
        window = self.get_active_window() or Window(self)
        if self.page:
            window.stack.set_visible_child_name(self.page)
        window.present()


def main(page=None):
    return App(page).run(sys.argv[:1])
