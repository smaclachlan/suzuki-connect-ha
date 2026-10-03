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
| Sensors | State of charge, Range, Remaining charge time, Odometer, Last reported by car, Energy remaining, Energy to charge target |
| Binary sensors | Charging, Charger connected, Door lock, Doors, Ignition, Climate active |
| Device tracker | Location |
| Numbers (settings) | Battery capacity, Charge target |
| Diagnostics | Last polled, Telemetry age, Vehicle health (opt-in) |

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

- Home Assistant 2024.8 or newer.
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
  not as a request to re-enter your password.

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

Polling reads the cloud's cached data and does not appear to wake the car, so
polling more often won't make the data fresher.

For automations that act on state of charge, check freshness first, for
example `{{ states('sensor.e_vitara_telemetry_age') | float(9999) < 60 }}`.

## Options

Settings → the integration → **Configure**:

- **Poll interval** — default 15 minutes, allowed 5–240.
- **Fetch vehicle health** — off by default. Adds a Vehicle health sensor,
  fetched at most hourly (one extra API call per vehicle).

## Example: stop an Ohme charge at your target

Suzuki's state of charge isn't available to Ohme's car integrations, so Ohme
can't stop at a percentage by itself. This automation pauses the Ohme charger
once the e Vitara reaches its **Charge target**, as long as the car's data is
recent. It uses the [Ohme integration](https://www.home-assistant.io/integrations/ohme/).

```yaml
alias: "e Vitara: pause Ohme at charge target"
triggers:
  - trigger: state
    entity_id: sensor.e_vitara_state_of_charge
conditions:
  # Only act on reasonably fresh data from the car.
  - condition: template
    value_template: >
      {{ states('sensor.e_vitara_telemetry_age') | float(9999) < 30 }}
  - condition: template
    value_template: >
      {{ states('sensor.e_vitara_state_of_charge') | float(0)
         >= states('number.e_vitara_charge_target') | float(101) }}
  - condition: state
    entity_id: binary_sensor.e_vitara_charging
    state: "on"
actions:
  - action: select.select_option
    target:
      entity_id: select.ohme_home_pro_charge_mode
    data:
      option: paused
mode: single
```

Adjust the entity IDs to match your setup: the Ohme ones depend on your
charger model, so check the charge-mode select's entity ID and options under
the Ohme device. With the default 15-minute poll, the charge can overshoot the
target by up to one poll interval of charging.

## Known limitations

- **EU accounts only** (`*.eur.connect.suzuki`). Other regions use different
  backends.
- **One session per account.** See above.
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
- **Not exposed yet:** charge schedules and history, climate schedules, trips,
  geofences, alert settings and subscription status. Endpoints for these are
  listed in [docs/API.md](docs/API.md).
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
