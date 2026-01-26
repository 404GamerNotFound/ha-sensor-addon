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

from .const import (
    DOMAIN,
    RESCAN_INTERVAL,
    SAVE_DELAY,
    STORAGE_KEY,
    STORAGE_VERSION,
    SUPPORTED_DEVICE_CLASSES,
)


@dataclass
class OccupancyState:
    total_seconds: float = 0.0
    total_activations: int = 0
    on_since: datetime | None = None
    last_updated: datetime | None = None
    last_triggered: datetime | None = None


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
            on_since = None
            if state.get("on_since"):
                on_since = dt_util.parse_datetime(state["on_since"])
            last_updated = None
            if state.get("last_updated"):
                last_updated = dt_util.parse_datetime(state["last_updated"])
            last_triggered = None
            if state.get("last_triggered"):
                last_triggered = dt_util.parse_datetime(state["last_triggered"])
            self._states[entity_id] = OccupancyState(
                total_seconds=float(state.get("total_seconds", 0.0)),
                total_activations=int(state.get("total_activations", 0)),
                on_since=on_since,
                last_updated=last_updated,
                last_triggered=last_triggered,
            )

    @callback
    def async_schedule_save(self) -> None:
        self._store.async_delay_save(self._serialize, SAVE_DELAY.total_seconds())

    def _serialize(self) -> dict[str, Any]:
        return {
            "states": {
                entity_id: {
                    "total_seconds": state.total_seconds,
                    "total_activations": state.total_activations,
                    "on_since": state.on_since.isoformat() if state.on_since else None,
                    "last_updated": state.last_updated.isoformat()
                    if state.last_updated
                    else None,
                    "last_triggered": state.last_triggered.isoformat()
                    if state.last_triggered
                    else None,
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
        self._entities: dict[str, MotionOccupancyBaseSensor] = {}
        self._source_entity_ids: set[str] = set()
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
            if state.attributes.get("device_class") in SUPPORTED_DEVICE_CLASSES
        ]
        new_entities: list[MotionOccupancyBaseSensor] = []
        pending_links: list[tuple[str, MotionOccupancyBaseSensor]] = []
        source_entity_ids: set[str] = set()
        for state in motion_states:
            entity_id = state.entity_id
            source_entity_ids.add(entity_id)
            source_entry = entity_registry.async_get(entity_id)
            if entity_id not in self._entities:
                device_info = self._device_info_for_entity(
                    device_registry, entity_id, state, source_entry
                )
                total_sensor = MotionOccupancyTotalSensor(
                    entity_id, state, self._store, device_info
                )
                count_sensor = MotionOccupancyCountSensor(
                    entity_id, state, self._store, device_info
                )
                self._entities[entity_id] = total_sensor
                self._entities[f"{entity_id}_count"] = count_sensor
                new_entities.extend([total_sensor, count_sensor])
            total_sensor = self._entities.get(entity_id)
            count_sensor = self._entities.get(f"{entity_id}_count")
            if source_entry and source_entry.device_id:
                if total_sensor:
                    pending_links.append((source_entry.device_id, total_sensor))
                if count_sensor:
                    pending_links.append((source_entry.device_id, count_sensor))
            self._sync_state(entity_id, state)
        if new_entities:
            self._async_add_entities(new_entities)
        if pending_links:
            self._ensure_entity_device_links(entity_registry, pending_links)
        self._store.async_schedule_save()
        self._source_entity_ids = source_entity_ids
        self._update_state_listener(self._source_entity_ids)

    @staticmethod
    def _device_info_for_entity(
        device_registry,
        entity_id: str,
        state: State,
        entry,
    ) -> DeviceInfo:
        if entry and entry.device_id:
            device = device_registry.async_get(entry.device_id)
            if device and (device.identifiers or device.connections):
                return DeviceInfo(
                    identifiers=device.identifiers or None,
                    connections=device.connections or None,
                )
        return DeviceInfo(
            identifiers={(DOMAIN, entity_id)},
            name=state.attributes.get("friendly_name", entity_id),
            manufacturer="Motion Occupancy Time",
            model="Virtual Motion Sensor",
        )

    @staticmethod
    def _ensure_entity_device_links(
        entity_registry,
        pending_links: list[tuple[str, MotionOccupancyBaseSensor]],
    ) -> None:
        for device_id, entity in pending_links:
            if not entity.entity_id:
                continue
            entry = entity_registry.async_get(entity.entity_id)
            if entry and entry.device_id != device_id:
                entity_registry.async_update_entity(
                    entity.entity_id, device_id=device_id
                )

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
        self._update_state_listener(self._source_entity_ids)

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
            if state.on_since is None:
                state.on_since = now
                state.last_updated = now
                state.total_activations += 1
                state.last_triggered = now
            else:
                last_updated = state.last_updated or state.on_since
                state.total_seconds += (now - last_updated).total_seconds()
                state.last_updated = now
        elif new_state.state == "off":
            if state.on_since is not None:
                last_updated = state.last_updated or state.on_since
                state.total_seconds += (now - last_updated).total_seconds()
                state.on_since = None
                state.last_updated = None
        for key in (entity_id, f"{entity_id}_count"):
            entity = self._entities.get(key)
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


class MotionOccupancyBaseSensor(SensorEntity):
    def __init__(
        self,
        source_entity_id: str,
        source_state: State,
        store: MotionOccupancyStore,
        device_info: DeviceInfo,
        unique_suffix: str,
        name_suffix: str,
    ) -> None:
        self._source_entity_id = source_entity_id
        self._source_name = source_state.attributes.get("friendly_name", source_entity_id)
        self._store = store
        self._attr_unique_id = f"{source_entity_id}_{unique_suffix}"
        self._attr_name = f"{self._source_name} {name_suffix}"
        self._attr_device_info = device_info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._store.states.get(self._source_entity_id)
        if not state:
            return {}
        current_duration = None
        if state.on_since:
            current_duration = round(
                (dt_util.utcnow() - state.on_since).total_seconds(), 2
            )
        return {
            "current_occupancy_seconds": current_duration,
            "last_triggered": state.last_triggered.isoformat()
            if state.last_triggered
            else None,
            "on_since": state.on_since.isoformat() if state.on_since else None,
            "source_entity_id": self._source_entity_id,
            "source_name": self._source_name,
            "total_activations": state.total_activations,
        }

    @property
    def available(self) -> bool:
        return self.hass.states.get(self._source_entity_id) is not None


class MotionOccupancyTotalSensor(MotionOccupancyBaseSensor):
    _attr_device_class = "duration"
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = "total_increasing"

    def __init__(
        self,
        source_entity_id: str,
        source_state: State,
        store: MotionOccupancyStore,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(
            source_entity_id,
            source_state,
            store,
            device_info,
            "occupancy_total",
            "Occupancy Total",
        )

    @property
    def native_value(self) -> float:
        state = self._store.states.get(self._source_entity_id)
        if not state:
            return 0.0
        if state.on_since:
            last_updated = state.last_updated or state.on_since
            ongoing = (dt_util.utcnow() - last_updated).total_seconds()
        else:
            ongoing = 0.0
        return round(state.total_seconds + ongoing, 2)


class MotionOccupancyCountSensor(MotionOccupancyBaseSensor):
    _attr_state_class = "total_increasing"
    _attr_native_unit_of_measurement = "events"

    def __init__(
        self,
        source_entity_id: str,
        source_state: State,
        store: MotionOccupancyStore,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(
            source_entity_id,
            source_state,
            store,
            device_info,
            "occupancy_count",
            "Occupancy Count",
        )

    @property
    def native_value(self) -> int:
        state = self._store.states.get(self._source_entity_id)
        if not state:
            return 0
        return state.total_activations
