# Suzuki Connect for Home Assistant

Unofficial Home Assistant integration for **Suzuki Connect** (EU) electric
vehicles — starting with the Suzuki e Vitara. It brings your car's state of
charge, range, charging and lock status, odometer and location into Home
Assistant so you can use them in dashboards and automations (for example,
smart-charging alongside Ohme or Octopus).

> [!IMPORTANT]
> **Suzuki allows only one logged-in device per account.** When Home Assistant
> signs in, your phone's Suzuki app is logged out, and vice versa. The
> recommended setup is a **separate Suzuki account just for Home Assistant**:
> invite it as a secondary driver from the main account in the Suzuki app, then
> use that account here. See [One active session per account](#one-active-session-per-account).

> **Unofficial / not affiliated.** This project is not affiliated with,
> endorsed by, or supported by Suzuki. "Suzuki" and "Suzuki Connect" are
> trademarks of their respective owners and are used here only to describe
> compatibility. It talks to the same servers the official app uses, with your
> own account; it may stop working if Suzuki changes their service, and using
> it may be contrary to the app's terms of service. Use at your own risk.

## Features

Each vehicle gets its own device with:

| Type | Entities |
|---|---|
| Sensors | State of charge, Range, Remaining charge time, Odometer, Trip meter, Last reported by car, Energy remaining, Energy to charge target |
| Binary sensors | Charging, Charger connected, Door lock, Doors, Ignition, Climate active |
| Device tracker | Location |
| Numbers (settings) | Battery capacity, Charge target |
| Diagnostics | Last polled, Telemetry age, Vehicle health (opt-in) |
| Extended data (opt-in) | Last trip distance, Last trip end, Distance this month, Last charge, Subscription; Charge schedule and Climate schedule binary sensors |

Disabled by default (enable in the entity settings): Average consumption,
Speed, individual climate states (A/C, preconditioning, defogger, defroster,
seat and steering heaters) and body states (hazards, headlights, handbrake,
seatbelt, bonnet, boot).

**Read-only.** Nothing this integration does changes anything on the car.

### Energy sensors

Suzuki doesn't report battery capacity, and e Vitara variants differ, so set
**Battery capacity** (usable kWh) on the vehicle's device page. Then:

- **Energy remaining** = state of charge × capacity.
- **Energy to charge target** = (Charge target − state of charge) × capacity,
  never below zero. **Charge target** defaults to 80 %.

Both stay *unknown* until capacity is set. These values are stored in Home
Assistant only; they are not sent to the car or to Suzuki.

## Requirements

- Home Assistant 2024.12 or newer.
- A Suzuki Connect account with an active subscription on an EU vehicle.

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories** → add this repository's URL, category
   **Integration**.
2. Install **Suzuki Connect**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Suzuki Connect**, and
   sign in with your Suzuki Connect email and password. If the account has
   more than one vehicle you choose which to add (EVs are preselected); each
   becomes its own device.

(Manual install: copy `custom_components/suzuki_connect/` into your HA
`config/custom_components/` and restart.)

Each Suzuki account can be added once. All of its vehicles share one session
and one poll. To change which vehicles are included, remove and re-add the
integration.

> [!NOTE]
> **Multiple cars are untested.** The integration has only been tested on an
> account with one car. Accounts with several cars should work, but see
> [Multiple cars](#multiple-cars) for what to expect, and please open an issue
> if something looks wrong.

## One active session per account

Suzuki allows only **one logged-in device per account** at a time. Signing in
on one device logs the other out:

- When Home Assistant signs in, the phone app is logged out.
- When you open the app and sign in, Home Assistant loses its session until it
  next reclaims it.

To keep this to a minimum, the integration:

- **Refreshes its token rather than logging in** wherever possible. Reads and
  refreshes don't log the phone out; only a fresh login does.
- **Saves its refresh token**, so restarting Home Assistant doesn't force a new
  login.
- **Reclaims a lost session at most once every 5 minutes.** If the app keeps
  taking the session back, Home Assistant skips polls instead of the two
  logging each other out in a loop. Skipped polls show as unavailable data,
  not as a request to re-enter your password. With a short poll interval this
  means up to 5 minutes of unavailable data each time the app takes over.

**Recommended:** use a dedicated account for Home Assistant. In the Suzuki
app, invite a second email address as a secondary driver, accept the
invitation, and use that account here. Your own account then stays logged in on
your phone.

## Polling vs. how fresh the car's data is

There are two separate clocks, and they can be hours apart:

- **Last polled** — when Home Assistant last fetched data from Suzuki's cloud
  successfully. This follows the poll interval (default 15 minutes).
- **Last reported by car** — when the car itself last sent data to the cloud.
  The car reports while it's awake; when it's parked and asleep, or out of
  mobile coverage, the cloud keeps serving the last values it has.

**Telemetry age** (a diagnostic sensor) is the difference at the last poll.
A recent *Last polled* with a large *Telemetry age* means polling is working
but the car hasn't reported. The values are not current, even though they
updated.

Polling reads the cloud's cached data and does not appear to wake the car.
While the car is awake (driving or charging) it reports about once a minute, so
a short poll interval catches short trips and charging progress that a
15-minute poll misses. While it's asleep, polling more often changes nothing.

For automations that act on state of charge, check freshness first, for
example `{{ states('sensor.e_vitara_telemetry_age') | float(9999) < 60 }}`.

## Options

Settings → the integration → **Configure**:

- **Live data poll interval** — default 15 minutes, 1–240. This covers the
  live status (charge, range, locks, location, trip meter). 1–2 minutes
  roughly matches how often the car reports while driving and how often the
  Suzuki app refreshes. Each poll is one API call per car. Like the app, the
  access token is used until Suzuki rejects it, then refreshed (one extra
  call). Suzuki doesn't publish rate limits, so if polls start failing at a
  short interval, raise it.
- **Fetch vehicle health** — off by default. Adds a Vehicle health sensor,
  fetched at most hourly (one extra API call per vehicle).
- **Fetch trips, charging history, schedules and subscription** — off by
  default. Adds the extended-data entities, refreshed on the slow interval
  below (two calls for the account plus four per vehicle).
- **Slow data refresh interval** — default 6 hours, 30 minutes to 24 hours.
  Covers the account's vehicle list (checked at startup, then on this
  interval) and, when enabled, trips, charging history, schedules and
  subscription. Each part is fetched
  separately; one that fails keeps its last value and never fails the poll.
  - *Last trip distance* has the trip's start, end, duration and average
    consumption as attributes; *Distance this month* has the trip count and
    the app's monthly driving score and harsh acceleration/braking counts
    (account-wide).
  - *Last charge* is when the latest charging session happened, with start
    and end charge level, duration, energy and AC/DC type as attributes.
  - *Charge schedule* / *Climate schedule* are on when any schedule is
    active, with every schedule's settings as attributes.
  - Trip and charging locations and driver names are never exposed.
  - These response formats come from the app's code and haven't been checked
    against a live car yet. If something looks wrong, **Download diagnostics**
    includes the raw (redacted) responses.

## Example: send the state of charge to Ohme

The [Ohme integration](https://www.home-assistant.io/integrations/ohme/) can
take the car's state of charge from Home Assistant, so Ohme's smart charging
plans from the real battery level. Its state-of-charge entity is **disabled by
default**: enable it on the Ohme device first.

This automation copies the e Vitara's state of charge to Ohme whenever it
changes. It skips stale readings, so Ohme isn't sent an old value from a car
that hasn't reported recently.

```yaml
alias: "e Vitara: send state of charge to Ohme"
triggers:
  - trigger: state
    entity_id: sensor.e_vitara_state_of_charge
  # Also when the car is plugged in, so Ohme has a value for planning.
  - trigger: state
    entity_id: binary_sensor.e_vitara_charger_connected
    to: "on"
conditions:
  - condition: template
    value_template: >
      {{ states('sensor.e_vitara_state_of_charge') | is_number }}
  # Only send reasonably fresh data from the car.
  - condition: template
    value_template: >
      {{ states('sensor.e_vitara_telemetry_age') | float(9999) < 30 }}
actions:
  - action: number.set_value
    target:
      entity_id: number.ohme_home_pro_state_of_charge
    data:
      value: "{{ states('sensor.e_vitara_state_of_charge') | int }}"
mode: queued
```

The entity IDs are examples; replace them with your own (the Ohme one depends
on your charger model). With the default 15-minute poll,
Ohme's value can lag the car by up to one poll interval plus the car's own
reporting delay. This example hasn't yet been tested through a full charge.

## Multiple cars

Each selected car gets its own device. All of them share the account's single
session, and each live poll fetches every car's status one after another:

- **API calls:** one per car per live poll (two cars at a 1-minute interval is
  about two calls a minute). The vehicle list and token refresh are shared by
  the account; extended data adds four calls per car on the slow interval.
- **One car failing** (for example, out of mobile coverage) keeps that car's
  last values and doesn't fail the poll; the poll only fails if every car
  fails.

**Not yet tested with more than one car.** In particular:

- **Trips** come from an account-wide endpoint and are matched to each car by
  the contract id on each trip. Suzuki may only return trips for the car the
  account last "selected" (a `selectedContractId` appears in its responses),
  in which case one car's trips could be missing. Trips are never credited to
  the wrong car.
- Charging history, schedules, subscription and health are fetched per car
  and should be unaffected.

If you have several cars, it would help to turn on extended data, check that
each car's *Last trip distance* and *Distance this month* look right, and
[open an issue](https://github.com/smaclachlan/suzuki-connect-ha/issues) with
what you see. **Download diagnostics** (credentials, VINs, contract ids and
locations redacted) shows what Suzuki returned for each car.

## Known limitations

- **EU accounts only** (`*.eur.connect.suzuki`). Other regions use different
  backends.
- **One session per account.** See above.
- **Multiple cars are untested.** See [Multiple cars](#multiple-cars).
- **Read-only.** Remote lock, climate and charging controls are not
  implemented.
- **Data is only as fresh as the car's last report.** See
  [Polling vs. how fresh the car's data is](#polling-vs-how-fresh-the-cars-data-is).
- **Timestamps** from the car have no timezone. They are assumed to be in Home
  Assistant's configured timezone.
- **Unconfirmed values.** These have been seen only in some states, or not at
  all on a live car, and may be wrong:
  - `charge_st` while charging (the integration treats any non-zero value as
    charging).
  - `chargerConnected_st`: only sent while plugged in. When it's missing,
    *Charger connected* shows unknown rather than off.
  - The meaning of the vehicle-health status codes.
- **Trip meter** is the car's `drv_km` value, assumed to be its resettable trip
  distance in km. Not yet confirmed.
- **Extended data** (trips, charging history, schedules, subscription) is
  parsed from formats found in the app's code, not yet checked against a live
  response. Energy in *Last charge* has no unit in the API and is assumed kWh.
  In accounts with several cars, trips are matched to cars by contract id;
  see [Multiple cars](#multiple-cars).
- **Not exposed yet:** geofences, alert settings and alert history. Endpoints
  for these are listed in [docs/API.md](docs/API.md).
- **Battery capacity** isn't reported by the API and must be entered by hand
  for the energy sensors.

## Troubleshooting

Settings → the integration → ⋮ → **Download diagnostics** gives a report with
poll timing and latency, the last error, token status and how old each car's
data is. Email, password, tokens, VIN, contract IDs and location are redacted;
check the file yourself before attaching it to an issue.

## Credits & license

Built by reverse-engineering the official Suzuki Connect EU app for
interoperability with the owner's own vehicle data. Licensed under the Apache
License 2.0 — see [LICENSE](LICENSE). API notes in [docs/API.md](docs/API.md).
