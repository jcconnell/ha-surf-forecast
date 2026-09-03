"""Sensor platform for the Surf Forecast integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from . import SurfConfigEntry, surf
from .coordinator import SurfData, SurfForecastCoordinator
from .entity import SurfForecastEntity


@dataclass(frozen=True, kw_only=True)
class SurfSensorDescription(SensorEntityDescription):
    """Describes a Surf Forecast sensor."""

    value_fn: Callable[[SurfData], StateType | datetime]
    attr_fn: Callable[[SurfData], dict[str, Any]] | None = None


def _hour(key: str) -> Callable[[SurfData], StateType]:
    """Read a parameter from the forecast hour nearest to now."""

    def _value(data: SurfData) -> StateType:
        return (data.current or {}).get(key)

    return _value


def _bearing_attrs(key: str) -> Callable[[SurfData], dict[str, Any]]:
    """Expose the compass point alongside a bearing in degrees."""

    def _attrs(data: SurfData) -> dict[str, Any]:
        return {"compass": surf.compass_point((data.current or {}).get(key))}

    return _attrs


def _tide(field: str) -> Callable[[SurfData], datetime | None]:
    """Read the timestamp of an upcoming tide extreme."""

    def _value(data: SurfData) -> datetime | None:
        tide = data.tide.get(field)
        return tide["time"] if tide else None

    return _value


def _tide_attrs(field: str) -> Callable[[SurfData], dict[str, Any]]:
    """Expose the height of an upcoming tide extreme."""

    def _attrs(data: SurfData) -> dict[str, Any]:
        tide = data.tide.get(field)
        return {"height_m": tide["height"] if tide else None}

    return _attrs


def _astronomy(key: str) -> Callable[[SurfData], Any]:
    """Read a value from today's astronomy entry."""

    def _value(data: SurfData) -> Any:
        return (data.astronomy_today or {}).get(key)

    return _value


