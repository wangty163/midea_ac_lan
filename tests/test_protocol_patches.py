"""Regression tests for locally verified DB/DC protocol extensions."""

from __future__ import annotations

import importlib.util
import threading
import time
import unittest
from pathlib import Path

module_path = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "midea_ac_lan"
    / "protocol_patches.py"
)
spec = importlib.util.spec_from_file_location(
    "midea_protocol_patches_test",
    module_path,
)
assert spec is not None and spec.loader is not None
protocol_patches = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol_patches)
protocol_patches.apply_midealocal_patches()
DBGeneralMessageBody = protocol_patches.DBGeneralMessageBody
DCGeneralMessageBody = protocol_patches.DCGeneralMessageBody
MessageDBChildLock = protocol_patches.MessageDBChildLock
MessageDCChildLock = protocol_patches.MessageDCChildLock


class ProtocolPatchTests(unittest.TestCase):
    def test_reconnect_backoff_stops_promptly_on_close(self) -> None:
        class FakeDevice:
            _is_run = True
            _socket = None
            device_id = 1

            def connect(self, check_protocol=False):
                return False

            def close_socket(self):
                self._socket = None

        device = FakeDevice()
        worker = threading.Thread(
            target=protocol_patches.MideaDevice._connect_loop,
            args=(device,),
        )
        worker.start()
        time.sleep(0.05)
        device._is_run = False
        worker.join(0.6)
        self.assertFalse(worker.is_alive())

    def test_washer_child_lock_commands_match_official_codec(self) -> None:
        self.assertEqual(
            MessageDBChildLock(0, True).serialize().hex(),
            "aa20db0000000000000202ffffffffffffffffffffffff20ffffffffffffff3fb5",
        )
        self.assertEqual(
            MessageDBChildLock(0, False).serialize().hex(),
            "aa20db0000000000000202ffffffffffffffffffffffff00ffffffffffffff3fd5",
        )

    def test_dryer_child_lock_commands_match_official_codec(self) -> None:
        self.assertEqual(
            MessageDCChildLock(0, True).serialize().hex(),
            "aa2bdc0000000000000202ffffffffffffffffffffffdfffffffffffffffffffffffffffffff00ffffffff34",
        )
        self.assertEqual(
            MessageDCChildLock(0, False).serialize().hex(),
            "aa2bdc0000000000000202ffffffffffffffffffffffcfffffffffffffffffffffffffffffff00ffffffff44",
        )

    def test_washer_fixed_report_bits(self) -> None:
        body = bytearray(32)
        body[13] = 0x20
        body[31] = 0x70
        parsed = DBGeneralMessageBody(body)
        self.assertTrue(parsed.child_lock)
        self.assertTrue(parsed.detergent_lack)
        self.assertTrue(parsed.softener_lack)
        self.assertTrue(parsed.door_opened)

    def test_dryer_packed_report_bits(self) -> None:
        body = bytearray(30)
        body[12] = 0x54  # light=1, child_lock=1, prevent_wrinkle=1
        body[13] = 0x51  # damp=1, steam=1, remind=1
        body[19] = 0x05  # bucket clean=1, eco dry=1
        parsed = DCGeneralMessageBody(body)
        self.assertTrue(parsed.child_lock)
        self.assertTrue(parsed.light)
        self.assertTrue(parsed.prevent_wrinkle_switch)
        self.assertTrue(parsed.remind_sound)
        self.assertTrue(parsed.steam_switch)
        self.assertTrue(parsed.damp_dry_signal)
        self.assertTrue(parsed.eco_dry_switch)
        self.assertTrue(parsed.bucket_clean_switch)


if __name__ == "__main__":
    unittest.main()
