#!/usr/bin/env python3
"""Local-only family configuration for Meshtastic radios."""

import base64
import json
import os
import secrets
import sys
from pathlib import Path

LOCAL = Path('local')
CONFIG = LOCAL / 'family.json'
MAX_MESSAGE_BYTES = 200


def load_config():
    if not CONFIG.exists():
        return {'pair_name': 'family', 'pair_key': None, 'group_name': 'group', 'group_key': None}
    return json.loads(CONFIG.read_text(encoding='utf-8'))


def save_config(config):
    LOCAL.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = LOCAL / (CONFIG.name + '.tmp')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(config, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, CONFIG)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def generate_key(config, field):
    config[field] = secrets.token_hex(32)
    save_config(config)  # persist before allowing it to be applied


def get_key(config, field):
    value = config.get(field)
    if not isinstance(value, str):
        raise ValueError(f'No {field}: create or import a key first')
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f'Invalid {field}') from exc
    if len(key) not in (16, 32):
        raise ValueError(f'{field} must be 16 or 32 bytes')
    return key


def messages_from_file(path):
    lines = [line.strip() for line in Path(path).read_text(encoding='utf-8').splitlines()]
    lines = [line for line in lines if line and not line.startswith('#')]
    if not lines or any('|' in line for line in lines):
        raise ValueError('Provide nonempty messages, one per line; | is reserved')
    if len(lines) > 50 or len('|'.join(lines).encode('utf-8')) > MAX_MESSAGE_BYTES:
        raise ValueError('Maximum is 50 messages and 200 UTF-8 bytes combined')
    return '|'.join(lines)


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


def main():
    config = load_config()
    while True:
        print('\n1 Create pair key  2 Create group key  3 Configure channel  4 Export pair QR')
        print('5 Change name  6 Bluetooth on/off  7 Update canned messages  8 Workshop radio/rotary profile  0 Exit')
        try:
            choice = input('Choice: ').strip()
            if choice == '0':
                return
            if choice in ('1', '2'):
                field = 'pair_key' if choice == '1' else 'group_key'
                if config.get(field) and not confirmed('Replace this saved key? Update every affected node afterward'):
                    continue
                generate_key(config, field)
                print('Key saved privately in local/family.json; it is not printed.')
                continue
            port = choose_port()
            if choice == '3':
                kind = input('Channel: pair or group? ').strip().lower()
                if kind not in ('pair', 'group'):
                    raise ValueError('Choose pair or group')
                role = input('Device role: tracker or child? ').strip().lower()
                if role not in ('tracker', 'child') or (kind == 'group' and role == 'tracker'):
                    raise ValueError('Group channel is only for child-facing devices')
                name = config[kind + '_name']
                key = get_key(config, kind + '_key')
                index = 0 if kind == 'pair' else 1
                precision = 32 if kind == 'pair' and role == 'tracker' else 0
                print('Changing channel', index, 'on', port, '; other slots and radio settings remain unchanged.')
                print('All group members must use the same compatible EU_868 frequency/slot and modem settings.')
                if not confirmed('Apply this channel?'):
                    continue
                def action(iface):
                    update_channel(iface.localNode, index, name, key, precision)
                def verify(iface):
                    ch = iface.localNode.channels[index]
                    return (ch.role == (1 if index == 0 else 2) and ch.settings.name == name and
                            bytes(ch.settings.psk) == key and not ch.settings.uplink_enabled and
                            not ch.settings.downlink_enabled and ch.settings.module_settings.position_precision == precision)
                reconnect(port, action, verify)
            elif choice == '4':
                key = get_key(config, 'pair_key')
                iface = connect(port)
                try:
                    url = pair_url(iface.localNode, key)
                finally:
                    iface.close()
                LOCAL.mkdir(mode=0o700, parents=True, exist_ok=True)
                image_path = LOCAL / 'pair-qr.png'
                replace = image_path.exists()
                if replace and not confirmed('Replace the existing private QR?'):
                    continue
                store_qr(url, image_path, replace=replace)
                print('Saved private pair-only QR to', image_path, '(contains the channel secret).')
            elif choice == '5':
                long_name = input('Long name: ').strip()
                short_name = input('Short name (max 4 UTF-8 bytes): ').strip()
                if not long_name or len(long_name.encode('utf-8')) > 39 or not short_name or len(short_name.encode('utf-8')) > 4:
                    raise ValueError('Names exceed device limits')
                reconnect(port, lambda i: i.localNode.setOwner(long_name, short_name),
                          lambda i: i.getLongName() == long_name and i.getShortName() == short_name)
            elif choice == '6':
                state = input('Enable Bluetooth? [y/n]: ').strip().lower()
                if state not in ('y', 'n'):
                    raise ValueError('Enter y or n')
                enabled = state == 'y'
                def action(iface):
                    iface.localNode.localConfig.bluetooth.enabled = enabled
                    if enabled:
                        from meshtastic.protobuf import config_pb2
                        iface.localNode.localConfig.bluetooth.mode = config_pb2.Config.BluetoothConfig.RANDOM_PIN
                    iface.localNode.writeConfig('bluetooth')
                def verify(iface):
                    from meshtastic.protobuf import config_pb2
                    bt = iface.localNode.localConfig.bluetooth
                    return bt.enabled == enabled and (not enabled or bt.mode == config_pb2.Config.BluetoothConfig.RANDOM_PIN)
                reconnect(port, action, verify)
            elif choice == '7':
                messages = messages_from_file(input('UTF-8 message file path: ').strip())
                def action(iface):
                    iface.localNode.moduleConfig.canned_message.enabled = True
                    iface.localNode.writeConfig('canned_message')
                    iface.localNode.set_canned_message(messages)
                reconnect(port, action, lambda i: i.localNode.moduleConfig.canned_message.enabled and
                          i.getCannedMessage() == messages)
            elif choice == '8':
                # EU_868 LONG_FAST occupies the entire permitted 250 kHz band.
                slot = 1
                if not confirmed('Set EU_868, LONG_FAST slot 1 and rotary pins 9/10/16?'):
                    continue
                reconnect(port, lambda i: update_profile(i.localNode, slot), lambda i: profile_matches(i.localNode, slot))
            else:
                print('Unknown choice.')
        except (OSError, ValueError, RuntimeError, KeyError, IndexError) as exc:
            print('Could not complete operation:', exc, file=sys.stderr)


if __name__ == '__main__':
    main()
