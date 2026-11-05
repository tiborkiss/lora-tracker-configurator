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
    def test_pair_only_qr_excludes_group_key(self):
        node = Node()
        pair, group = b'\x11' * 32, b'\x22' * 32
        c.update_channel(node, 0, 'pair', pair, 32)
        c.update_channel(node, 1, 'group', group, 0)
        url = c.pair_url(node, pair)
        fragment = url.split('#')[1]
        payload = apponly_pb2.ChannelSet()
        payload.ParseFromString(base64.urlsafe_b64decode(fragment + '=' * (-len(fragment) % 4)))
        self.assertEqual(len(payload.settings), 1)
        self.assertEqual(bytes(payload.settings[0].psk), pair)
        self.assertNotIn(group, payload.SerializeToString())
        with self.assertRaises(ValueError):
            c.pair_url(node, group)

    def test_group_write_preserves_pair(self):
        node = Node()
        c.update_channel(node, 0, 'pair', b'a' * 32, 32)
        before = node.channels[0].SerializeToString()
        c.update_channel(node, 1, 'group', b'b' * 32, 0)
        self.assertEqual(node.channels[0].SerializeToString(), before)
        self.assertEqual(node.writes, [0, 1])
        self.assertTrue(c.channel_matches(node, 0, 'pair', b'a' * 32, 32))
        self.assertTrue(c.channel_matches(node, 1, 'group', b'b' * 32, 0))
        self.assertFalse(c.channel_matches(node, 0, 'pair', b'a' * 32, 0))

    def test_workshop_profile_explicit_slot_and_rotary(self):
        node = Node()
        c.update_profile(node, 1)
        self.assertTrue(c.profile_matches(node, 1))
        self.assertEqual(node.writes, ['lora', 'canned_message'])
        node.localConfig.lora.channel_num = 0
        self.assertFalse(c.profile_matches(node, 1))


class SharedSecretTests(unittest.TestCase):
    def test_keys_saved_before_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(c, 'SHARED', Path(tmp) / 'shared.json'):
                shared = c.load_shared()
                self.assertEqual(shared['pair_name'], 'pair')
                self.assertEqual(shared['group_name'], 'group')
                c.generate_key(shared, 'pair_key')
                c.generate_key(shared, 'group_key')
                # The generation persisted; a fresh load sees the same secrets.
                reloaded = c.load_shared()
                self.assertEqual(c.get_key(reloaded, 'pair_key'), c.get_key(shared, 'pair_key'))
                self.assertEqual(c.get_key(reloaded, 'group_key'), c.get_key(shared, 'group_key'))
                self.assertEqual(len(c.get_key(reloaded, 'pair_key')), 32)

    def test_missing_key_is_refused(self):
        with self.assertRaises(ValueError):
            c.get_key({}, 'pair_key')


class DeviceConfigTests(unittest.TestCase):
    def test_prompt_device_writes_complete_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '3.json'
            answers = iter(['child', 'Kid Three', 'K3', 'y', ''])
            with patch('builtins.input', lambda _: next(answers)):
                device = c.prompt_device(path)
            self.assertEqual(device['role'], 'child')
            self.assertEqual(device['bluetooth'], True)
            self.assertIsNone(device['canned'])
            self.assertEqual(c.load_device(path)['long_name'], 'Kid Three')

    def test_prompt_device_reads_canned_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '2.json'
            messages = Path(tmp) / 'm.txt'
            messages.write_text('hello\nhelp\n', encoding='utf-8')
            answers = iter(['tracker', 'Träckthäspäck', 'TRK', 'n', str(messages)])
            with patch('builtins.input', lambda _: next(answers)):
                device = c.prompt_device(path)
            self.assertEqual(device['canned'], ['hello', 'help'])

    def test_prompt_device_rejects_bad_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = iter(['friend', 'x', 'x', 'y', ''])
            with patch('builtins.input', lambda _: next(answers)):
                with self.assertRaises(ValueError):
                    c.prompt_device(Path(tmp) / '4.json')

    def test_load_device_rejects_unknown_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '9.json'
            path.write_text('{"role": "friend", "long_name": "x", "short_name": "x"}', encoding='utf-8')
            with self.assertRaises(ValueError):
                c.load_device(path)

    def test_canned_string_validates_stored_lists(self):
        with self.assertRaises(ValueError):
            c.canned_string({'canned': ['a|b']})
        self.assertEqual(c.canned_string({'canned': ['hi', 'ho']}), 'hi|ho')
        self.assertIsNone(c.canned_string({'canned': None}))


class ApplyAllTests(unittest.TestCase):
    def shared(self):
        return {'pair_name': 'pair', 'pair_key': 'ab' * 32, 'group_name': 'group', 'group_key': 'cd' * 32}

    def run_apply(self, device):
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
             patch.object(c, 'set_bluetooth', wrap('bluetooth', c.set_bluetooth)), \
             patch.object(c, 'set_canned', wrap('canned', c.set_canned)):
            c.apply_all('x', device, self.shared())
        return calls, iface

    def test_child_covers_both_channels_name_bluetooth_canned(self):
        device = {'role': 'child', 'long_name': 'Kid', 'short_name': 'K1', 'bluetooth': True, 'canned': ['hi']}
        calls, iface = self.run_apply(device)
        self.assertEqual(calls, ['profile', 'channel', 'channel', 'name', 'bluetooth', 'canned'])
        self.assertTrue(c.channel_matches(iface.localNode, 0, 'pair', bytes.fromhex('ab' * 32), 0))
        self.assertTrue(c.channel_matches(iface.localNode, 1, 'group', bytes.fromhex('cd' * 32), 0))
        self.assertEqual(iface.getLongName(), 'Kid')
        self.assertTrue(iface.localNode.localConfig.bluetooth.enabled)
        self.assertEqual(iface.getCannedMessage(), 'hi')

    def test_tracker_skips_group_and_uses_full_precision(self):
        device = {'role': 'tracker', 'long_name': 'TRK', 'short_name': 'TRK', 'bluetooth': False, 'canned': None}
        calls, iface = self.run_apply(device)
        self.assertEqual(calls, ['profile', 'channel', 'name', 'bluetooth'])
        self.assertTrue(c.channel_matches(iface.localNode, 0, 'pair', bytes.fromhex('ab' * 32), 32))
        self.assertFalse(iface.localNode.localConfig.bluetooth.enabled)

    def test_missing_group_key_aborts_before_any_write(self):
        device = {'role': 'child', 'long_name': 'Kid', 'short_name': 'K1', 'bluetooth': True, 'canned': None}
        shared = {'pair_name': 'pair', 'pair_key': 'ab' * 32, 'group_name': 'group', 'group_key': None}
        with patch.object(c, 'reconnect') as reconnect:
            with self.assertRaises(ValueError):
                c.apply_all('x', device, shared)
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
