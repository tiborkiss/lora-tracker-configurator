#!/usr/bin/env python3
"""Local-only family configuration for Meshtastic radios.

Configuration lives beside this script, never in the current directory.

- local/shared.json — network-wide settings: the group channel secret
  (generated once for the whole workshop), the track channel name, the
  frequency slot and whether the public channel is enabled.
- local/shared_messages.json — canned message sets, keyed by a short name.
  Device JSONs reference a set by key; edit this file with a text editor.
- local/<n>-tracker.json — the device in the child's hand (no GPS). Carries
  the pair's track key, the family's own key, and the group channel.
- local/<n>-tracked.json — the GPS device being tracked. Carries the same
  pair track key. Pair keys are unique per <n>; different pairs cannot
  decode each other.

Channel layout: track is primary on both devices (position precision 32 on
the tracked GPS device); family and group are secondaries on the tracker;
the optional public channel is a secondary with the well-known default key.
"""

import base64
import json
import os
import re
import secrets
import sys
from pathlib import Path

LOCAL = Path(__file__).resolve().parent / 'local'
SHARED = LOCAL / 'shared.json'
SHARED_MESSAGES = LOCAL / 'shared_messages.json'
MAX_MESSAGE_BYTES = 200

SHARED_DEFAULTS = {'group_name': 'group', 'group_key': None, 'track_name': 'track',
                   'frequency_slot': 1, 'public_channel': False}
FAMILY_NAME = 'family'
PUBLIC_PSK = b'\x01'  # the well-known default channel key (AQ==)


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path, config):
    LOCAL.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(config, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_shared():
    if not SHARED.exists():
        config = SHARED_DEFAULTS.copy()
        save_shared(config)
        return config
    config = load_json(SHARED)
    for key, value in SHARED_DEFAULTS.items():
        config.setdefault(key, value)
    config['frequency_slot'] = int(config['frequency_slot'])
    if config['frequency_slot'] < 1:
        raise ValueError('frequency_slot must be 1 or higher')
    return config


def save_shared(config):
    save_json(SHARED, config)


def load_shared_messages():
    if not SHARED_MESSAGES.exists():
        return {}
    return load_json(SHARED_MESSAGES)


def save_shared_messages(sets):
    save_json(SHARED_MESSAGES, sets)


def generate_key(config, field):
    config[field] = secrets.token_hex(32)
    save_shared(config)  # persist before allowing it to be applied


def get_key(config, field):
    value = config.get(field)
    if not isinstance(value, str):
        raise ValueError(f'No {field}: create the keys first (menu 1)')
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f'Invalid {field}') from exc
    if len(key) not in (16, 32):
        raise ValueError(f'{field} must be 16 or 32 bytes')
    return key


def device_kind(path):
    if path.name.endswith('-tracker.json'):
        return 'tracker'
    if path.name.endswith('-tracked.json'):
        return 'tracked'
    raise ValueError(f'{path.name} must be named <n>-tracker.json or <n>-tracked.json')


def sibling_path(path):
    other = 'tracked' if device_kind(path) == 'tracker' else 'tracker'
    stem = path.name[:-len('.json')].rsplit('-', 1)[0]
    return path.with_name(f'{stem}-{other}.json')


def load_device(path):
    config = load_json(path)
    required = ['track_key', 'long_name', 'short_name', 'bluetooth']
    if device_kind(path) == 'tracker':
        required.append('family_key')
    for field in required:
        if field not in config:
            raise ValueError(f'{path.name} is missing {field}')
    return config


def save_device(path, config):
    save_json(path, config)


def prompt_device(path):
    """Create a device file; a pair's track key is shared with its sibling."""
    kind = device_kind(path)
    sibling = sibling_path(path)
    track_key = load_device(sibling)['track_key'] if sibling.exists() else secrets.token_hex(32)
    print(f'{path.name} not found — answer a few questions to create it.')
    long_name = input('Long name: ').strip()
    short_name = input('Short name (max 4 UTF-8 bytes): ').strip()
    if not long_name or len(long_name.encode('utf-8')) > 39 or not short_name or len(short_name.encode('utf-8')) > 4:
        raise ValueError('Names exceed device limits')
    state = input('Enable Bluetooth? [y/n]: ').strip().lower()
    if state not in ('y', 'n'):
        raise ValueError('Enter y or n')
    config = {'track_key': track_key, 'long_name': long_name, 'short_name': short_name,
              'bluetooth': state == 'y', 'canned': None}
    if kind == 'tracker':
        config['family_key'] = secrets.token_hex(32)
        print('A new family key was generated; export the family QR for the parents (menu 4).')
    canned = input('Canned set key from shared_messages.json (empty for none): ').strip()
    if canned:
        if canned not in load_shared_messages():
            raise ValueError(f'No canned set "{canned}" in shared_messages.json')
        config['canned'] = canned
    save_device(path, config)
    print('Saved', path.name)
    return config


