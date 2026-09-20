"""Quota-aware data coordinator for the Surf Forecast integration.

The free Stormglass plan allows only 10 requests per day, so this coordinator
is built around spending as few as possible:

* Every endpoint is fetched for its full horizon in a single request.
* Raw payloads are persisted, so restarting Home Assistant costs nothing.
* Tide and astronomy are deterministic, so they are refetched far less often
  than the weather.
* Derived values are recomputed hourly from the cache for free, which keeps
  "current conditions" honest between the sparse network fetches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import geo, parse, surf
from .api import (
    StormglassAuthError,
    StormglassClient,
    StormglassError,
    StormglassQuotaError,
)
from .const import (
    ASTRONOMY_CACHE_HOURS,
    ASTRONOMY_HORIZON_DAYS,
    CONF_API_KEY,
    CONF_ENABLE_ASTRONOMY,
    CONF_ENABLE_TIDE,
    CONF_FORECAST_DAYS,
    CONF_GOOD_SURF_THRESHOLD,
    CONF_IDEAL_MAX_HEIGHT,
    CONF_IDEAL_MIN_HEIGHT,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_SHORE_DIRECTION,
    CONF_TIDE_DATUM,
    CONF_UPDATE_INTERVAL,
    DEFAULT_FORECAST_DAYS,
    DEFAULT_GOOD_SURF_THRESHOLD,
    DEFAULT_IDEAL_MAX_HEIGHT,
    DEFAULT_IDEAL_MIN_HEIGHT,
    DEFAULT_SHORE_DIRECTION,
    DEFAULT_TIDE_DATUM,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    ISSUE_SHORE_DIRECTION,
    QUOTA_RESERVE,
    SHORE_CHECK_MAX_OFFSET,
    SHORE_CHECK_MIN_COHERENCE,
    SHORE_CHECK_MIN_HOURS,
    STORAGE_KEY,
    STORAGE_VERSION,
    TIDE_CACHE_HOURS,
    TIDE_HORIZON_DAYS,
)

_LOGGER = logging.getLogger(__name__)

# How many forecast hours to expose as an attribute. Bounded so the recorder
# is not asked to store an unreasonable blob every state change.
FORECAST_ATTRIBUTE_HOURS = 24


@dataclass(slots=True)
class SurfData:
    """Everything the entities need for one surf spot."""

    hours: list[dict[str, Any]] = field(default_factory=list)
    tides: list[dict[str, Any]] = field(default_factory=list)
    astronomy: list[dict[str, Any]] = field(default_factory=list)
    current: dict[str, Any] | None = None
    rating: dict[str, Any] = field(default_factory=dict)
    conditions: str | None = None
    wind_relation: str | None = None
    tide: dict[str, Any] = field(default_factory=dict)
    astronomy_today: dict[str, Any] | None = None
    best_window: dict[str, Any] | None = None
    forecast: list[dict[str, Any]] = field(default_factory=list)
    requests_remaining: int | None = None
    daily_quota: int | None = None
    station: dict[str, Any] | None = None
    shore_check: dict[str, Any] = field(default_factory=dict)
    last_weather_fetch: datetime | None = None
    quota_blocked: bool = False


class SurfForecastCoordinator(DataUpdateCoordinator[SurfData]):
    """Fetch, cache and derive surf conditions for a single spot."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Set up the coordinator from a config entry."""
        self.entry = entry
        self._store: Store = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
        )

        self._weather_payload: dict[str, Any] | None = None
        self._tide_payload: dict[str, Any] | None = None
        self._astronomy_payload: dict[str, Any] | None = None
        self._weather_fetched: datetime | None = None
        self._tide_fetched: datetime | None = None
        self._astronomy_fetched: datetime | None = None
        self._quota_date: str | None = None
        self._quota_blocked = False
        self._unsub_hourly: Any = None

        self.client = StormglassClient(
            async_get_clientsession(hass),
            entry.data[CONF_API_KEY],
            entry.data[CONF_LATITUDE],
            entry.data[CONF_LONGITUDE],
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=timedelta(hours=self.update_interval_hours),
        )

    # --- Configuration accessors -------------------------------------------

    def _option(self, key: str, default: Any) -> Any:
        """Read an option, falling back to entry data then the default."""
        return self.entry.options.get(key, self.entry.data.get(key, default))

    @property
    def update_interval_hours(self) -> int:
        """Hours between network refreshes."""
        return int(self._option(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))

    @property
    def forecast_days(self) -> int:
        """How many days of hourly forecast to request."""
        return int(self._option(CONF_FORECAST_DAYS, DEFAULT_FORECAST_DAYS))

    @property
    def shore_direction(self) -> float:
        """Bearing the beach faces out to sea."""
        return float(
            self._option(CONF_SHORE_DIRECTION, DEFAULT_SHORE_DIRECTION)
        )

    @property
    def ideal_min_height(self) -> float:
        """Lower bound of the preferred wave height band, in metres."""
        return float(self._option(CONF_IDEAL_MIN_HEIGHT, DEFAULT_IDEAL_MIN_HEIGHT))

    @property
    def ideal_max_height(self) -> float:
        """Upper bound of the preferred wave height band, in metres."""
        return float(self._option(CONF_IDEAL_MAX_HEIGHT, DEFAULT_IDEAL_MAX_HEIGHT))

    @property
    def good_surf_threshold(self) -> float:
        """Rating at or above which the surf counts as good."""
        return float(
            self._option(CONF_GOOD_SURF_THRESHOLD, DEFAULT_GOOD_SURF_THRESHOLD)
        )

    @property
    def tide_datum(self) -> str:
        """Reference datum for tide heights."""
        return str(self._option(CONF_TIDE_DATUM, DEFAULT_TIDE_DATUM))

    @property
    def tide_enabled(self) -> bool:
        """Whether to spend a request on tide data."""
        return bool(self._option(CONF_ENABLE_TIDE, True))

    @property
    def astronomy_enabled(self) -> bool:
        """Whether to spend a request on astronomy data."""
        return bool(self._option(CONF_ENABLE_ASTRONOMY, True))

    # --- Persistence --------------------------------------------------------

    async def async_load_cache(self) -> None:
        """Restore payloads saved by a previous run, so a restart is free."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return

        self._weather_payload = stored.get("weather")
        self._tide_payload = stored.get("tide")
        self._astronomy_payload = stored.get("astronomy")
        self._weather_fetched = parse.parse_timestamp(stored.get("weather_fetched"))
        self._tide_fetched = parse.parse_timestamp(stored.get("tide_fetched"))
        self._astronomy_fetched = parse.parse_timestamp(
            stored.get("astronomy_fetched")
        )

        quota = stored.get("quota")
        if isinstance(quota, dict):
            self._quota_date = quota.get("date")
            if self._quota_date == dt_util.utcnow().date().isoformat():
                self.client.daily_quota = quota.get("daily_quota")
                self.client.request_count = quota.get("request_count")

        _LOGGER.debug(
            "Restored Stormglass cache for %s (weather fetched %s)",
            self.entry.title,
            self._weather_fetched,
        )

    async def _async_save_cache(self) -> None:
        """Persist raw payloads and the quota counters."""
        await self._store.async_save(
            {
                "weather": self._weather_payload,
                "tide": self._tide_payload,
                "astronomy": self._astronomy_payload,
                "weather_fetched": _iso(self._weather_fetched),
                "tide_fetched": _iso(self._tide_fetched),
                "astronomy_fetched": _iso(self._astronomy_fetched),
                "quota": {
                    "date": dt_util.utcnow().date().isoformat(),
                    "daily_quota": self.client.daily_quota,
                    "request_count": self.client.request_count,
                },
            }
        )

    # --- Hourly recompute ---------------------------------------------------

    @callback
    def async_start_hourly_refresh(self) -> None:
        """Recompute derived values on the hour without spending quota."""

        @callback
        def _recompute(_now: datetime) -> None:
            if self._weather_payload is None:
                return
            data = self._build_data()
            if not parse.covers(data.hours, dt_util.utcnow()):
                # The cache has run out. Publishing an empty snapshot here
                # would mark the coordinator successful again and hide a
                # genuine fetch failure, so leave the state alone.
                return
            # Not async_set_updated_data: that restarts the refresh timer, and
            # firing it every hour would push the network fetch back forever.
            self.data = data
            self.async_update_listeners()

        self._unsub_hourly = async_track_time_change(
            self.hass, _recompute, minute=0, second=5
        )

    @callback
    def async_stop_hourly_refresh(self) -> None:
        """Cancel the hourly recompute timer."""
        if self._unsub_hourly is not None:
            self._unsub_hourly()
            self._unsub_hourly = None

    # --- Fetching -----------------------------------------------------------

    def _reset_quota_if_new_day(self, now: datetime) -> None:
        """Clear the exhausted-quota flag once Stormglass rolls over to a new UTC day."""
        today = now.date().isoformat()
        if self._quota_date != today:
            self._quota_date = today
            self._quota_blocked = False
            self.client.request_count = None

    def _may_spend(self, essential: bool = False) -> bool:
        """Whether another request is allowed right now."""
        if self._quota_blocked:
            return False
        remaining = self.client.requests_remaining
        if remaining is None:
            return True
        if essential:
            return remaining > 0
        return remaining > QUOTA_RESERVE

    async def _async_update_data(self) -> SurfData:
        """Refresh whatever has aged out, then derive the current picture."""
        now = dt_util.utcnow()
        self._reset_quota_if_new_day(now)

        cached_hours = parse.normalize_hours(self._weather_payload)
        cached_tides = parse.normalize_tides(self._tide_payload)
        cached_astronomy = parse.normalize_astronomy(self._astronomy_payload)

        weather_ttl = timedelta(hours=self.update_interval_hours) * 0.75
        need_weather = (
            self._weather_payload is None
            or self._weather_fetched is None
            or (now - self._weather_fetched) >= weather_ttl
            or not parse.covers(cached_hours, now)
        )
        need_tide = self.tide_enabled and _is_stale(
            self._tide_payload,
            self._tide_fetched,
            cached_tides,
            now,
            timedelta(hours=TIDE_CACHE_HOURS),
            timedelta(days=1),
        )
        need_astronomy = self.astronomy_enabled and _is_stale(
            self._astronomy_payload,
            self._astronomy_fetched,
            cached_astronomy,
            now,
            timedelta(hours=ASTRONOMY_CACHE_HOURS),
            timedelta(days=1),
        )

        fetched_anything = False
        errors: list[str] = []

        if need_weather and not self._may_spend(essential=True):
            # The forecast is the one dataset we cannot do without, so a
            # skipped fetch has to be reported rather than silently ignored.
            errors.append(
                "Daily Stormglass request limit reached, no forecast fetched"
            )
        elif need_weather:
            try:
                self._weather_payload = await self.client.async_get_weather(
                    start=now.replace(minute=0, second=0, microsecond=0),
                    end=now + timedelta(days=self.forecast_days),
                )
                self._weather_fetched = now
                fetched_anything = True
            except StormglassAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except StormglassQuotaError as err:
                self._quota_blocked = True
                errors.append(str(err))
            except StormglassError as err:
                errors.append(str(err))

        if need_tide and self._may_spend():
            try:
                self._tide_payload = await self.client.async_get_tide_extremes(
                    start=now,
                    end=now + timedelta(days=TIDE_HORIZON_DAYS),
                    datum=self.tide_datum,
                )
                self._tide_fetched = now
                fetched_anything = True
            except StormglassAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except StormglassQuotaError as err:
                self._quota_blocked = True
                errors.append(str(err))
            except StormglassError as err:
                errors.append(str(err))

        if need_astronomy and self._may_spend():
            try:
                self._astronomy_payload = await self.client.async_get_astronomy(
                    start=now,
                    end=now + timedelta(days=ASTRONOMY_HORIZON_DAYS),
                )
                self._astronomy_fetched = now
                fetched_anything = True
            except StormglassAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except StormglassQuotaError as err:
                self._quota_blocked = True
                errors.append(str(err))
            except StormglassError as err:
                errors.append(str(err))

        if fetched_anything:
            await self._async_save_cache()

        data = self._build_data()
        self._async_update_shore_issue(data)

        if errors and not parse.covers(data.hours, now):
            # Nothing usable cached either, so this really is a failure.
            raise UpdateFailed("; ".join(errors))
        if errors:
            _LOGGER.warning(
                "Surf Forecast for %s is serving cached data: %s",
                self.entry.title,
                "; ".join(errors),
            )

        return data

    # --- Derivation ---------------------------------------------------------

    def _build_data(self) -> SurfData:
        """Turn the cached payloads into the values the entities publish."""
        now = dt_util.utcnow()
        hours = parse.normalize_hours(self._weather_payload)
        tides = parse.normalize_tides(self._tide_payload)
        astronomy = parse.normalize_astronomy(self._astronomy_payload)

        current = parse.nearest_hour(hours, now)
        rating = self._rate(current)
        astronomy_today = parse.astronomy_for_day(astronomy, now)

        return SurfData(
            hours=hours,
            tides=tides,
            astronomy=astronomy,
            current=current,
            rating=rating,
            conditions=surf.conditions_text(rating.get("rating")),
            wind_relation=surf.wind_relation(
                (current or {}).get("windDirection"),
                self.shore_direction,
                (current or {}).get("windSpeed"),
            ),
            tide=parse.tide_context(tides, now),
            astronomy_today=astronomy_today,
            best_window=self._best_window(hours, astronomy_today, now),
            forecast=self._forecast_summary(hours, now),
            requests_remaining=self.client.requests_remaining,
            daily_quota=self.client.daily_quota,
            shore_check=self._shore_check(hours),
            station=_station(self._tide_payload),
            last_weather_fetch=self._weather_fetched,
            quota_blocked=self._quota_blocked,
        )

    def _shore_check(self, hours: list[dict[str, Any]]) -> dict[str, Any]:
        """Sanity check the configured shore direction against the swell.

        Waves cannot reach a break from inland, so if the forecast has swell
        arriving consistently from behind the beach, the shore direction is
        almost certainly wrong. This uses data already fetched, so it costs
        nothing.
        """
        # Swell only. The combined waveDirection includes local wind chop,
        # which at a lee-side break blows straight out from behind the beach
        # and would condemn a correct shore direction.
        samples: list[tuple[float, float]] = []
        for hour in hours:
            direction = hour.get("swellDirection")
            if direction is not None:
                samples.append((direction, hour.get("swellHeight") or 1.0))
        if len(samples) < SHORE_CHECK_MIN_HOURS:
            return {}

        mean, coherence = geo.circular_mean(samples)
        if mean is None:
            return {}

        offset = geo.angle_difference(mean, self.shore_direction)
        return {
            "mean_wave_from": round(mean, 1),
            "coherence": round(coherence, 2),
            "offset_from_onshore": round(offset, 1),
            "hours": len(samples),
            # Beyond 90 degrees the swell would have crossed land to arrive.
            "suspect": (
                offset > SHORE_CHECK_MAX_OFFSET
                and coherence >= SHORE_CHECK_MIN_COHERENCE
            ),
        }

    @callback
    def _async_update_shore_issue(self, data: SurfData) -> None:
        """Raise or clear the repair issue for an implausible shore direction."""
        issue_id = f"{ISSUE_SHORE_DIRECTION}_{self.entry.entry_id}"
        check = data.shore_check

        if not check.get("suspect"):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        likely = round(check["mean_wave_from"]) % 360
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_SHORE_DIRECTION,
            translation_placeholders={
                "name": self.entry.title,
                "configured": f"{self.shore_direction:.0f}",
                "wave_from": f"{check['mean_wave_from']:.0f}",
                "offset": f"{check['offset_from_onshore']:.0f}",
                "suggested": f"{likely}",
            },
        )

    def _rate(self, hour: dict[str, Any] | None) -> dict[str, Any]:
        """Score one forecast hour."""
        if not hour:
            return {}
        height, period = surf.surf_at_break(hour, self.shore_direction)
        rating = surf.surf_rating(
            wave_height=height,
            swell_period=period,
            wind_speed=hour.get("windSpeed"),
            wind_from=hour.get("windDirection"),
            shore_direction=self.shore_direction,
            ideal_min=self.ideal_min_height,
            ideal_max=self.ideal_max_height,
        )
        rating["surf_height"] = height
        rating["surf_period"] = period
        return rating

    def _forecast_summary(
        self,
        hours: list[dict[str, Any]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Build a compact rated forecast for the next day."""
        summary: list[dict[str, Any]] = []
        for hour in hours:
            if hour["time"] < now:
                continue
            if len(summary) >= FORECAST_ATTRIBUTE_HOURS:
                break
            rating = self._rate(hour)
            summary.append(
                {
                    "time": hour["time"].isoformat(),
                    "rating": rating.get("rating"),
                    "conditions": surf.conditions_text(rating.get("rating")),
                    "wave_height": hour.get("waveHeight"),
                    "surf_height": rating.get("surf_height"),
                    "swell_period": rating.get("surf_period"),
                    "wind_speed": hour.get("windSpeed"),
                    "wind_relation": surf.wind_relation(
                        hour.get("windDirection"),
                        self.shore_direction,
                        hour.get("windSpeed"),
                    ),
                }
            )
        return summary

    def _best_window(
        self,
        hours: list[dict[str, Any]],
        astronomy_today: dict[str, Any] | None,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Find the best-rated daylight hour still to come today."""
        sunrise = (astronomy_today or {}).get("sunrise")
        sunset = (astronomy_today or {}).get("sunset")
        end_of_day = now + timedelta(hours=24)

        best: dict[str, Any] | None = None
        for hour in hours:
            moment = hour["time"]
            if moment < now or moment > end_of_day:
                continue
            if sunrise and sunset and not (sunrise <= moment <= sunset):
                continue
            rating = self._rate(hour)
            score = rating.get("rating")
            if score is None:
                continue
            if best is None or score > best["rating"]:
                best = {
                    "time": moment,
                    "rating": score,
                    "conditions": surf.conditions_text(score),
                    "wave_height": rating.get("surf_height"),
                }
        return best


def _is_stale(
    payload: dict[str, Any] | None,
    fetched: datetime | None,
    entries: list[dict[str, Any]],
    now: datetime,
    max_age: timedelta,
    min_horizon: timedelta,
) -> bool:
    """Whether a deterministic dataset needs refetching."""
    if payload is None or fetched is None or not entries:
        return True
    if (now - fetched) >= max_age:
        return True
    end = parse.horizon_end(entries)
    return end is None or end - now < min_horizon


def _station(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract the tide station description from a tide payload."""
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    station = meta.get("station")
    return station if isinstance(station, dict) else None


def _iso(value: datetime | None) -> str | None:
    """Serialise a datetime for the store."""
    return value.isoformat() if value else None
