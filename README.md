# Surf Forecast for Home Assistant

A Home Assistant integration that turns [Stormglass](https://stormglass.io)
marine data into a surf forecast for a specific break: wave height, swell
period, tide, wind relative to the shore, and a single 0–10 rating that says
whether it is worth going.

Built around one hard constraint: **the free Stormglass plan allows 10 API
requests per day.** Most of the design below exists to stay under that.

## Examples
<img width="425" alt="Light" src="https://github.com/user-attachments/assets/d4fae560-808b-4fcd-9eb6-abdacaa13319" /> <img width="425" alt="dark" src="https://github.com/user-attachments/assets/aa6dc361-67a7-43be-8deb-59fe809660c0" />



## What you get

Each spot you add becomes a device with these entities.

**The judgement**

| Entity | What it tells you |
| --- | --- |
| `Surf rating` | 0–10, with the component scores as attributes |
| `Surf conditions` | Flat / Poor / Fair / Good / Very good / Epic |
| `Wind direction relative to shore` | Glassy, Offshore, Cross-offshore, Cross-shore, Cross-onshore, Onshore |
| `Best window today` | Timestamp of the best-rated remaining daylight hour |
| `Good surf` | On when the rating clears your threshold |
| `Clean wind` | On when the wind is glassy or has an offshore component |

**The numbers** — wave height, period and direction; swell and secondary
swell; wind wave; wind speed, gust and direction; water and air temperature;
current speed and direction.

**Tide and sky** — tide state, next high tide, next low tide, sunrise, sunset,
moon phase and illumination.

**Diagnostics** — API requests remaining, last forecast fetch, and a quota
exhausted problem sensor.

Secondary swell, wind wave, current and moon entities are disabled by default;
enable them per entity if you want them.

## How the rating works

Three questions, asked in the order a surfer asks them.

1. **Is there enough swell?** Wave height is scored against the ideal band you
   configure (1.0–2.5 m by default). Below 0.3 m scores zero; well above the
   band decays toward a floor rather than to zero, because oversized surf is
   still surf for somebody. Only swell that can reach the break counts:
   components arriving from more than 100 degrees off the shore direction
   would have to cross land, so they are left out. On a lee-side break this
   matters a lot — at a south-facing Hawaiian spot, trade-wind chop from the
   east-northeast can double the open-water wave height without ever
   touching the beach. The height actually used is on the rating as
   `surf_height_m`.
2. **Does it have power?** Swell period from 5 s (wind slop) to 15 s
   (groundswell).
3. **Is the wind wrecking it?** Calm air is ideal regardless of direction. As
   the wind builds, direction matters more, scored from the angle between the
   wind and a dead-offshore wind. The falloff is steepest through
   cross-offshore and cross-shore, which is where surf-forecast.com's own
   ratings collapse once the wind gets up: across 207 of their forecast hours,
   a cross-offshore break loses half its rating when the wind passes 5 m/s
   while a dead-offshore one loses none. Only a gale is penalised from
   offshore, because it stops waves breaking cleanly.

Size gates the result, so a glassy long-period forecast on a flat ocean still
rates zero:

```
quality = 0.40 * period_score + 0.60 * wind_score
rating  = 10 * height_score * (0.15 + 0.85 * quality)
```

## Which way does the beach face?

Shore direction is the compass bearing the beach looks out to sea: a
west-facing California beach is `270`, a south-facing one is `180`. An offshore
wind then arrives from the opposite bearing. **Get this wrong and the wind
scoring inverts**, so setup offers three ways to get it right.

**1. Pick a compass direction.** Setup asks which way the beach looks out to
sea and offers the sixteen compass points: a south-facing break is *South*, a
west-facing one is *West*. That is how people describe a spot out loud, and it
is precise enough — 22.5 degree steps mean at most 11 degrees of error, which
costs about a third of a rating point. The mistake that actually matters is
being 80 degrees out, which a dropdown makes hard to do.

**2. A coastline estimate** prefills that dropdown. OSM draws
`natural=coastline` with land on the left and water on the right, so a
segment's seaward normal is its bearing plus 90 degrees. Taking the nearest
segment is unreliable near harbours and jetties — exactly where surf spots are
— so this takes a length-weighted circular mean of nearby segments, ignores
anything under 50 m, and reports how well they agreed. If they do not agree, no
estimate is offered rather than a confident wrong one.

**3. Enter an exact bearing** instead, if you already know it.

### The integration checks your answer

Waves cannot cross land to reach a break. So if the forecast shows swell
persistently arriving from behind the beach, the shore direction must be wrong
— and the integration says so, with a repair notice suggesting a better value.
This runs on data already fetched, so it costs no API quota.

You do not need to be precise: the rating moves by roughly 0.3 per 10 degrees,
so within about 15 degrees is plenty. Being 80 degrees out is what flips
offshore to onshore.

## Staying inside 10 requests a day

Each endpoint call costs exactly one request, whatever you ask it for. So:

- **One request buys the whole forecast.** All 20 marine parameters, for every
  hour of the horizon, come back in a single weather call.
- **Tide and astronomy are refetched rarely.** Astronomical tide and sun/moon
  times are deterministic, so a 10-day tide fetch stays valid for 10 days. They
  are renewed only when they age out (12 hours) or their horizon runs short.
- **Restarts are free.** Raw payloads are persisted, so restarting Home
  Assistant or reloading the entry serves from cache instead of the network.
- **Current conditions still track the clock.** Derived values are recomputed
  from the cache every hour at no cost, so "now" is right even though the
  network fetch happens every 6 hours.
- **A reserve is held back** so a manual reload never hits a hard stop.

At the default 6-hour interval that is roughly **6 requests per day**.

If the allowance does run out, the integration keeps publishing the cached
forecast for as long as it still covers the current time, rather than going
blank. Once the cache is genuinely exhausted the entities go unavailable
instead of showing stale nonsense.

Note that **validating your API key during setup spends one request.**

## Installation

### HACS (custom repository)

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/jcconnell/ha-surf-forecast`, category *Integration*
3. Install, then restart Home Assistant

### Manual

Copy `custom_components/surf_forecast` into your `config/custom_components/`
directory and restart Home Assistant.

## Setup

1. Get a free API key at
   [dashboard.stormglass.io](https://dashboard.stormglass.io/register).
2. Settings → Devices & Services → **Add Integration** → *Surf Forecast*.
3. Enter a spot name, the API key, and the break's location. The next screen
   asks which way the beach faces — see above.

Add the integration again for each additional spot. Note that every extra spot
multiplies your request usage, which the free plan will not stretch far.

### Options

| Option | Default | Notes |
| --- | --- | --- |
| Hours between forecast refreshes | 6 | 6 h ≈ 6 requests/day |
| Days of forecast to request | 5 | Costs nothing extra |
| Shore direction | 270° | Bearing the beach faces out to sea |
| Smallest / largest good wave height | 1.0 / 2.5 m | Your ideal band |
| Rating that counts as good surf | 5.0 | Drives the `Good surf` sensor |
| Tide datum | MSL | `LAT`, `MLLW`, `MLW`, `MSL`, `MHW`, `MHHW`, `HAT` |
| Fetch tide data | on | Turn off to save a request |
| Fetch sunrise, sunset and moon data | on | Turn off to save a request |

### Units

Wave heights are reported in metres and shown in feet on the US unit system;
wind is reported in m/s and shown in mph. Every one of those can be overridden
per entity in Home Assistant — open the entity, then its settings, and pick a
unit. Wind speed and gust accept **knots** (`kn`), which is usually what you
want for a marine forecast, along with km/h, ft/s and Beaufort.

## Example automation

```yaml
automation:
  - alias: "Dawn patrol callout"
    triggers:
      - trigger: time
        at: "05:45:00"
    conditions:
      - condition: numeric_state
        entity_id: sensor.home_break_surf_rating
        above: 6
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "Surf is on"
          message: >-
            {{ states('sensor.home_break_surf_conditions') }} —
            {{ states('sensor.home_break_wave_height') }}
            {{ state_attr('sensor.home_break_wave_height','unit_of_measurement') }}
            at {{ states('sensor.home_break_swell_period') }}s,
            {{ states('sensor.home_break_wind_direction_relative_to_shore') | lower }}.
            Best window
            {{ as_timestamp(states('sensor.home_break_best_window_today'))
               | timestamp_custom('%-I:%M %p') }}.
```

The `Surf rating` sensor also carries a `forecast` attribute: the next 24 hours
rated hour by hour. It is deliberately excluded from the recorder database.

## Tests

Two suites. The scoring and parsing logic has no Home Assistant dependency and
runs on its own:

```bash
pip install pytest
python -m pytest tests -q
```

The integration tests drive real Home Assistant with mocked HTTP:

```bash
pip install pytest-homeassistant-custom-component
python -m pytest tests tests_ha -q
```

## Credits

Weather, tide and astronomy data from [Stormglass.io](https://stormglass.io).
Not affiliated with or endorsed by Stormglass.
