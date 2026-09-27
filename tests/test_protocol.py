import contextlib
import io
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from o2ctl import battery as B  # noqa: E402
from o2ctl import bindings, config  # noqa: E402
from o2ctl import device as D  # noqa: E402
from o2ctl import macros as M  # noqa: E402
from o2ctl import protocol as P  # noqa: E402


def hexpad(text):
    """'03 02 fb' -> 64-byte packet with the rest zeroed."""
    raw = bytes.fromhex(text)
    return raw + bytes(P.PACKET_SIZE - len(raw))


class LightingTest(unittest.TestCase):
    def test_single_color_matches_hardware_verified_packet(self):
        # Sent to a real Model O 2 Wireless through the dongle; it turned solid green.
        light = {"effect": "single-color", "speed": 60, "brightness": 100, "colors": ["#00ff00"]}
        packets = P.lighting_packets(1, light, P.DONGLE)
        self.assertEqual(packets[0], hexpad("03 02 fb 00 01 04 0c 14 01 0c 14 00 ff 00"))
        self.assertEqual(packets[1], hexpad("03 02 fb 01 01 04"))
        self.assertEqual(packets[2], hexpad("03 02 fb 02 01 04"))

    def test_colours_spill_into_later_dongle_chunks(self):
        colors = ["#010203", "#040506", "#070809", "#0a0b0c", "#0d0e0f", "#101112"]
        light = {"effect": "breathing", "speed": 100, "brightness": 50, "colors": colors}
        p = P.lighting_packets(2, light, P.DONGLE)
        # block: 14 0a 06 14 0a | 01 02 03 | 04 05 06 | 07 08 09 | ...
        self.assertEqual(p[0], hexpad("03 02 fb 00 02 03 14 0a 06 14 0a 01 02 03"))
        self.assertEqual(p[1], hexpad("03 02 fb 01 02 03 04 05 06 07 08 09 0a 0b 0c"))
        self.assertEqual(p[2], hexpad("03 02 fb 02 02 03 0d 0e 0f 10 11 12"))

    def test_effect_uses_only_its_colour_count(self):
        light = {"effect": "glorious", "speed": 60, "brightness": 60, "colors": ["#ffffff"] * 6}
        block = P.lighting_block(light, P.DONGLE)
        self.assertEqual(block[2], 0)
        self.assertEqual(bytes(block[5:8]), bytes(3))

    def test_wired_layout(self):
        light = {"effect": "single-color", "speed": 60, "brightness": 100, "colors": ["#00ff00"]}
        (p,) = P.lighting_packets(3, light, P.WIRED)
        # Colours start at block offset 11 over the cable (packet byte 16).
        self.assertEqual(p, hexpad("03 02 fa 03 04 0c 14 01 0c 14 00 00 00 00 00 00 00 ff 00"))

    def test_level_scaling_matches_core(self):
        self.assertEqual(P._brightness_level(0), 0)
        self.assertEqual(P._brightness_level(3), 1)
        self.assertEqual(P._brightness_level(55), 11)
        self.assertEqual(P._speed_level(0), 1)
        self.assertEqual(P._speed_level(100), 20)

    def test_colour_forms(self):
        self.assertEqual(P.parse_color("#ff8000"), (255, 128, 0))
        self.assertEqual(P.parse_color("f80"), (255, 136, 0))
        self.assertEqual(P.parse_color((1, 2, 3)), (1, 2, 3))
        for bad in ("#ff80", "zzzzzz", ""):
            with self.assertRaises(P.ProtocolError):
                P.parse_color(bad)

    def test_off_effect(self):
        light = {"effect": "off", "speed": 60, "brightness": 60, "colors": []}
        self.assertEqual(P.lighting_packets(1, light, P.DONGLE)[0][5], 0)

    def test_rejects_unknown_effect(self):
        with self.assertRaises(P.ProtocolError):
            P.lighting_packets(1, {"effect": "disco", "speed": 1, "brightness": 1, "colors": []}, P.DONGLE)


