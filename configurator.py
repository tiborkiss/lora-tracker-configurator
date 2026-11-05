#!/usr/bin/env python3
"""Local-only family configuration for Meshtastic radios.

Configuration lives beside this script, never in the current directory:
local/shared.json holds the two channel secrets, generated once and used by
every device. local/<n>.json holds one device's properties. An index is just
a file name: kids get 1..n at the workshop, the tracker is 0; running without
an argument selects 0. The menu asks for missing values and stores them, so
device files can be prepared in advance and applied later with "Apply all".
"""

import base64
import json
import os
import secrets
import sys
from pathlib import Path

LOCAL = Path(__file__).resolve().parent / 'local'
SHARED = LOCAL / 'shared.json'
MAX_MESSAGE_BYTES = 200

SHARED_DEFAULTS = {'pair_name': 'pair', 'pair_key': None, 'group_name': 'group', 'group_key': None}


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
    return config


def save_shared(config):
    save_json(SHARED, config)


def load_device(path):
    config = load_json(path)
    for field in ('role', 'long_name', 'short_name'):
        if field not in config:
            raise ValueError(f'{path.name} is missing {field}')
    if config['role'] not in ('tracker', 'child'):
        raise ValueError(f'{path.name} has an invalid role')
    return config


def save_device(path, config):
    save_json(path, config)


def prompt_device(path):
    print(f'{path.name} not found — answer a few questions to create it.')
    role = input('Role — tracker or child? ').strip().lower()
    if role not in ('tracker', 'child'):
        raise ValueError('Role must be tracker or child')
    long_name = input('Long name: ').strip()
    short_name = input('Short name (max 4 UTF-8 bytes): ').strip()
    if not long_name or len(long_name.encode('utf-8')) > 39 or not short_name or len(short_name.encode('utf-8')) > 4:
        raise ValueError('Names exceed device limits')
    state = input('Enable Bluetooth? [y/n]: ').strip().lower()
    if state not in ('y', 'n'):
        raise ValueError('Enter y or n')
    canned = []
    source = input('Canned message file, one per line (empty to skip): ').strip()
    if source:
        canned = messages_from_file(source).split('|')
    config = {'role': role, 'long_name': long_name, 'short_name': short_name,
              'bluetooth': state == 'y', 'canned': canned or None}
    save_device(path, config)
    print('Saved', path.name)
    return config


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


def canned_string(config):
    lines = config.get('canned')
    return validate_messages(lines) if lines else None


def pair_url(node, key):
    """Export only the primary pair channel; validate the result before saving."""
    from meshtastic.protobuf import apponly_pb2
    url = node.getURL(includeAll=False)
    encoded = url.split('#', 1)[1]
    raw = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
    channels = apponly_pb2.ChannelSet()
    channels.ParseFromString(raw)
    expected = node.channels[0].settings
    if (node.channels[0].role != 1 or len(channels.settings) != 1 or
            bytes(channels.settings[0].psk) != key or channels.settings[0] != expected):
        raise ValueError('Pair QR verification failed; no QR was written')
    return url


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


def apply_all(port, device, shared):
    """Apply every property of <n>.json (plus the shared keys) to one device."""
    steps = [('radio/rotary profile',
              lambda i: update_profile(i.localNode, 1),
              lambda i: profile_matches(i.localNode, 1))]
    precision = 32 if device['role'] == 'tracker' else 0
    pair_key = get_key(shared, 'pair_key')
    steps.append(('pair channel',
                  lambda i: update_channel(i.localNode, 0, shared['pair_name'], pair_key, precision),
                  lambda i: channel_matches(i.localNode, 0, shared['pair_name'], pair_key, precision)))
    if device['role'] == 'child':
        group_key = get_key(shared, 'group_key')
        steps.append(('group channel',
                      lambda i: update_channel(i.localNode, 1, shared['group_name'], group_key, 0),
                      lambda i: channel_matches(i.localNode, 1, shared['group_name'], group_key, 0)))
    long_name, short_name = device['long_name'], device['short_name']
    steps.append(('name',
                  lambda i: i.localNode.setOwner(long_name, short_name),
                  lambda i: i.getLongName() == long_name and i.getShortName() == short_name))
    enabled = bool(device['bluetooth'])
    steps.append(('bluetooth',
                  lambda i: set_bluetooth(i, enabled),
                  lambda i: bluetooth_matches(i, enabled)))
    messages = canned_string(device)
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


