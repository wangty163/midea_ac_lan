"""Select for Midea Lan."""

from typing import cast

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE_ID, CONF_SWITCHES, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from midealocal.device import MideaDevice

from .const import DEVICES, DOMAIN
from .midea_devices import MIDEA_DEVICES
from .midea_entity import MideaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up selects for device."""
    device_id = config_entry.data.get(CONF_DEVICE_ID)
    device = hass.data[DOMAIN][DEVICES].get(device_id)
    extra_switches = config_entry.options.get(CONF_SWITCHES, [])
    selects = []
    for entity_key, config in cast(
        "dict",
        MIDEA_DEVICES[device.device_type]["entities"],
    ).items():
        supported_subtypes = config.get("subtypes")
        supports_device = (
            supported_subtypes is None or device.subtype in supported_subtypes
        )
        if (
            config["type"] == Platform.SELECT
            and supports_device
            and (entity_key in extra_switches or config.get("default", False))
        ):
            dev = MideaSelect(device, entity_key)
            selects.append(dev)
    async_add_entities(selects)


class MideaSelect(MideaEntity, SelectEntity):
    """Represent a Midea select."""

    def __init__(self, device: MideaDevice, entity_key: str) -> None:
        """Midea select init."""
        super().__init__(device, entity_key)
        self._attribute = self._config.get("attribute", entity_key)
        self._options_config = self._config.get("options")

    @property
    def options(self) -> list[str]:
        """Return entity options."""
        if isinstance(self._options_config, str):
            return cast("list", getattr(self._device, self._options_config))
        return list(cast("list", self._options_config))

    @property
    def current_option(self) -> str | None:
        """Return entity current option."""
        option = cast("str", self._device.get_attribute(self._attribute))
        return option if option in self.options else None

    def select_option(self, option: str) -> None:
        """Select entity option.

        Raises:
            HomeAssistantError: If the dryer is off or is not in standby.

        """
        if self._config.get("requires_power") and not self._device.get_attribute(
            "power",
        ):
            raise HomeAssistantError(
                "Turn on the dryer before selecting a drying program",
            )
        allowed_statuses = self._config.get("allowed_statuses")
        if (
            allowed_statuses is not None
            and self._device.get_attribute(
                "status",
            )
            not in allowed_statuses
        ):
            raise HomeAssistantError(
                "The drying program can only be changed while the dryer is in standby",
            )
        self._device.set_attribute(self._attribute, option)
