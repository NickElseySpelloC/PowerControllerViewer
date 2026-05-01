"""View model for LightingControl summary and daily pages."""
import datetime as dt
import operator

from sc_foundation import DateHelper

from view_models.common import format_date_with_ordinal, nav_url

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build_lighting_view(
    state: dict,
    state_idx: int,
    next_idx: int | None,
    all_states: list[dict],
    key: str | None,
    refresh_delay: int,
    debug_message: str | None = None,
) -> dict:
    last_save = state.get("LocalLastSaveTime") or DateHelper.now()
    switch_events = state.get("SwitchEvents") or []
    schedules = _enrich_schedules(state.get("Schedules") or [])

    next_state = all_states[next_idx] if next_idx is not None else None
    dusk = state.get("Dusk")
    dawn = state.get("Dawn")

    return {
        "home_url": nav_url("/", key),
        "AccessKey": key,
        "RefreshDelay": refresh_delay,
        "state_file_type": "LightingControl",
        "DeviceName": state.get("DeviceName") or "Unknown",
        "next_url": nav_url("/summary", key, state_idx=next_idx) if next_idx is not None else None,
        "NextDeviceName": (next_state.get("DeviceName")) if next_state else None,
        "daily_url": nav_url("/daily", key, state_idx=state_idx) if switch_events else None,
        "LastCheck": format_date_with_ordinal(last_save, show_time=True),
        "TimeNow": DateHelper.now_str(),
        "LastStatusMessage": state.get("LastStatusMessage") or "",
        "DuskTime": dusk.strftime("%H:%M") if isinstance(dusk, dt.datetime) else None,
        "DawnTime": dawn.strftime("%H:%M") if isinstance(dawn, dt.datetime) else None,
        "HaveSwitchStates": bool(state.get("SwitchStates")),
        "SwitchStates": state.get("SwitchStates") or [],
        "HaveEvents": bool(switch_events),
        "Schedules": schedules,
        "DebugMessage": debug_message,
    }


def build_lighting_ws_update(state: dict) -> dict:
    """Fields for in-place DOM update from WebSocket."""
    switch_states = state.get("SwitchStates") or []
    return {
        "LastStatusMessage": state.get("LastStatusMessage") or "",
        "LastCheck": format_date_with_ordinal(state.get("LocalLastSaveTime"), show_time=True),
        "SwitchStates": switch_states,
    }


def build_lighting_daily_view(
    state: dict,
    state_idx: int,
    day: int,
    max_day: int,
    key: str | None,
    refresh_delay: int,
) -> dict:
    switch_events = sorted(
        state.get("SwitchEvents") or [],
        key=operator.itemgetter("Date"),
        reverse=True,
    )
    day_data = switch_events[day] if day < len(switch_events) else {}
    page_date = day_data.get("Date")

    event_data = []
    for event in (day_data.get("Events") or []):
        trigger = event.get("Schedule") or event.get("Input") or "No Trigger"
        t = event.get("Time")
        event_data.append({
            "Time": t.strftime("%H:%M") if isinstance(t, dt.time) else "?",
            "Switch": event.get("Switch") or "Unknown",
            "Trigger": trigger,
            "State": event.get("State") or "OFF",
        })

    return {
        "home_url": nav_url("/", key),
        "AccessKey": key,
        "RefreshDelay": refresh_delay,
        "CurrentIndex": state_idx,
        "DeviceName": state.get("DeviceName") or "Unknown",
        "Date": page_date.strftime("%d/%m/%Y") if page_date else "Unknown",
        "DateLong": format_date_with_ordinal(page_date),
        "HaveEvents": bool(event_data),
        "Events": event_data,
        "CurrentDay": day,
        "PreviousDay": day + 1 if day < max_day else None,
        "NextDay": day - 1 if day > 0 else None,
        "summary_url": nav_url("/summary", key, state_idx=state_idx),
        "prev_day_url": nav_url("/daily", key, state_idx=state_idx, day=day + 1) if day < max_day else None,
        "next_day_url": nav_url("/daily", key, state_idx=state_idx, day=day - 1) if day > 0 else None,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _enrich_schedules(schedules: list[dict]) -> list[dict]:
    """Add DaysEnabled list and formatted DatesOff to each schedule event."""
    for schedule in schedules:
        for event in (schedule.get("Events") or []):
            dow_str = event.get("DaysOfWeek") or ""
            enabled = _DAY_NAMES if dow_str == "All" else [d.strip() for d in dow_str.split(",") if d.strip()]
            event["DaysEnabled"] = [{"Day": d, "Enabled": d in enabled} for d in _DAY_NAMES]

            for rng in (event.get("DatesOff") or []):
                sd = rng.get("StartDate")
                ed = rng.get("EndDate")
                rng["StartDateAU"] = sd.strftime("%-d %b %y") if isinstance(sd, dt.date) else ""
                rng["EndDateAU"] = ed.strftime("%-d %b %y") if isinstance(ed, dt.date) else ""
    return schedules