class PerformanceTest(unittest.TestCase):
    def perf(self):
        return config.default_profile()["performance"]

    def test_default_dongle_packets(self):
        p = P.performance_packets(1, self.perf(), P.DONGLE)
        self.assertEqual(len(p), 4)
        self.assertEqual(p[0], hexpad("03 04 fb 00 01 02 04 01 02 04 00 08 00 ff ff 00"))
        self.assertEqual(p[1], hexpad("03 04 fb 01 01 10 00 00 00 ff 20 00 ff 00 00"))
        self.assertEqual(p[2], hexpad("03 04 fb 02 01 40 00 00 ff 00"))
        self.assertEqual(p[3], hexpad("03 04 fb 03 01"))

    def test_default_wired_packet(self):
        (p,) = P.performance_packets(2, self.perf(), P.WIRED)
        head = "03 04 fa 02 02 04 01 02 04 00" + " 00" * 6
        stages = "08 00 ff ff 00 00 00 00 10 00 00 00 ff 00 00 00 20 00 ff 00 00 00 00 00 40 00 00 ff 00"
        self.assertEqual(p, hexpad(head + " " + stages))

    def test_settings_bytes(self):
        perf = self.perf()
        perf.update(polling_rate=500, lift_off=2, debounce=8, motion_sync=True, active_stage=0)
        perf["dpi_stages"] = [{"dpi": 26000, "color": "#123456"}]
        block = P.performance_block(perf, P.DONGLE)
        self.assertEqual(bytes(block[:11]), bytes.fromhex("00 01 02 08 03 01 08 02 12 34 56"))

    def test_six_stages_fit_the_dongle_chunks(self):
        perf = self.perf()
        perf["dpi_stages"] = [{"dpi": 100 * (i + 1), "color": "#000000"} for i in range(6)]
        packets = P.performance_packets(1, perf, P.DONGLE)
        # Last stage (600 DPI = 12) lands at block offset 31, i.e. chunk 3 byte 0.
        self.assertEqual(packets[3][5:7], bytes([12, 0]))

    def test_validation(self):
        for key, value in [("polling_rate", 2000), ("lift_off", 3), ("debounce", 17), ("active_stage", 9)]:
            perf = self.perf()
            perf[key] = value
            with self.assertRaises(P.ProtocolError, msg=key):
                P.performance_block(perf, P.DONGLE)
        perf = self.perf()
        perf["dpi_stages"][0]["dpi"] = 50
        with self.assertRaises(P.ProtocolError):
            P.performance_block(perf, P.DONGLE)
        perf["dpi_stages"] = [{"dpi": 800, "color": "#000000"}] * 7
        with self.assertRaises(P.ProtocolError):
            P.performance_block(perf, P.DONGLE)


class ButtonsTest(unittest.TestCase):
    def test_default_dongle_packets(self):
        p = P.buttons_packets(1, P.DEFAULT_BINDINGS, P.DONGLE)
        self.assertEqual(p[0], hexpad("03 03 fb 00 01 01 01 00 00 01 02 00 00 01 03 00 00"))
        self.assertEqual(p[1], hexpad("03 03 fb 01 01 01 04 00 00 01 05 00 00 66 03 00 00"))

    def test_default_wired_packet_keeps_wheel_slots(self):
        (p,) = P.buttons_packets(1, P.DEFAULT_BINDINGS, P.WIRED)
        self.assertEqual(p[:8], bytes.fromhex("03 03 fa 01 00 00 00 00"))
        self.assertEqual(p[8:44], P.DEFAULT_SLOTS[:36])
        self.assertEqual(p[32:36], bytes([1, 160, 0, 0]))

    def test_slot_encodings(self):
        cases = {
            "key:ctrl+c": "02 01 06 00",
            "key:ctrl+shift+alt+super+f5": "02 0f 3e 00",
            "media:play-pause": "03 cd 00 00",
            "media:media-player": "03 83 01 00",
            "dpi-shift:400": "66 05 08 00",
            "dpi-shift:26000": "66 05 08 02",
            "dpi-cycle-up": "66 03 00 00",
            "profile-cycle-up": "01 b0 00 00",
            "battery-check": "77 00 00 00",
            "disabled": "00 00 00 00",
            "key:f13": "02 00 68 00",
            "key:0": "02 00 27 00",
            "key:1": "02 00 1e 00",
        }
        for text, expected in cases.items():
            self.assertEqual(P.binding_slot(bindings.parse(text)), bytes.fromhex(expected), text)

    def test_remap_lands_in_the_right_slot(self):
        b = dict(P.DEFAULT_BINDINGS, back=bindings.parse("media:volume-down"))
        p = P.buttons_packets(1, b, P.DONGLE)
        self.assertEqual(p[1][5:9], bytes.fromhex("03 ea 00 00"))

    def test_bad_bindings(self):
        for text in ("key:ctrl+", "key:hyper+a", "media:party", "fly", "dpi-shift:9"):
            with self.assertRaises(P.ProtocolError, msg=text):
                bindings.parse(text)


