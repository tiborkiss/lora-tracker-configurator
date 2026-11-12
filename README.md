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

Each child *n* owns **one or two** devices, configured by files:

- `<n>-handheld.json` — the device **in the child's hand**. The kid takes
  this one home. If the board has GPS, say **yes** to the GPS question at
  creation (or add `"gps": true` by hand): the device then shares its
  position on the track channel with full precision **and** keeps family
  and group — a **one-device** tracker that is its own beacon.
- `<n>-beacon.json` — the dedicated **GPS beacon** (position source). Only
  for kids who get two devices; a one-device child never creates this file.

| Channel | Key | Stored in | beacon (GPS) | handheld (child) |
|---|---|---|---|---|
| **track** (primary) | unique per pair | both pair files | idx 0, position precision 32 | idx 0, precision 0 — **32 with `"gps": true`** |
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
  only selects a set by key. **Every device file must reference one set** —
  with no set the rotary cannot send messages.
- `<n>-*.json` — the pair's properties: names, Bluetooth, canned set key,
  and the pair's private `track_key` (identical in both files, generated
  automatically when the first file is created; the sibling inherits it).
  The handheld file additionally holds the auto-generated `family_key`, and
  `"gps": true` when the child's device shares its position (one-device
  mode). Beacon files and `"gps": true` handheld files also contain
  `gps_update_interval` in seconds, initially 120.
  Edit this integer in the device file to change what Apply all writes; older
  beacon files use 120 when the field is absent and save it after a successful
  application. A value of 0 uses the firmware default. Rotary changes affect
  the device, but Apply all restores the value from the file.

First run for a missing file asks names, Bluetooth, for a handheld whether
it has GPS, and a canned set key (**required** — a device without a set
would have a useless rotary), then stores the JSON. Prepare all files in
advance — on the workshop PC you then only connect each device and apply.

## Setting up `local/` from scratch

The whole `local/` folder is gitignored — device keys and channel secrets
never leave your machine — so a fresh clone ships with no configuration.
Create the folder beside `configurator.py` and let the menu generate most
of it: run menu **1** once to create `shared.json` with the group key, and
first-run prompts create the `<n>-handheld.json` / `<n>-beacon.json` files.

The one file you write by hand is `local/shared_messages.json`: the canned
message sets the menu selects from. Define it **before creating device
files** — creation requires a set, since a device with no canned set has a
useless rotary. It is a JSON object mapping a short set
name to the messages joined with `|` (the separator is stored as-is, so `|`
cannot appear inside a message):

```json
{
  "workshop": "Hi!|Help!|Where are you?|On my way|Coming now|Found you|See you later|Yes|No"
}
```

Each set may hold at most 50 messages and the whole joined string must fit
in 200 UTF-8 bytes (the firmware's canned-message storage limit). Device
files reference a set by its key; menu **7** (or the first-run prompt)
picks which set a device gets.

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
10. **Apply all (this device)** — profile, buzzer, beacon GPS interval, channels (by kind), public
    channel if enabled, name, Bluetooth, canned set — read-back verified per step.
    On success the board's USB serial number is stored in the device file.
11. **Apply all (pair, auto-detect)** — plug both devices of the pair into
    one PC and run this. It first resolves the full assignment — matching
    boards by USB serial, auto-assigning a lone unclaimed port, asking
    (with each board's current name shown) when several ports are free —
    then prints the **plan** and applies after a single confirmation:

        Plan:
          1-beacon.json (beacon) → /dev/ttyACM1   [USB serial 021D…]
          1-handheld.json (handheld) → /dev/ttyACM0   [USB serial FDBF…]
          (1-beacon.json is bound to USB serial …, which is not connected; skipped.)
        Apply 2 device file(s)? [y/N]:

    All questions are answered before anything is written; declining
    leaves both boards untouched. Bindings are remembered, so later runs
    need no questions. You answer by list number — the serial shown is
    informational. Easiest: plug one board at a time; a single unclaimed
    port is planned without asking. The port list is a snapshot — do not
    swap boards while the prompt is open; rerun the menu instead. A wrong
    first-time assignment is fixed by deleting the `usb_serial` field from
    the two files and re-running.
12. **Power mode (beacon)** — toggles a beacon between
    **INTERACTIVE CLIENT** (screen wakes on rotary, normal cycles, 15-minute
    broadcast period) and **STEALTH TRACKER** (dark screen, sleepy low-power
    cycles, deaf between wakes, **30-minute wake period**). Confirmed before
    writing; read-back verified. **Beacon files only** — never enable
    stealth on a one-device tracker: a sleepy beacon is deaf between wakes,
    which would make the child's group chat arrive late or not at all.
    `Apply all` does not change the power mode; run 12
    again to switch back.
0. Exit.

Every write reconnects and reads back; a mismatch is reported as an error but
may require inspecting a partially applied configuration.

Apply all configures the P0.03 PWM buzzer for incoming messages and bells on
both devices, with a four-second notification window. Sound is enabled by
default on a fresh device. The device's existing sound mode is preserved on
reapplication: use the rotary's **System → Notifications** menu to choose
**All Disabled** for silence or turn sounds back on.

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

Nodes do not need a private channel key to relay its encrypted packets. Relays
must use compatible radio settings and permit foreign or opaque traffic; relay
policy, hop limit and coverage affect whether a packet reaches its destination.

Rotating a key disconnects devices still using it: re-apply afterward. Keep
`local/` and exported QRs private; back them up so keys are not lost. Never
commit or share the keys by accident.

Run offline tests with `python -m unittest -v`.

## License

Distributed under the MIT License. See [LICENSE](LICENSE).
