"""Surf quality scoring.

Pure functions with no Home Assistant or network dependencies, so the rating
model can be exercised directly in unit tests.

The model answers three questions a surfer asks in order:
  1. Is there enough swell to ride?   -> height_score
  2. Does it have any power behind it? -> period_score
  3. Is the wind wrecking it?          -> wind_score

Size gates everything: a glassy 12 second groundswell is still a zero if the
ocean is flat, so the height score multiplies rather than averages.
"""

from __future__ import annotations

import math

# Re-exported so callers of this module keep a single scoring entry point.
from .geo import angle_difference, compass_point  # noqa: F401

# Below this the spot is not rideable at all.
MIN_RIDEABLE_HEIGHT = 0.3
# Above this it is closing out / beyond most surfers, but never scored zero.
MAX_RIDEABLE_HEIGHT = 6.0
OVERSIZED_FLOOR = 0.15

# Wind speeds in m/s.
GLASSY_WIND = 1.5
FULL_WIND_INFLUENCE = 10.0
STRONG_OFFSHORE = 9.0

# Swell periods in seconds.
MIN_PERIOD = 5.0
MAX_PERIOD = 15.0
MIN_PERIOD_SCORE = 0.1

CONDITION_FLAT = "Flat"
CONDITION_POOR = "Poor"
CONDITION_FAIR = "Fair"
CONDITION_GOOD = "Good"
CONDITION_VERY_GOOD = "Very good"
CONDITION_EPIC = "Epic"

CONDITION_BANDS = (
    (1.0, CONDITION_FLAT),
    (3.0, CONDITION_POOR),
    (5.0, CONDITION_FAIR),
    (7.0, CONDITION_GOOD),
    (8.5, CONDITION_VERY_GOOD),
    (10.01, CONDITION_EPIC),
)

WIND_OFFSHORE = "Offshore"
WIND_CROSS_OFFSHORE = "Cross-offshore"
WIND_CROSS_SHORE = "Cross-shore"
WIND_CROSS_ONSHORE = "Cross-onshore"
WIND_ONSHORE = "Onshore"
WIND_GLASSY = "Glassy"

WIND_BANDS = (
    (30.0, WIND_OFFSHORE),
    (75.0, WIND_CROSS_OFFSHORE),
    (105.0, WIND_CROSS_SHORE),
    (150.0, WIND_CROSS_ONSHORE),
    (180.01, WIND_ONSHORE),
)

# A wave component arriving from further than this off the shore normal has to
# cross land to reach the break. The margin past 90 degrees allows for
# refraction around the ends of a beach.
SWELL_WINDOW = 100.0

