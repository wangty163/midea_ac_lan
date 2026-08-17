"""Tests for the model-specific clothes-dryer program select."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest import TestCase
from unittest.mock import patch

from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError

from custom_components.midea_ac_lan import select
from custom_components.midea_ac_lan.const import DEVICES, DOMAIN
from custom_components.midea_ac_lan.midea_devices import MIDEA_DEVICES
from custom_components.midea_ac_lan.protocol_patches import (
    DRYER_PROGRAM,
    DRYER_PROGRAMS,
)

if TYPE_CHECKING:
    from midealocal.device import MideaDevice


class FakeDryer:
    """Minimal dryer used by the select entity tests."""

    device_type = 0xDC
    device_id = 123
    name = "Dryer"
    subtype = 14388

    def __init__(self) -> None:
        """Initialize fake dryer state."""
        self.attributes: dict[str, object] = {
            "power": False,
            "program": "mixed_wash",
            "status": "idle",
        }
        self.set_calls: list[tuple[str, str]] = []

    def get_attribute(self, attr: str) -> object:
        """Return a test attribute.

        Returns:
            The stored attribute value.

        """
        return self.attributes[attr]

    def set_attribute(self, attr: str, value: str) -> None:
        """Record a control call."""
        self.set_calls.append((attr, value))


class TestDryerProgramEntity(TestCase):
    """Verify the dryer program select and its safety boundaries."""

    @staticmethod
    def test_program_select_is_default_only_for_verified_subtype() -> None:
        """Only subtype 14388 should receive the default program select."""
        entities = MIDEA_DEVICES[0xDC]["entities"]
        assert isinstance(entities, dict)
        config = entities[DRYER_PROGRAM]
        assert isinstance(config, dict)
        assert config["type"] == Platform.SELECT
        assert config["default"] is True
        assert config["subtypes"] == {14388}
        assert list(config["options"]) == list(DRYER_PROGRAMS)

        for subtype, expected_count in ((14388, 1), (9999, 0)):
            device = SimpleNamespace(
                device_type=0xDC,
                device_id=123,
                name="Dryer",
                subtype=subtype,
            )
            hass = SimpleNamespace(data={DOMAIN: {DEVICES: {123: device}}})
            config_entry = SimpleNamespace(data={"device_id": 123}, options={})
            added_entities: list[object] = []

            with patch.object(
                select,
                "MideaSelect",
                return_value=DRYER_PROGRAM,
            ):
                asyncio.run(
                    select.async_setup_entry(
                        hass,
                        config_entry,
                        added_entities.extend,
                    ),
                )

            assert len(added_entities) == expected_count

    def test_program_change_requires_powered_standby_dryer(self) -> None:
        """Changing the program must never power on or start the dryer."""
        device = FakeDryer()
        entity = select.MideaSelect(cast("MideaDevice", device), DRYER_PROGRAM)

        assert entity.current_option == "mixed_wash"

        with self.assertRaises(HomeAssistantError):  # noqa: PT027
            entity.select_option("small_piece_dry")
        assert device.set_calls == []

        device.attributes["power"] = True
        device.attributes["status"] = "start"
        with self.assertRaises(HomeAssistantError):  # noqa: PT027
            entity.select_option("small_piece_dry")
        assert device.set_calls == []

        device.attributes["status"] = "standby"
        entity.select_option("small_piece_dry")
        assert device.set_calls == [("program", "small_piece_dry")]

    @staticmethod
    def test_unknown_reported_program_is_not_exposed_as_invalid_option() -> None:
        """An unknown device value should yield no current select option."""
        device = FakeDryer()
        device.attributes["program"] = 255
        entity = select.MideaSelect(cast("MideaDevice", device), DRYER_PROGRAM)

        assert entity.current_option is None

    @staticmethod
    def test_program_options_have_english_and_chinese_translations() -> None:
        """Every exposed option should have a readable UI label."""
        translations = (
            Path(__file__).parents[1]
            / "custom_components"
            / "midea_ac_lan"
            / "translations"
        )
        for language in ("en", "zh-Hans"):
            data = json.loads(
                (translations / f"{language}.json").read_text(encoding="utf-8"),
            )
            program = data["entity"]["select"]["dryer_program"]
            assert set(program["state"]) == set(DRYER_PROGRAMS)