class ProfileAndEventsTest(unittest.TestCase):
    def test_profile_packet(self):
        self.assertEqual(P.profile_packet(2, P.DONGLE), [hexpad("03 01 fb 02")])
        self.assertEqual(P.profile_packet(3, P.WIRED), [hexpad("03 01 fa 03")])
        with self.assertRaises(P.ProtocolError):
            P.profile_packet(4, P.DONGLE)

    def test_events(self):
        self.assertEqual(P.parse_event(bytes.fromhex("06 fb 57 01 00 00 00 00")),
                         {"event": "battery", "level": 87, "charging": True})
        # Seen on hardware: 47%, not charging.
        self.assertEqual(P.parse_event(bytes.fromhex("06 fb 2f 00 00 00 00 00")),
                         {"event": "battery", "level": 47, "charging": False})
        self.assertEqual(P.parse_event(bytes.fromhex("06 f6 01 00")), {"event": "profile", "profile": 2})
        self.assertEqual(P.parse_event(bytes.fromhex("04 f1 03 00")), {"event": "profile", "profile": 3})
        self.assertIsNone(P.parse_event(bytes.fromhex("04 f1 a0 00")))  # reset-to-default notice
        self.assertIsNone(P.parse_event(bytes.fromhex("06 fb c8 00")))
        self.assertIsNone(P.parse_event(bytes.fromhex("01 00 05 00")))


class BindingTextTest(unittest.TestCase):
    def test_round_trip(self):
        for text in ("left-click", "dpi-shift:800", "media:mute", "key:ctrl+shift+t"):
            self.assertEqual(bindings.format(bindings.parse(text)), text)

    def test_aliases(self):
        self.assertEqual(bindings.parse("key:Control+Esc"),
                         {"type": "key", "key": "escape", "modifiers": ["ctrl"]})


