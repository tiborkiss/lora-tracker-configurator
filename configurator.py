#!/usr/bin/env python3
"""Local-only family configuration for Meshtastic radios.

Configuration lives beside this script, never in the current directory.

- local/shared.json — network-wide settings: the group channel secret
  (generated once for the whole workshop), the track channel name, the
  frequency slot and whether the public channel is enabled.
- local/shared_messages.json — canned message sets, keyed by a short name.
  Device JSONs reference a set by key (required: the rotary can only send
  when a set is assigned); edit this file with a text editor.
- local/<n>-handheld.json — the device in the child's hand. Carries the
  pair's track key, the family's own key, and the group channel. With
  "gps": true the board also broadcasts its position on the track
  channel (one-device mode: the kid's device is its own beacon).
- local/<n>-beacon.json — the GPS beacon (position source). Carries the same
  pair track key. Pair keys are unique per <n>; different pairs cannot
  decode each other.

Channel layout: track is primary on both devices (position precision 32
when the device carries GPS); family and group are secondaries on the
handheld; the optional public channel is a secondary with the well-known
default key. A child with a single GPS-capable device uses only the
handheld file with "gps": true; the beacon file stays reserved for a
dedicated pair device — the sleepy/stealth mode it enables would make
the device deaf to group messages between wakes.
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
DEFAULT_GPS_UPDATE_INTERVAL = 120
MAX_GPS_UPDATE_INTERVAL = 2147483647

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
    if path.name.endswith('-handheld.json'):
        return 'handheld'
    if path.name.endswith('-beacon.json'):
        return 'beacon'
    raise ValueError(f'{path.name} must be named <n>-handheld.json or <n>-beacon.json')


def sibling_path(path):
    other = 'beacon' if device_kind(path) == 'handheld' else 'handheld'
    stem = path.name[:-len('.json')].rsplit('-', 1)[0]
    return path.with_name(f'{stem}-{other}.json')


def load_device(path):
    config = load_json(path)
    required = ['track_key', 'long_name', 'short_name', 'bluetooth', 'canned']
    kind = device_kind(path)
    if kind == 'handheld':
        required.append('family_key')
    for field in required:
        if field not in config:
            raise ValueError(f'{path.name} is missing {field}')
    if kind == 'beacon' or config.get('gps'):
        config.setdefault('gps_update_interval', DEFAULT_GPS_UPDATE_INTERVAL)
        gps_update_interval(config)
    return config


def save_device(path, config):
    save_json(path, config)


def prompt_device(path):
    """Create a device file; a pair's track key is shared with its sibling."""
    kind = device_kind(path)
    sibling = sibling_path(path)
    track_key = load_device(sibling)['track_key'] if sibling.exists() else secrets.token_hex(32)
    print(f'{path.name} not found — answer a few questions to create it.')
    long_name = ask('Long name: ').strip()
    short_name = ask('Short name (max 4 UTF-8 bytes): ').strip()
    if not long_name or len(long_name.encode('utf-8')) > 39 or not short_name or len(short_name.encode('utf-8')) > 4:
        raise ValueError('Names exceed device limits')
    state = ask('Enable Bluetooth? [y/n]: ').strip().lower()
    if state not in ('y', 'n'):
        raise ValueError('Enter y or n')
    bluetooth = state == 'y'
    gps = None
    if kind == 'handheld':
        state = ask('Does this device have GPS and should it share its position? [y/n]: ').strip().lower()
        if state not in ('y', 'n'):
            raise ValueError('Enter y or n')
        gps = state == 'y'
    config = {'track_key': track_key, 'long_name': long_name, 'short_name': short_name,
              'bluetooth': bluetooth, 'canned': None}
    if kind == 'handheld':
        config['family_key'] = secrets.token_hex(32)
        if gps:
            config['gps'] = True
            config['gps_update_interval'] = DEFAULT_GPS_UPDATE_INTERVAL
            print('One-device mode: this handheld is its own beacon; keep it out of stealth so group chat stays live.')
        print('A new family key was generated; export the family QR for the parents (menu 4).')
    else:
        config['gps_update_interval'] = DEFAULT_GPS_UPDATE_INTERVAL
    sets = load_shared_messages()
    if not sets:
        raise ValueError('Define canned sets in local/shared_messages.json first — every device '
                         'needs one or the child cannot send messages with the rotary')
    print('Canned sets available:', ', '.join(sorted(sets)))
    canned = ask('Canned set key from shared_messages.json: ').strip()
    if canned not in sets:
        raise ValueError(f'No canned set "{canned}" in shared_messages.json — every device needs one')
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
    """Build the family-channel URL offline from <n>-handheld.json."""
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


