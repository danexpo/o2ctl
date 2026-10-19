"""Finding the mouse's vendor hidraw node and talking to it."""

import errno
import fcntl
import glob
import os
import select
import time
from contextlib import contextmanager

from . import protocol as P

# Pauses after each packet, from Glorious CORE's send loop.
DELAYS = {
    P.DONGLE: {"profile": 0.15, "performance": 0.15, "lighting": 0.05, "buttons": 0.15, "macro": 0.03,
               "sleep": 0.03},
    P.WIRED: {"profile": 0.10, "performance": 0.15, "lighting": 0.10, "buttons": 0.10, "macro": 0.03,
              "sleep": 0.03},
    # CORE waits 30 ms after every Bluetooth packet.
    P.BLUETOOTH: {"profile": 0.03, "performance": 0.03, "lighting": 0.03, "buttons": 0.03, "macro": 0.03,
                  "sleep": 0.03},
}

# Quiet time the dongle needs before the next write. It relays packets over the
# radio after the ioctl returns; a profile switch sent while it is still busy is
# dropped (seen on hardware: "write all" ended on profile 3's lighting).
BLOCK_GAP = {P.DONGLE: 0.3, P.WIRED: 0.05, P.BLUETOOTH: 0.1}
PROFILE_SETTLE = {P.DONGLE: 1.0, P.WIRED: 0.2, P.BLUETOOTH: 0.3}

UDEV_HINT = (
    "No permission to open {path}. Install the udev rule:\n"
    "  sudo install -m644 data/70-glorious-model-o2.rules /etc/udev/rules.d/\n"
    "  sudo udevadm control --reload && sudo udevadm trigger"
)


def _ioc(direction, number, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | number


def HIDIOCSFEATURE(size):
    return _ioc(3, 0x06, size)


def HIDIOCGFEATURE(size):
    return _ioc(3, 0x07, size)


class DeviceError(RuntimeError):
    pass


def _ioctl_error(e):
    if e.errno in (errno.ENODEV, errno.ENOENT):
        return DeviceError("The mouse was disconnected.")
    if e.errno in (errno.EPIPE, errno.ETIMEDOUT):
        return DeviceError("The dongle did not accept the command. Is the mouse switched on?")
    return DeviceError(f"Write failed: {e.strerror}")


def _set_feature(fd, packet, size):
    if len(packet) != size:
        raise DeviceError(f"packet is {len(packet)} bytes, expected {size}")
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE(size), bytes(packet))
    except OSError as e:
        raise _ioctl_error(e) from None


class RawHid:
    """Feature reports in both directions on one open node."""

    def __init__(self, fd, report_id=P.REPORT_ID, size=P.PACKET_SIZE):
        self.fd = fd
        self.report_id = report_id
        self.size = size

    def set_feature(self, packet):
        _set_feature(self.fd, packet, self.size)

    def get_feature(self, report_id=None):
        """Read a feature report; returns it without the report ID."""
        buf = bytearray(self.size)
        buf[0] = self.report_id if report_id is None else report_id
        try:
            fcntl.ioctl(self.fd, HIDIOCGFEATURE(self.size), buf, True)
        except OSError as e:
            raise _ioctl_error(e) from None
        return bytes(buf[1:])