class MacroTest(unittest.TestCase):
    COPY = [
        {"key": "ctrl", "down": True, "delay": 20},
        {"key": "c", "down": True, "delay": 20},
        {"key": "c", "down": False, "delay": 20},
        {"key": "ctrl", "down": False, "delay": 20},
    ]

    def test_hardware_verified_macro(self):
        # Sent to a real Model O 2 Wireless through the dongle and bound to a button; it typed "o2".
        self.assertEqual(P.macro_packets(1, M.parse("o 2"), P.DONGLE), [
            hexpad("03 05 fb 00 00 02 04 80 14 12 00 14 12 80 14 1f"),
            hexpad("03 05 fb 00 01 02 04 00 14 1f"),
        ])

    def test_dongle_packets(self):
        # 3 steps per packet from byte 7: [03 05 fb id-1 chunk chunks count].
        p = P.macro_packets(2, self.COPY, P.DONGLE)
        self.assertEqual(p, [
            hexpad("03 05 fb 01 00 02 04 80 14 e0 80 14 06 00 14 06"),
            hexpad("03 05 fb 01 01 02 04 00 14 e0"),
        ])

    def test_wired_packet(self):
        (p,) = P.macro_packets(1, self.COPY, P.WIRED)
        self.assertEqual(p, hexpad("03 05 fa 00 00 01 04 00 80 14 e0 80 14 06 00 14 06 00 14 e0"))

    def test_long_delay_and_mouse_button(self):
        steps = [{"key": "mouse:left", "down": True, "delay": 1000},
                 {"key": "mouse:left", "down": False, "delay": P.MACRO_DELAY_MAX}]
        self.assertEqual(P.macro_block(steps), bytes.fromhex("83 e8 b7 7f ff b7"))

    def test_chunking_at_the_limit(self):
        steps = [{"key": "a", "down": i % 2 == 0, "delay": 1} for i in range(P.MACRO_MAX_STEPS - 1)]
        steps.append({"key": "a", "down": False, "delay": 1})
        self.assertEqual(len(P.macro_packets(1, steps, P.DONGLE)), 29)
        self.assertEqual(len(P.macro_packets(1, steps, P.WIRED)), 5)
        with self.assertRaises(P.ProtocolError):
            P.macro_block(steps + [steps[0]])

    def test_validation(self):
        bad = [
            [],
            [{"key": "a", "down": True, "delay": 0}],
            [{"key": "a", "down": True, "delay": P.MACRO_DELAY_MAX + 1}],
            [{"key": "hyper", "down": True, "delay": 5}],
            [{"key": "mouse:side", "down": True, "delay": 5}],
        ]
        for steps in bad:
            with self.assertRaises(P.ProtocolError, msg=steps):
                P.macro_block(steps)
        with self.assertRaises(P.ProtocolError):
            P.macro_packets(P.MAX_MACROS + 1, self.COPY, P.DONGLE)

    def test_binding_slot(self):
        # CORE: [0x1F + macro id, mode]; mode 1 once, E0 repeat while held, E1 toggle.
        saved = [{"id": 1, "name": "copy", "steps": self.COPY}, {"id": 3, "name": "spam", "steps": self.COPY}]
        cases = {"macro:copy": "20 01 00 00", "macro:spam:repeat": "22 e0 00 00", "macro:3:toggle": "22 e1 00 00"}
        for text, expected in cases.items():
            binding = bindings.parse(text, saved)
            self.assertEqual(P.binding_slot(binding), bytes.fromhex(expected), text)
        self.assertEqual(bindings.format(bindings.parse("macro:3:toggle", saved), saved), "macro:spam:toggle")
        self.assertEqual(bindings.describe({"type": "macro", "macro": 1, "mode": "once"}, saved),
                         "Macro copy (play once)")
        with self.assertRaises(P.ProtocolError):
            bindings.parse("macro:nope", saved)

    def test_text(self):
        self.assertEqual(M.parse("ctrl+c"), self.COPY)
        steps = M.parse("Control+Esc 150ms +shift a -shift mouse:left", delay=10)
        self.assertEqual([(s["key"], s["down"], s["delay"]) for s in steps], [
            ("ctrl", True, 10), ("escape", True, 10), ("escape", False, 10), ("ctrl", False, 150),
            ("shift", True, 10), ("a", True, 10), ("a", False, 10), ("shift", False, 10),
            ("mouse:left", True, 10), ("mouse:left", False, 10),
        ])
        self.assertEqual(M.parse("a 100ms 50ms")[-1]["delay"], 150)
        for text in ("", "100ms a", "+shift a", "a+", "hyper", "a 0ms"):
            with self.assertRaises(P.ProtocolError, msg=text):
                M.parse(text)

    def test_text_round_trip(self):
        for text in ("ctrl+c 100ms ctrl+v", "a b c", "+shift a 5ms -shift", "mouse:left 2000ms mouse:right",
                     "ctrl+shift+t", "+a +b -a -b"):
            steps = M.parse(text)
            self.assertEqual(M.parse(M.format(steps)), steps, text)
        self.assertEqual(M.format(M.parse("ctrl+c 100ms ctrl+v")), "ctrl+c 100ms ctrl+v")

    def test_saved_macros(self):
        state = config.default_state()
        a = M.put(state, "a", M.parse("a"))
        b = M.put(state, "b", M.parse("b"))
        self.assertEqual((a["id"], b["id"]), (1, 2))
        state["profiles"][1]["buttons"]["forward"] = {"type": "macro", "macro": 1, "mode": "once"}
        self.assertEqual(M.users(state, 1), [(2, "forward")])
        self.assertEqual(M.used_by_profile(state, state["profiles"][1]), [a])
        self.assertEqual(M.delete(state, a), [2])
        self.assertEqual(state["profiles"][1]["buttons"]["forward"], P.DEFAULT_BINDINGS["forward"])
        self.assertEqual(M.put(state, "c", M.parse("c"))["id"], 1)  # freed ids are reused
        self.assertIs(M.put(state, "b", M.parse("x")), b)
        self.assertEqual(b["steps"], M.parse("x"))
        for name in ("", "12", "a:b"):
            with self.assertRaises(P.ProtocolError):
                M.put(state, name, M.parse("a"))

    def test_cli_saves_without_the_mouse(self):
        from o2ctl import cli
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["macro", "set", "cp", "ctrl+c", "ctrl+v", "-n"]), 0)
            self.assertEqual(cli.main(["bind", "back", "macro:cp:repeat", "-n", "-p", "2"]), 0)
            state = config.load()
            self.assertEqual(state["macros"][0]["name"], "cp")
            self.assertEqual(state["profiles"][1]["buttons"]["back"], {"type": "macro", "macro": 1, "mode": "repeat"})
            self.assertEqual(cli.main(["macro", "delete", "cp", "-n"]), 0)
            state = config.load()
            self.assertEqual(state["macros"], [])
            self.assertEqual(state["profiles"][1]["buttons"]["back"], P.DEFAULT_BINDINGS["back"])