def set_buzzer_pin(node):
    node.localConfig.device.buzzer_gpio = 3  # P0.03
    node.writeConfig('device')


STEALTH_BROADCAST_SECS = 1800  # stealth deep-sleep period (30 min); also the broadcast period
INTERACTIVE_BROADCAST_SECS = 900  # Meshtastic default restored when leaving stealth


def set_stealth_mode(iface, stealth):
    """Switch a beacon between interactive CLIENT and stealth tracker operation.

    Stealth = role TRACKER + power.is_power_saving (sleepy cycles, dark screen)
    with a 30-minute wake period; interactive = role CLIENT + power saving off
    with the default 15-minute period. In power-saving mode the firmware uses
    position_broadcast_secs as the deep-sleep duration, so it is written with
    the mode. Writes reboot the device, so the caller runs them under the
    reconnect-retry pattern.
    """
    from meshtastic.protobuf import config_pb2
    local = iface.localNode.localConfig
    local.device.role = (config_pb2.Config.DeviceConfig.TRACKER if stealth
                         else config_pb2.Config.DeviceConfig.CLIENT)
    local.power.is_power_saving = stealth
    local.position.position_broadcast_secs = (STEALTH_BROADCAST_SECS if stealth
                                              else INTERACTIVE_BROADCAST_SECS)
    iface.localNode.writeConfig('device')
    iface.localNode.writeConfig('power')
    iface.localNode.writeConfig('position')


def stealth_mode_matches(iface, stealth):
    from meshtastic.protobuf import config_pb2
    local = iface.localNode.localConfig
    want_role = (config_pb2.Config.DeviceConfig.TRACKER if stealth
                 else config_pb2.Config.DeviceConfig.CLIENT)
    want_secs = STEALTH_BROADCAST_SECS if stealth else INTERACTIVE_BROADCAST_SECS
    return (local.device.role == want_role
            and local.power.is_power_saving == stealth
            and local.position.position_broadcast_secs == want_secs)


def gps_update_interval(device):
    value = device.get('gps_update_interval', DEFAULT_GPS_UPDATE_INTERVAL)
    if type(value) is not int or not 0 <= value <= MAX_GPS_UPDATE_INTERVAL:
        raise ValueError(f'gps_update_interval must be an integer from 0 to {MAX_GPS_UPDATE_INTERVAL} seconds')
    return value


def set_gps_update_interval(node, interval):
    node.localConfig.position.gps_update_interval = interval
    node.writeConfig('position')


def set_buzzer_notifications(node):
    notification = node.moduleConfig.external_notification
    notification.enabled = True
    notification.use_pwm = True
    notification.use_i2s_as_buzzer = False
    notification.output_buzzer = 3
    notification.alert_message_buzzer = True
    notification.alert_bell_buzzer = True
    notification.output_ms = 4000
    notification.nag_timeout = 0
    node.writeConfig('external_notification')


def buzzer_notifications_match(node):
    notification = node.moduleConfig.external_notification
    return (notification.enabled and notification.use_pwm and not notification.use_i2s_as_buzzer and
            notification.output_buzzer == 3 and notification.alert_message_buzzer and
            notification.alert_bell_buzzer and notification.output_ms == 4000 and notification.nag_timeout == 0)


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
             32 if kind == 'beacon' or device.get('gps') else 0)]
    if kind == 'handheld':
        plan.append((1, FAMILY_NAME, FAMILY_NAME, get_key(device, 'family_key'), 0))
        plan.append((2, 'group', shared['group_name'], get_key(shared, 'group_key'), 0))
    return plan


def public_index(kind):
    return 3 if kind == 'handheld' else 1


def usb_ports():
    """Only USB serial boards — legacy /dev/ttyS* ports are noise."""
    from serial.tools.list_ports import comports
    return [p for p in comports() if p.serial_number or p.vid]


