# Suzuki Connect (EU) — API map

Reverse-engineered from the official Android app, **SuzukiConnect** EU
(`suzuki.app.a025.SzkCnnctEur`), version `1.0.53` (versionCode 55), published by
Magyar Suzuki Zrt. Source: static analysis of the APK (jadx), then confirmed
against a live UK e Vitara for the read path (login, vehicle list, dashboard).

Endpoint paths and JSON field names come from decompiled Retrofit interfaces and
Gson models. Sections marked *(confirmed)* were checked against live responses;
everything else, including all remote commands, is unverified.

## Transport

- **Client:** Retrofit2 + OkHttp + Gson.
- **Base URL (PROD):** `https://en01cs.sc.eur.connect.suzuki` — resolved from
  plaintext strings in `libnative-lib.so` (the `Keys.apiKey("PROD")` getter).
  Pre-prod/QA uses `*.sc.pre-eur.connect.suzuki`, dev uses `*.suzukiconnect.info`.
- **No certificate pinning.** The OkHttp client is built with no
  `CertificatePinner` and there is no `network_security_config` pinning. A proxy
  (mitmproxy) will work without Frida if we ever need to confirm a shape.
- **Timeouts:** 20s default; some calls opt into 120s via a `CONNECT_TIMEOUT`
  header, 30s via `WRITE_TIMEOUT` (stripped before sending by the interceptor).

## Authentication

### Login — `POST /api/sconnectapp/login/token`
Form-urlencoded (`@FieldMap`). Fields the app sends:

| field | value |
|---|---|
| `mailID` | account email |
| `password` | account password |
| `grant_type` | `password` |
| `override` | `0` = normal (returns `400008` if another device is logged in); **`1` = force login, evicting the other device** |
| `client_id` | app-level identifier, same for every install (see `pysuzukiconnect/const.py`) |
| `client_secret` | app-level identifier, same for every install (see `pysuzukiconnect/const.py`) |
| `biometric_uuid` | empty unless biometric login is set up |
| `device_id` | random UUID, generated once and persisted |
| `device_type` | `Android` |
| `device_model` | `Build.MODEL` |
| `os_version` | `Build.VERSION.RELEASE` |
| `device_token` | FCM push token (may be empty) |
| `appVer` | `1.0.53` |
| `preferred_language` | e.g. `EN` |

Returns `SignInResponse` containing a JWT (stored as `key_jwt_token`) and a
`refresh_token`.

### Token refresh — `POST /api/sconnectapp/login/token`
Same endpoint, `grant_type=refresh_token`, plus `access_token`, `refresh_token`,
`mailID`, `client_id`, `client_secret`, and the same device fields.

### Token lifetime
The login and refresh responses include `expiresIn` (observed `240`), but the
app never reads it. Its OkHttp `Authenticator` (`jc/C2989j`) refreshes with
`grant_type=refresh_token` only when a call returns **401**, then retries. The
integration does the same; it also refreshes a little early if the access
token is a JWT with an `exp` claim. Diagnostics record the age of each token
when it was rejected, so the real lifetime can be observed.

### Authorization
A request interceptor adds `Authorization: Bearer <JWT>` to **every** call except
the login/token endpoint. Reads need nothing more.

### Session model (confirmed)
**Single active session per account.** A normal login (`override=0`) while
another device is active returns `400008 "Another Active Login"` (over HTTP
status `505`), with the other device's name in `title`. Logging in with
**`override=1`** forces it through and logs the other device out — this is what
the app's "CONTINUE" dialog does (verified: `override:1` → HTTP 200). The device
name shown to the user is the `device_model` field we send, so the integration
should send `"Home Assistant"`. Backend also caps registered devices at 5.

Implication for the integration: it can always reclaim its session with
`override=1`, but it and the owner's phone cannot both hold the session at once —
each reclaim evicts the other. Design: log in once, prefer `refresh_token`, and
only full-login (`override=1`) when a refresh is rejected. Whether a
`refresh_token` survives the phone evicting the session is still to be measured
(`scripts/session_test.py`).

### Remote-command second factor (`r_key`)
State-changing *remote* commands (lock, climate, etc.) additionally require an
`r_key` header. It is obtained by verifying the user's **remote PIN** and is
cached. Not needed for any read.

## Reading State of Charge — the minimal path

Three calls, all read-only, Bearer auth only:

1. **`GET /api/profile/vehicleDetailsAuth`** → `PrimaryVehicleResponse`
   Gives `CONTRACT_ID` (and `VIN_GEN`, `FUEL_TYPE`, `ODOMETER_READING`,
   `DCM_STATUS`, GPS, …). The contract ID keys every other call.

2. **`POST /api/dashboard/dashboardOauth`** → `DashBoardResponse`
   Body (JSON): `{ "contract_id": "<id>", "deviceId": "<uuid>", "origin": "DEVICE" }`
   plus `Authorization` header. **This is the one that carries the EV state.**

Relevant `DashBoardResponse` fields:

