<p align="center">
  <img src="custom_components/suzuki_connect/brand/icon@2x.png" alt="Suzuki Connect" width="128">
</p>

<h1 align="center">Suzuki Connect for Home Assistant</h1>

<p align="center">
  <a href="https://github.com/smaclachlan/suzuki-connect-ha/releases"><img src="https://img.shields.io/github/v/release/smaclachlan/suzuki-connect-ha?include_prereleases&style=flat-square" alt="Release"></a>
  <a href="https://hacs.xyz/docs/faq/custom_repositories"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square" alt="HACS custom repository"></a>
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/Home%20Assistant-2024.12%2B-41BDF5?style=flat-square&logo=homeassistant&logoColor=white" alt="Home Assistant 2024.12+"></a>
  <a href="https://github.com/smaclachlan/suzuki-connect-ha/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/smaclachlan/suzuki-connect-ha/validate.yml?branch=main&style=flat-square&label=validate" alt="Validate"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/smaclachlan/suzuki-connect-ha?style=flat-square" alt="License"></a>
</p>

<p align="center">
  Your Suzuki e Vitara's battery, charging, doors, location and trips in Home Assistant,<br>
  via the Suzuki Connect (EU) cloud. Unofficial and read-only.
</p>

Please report problems in [issues](https://github.com/smaclachlan/suzuki-connect-ha/issues);
pull requests are welcome.

> [!IMPORTANT]
> **Suzuki allows only one logged-in device per account.** When Home Assistant
> signs in, the Suzuki app on your phone is logged out, and the other way round.
> Use a **separate Suzuki account for Home Assistant**: invite it as a secondary
> driver from your main account in the Suzuki app. See
> [One session per account](#one-session-per-account).

---

## Contents

- [Highlights](#highlights)
- [Installation](#installation)
- [Configuration](#configuration)
- [Entities](#entities)
- [Trip and charging history](#trip-and-charging-history)
- [Dashboard cards](#dashboard-cards)
- [How it works](#how-it-works)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Credits, license and disclaimer](#credits-license-and-disclaimer)

## Highlights

- **Battery and charging** — battery level, range, charging state, cable,
  time remaining, and energy remaining / to your charge target.
- **Doors and driving** — doors, lock, ignition, odometer, trip meter and
  live location (device tracker).
- **Trips and charging history** — browse every trip and charging session in
  Home Assistant's Calendar, plus ready-made dashboard cards.
- **Fast or gentle polling** — live data as often as every minute; slow-changing
  data on its own longer interval.
- **Careful with your session** — refreshes its token instead of logging in,
  so it doesn't keep logging your phone out.
- **Read-only** — nothing it does changes anything on the car.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=smaclachlan&repository=suzuki-connect-ha&category=integration)

1. Click the button above, or in HACS go to **⋮ → Custom repositories** and add
   `https://github.com/smaclachlan/suzuki-connect-ha` as an **Integration**.
2. Download **Suzuki Connect**, then restart Home Assistant.

To try pre-releases, turn on **Show beta versions** for this repository in
HACS (⋮ → Redownload).

### Manual

Copy `custom_components/suzuki_connect/` into your Home Assistant
`config/custom_components/` folder and restart.

### Requirements

- Home Assistant **2024.12** or newer.
- A Suzuki Connect account with an active subscription, for a car on the
  **EU** service.

## Configuration

[![Open your Home Assistant instance and start setting up Suzuki Connect.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=suzuki_connect)

1. Click the button above, or go to **Settings → Devices & services → Add
   integration → Suzuki Connect**.
2. Sign in with your Suzuki Connect email and password.
3. If the account has more than one car, choose which to add (EVs are
   preselected). Each car becomes its own device.

Each Suzuki account can be added once; its cars share one session and one
poll. To change which cars are included, remove and re-add the integration.

### Options

**Settings → Devices & services → Suzuki Connect → Configure**:

| Option | Default | Range | What it does |
|---|---|---|---|
| Live data poll interval | 15 min | 1–240 min | How often to fetch the live status (battery, charging, doors, location, trip meter). 1–2 min matches how often the car reports while driving. |
| Fetch vehicle health | Off | | Adds a *Vehicle health* sensor, fetched at most hourly. |
| Fetch trips, charging history, schedules and subscription | Off | | Adds the trip, charging-session and schedule entities and the [history calendars](#trip-and-charging-history). |
| Slow data refresh interval | 6 h | 30 min–24 h | How often to refresh the car list and, if enabled, trips, charging history and schedules. |

## Entities

Each car is a device. Entity names start with their area, so related entities
sit together on the device page:

| Area | Entities |
|---|---|
| **Battery** | Battery level, Battery range, Battery energy remaining, Battery energy to target |
| **Charging** | Charging, Charging cable connected, Charging time remaining · *extended:* Charging last session, Charging schedule |
| **Climate** | Climate active · *extended:* Climate schedule |
| **Doors and driving** | Doors, Doors lock, Ignition, Location, Odometer |
| **Trips** | Trip meter · *extended:* Trip last distance, Trip last end, Trip distance this month |
| **Calendars** | *extended:* Trip history, Charging history |
| **Configuration** | Battery capacity, Charge target |
| **Diagnostic** | Last reported by car, Last polled, Telemetry age · *options:* Vehicle health, Subscription |

*Extended* entities appear when **Fetch trips, charging history, schedules and
subscription** is on.

<details>
<summary><b>Disabled by default</b> (enable them in the entity settings)</summary>

Under Diagnostic: Vehicle health (Suzuki hasn't returned anything for it
so far), Average consumption, Speed, the individual climate states
(Climate air conditioning, battery preconditioning, defogger, defroster, seat
heater, steering wheel heater) and body states (Hazard lights, Headlights,
Handbrake, Seatbelt, Bonnet, Boot). Speed, Seatbelt, Bonnet and Boot haven't
been seen in Suzuki's data yet; they're kept in case they appear.

</details>

<details>
<summary><b>Upgrading from 0.1.x</b>: entities were renamed</summary>

In 0.2.0 entities were renamed so they group by area (e.g. *State of charge* is
now *Battery level*). Existing installs keep their entity IDs, so automations
and dashboards keep working; only the displayed names change. New installs get
IDs from the new names, e.g. `sensor.e_vitara_battery_level`.

</details>

### Energy sensors

Suzuki doesn't report battery capacity, and e Vitara variants differ, so set
**Battery capacity** (usable kWh) on the car's device page. Then:

- **Battery energy remaining** = battery level × capacity.
- **Battery energy to target** = (Charge target − battery level) × capacity,
  never below zero. **Charge target** defaults to 80%.

Both stay *unknown* until capacity is set. These two settings live in Home
Assistant only; nothing is sent to the car or to Suzuki.

## Trip and charging history

With extended data on, each car gets two calendars. Open **Calendar** in the
sidebar to browse them:

| Calendar | One event per… | Shows |
|---|---|---|
| **Trip history** | trip | distance, duration and efficiency; battery used and odometer in the details |
| **Charging history** | charging session | start → end level, energy, duration and type |

- Trips for this and last month refresh on the slow interval. Browse to an
  older month and it's fetched once and kept, back to the account's first trip.
- Suzuki only returns the latest few charging sessions, so *Charging history*
  fills in from when the integration started.
- Events appear after the trip or charge, so use them for browsing rather than
  as automation triggers.
- Locations are never included.

## Dashboard cards

The integration comes with two cards; there's nothing extra to install. With
extended data on, edit a dashboard → **Add card** and search for **Suzuki**:

| Card | Shows | Pick this sensor |
|---|---|---|
| **Suzuki recent trips** | Last 10 trips: date, time, distance, duration, efficiency, battery used | *Trip last distance* |
| **Suzuki charging sessions** | Recent charging sessions: date, time, charge added, energy, duration, type | *Charging last session* |

Each card has a visual editor (sensor, title, number of rows). In YAML:

```yaml
type: custom:suzuki-recent-trips-card
entity: sensor.e_vitara_trip_last_distance
title: Recent trips   # optional
max: 10               # optional, 1–10
```

```yaml
type: custom:suzuki-charging-sessions-card
entity: sensor.e_vitara_charging_last_session
```

The data comes from the sensors' `recent_trips` and `recent_sessions`
attributes, which are kept out of the history database. After updating the
integration, reload the browser page if a card doesn't appear.

<details>
<summary>Alternative: a plain Markdown card (no custom card)</summary>

Add a **Markdown** card (**Edit dashboard → Add card → Markdown → Show code
editor**) and paste:

<!-- recent-trips-card -->
```yaml
type: markdown
title: Recent trips
content: |
  {% set trips = state_attr('sensor.e_vitara_trip_last_distance', 'recent_trips') or [] %}
  | Date | Time | Distance | Duration | Efficiency | Battery |
  |:--|:--|--:|--:|--:|--:|
  {%- for t in trips %}
  | {{ as_timestamp(t.start, 0) | timestamp_custom('%a %d %b') }} | {{ as_timestamp(t.start, 0) | timestamp_custom('%H:%M') }} | {{ t.distance if t.distance is not none else '–' }} {{ t.distance_unit or '' }} | {{ (t.duration_minutes or 0) | round | int }} min | {{ t.average_consumption if t.average_consumption is not none else '–' }} {{ t.average_consumption_unit or '' }} | {{ (t.battery_used_pct ~ '%') if t.battery_used_pct is not none else '–' }} |
  {%- endfor %}
```
<!-- /recent-trips-card -->

</details>

Entity IDs above are for new installs; installs that started on 0.2.0b1 or b2
have `sensor.e_vitara_last_trip_distance` and
`sensor.e_vitara_last_charge` instead.

## How it works

### One session per account

Suzuki allows **one logged-in device per account**. Signing in on one device
logs the other out. To keep that to a minimum, the integration:

- **Refreshes its token instead of logging in.** Reads and refreshes don't log
  the phone out; only a full login does. Tokens last 4 minutes and are
  refreshed shortly before they expire.
- **Saves its tokens**, so restarting Home Assistant refreshes rather than
  logging in again.
- **Reclaims a lost session at most once every 5 minutes.** If the phone keeps
  taking the session back, Home Assistant skips polls (data shows as
  unavailable) instead of the two logging each other out in a loop. It never
  asks you to re-enter your password for this.

**Recommended:** a dedicated account for Home Assistant. In the Suzuki app,
invite a second email address as a secondary driver, accept the invitation, and
use that account here. Your own account stays logged in on your phone.

### How fresh is the data?

Two different clocks, which can be hours apart:

| Sensor | Meaning |
|---|---|
| **Last polled** | When Home Assistant last fetched data from Suzuki. Follows the poll interval. This is what the Suzuki app calls "Last updated". |
| **Last reported by car** | The car's last trip/park report (a few minutes after the ignition goes off). It doesn't move while the car is parked and charging, even though the charging values keep updating. |

**Telemetry age** is the difference between the two. It flags stale data when
the car is parked and *not* charging; while charging it grows even though the
values are current, so don't use it to gate automations.

Suzuki sometimes leaves fields out of a response (or sends them blank). So
that sensors don't flicker to *unknown*, a missing value keeps its last
reading for up to 3 polls in a row (3 minutes at a 1-minute interval); after
that it shows unknown.
(*Charging cable connected* is the exception: Suzuki omits it when unplugged.)

Polling reads Suzuki's cloud and doesn't appear to wake the car. While the car
is awake (driving or charging) it reports about once a minute, so a short poll
interval catches short trips and charging progress; while it's asleep, polling
more often changes nothing.

### API usage

| What | When | Calls |
|---|---|---|
| Live status | every live poll | 1 per car |
| Token refresh | about every 3.5 minutes | 1 per account |
| Car list | at startup, then each slow interval | 1 per account |
| Trips, charging history, schedules (extended) | each slow interval | 2 per account + 3 per car |
| Vehicle health (option) | hourly | 1 per car |
| Older months of trips | when you browse to them in the calendar | 1 per month, once |

One car at a 1-minute poll is about 1.3 calls a minute. Suzuki doesn't publish
rate limits; if polls start failing at a short interval, raise it.

<details>
<summary><b>Multiple cars</b> (untested)</summary>

Each selected car gets its own device. They share the account's session, and
each live poll fetches every car's status in turn. If one car fails (for
example, out of mobile coverage), it keeps its last values and the others still
update; the poll only fails if every car fails.

This has only been tested on an account with one car. In particular, trips
come from an account-wide endpoint and are matched to cars by contract ID.
Suzuki may only return trips for the car the account last "selected", in which
case one car's trips could be missing (they're never credited to the wrong
car). Charging history, schedules and health are fetched per car.

If you have several cars, please turn on extended data, check each car's
*Trip last distance* and *Trip distance this month*, and
[open an issue](https://github.com/smaclachlan/suzuki-connect-ha/issues) with
what you see and a diagnostics download.

</details>

## Examples

<details>
<summary><b>Send the battery level to Ohme</b> for smart charging</summary>

The [Ohme integration](https://www.home-assistant.io/integrations/ohme/) can
take the car's state of charge from Home Assistant, so Ohme plans charging
from the real battery level. Its state-of-charge entity is **disabled by
default**; enable it on the Ohme device first. This automation copies the
battery level to Ohme whenever it changes, and when the car is plugged in:

```yaml
alias: "e Vitara: send battery level to Ohme"
triggers:
  - trigger: state
    entity_id: sensor.e_vitara_battery_level
  # Also when the car is plugged in, so Ohme has a value for planning.
  - trigger: state
    entity_id: binary_sensor.e_vitara_charging_cable_connected
    to: "on"
conditions:
  # Unavailable (a failed poll) or unknown isn't a number, so it's skipped.
  - condition: template
    value_template: >
      {{ states('sensor.e_vitara_battery_level') | is_number }}
actions:
  - action: number.set_value
    target:
      entity_id: number.ohme_home_pro_state_of_charge
    data:
      value: "{{ states('sensor.e_vitara_battery_level') | int }}"
mode: queued
```

Replace the entity IDs with your own. The Ohme one depends on your charger
model; installs from before 0.2.0 have `sensor.e_vitara_state_of_charge` for
the battery level. Ohme's value lags the car by up to one poll interval.

</details>

## Troubleshooting

**Download diagnostics**: **Settings → Devices & services → Suzuki Connect → ⋮
→ Download diagnostics**. It includes poll timing, the last error, token
status, how old each car's data is and the raw responses from Suzuki. Email,
password, tokens, VIN, contract IDs, phone numbers and locations are redacted;
still, check the file before attaching it to an issue.

| Symptom | What to check |
|---|---|
| Your phone keeps getting logged out | Use a separate account for Home Assistant (see [One session per account](#one-session-per-account)). In diagnostics, `auth.forced_logins` should stay at 0–1. |
| Data shows as unavailable for a few minutes | The phone app took the session; Home Assistant reclaims it within 5 minutes. |
| *Last reported by car* is hours old | Normal while parked or charging; see [How fresh is the data?](#how-fresh-is-the-data) |
| Energy sensors are *unknown* | Set **Battery capacity** on the device page. |
| Trip or charging entities are missing | Turn on **Fetch trips, charging history, schedules and subscription** in the options. |
| The Suzuki cards aren't in the card picker | Reload the browser page (or clear its cache) after installing or updating. |

## Known limitations

- **EU accounts only** (`*.eur.connect.suzuki`); other regions use different
  services.
- **Read-only**: remote lock, climate and charging controls aren't
  implemented.
- **One session per account**; see above.
- **Multiple cars are untested**; see above.
- **Battery capacity** isn't reported by Suzuki and has to be entered by hand.
- **Timestamps**: *Last reported by car* is UTC. Trip and charging times come
  without a timezone and are read in Home Assistant's timezone, so set that to
  the car's.
- **Not yet confirmed on a live car**: the trip meter (`drv_km`, assumed to be
  the resettable trip distance in km), charge and climate schedules while
  active, and the meaning of vehicle-health codes.
- **Charging cable connected**: Suzuki's live data rarely includes it. It shows
  on while charging; otherwise, with extended data on, it comes from the
  charging data (refreshed on the slow interval and as soon as charging or
  the ignition changes). Without extended data it's unknown when not
  charging.
- **Not exposed yet**: geofences, alert settings and alert history. The
  endpoints are listed in [docs/API.md](docs/API.md).

## Credits, license and disclaimer

Built by reverse-engineering the official Suzuki Connect EU app, for
interoperability with the owner's own vehicle data. API notes are in
[docs/API.md](docs/API.md). Licensed under the Apache License 2.0; see
[LICENSE](LICENSE).

> **Unofficial and not affiliated.** This project is not affiliated with,
> endorsed by or supported by Suzuki. "Suzuki" and "Suzuki Connect" are
> trademarks of their respective owners, used here only to describe
> compatibility. It talks to the same servers as the official app, with your
> own account; it may stop working if Suzuki changes their service, and using
> it may be contrary to the app's terms of service. Use at your own risk.
