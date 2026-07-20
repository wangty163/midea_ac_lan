"""Regression tests for the connection watchdog task lifecycle."""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry

import custom_components.midea_ac_lan as integration
from custom_components.midea_ac_lan import connection_watchdog

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from midealocal.device import MideaDevice


class _FakeDevice:
    """Minimal Midea device used by integration setup."""

    def __init__(self) -> None:
        self.update_callbacks: list[Any] = []
        self.opened = False
        self.device_id = 123456
        self.close_socket_calls = 0

    def register_update(self, callback: Any) -> None:  # noqa: ANN401
        """Record the integration update callback."""
        self.update_callbacks.append(callback)

    def open(self) -> None:
        """Record that setup opened the connection."""
        self.opened = True

    def close_socket(self) -> None:
        """Record a forced reconnect request."""
        self.close_socket_calls += 1


class _FakeConfigEntries:
    """Minimal config-entry manager used by integration setup."""

    async def async_forward_entry_setups(
        self,
        config_entry: ConfigEntry,
        platforms: list[str],
    ) -> None:
        """Pretend platform setup completed."""


class _ReloadConfigEntries:
    """Model Home Assistant's detached config-entry reload scheduling."""

    def __init__(self, hass: _FakeHass) -> None:
        self.hass = hass
        self.scheduled_entry_ids: list[str] = []
        self.reload_task: asyncio.Task[Any] | None = None
        self.reload_finished = asyncio.Event()

    def async_schedule_reload(self, entry_id: str) -> None:
        """Schedule reload in a task not owned by the config entry."""
        self.scheduled_entry_ids.append(entry_id)
        self.reload_task = self.hass.async_create_task(
            self.async_reload(entry_id),
            f"config entry reload {entry_id}",
        )

    async def async_reload(self, entry_id: str) -> bool:
        """Represent the separately scheduled unload/setup sequence.

        Returns
        -------
        True after the simulated reload completes.

        """
        assert self.scheduled_entry_ids == [entry_id]
        assert self.reload_task is asyncio.current_task()
        await asyncio.sleep(0)
        self.reload_finished.set()
        return True


class _FakeHass:
    """Track normal and background tasks in separate buckets."""

    def __init__(self, device: _FakeDevice) -> None:
        self.config_entries: Any = _FakeConfigEntries()
        self.data: dict[str, Any] = {}
        self.device = device
        self.is_stopping = False
        self.startup_tasks: set[asyncio.Task[Any]] = set()
        self.background_tasks: set[asyncio.Task[Any]] = set()

    async def async_add_import_executor_job(
        self,
        target: Any,  # noqa: ANN401, ARG002
        *args: Any,  # noqa: ANN401, ARG002
    ) -> _FakeDevice:
        """Return the prepared fake device.

        Returns
        -------
        The fake device assigned to this Home Assistant instance.

        """
        return self.device

    async def async_add_executor_job(  # noqa: PLR6301
        self,
        target: Any,  # noqa: ANN401
        *args: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        """Run the supplied executor target inline for the test.

        Returns
        -------
        The executor target's return value.

        """
        return target(*args)

    def async_create_task(
        self,
        target: Coroutine[Any, Any, Any],
        name: str | None = None,
        eager_start: bool = False,  # noqa: ARG002
    ) -> asyncio.Task[Any]:
        """Create a startup-tracked task like Home Assistant does.

        Returns
        -------
        The newly scheduled startup task.

        """
        task = asyncio.create_task(target, name=name)
        self.startup_tasks.add(task)
        task.add_done_callback(self.startup_tasks.discard)
        return task

    def async_create_background_task(
        self,
        target: Coroutine[Any, Any, Any],
        name: str,
        eager_start: bool = False,  # noqa: ARG002
    ) -> asyncio.Task[Any]:
        """Create a task that is excluded from startup tracking.

        Returns
        -------
        The newly scheduled background task.

        """
        task = asyncio.create_task(target, name=name)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task


class WatchdogTaskLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Keep the long-lived watchdog out of Home Assistant startup tracking."""

    async def test_watchdog_is_entry_owned_background_task(self) -> None:  # noqa: PLR6301
        """The watchdog must not block startup and must cancel on entry unload."""
        device = _FakeDevice()
        hass = _FakeHass(device)
        config_entry = ConfigEntry(
            version=2,
            minor_version=1,
            domain="midea_ac_lan",
            title="Test appliance",
            data={
                "type": 0xAC,
                "name": "Test appliance",
                "device_id": 123456,
                "token": "",
                "key": "",
                "ip_address": "192.0.2.1",
                "port": 6444,
                "model": "test",
                "protocol": 2,
            },
            source="user",
        )
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def forever_watchdog(*args: Any) -> None:  # noqa: ANN401, ARG001
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        with patch.object(integration, "watch_connection", new=forever_watchdog):
            result = await integration.async_setup_entry(hass, config_entry)

        assert result
        await asyncio.wait_for(started.wait(), timeout=1)
        assert device.opened
        assert hass.startup_tasks == set()
        entry_background_tasks = config_entry._background_tasks  # noqa: SLF001
        assert len(entry_background_tasks) == 1
        watchdog = next(iter(entry_background_tasks))
        assert watchdog.get_name() == "midea_ac_lan_watchdog_123456"
        assert len(config_entry._on_unload or []) == 1  # noqa: SLF001

        await config_entry._async_process_on_unload(hass)  # noqa: SLF001
        await asyncio.wait_for(cancelled.wait(), timeout=1)

        assert watchdog.cancelled()
        assert hass.background_tasks == set()

    async def test_failed_reconnect_schedules_reload_outside_watchdog(  # noqa: PLR6301
        self,
    ) -> None:
        """A watchdog must not cancel itself by awaiting its entry reload."""
        device = _FakeDevice()
        hass = _FakeHass(device)
        reload_manager = _ReloadConfigEntries(hass)
        hass.config_entries = reload_manager
        entry = SimpleNamespace(entry_id="test-entry")
        health = connection_watchdog.ConnectionHealth(
            last_status=time.monotonic() - 1,
        )

        with patch.multiple(
            connection_watchdog,
            STARTUP_GRACE_SECONDS=0,
            STALE_AFTER_SECONDS=0,
            RECONNECT_GRACE_SECONDS=0,
            CHECK_INTERVAL_SECONDS=0,
        ):
            await asyncio.wait_for(
                connection_watchdog.watch_connection(
                    hass,
                    entry,
                    cast("MideaDevice", device),
                    health,
                ),
                timeout=1,
            )

        await asyncio.wait_for(reload_manager.reload_finished.wait(), timeout=1)
        assert device.close_socket_calls == 1
        assert reload_manager.scheduled_entry_ids == ["test-entry"]
        assert reload_manager.reload_task is not None
        assert reload_manager.reload_task.done()
        assert not reload_manager.reload_task.cancelled()


if __name__ == "__main__":
    unittest.main()
