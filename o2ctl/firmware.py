"""Firmware updates, following Glorious CORE 2.1.3 (ModelOV2USBDriver and
ModelOV2FWUpdate, the flasher its macOS updater runs). docs/FIRMWARE.md has
the protocol.

The mouse is flashed over the USB cable and the dongle on its own. Unlike
settings writes, every command except image chunks reads a reply back and
checks it before going on.
"""

import hashlib
import io
import os
import time
import urllib.request
import zipfile

from . import protocol as P

FIRMWARE_URL = ("https://gloriouscore.nyc3.digitaloceanspaces.com/Glorious_Core/"
                "1.0.0.5_v259_20231023_MOW_V2_Firmware.zip")
FIRMWARE_ZIP_SHA256 = "83166b46f994770275a9664361867dacfeb7d414a99537954b161647ed09bfb0"
LATEST = "0259"

# sha256 of the published images -> (transport that flashes it, version)
KNOWN_IMAGES = {
    "ecef6e634371e5412d81516128bb905d646400ef183b264f0a966c8bcf50002a": (P.WIRED, "0259"),
    "ecc1a7fefaf9aa6efaafb7c169f1db8ce297914bf88f4ad5414f83d4bd924d57": (P.DONGLE, "0259"),
}
# Folder of each image in Glorious' zip.
ZIP_FOLDER = {P.WIRED: "USB", P.DONGLE: "Dongle"}

CMD_PREPARE = 0x27
CMD_INIT = 0x42
CMD_OBJECT = 0x25
CMD_UPGRADE = 0x18
CMD_CONTENT = 0x40
CMD_INFO = 0x23
CMD_CRC = 0x41

# CORE's buffers are 65 bytes, one more than feature report 3; the last
# byte is always zero, so the report is sent without it.
FRAME_SIZE = 0x41
MAX_CHUNK = P.PACKET_SIZE - 9
# Pause after each command (CORE's iSleep), in seconds.
SLEEP = {CMD_PREPARE: 0.010, CMD_INIT: 0.050, CMD_OBJECT: 0.050, CMD_UPGRADE: 0.050,
         CMD_CONTENT: 0.005, CMD_INFO: 0.010, CMD_CRC: 0.001}
POLLS = 1000
REPLY_MARK = 0x65
RESET = bytes([0x03, 0x08, 0xFA, 0x06]) + bytes(P.PACKET_SIZE - 4)


class FirmwareError(RuntimeError):
    pass


def _le(value, size):
    return value.to_bytes(size, "little")


def command(seq, seq2, cmd, length, args=b"", target=0):
    """One updater packet: 03, checksum, 65 3A, seq, target, cmd, seq2, length, payload."""
    if len(args) > MAX_CHUNK:
        raise FirmwareError("packet does not fit in a feature report")
    buf = bytearray(FRAME_SIZE)
    buf[2:8] = bytes([REPLY_MARK, 0x3A, seq, target, cmd, seq2])
    buf[8] = length
    buf[9 : 9 + len(args)] = args
    buf[1] = sum(buf[2:]) & 0xFF
    buf[0] = P.REPORT_ID
    return bytes(buf[: P.PACKET_SIZE])


def reply_ok(reply, seq):
    """A reply (report ID removed) is good when it reads 65, status 00, ?, seq."""
    return len(reply) > 3 and reply[0] == REPLY_MARK and reply[1] == 0 and reply[3] == seq


def parse_ota(reply):
    """OTA parameters from the reply to 42."""
    return {
        "new_flow": reply[8] != 0x00 or reply[9] != 0x01,
        "crc": int.from_bytes(reply[12:14], "little"),
        "max_object": int.from_bytes(reply[14:18], "little"),
        "mtu": int.from_bytes(reply[18:20], "little"),
        "prn": int.from_bytes(reply[20:22], "little"),
        "spec_check": reply[22],
    }


def parse_info(reply):
    """(version bytes, checksum) from the reply to 23."""
    return bytes(reply[9:15]), int.from_bytes(reply[14:16], "little")


def version_field(version):
    # CORE decodes the reply bytes as char codes and re-encodes them as UTF-8.
    raw = bytes(version).decode("latin-1").encode("utf-8")[:5]
    return raw + bytes(5 - len(raw))


def chunks(image, mtu, max_object):
    """Split the image into `mtu`-byte chunks that never cross a `max_object`
    boundary; the last one is padded with zeros."""
    out = []
    size, filled, at = mtu, 0, 0
    while at < len(image):
        data = image[at : at + size]
        out.append(data + bytes(size - len(data)))
        at += size
        filled += size
        if max_object - filled >= mtu:
            size = mtu
        elif max_object - filled <= 0:
            size, filled = mtu, 0
        else:
            size = max_object - filled
    return out


def image_info(image):
    """(transport, version) for a published image, else None."""
    return KNOWN_IMAGES.get(hashlib.sha256(image).hexdigest())


def load_image(path, transport):
    """The image for `transport` from a .bin, or from Glorious' zip."""
    with open(path, "rb") as f:
        data = f.read()
    if not zipfile.is_zipfile(io.BytesIO(data)):
        return data
    folder = ZIP_FOLDER[transport]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist()
                 if n.lower().endswith(".bin") and f"/{folder.lower()}/" in "/" + n.lower()]
        if len(names) != 1:
            raise FirmwareError(f"expected one .bin in the {folder} folder of {path}, found {len(names)}")
        return z.read(names[0])


