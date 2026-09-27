# Model O 2 Wireless protocol

This comes from reading Glorious CORE 2.1.21 (`resources/app.asar`,
`out/main/index.js`: `MouseV2DeviceHandler`, `PreparePerformanceBuffers2`,
`PreparePresetEffectBuffers`, `PrepareKeybindingBuffers2`,
`PrepareProfileUpdateBuffer2`). The macro format comes from CORE 2.1.3
(`ModelOV2Series.SetMacroDataToDevice` and `MacroToData`; the 2.1.21 installer
couldn't be unpacked on Linux). Anything marked **verified** was confirmed
on a real mouse through the dongle.

## Transport

| PID    | Link         | Route byte |
|--------|--------------|------------|
| 0x822D | 2.4 GHz dongle | `FB`     |
| 0x822A | USB cable    | `FA`       |
| 0x822B | Bluetooth    | report 5, 21 bytes (not implemented) |

USB interface 1 is the one to use. Its report descriptor has vendor page
`FF00` usage 1 with feature report 3 (63 bytes + ID), plus the mouse,
consumer, system and vendor-input (report 6) collections.

- **Commands** are `HIDIOCSFEATURE` with 64 bytes, `[03, cmd, route, ...]`.
- **Reads** (`HIDIOCGFEATURE`) time out on the dongle (**verified**). CORE never reads settings back either.
- **Firmware updates** are described in FIRMWARE.md; o2ctl doesn't do them.
- **Firmware** is the USB `bcdDevice` (`0x0259` → "0259"), which is what CORE's native `GetFWVersion` returns.

| cmd | Meaning |
|-----|---------|
| 01  | activate profile |
| 02  | lighting |
| 03  | button bindings |
| 04  | performance (DPI, polling, LOD, debounce, motion sync) |
| 05  | macro data |

Profiles are numbered 1–3 on the wire.

## 01 activate profile

`03 01 <route> <profile>`. The mouse sends no event when the host switches profile (**verified**).

## 04 performance

The block, before chunking:

| byte | value |
|------|-------|
| 0 | selected stage (0-based) |
| 1 | stage count (1–6) |
| 2 | lift-off, mm (1/2) |
| 3 | debounce, ms |
| 4 | polling: 1=125, 2=250, 3=500, 4=1000 Hz |
| 5 | motion sync 0/1 |
| 6+5n (dongle) or 12+8n (cable) | stage n: `dpi/50` as int16 LE, then R G B |

- **Dongle:** 4 packets `03 04 FB <chunk> <profile>` + block[0:11], [11:21], [21:31], [31:41], each at byte 5.
- **Cable:** one packet `03 04 FA <profile>` + block[0:60] at byte 4.

## 02 lighting

Effect ids: 0 off, 1 Glorious, 2 seamless breathing, 3 breathing (up to 6
colours), 4 single colour, 5 breathing single colour, 6 tail, 7 rave (2
colours), 8 wave.

The block:

| byte | value |
|------|-------|
| 0 | speed |
| 1 | brightness, wired |
| 2 | colour count |
| 3 | speed |
| 4 | brightness, wireless |
| 5.. (dongle) or 11.. (cable) | RGB triplets |

- **Levels** are 0–20. CORE maps 0–100 % with `v > 4 ? v*20/100 : 1` (brightness 0 stays 0).
- **Dongle:** 3 packets `03 02 FB <chunk> <profile> <effect>` + block[0:8], [8:17], [17:26], each at byte 6 (**verified**: `03 02 fb 00 01 04 0c 14 01 0c 14 00 ff 00` gives solid green).
- **Cable:** `03 02 FA <profile> <effect>` + block[0:29] at byte 5.

## 03 buttons

The table has 4 bytes per slot. Slots: 0 left, 1 right, 2 middle, 3 back,
4 forward, 5 DPI, 6 wheel up, 7 wheel down. Slots 6 and 7 only exist over
the cable.

| slot value | action |
|------------|--------|
| `01 xx 00 00` | mouse: 1 left, 2 right, 3 middle, 4 back, 5 forward, A0/A1 wheel, B0/B1 profile ±. |
| `66 xx lo hi` | DPI: 1 up, 2 down, 3 cycle, 4 cycle reverse. `5` is DPI shift, where `lo hi` = dpi/50. |
| `02 mm kk 00` | key: HID usage `kk`, modifiers `mm` (1 ctrl, 2 shift, 4 alt, 8 super). |
| `03 lo hi 00` | consumer usage, e.g. `03 cd 00` play/pause. |
| `77 00 00 00` | show battery level. |
| `00 00 00 00` | disabled. |
| `1F+id mode 00 00` | macro `id` (1-based, so the first is `20`). Mode: `01` play once, `E0` repeat while held, `E1` repeat until pressed again. |

- **Dongle:** `03 03 FB <chunk> <profile>` + table[0:12] and table[12:24], each at byte 5.
- **Cable:** `03 03 FA <profile>` + table[0:36] at byte 8.

## 05 macros

Macros are stored by id, apart from the profiles; a button in any profile
plays one through its slot value above. Each step is 3 bytes:

| byte | value |
|------|-------|
| 0 | `80` if the step presses, `00` if it releases, OR the delay's high 7 bits |
| 1 | delay, low byte |
| 2 | key |

The delay is the wait in ms after the step (CORE sends 1 after the last one).
Keys are keyboard HID usages, modifiers included (`E0` left ctrl … `E7`
right super), and `B7` left, `B8` right, `B9` middle, `BA` back, `BB`
forward for mouse buttons.

**Verified** through the dongle: macro 1 = `o` down, `o` up, `2` down, `2` up,
20 ms apart, sent as `03 05 fb 00 00 02 04 80 14 12 00 14 12 80 14 1f` and
`03 05 fb 00 01 02 04 00 14 1f`, then bound with slot `20 01 00 00`, types `o2`.
The keyboard interface reports the steps 20 ms apart.
Also **verified**: `E1` (shift) comes out as the modifier bit, and mode `E0`
repeats while the button is held and stops on release. The wait after the
last step came out about twice as long as set (300 ms → ~600 ms between
repeats). CORE builds the steps in a 256-byte buffer, so a
macro holds at most 85 steps. CORE doesn't cap the number of macros; o2ctl
uses ids 1–16.

- **Dongle:** packets `03 05 FB <id-1> <chunk> <chunks> <steps>` with 3 steps (9 bytes) from byte 7.
- **Cable:** packets `03 05 FA <id-1> <chunk> <chunks> <steps> 00` with 18 steps (54 bytes) from byte 8.
- CORE waits 30 ms between packets, and 200 ms after the last one over the dongle.

## Events (input report 6 on the same interface)

| report | meaning |
|--------|---------|
| `06 FB <level> <charging>` | battery; sent about every 65 s over the dongle (**verified**: `06 fb 2f 00` = 47 %); after the first `charging` report none arrive until unplugged (seen 2026-09-27: 2.5 h silent, then 98 %) |
| `06 F6 <index>` | the mouse switched profile itself (0-based) |
| `06 F9 <≥80> <slot>` | button bound to a host shortcut was pressed |
| `04 F1 <1-3>` | profile change over the cable; `04 F1 A0` = reset to default |

## Timing (dongle)

Both **verified**:

- The dongle relays packets after the ioctl returns. A profile switch sent
  straight after a burst of writes is dropped. Wait about 1 s after the last
  write before `03 01`; o2ctl also leaves 0.3 s between settings blocks.
  CORE never hits this because it doesn't send writes back to back.
- The LEDs show the lighting that was written last, even when that packet
  targeted a profile that isn't active. Stored profiles are kept apart, and
  re-activating the active profile restores its lighting.
