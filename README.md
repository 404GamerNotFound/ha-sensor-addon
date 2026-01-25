# Motion Occupancy Time (Home Assistant Custom Integration)

This repository provides a Home Assistant custom integration that adds occupancy duration and activation count sensors to each existing motion `binary_sensor`.

## Features

- Creates new sensor entities for every `binary_sensor` with `device_class: motion` or `occupancy`.
- Attaches the new sensor to the same device as the original motion entity.
- Persists the total occupied time and activation counts across restarts.
- No MQTT required; everything runs directly inside Home Assistant.

## Installation (HACS)

1. Add this repository as a custom repository in HACS (Integration).
2. Install **Motion Occupancy Time**.
3. Restart Home Assistant.
4. Add the integration via **Settings → Devices & Services**.

## Resulting entity

Each motion sensor gets additional entities named:

```
<Motion Sensor Friendly Name> Occupancy Total
<Motion Sensor Friendly Name> Occupancy Count
```

The total sensor reports the occupied time in seconds. The count sensor reports how many times the motion entity switched to `on`. Both sensors expose attributes such as the current occupancy duration, last trigger timestamp, and source entity ID.