def cache_dir():
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "o2ctl")


def download(directory=None):
    """Glorious' firmware zip, downloaded once and checked."""
    directory = directory or cache_dir()
    path = os.path.join(directory, os.path.basename(FIRMWARE_URL))
    if not os.path.exists(path):
        os.makedirs(directory, exist_ok=True)
        with urllib.request.urlopen(FIRMWARE_URL, timeout=60) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != FIRMWARE_ZIP_SHA256:
            raise FirmwareError(f"{FIRMWARE_URL} did not match the expected checksum")
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
    return path


def official_image(transport):
    """Glorious' latest image for the mouse (WIRED) or the dongle."""
    image = load_image(download(), transport)
    if image_info(image) != (transport, LATEST):
        raise FirmwareError("the downloaded firmware is not the expected image")
    return image


def flash(dev, image, progress=None):
    """Flash `image` onto `dev` (a device.Device), explaining how far it got on failure."""
    flasher = Flasher(None, progress)
    try:
        with dev.raw() as hid:
            flasher.hid = hid
            flasher.update(image)
    except RuntimeError as e:  # FirmwareError or device.DeviceError
        if flasher.writing:
            raise FirmwareError(f"{e}. The update stopped partway. Leave it plugged in and "
                                "update again.") from None
        raise FirmwareError(f"{e}. Nothing was written; the firmware is unchanged.") from None


class Flasher:
    """Runs CORE's update sequence over `hid`, which has set_feature(packet)
    and get_feature() -> reply without the report ID."""

    def __init__(self, hid, progress=None, sleep=None):
        self.hid = hid
        self.progress = progress or (lambda percent: None)
        self.sleep = sleep or time.sleep
        self.seq = self.seq2 = 0
        self.ota = None
        self.version = None
        self.writing = False  # true once image data has gone out

    def _exchange(self, cmd, length, args=b"", seq2=None, reply=True):
        packet = command(self.seq, self.seq2 if seq2 is None else seq2, cmd, length, args)
        self.hid.set_feature(packet)
        self.sleep(SLEEP[cmd])
        answer = None
        if reply:
            for _ in range(POLLS):
                answer = self.hid.get_feature()
                self.sleep(SLEEP[cmd])
                if reply_ok(answer, self.seq):
                    break
            else:
                raise FirmwareError(f"no valid reply to command {cmd:02x}")
        self.seq = (self.seq + 1) & 0xFF
        self.seq2 = (self.seq2 + 1) & 0xFF
        return answer

    def _prepare(self, size):
        self._exchange(CMD_PREPARE, 0x06, bytes([CMD_PREPARE]) + _le(size, 4))

    def _init(self):
        return self._exchange(CMD_INIT, 0x02, bytes([CMD_INIT]))

    def _object(self, offset):
        # CORE puts the first sequence number in both fields here.
        self._exchange(CMD_OBJECT, 0x09, bytes([CMD_OBJECT]) + _le(offset, 4)
                       + _le(self.ota["max_object"], 4), seq2=self.seq)

    def _crc(self, total):
        self._exchange(CMD_CRC, 0x03, bytes([CMD_CRC]) + _le(total, 2))

    def check(self):
        """Ask for the OTA parameters and the current version (CORE's first step)."""
        self.seq = self.seq2 = 0
        self._prepare(0)
        self.ota = parse_ota(self._init())
        if not 0 < self.ota["mtu"] <= MAX_CHUNK or self.ota["max_object"] < self.ota["mtu"]:
            raise FirmwareError(f"unexpected update parameters: {self.ota}")
        self.version, _ = parse_info(self._exchange(CMD_INFO, 0x02, bytes([CMD_INFO])))
        return self.ota, self.version

    def update(self, image):
        self.check()
        pieces = chunks(image, self.ota["mtu"], self.ota["max_object"])
        max_object = self.ota["max_object"]
        self.seq = 1  # CORE restarts only the first counter here
        self._prepare(len(image))
        self._init()
        self._object(0)
        total = offset = 0
        shown = -1
        self.writing = True
        for i, piece in enumerate(pieces):
            offset += len(piece)
            boundary = offset % max_object == 0
            if boundary and total:
                self._crc(total)
                self.sleep(0.05)
            total = (total + sum(piece)) & 0xFFFF
            self._exchange(CMD_CONTENT, len(piece), piece, reply=False)
            if boundary:
                self._crc(total)
                self.sleep(0.05)
                self._object(offset)
            percent = 100 * (i + 1) // len(pieces)
            if percent != shown:
                self.progress(percent)
                shown = percent
        self._crc(total)
        self._exchange(CMD_UPGRADE, 0x0C, bytes([CMD_UPGRADE]) + _le(len(image), 4)
                       + _le(sum(image) & 0xFFFF, 2) + version_field(self.version))
        self.sleep(1)
        try:
            self.hid.set_feature(RESET)
        except RuntimeError:
            pass  # it may restart before the write returns; CORE ignores the result too
        self.sleep(1)