| JSON field | meaning |
|---|---|
| `currentChargeLevel` | **State of charge (%)** |
| `driving_range` / `driving_range_unit` | estimated range |
| `charge_st` | charging status |
| `chargerConnected_st` | charge cable connected |
| `batt_st` | high-voltage battery status |
| `batteryPreconditioning_st` | battery preconditioning on/off |
| `lowBatteryThreshold`, `rangeThreshold` | user alert thresholds |
| `averageConsumption` / `averageConsumptionUnit` | efficiency |
| `GPS` / `latestGPS` / `latitude` / `longitude` | location |
| `ignition_status`, `vehicleSpeed`, `mileage`, `drv_km` | drive state / odometer |
| `doorlock_st`, `opendoor_st`, door/window `_st` fields, `trunkStatus`, `hoodStatus` | body status |
| `acOn_st`, `defoggerOn_st`, `defrosterOn_st`, seat/steering heater `_st` | climate state |
| `FUEL_TYPE`, `vehicleType`, `VRN`, `VIN_GEN` | vehicle identity |

(Fields ending `Pending` are in-flight remote-command flags, not state.)

## Confirmed response shapes (verified live 2026-10-02, UK e Vitara)

Common envelope: `{ "errors": [...], "result": { "message", "title", "data": {...} } }`.
Login additionally has top-level `access_token`, `refresh_token`, `expiresIn`.

**Login** (`/api/sconnectapp/login/token`):
- `access_token` — the JWT (top level)
- `refresh_token` — (top level)
- `expiresIn` — observed `240`. Units unknown, and unused: the app refreshes on 401 instead (see *Token lifetime*).
- `result.data.USER_DETAILS.DEFAULT_CONTRACT_ID` — the account's default vehicle
- `result.data.VEHICLE_DATA.{PRIMARY_VEHICLE_LIST,SECONDARY_VEHICLE_LIST}` — each
  vehicle has `CONTRACT_ID`, `FUEL_TYPE` (`EV`), `BrandCode` (`e VITARA`),
  `VIN_GEN`, `DCM_STATUS`, `ODOMETER_READING`(+`_UNIT`).
  **The test car was in `SECONDARY_VEHICLE_LIST` (PRIMARY empty)** because the
  account is an invited/secondary driver — the client must scan **both** lists.

**Dashboard** (`/api/dashboard/dashboardOauth`), body
`{contract_id, deviceId, origin:"DEVICE"}`:
The EV live state is at **`result.data.DASHBOARD_DATA.user_data`**:

| path (under `...DASHBOARD_DATA.user_data`) | observed | meaning |
|---|---|---|
| `currentChargeLevel` | `47` | **SOC %** |
| `driving_range` / `driving_range_unit` | `88` / `mile` | range |
| `charge_st` | `0` | charging (0 = not charging) |
| `remainingChargingTime` | `-1` | **milliseconds** remaining (-1 = n/a); confirmed live: `16800000` = app's "4h 40m" |
| `batteryPreconditioning_st` / `_consent` | `0` / `0` | battery precondition |
| `averageConsumption` / `averageConsumptionUnit` | `0.2` / `miles/kWh` | efficiency |
| `acOn_st`,`defoggerOn_st`,`defrosterOn_st`,`seatHeaterOn_st`,`steeringHeaterOn_st` | `0` | climate states |
| `ignition_status`,`doorlock_st`,`opendoor_st`,`hzrd_st`,`prbrk_st`,`headlight_st` | | body/ignition |
| `doorLockedRemotely`,`doorLockResponseTime`,`isDoorUnlockAllowed` | `Y` / ts / `N` | lock detail |
| `latestGPS[0].latitude/longitude`, `GPS[0]...` | | location |
| `mileage`, `drv_km` | `22632`, `153` | odometer (km) / trip meter (km, assumed resettable) |
| `fuel` | `46.7` | mirrors SOC for EV (≈ `currentChargeLevel`) |
| `*Pending` (`doorLockPending`, `acONPending`, …) | `N` | in-flight remote-command flags |

Freshness/other (under `result.data.DASHBOARD_DATA`): `lut` (last vehicle
report time, e.g. `2026-10-02 18:54:37`), `selectedContractId`, `FUEL_TYPE`,
`VIN_GEN`, `DEVICE_TYPE` (`G3`), `REMOTE_PIN` (1 = a remote PIN is set).
Reads returned cached telematics with `realTimeOpsPending: N` — polling does
**not** appear to wake the car.

## Other read endpoints

