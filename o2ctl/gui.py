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
from . import macros as M  # noqa: E402
from . import battery as B  # noqa: E402
from . import device as D  # noqa: E402
from . import firmware as F  # noqa: E402
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
        self.unsent = set()  # (profile, block), ("macro", id) or ("sleep", None) changed while the mouse was away

        self.stack = Adw.ViewStack()
        self.toasts = Adw.ToastOverlay(child=self.stack)
        self.banner = Adw.Banner(revealed=False)

        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.NARROW))
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
        self.stack.add_titled_with_icon(self.build_macros(), "macros", "Macros", "media-record-symbolic")

        self.load_profile()
        self.with_loading(self.load_sleep)
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
        if block == "buttons":
            self.rebuild_macros()  # "used by" lines

    def send(self, number, block):
        if number == "macro":
            self.send_macro(block)
            return
        if number == "sleep":
            self.send_sleep()
            return
        dev = self.device
        if dev is None:
            self.unsent.add((number, block))
            return
        snapshot = {block: _copy(config.profile(self.state, number)[block])}
        self.writer.submit((number, block), lambda: getattr(dev, f"write_{block}")(number, snapshot))

    def send_macro(self, macro_id):
        macro = M.by_id(self.state, macro_id)
        if macro is None:
            return
        dev = self.device
        if dev is None:
            self.unsent.add(("macro", macro_id))
            return
        snapshot = _copy(macro)
        self.writer.submit(("macro", macro_id), lambda: dev.write_macro(snapshot))

    def show_error(self, message):
        self.toasts.add_toast(Adw.Toast(title=message.splitlines()[0], timeout=6))
        return False

    # ----- menu ----------------------------------------------------------

    def build_menu(self):
        menu = Gio.Menu()
        menu.append("Write all profiles and macros to mouse", "win.apply-all")
        menu.append("Reset this profile to defaults", "win.reset")
        menu.append("Firmware…", "win.firmware")
        menu.append("About", "win.about")
        for name, handler in (("apply-all", self.on_apply_all), ("reset", self.on_reset),
                              ("firmware", self.on_firmware), ("about", self.on_about)):
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
            for macro in state["macros"]:
                dev.write_macro(macro)
            for n in range(1, P.PROFILE_COUNT + 1):
                dev.write_profile(n, config.profile(state, n))
            dev.write_sleep(state["light_sleep"])
            dev.activate_profile(state["active_profile"])
            GLib.idle_add(self.show_error, "All profiles and macros written to the mouse.")

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

    def on_firmware(self, *_):
        FirmwareDialog().present(self)

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
        self.battery_row = Adw.ActionRow(title="Battery", subtitle="Waiting for a report (can take a few minutes)")
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

        idle = Adw.PreferencesGroup(title="When idle")
        self.sleep_switch = Adw.SwitchRow(title="Turn the lights off",
                                          subtitle="All of them, after the mouse sits unused")
        self.sleep_minutes = Adw.SpinRow.new_with_range(1, P.SLEEP_MAX, 1)
        self.sleep_minutes.set_title("Minutes")
        self.sleep_switch.connect("notify::active", self.on_sleep)
        self.sleep_minutes.connect("notify::value", self.on_sleep)
        idle.add(self.sleep_switch)
        idle.add(self.sleep_minutes)
        page.add(idle)

        note = Adw.PreferencesGroup(
            description="You can also change lighting on the mouse itself: hold wheel click + DPI and press "
                        "forward (effect), right (brightness), back (speed) or left (colour). Those changes "
                        "aren't seen here.")
        page.add(note)
        return page

    def load_sleep(self):
        minutes = self.state["light_sleep"]
        self.sleep_switch.set_active(minutes is not None)
        self.sleep_minutes.set_value(minutes or 5)
        self.sleep_minutes.set_sensitive(minutes is not None)

    def on_sleep(self, *_):
        on = self.sleep_switch.get_active()
        self.sleep_minutes.set_sensitive(on)
        if self.loading:
            return
        self.state["light_sleep"] = int(self.sleep_minutes.get_value()) if on else None
        config.save(self.state)
        self.send_sleep()

    def send_sleep(self):
        dev = self.device
        if dev is None:
            self.unsent.add(("sleep", None))
            return
        minutes = self.state["light_sleep"]
        self.writer.submit("sleep", lambda: dev.write_sleep(minutes))

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
        self.choices.append(("macro", None))
        labels = [P.MOUSE_ACTION_LABELS[a] for a in P.MOUSE_ACTIONS] + ["DPI shift (hold)…"]
        labels += [P.MEDIA_ACTION_LABELS[a] for a in P.MEDIA_ACTIONS] + ["Keystroke…", "Macro…"]
        self.mode_names = list(P.MACRO_MODES)

        self.button_rows = {}
        for button in P.BUTTONS:
            combo = Adw.ComboRow(title=P.BUTTON_LABELS[button], model=Gtk.StringList.new(labels),
                                 enable_search=True)
            combo.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
            keys = Adw.EntryRow(title="Key combination", show_apply_button=True, visible=False)
            shift = Adw.SpinRow.new_with_range(P.DPI_MIN, P.DPI_MAX, P.DPI_STEP)
            shift.set_title("DPI while held")
            shift.set_visible(False)
            macro = Adw.ComboRow(title="Macro", model=Gtk.StringList(), visible=False)
            mode = Adw.ComboRow(title="Plays", visible=False,
                                model=Gtk.StringList.new([P.MACRO_MODE_LABELS[m] for m in self.mode_names]))
            combo.connect("notify::selected", self.on_binding_choice, button)
            keys.connect("apply", self.on_binding_keys, button)
            shift.connect("notify::value", self.on_binding_shift, button)
            macro.connect("notify::selected", self.on_binding_macro, button)
            mode.connect("notify::selected", self.on_binding_macro, button)
            for row in (combo, keys, shift, macro, mode):
                group.add(row)
            self.button_rows[button] = (combo, keys, shift, macro, mode)
        page.add(group)
        return page

    def refresh_macro_choices(self):
        """Refill every button's macro list after macros are added or removed."""
        names = [m["name"] for m in self.state["macros"]]

        def fill():
            for button in P.BUTTONS:
                macro = self.button_rows[button][3]
                macro.set_model(Gtk.StringList.new(names))
                self.show_binding(button)
        self.with_loading(fill)

    def show_macro_rows(self, button, visible):
        _, _, _, macro, mode = self.button_rows[button]
        macro.set_visible(visible)
        mode.set_visible(visible)

    def show_binding(self, button):
        combo, keys, shift, macro, mode = self.button_rows[button]
        binding = self.prof["buttons"][button]
        kind = binding["type"]
        if kind == "macro":
            choice = ("macro", None)
            ids = [m["id"] for m in self.state["macros"]]
            if binding["macro"] in ids:
                macro.set_selected(ids.index(binding["macro"]))
            mode.set_selected(self.mode_names.index(binding.get("mode", "once")))
        elif kind == "mouse" and binding["action"] == "dpi-shift":
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
        self.show_macro_rows(button, choice[0] == "macro")

    def on_binding_choice(self, combo, _pspec, button):
        kind, action = self.choices[combo.get_selected()]
        _, keys, shift, macro, mode = self.button_rows[button]
        keys.set_visible(kind == "key")
        shift.set_visible(kind == "dpi-shift")
        self.show_macro_rows(button, kind == "macro")
        if self.loading:
            return
        if kind == "key":
            keys.grab_focus()
            return  # written once a combination is applied
        if kind == "macro":
            if not self.state["macros"]:
                self.show_error("No macros yet. Make one on the Macros page first.")
                return
            self.on_binding_macro(macro, None, button)
            return
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

    def on_binding_macro(self, _row, _pspec, button):
        if self.loading or not self.state["macros"]:
            return
        _, _, _, macro, mode = self.button_rows[button]
        chosen = self.state["macros"][min(macro.get_selected(), len(self.state["macros"]) - 1)]
        self.prof["buttons"][button] = {"type": "macro", "macro": chosen["id"],
                                        "mode": self.mode_names[mode.get_selected()]}
        self.send_macro(chosen["id"])  # in case the mouse lost it (factory reset)
        self.changed("buttons")

    def on_binding_shift(self, row, _pspec, button):
        if self.loading:
            return
        dpi = int(round(row.get_value() / P.DPI_STEP) * P.DPI_STEP)
        self.prof["buttons"][button] = {"type": "mouse", "action": "dpi-shift", "dpi": dpi}
        self.changed("buttons")

    # ----- macros page ----------------------------------------------------

    def build_macros(self):
        page = Adw.PreferencesPage()
        self.macro_group = Adw.PreferencesGroup(
            title="Macros",
            description="Saved on the mouse, shared by all profiles. Bind one on the Buttons page.")
        self.macro_rows = []
        page.add(self.macro_group)

        new = Adw.PreferencesGroup(
            title="New macro",
            description="Steps are separated by spaces: a key (a, f5, enter), a combination (ctrl+c), "
                        "mouse:left, +shift or -shift to only press or release a key, and 150ms to wait. "
                        f"Steps are {M.DEFAULT_DELAY} ms apart unless a wait says otherwise.")
        self.new_macro_name = Adw.EntryRow(title="Name")
        self.new_macro_steps = Adw.EntryRow(title="Steps, e.g. ctrl+c 100ms ctrl+v", show_apply_button=True)
        self.new_macro_steps.connect("apply", self.on_macro_add)
        new.add(self.new_macro_name)
        new.add(self.new_macro_steps)
        page.add(new)
        self.rebuild_macros()
        return page

    def rebuild_macros(self):
        for row in self.macro_rows:
            self.macro_group.remove(row)
        self.macro_rows = []
        for macro in self.state["macros"]:
            used = M.users(self.state, macro["id"])
            where = ", ".join(f"profile {n} {P.BUTTON_LABELS[b].split(' (')[0].lower()}" for n, b in used)
            row = Adw.ExpanderRow(title=macro["name"], subtitle=where or "Not bound to a button",
                                  use_markup=False)
            steps = Adw.EntryRow(title="Steps", text=M.format(macro["steps"]), show_apply_button=True)
            steps.connect("apply", self.on_macro_steps, macro["id"])
            delete = Adw.ActionRow(title="Delete macro",
                                   subtitle="Buttons that play it go back to their default action")
            button = Gtk.Button(label="Delete", valign=Gtk.Align.CENTER)
            button.add_css_class("destructive-action")
            button.connect("clicked", self.on_macro_delete, macro["id"])
            delete.add_suffix(button)
            row.add_row(steps)
            row.add_row(delete)
            self.macro_rows.append(row)
        if not self.macro_rows:
            self.macro_rows.append(Adw.ActionRow(title="No macros yet", subtitle="Add one below"))
        for row in self.macro_rows:
            self.macro_group.add(row)

    def on_macro_add(self, entry):
        try:
            name = M.check_name(self.new_macro_name.get_text())
            if M.find(self.state, name):
                raise P.ProtocolError("There is already a macro with that name.")
            macro = M.put(self.state, name, M.parse(entry.get_text()))
        except P.ProtocolError as e:
            entry.add_css_class("error")
            self.show_error(str(e))
            return
        entry.remove_css_class("error")
        entry.set_text("")
        self.new_macro_name.set_text("")
        config.save(self.state)
        self.send_macro(macro["id"])
        self.rebuild_macros()
        self.refresh_macro_choices()

    def on_macro_steps(self, entry, macro_id):
        macro = M.by_id(self.state, macro_id)
        try:
            macro["steps"] = M.parse(entry.get_text())
        except P.ProtocolError as e:
            entry.add_css_class("error")
            self.show_error(str(e))
            return
        entry.remove_css_class("error")
        config.save(self.state)
        self.send_macro(macro_id)

    def on_macro_delete(self, _button, macro_id):
        macro = M.by_id(self.state, macro_id)
        dialog = Adw.AlertDialog(heading=f"Delete macro {macro['name']}?",
                                 body="Buttons that play it go back to their default action.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, response):
            if response != "delete" or M.by_id(self.state, macro_id) is None:
                return
            for number in M.delete(self.state, macro):
                self.send(number, "buttons")
            config.save(self.state)
            self.rebuild_macros()
            self.refresh_macro_choices()

        dialog.connect("response", done)
        dialog.present(self)

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
        self.with_loading(fill)
        self.refresh_macro_choices()  # also shows the button bindings

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
                pending, self.unsent = sorted(self.unsent, key=str), set()  # mixed ints and strings
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
        reading = B.load()
        if not reading:
            return
        wired = self.device is not None and self.device.transport == P.WIRED
        self.battery_row.set_subtitle(B.describe(reading, wired=wired))
        self.battery_bar.set_value(reading["level"])
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
            B.record(event)
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


class FirmwareDialog(Adw.Dialog):
    """Installed versions, and an update of the mouse over the cable."""

    def __init__(self):
        super().__init__(title="Firmware", content_width=440)
        self.busy = False
        self.closed = False
        self.connect("closed", lambda *_: setattr(self, "closed", True))
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description="The mouse is updated over the USB cable. The dongle can only be updated "
                        "from the terminal, and that hasn't been tried yet.")
        self.mouse_row = Adw.ActionRow(title="Mouse")
        self.dongle_row = Adw.ActionRow(title="Dongle")
        group.add(self.mouse_row)
        group.add(self.dongle_row)
        group.add(Adw.ActionRow(title="Latest from Glorious", subtitle=F.LATEST))
        page.add(group)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=6)
        self.button = Gtk.Button(label="Update mouse", halign=Gtk.Align.CENTER,
                                 css_classes=["pill", "suggested-action"])
        self.button.connect("clicked", self.on_update)
        self.progress = Gtk.ProgressBar(show_text=True, visible=False)
        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dim-label"])
        for widget in (self.button, self.progress, self.status):
            box.append(widget)
        actions = Adw.PreferencesGroup()
        actions.add(box)
        page.add(actions)

        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
        self.refresh()
        GLib.timeout_add_seconds(2, self.refresh)

    def wired(self):
        return next((d for d in D.find_devices() if d.transport == P.WIRED), None)

    def refresh(self):
        if self.closed:
            return False
        found = {d.transport: d for d in D.find_devices()}
        mouse, dongle = found.get(P.WIRED), found.get(P.DONGLE)
        self.mouse_row.set_subtitle((mouse.firmware or "unknown") if mouse else "Plug in the cable to see its version")
        self.dongle_row.set_subtitle((dongle.firmware or "unknown") if dongle else "Not plugged in")
        if not self.busy:
            self.button.set_sensitive(mouse is not None)
            if mouse is None:
                self.status.set_label("Plug the mouse in with the USB cable to update it.")
            elif mouse.firmware == F.LATEST:
                self.status.set_label("The mouse is up to date. Updating reinstalls the same version.")
            else:
                self.status.set_label("")
        return True

    def on_update(self, *_):
        confirm = Adw.AlertDialog(
            heading="Update the mouse firmware?",
            body=f"This installs firmware {F.LATEST} from Glorious over the cable. Keep the cable plugged in "
                 "and don't use the mouse until it finishes, about a minute.\n\n"
                 "If it fails partway the mouse may stop working, with no known way to recover it.",
        )
        confirm.add_response("cancel", "Cancel")
        confirm.add_response("update", "Update")
        confirm.set_response_appearance("update", Adw.ResponseAppearance.DESTRUCTIVE)
        confirm.connect("response", lambda d, r: r == "update" and self.start())
        confirm.present(self)

    def start(self):
        dev = self.wired()
        if dev is None:
            self.status.set_label("The mouse isn't on the cable.")
            return
        self.busy = True
        self.set_can_close(False)
        self.button.set_sensitive(False)
        self.progress.set_fraction(0)
        self.progress.set_text("Downloading…")
        self.progress.set_visible(True)
        self.status.set_label("Don't unplug the mouse or press its buttons.")

        def progress(percent):
            GLib.idle_add(self.show_progress, percent)

        def run():
            try:
                image = F.official_image(P.WIRED)
                F.flash(dev, image, progress)
            except (F.FirmwareError, D.DeviceError, OSError) as e:
                GLib.idle_add(self.finish, str(e))
            else:
                GLib.idle_add(self.finish, None)

        threading.Thread(target=run, daemon=True).start()

    def show_progress(self, percent):
        self.progress.set_fraction(percent / 100)
        self.progress.set_text(f"Updating… {percent}%")
        return False

    def finish(self, error):
        self.busy = False
        self.set_can_close(True)
        self.progress.set_visible(error is None)
        if error is None:
            self.progress.set_text("Done")
            self.status.set_label("Done. The mouse restarted and is ready to use.")
        else:
            self.status.set_label(error)
        self.refresh()
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
