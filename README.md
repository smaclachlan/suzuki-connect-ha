# Suzuki Connect for Home Assistant

Unofficial Home Assistant integration for **Suzuki Connect** (EU) electric
vehicles — starting with the Suzuki e Vitara. It brings your car's state of
charge, range, charging and lock status, odometer and location into Home
Assistant so you can use them in dashboards and automations (for example,
smart-charging alongside Ohme or Octopus).

> **Unofficial / not affiliated.** This project is not affiliated with,
> endorsed by, or supported by Suzuki. "Suzuki" and "Suzuki Connect" are
> trademarks of their respective owners and are used here only to describe
> compatibility. It talks to the same servers the official app uses, with your
> own account; it may stop working if Suzuki changes their service, and using
> it may be contrary to the app's terms of service. Use at your own risk.

## Features

| Type | Entities |
|---|---|
| Sensors | State of charge (%), Range, Remaining charge time, Odometer, Last updated |
| Binary sensors | Charging, Charger connected, Door lock |
| Device tracker | Vehicle location |

Read-only today. Remote controls (lock/unlock, climate/preconditioning, charge
start/stop) are planned.

## Requirements

- Home Assistant 2024.8 or newer.
- A Suzuki Connect account with an active subscription on an EU vehicle.

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories** → add this repository's URL, category
   **Integration**.
2. Install **Suzuki Connect**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Suzuki Connect**, and
   sign in with your Suzuki Connect email and password.

(Manual install: copy `custom_components/suzuki_connect/` into your HA
`config/custom_components/` and restart.)

## Important: one active session per account

Suzuki allows only **one logged-in device per account** at a time. When Home
Assistant signs in it takes over the session, which logs your phone's Suzuki
app out (and opening the app again will log Home Assistant out until its next
poll). Reads do not evict — only a fresh login does — and the integration
refreshes its token rather than re-logging-in wherever possible, so in normal
use they rarely fight. If the app keeps taking the session back, Home Assistant
reclaims it at most once every 5 minutes and otherwise just skips that poll, so
the two never fight in a tight loop. If you want to use the phone app freely, consider a
dedicated Suzuki account (an invited/secondary driver) just for Home Assistant.

## Options

- **Poll interval** (default 15 minutes) — Settings → the integration →
  Configure. Reads return cached telematics and do not appear to wake the car.

## Credits & license

Built by reverse-engineering the official Suzuki Connect EU app for
interoperability with the owner's own vehicle data. Licensed under the Apache
License 2.0 — see [LICENSE](LICENSE). API notes in [docs/API.md](docs/API.md).
