# To do

## Bluetooth support (before a public release)

The mouse keeps its settings onboard, so it works over Bluetooth after being
set up through the dongle or cable. What o2ctl can't do over Bluetooth
(`093a:822b`) yet:

- **Battery in the bar.** First check whether the mouse reports battery through
  the standard Bluetooth battery service: pair it and run `upower -d`. If it
  does, `o2ctl battery --bar` can read it from UPower with no protocol work.
- **Settings.** Bluetooth uses report 5 with 21-byte packets instead of feature
  report 3 (`docs/PROTOCOL.md`). Work out the framing from Glorious CORE's
  `MouseV2DeviceHandler`, then test each command on the mouse over Bluetooth.
