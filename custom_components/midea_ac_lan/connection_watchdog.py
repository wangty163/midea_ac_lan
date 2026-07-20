"""Self-healing connection watchdog for Midea LAN devices."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from midealocal.device import MideaDevice

_LOGGER = logging.getLogger(__name__)

STARTUP_GRACE_SECONDS = 180
STALE_AFTER_SECONDS = 300
RECONNECT_GRACE_SECONDS = 120
CHECK_INTERVAL_SECONDS = 30


@dataclass
class ConnectionHealth:
    """Mutable connection freshness state shared with the socket callback."""

    last_status: float
    reconnect_requested_at: float | None = None


async def watch_connection(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: MideaDevice,
    health: ConnectionHealth,
) -> None:
    """Recover a stale socket, then reload only if the thread cannot recover."""

    await asyncio.sleep(STARTUP_GRACE_SECONDS)
    while not hass.is_stopping:
        now = time.monotonic()
        stale_for = now - health.last_status
        if stale_for <= STALE_AFTER_SECONDS:
            health.reconnect_requested_at = None
        elif health.reconnect_requested_at is None:
            health.reconnect_requested_at = now
            _LOGGER.warning(
                "Appliance [%s] has no status update for %.0fs; forcing socket reconnect",
                device.device_id,
                stale_for,
            )
            await hass.async_add_executor_job(device.close_socket)
        elif now - health.reconnect_requested_at >= RECONNECT_GRACE_SECONDS:
            _LOGGER.error(
                "Appliance [%s] did not recover after socket reconnect; reloading entry",
                device.device_id,
            )
            await hass.config_entries.async_reload(entry.entry_id)
            return
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
