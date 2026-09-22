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
    P.DONGLE: {"profile": 0.15, "performance": 0.15, "lighting": 0.05, "buttons": 0.15},
    P.WIRED: {"profile": 0.10, "performance": 0.15, "lighting": 0.10, "buttons": 0.10},
}

# Quiet time the dongle needs before the next write. It relays packets over the
# radio after the ioctl returns; a profile switch sent while it is still busy is
# dropped (seen on hardware: "write all" ended on profile 3's lighting).
BLOCK_GAP = {P.DONGLE: 0.3, P.WIRED: 0.05}
PROFILE_SETTLE = {P.DONGLE: 1.0, P.WIRED: 0.2}

UDEV_HINT = (
    "No permission to open {path}. Install the udev rule:\n"
    "  sudo install -m644 data/70-glorious-model-o2.rules /etc/udev/rules.d/\n"
    "  sudo udevadm control --reload && sudo udevadm trigger"
)


def _ioc(direction, number, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | number


HIDIOCSFEATURE = _ioc(3, 0x06, P.PACKET_SIZE)


class DeviceError(RuntimeError):
    pass


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
        return {P.DONGLE: "2.4 GHz dongle", P.WIRED: "USB cable"}[self.transport]

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
        with _write_lock():
            wait = self._last_write + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            fd = self._open(os.O_RDWR)
            try:
                for packet in packets:
                    if len(packet) != P.PACKET_SIZE:
                        raise DeviceError(f"packet is {len(packet)} bytes, expected {P.PACKET_SIZE}")
                    try:
                        fcntl.ioctl(fd, HIDIOCSFEATURE, bytes(packet))
                    except OSError as e:
                        if e.errno in (errno.ENODEV, errno.ENOENT):
                            raise DeviceError("The mouse was disconnected.") from None
                        if e.errno in (errno.EPIPE, errno.ETIMEDOUT):
                            raise DeviceError(
                                "The dongle did not accept the command. Is the mouse switched on?"
                            ) from None
                        raise DeviceError(f"Write failed: {e.strerror}") from None
                    time.sleep(delay)
            finally:
                os.close(fd)
                self._last_write = time.monotonic()

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


def _is_config_interface(descriptor):
    # Vendor page 0xFF00, usage 1, holding feature report 3.
    return descriptor is not None and descriptor.startswith(bytes.fromhex("0600ff0901a101")) and b"\x85\x03" in descriptor


def find_devices(sysfs="/sys/class/hidraw"):
    """Every connected Model O 2 Wireless config interface, cable first."""
    found = []
    for node in sorted(glob.glob(os.path.join(sysfs, "hidraw*"))):
        uevent = (_read(os.path.join(node, "device", "uevent")) or b"").decode(errors="replace")
        fields = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
        try:
            bus, vid, pid = (int(x, 16) for x in fields.get("HID_ID", "").split(":"))
        except ValueError:
            continue
        if bus != 0x03 or vid != P.VID or pid not in (P.PID_DONGLE, P.PID_WIRED):
            continue
        if not _is_config_interface(_read(os.path.join(node, "device", "report_descriptor"))):
            continue
        usb_dev = os.path.realpath(os.path.join(node, "device", "..", ".."))
        bcd = (_read(os.path.join(usb_dev, "bcdDevice")) or b"").decode().strip() or None
        transport = P.DONGLE if pid == P.PID_DONGLE else P.WIRED
        found.append(Device("/dev/" + os.path.basename(node), transport, bcd))
    # The dongle stays enumerated when the mouse is off or cabled, so a
    # wired connection wins when both are present.
    found.sort(key=lambda d: d.transport != P.WIRED)
    return found


def bluetooth_connected(sysfs="/sys/class/hidraw"):
    for node in glob.glob(os.path.join(sysfs, "hidraw*")):
        uevent = (_read(os.path.join(node, "device", "uevent")) or b"").decode(errors="replace")
        if f"{P.VID:08X}:{P.PID_BLUETOOTH:08X}" in uevent.upper():
            return True
    return False


def find_device():
    devices = find_devices()
    return devices[0] if devices else None