def choose_port():
    """Return the selected USB serial port object (device path + USB serial)."""
    ports = usb_ports()
    if not ports:
        raise ValueError('No USB serial ports found; connect a device')
    for i, port in enumerate(ports, 1):
        print(f'{i}. {port.device} — {port.description} (USB serial: {port.serial_number or "unknown"})')
    choice = int(ask('Select the connected device number: '))
    if choice < 1 or choice > len(ports):
        raise ValueError('Invalid port selection')
    return ports[choice - 1]


def connected_serials():
    """Map USB serial number -> device path for everything plugged in."""
    from serial.tools.list_ports import comports
    return {p.serial_number: p.device for p in comports() if p.serial_number}


def connect(port):
    from meshtastic.serial_interface import SerialInterface
    return SerialInterface(devPath=port, timeout=30)


def ask(prompt):
    """ask() that exits cleanly when stdin closes instead of looping."""
    try:
        return input(prompt)
    except EOFError:
        print('\nInput closed; exiting.')
        raise SystemExit(0)


def confirmed(message):
    return ask(message + ' [y/N]: ').strip().lower() == 'y'


def safe_close(iface, timeout=5.0):
    """close() that cannot hang and keeps the library threads quiet.
    The stock close() writes a farewell packet (blocking flush) while the
    heartbeat timer and reader thread may still be writing; instead: stop
    the heartbeat, let the reader exit on _wantExit, then release the
    stream and finish the library close under a watchdog."""
    import threading

    def cancel_heartbeat():
        try:
            timer = getattr(iface, 'heartbeatTimer', None)
            if timer is not None:
                timer.cancel()
        except Exception:
            pass

    def attempt():
        try:
            iface._wantExit = True  # the reader checks this each 0.5 s read cycle
        except Exception:
            pass
        cancel_heartbeat()
        rx = getattr(iface, '_rxThread', None)
        if rx is not None and rx is not threading.current_thread():
            try:
                rx.join(1.5)
            except Exception:
                pass
        cancel_heartbeat()  # the reader may have re-armed it just before exiting
        try:
            if getattr(iface, 'stream', None) is not None:
                iface.stream.close()
        except Exception:
            pass
        try:
            iface.close()  # direct call: safe_close must not recurse
        except Exception:
            pass

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(timeout)


def reconnect(port, action, verify, attempts=3):
    """Apply, then verify through a fresh session, retrying whole steps:
    config writes reboot the device, so the next connect can arrive early."""
    import time
    last = None
    for _ in range(attempts):
        try:
            iface = connect(port)
            try:
                action(iface)
            finally:
                safe_close(iface)
            # Local admin writes have no device acknowledgement; read from a fresh session.
            time.sleep(3)
            iface = connect(port)
            try:
                if not verify(iface):
                    raise RuntimeError('Read-back did not match; the device may be partially configured')
            finally:
                safe_close(iface)
        except Exception as exc:  # reboots make connects flaky; retry the step
            last = exc
            time.sleep(5)
            continue
        print('Read-back verified.')
        return
    raise RuntimeError(f'Could not complete step on {port}: {last}')


def connect_serial(serial):
    """Resolve the current port path of a USB serial and connect to it.
    The tty index changes across the device's own reboots; the serial does not."""
    path = connected_serials().get(serial)
    if not path:
        raise OSError(f'device with USB serial {serial} is not connected')
    return connect(path)


def reconnect_serial(serial, action, verify, attempts=6, settle_seconds=3):
    """reconnect() for a USB serial: re-resolves the port path on every
    attempt, waiting out reboots and re-enumerations."""
    import time
    last = None
    for _ in range(attempts):
        try:
            iface = connect_serial(serial)
            try:
                action(iface)
            finally:
                safe_close(iface)
            time.sleep(settle_seconds)
            iface = connect_serial(serial)
            try:
                if not verify(iface):
                    raise RuntimeError('Read-back did not match; the device may be partially configured')
            finally:
                safe_close(iface)
        except Exception as exc:
            last = exc
            time.sleep(5)
            continue
        print('Read-back verified.')
        return
    raise RuntimeError(f'Could not complete step on device {serial}: {last}')