| Method + path | Response | Purpose |
|---|---|---|
| `GET /api/v2/climate_control/getACDetails/{contract_id}` | `RemoteAcDetailResponse` | A/C / preconditioning detail |
| `GET /api/dashboard/vehicleHealthStatus/{contractID}` | `HealthCheckResponse` | vehicle health |
| `GET /api/subscription/getStatus/{contractId}` | `SubscriptionResponse` | connected-services subscription |
| `POST /api/v2/remoteCharge/getAllSchedules` | `EvChargingScheduleResponse` | EV charge schedules |
| `POST /api/v2/remoteCharge/charging_history` | `EvChargingHistoryResponse` | charge history |
| `GET /api/v2/climate_control_schedule/getAll/{contract_id}` | `GetAllScheduleListData` | climate schedules |
| `GET /api/trip/drivingHistory/{month}` | `DrivingHistoryResponse` | trips |
| `GET /api/profile/getAlertSettings/{contract_id}` | `AlertSettingsResponse` | alert config |

### Extended data used by the integration (from the decompiled app; unverified live)

Request formats:

| Call | Request |
|---|---|
| Driving history | `GET /api/trip/drivingHistory/{month}`, `month` = `yyyy-MM`. Account-wide: no contract id; each trip carries `contractID`. |
| Charging history | `POST /api/v2/remoteCharge/charging_history`, JSON `{"contractId": <id>, "default": "0"}` (`"0"` is what the history screen sends) |
| Charge schedules | `POST /api/v2/remoteCharge/getAllSchedules`, JSON `{"contractID": <id>}` |
| Climate schedules | `GET /api/v2/climate_control_schedule/getAll/{contract_id}` |
| Subscription | `GET /api/subscription/getStatus/{contractId}` |

Response fields (all under `result.data`):

- **Driving history:** `tripDetails[].{tripDate, tripList[]}`; each trip has
  `contractID`, `startDate`/`startTime`, `endDate`/`endTime`, `tripDistance`
  (+`tripDistanceUnit`), `trip_duration`, `avgConsumption`
  (+`avgConsumptionUnit`), `startPosition`/`endPosition` (lat/lon lists),
  `trip_driver_name`, `USR_TIMEZONE`. `driverReport.{drb_score,
  harsh_acc_count, harsh_break_count}` is a monthly summary. Value formats
  (time and duration strings) are unknown.
- **Charging history:** `chargingHistoryList[].{chargeTime,
  batteryChargedDuration, batteryLevelAtStartCharge, batteryLevelAtStopCharge,
  energyConsumption, chargeType, chargingStLatitude, chargingStLongitude,
  chargingStLocation}`, plus summary fields (`averageChargeAmount`,
  `electricityConsumption`, `kmDriven`, …). No unit is given for
  `energyConsumption`.
- **Charge schedules:** `schedule[].{scheduleId, isActive, isOngoing,
  StartTime, endTime, notifyDays, activeDay, scheduleDate, status}`.
- **Climate schedules:** `schedules[].{reservation_id, active, schedule_time,
  schedule_date, selected_days, isRepeatSelected, duration, temperature,
  options_selected}`.
- **Subscription:** `subscriptionDetails.{planName, planId,
  subscriptionStatus, focStatus, foc_error_msg}`.

## Remote commands (write — need `r_key` and/or remote PIN)

| Method + path | Purpose |
|---|---|
| `GET /remote/api/remoteapi/{operation}` (+`r_key`) | generic remote op (lock/unlock, lights, finder…) |
| `POST /remote/api/v2/remoteapi/charge_control/{operation}` (+Auth) | start/stop charging |
| `POST /remote/api/v2/remoteapi/climate_control/ac_ops` (+`r_key`) | A/C & preconditioning |
| `POST /remote/api/v2/remoteapi/remote_charge/createSchdule` / `updateSchedule` | EV charge schedule |
| `POST /api/v2/climate_control_schedule/create` / `edit` / `delete` | climate schedule |
| `GET /api/logout` | end session |

## `client_id` / `client_secret`

These identify the *app*, not a user: every install of the EU app sends the same
pair, as with the Toyota/Kia/Hyundai integrations. They are not secrets in the
OAuth sense and grant nothing without a user's own email and password. They live
in `pysuzukiconnect/const.py` only and are deliberately not repeated in
documentation.

In the APK they're not plaintext: `libnative-lib.so` contains a
`SuzukiCipherKey` and the getters decrypt a stored blob at runtime. They were
obtained from one login capture (the app has no certificate pinning).

## Open questions (resolve during test-client / capture stage)

1. ~~`client_id`, `client_secret`~~ — DONE (confirmed by live login).
0. ~~Session force mechanism~~ — DONE: `override=1` forces login (confirmed 200).
   **Still open:** does a `refresh_token` keep working after the phone evicts the
   session? Decides whether refresh-only polling can coexist with the phone.
0. ~~`expiresIn` units~~ — moot: the app ignores it and refreshes on 401. Real token lifetime to be observed via diagnostics.
2. Exact nesting of `SignInResponse` (where JWT + refresh token sit) and
   `DashBoardResponse.result` (the `currentChargeLevel` path).
3. Whether `dashboardOauth` returns a cached last-known value or triggers a
   vehicle wake / fresh poll (matters for 12V battery drain and poll interval).
4. Rate limits / throttling.
5. Token lifetime and exact refresh semantics.