class ConfigTest(unittest.TestCase):
    def test_round_trip_and_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "o2ctl", "config.json")
            self.assertEqual(config.load(path), config.default_state())
            state = config.default_state()
            state["profiles"][1]["performance"]["polling_rate"] = 500
            config.save(state, path)
            self.assertEqual(config.load(path), state)

            partial = {"active_profile": 9, "profiles": [{"performance": {"debounce": 6, "active_stage": 7}}]}
            fixed = config.normalise(partial)
            self.assertEqual(fixed["active_profile"], 1)
            self.assertEqual(len(fixed["profiles"]), 3)
            self.assertEqual(fixed["profiles"][0]["performance"]["debounce"], 6)
            self.assertEqual(fixed["profiles"][0]["performance"]["active_stage"], 3)
            self.assertEqual(set(fixed["profiles"][0]["buttons"]), set(P.BUTTONS))
            self.assertEqual(len(fixed["profiles"][0]["lighting"]["colors"]), P.MAX_COLORS)


# Report descriptors read from a real Model O 2 Wireless dongle.
KEYBOARD_DESC = bytes.fromhex(
    "05 01 09 06 a1 01 05 07 19 e0 29 e7 15 00 25 01 75 01 95 08 81 02 95 01 75 08 81 01"
    " 95 05 75 01 05 08 19 01 29 05 91 02 95 01 75 03 91 01 95 06 75 08 15 00 26 ff 00"
    " 05 07 19 00 2a ff 00 81 00 c0")