def validate_messages(lines):
    lines = [line.strip() for line in lines]
    lines = [line for line in lines if line and not line.startswith('#')]
    if not lines or any('|' in line for line in lines):
        raise ValueError('Provide nonempty messages, one per line; | is reserved')
    if len(lines) > 50 or len('|'.join(lines).encode('utf-8')) > MAX_MESSAGE_BYTES:
        raise ValueError('Maximum is 50 messages and 200 UTF-8 bytes combined')
    return '|'.join(lines)


def messages_from_file(path):
    return validate_messages(Path(path).read_text(encoding='utf-8').splitlines())


def resolve_canned(device):
    key = device.get('canned')
    if not key:
        return None
    sets = load_shared_messages()
    if key not in sets:
        raise ValueError(f'No canned set "{key}" in shared_messages.json')
    return validate_messages(sets[key].split('|'))


def pair_url(node, key):
    """Export only the primary track channel; validate before saving."""
    from meshtastic.protobuf import apponly_pb2
    url = node.getURL(includeAll=False)
    encoded = url.split('#', 1)[1]
    raw = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
    channels = apponly_pb2.ChannelSet()
    channels.ParseFromString(raw)
    expected = node.channels[0].settings
    if (node.channels[0].role != 1 or len(channels.settings) != 1 or
            bytes(channels.settings[0].psk) != key or channels.settings[0] != expected):
        raise ValueError('Track QR verification failed; no QR was written')
    return url


def family_url(device, shared):
    """Build the family-channel URL offline from <n>-tracker.json."""
    from meshtastic.protobuf import apponly_pb2, config_pb2
    channels = apponly_pb2.ChannelSet()
    settings = channels.settings.add()
    settings.name = FAMILY_NAME
    settings.psk = get_key(device, 'family_key')
    settings.uplink_enabled = False
    settings.downlink_enabled = False
    channels.lora_config.region = config_pb2.Config.LoRaConfig.EU_868
    channels.lora_config.use_preset = True
    channels.lora_config.modem_preset = config_pb2.Config.LoRaConfig.LONG_FAST
    channels.lora_config.channel_num = shared['frequency_slot']
    return 'https://meshtastic.org/e/#' + base64.urlsafe_b64encode(channels.SerializeToString()).decode().rstrip('=')


def store_qr(url, image_path, replace=False):
    import io
    import qrcode
    buffer = io.BytesIO()
    qrcode.make(url).save(buffer, format='PNG')
    # Exclusive creation prevents accidental replacement; the temporary path
    # keeps an explicitly requested replacement atomic.
    target = image_path.with_name(image_path.name + '.tmp') if replace else image_path
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(buffer.getvalue())
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(target, image_path)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def update_channel(node, index, name, key, precision=None):
    if not name or len(name.encode('utf-8')) > 11:
        raise ValueError('Channel name must be 1–11 UTF-8 bytes')
    channel = node.channels[index]
    # Do not carry old channel flags, keys or MQTT settings into a new group.
    channel.settings.Clear()
    channel.role = 1 if index == 0 else 2
    channel.settings.name = name
    channel.settings.psk = key
    channel.settings.uplink_enabled = False
    channel.settings.downlink_enabled = False
    if precision is not None:
        channel.settings.module_settings.position_precision = precision
    node.writeChannel(index)


def channel_matches(node, index, name, key, precision):
    channel = node.channels[index]
    return (channel.role == (1 if index == 0 else 2) and channel.settings.name == name and
            bytes(channel.settings.psk) == key and not channel.settings.uplink_enabled and
            not channel.settings.downlink_enabled and
            channel.settings.module_settings.position_precision == precision)


def apply_public_channel(node, index):
    """Secondary channel with the well-known default key: same channel
    identity as stock devices' public primary once the slots align."""
    channel = node.channels[index]
    channel.settings.Clear()
    channel.role = 2
    channel.settings.psk = PUBLIC_PSK
    channel.settings.uplink_enabled = True
    channel.settings.downlink_enabled = True
    node.writeChannel(index)


