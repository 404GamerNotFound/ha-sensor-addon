from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN, RESCAN_INTERVAL, SAVE_DELAY, STORAGE_KEY, STORAGE_VERSION


@dataclass
class OccupancyState:
    total_seconds: float = 0.0
    last_on: datetime | None = None


class MotionOccupancyStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._states: dict[str, OccupancyState] = {}

    @property
    def states(self) -> dict[str, OccupancyState]:
        return self._states

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        for entity_id, state in data.get("states", {}).items():
            last_on = None
            if state.get("last_on"):
                last_on = dt_util.parse_datetime(state["last_on"])
            self._states[entity_id] = OccupancyState(
                total_seconds=float(state.get("total_seconds", 0.0)),
                last_on=last_on,
            )

    @callback
    def async_schedule_save(self) -> None:
        self._store.async_delay_save(self._serialize, SAVE_DELAY.total_seconds())

    def _serialize(self) -> dict[str, Any]:
        return {
            "states": {
                entity_id: {
                    "total_seconds": state.total_seconds,
                    "last_on": state.last_on.isoformat() if state.last_on else None,
                }
                for entity_id, state in self._states.items()
            }
        }


class MotionOccupancyManager:
    def __init__(
        self,
        hass: HomeAssistant,
        async_add_entities: AddEntitiesCallback,
    ) -> None:
        self.hass = hass
        self._async_add_entities = async_add_entities
        self._store = MotionOccupancyStore(hass)
        self._entities: dict[str, MotionOccupancySensor] = {}
        self._tracked_entity_ids: set[str] = set()
        self._unsub_state = None
        self._unsub_interval = None

    async def async_initialize(self) -> None:
        await self._store.async_load()
        await self._refresh_entities()
        self._setup_state_listener()
        self._unsub_interval = async_track_time_interval(
            self.hass, self._handle_interval, RESCAN_INTERVAL
        )

    async def async_unload(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None

    async def _handle_interval(self, _now: datetime) -> None:
        await self._refresh_entities()

    async def _refresh_entities(self) -> None:
        entity_registry = async_get_entity_registry(self.hass)
        device_registry = async_get_device_registry(self.hass)
        motion_states = [
            state
            for state in self.hass.states.async_all("binary_sensor")
            if state.attributes.get("device_class") == "motion"
        ]
        new_entities: list[MotionOccupancySensor] = []
        for state in motion_states:
            entity_id = state.entity_id
            if entity_id not in self._entities:
                device_info = self._device_info_for_entity(
                    entity_registry, device_registry, entity_id
                )
                sensor = MotionOccupancySensor(entity_id, state, self._store, device_info)
                self._entities[entity_id] = sensor
                new_entities.append(sensor)
            self._sync_state(entity_id, state)
        if new_entities:
            self._async_add_entities(new_entities)
        self._store.async_schedule_save()
        self._update_state_listener(set(self._entities))

    @staticmethod
    def _device_info_for_entity(entity_registry, device_registry, entity_id: str) -> DeviceInfo | None:
        entry = entity_registry.async_get(entity_id)
        if not entry or not entry.device_id:
            return None
        device = device_registry.async_get(entry.device_id)
        if not device:
            return None
        if not device.identifiers:
            return None
        return DeviceInfo(identifiers=device.identifiers)

    def _update_state_listener(self, entity_ids: set[str]) -> None:
        if entity_ids == self._tracked_entity_ids:
            return
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if entity_ids:
            self._unsub_state = async_track_state_change_event(
                self.hass, entity_ids, self._handle_state_change
            )
        self._tracked_entity_ids = entity_ids

    @callback
    def _setup_state_listener(self) -> None:
        self._update_state_listener(self._tracked_entity_ids)

    @callback
    def _handle_state_change(self, event) -> None:
        entity_id = event.data.get("entity_id")
        new_state: State | None = event.data.get("new_state")
        if not entity_id or new_state is None:
            return
        self._sync_state(entity_id, new_state)
        self._store.async_schedule_save()

    def _sync_state(self, entity_id: str, new_state: State) -> None:
        state = self._store.states.setdefault(entity_id, OccupancyState())
        now = dt_util.utcnow()
        if new_state.state == "on":
            if state.last_on is None:
                state.last_on = now
        elif new_state.state == "off":
            if state.last_on is not None:
                state.total_seconds += (now - state.last_on).total_seconds()
                state.last_on = None
        entity = self._entities.get(entity_id)
        if entity:
            entity.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager = MotionOccupancyManager(hass, async_add_entities)
    hass.data[DOMAIN][entry.entry_id]["manager"] = manager
    await manager.async_initialize()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    manager: MotionOccupancyManager | None = hass.data[DOMAIN][entry.entry_id].get("manager")
    if manager:
        await manager.async_unload()


class MotionOccupancySensor(SensorEntity):
    _attr_device_class = "duration"
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = "total_increasing"

    def __init__(
        self,
        source_entity_id: str,
        source_state: State,
        store: MotionOccupancyStore,
        device_info: DeviceInfo | None,
    ) -> None:
        self._source_entity_id = source_entity_id
        self._source_name = source_state.attributes.get("friendly_name", source_entity_id)
        self._store = store
        self._attr_unique_id = f"{source_entity_id}_occupancy_total"
        self._attr_name = f"{self._source_name} Occupancy Total"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float:
        state = self._store.states.get(self._source_entity_id)
        if not state:
            return 0.0
        return round(state.total_seconds, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._store.states.get(self._source_entity_id)
        if not state:
            return {}
        return {
            "last_on": state.last_on.isoformat() if state.last_on else None,
            "source_entity_id": self._source_entity_id,
        }

    @property
    def available(self) -> bool:
        return self.hass.states.get(self._source_entity_id) is not None
