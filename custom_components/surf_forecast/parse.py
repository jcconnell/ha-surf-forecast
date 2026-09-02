"""Normalisation of raw Stormglass payloads.

Pure functions with no Home Assistant or network dependencies. Everything
here turns Stormglass's source-keyed JSON into flat dictionaries with real
datetimes, so the coordinator and entities never touch the wire format.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from .const import PREFERRED_SOURCES

TIDE_HIGH = "high"
TIDE_LOW = "low"

TIDE_RISING = "Rising"
TIDE_FALLING = "Falling"


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a Stormglass ISO timestamp into an aware UTC datetime.

    Stormglass mixes ``2019-03-15 03:40:44+00:00`` and
    ``2018-01-19T17:00:00+00:00`` between endpoints; both are accepted.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def pick_value(
    source_map: Any,
    preferred: tuple[str, ...] = PREFERRED_SOURCES,
) -> float | None:
    """Pick the best available value from Stormglass's {source: value} map.

    Stormglass's own blended model ("sg") wins when present; otherwise fall
    back through known providers and finally to any numeric value at all.
    """
    if isinstance(source_map, (int, float)):
        return float(source_map)
    if not isinstance(source_map, dict):
        return None
    for source in preferred:
        if source in source_map:
            value = _as_float(source_map[source])
            if value is not None:
                return value
    for value in source_map.values():
        parsed = _as_float(value)
        if parsed is not None:
            return parsed
    return None


def _as_float(value: Any) -> float | None:
    """Coerce a value to float, tolerating the strings Stormglass sometimes returns."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_hours(payload: dict | None) -> list[dict[str, Any]]:
    """Flatten the weather endpoint's ``hours`` array.

    Each entry becomes ``{"time": datetime, "waveHeight": float, ...}``.
    """
    if not isinstance(payload, dict):
        return []
    hours: list[dict[str, Any]] = []
    for raw in payload.get("hours") or []:
        if not isinstance(raw, dict):
            continue
        timestamp = parse_timestamp(raw.get("time"))
        if timestamp is None:
            continue
        entry: dict[str, Any] = {"time": timestamp}
        for key, value in raw.items():
            if key == "time":
                continue
            parsed = pick_value(value)
            if parsed is not None:
                entry[key] = parsed
        hours.append(entry)
    hours.sort(key=lambda item: item["time"])
    return hours


def normalize_tides(payload: dict | None) -> list[dict[str, Any]]:
    """Flatten the tide extremes endpoint into sorted high/low events."""
    if not isinstance(payload, dict):
        return []
    tides: list[dict[str, Any]] = []
    for raw in payload.get("data") or []:
        if not isinstance(raw, dict):
            continue
        timestamp = parse_timestamp(raw.get("time"))
        height = _as_float(raw.get("height"))
        if timestamp is None or height is None:
            continue
        tide_type = str(raw.get("type") or "").lower()
        if tide_type not in (TIDE_HIGH, TIDE_LOW):
            continue
        tides.append({"time": timestamp, "height": height, "type": tide_type})
    tides.sort(key=lambda item: item["time"])
    return tides


def normalize_astronomy(payload: dict | None) -> list[dict[str, Any]]:
    """Flatten the astronomy endpoint into one entry per day."""
    if not isinstance(payload, dict):
        return []
    days: list[dict[str, Any]] = []
    for raw in payload.get("data") or []:
        if not isinstance(raw, dict):
            continue
        timestamp = parse_timestamp(raw.get("time"))
        if timestamp is None:
            continue
        entry: dict[str, Any] = {"time": timestamp}
        for key in (
            "sunrise",
            "sunset",
            "moonrise",
            "moonset",
            "civilDawn",
            "civilDusk",
            "nauticalDawn",
            "nauticalDusk",
            "astronomicalDawn",
            "astronomicalDusk",
        ):
            entry[key] = parse_timestamp(raw.get(key))
        entry["moonFraction"] = _as_float(raw.get("moonFraction"))
        moon_phase = raw.get("moonPhase")
        if isinstance(moon_phase, dict):
            current = moon_phase.get("current")
            if isinstance(current, dict):
                entry["moonPhase"] = current.get("text")
                entry["moonPhaseValue"] = _as_float(current.get("value"))
        days.append(entry)
    days.sort(key=lambda item: item["time"])
    return days


def nearest_hour(
    hours: list[dict[str, Any]],
    now: datetime,
    tolerance: timedelta = timedelta(hours=2),
) -> dict[str, Any] | None:
    """Return the forecast hour closest to ``now``, or None if none is close."""
    if not hours:
        return None
    closest = min(hours, key=lambda item: abs(item["time"] - now))
    if abs(closest["time"] - now) > tolerance:
        return None
    return closest


def covers(hours: list[dict[str, Any]], moment: datetime) -> bool:
    """Whether the cached forecast still spans the given moment."""
    if not hours:
        return False
    return hours[0]["time"] <= moment <= hours[-1]["time"]


def horizon_end(entries: list[dict[str, Any]]) -> datetime | None:
    """Timestamp of the last entry, or None when empty."""
    if not entries:
        return None
    return entries[-1]["time"]


def tide_context(
    tides: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Work out the surrounding tide state from the extremes list."""
    previous = None
    next_high = None
    next_low = None
    upcoming = None

    for tide in tides:
        if tide["time"] <= now:
            previous = tide
            continue
        if upcoming is None:
            upcoming = tide
        if tide["type"] == TIDE_HIGH and next_high is None:
            next_high = tide
        elif tide["type"] == TIDE_LOW and next_low is None:
            next_low = tide

    state = None
    if upcoming is not None:
        state = TIDE_RISING if upcoming["type"] == TIDE_HIGH else TIDE_FALLING

    return {
        "state": state,
        "previous": previous,
        "next": upcoming,
        "next_high": next_high,
        "next_low": next_low,
    }


def astronomy_for_day(
    days: list[dict[str, Any]],
    moment: datetime,
) -> dict[str, Any] | None:
    """Return the astronomy entry covering the UTC day of ``moment``."""
    if not days:
        return None
    target: date = moment.astimezone(timezone.utc).date()
    for entry in days:
        if entry["time"].date() == target:
            return entry
    # Fall back to the next entry still in the future, then the last known day.
    for entry in days:
        if entry["time"].date() >= target:
            return entry
    return days[-1]