def public_channel_matches(node, index):
    channel = node.channels[index]
    return (channel.role == 2 and channel.settings.name == '' and
            bytes(channel.settings.psk) == PUBLIC_PSK and
            channel.settings.uplink_enabled and channel.settings.downlink_enabled)


def update_profile(node, slot):
    """Set common radio parameters and the custom board's rotary input."""
    from meshtastic.protobuf import config_pb2, module_config_pb2
    radio = node.localConfig.lora
    radio.region = config_pb2.Config.LoRaConfig.EU_868
    radio.use_preset = True
    radio.modem_preset = config_pb2.Config.LoRaConfig.LONG_FAST
    radio.channel_num = slot
    node.writeConfig('lora')
    rotary = node.moduleConfig.canned_message
    rotary.rotary1_enabled = True
    rotary.inputbroker_pin_a, rotary.inputbroker_pin_b, rotary.inputbroker_pin_press = 9, 10, 16
    events = module_config_pb2.ModuleConfig.CannedMessageConfig
    rotary.inputbroker_event_cw = events.RIGHT
    rotary.inputbroker_event_ccw = events.LEFT
    rotary.inputbroker_event_press = events.SELECT
    node.writeConfig('canned_message')


def profile_matches(node, slot):
    from meshtastic.protobuf import config_pb2, module_config_pb2
    radio = node.localConfig.lora
    rotary = node.moduleConfig.canned_message
    events = module_config_pb2.ModuleConfig.CannedMessageConfig
    return (radio.region == config_pb2.Config.LoRaConfig.EU_868 and radio.use_preset and
            radio.modem_preset == config_pb2.Config.LoRaConfig.LONG_FAST and radio.channel_num == slot and
            rotary.rotary1_enabled and
            (rotary.inputbroker_pin_a, rotary.inputbroker_pin_b, rotary.inputbroker_pin_press) == (9, 10, 16) and
            (rotary.inputbroker_event_cw, rotary.inputbroker_event_ccw, rotary.inputbroker_event_press) ==
            (events.RIGHT, events.LEFT, events.SELECT))


def set_bluetooth(iface, enabled):
    from meshtastic.protobuf import config_pb2
    iface.localNode.localConfig.bluetooth.enabled = enabled
    if enabled:
        iface.localNode.localConfig.bluetooth.mode = config_pb2.Config.BluetoothConfig.RANDOM_PIN
    iface.localNode.writeConfig('bluetooth')


def bluetooth_matches(iface, enabled):
    from meshtastic.protobuf import config_pb2
    bluetooth = iface.localNode.localConfig.bluetooth
    return bluetooth.enabled == enabled and (not enabled or
                                             bluetooth.mode == config_pb2.Config.BluetoothConfig.RANDOM_PIN)


def set_canned(iface, messages):
    iface.localNode.moduleConfig.canned_message.enabled = True
    iface.localNode.writeConfig('canned_message')
    iface.localNode.set_canned_message(messages)


def channel_plan(kind, device, shared):
    """(index, label, name, key, precision) for one device kind, in order."""
    plan = [(0, 'track', shared['track_name'], get_key(device, 'track_key'),
             32 if kind == 'tracked' else 0)]
    if kind == 'tracker':
        plan.append((1, FAMILY_NAME, FAMILY_NAME, get_key(device, 'family_key'), 0))
        plan.append((2, 'group', shared['group_name'], get_key(shared, 'group_key'), 0))
    return plan


def public_index(kind):
    return 3 if kind == 'tracker' else 1


def choose_port():
    from serial.tools.list_ports import comports
    ports = list(comports())
    if not ports:
        raise ValueError('No serial ports found; connect a device')
    for i, port in enumerate(ports, 1):
        print(f'{i}. {port.device} — {port.description} (USB serial: {port.serial_number or "unknown"})')
    choice = int(input('Select the connected device number: '))
    if choice < 1 or choice > len(ports):
        raise ValueError('Invalid port selection')
    return ports[choice - 1].device


def connect(port):
    from meshtastic.serial_interface import SerialInterface
    return SerialInterface(devPath=port, timeout=30)


def confirmed(message):
    return input(message + ' [y/N]: ').strip().lower() == 'y'


