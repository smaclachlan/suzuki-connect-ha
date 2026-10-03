# Test fixtures

Sanitised Suzuki Connect responses (contract ID, VIN and location replaced).

| file | source |
|---|---|
| `vehicles.json` | captured from a live e Vitara (sanitised) |
| `dashboard.json` | captured: parked, unplugged, locked (sanitised) |
| `dashboard_charging.json` | **synthetic**, derived from `dashboard.json`: `charge_st=1`, `chargerConnected_st="Y"`, `remainingChargingTime` as a string |
| `dashboard_plugged_in_not_charging.json` | **synthetic**: plugged in, `charge_st=0` |
| `dashboard_null_values.json` | **synthetic**: fields present but `null` |
| `dashboard_dormant.json` | **synthetic**: stale `lut`, `user_data: null` |

Synthetic fixtures pin the parser's behaviour for shapes we expect but have not
yet captured. Replace them with real (sanitised) captures as they become
available, keeping the file names so the tests keep running.