# Stormglass's partitions of the sea state: (height, period, direction) keys.
WAVE_COMPONENTS = (
    ("swellHeight", "swellPeriod", "swellDirection"),
    ("secondarySwellHeight", "secondarySwellPeriod", "secondarySwellDirection"),
    ("windWaveHeight", "windWavePeriod", "windWaveDirection"),
)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Constrain value to the inclusive range [low, high]."""
    return max(low, min(high, value))


def offshore_offset(wind_from: float, shore_direction: float) -> float:
    """Degrees between the wind and a perfectly offshore wind.

    ``shore_direction`` is the bearing the beach faces out to sea, so a truly
    offshore wind arrives from the opposite bearing. Returns 0 for dead
    offshore through 180 for dead onshore.
    """
    return angle_difference(wind_from, (shore_direction + 180.0) % 360.0)


def surf_at_break(
    hour: dict, shore_direction: float
) -> tuple[float | None, float | None]:
    """Height and period of the sea that can actually reach the break.

    Stormglass's ``waveHeight`` is the whole open-water sea state at the grid
    point, including local wind chop running along or away from the coast.
    On a south-facing Hawaiian break the trade-wind sea from the east-northeast
    can double that figure while never touching the beach. So combine only the
    components arriving from seaward, as significant heights add: the root of
    the summed squares. The period is the dominant such component's.

    Falls back to ``waveHeight`` and ``swellPeriod`` when the forecast has no
    component breakdown to filter.
    """
    energy = 0.0
    dominant: tuple[float, float | None] | None = None
    have_components = False
    for height_key, period_key, direction_key in WAVE_COMPONENTS:
        height = hour.get(height_key)
        if height is None:
            continue
        have_components = True
        direction = hour.get(direction_key)
        if (
            direction is not None
            and angle_difference(direction, shore_direction) > SWELL_WINDOW
        ):
            continue
        energy += height * height
        if dominant is None or height > dominant[0]:
            dominant = (height, hour.get(period_key))

    if not have_components:
        return hour.get("waveHeight"), hour.get("swellPeriod") or hour.get(
            "wavePeriod"
        )
    if dominant is None:
        # Everything out there is heading the wrong way: the break is flat.
        return 0.0, None
    period = dominant[1] or hour.get("swellPeriod") or hour.get("wavePeriod")
    return round(math.sqrt(energy), 2), period


def wind_relation(
    wind_from: float | None,
    shore_direction: float,
    wind_speed: float | None = None,
) -> str | None:
    """Classify the wind relative to the shore, or call it glassy if calm."""
    if wind_from is None:
        return None
    if wind_speed is not None and wind_speed <= GLASSY_WIND:
        return WIND_GLASSY
    offset = offshore_offset(wind_from, shore_direction)
    for limit, label in WIND_BANDS:
        if offset < limit:
            return label
    return WIND_ONSHORE


def height_score(
    height: float | None,
    ideal_min: float,
    ideal_max: float,
) -> float:
    """Score wave size from 0 (flat) to 1 (in the ideal band)."""
    if height is None or height <= MIN_RIDEABLE_HEIGHT:
        return 0.0
    if height < ideal_min:
        span = ideal_min - MIN_RIDEABLE_HEIGHT
        if span <= 0:
            return 1.0
        return clamp((height - MIN_RIDEABLE_HEIGHT) / span)
    if height <= ideal_max:
        return 1.0
    if height >= MAX_RIDEABLE_HEIGHT:
        return OVERSIZED_FLOOR
    span = MAX_RIDEABLE_HEIGHT - ideal_max
    if span <= 0:
        return OVERSIZED_FLOOR
    decay = (height - ideal_max) / span
    return clamp(1.0 - (1.0 - OVERSIZED_FLOOR) * decay, OVERSIZED_FLOOR, 1.0)


def period_score(period: float | None) -> float:
    """Score swell period from 0.1 (wind slop) to 1 (long groundswell)."""
    if period is None or period <= 0:
        return 0.0
    if period <= MIN_PERIOD:
        return MIN_PERIOD_SCORE
    if period >= MAX_PERIOD:
        return 1.0
    fraction = (period - MIN_PERIOD) / (MAX_PERIOD - MIN_PERIOD)
    return clamp(MIN_PERIOD_SCORE + (1.0 - MIN_PERIOD_SCORE) * fraction)


def wind_score(
    wind_speed: float | None,
    wind_from: float | None,
    shore_direction: float,
) -> float:
    """Score how much the wind helps or ruins the surface, 0 to 1.

    Calm air is ideal whatever its direction. As the wind builds, direction
    matters more; a hard offshore is penalised too, because it holds waves up
    and eventually stops them breaking cleanly.
    """
    if wind_speed is None:
        return 0.5
    if wind_speed <= GLASSY_WIND:
        return 1.0
    if wind_from is None:
        return 0.5

    offset = offshore_offset(wind_from, shore_direction)
    # 1.0 dead offshore, 0.5 cross-shore, 0.0 dead onshore.
    direction_quality = (math.cos(math.radians(offset)) + 1.0) / 2.0

    influence = clamp(
        (wind_speed - GLASSY_WIND) / (FULL_WIND_INFLUENCE - GLASSY_WIND)
    )
    score = 1.0 - influence * (1.0 - direction_quality)

    if wind_speed > STRONG_OFFSHORE and direction_quality > 0.5:
        score *= clamp(1.0 - (wind_speed - STRONG_OFFSHORE) / 12.0, 0.4, 1.0)

    return clamp(score)


def surf_rating(
    wave_height: float | None,
    swell_period: float | None,
    wind_speed: float | None,
    wind_from: float | None,
    shore_direction: float,
    ideal_min: float,
    ideal_max: float,
) -> dict[str, float | None]:
    """Combine the component scores into a 0-10 rating.

    Returns the rating alongside its components so the sensor can expose the
    reasoning as attributes instead of an unexplained number.
    """
    h = height_score(wave_height, ideal_min, ideal_max)
    p = period_score(swell_period)
    w = wind_score(wind_speed, wind_from, shore_direction)

    # Cleanliness and power modulate the rating, size gates it.
    quality = 0.40 * p + 0.60 * w
    rating = 10.0 * h * (0.15 + 0.85 * quality)
    rating = round(clamp(rating, 0.0, 10.0), 1)

    return {
        "rating": rating,
        "height_score": round(h, 3),
        "period_score": round(p, 3),
        "wind_score": round(w, 3),
    }


def conditions_text(rating: float | None) -> str | None:
    """Map a 0-10 rating onto a human readable condition."""
    if rating is None:
        return None
    for limit, label in CONDITION_BANDS:
        if rating < limit:
            return label
    return CONDITION_EPIC