def reconnect(port, action, verify):
    iface = connect(port)
    try:
        action(iface)
    finally:
        iface.close()
    # Local admin writes have no device acknowledgement; read from a fresh session.
    import time
    time.sleep(3)
    iface = connect(port)
    try:
        if not verify(iface):
            raise RuntimeError('Read-back did not match; the device may be partially configured')
    finally:
        iface.close()
    print('Read-back verified.')


def apply_all(port, kind, device, shared):
    """Apply every property of the device file (plus shared settings)."""
    slot = shared['frequency_slot']
    steps = [('radio/rotary profile',
              lambda i: update_profile(i.localNode, slot),
              lambda i: profile_matches(i.localNode, slot))]
    for index, label, name, key, precision in channel_plan(kind, device, shared):
        steps.append((f'{label} channel',
                      lambda i, idx=index, n=name, k=key, p=precision: update_channel(i.localNode, idx, n, k, p),
                      lambda i, idx=index, n=name, k=key, p=precision: channel_matches(i.localNode, idx, n, k, p)))
    if shared.get('public_channel'):
        index = public_index(kind)
        steps.append(('public channel',
                      lambda i, idx=index: apply_public_channel(i.localNode, idx),
                      lambda i, idx=index: public_channel_matches(i.localNode, idx)))
    long_name, short_name = device['long_name'], device['short_name']
    steps.append(('name',
                  lambda i: i.localNode.setOwner(long_name, short_name),
                  lambda i: i.getLongName() == long_name and i.getShortName() == short_name))
    enabled = bool(device['bluetooth'])
    steps.append(('bluetooth',
                  lambda i: set_bluetooth(i, enabled),
                  lambda i: bluetooth_matches(i, enabled)))
    messages = resolve_canned(device)
    if messages:
        steps.append(('canned messages',
                      lambda i: set_canned(i, messages),
                      lambda i: i.localNode.moduleConfig.canned_message.enabled and
                      i.getCannedMessage() == messages))
    for label, action, verify in steps:
        print('Applying', label, '…')
        reconnect(port, action, verify)
    print('All settings applied and read-back verified.')


def enter_dfu(port):
    iface = connect(port)
    try:
        iface.localNode.enterDFUMode()
    finally:
        iface.close()
    print('Rebooted into the bootloader. Copy the application .uf2 onto the MESHBOOT drive when it appears.')


def resolve_device_path(argv):
    number = argv[1] if len(argv) > 1 else None
    kind = argv[2] if len(argv) > 2 else None
    if kind and kind not in ('tracker', 'tracked'):
        raise ValueError('Second argument must be tracker or tracked')
    if not number:
        number = input('Child number: ').strip()
    if not re.fullmatch(r'[0-9]+', number):
        raise ValueError('Child number must be numeric')
    number = str(int(number))
    if kind:
        return LOCAL / f'{number}-{kind}.json'
    existing = [p for p in (LOCAL / f'{number}-tracker.json', LOCAL / f'{number}-tracked.json') if p.exists()]
    if len(existing) == 1:
        return existing[0]
    asked = input('Which device — tracker (child handheld) or tracked (GPS)? ').strip().lower()
    if asked not in ('tracker', 'tracked'):
        raise ValueError('Enter tracker or tracked')
    return LOCAL / f'{number}-{asked}.json'