def apply_gps_interval(serial, interval, attempts=6):
    """Verify through reconnects before retrying a position write that may reboot."""
    import time
    last = None
    try:
        iface = connect_serial(serial)
        try:
            set_gps_update_interval(iface.localNode, interval)
        finally:
            safe_close(iface)
    except Exception as exc:
        last = exc
    time.sleep(12)
    for attempt in range(attempts):
        try:
            iface = connect_serial(serial)
            try:
                if iface.localNode.localConfig.position.gps_update_interval == interval:
                    print('Read-back verified.')
                    return
                last = RuntimeError('GPS interval read-back did not match')
                if attempt < attempts - 1:
                    set_gps_update_interval(iface.localNode, interval)
            finally:
                safe_close(iface)
            if attempt < attempts - 1:
                time.sleep(12)
        except Exception as exc:
            last = exc
            time.sleep(5)
    raise RuntimeError(f'Could not verify GPS update interval on device {serial}: {last}')


def apply_all(serial, kind, device, shared):
    """Apply every property of the device file (plus shared settings) to the
    device with this USB serial; the port path is re-resolved per step."""
    slot = shared['frequency_slot']
    has_gps = kind == 'beacon' or bool(device.get('gps'))
    interval = gps_update_interval(device) if has_gps else None
    steps = [('radio/rotary profile',
              lambda i: update_profile(i.localNode, slot),
              lambda i: profile_matches(i.localNode, slot)),
             ('buzzer pin',
              lambda i: set_buzzer_pin(i.localNode),
              lambda i: i.localNode.localConfig.device.buzzer_gpio == 3),
             ('buzzer notifications',
              lambda i: set_buzzer_notifications(i.localNode),
              lambda i: buzzer_notifications_match(i.localNode))]
    if interval is not None:
        steps.append(('GPS update interval', None, None))
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
    if not messages:
        raise ValueError('The device file references no canned set, so the rotary could not send '
                         'messages — pick a set first (menu 7), then apply again')
    steps.append(('canned messages',
                  lambda i: set_canned(i, messages),
                  lambda i: i.localNode.moduleConfig.canned_message.enabled and
                  i.getCannedMessage() == messages))
    for label, action, verify in steps:
        print('Applying', label, '…')
        if label == 'GPS update interval':
            apply_gps_interval(serial, interval)
        elif label in ('buzzer pin', 'buzzer notifications'):
            reconnect_serial(serial, action, verify, settle_seconds=12)
        else:
            reconnect_serial(serial, action, verify)
    print('All settings applied and read-back verified.')


def apply_one(serial, kind, device, shared, path):
    """Apply one device file and remember which USB serial it belongs to."""
    apply_all(serial, kind, device, shared)
    latest = load_device(path)
    latest['usb_serial'] = serial
    save_device(path, latest)
    device.clear()
    device.update(latest)


def port_names(entries):
    """Best-effort current owner name per (serial, port); lets the operator
    tell two plugged-in boards apart when (re)binding. Never raises."""
    names = {}
    for serial, port in entries:
        try:
            iface = connect(port)
            try:
                names[serial] = iface.getLongName() or '?'
            finally:
                safe_close(iface)
        except Exception:
            names[serial] = 'unreachable'
    return names


def resolve_selected(selected, session, candidates):
    """Guard a single-device operation against the wrong board.

    The selected port's USB serial decides: a board already bound to the
    other file of the pair switches the session to it; a board bound
    nowhere is offered as a first binding; a board that contradicts the
    session's own binding is refused.
    """
    serial = selected.serial_number
    if not serial:
        return
    if session['device'].get('usb_serial') == serial:
        return
    for kind, path in candidates.items():
        if path.exists() and path != session['path'] and load_device(path).get('usb_serial') == serial:
            print(f'That board belongs to {path.name}; switching to it.')
            session.update(path=path, device=load_device(path), kind=kind)
            return
    bound = session['device'].get('usb_serial')
    if bound:
        sibling = candidates['handheld' if session['kind'] == 'beacon' else 'beacon']
        if not sibling.exists() and confirmed(
                f'Board {serial} is not the one bound to {session["path"].name}. Create {sibling.name} for it now?'):
            device = prompt_device(sibling)
            device['usb_serial'] = serial
            save_device(sibling, device)
            session.update(path=sibling, device=device, kind=device_kind(sibling))
            return
        raise ValueError(f'{selected.device} is board {serial}, but {session["path"].name} belongs to {bound}. '
                         'Plug that board instead.')
    if not confirmed(f'Board {serial} is not yet bound; apply {session["path"].name} to it?'):
        raise ValueError('Operation aborted; no changes made.')