VENDOR_DESC = bytes.fromhex(
    "06 00 ff 09 01 a1 01 25 ff 75 08 95 3f 85 03 09 01 b1 02 c0 05 01 09 02 a1 01 85 01"
    " 09 01 a1 00 05 09 19 01 29 07 15 00 25 01 95 07 75 01 81 02 95 01 75 01 81 01 05 01"
    " 09 30 09 31 16 01 80 26 ff 7f 75 10 95 02 81 06 09 38 15 81 25 7f 75 08 95 01 81 06"
    " 06 01 ff 09 f1 75 08 95 05 15 00 26 ff 00 81 00 c0 c0 05 0c 09 01 a1 01 85 04 19 00"
    " 2a 9c 02 15 00 26 9c 02 75 10 95 01 81 00 c0 05 01 09 80 a1 01 85 05 1a 81 00 2a 83"
    " 00 15 00 25 01 75 01 95 03 81 02 95 05 81 01 c0 06 00 ff 0a 00 ff a1 01 85 06 15 00"
    " 26 ff 00 09 30 75 08 95 07 81 02 c0")


class DiscoveryTest(unittest.TestCase):
    def make_node(self, root, name, hid_id, desc, bcd="0259"):
        usb = os.path.join(root, "usb", name)
        hid = os.path.join(usb, "intf", "hid")
        os.makedirs(hid)
        with open(os.path.join(usb, "bcdDevice"), "w") as f:
            f.write(bcd + "\n")
        with open(os.path.join(hid, "uevent"), "w") as f:
            f.write(f"DRIVER=hid-generic\nHID_ID={hid_id}\nHID_NAME=Glorious Model O 2 Wireless\n")
        with open(os.path.join(hid, "report_descriptor"), "wb") as f:
            f.write(desc)
        node = os.path.join(root, "hidraw", name)
        os.makedirs(node)
        os.symlink(hid, os.path.join(node, "device"))

    def test_finds_vendor_interface_and_prefers_cable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_node(tmp, "hidraw0", "0003:0000093A:0000822D", KEYBOARD_DESC)
            self.make_node(tmp, "hidraw2", "0003:0000093A:0000822D", VENDOR_DESC)
            self.make_node(tmp, "hidraw4", "0003:00002516:0000014D", VENDOR_DESC)
            found = D.find_devices(os.path.join(tmp, "hidraw"))
            self.assertEqual([(d.path, d.transport, d.firmware) for d in found],
                             [("/dev/hidraw2", P.DONGLE, "0259")])
            self.make_node(tmp, "hidraw9", "0003:0000093A:0000822A", VENDOR_DESC, bcd="0102")
            found = D.find_devices(os.path.join(tmp, "hidraw"))
            self.assertEqual([d.transport for d in found], [P.WIRED, P.DONGLE])


