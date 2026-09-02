"""The Surf Forecast integration, backed by the Stormglass API."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import SurfForecastCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

SurfConfigEntry = ConfigEntry[SurfForecastCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SurfConfigEntry) -> bool:
    """Set up a surf spot from a config entry."""
    coordinator = SurfForecastCoordinator(hass, entry)

    # Load the persisted payloads first so a restart does not spend quota.
    await coordinator.async_load_cache()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    coordinator.async_start_hourly_refresh()
    entry.async_on_unload(coordinator.async_stop_hourly_refresh)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SurfConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: SurfConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
