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

    def writeChannel(self, index):
        self.writes.append(index)

    def writeConfig(self, name):
        self.writes.append(name)

    def getURL(self, includeAll=True):
        result = apponly_pb2.ChannelSet()
        for channel in self.channels:
            if channel.role == 1 or (includeAll and channel.role == 2):
                result.settings.append(channel.settings)
        result.lora_config.CopyFrom(self.localConfig.lora)
        return 'https://meshtastic.org/e/#' + base64.urlsafe_b64encode(result.SerializeToString()).decode().rstrip('=')


class FamilyTests(unittest.TestCase):
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

    def test_workshop_profile_explicit_slot_and_rotary(self):
        node = Node()
        c.update_profile(node, 1)
        self.assertTrue(c.profile_matches(node, 1))
        self.assertEqual(node.writes, ['lora', 'canned_message'])
        node.localConfig.lora.channel_num = 0
        self.assertFalse(c.profile_matches(node, 1))

    def test_secret_saved_before_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'local'
            with patch.object(c, 'LOCAL', directory), patch.object(c, 'CONFIG', directory / 'family.json'):
                config = {}
                c.generate_key(config, 'pair_key')
                self.assertEqual(c.get_key(c.load_config(), 'pair_key'), bytes.fromhex(config['pair_key']))
                self.assertEqual(len(c.get_key(config, 'pair_key')), 32)

    def test_messages_use_utf8_byte_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'messages.txt'
            path.write_text('é' * 101, encoding='utf-8')
            with self.assertRaises(ValueError):
                c.messages_from_file(path)
            path.write_text('hello\n# comment\nhelp\n', encoding='utf-8')
            self.assertEqual(c.messages_from_file(path), 'hello|help')

    def test_readback_failure_is_not_success(self):
        class Fake:
            def close(self):
                pass
        with patch.object(c, 'connect', side_effect=[Fake(), Fake()]), patch('time.sleep'):
            with self.assertRaises(RuntimeError):
                c.reconnect('test', lambda _: None, lambda _: False)


if __name__ == '__main__':
    unittest.main()