def pair_plan(device_path, shared):
    """Resolve what apply_pair would do, without writing anything.

    Returns (steps, notes): steps are (path, kind, device, serial, port)
    tuples ready to apply; notes explain skips. Assignment questions are
    asked here, so every decision is made before the first write.
    """
    ports = connected_serials()
    claimed = set()
    steps = []
    notes = []
    for path in (device_path, sibling_path(device_path)):
        if not path.exists():
            notes.append(f'{path.name} does not exist; skipped.')
            continue
        kind = device_kind(path)
        device = load_device(path)
        serial = device.get('usb_serial')
        if serial and serial in ports:
            steps.append((path, kind, device, serial, ports[serial]))
            claimed.add(serial)
            continue
        if serial:
            notes.append(f'{path.name} is bound to USB serial {serial}, which is not connected; skipped.')
            continue
        free = {s: dev for s, dev in ports.items() if s not in claimed}
        if not free:
            notes.append(f'No unclaimed port for {path.name}; connect it and run again.')
            continue
        entries = sorted(free.items())
        if len(entries) == 1:
            serial, port = entries[0]
            notes.append(f'{path.name}: only one unclaimed port — will assign {port}.')
        else:
            # The serial is informational; answer with the list number. The
            # list is a snapshot: if boards are swapped, rerun this menu.
            print('(inspecting the boards…)')
            names = port_names(entries)
            print(f'Assign {path.name} ({kind}) — pick the connected device:')
            for i, (s, dev) in enumerate(entries, 1):
                print(f'{i}. {dev} (USB serial {s}) — currently "{names.get(s, "?")}"')
            pick = int(ask('Device number: '))
            if pick < 1 or pick > len(entries):
                raise ValueError('Invalid selection')
            serial, port = entries[pick - 1]
        steps.append((path, kind, device, serial, port))
        claimed.add(serial)
    return steps, notes


def apply_pair(device_path, shared):
    """Print the full file→port plan, confirm once, then apply the pair."""
    steps, notes = pair_plan(device_path, shared)
    for note in notes:
        print(f'  ({note})')
    if not steps:
        print('Nothing to apply.')
        return
    print('Plan:')
    for path, kind, device, serial, port in steps:
        print(f'  {path.name} ({kind}) → {port}   [USB serial {serial}]')
    if not confirmed(f'Apply {len(steps)} device file(s)?'):
        print('Aborted; nothing was written.')
        return
    for path, kind, device, serial, port in steps:
        print(f'=== {path.name} → {port} ===')
        apply_one(serial, kind, device, shared, path)
    print('Pair application finished.')


def enter_dfu(port):
    iface = connect(port)
    try:
        iface.localNode.enterDFUMode()
    finally:
        safe_close(iface)
    print('Rebooted into the bootloader. Copy the application .uf2 onto the MESHBOOT drive when it appears.')


def resolve_child_number(argv):
    number = argv[1] if len(argv) > 1 else None
    if not number:
        number = ask('Child number: ').strip()
    if not re.fullmatch(r'[0-9]+', number):
        raise ValueError('Child number must be numeric')
    return str(int(number))


