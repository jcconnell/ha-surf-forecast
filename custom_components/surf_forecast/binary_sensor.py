"""Binary sensor platform for the Surf Forecast integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SurfConfigEntry, surf
from .coordinator import SurfData, SurfForecastCoordinator
from .entity import SurfForecastEntity

CLEAN_WIND = (surf.WIND_GLASSY, surf.WIND_OFFSHORE, surf.WIND_CROSS_OFFSHORE)


@dataclass(frozen=True, kw_only=True)
class SurfBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Surf Forecast binary sensor."""

    value_fn: Callable[[SurfForecastCoordinator, SurfData], bool | None]


BINARY_SENSORS: tuple[SurfBinarySensorDescription, ...] = (
    SurfBinarySensorDescription(
        key="good_surf",
        translation_key="good_surf",
        icon="mdi:surfing",
        value_fn=lambda coordinator, data: (
            None
            if (rating := data.rating.get("rating")) is None
            else rating >= coordinator.good_surf_threshold
        ),
    ),
    SurfBinarySensorDescription(
        key="offshore_wind",
        translation_key="offshore_wind",
        icon="mdi:weather-windy",
        value_fn=lambda coordinator, data: (
            None if data.wind_relation is None else data.wind_relation in CLEAN_WIND
        ),
    ),
    SurfBinarySensorDescription(
        key="quota_exhausted",
        translation_key="quota_exhausted",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator, data: data.quota_blocked,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SurfConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the surf binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        SurfForecastBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class SurfForecastBinarySensor(SurfForecastEntity, BinarySensorEntity):
    """A boolean judgement about the current conditions."""

    entity_description: SurfBinarySensorDescription

    def __init__(
        self,
        coordinator: SurfForecastCoordinator,
        description: SurfBinarySensorDescription,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the current state."""
        return self.entity_description.value_fn(self.coordinator, self.coordinator.data)
