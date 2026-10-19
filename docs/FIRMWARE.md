# Model O 2 Wireless firmware updates

Findings from reading Glorious CORE 2.1.3 (`ModelOV2FWUpdate`,
`ModelOV2USBDriver`, `FWUpdateService`, and the separate
`macos-updater/index.js`) and its firmware package. `o2ctl firmware update`
follows it. Updating the mouse over the cable is **verified** (0259 onto a
mouse already on 0259); updating the dongle hasn't been tried.

## What exists

The newest firmware Glorious publishes for this mouse is **0259**, dated 2023-10-23:

    https://gloriouscore.nyc3.digitaloceanspaces.com/Glorious_Core/1.0.0.5_v259_20231023_MOW_V2_Firmware.zip

It is listed in `Glorious_Core/Version.json` (CORE 1) and
`CORE2/version_he.json` (CORE 2). In the CORE 2 list, 55 of 56 devices have
their `SN` set to `temp_remove`, this mouse included, so CORE 2 no longer
offers firmware from that list. The package holds:

| file | size | for |
|------|------|-----|
| `USB/MOW_V2_FW0259_20231023_mouse.bin` | 86,632 | the mouse, flashed over the cable (`093a:822a`) |
| `Dongle/MOW_V2_FW0259_20231023_dongle.bin` | 52,224 | the dongle (`093a:822d`) |
| `*/FWUpdate.exe`, `*/Setting.xml` | | Windows updater and its settings (VID/PID, bin name, `FWSTRING` `_WAS_`) |

Both images are raw ARM Cortex-M images for a PixArt chip: a vector table
at offset 0, with flash at `0x10000000` (mouse reset vector `0x10000331`).
Neither is signed as far as the protocol shows: the updater only sends
16-bit byte-sum checksums.

The version is the USB `bcdDevice`: `0x0259` → "0259". Through the dongle
that is the dongle's version; the mouse's own version shows only over
the cable.

## How CORE flashes it

On Windows CORE runs the bundled `FWUpdate.exe` (a vendor tool). On macOS it
runs `macos-updater/index.js`, which uses the JavaScript flasher below with
the `.bin`. CORE updates the mouse (over USB) first, then the dongle.

Unlike settings writes, every step here **reads a reply**
(`HIDIOCGFEATURE`) and checks it. The dongle doesn't answer reads for the
mouse (see PROTOCOL.md), so an update can't reach the mouse through the
dongle.

### Packet

CORE builds 65-byte buffers, but feature report 3 is 64 bytes with the
report ID and CORE never uses the last byte, so o2ctl sends 64:

| byte | value |
|------|-------|
| 0 | `03` |
| 1 | low byte of the sum of bytes 2–64 |
| 2 | `65` |
| 3 | `3A` |
| 4 | sequence number (counts up from 0 per device, wraps at 256) |
| 5 | target (0) |
| 6 | command |
| 7 | second sequence number (counts up alongside byte 4) |
| 8 | length of bytes 9 onwards |
| 9 | command again |
| 10.. | arguments, little-endian |

Replies are read with `HIDIOCGFEATURE` (works over the cable, **verified**).
With the report ID removed, a reply is good when its byte 0 is `65`, byte 1
is `00` and byte 3 equals the current sequence number. CORE polls up to 1000
times.

| cmd | name | arguments |
|-----|------|-----------|
| `27` | write / prepare | total image size (u32) |
| `42` | init check | none; the reply carries the OTA parameters |
| `23` | get info | none; reply bytes 9–14 are the version string, 14–15 a checksum |
| `25` | create object | offset (u32), max object size (u32) |
| `40` | content | one chunk of the image (length in byte 8); no reply is read |
| `41` | CRC check | running 16-bit byte sum |
| `18` | upgrade | image size (u32), image byte sum (u16), 5-byte version string |
| reset | | the fixed packet `03 08 FA 06`, a plain route-`FA` command sent after `18` |

In the `42` reply (indexed as returned: `65`, status, …), bytes 12–13 are a
CRC, 14–17 the max object size (u32), 18–19 the MTU, i.e. the chunk size
(u16), and 20–21 a PRN threshold (u16); bytes 8–9 flag a "new flow". The
names match Nordic's DFU, but it runs on the PixArt chip.

The mouse answers (**verified**): version `5F 57 41 53 5F` ("_WAS_", the
`FWSTRING` in `Setting.xml`), max object size 4096, MTU 55 (the most that fits
in a report), PRN threshold 74.

### Sequence

Both sequence numbers start at 0. Before step 3 the first one restarts at 1
while the second keeps counting, and `25` puts the first one in both fields.

1. `27` with size 0, `42` (read OTA parameters), `23` (read current version).
2. Split the image into MTU-sized chunks that never cross a max-object boundary; pad the last one with zeros.
3. `27` with the real size, `42`, `25` at offset 0.
4. For each chunk: `40`. At every max-object boundary: `41` with the running sum, then `25` for the next object.
5. `41` with the final sum, `18`, wait 1 s, reset, wait 1 s.

At a boundary CORE sends `41` with the sum *before* the last chunk of the
object, then the chunk, then `41` with the sum after it, then `25`. The sum
runs over the whole image, not per object. `18` carries the version string
the mouse reported in step 1, not the one in `Setting.xml`.

## Risks

- A failed or wrong flash could leave the mouse unusable. For the Model O 2
  Wireless, CORE's device list defines no bootloader VID/PID to recover with
  (`vid_BT`/`pid_BT` are unset), so there is no known recovery path. The
  PixArt chip may have a ROM bootloader, but that is unconfirmed.
- Only the mouse over the cable has been flashed. The dongle uses the same
  commands in CORE's code, but nothing has been sent to it.
- There is only 0259 to install. A mouse already on 0259 gains nothing new,
  though reinstalling it works.