def main(argv=None):
    argv = sys.argv if argv is None else argv
    try:
        number = resolve_child_number(argv)
        kind_arg = argv[2] if len(argv) > 2 else None
        if kind_arg and kind_arg not in ('handheld', 'beacon'):
            raise ValueError('Second argument must be handheld or beacon')
        shared = load_shared()
        candidates = {kind: LOCAL / f'{number}-{kind}.json' for kind in ('beacon', 'handheld')}
        session = {'path': None, 'device': None, 'kind': None}

        def open_device(kind):
            path = candidates[kind]
            device = load_device(path) if path.exists() else prompt_device(path)
            session.update(path=path, device=device, kind=device_kind(path))

        def ensure_device():
            """Resolve which file an action targets; both files existing is fine."""
            if session['device'] is None:
                asked = ask("Which device — handheld (in the child's hand) or beacon (GPS)? ").strip().lower()
                if asked not in candidates:
                    raise ValueError('Enter handheld or beacon')
                open_device(asked)
            return session

        if kind_arg:
            open_device(kind_arg)
        else:
            existing = [p for p in candidates.values() if p.exists()]
            if len(existing) == 1:
                open_device(device_kind(existing[0]))

        def remember(serial):
            """Bind the session file to this board after a successful op."""
            if serial and not session['device'].get('usb_serial'):
                session['device']['usb_serial'] = serial
                save_device(session['path'], session['device'])

        label = (f"device {session['path'].stem} ({session['kind']})" if session['device'] is not None
                 else f'pair {number} (beacon + handheld)')
        print(f'\nChild {number}: {label}; secrets: pair keys in device files, group in shared.json')
        while True:
            context = session['path'].name if session['path'] else f'pair {number}'
            print(f'\n[{context}]')
            print('1 Create group key (once)  2 Configure channel  3 Export track QR  4 Export family QR')
            print('5 Change name  6 Bluetooth on/off  7 Canned messages selector  8 Radio/rotary profile')
            print('9 Enter DFU mode  10 Apply all (this device)  11 Apply all (pair, auto-detect)')
            print('12 Power mode (beacon)  0 Exit')
            try:
                choice = ask('Choice: ').strip()
                if choice == '0':
                    return
                if choice == '1':
                    if shared.get('group_key') and not confirmed('Replace the saved group key? Update every device afterward'):
                        continue
                    generate_key(shared, 'group_key')
                    print('Group key saved in shared.json; generate it once — every device uses the same one.')
                    continue
                port = None
                dev = None
                if choice in ('2', '3', '5', '6', '7', '8', '9', '10'):
                    dev = ensure_device()
                    dev['device'] = load_device(dev['path'])
                    selected = choose_port()
                    port = selected.device
                    resolve_selected(selected, session, candidates)
                elif choice == '4':
                    dev = ensure_device()
                if choice == '2':
                    options = [label for _, label, *_ in channel_plan(dev['kind'], dev['device'], shared)]
                    if shared.get('public_channel'):
                        options.append('public')
                    chosen = ask(f"Channel ({', '.join(options)}): ").strip().lower()
                    if chosen == 'public':
                        if not confirmed(f'Apply the public channel to index {public_index(dev["kind"])} on {port}?'):
                            continue
                        index = public_index(dev['kind'])
                        reconnect(port, lambda i: apply_public_channel(i.localNode, index),
                                  lambda i: public_channel_matches(i.localNode, index))
                        remember(selected.serial_number)
                        continue
                    plan = {label: (idx, name, key, precision)
                            for idx, label, name, key, precision in channel_plan(dev['kind'], dev['device'], shared)}
                    if chosen not in plan:
                        raise ValueError(f'Choose one of: {", ".join(options)}')
                    index, name, key, precision = plan[chosen]
                    print('All devices must carry the same EU_868 frequency slot and modem settings; use Apply all to be sure.')
                    if not confirmed(f'Apply the {chosen} channel to {port}?'):
                        continue
                    reconnect(port, lambda i: update_channel(i.localNode, index, name, key, precision),
                              lambda i: channel_matches(i.localNode, index, name, key, precision))
                    remember(selected.serial_number)
                elif choice == '3':
                    key = get_key(dev['device'], 'track_key')
                    iface = connect(port)
                    try:
                        url = pair_url(iface.localNode, key)
                    finally:
                        safe_close(iface)
                    image_path = LOCAL / f"{dev['path'].stem}-track-qr.png"
                    replace = image_path.exists()
                    if replace and not confirmed('Replace the existing private QR?'):
                        continue
                    store_qr(url, image_path, replace=replace)
                    print('Saved private track-only QR to', image_path, '(contains the channel secret).')
                elif choice == '4':
                    if dev['kind'] != 'handheld':
                        raise ValueError('The family channel lives on the handheld file')
                    image_path = LOCAL / f"{dev['path'].stem}-family-qr.png"
                    replace = image_path.exists()
                    if replace and not confirmed('Replace the existing family QR?'):
                        continue
                    store_qr(family_url(dev['device'], shared), image_path, replace=replace)
                    print('Saved family QR to', image_path, '(contains the family secret; give it to the parents).')
                elif choice == '5':
                    long_name = ask('Long name: ').strip()
                    short_name = ask('Short name (max 4 UTF-8 bytes): ').strip()
                    if not long_name or len(long_name.encode('utf-8')) > 39 or not short_name or \
                            len(short_name.encode('utf-8')) > 4:
                        raise ValueError('Names exceed device limits')
                    reconnect(port, lambda i: i.localNode.setOwner(long_name, short_name),
                              lambda i: i.getLongName() == long_name and i.getShortName() == short_name)
                    dev['device'].update(long_name=long_name, short_name=short_name)
                    save_device(dev['path'], dev['device'])
                    remember(selected.serial_number)
                elif choice == '6':
                    state = ask('Enable Bluetooth? [y/n]: ').strip().lower()
                    if state not in ('y', 'n'):
                        raise ValueError('Enter y or n')
                    enabled = state == 'y'
                    reconnect(port, lambda i: set_bluetooth(i, enabled), lambda i: bluetooth_matches(i, enabled))
                    dev['device']['bluetooth'] = enabled
                    save_device(dev['path'], dev['device'])
                    remember(selected.serial_number)
                elif choice == '7':
                    sets = load_shared_messages()
                    if not sets:
                        raise ValueError('shared_messages.json defines no sets; add {"key": "msg1|msg2"} with an editor')
                    print('Available sets:', ', '.join(sorted(sets)))
                    chosen = ask('Set key: ').strip()
                    if chosen not in sets:
                        raise ValueError(f'No canned set "{chosen}"')
                    messages = validate_messages(sets[chosen].split('|'))
                    reconnect(port, lambda i: set_canned(i, messages),
                              lambda i: i.localNode.moduleConfig.canned_message.enabled and
                              i.getCannedMessage() == messages)
                    dev['device']['canned'] = chosen
                    save_device(dev['path'], dev['device'])
                    remember(selected.serial_number)
                elif choice == '8':
                    slot = shared['frequency_slot']
                    if not confirmed(f'Set EU_868, LONG_FAST slot {slot} and rotary pins 9/10/16?'):
                        continue
                    reconnect(port, lambda i: update_profile(i.localNode, slot),
                              lambda i: profile_matches(i.localNode, slot))
                    remember(selected.serial_number)
                elif choice == '9':
                    enter_dfu(port)
                elif choice == '10':
                    if not confirmed('Apply every setting from the JSON files?'):
                        continue
                    apply_one(selected.serial_number, dev['kind'], dev['device'], shared, dev['path'])
                elif choice == '12':
                    if dev['kind'] != 'beacon':
                        raise ValueError('Power mode switching applies to beacon devices only')
                    iface = connect_serial(selected.serial_number)
                    try:
                        stealth = not stealth_mode_matches(iface, True)
                    finally:
                        safe_close(iface)
                    label = ('STEALTH TRACKER (dark screen, sleepy cycles, deaf between wakes)' if stealth
                             else 'INTERACTIVE CLIENT (screen wakes on rotary, normal cycles)')
                    if not confirmed(f'Switch beacon to {label}?'):
                        continue
                    reconnect_serial(selected.serial_number, lambda i: set_stealth_mode(i, stealth),
                                     lambda i: stealth_mode_matches(i, stealth), settle_seconds=12)
                    print('Power mode applied and read-back verified.'
                          ' Apply all does not change it; run this action again to switch back.')
                elif choice == '11':
                    anchor = session['path'] or candidates['beacon']
                    for path in (anchor, sibling_path(anchor)):
                        if not path.exists() and confirmed(f'{path.name} does not exist — create it now?'):
                            prompt_device(path)
                    apply_pair(anchor, shared)  # prints the plan, confirms, applies
                else:
                    print('Unknown choice.')
            except Exception as exc:  # device stacks raise arbitrary types
                print('Could not complete operation:', exc, file=sys.stderr)
    except (OSError, ValueError, KeyError) as exc:
        print('Could not start:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
