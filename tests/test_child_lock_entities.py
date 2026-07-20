"""Tests for washer and dryer child-lock entities."""

import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from homeassistant.const import Platform

from custom_components.midea_ac_lan import switch
from custom_components.midea_ac_lan.const import DEVICES, DOMAIN
from custom_components.midea_ac_lan.midea_devices import MIDEA_DEVICES
from custom_components.midea_ac_lan.protocol_patches import CHILD_LOCK


class TestChildLockEntities(TestCase):
    """Verify child locks use the standard switch platform."""

    @staticmethod
    def test_washer_and_dryer_child_locks_are_default_switches() -> None:
        """Washer and dryer child locks should be enabled switch entities."""
        for device_type in (0xDB, 0xDC):
            config = MIDEA_DEVICES[device_type]["entities"][CHILD_LOCK]
            assert config["type"] == Platform.SWITCH
            assert config["default"] is True

    @staticmethod
    def test_default_switch_is_created_without_extra_switch_option() -> None:
        """Default switches should not require selection in integration options."""
        device = SimpleNamespace(device_type=0xDB)
        hass = SimpleNamespace(data={DOMAIN: {DEVICES: {1: device}}})
        config_entry = SimpleNamespace(data={"device_id": 1}, options={})
        added_entities = []

        with (
            patch.object(
                switch,
                "MIDEA_DEVICES",
                {
                    0xDB: {
                        "entities": {
                            CHILD_LOCK: {
                                "type": Platform.SWITCH,
                                "default": True,
                            },
                        },
                    },
                },
            ),
            patch.object(switch, "MideaSwitch", return_value=CHILD_LOCK),
        ):
            asyncio.run(
                switch.async_setup_entry(
                    hass,
                    config_entry,
                    added_entities.extend,
                ),
            )

        assert added_entities == [CHILD_LOCK]
