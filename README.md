# Motion Occupancy Time (Home Assistant Custom Integration)

This repository provides a Home Assistant custom integration that adds a total occupancy-time sensor to each existing motion `binary_sensor`.

## Features

- Creates a new sensor entity for every `binary_sensor` with `device_class: motion`.
- Attaches the new sensor to the same device as the original motion entity.
- Persists the total occupied time across restarts.
- No MQTT required; everything runs directly inside Home Assistant.

## Installation (HACS)

1. Add this repository as a custom repository in HACS (Integration).
2. Install **Motion Occupancy Time**.
3. Restart Home Assistant.
4. Add the integration via **Settings → Devices & Services**.

## Resulting entity

Each motion sensor gets an additional entity named:

```
<Motion Sensor Friendly Name> Occupancy Total
```

The sensor reports the total occupied time in seconds and exposes the last active timestamp in its attributes.