def main(argv=None):
    argv = sys.argv if argv is None else argv
    try:
        device_path = resolve_device_path(argv)
        shared = load_shared()
        device = load_device(device_path) if device_path.exists() else prompt_device(device_path)
        kind = device_kind(device_path)
        print(f'\nDevice {device_path.stem} ({kind}); secrets: track/family in this file, group in shared.json')
        while True:
            print('\n1 Create group key (once)  2 Configure channel  3 Export track QR  4 Export family QR')
            print('5 Change name  6 Bluetooth on/off  7 Canned messages selector  8 Radio/rotary profile')
            print('9 Enter DFU mode  10 Apply all  0 Exit')
            try:
                choice = input('Choice: ').strip()
                if choice == '0':
                    return
                if choice == '1':
                    if shared.get('group_key') and not confirmed('Replace the saved group key? Update every device afterward'):
                        continue
                    generate_key(shared, 'group_key')
                    print('Group key saved in shared.json; generate it once — every device uses the same one.')
                    continue
                port = choose_port()
                if choice == '2':
                    options = [label for _, label, *_ in channel_plan(kind, device, shared)]
                    if shared.get('public_channel'):
                        options.append('public')
                    chosen = input(f"Channel ({', '.join(options)}): ").strip().lower()
                    if chosen == 'public':
                        if not confirmed(f'Apply the public channel to index {public_index(kind)} on {port}?'):
                            continue
                        index = public_index(kind)
                        reconnect(port, lambda i: apply_public_channel(i.localNode, index),
                                  lambda i: public_channel_matches(i.localNode, index))
                        continue
                    plan = {label: (idx, name, key, precision)
                            for idx, label, name, key, precision in channel_plan(kind, device, shared)}
                    if chosen not in plan:
                        raise ValueError(f'Choose one of: {", ".join(options)}')
                    index, name, key, precision = plan[chosen]
                    print('All devices must carry the same EU_868 frequency slot and modem settings; use Apply all to be sure.')
                    if not confirmed(f'Apply the {chosen} channel to {port}?'):
                        continue
                    reconnect(port, lambda i: update_channel(i.localNode, index, name, key, precision),
                              lambda i: channel_matches(i.localNode, index, name, key, precision))
                elif choice == '3':
                    key = get_key(device, 'track_key')
                    iface = connect(port)
                    try:
                        url = pair_url(iface.localNode, key)
                    finally:
                        iface.close()
                    image_path = LOCAL / f'{device_path.stem}-track-qr.png'
                    replace = image_path.exists()
                    if replace and not confirmed('Replace the existing private QR?'):
                        continue
                    store_qr(url, image_path, replace=replace)
                    print('Saved private track-only QR to', image_path, '(contains the channel secret).')
                elif choice == '4':
                    if kind != 'tracker':
                        raise ValueError('The family channel lives on the tracker file')
                    image_path = LOCAL / f'{device_path.stem}-family-qr.png'
                    replace = image_path.exists()
                    if replace and not confirmed('Replace the existing family QR?'):
                        continue
                    store_qr(family_url(device, shared), image_path, replace=replace)
                    print('Saved family QR to', image_path, '(contains the family secret; give it to the parents).')
                elif choice == '5':
                    long_name = input('Long name: ').strip()
                    short_name = input('Short name (max 4 UTF-8 bytes): ').strip()
                    if not long_name or len(long_name.encode('utf-8')) > 39 or not short_name or \
                            len(short_name.encode('utf-8')) > 4:
                        raise ValueError('Names exceed device limits')
                    reconnect(port, lambda i: i.localNode.setOwner(long_name, short_name),
                              lambda i: i.getLongName() == long_name and i.getShortName() == short_name)
                    device.update(long_name=long_name, short_name=short_name)
                    save_device(device_path, device)
                elif choice == '6':
                    state = input('Enable Bluetooth? [y/n]: ').strip().lower()
                    if state not in ('y', 'n'):
                        raise ValueError('Enter y or n')
                    enabled = state == 'y'
                    reconnect(port, lambda i: set_bluetooth(i, enabled), lambda i: bluetooth_matches(i, enabled))
                    device['bluetooth'] = enabled
                    save_device(device_path, device)
                elif choice == '7':
                    sets = load_shared_messages()
                    if not sets:
                        raise ValueError('shared_messages.json defines no sets; add {"key": "msg1|msg2"} with an editor')
                    print('Available sets:', ', '.join(sorted(sets)))
                    chosen = input('Set key: ').strip()
                    if chosen not in sets:
                        raise ValueError(f'No canned set "{chosen}"')
                    messages = validate_messages(sets[chosen].split('|'))
                    reconnect(port, lambda i: set_canned(i, messages),
                              lambda i: i.localNode.moduleConfig.canned_message.enabled and
                              i.getCannedMessage() == messages)
                    device['canned'] = chosen
                    save_device(device_path, device)
                elif choice == '8':
                    slot = shared['frequency_slot']
                    if not confirmed(f'Set EU_868, LONG_FAST slot {slot} and rotary pins 9/10/16?'):
                        continue
                    reconnect(port, lambda i: update_profile(i.localNode, slot),
                              lambda i: profile_matches(i.localNode, slot))
                elif choice == '9':
                    enter_dfu(port)
                elif choice == '10':
                    if not confirmed('Apply every setting from the JSON files?'):
                        continue
                    apply_all(port, kind, device, shared)
                else:
                    print('Unknown choice.')
            except (OSError, ValueError, RuntimeError, KeyError, IndexError) as exc:
                print('Could not complete operation:', exc, file=sys.stderr)
    except (OSError, ValueError, KeyError) as exc:
        print('Could not start:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