class Device:
    def __init__(self, path, transport, firmware=None):
        self.path = path
        self.transport = transport
        self.firmware = firmware
        self._last_write = 0.0  # monotonic time the previous send finished

    def __repr__(self):
        return f"Device({self.path!r}, {self.transport!r}, firmware={self.firmware!r})"

    @property
    def label(self):
        return {P.DONGLE: "2.4 GHz dongle", P.WIRED: "USB cable", P.BLUETOOTH: "Bluetooth"}[self.transport]

    def _open(self, flags):
        try:
            return os.open(self.path, flags)
        except PermissionError:
            raise DeviceError(UDEV_HINT.format(path=self.path)) from None
        except FileNotFoundError:
            raise DeviceError("The mouse was disconnected.") from None

    def send(self, packets, delay, gap=None):
        """Write feature reports, first waiting `gap` seconds after the previous send."""
        if gap is None:
            gap = BLOCK_GAP[self.transport]
        _, size = P.report_format(self.transport)
        with _write_lock():
            wait = self._last_write + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            fd = self._open(os.O_RDWR)
            try:
                for packet in packets:
                    _set_feature(fd, packet, size)
                    time.sleep(delay)
            finally:
                os.close(fd)
                self._last_write = time.monotonic()

    @contextmanager
    def raw(self):
        """Hold the node open, and other writers off, for a long exchange."""
        with _write_lock():
            fd = self._open(os.O_RDWR)
            try:
                yield RawHid(fd, *P.report_format(self.transport))
            finally:
                os.close(fd)
                self._last_write = time.monotonic()

    def query_battery(self, tries=3):
        """Ask the mouse for its battery level, as CORE 1 does: write, wait, read.

        Only worth doing over the cable or Bluetooth. The dongle answers too,
        but with the last level it heard, even when the mouse is off or on
        the cable.
        """
        packet = P.battery_query_packet(self.transport)
        with self.raw() as hid:
            for _ in range(tries):
                hid.set_feature(packet)
                time.sleep(0.05)
                reading = P.parse_battery_reply(hid.get_feature())
                if reading:
                    return reading
                time.sleep(0.05)
        return None

    def activate_profile(self, number):
        self.send(P.profile_packet(number, self.transport), DELAYS[self.transport]["profile"],
                  gap=PROFILE_SETTLE[self.transport])

    def write_performance(self, number, profile):
        packets = P.performance_packets(number, profile["performance"], self.transport)
        self.send(packets, DELAYS[self.transport]["performance"])

    def write_lighting(self, number, profile):
        packets = P.lighting_packets(number, profile["lighting"], self.transport)
        self.send(packets, DELAYS[self.transport]["lighting"])

    def write_buttons(self, number, profile):
        packets = P.buttons_packets(number, profile["buttons"], self.transport)
        self.send(packets, DELAYS[self.transport]["buttons"])

    def write_macro(self, macro):
        """Store one saved macro ({"id", "steps"}) on the mouse."""
        packets = P.macro_packets(macro["id"], macro["steps"], self.transport)
        self.send(packets, DELAYS[self.transport]["macro"])

    def write_sleep(self, minutes):
        self.send(P.sleep_packet(minutes, self.transport), DELAYS[self.transport]["sleep"])

    def write_profile(self, number, profile):
        self.write_performance(number, profile)
        self.write_lighting(number, profile)
        self.write_buttons(number, profile)

    def events(self, timeout=None, stop=None):
        """Yield decoded status events until `timeout` seconds pass or `stop()` is true.

        The vendor node also carries every mouse movement report, so this
        reads a lot of data while the mouse moves and yields only the rare
        status reports.
        """
        fd = self._open(os.O_RDONLY | os.O_NONBLOCK)
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                if stop is not None and stop():
                    return
                wait = 0.5
                if deadline is not None:
                    wait = min(wait, deadline - time.monotonic())
                    if wait <= 0:
                        return
                if not select.select([fd], [], [], wait)[0]:
                    continue
                try:
                    report = os.read(fd, P.PACKET_SIZE)
                except OSError as e:
                    if e.errno == errno.EAGAIN:
                        continue
                    raise DeviceError("The mouse was disconnected.") from None
                if report and report[0] != 1:
                    event = P.parse_event(report)
                    if event:
                        yield event
        finally:
            os.close(fd)


@contextmanager
def _write_lock():
    """Keep the GUI and the CLI from interleaving multi-packet writes."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    with open(os.path.join(runtime, "o2ctl.lock"), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


VENDOR_COLLECTION = bytes.fromhex("0600ff0901a101")  # usage page 0xFF00, usage 1


def _is_config_interface(descriptor, bus=0x03):
    if descriptor is None:
        return False
    if bus == 0x05:
        # Bluetooth puts every collection on one node; the vendor one holds report 5.
        at = descriptor.find(VENDOR_COLLECTION)
        return at >= 0 and b"\x85\x05" in descriptor[at:]
    # USB: interface 1 starts with the vendor collection, holding report 3.
    return descriptor.startswith(VENDOR_COLLECTION) and b"\x85\x03" in descriptor


TRANSPORT_OF = {(0x03, P.PID_DONGLE): P.DONGLE, (0x03, P.PID_WIRED): P.WIRED,
                (0x05, P.PID_BLUETOOTH): P.BLUETOOTH}
# The dongle stays enumerated when the mouse is off, cabled or on Bluetooth,
# so it comes last.
PREFERENCE = (P.WIRED, P.BLUETOOTH, P.DONGLE)


def find_devices(sysfs="/sys/class/hidraw"):
    """Every connected Model O 2 Wireless config interface, cable first, dongle last."""
    found = []
    for node in sorted(glob.glob(os.path.join(sysfs, "hidraw*"))):
        uevent = (_read(os.path.join(node, "device", "uevent")) or b"").decode(errors="replace")
        fields = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
        try:
            bus, vid, pid = (int(x, 16) for x in fields.get("HID_ID", "").split(":"))
        except ValueError:
            continue
        transport = TRANSPORT_OF.get((bus, pid))
        if vid != P.VID or transport is None:
            continue
        if not _is_config_interface(_read(os.path.join(node, "device", "report_descriptor")), bus):
            continue
        bcd = None
        if bus == 0x03:  # Bluetooth has no USB device to read the version from
            usb_dev = os.path.realpath(os.path.join(node, "device", "..", ".."))
            bcd = (_read(os.path.join(usb_dev, "bcdDevice")) or b"").decode().strip() or None
        found.append(Device("/dev/" + os.path.basename(node), transport, bcd))
    found.sort(key=lambda d: PREFERENCE.index(d.transport))
    return found


def find_device():
    devices = find_devices()
    return devices[0] if devices else None