SENSORS: tuple[SurfSensorDescription, ...] = (
    # --- Headline judgement ---
    SurfSensorDescription(
        key="surf_rating",
        translation_key="surf_rating",
        icon="mdi:surfing",
        native_unit_of_measurement="/10",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.rating.get("rating"),
        attr_fn=lambda data: {
            "height_score": data.rating.get("height_score"),
            "period_score": data.rating.get("period_score"),
            "wind_score": data.rating.get("wind_score"),
            "conditions": data.conditions,
            "forecast": data.forecast,
        },
    ),
    SurfSensorDescription(
        key="surf_conditions",
        translation_key="surf_conditions",
        icon="mdi:waves",
        value_fn=lambda data: data.conditions,
    ),
    SurfSensorDescription(
        key="wind_relation",
        translation_key="wind_relation",
        icon="mdi:weather-windy",
        value_fn=lambda data: data.wind_relation,
        # The shore-direction sanity check belongs with the sensor it affects.
        attr_fn=lambda data: {
            "mean_wave_from": data.shore_check.get("mean_wave_from"),
            "offset_from_onshore": data.shore_check.get("offset_from_onshore"),
            "shore_direction_suspect": data.shore_check.get("suspect"),
        },
    ),
    SurfSensorDescription(
        key="best_window",
        translation_key="best_window",
        icon="mdi:clock-star-four-points-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: (data.best_window or {}).get("time"),
        attr_fn=lambda data: {
            "rating": (data.best_window or {}).get("rating"),
            "conditions": (data.best_window or {}).get("conditions"),
            "wave_height_m": (data.best_window or {}).get("wave_height"),
        },
    ),
    # --- Waves and swell ---
    SurfSensorDescription(
        key="wave_height",
        translation_key="wave_height",
        icon="mdi:waves",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_hour("waveHeight"),
    ),
    SurfSensorDescription(
        key="wave_period",
        translation_key="wave_period",
        icon="mdi:sine-wave",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_hour("wavePeriod"),
    ),
    SurfSensorDescription(
        key="wave_direction",
        translation_key="wave_direction",
        icon="mdi:compass-outline",
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=0,
        value_fn=_hour("waveDirection"),
        attr_fn=_bearing_attrs("waveDirection"),
    ),
    SurfSensorDescription(
        key="swell_height",
        translation_key="swell_height",
        icon="mdi:waves",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_hour("swellHeight"),
    ),
    SurfSensorDescription(
        key="swell_period",
        translation_key="swell_period",
        icon="mdi:sine-wave",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_hour("swellPeriod"),
    ),
    SurfSensorDescription(
        key="swell_direction",
        translation_key="swell_direction",
        icon="mdi:compass-outline",
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=0,
        value_fn=_hour("swellDirection"),
        attr_fn=_bearing_attrs("swellDirection"),
    ),
    SurfSensorDescription(
        key="secondary_swell_height",
        translation_key="secondary_swell_height",
        icon="mdi:waves",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=_hour("secondarySwellHeight"),
    ),
    SurfSensorDescription(
        key="secondary_swell_period",
        translation_key="secondary_swell_period",
        icon="mdi:sine-wave",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=_hour("secondarySwellPeriod"),
    ),
    SurfSensorDescription(
        key="secondary_swell_direction",
        translation_key="secondary_swell_direction",
        icon="mdi:compass-outline",
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=_hour("secondarySwellDirection"),
        attr_fn=_bearing_attrs("secondarySwellDirection"),
    ),
    SurfSensorDescription(
        key="wind_wave_height",
        translation_key="wind_wave_height",
        icon="mdi:waves",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=_hour("windWaveHeight"),
    ),
    SurfSensorDescription(
        key="wind_wave_period",
        translation_key="wind_wave_period",
        icon="mdi:sine-wave",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=_hour("windWavePeriod"),
    ),
    # --- Wind ---
    SurfSensorDescription(
        key="wind_speed",
        translation_key="wind_speed",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_hour("windSpeed"),
    ),
    SurfSensorDescription(
        key="wind_gust",
        translation_key="wind_gust",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_hour("gust"),
    ),
    SurfSensorDescription(
        key="wind_direction",
        translation_key="wind_direction",
        icon="mdi:compass-outline",
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=0,
        value_fn=_hour("windDirection"),
        attr_fn=_bearing_attrs("windDirection"),
    ),
    # --- Water ---
    SurfSensorDescription(
        key="water_temperature",
        translation_key="water_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_hour("waterTemperature"),
    ),
    SurfSensorDescription(
        key="air_temperature",
        translation_key="air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_hour("airTemperature"),
    ),
    SurfSensorDescription(
        key="current_speed",
        translation_key="current_speed",
        icon="mdi:arrow-right-bold-outline",
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=_hour("currentSpeed"),
    ),
    SurfSensorDescription(
        key="current_direction",
        translation_key="current_direction",
        icon="mdi:compass-outline",
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=_hour("currentDirection"),
        attr_fn=_bearing_attrs("currentDirection"),
    ),
    # --- Tide ---
    SurfSensorDescription(
        key="tide_state",
        translation_key="tide_state",
        icon="mdi:waves-arrow-up",
        value_fn=lambda data: data.tide.get("state"),
        attr_fn=lambda data: {
            "station": (data.station or {}).get("name"),
            "station_distance_km": (data.station or {}).get("distance"),
        },
    ),
    SurfSensorDescription(
        key="next_high_tide",
        translation_key="next_high_tide",
        icon="mdi:waves-arrow-up",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_tide("next_high"),
        attr_fn=_tide_attrs("next_high"),
    ),
    SurfSensorDescription(
        key="next_low_tide",
        translation_key="next_low_tide",
        icon="mdi:waves-arrow-right",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_tide("next_low"),
        attr_fn=_tide_attrs("next_low"),
    ),
    # --- Astronomy ---
    SurfSensorDescription(
        key="sunrise",
        translation_key="sunrise",
        icon="mdi:weather-sunset-up",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_astronomy("sunrise"),
    ),
    SurfSensorDescription(
        key="sunset",
        translation_key="sunset",
        icon="mdi:weather-sunset-down",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_astronomy("sunset"),
    ),
    SurfSensorDescription(
        key="moon_phase",
        translation_key="moon_phase",
        icon="mdi:moon-waning-crescent",
        entity_registry_enabled_default=False,
        value_fn=_astronomy("moonPhase"),
    ),
    SurfSensorDescription(
        key="moon_illumination",
        translation_key="moon_illumination",
        icon="mdi:moon-waning-crescent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            None
            if (fraction := (data.astronomy_today or {}).get("moonFraction")) is None
            else round(fraction * 100, 1)
        ),
    ),
    # --- Diagnostics ---
    SurfSensorDescription(
        key="requests_remaining",
        translation_key="requests_remaining",
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.requests_remaining,
        attr_fn=lambda data: {
            "daily_quota": data.daily_quota,
            "quota_exhausted": data.quota_blocked,
        },
    ),
    SurfSensorDescription(
        key="last_forecast_fetch",
        translation_key="last_forecast_fetch",
        icon="mdi:cloud-download-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_weather_fetch,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SurfConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the surf sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        SurfForecastSensor(coordinator, description, hass) for description in SENSORS
    )


class SurfForecastSensor(SurfForecastEntity, SensorEntity):
    """A single derived value for a surf spot."""

    entity_description: SurfSensorDescription

    # The rated hourly forecast is far too large to write to the database on
    # every state change.
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(
        self,
        coordinator: SurfForecastCoordinator,
        description: SurfSensorDescription,
        hass: HomeAssistant,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

        # Wave heights in metres are unhelpful to a surfer on the US unit
        # system, whose default for distance would otherwise be yards.
        if description.device_class == SensorDeviceClass.DISTANCE:
            self._attr_suggested_unit_of_measurement = (
                UnitOfLength.FEET
                if hass.config.units is US_CUSTOMARY_SYSTEM
                else UnitOfLength.METERS
            )

    @property
    def native_value(self) -> StateType | datetime:
        """Return the sensor's current value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the supporting detail for this sensor."""
        if self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(self.coordinator.data)
