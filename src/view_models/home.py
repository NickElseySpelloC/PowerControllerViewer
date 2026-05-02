"""View model for the home (device list) page."""
import datetime as dt

from sc_foundation import DateHelper

from view_models.common import format_date_with_ordinal, hours_to_string, nav_url


_TYPE_ORDER = ["PowerController", "LightingControl", "TempProbes", "OutputMetering"]
_TYPE_LABELS = {
    "PowerController": "Power Controllers",
    "LightingControl": "Lighting Controllers",
    "TempProbes": "Temperature Probes",
    "OutputMetering": "Metered Outputs",
}


def build_home_view(all_states: list[dict], key: str | None, refresh_delay: int) -> dict:
    last_update = format_date_with_ordinal(_latest_save(all_states), show_time=True)
    return {
        "home_url": nav_url("/", key),
        "AccessKey": key,
        "RefreshDelay": refresh_delay,
        "TimeNow": DateHelper.now_str(),
        "LastStateUpdate": last_update,
        "LastCheck": last_update,
        "TotalDevices": len(all_states),
        "DeviceGroups": _group_devices(all_states, key),
    }


def _group_devices(all_states: list[dict], key: str | None) -> list[dict]:
    """Return devices grouped by StateFileType in a defined display order."""
    buckets: dict[str, list[dict]] = {}
    for state in all_states:
        stype = state.get("StateFileType", "PowerController")
        buckets.setdefault(stype, []).append(_build_device_row(state, key))

    groups = []
    seen: set[str] = set()
    for stype in _TYPE_ORDER:
        if stype in buckets:
            groups.append({
                "TypeLabel": _TYPE_LABELS.get(stype, stype),
                "StateFileType": stype,
                "Devices": buckets[stype],
            })
            seen.add(stype)
    for stype, devices in buckets.items():  # any unrecognised types last
        if stype not in seen:
            groups.append({"TypeLabel": stype, "StateFileType": stype, "Devices": devices})
    return groups


def build_home_device_ws(state: dict) -> dict:
    """Minimal dict for WebSocket home-row update."""
    ts = state.get("LocalLastSaveTime")
    return {
        "device_name": state.get("DeviceName"),
        "last_check": format_date_with_ordinal(ts, show_time=True),
        "last_check_iso": ts.isoformat() if isinstance(ts, dt.datetime) else "",
        "is_running": _is_running(state),
        "status": _status_text(state),
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_device_row(state: dict, key: str | None) -> dict:
    idx = state.get("_idx", 0)
    ts = state.get("LocalLastSaveTime")
    return {
        "StateIndex": idx,
        "StateFileType": state.get("StateFileType", "PowerController"),
        "DeviceName": state.get("DeviceName", "Unknown"),
        "DeviceDescription": state.get("DeviceDescription", ""),
        "summary_url": nav_url("/summary", key, state_idx=idx),
        "LastCheck": format_date_with_ordinal(ts, show_time=True),
        "LastCheckISO": ts.isoformat() if isinstance(ts, dt.datetime) else "",
        "IsDeviceRunning": _is_running(state),
        "Status": _status_text(state),
    }


def _is_running(state: dict) -> bool:
    stype = state.get("StateFileType")
    if stype == "LightingControl":
        return any(sw.get("OutputState") == "ON" for sw in (state.get("SwitchStates") or []))
    if stype == "PowerController":
        return bool((state.get("Output") or {}).get("IsOn", False))
    return False


def _status_text(state: dict) -> str:
    stype = state.get("StateFileType")
    if stype == "LightingControl":
        on_count = sum(1 for sw in (state.get("SwitchStates") or []) if sw.get("OutputState") == "ON")
        return f"{on_count} light{'s' if on_count != 1 else ''} on"
    if stype == "PowerController":
        output = state.get("Output") or {}
        is_on = bool(output.get("IsOn"))
        otype = output.get("Type", "")
        if otype in {"smart device", "shelly"}:
            remaining = hours_to_string((output.get("RunPlan") or {}).get("RemainingHours", 0))
            if is_on:
                start = (output.get("RunHistory") or {}).get("LastStartTime")
                started = f"On at {start.strftime('%H:%M')}, " if isinstance(start, dt.datetime) else "On, "
                return f"{started}{remaining} remaining today."
            return f"Not running, {remaining} remaining today."
        if is_on:
            start = (output.get("RunHistory") or {}).get("LastStartTime")
            return f"On at {start.strftime('%H:%M')}." if isinstance(start, dt.datetime) else "On."
        return "Off."
    if stype == "TempProbes":
        n = len((state.get("TempProbeLogging") or {}).get("probes") or [])
        return f"{n} probe{'s' if n != 1 else ''} active."
    if stype == "OutputMetering":
        n = len(state.get("Meters") or [])
        return f"{n} meter{'s' if n != 1 else ''} logged."
    return ""


def _latest_save(all_states: list[dict]) -> dt.datetime | None:
    times = [s.get("LocalLastSaveTime") for s in all_states if s.get("LocalLastSaveTime")]
    return max(times) if times else None
