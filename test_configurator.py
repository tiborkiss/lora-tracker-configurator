import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import configurator as c
from meshtastic.protobuf import apponly_pb2, channel_pb2, config_pb2, localonly_pb2


class Node:
    def __init__(self):
        self.channels = [channel_pb2.Channel(index=i) for i in range(8)]
        self.localConfig = config_pb2.Config()
        self.moduleConfig = localonly_pb2.LocalModuleConfig()
        self.writes = []
        self.canned = ''

    def writeChannel(self, index):
        self.writes.append(index)

    def writeConfig(self, name):
        self.writes.append(name)

    def set_canned_message(self, text):
        self.canned = text

    def getURL(self, includeAll=True):
        result = apponly_pb2.ChannelSet()
        for channel in self.channels:
            if channel.role == 1 or (includeAll and channel.role == 2):
                result.settings.append(channel.settings)
        result.lora_config.CopyFrom(self.localConfig.lora)
        return 'https://meshtastic.org/e/#' + base64.urlsafe_b64encode(result.SerializeToString()).decode().rstrip('=')


class FakeIface:
    def __init__(self):
        self.localNode = Node()
        self.names = {}

    def getLongName(self):
        return self.names.get('long', '')

    def getShortName(self):
        return self.names.get('short', '')

    def getCannedMessage(self):
        return self.localNode.canned

    def close(self):
        pass


class ChannelTests(unittest.TestCase):
    def test_track_qr_excludes_other_channels(self):
        node = Node()
        track, other = b'\x11' * 32, b'\x22' * 32
        c.update_channel(node, 0, 'track', track, 32)
        c.update_channel(node, 1, 'family', other, 0)
        url = c.pair_url(node, track)
        fragment = url.split('#')[1]
        payload = apponly_pb2.ChannelSet()
        payload.ParseFromString(base64.urlsafe_b64decode(fragment + '=' * (-len(fragment) % 4)))
        self.assertEqual(len(payload.settings), 1)
        self.assertEqual(bytes(payload.settings[0].psk), track)
        self.assertNotIn(other, payload.SerializeToString())
        with self.assertRaises(ValueError):
            c.pair_url(node, other)

    def test_second_channel_write_preserves_primary(self):
        node = Node()
        c.update_channel(node, 0, 'track', b'a' * 32, 32)
        before = node.channels[0].SerializeToString()
        c.update_channel(node, 1, 'family', b'b' * 32, 0)
        self.assertEqual(node.channels[0].SerializeToString(), before)
        self.assertEqual(node.writes, [0, 1])
        self.assertTrue(c.channel_matches(node, 0, 'track', b'a' * 32, 32))
        self.assertTrue(c.channel_matches(node, 1, 'family', b'b' * 32, 0))
        self.assertFalse(c.channel_matches(node, 0, 'track', b'a' * 32, 0))

    def test_workshop_profile_explicit_slot_and_rotary(self):
        node = Node()
        c.update_profile(node, 1)
        self.assertTrue(c.profile_matches(node, 1))
        self.assertEqual(node.writes, ['lora', 'canned_message'])
        node.localConfig.lora.channel_num = 0
        self.assertFalse(c.profile_matches(node, 1))

    def test_public_channel_uses_default_key_and_mqtt_open(self):
        node = Node()
        c.apply_public_channel(node, 1)
        self.assertTrue(c.public_channel_matches(node, 1))
        self.assertEqual(node.writes, [1])
        self.assertEqual(node.channels[1].settings.name, '')
        node.channels[1].settings.psk = b'\x02'
        self.assertFalse(c.public_channel_matches(node, 1))


class SharedTests(unittest.TestCase):
    def test_defaults_and_group_key_persisted_before_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(c, 'SHARED', Path(tmp) / 'shared.json'):
                shared = c.load_shared()
                self.assertEqual(shared['group_name'], 'group')
                self.assertEqual(shared['track_name'], 'track')
                self.assertEqual(shared['frequency_slot'], 1)
                self.assertFalse(shared['public_channel'])
                c.generate_key(shared, 'group_key')
                self.assertEqual(c.get_key(c.load_shared(), 'group_key'), c.get_key(shared, 'group_key'))

    def test_invalid_slot_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'shared.json'
            path.write_text('{"frequency_slot": 0}', encoding='utf-8')
            with patch.object(c, 'SHARED', path):
                with self.assertRaises(ValueError):
                    c.load_shared()

    def test_missing_key_is_refused(self):
        with self.assertRaises(ValueError):
            c.get_key({}, 'group_key')


