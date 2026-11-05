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
python configurator.py <child number>
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python configurator.py <child number>
```

Install the necessary USB serial driver and permissions on your PC. The menu
lists available serial ports: select the intended device explicitly.

## Devices, files, and channels

Each child *n* owns a **pair** of devices, configured by two files:

- `<n>-handheld.json` — the device **in the child's hand** (no GPS). The kid
  takes this one home.
- `<n>-beacon.json` — the **GPS beacon** (position source).

| Channel | Key | Stored in | beacon (GPS) | handheld (child) |
|---|---|---|---|---|
| **track** (primary) | unique per pair | both pair files | idx 0, position precision 32 | idx 0, precision 0 |
| **family** | unique per family, auto-generated | `<n>-handheld.json` | — | idx 1 |
| **group** | one workshop-wide key | `shared.json` only | — | idx 2 |
| **public** (optional) | well-known default key | network-wide switch | idx 1 if on | idx 3 if on |

`local/` lives **beside the script** (never the current directory):

- `shared.json` — `group_name`, `group_key` (generate **once** for the whole
  workshop), `track_name`, `frequency_slot` (default 1), `public_channel`
  (default false). These are network-wide: a device whose slot differs from
  the others hears nobody.
- `shared_messages.json` — canned message sets, keyed by a short name
  (`{"workshop": "Hi|Help|Found you"}`). Edit with a text editor; the menu
  only selects a set by key.
- `<n>-*.json` — the pair's properties: names, Bluetooth, canned set key,
  and the pair's private `track_key` (identical in both files, generated
  automatically when the first file is created; the sibling inherits it).
  The handheld file additionally holds the auto-generated `family_key`.

First run for a missing file asks names, Bluetooth and an optional canned
set key, then stores the JSON. Prepare all files in advance — on the
workshop PC you then only connect each device and apply.

## Menu

1. **Create group key (once)** — stored in `shared.json` before use.
2. **Configure channel** — pick track / family / group / public (valid
   options depend on the device kind).
3. **Export track QR** — primary-only QR of the connected device (contains
   the pair secret).
4. **Export family QR** — built offline from `<n>-handheld.json` for the
   parents' app; no device needed. (Handheld files only.)
5. **Change name** 6. **Bluetooth on/off** 7. **Canned messages selector** —
   these also update `<n>-*.json`, so it stays the source of truth.
8. **Radio/rotary profile** — EU_868, LONG_FAST, the shared `frequency_slot`,
   rotary pins and events. Apply to **every** device.
9. **Enter DFU mode** — reboots into the bootloader so a new `.uf2` can be
   copied onto the MESHBOOT drive; kids can update firmware without the
   provisioning tool.
10. **Apply all (this device)** — profile, channels (by kind), public channel
    if enabled, name, Bluetooth, canned set — read-back verified per step.
    On success the board's USB serial number is stored in the device file.
11. **Apply all (pair, auto-detect)** — plug both devices of the pair into
    one PC and run this once: each file is matched to its board by USB
    serial and applied automatically. The very first time (or when a board
    is new/replaced) the tool asks which port is which, records the
    binding, and every later run needs no questions. You answer by list
    number — the serial shown is informational. Easiest: plug one board at
    a time; a single unclaimed port is assigned without asking. The port
    list is a snapshot — do not swap boards while the prompt is open;
    rerun the menu instead. A wrong first-time assignment is fixed by
    deleting the `usb_serial` field from the two files and re-running.
0. Exit.

Every write reconnects and reads back; a mismatch is reported as an error but
may require inspecting a partially applied configuration.

## Frequency slots and the public channel (why it is a network-wide switch)

A device tunes **one** radio frequency: the slot comes from the primary
channel (or `frequency_slot`). *All* channels — track, family, group, public
— transmit on that same frequency; channels are virtual labels, not
frequencies. A node retransmits a packet only if it can decode it: same
frequency **and** a channel with matching name+key. Foreign stock devices sit
on their own LongFast slot, so they hear nothing of ours today. To
experiment with them, set `public_channel: true` and `frequency_slot` to the
stock LongFast slot in `shared.json`, then Apply all to every device; the
public secondary (empty name, default key) then decodes exactly like their
primary, while track/family/group keep working unchanged.

Private channels are flooded only by nodes holding their key: group traffic
is relayed by the whole class, track traffic only by the pair itself.

Rotating a key disconnects devices still using it: re-apply afterward. Keep
`local/` and exported QRs private; back them up so keys are not lost. Never
commit or share the keys by accident.

Run offline tests with `python -m unittest -v`.