class WriteTimingTest(unittest.TestCase):
    """The dongle drops a profile switch that follows a burst too closely."""

    def setUp(self):
        self.now = 1000.0
        self.log = []  # ("sleep", seconds) and ("write", command, time)
        fake_time = type("T", (), {})()
        fake_time.monotonic = lambda: self.now
        fake_time.sleep = self.sleep
        self.patches = [
            unittest.mock.patch.object(D, "time", fake_time),
            unittest.mock.patch.object(D.os, "open", lambda *a: 99),
            unittest.mock.patch.object(D.os, "close", lambda fd: None),
            unittest.mock.patch.object(D.fcntl, "ioctl", self.ioctl),
            unittest.mock.patch.object(D, "_write_lock", contextlib.nullcontext),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def sleep(self, seconds):
        self.log.append(("sleep", seconds))
        self.now += seconds

    def ioctl(self, fd, request, data):
        self.log.append(("write", data[1], self.now))

    def writes(self):
        return [(cmd, t) for kind, cmd, *rest in self.log if kind == "write" for t in rest]

    def test_profile_switch_waits_for_the_dongle_to_settle(self):
        dev = D.Device("/dev/hidraw9", P.DONGLE)
        state = config.default_state()
        for n in (1, 2, 3):
            dev.write_profile(n, config.profile(state, n))
        dev.activate_profile(1)
        writes = self.writes()
        (last_cmd, last_t), (prev_cmd, prev_t) = writes[-1], writes[-2]
        self.assertEqual(last_cmd, P.CMD_PROFILE)
        # Measured from when the previous send finished (its trailing delay included).
        prev_finished = prev_t + D.DELAYS[P.DONGLE]["buttons"]
        self.assertGreaterEqual(last_t - prev_finished, D.PROFILE_SETTLE[P.DONGLE] - 1e-9)

    def test_blocks_are_spaced(self):
        dev = D.Device("/dev/hidraw9", P.DONGLE)
        prof = config.default_profile()
        dev.write_performance(1, prof)
        dev.write_lighting(1, prof)
        writes = self.writes()
        first_light = next(t for cmd, t in writes if cmd == P.CMD_LIGHTING)
        last_perf = max(t for cmd, t in writes if cmd == P.CMD_PERFORMANCE)
        gap = first_light - (last_perf + D.DELAYS[P.DONGLE]["performance"])
        self.assertGreaterEqual(gap, D.BLOCK_GAP[P.DONGLE] - 1e-9)

    def test_first_write_does_not_wait(self):
        dev = D.Device("/dev/hidraw9", P.DONGLE)
        dev.activate_profile(2)
        self.assertEqual(self.log[0][0], "write")


class BatteryTest(unittest.TestCase):
    def test_bar_hidden_without_receiver(self):
        self.assertEqual(B.bar_output({"level": 50, "charging": False, "time": 0}, False)["text"], "")

    def test_bar_waiting_for_first_report(self):
        out = B.bar_output(None, True)
        self.assertEqual(out["text"], B.ICON)
        self.assertIn("waiting", out["tooltip"])

    def test_bar_level_charging_and_classes(self):
        now = 1000.0
        out = B.bar_output({"level": 47, "charging": False, "time": now - 10}, True, now)
        self.assertEqual(out["text"], f"{B.ICON} 47%")
        self.assertEqual(out["class"], [])
        self.assertEqual(out["tooltip"], "Model O 2: 47%")
        out = B.bar_output({"level": 12, "charging": False, "time": now - 1200}, True, now)
        self.assertEqual(out["class"], ["low", "active", "stale"])
        self.assertEqual(out["tooltip"], "Model O 2: 12% · 20 min ago")
        out = B.bar_output({"level": 12, "charging": True, "time": now}, True, now)
        self.assertEqual(out["text"], f"{B.ICON} 12% {B.CHARGING}")
        self.assertEqual(out["class"], [])

    def test_bar_stale_charging_hides_level(self):
        # Seen on hardware: no reports while on the cable, so the level froze at 3%.
        now = 10000.0
        out = B.bar_output({"level": 3, "charging": True, "time": now - 9000}, True, now)
        self.assertEqual(out["text"], f"{B.ICON} {B.CHARGING}")
        self.assertEqual(out["tooltip"], "Model O 2: charging (was 3% 2 h ago; no reports until unplugged)")
        self.assertNotIn("percentage", out)

    def test_low_alert_once_per_threshold(self):
        alert = B.LowBatteryAlert((15, 5))
        self.assertIsNone(alert.check(40, False))
        self.assertEqual(alert.check(15, False), 15)
        self.assertIsNone(alert.check(14, False))
        self.assertEqual(alert.check(5, False), 5)
        self.assertIsNone(alert.check(3, False))
        self.assertIsNone(alert.check(3, True))      # charging re-arms
        self.assertEqual(alert.check(15, False), 15)

    def test_low_alert_skipping_levels_reports_lowest(self):
        alert = B.LowBatteryAlert((15, 5))
        self.assertEqual(alert.check(4, False), 5)
        self.assertIsNone(alert.check(4, False))

    def test_record_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "o2ctl", "battery.json")
            self.assertIsNone(B.load(path))
            B.record({"event": "battery", "level": 87, "charging": True}, path, now=5.0)
            self.assertEqual(B.load(path), {"level": 87, "charging": True, "time": 5.0})
            with open(path, "w") as f:
                f.write("{broken")
            self.assertIsNone(B.load(path))

    def test_old_config_battery_key_dropped(self):
        state = config.normalise({"battery": {"level": 1, "charging": False, "time": 0}})
        self.assertNotIn("battery", state)


if __name__ == "__main__":
    unittest.main()