def main(argv=None):
    argv = sys.argv if argv is None else argv
    index = str(int(argv[1])) if len(argv) > 1 else '0'
    device_path = LOCAL / f'{index}.json'
    try:
        shared = load_shared()
        device = load_device(device_path) if device_path.exists() else prompt_device(device_path)
        print(f'\nDevice {index} ({device["role"]}) — {device_path.name}; secrets in shared.json')
        while True:
            print('\n1 Create keys (pair + group, once)  2 Configure channel  3 Export pair QR')
            print('4 Change name  5 Bluetooth on/off  6 Update canned messages  7 Radio/rotary profile')
            print('8 Enter DFU mode  9 Apply all  0 Exit')
            try:
                choice = input('Choice: ').strip()
                if choice == '0':
                    return
                if choice == '1':
                    for field, label in (('pair_key', 'pair'), ('group_key', 'group')):
                        if shared.get(field) and not confirmed(f'Replace the saved {label} key? Update every device afterward'):
                            continue
                        generate_key(shared, field)
                    print('Keys saved in shared.json; generate them once — every device uses the same ones.')
                    continue
                port = choose_port()
                if choice == '2':
                    kind = input('Channel: pair or group? ').strip().lower()
                    if kind not in ('pair', 'group'):
                        raise ValueError('Choose pair or group')
                    if kind == 'group' and device['role'] != 'child':
                        raise ValueError('Group channel is only for child-facing devices')
                    name, index_ = (shared['pair_name'], 0) if kind == 'pair' else (shared['group_name'], 1)
                    key = get_key(shared, kind + '_key')
                    precision = 32 if kind == 'pair' and device['role'] == 'tracker' else 0
                    print('All devices must carry the same EU_868 frequency slot and modem settings; use Apply all to be sure.')
                    if not confirmed(f'Apply the {kind} channel to {port}?'):
                        continue
                    reconnect(port, lambda i: update_channel(i.localNode, index_, name, key, precision),
                              lambda i: channel_matches(i.localNode, index_, name, key, precision))
                elif choice == '3':
                    key = get_key(shared, 'pair_key')
                    iface = connect(port)
                    try:
                        url = pair_url(iface.localNode, key)
                    finally:
                        iface.close()
                    image_path = LOCAL / 'pair-qr.png'
                    replace = image_path.exists()
                    if replace and not confirmed('Replace the existing private QR?'):
                        continue
                    store_qr(url, image_path, replace=replace)
                    print('Saved private pair-only QR to', image_path, '(contains the channel secret).')
                elif choice == '4':
                    long_name = input('Long name: ').strip()
                    short_name = input('Short name (max 4 UTF-8 bytes): ').strip()
                    if not long_name or len(long_name.encode('utf-8')) > 39 or not short_name or \
                            len(short_name.encode('utf-8')) > 4:
                        raise ValueError('Names exceed device limits')
                    reconnect(port, lambda i: i.localNode.setOwner(long_name, short_name),
                              lambda i: i.getLongName() == long_name and i.getShortName() == short_name)
                    device.update(long_name=long_name, short_name=short_name)
                    save_device(device_path, device)
                elif choice == '5':
                    state = input('Enable Bluetooth? [y/n]: ').strip().lower()
                    if state not in ('y', 'n'):
                        raise ValueError('Enter y or n')
                    enabled = state == 'y'
                    reconnect(port, lambda i: set_bluetooth(i, enabled), lambda i: bluetooth_matches(i, enabled))
                    device['bluetooth'] = enabled
                    save_device(device_path, device)
                elif choice == '6':
                    messages = messages_from_file(input('UTF-8 message file path: ').strip())
                    reconnect(port, lambda i: set_canned(i, messages),
                              lambda i: i.localNode.moduleConfig.canned_message.enabled and
                              i.getCannedMessage() == messages)
                    device['canned'] = messages.split('|')
                    save_device(device_path, device)
                elif choice == '7':
                    # EU_868 LONG_FAST occupies the entire permitted 250 kHz band.
                    if not confirmed('Set EU_868, LONG_FAST slot 1 and rotary pins 9/10/16?'):
                        continue
                    reconnect(port, lambda i: update_profile(i.localNode, 1),
                              lambda i: profile_matches(i.localNode, 1))
                elif choice == '8':
                    enter_dfu(port)
                elif choice == '9':
                    if not confirmed('Apply every setting from the JSON files?'):
                        continue
                    apply_all(port, device, shared)
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
