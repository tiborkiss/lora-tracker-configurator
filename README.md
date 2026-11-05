# LoRa tracker family configurator

Experimental standalone Python menu for configuring Meshtastic radios over USB.
No firmware source checkout is required. Requires Python 3.9 or newer and a
working USB connection; hardware and Windows testing are still pending.

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

## Pair and group

1. Generate a pair key once; it is saved in `local/family.json` before use.
2. Select each device over USB and apply the **pair** channel to both. Select
   tracker for precise position sharing; select child for the GPS-less unit.
3. Optional: put the same group key in each participating family's local file
   and apply **group** to the child-facing device only. Rename channels by
   editing `pair_name` and `group_name` in the local JSON file.
4. Apply the workshop profile to each custom board: EU_868, LONG_FAST, slot 1,
   and its rotary pins and events. This changes radio configuration; follow
   the applicable local rules and attach the correct antenna before transmitting.
5. Export the pair-only QR from a device with the matching pair key; it is saved
   as `local/pair-qr.png`. A parent needs their own Meshtastic radio/node paired
   with the phone. QR import may replace existing channels and radio settings;
   verify the app's import options before proceeding.

Rotating a key disconnects devices still using the old key: apply the new pair
key to both pair devices and any family nodes; a new group key separates your
child device and selected recipients from the workshop group. Keep
`local/family.json` and QR images private. Copy the file to a safe location so
the key is not lost. Never commit or share the group key by accident.

The menu can also change the device name, enable/disable Bluetooth and write
canned messages from a UTF-8 file (one message per line, 200 bytes total). It
reconnects to check read-back after writes; a mismatch is reported as an error,
but may require inspecting a partially applied configuration. Device-specific
firmware/API compatibility and actual QR imports remain to be tested.

Run offline tests with `python -m unittest -v`.
