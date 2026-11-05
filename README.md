# LoRa tracker family configurator

Standalone Python menu for configuring Meshtastic radios over USB. No firmware
source checkout is required. Requires Python 3.9 or newer and a working USB
connection; Windows testing is still pending.

## Install

Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python configurator.py
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python configurator.py
```

Install the necessary USB serial driver and permissions on your PC. The menu
lists available serial ports: select the intended device explicitly.

## Device indexes and files

Configuration lives in `local/` **beside the script** (never the current
directory, so the same keys are found no matter where you launch it):

- `local/shared.json` — the two channel secrets and their names. **Generate
  the keys once** (menu 1); every device uses the same ones. The group
  channel is the shared one: the family channel at home, the class channel at
  the workshop. Edit `pair_name` / `group_name` here to rename channels.
- `local/<n>.json` — one device's properties: role (tracker or child), names,
  Bluetooth on/off, canned messages. The index is just a file name; kids get
  1..n at the workshop, the tracker is 0, and running
  `python configurator.py` without an argument selects 0.

First run for an index asks role, names, Bluetooth and an optional canned
message file, then stores `local/<n>.json`. Prepare all the JSON files in
advance — on the workshop PC you then only connect each device and apply.

## Menu

1. **Create keys (pair + group, once)** — stored in `shared.json` before use.
2. **Configure channel** — applies the pair channel (index 0; full position
   precision on the tracker) or, on child devices, the group channel
   (index 1).
3. **Export pair QR** — pair-only QR of the connected device to
   `local/pair-qr.png` (contains the channel secret).
4. **Change name** 5. **Bluetooth on/off** 6. **Canned messages** — these also
   update `<n>.json`, so it stays the source of truth.
7. **Radio/rotary profile** — EU_868, LONG_FAST, slot 1, rotary pins and
   events. Apply to **every** device: devices with different slots cannot hear
   each other. Mind local radio rules; attach the antenna first.
8. **Enter DFU mode** — reboots the device into the bootloader so a new
   `.uf2` can be copied onto the MESHBOOT drive; kids can update firmware
   without the provisioning tool.
9. **Apply all** — applies profile, channels (by role), name, Bluetooth and
   canned messages from the JSON files, with read-back verification per step.
   This is the workshop path: one pass per device.
0. Exit.

Every write reconnects and reads back; a mismatch is reported as an error but
may require inspecting a partially applied configuration.

Rotating a key disconnects devices still using the old key: re-apply to every
device afterward. Keep `local/` and the QR image private; copy it somewhere
safe so the keys are not lost. Never commit or share the keys by accident.

Run offline tests with `python -m unittest -v`.