class DeviceFileTests(unittest.TestCase):
    def test_kind_and_sibling_paths(self):
        self.assertEqual(c.device_kind(Path('5-handheld.json')), 'handheld')
        self.assertEqual(c.device_kind(Path('5-beacon.json')), 'beacon')
        self.assertEqual(c.sibling_path(Path('5-handheld.json')), Path('5-beacon.json'))
        self.assertEqual(c.sibling_path(Path('5-beacon.json')), Path('5-handheld.json'))
        with self.assertRaises(ValueError):
            c.device_kind(Path('5.json'))

    def test_handheld_created_first_then_beacon_shares_pair_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            handheld = Path(tmp) / '2-handheld.json'
            beacon = Path(tmp) / '2-beacon.json'
            answers = iter(['Kid Two', 'K2', 'y', 'workshop'])
            messages = Path(tmp) / 'shared_messages.json'
            messages.write_text('{"workshop": "hi|ho"}', encoding='utf-8')
            with patch.object(c, 'SHARED_MESSAGES', messages), patch('builtins.input', lambda _: next(answers)):
                device = c.prompt_device(handheld)
            self.assertEqual(device['canned'], 'workshop')
            answers = iter(['Beacon Two', 'B2', 'n', ''])
            with patch('builtins.input', lambda _: next(answers)):
                other = c.prompt_device(beacon)
            self.assertEqual(other['track_key'], device['track_key'])
            self.assertNotIn('family_key', other)
            self.assertEqual(c.load_device(handheld)['family_key'], device['family_key'])

    def test_beacon_created_first_generates_pair_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            beacon = Path(tmp) / '7-beacon.json'
            handheld = Path(tmp) / '7-handheld.json'
            answers = iter(['Beacon', 'BCN', 'y', ''])
            with patch('builtins.input', lambda _: next(answers)):
                first = c.prompt_device(beacon)
            answers = iter(['Kid', 'KID', 'n', ''])
            with patch('builtins.input', lambda _: next(answers)):
                second = c.prompt_device(handheld)
            self.assertEqual(first['track_key'], second['track_key'])
            self.assertIn('family_key', second)

    def test_unknown_canned_set_rejected_at_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages = Path(tmp) / 'shared_messages.json'
            messages.write_text('{}', encoding='utf-8')
            answers = iter(['Kid', 'K1', 'y', 'nope'])
            with patch.object(c, 'SHARED_MESSAGES', messages), patch('builtins.input', lambda _: next(answers)):
                with self.assertRaises(ValueError):
                    c.prompt_device(Path(tmp) / '1-handheld.json')

    def test_load_device_requires_family_key_on_handheld(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '3-handheld.json'
            path.write_text('{"track_key": "ab", "long_name": "x", "short_name": "x", "bluetooth": true}',
                            encoding='utf-8')
            with self.assertRaises(ValueError):
                c.load_device(path)

    def test_canned_resolution_uses_shared_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages = Path(tmp) / 'shared_messages.json'
            messages.write_text('{"workshop": "hi|ho"}', encoding='utf-8')
            with patch.object(c, 'SHARED_MESSAGES', messages):
                self.assertEqual(c.resolve_canned({'canned': 'workshop'}), 'hi|ho')
                self.assertIsNone(c.resolve_canned({'canned': None}))
                with self.assertRaises(ValueError):
                    c.resolve_canned({'canned': 'missing'})


class FamilyUrlTests(unittest.TestCase):
    def test_family_url_carries_family_channel_and_radio(self):
        device = {'family_key': 'cd' * 32}
        shared = {'frequency_slot': 1}
        url = c.family_url(device, shared)
        fragment = url.split('#')[1]
        payload = apponly_pb2.ChannelSet()
        payload.ParseFromString(base64.urlsafe_b64decode(fragment + '=' * (-len(fragment) % 4)))
        self.assertEqual(len(payload.settings), 1)
        self.assertEqual(payload.settings[0].name, 'family')
        self.assertEqual(bytes(payload.settings[0].psk), bytes.fromhex('cd' * 32))
        self.assertEqual(payload.lora_config.region, config_pb2.Config.LoRaConfig.EU_868)
        self.assertEqual(payload.lora_config.channel_num, 1)


class ApplyAllTests(unittest.TestCase):
    def shared(self, public=False):
        return {'group_name': 'group', 'group_key': 'ef' * 32, 'track_name': 'track',
                'frequency_slot': 1, 'public_channel': public}

    def run_apply(self, kind, device, shared):
        calls = []
        iface = FakeIface()
        node = iface.localNode

        def set_owner(long_name, short_name):
            calls.append('name')
            iface.names = {'long': long_name, 'short': short_name}

        node.setOwner = set_owner

        def wrap(label, original):
            def wrapper(*args, **kwargs):
                calls.append(label)
                return original(*args, **kwargs)
            return wrapper

        def fake_reconnect(port, action, verify):
            action(iface)
            self.assertTrue(verify(iface), f'verify failed after {calls[-1]}')

        with patch.object(c, 'connect', return_value=iface), \
             patch.object(c, 'reconnect', fake_reconnect), \
             patch('time.sleep'), \
             patch.object(c, 'update_profile', wrap('profile', c.update_profile)), \
             patch.object(c, 'update_channel', wrap('channel', c.update_channel)), \
             patch.object(c, 'apply_public_channel', wrap('public', c.apply_public_channel)), \
             patch.object(c, 'set_bluetooth', wrap('bluetooth', c.set_bluetooth)), \
             patch.object(c, 'set_canned', wrap('canned', c.set_canned)):
            c.apply_all('x', kind, device, shared)
        return calls, iface

    def test_handheld_covers_track_family_group_name_bluetooth_canned(self):
        device = {'track_key': 'ab' * 32, 'family_key': 'cd' * 32, 'long_name': 'Kid', 'short_name': 'K1',
                  'bluetooth': True, 'canned': 'workshop'}
        with tempfile.TemporaryDirectory() as tmp:
            messages = Path(tmp) / 'shared_messages.json'
            messages.write_text('{"workshop": "hi"}', encoding='utf-8')
            with patch.object(c, 'SHARED_MESSAGES', messages):
                calls, iface = self.run_apply('handheld', device, self.shared())
        self.assertEqual(calls, ['profile', 'channel', 'channel', 'channel', 'name', 'bluetooth', 'canned'])
        self.assertTrue(c.channel_matches(iface.localNode, 0, 'track', bytes.fromhex('ab' * 32), 0))
        self.assertTrue(c.channel_matches(iface.localNode, 1, 'family', bytes.fromhex('cd' * 32), 0))
        self.assertTrue(c.channel_matches(iface.localNode, 2, 'group', bytes.fromhex('ef' * 32), 0))
        self.assertEqual(iface.getLongName(), 'Kid')
        self.assertEqual(iface.getCannedMessage(), 'hi')

    def test_beacon_skips_group_and_uses_full_precision(self):
        device = {'track_key': 'ab' * 32, 'long_name': 'BCN', 'short_name': 'BCN', 'bluetooth': False, 'canned': None}
        calls, iface = self.run_apply('beacon', device, self.shared())
        self.assertEqual(calls, ['profile', 'channel', 'name', 'bluetooth'])
        self.assertTrue(c.channel_matches(iface.localNode, 0, 'track', bytes.fromhex('ab' * 32), 32))
        self.assertFalse(iface.localNode.localConfig.bluetooth.enabled)

    def test_public_channel_applied_when_enabled(self):
        device = {'track_key': 'ab' * 32, 'long_name': 'BCN', 'short_name': 'BCN', 'bluetooth': False, 'canned': None}
        calls, iface = self.run_apply('beacon', device, self.shared(public=True))
        self.assertEqual(calls, ['profile', 'channel', 'public', 'name', 'bluetooth'])
        self.assertTrue(c.public_channel_matches(iface.localNode, c.public_index('beacon')))

    def test_missing_group_key_aborts_before_any_write(self):
        device = {'track_key': 'ab' * 32, 'family_key': 'cd' * 32, 'long_name': 'Kid', 'short_name': 'K1',
                  'bluetooth': True, 'canned': None}
        shared = self.shared()
        shared['group_key'] = None
        with patch.object(c, 'reconnect') as reconnect:
            with self.assertRaises(ValueError):
                c.apply_all('x', 'handheld', device, shared)
            reconnect.assert_not_called()


class EnterDfuTests(unittest.TestCase):
    def test_enter_dfu_calls_node(self):
        entered = []

        class FakeNode:
            def enterDFUMode(self):
                entered.append(True)

        class FakeIface:
            localNode = FakeNode()

            def close(self):
                pass

        with patch.object(c, 'connect', return_value=FakeIface()):
            c.enter_dfu('x')
        self.assertEqual(entered, [True])


class MessagesTests(unittest.TestCase):
    def test_messages_use_utf8_byte_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'messages.txt'
            path.write_text('é' * 101, encoding='utf-8')
            with self.assertRaises(ValueError):
                c.messages_from_file(path)
            path.write_text('hello\n# comment\nhelp\n', encoding='utf-8')
            self.assertEqual(c.messages_from_file(path), 'hello|help')


class ReadbackTests(unittest.TestCase):
    def test_readback_failure_is_not_success(self):
        class Fake:
            def close(self):
                pass
        with patch.object(c, 'connect', side_effect=[Fake(), Fake()]), patch('time.sleep'):
            with self.assertRaises(RuntimeError):
                c.reconnect('test', lambda _: None, lambda _: False)


if __name__ == '__main__':
    unittest.main()
