"""Runtime protocol extensions for locally verified Midea DB/DC appliances.

The installed midea-local release does not yet expose several fields that are
present in Midea's model-specific Lua codecs.  Keep the compatibility patch in
the integration so a Home Assistant container recreation cannot erase it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from midealocal.device import MideaDevice
from midealocal.devices.db import MideaDBDevice
from midealocal.devices.db.message import DBGeneralMessageBody, MessageDBBase
from midealocal.devices.dc import MideaDCDevice
from midealocal.devices.dc.message import DCGeneralMessageBody, MessageDCBase
from midealocal.message import ListTypes, MessageType

_LOGGER = logging.getLogger(__name__)

CHILD_LOCK = "child_lock"
DOOR_OPENED = "door_opened"
DETERGENT_LACK = "detergent_lack"
SOFTENER_LACK = "softener_lack"
DRYER_LIGHT = "light"
PREVENT_WRINKLE_SWITCH = "prevent_wrinkle_switch"
REMIND_SOUND = "remind_sound"
STEAM_SWITCH = "steam_switch"
DAMP_DRY_SIGNAL = "damp_dry_signal"
ECO_DRY_SWITCH = "eco_dry_switch"
BUCKET_CLEAN_SWITCH = "bucket_clean_switch"

_PATCH_MARKER = "_midea_ac_lan_protocol_patch_applied"


class MessageDBChildLock(MessageDBBase):
    """Set the front-load washer child-lock bit."""

    def __init__(self, protocol_version: int, enabled: bool) -> None:
        super().__init__(
            protocol_version=protocol_version,
            message_type=MessageType.set,
            body_type=ListTypes.X02,
        )
        self.enabled = enabled

    @property
    def _body(self) -> bytearray:
        # Exact fixed-width 0202 frame emitted by the model-specific Midea
        # codec used by this appliance. byte13 bit5 is the child lock; 0xff
        # leaves unrelated fields unchanged and the reserved tail is 0x3f.
        body = bytearray([0xFF] * 21)
        body[12] = 0x20 if self.enabled else 0x00
        body[20] = 0x3F
        return body


class MessageDCChildLock(MessageDCBase):
    """Set the clothes-dryer child-lock two-bit field."""

    def __init__(self, protocol_version: int, enabled: bool) -> None:
        super().__init__(
            protocol_version=protocol_version,
            message_type=MessageType.set,
            body_type=ListTypes.X02,
        )
        self.enabled = enabled

    @property
    def _body(self) -> bytearray:
        # Midea codec T_0000_DC_14388: full-frame byte23 bits4..5.
        # Other two-bit fields use 0b11 to mean "unchanged".
        body = bytearray([0xFF] * 32)
        body[11] = 0xDF if self.enabled else 0xCF
        body[27] = 0x00
        return body


def _patch_message_decoders() -> None:
    original_db_init = DBGeneralMessageBody.__init__
    original_dc_init = DCGeneralMessageBody.__init__

    def db_init(self: DBGeneralMessageBody, body: bytearray) -> None:
        original_db_init(self, body)
        if len(body) > 13:
            self.child_lock = bool(body[13] & 0x20)
        if len(body) > 31:
            self.detergent_lack = bool(body[31] & 0x10)
            self.softener_lack = bool(body[31] & 0x20)
            self.door_opened = bool(body[31] & 0x40)

    def dc_init(self: DCGeneralMessageBody, body: bytearray) -> None:
        original_dc_init(self, body)
        if len(body) > 12:
            packed = body[12]
            self.prevent_wrinkle_switch = ((packed & 0x0C) >> 2) == 1
            self.child_lock = ((packed & 0x30) >> 4) == 1
            self.light = ((packed & 0xC0) >> 6) == 1
        if len(body) > 13:
            packed = body[13]
            self.remind_sound = (packed & 0x03) == 1
            self.steam_switch = ((packed & 0x30) >> 4) == 1
            self.damp_dry_signal = ((packed & 0xC0) >> 6) == 1
        if len(body) > 19:
            packed = body[19]
            self.eco_dry_switch = (packed & 0x03) == 1
            self.bucket_clean_switch = ((packed & 0x0C) >> 2) == 1

    DBGeneralMessageBody.__init__ = db_init
    DCGeneralMessageBody.__init__ = dc_init


def _patch_devices() -> None:
    original_db_init = MideaDBDevice.__init__
    original_dc_init = MideaDCDevice.__init__
    original_db_set = MideaDBDevice.set_attribute
    original_dc_set = MideaDCDevice.set_attribute

    def db_init(self: MideaDBDevice, *args: Any, **kwargs: Any) -> None:
        original_db_init(self, *args, **kwargs)
        self._attributes.update(
            {
                CHILD_LOCK: False,
                DOOR_OPENED: False,
                DETERGENT_LACK: False,
                SOFTENER_LACK: False,
            }
        )

    def dc_init(self: MideaDCDevice, *args: Any, **kwargs: Any) -> None:
        original_dc_init(self, *args, **kwargs)
        self._attributes.update(
            {
                CHILD_LOCK: False,
                DRYER_LIGHT: False,
                PREVENT_WRINKLE_SWITCH: False,
                REMIND_SOUND: False,
                STEAM_SWITCH: False,
                DAMP_DRY_SIGNAL: False,
                ECO_DRY_SWITCH: False,
                BUCKET_CLEAN_SWITCH: False,
            }
        )

    def db_set(self: MideaDBDevice, attr: str, value: bool | int | str) -> None:
        if attr == CHILD_LOCK:
            if not isinstance(value, bool):
                raise TypeError("[db] child_lock expects bool")
            self.build_send(MessageDBChildLock(self._message_protocol_version, value))
            return
        original_db_set(self, attr, value)

    def dc_set(self: MideaDCDevice, attr: str, value: bool | int | str) -> None:
        if attr == CHILD_LOCK:
            if not isinstance(value, bool):
                raise TypeError("[dc] child_lock expects bool")
            self.build_send(MessageDCChildLock(self._message_protocol_version, value))
            return
        original_dc_set(self, attr, value)

    MideaDBDevice.__init__ = db_init
    MideaDBDevice.set_attribute = db_set
    MideaDCDevice.__init__ = dc_init
    MideaDCDevice.set_attribute = dc_set


def _patch_interruptible_reconnect() -> None:
    """Make close/reload stop a device even during exponential backoff."""

    def connect_loop(self: MideaDevice) -> None:
        retries = 0
        while self._is_run and self._socket is None:
            if self.connect(check_protocol=True):
                return
            self.close_socket()
            retries += 1
            delay = min(5 * (2 ** (retries - 1)), 60)
            _LOGGER.warning(
                "[%s] Unable to connect, sleep %s seconds and retry",
                self.device_id,
                delay,
            )
            deadline = time.monotonic() + delay
            while self._is_run and time.monotonic() < deadline:
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    MideaDevice._connect_loop = connect_loop


def apply_midealocal_patches() -> None:
    """Apply the compatibility patches exactly once per interpreter."""

    if getattr(MideaDevice, _PATCH_MARKER, False):
        return
    _patch_message_decoders()
    _patch_devices()
    _patch_interruptible_reconnect()
    setattr(MideaDevice, _PATCH_MARKER, True)
