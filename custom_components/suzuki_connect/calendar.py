"""Trip and charging history calendars for Suzuki Connect (extended data).

Each past trip or charging session is a calendar event, so the history can
be browsed in Home Assistant's Calendar view. Events appear after the fact
(on the slow refresh), so they are for browsing, not for triggering
automations. Locations are never included.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SuzukiConfigEntry
from .const import CONF_ENABLE_EXTENDED
from .entity import SuzukiConnectEntity
from .pysuzukiconnect.models import ChargeSession, Trip

# Calendar events need an end after their start; trips and charging sessions
# are reported to the minute, so short ones get at least this long.
MIN_EVENT = timedelta(minutes=1)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SuzukiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if not entry.options.get(CONF_ENABLE_EXTENDED):
        return
    coordinator = entry.runtime_data
    async_add_entities(
        entity
        for cid in coordinator.vehicles
        for entity in (
            SuzukiTripCalendar(coordinator, cid),
            SuzukiChargingCalendar(coordinator, cid),
        )
    )


def _number(value: float | None) -> str:
    return f"{value:g}" if value is not None else "?"


def _duration(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    hours, mins = divmod(round(minutes), 60)
    return f"{hours} h {mins:02d} min" if hours else f"{mins} min"


def _span(start: datetime, end: datetime | None, minutes: float | None) -> datetime:
    """An end strictly after ``start``, from the end time or the duration."""
    if end is None and minutes is not None:
        end = start + timedelta(minutes=minutes)
    return max(end or start, start + MIN_EVENT)


class _HistoryCalendar(SuzukiConnectEntity, CalendarEntity):
    """A calendar of past events: there is never a current or upcoming one."""

    @property
    def event(self) -> CalendarEvent | None:
        return None


class SuzukiTripCalendar(_HistoryCalendar):
    _attr_translation_key = "trip_history"

    def __init__(self, coordinator, contract_id: int) -> None:
        super().__init__(coordinator, contract_id, "trip_history")

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        trips = await self.coordinator.async_trips_between(
            self._contract_id, start_date, end_date
        )
        return [self._event(t) for t in trips]

    def _event(self, trip: Trip) -> CalendarEvent:
        start = self.coordinator.vehicle_time(trip.start)
        end = _span(start, self.coordinator.vehicle_time(trip.end), trip.duration_minutes)
        summary = " · ".join(
            part for part in (
                f"{_number(trip.distance)} {trip.distance_unit or ''}".strip(),
                _duration(trip.duration_minutes),
                f"{_number(trip.average_consumption)} {trip.average_consumption_unit or ''}".strip()
                if trip.average_consumption is not None else None,
            ) if part
        )
        details = []
        if trip.battery_used_pct is not None:
            details.append(f"Battery used: {trip.battery_used_pct}%")
        if trip.start_odometer is not None and trip.end_odometer is not None:
            details.append(
                f"Odometer: {_number(trip.start_odometer)} → {_number(trip.end_odometer)}"
            )
        return CalendarEvent(
            start=start,
            end=end,
            summary=f"Trip: {summary}",
            description="\n".join(details) or None,
            uid=f"trip-{trip.trip_id}" if trip.trip_id else None,
        )


class SuzukiChargingCalendar(_HistoryCalendar):
    _attr_translation_key = "charging_history"

    def __init__(self, coordinator, contract_id: int) -> None:
        super().__init__(coordinator, contract_id, "charging_history")

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        events = []
        for session in self.coordinator.charge_sessions(self._contract_id):
            event = self._event(session)
            if event is not None and event.start < end_date and event.end > start_date:
                events.append(event)
        return events

    def _event(self, session: ChargeSession) -> CalendarEvent | None:
        start = self.coordinator.vehicle_time(session.time)
        if start is None:
            return None
        parts = []
        if session.start_level is not None and session.end_level is not None:
            parts.append(f"{session.start_level}% → {session.end_level}%")
        if session.energy is not None:
            parts.append(f"{_number(session.energy)} kWh")
        if (duration := _duration(session.duration_minutes)) is not None:
            parts.append(duration)
        if session.charge_type:
            parts.append(session.charge_type)
        return CalendarEvent(
            start=start,
            end=_span(start, None, session.duration_minutes),
            summary="Charging: " + (" · ".join(parts) or "session"),
            uid=f"charge-{start.isoformat()}",
        )
