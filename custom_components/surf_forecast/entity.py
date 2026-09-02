"""Shared entity base for the Surf Forecast integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SurfForecastCoordinator


class SurfForecastEntity(CoordinatorEntity[SurfForecastCoordinator]):
    """Base entity tying every sensor to the surf spot's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SurfForecastCoordinator, key: str) -> None:
        """Initialise the entity for one spot."""
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Stormglass",
            model="Surf spot",
            configuration_url="https://dashboard.stormglass.io",
        )
