"""View model for TempProbes summary page (with Chart.js data)."""
import datetime as dt

from sc_foundation import DateHelper

from view_models.common import format_date_with_ordinal, nav_url


def build_temp_probes_view(
    state: dict,
    state_idx: int,
    next_idx: int | None,
    all_states: list[dict],
    key: str | None,
    refresh_delay: int,
    debug_message: str | None = None,
) -> dict:
    probe_logging = state.get("TempProbeLogging") or {}
    probe_data = probe_logging.get("probes") or []
    probe_history = probe_logging.get("history") or []
    charting = state.get("Charting") or {}
    last_save = state.get("LocalLastSaveTime") or DateHelper.now()

    next_state = all_states[next_idx] if next_idx is not None else None

    temp_probes = [_build_probe_entry(p) for p in probe_data]
    smart_devices = _get_smart_devices(all_states)
    charts_data = _build_charts_data(charting, probe_history, probe_data) if charting.get("Enable") else []

    return {
        "home_url": nav_url("/", key),
        "AccessKey": key,
        "RefreshDelay": refresh_delay,
        "state_file_type": "TempProbes",
        "DeviceName": state.get("DeviceName") or "Unknown",
        "next_url": nav_url("/summary", key, state_idx=next_idx) if next_idx is not None else None,
        "NextDeviceName": next_state.get("DeviceName") if next_state else None,
        "LastCheck": format_date_with_ordinal(last_save, show_time=True),
        "CurrentTime": DateHelper.now().strftime("%H:%M:%S"),
        "TempProbes": temp_probes,
        "SmartDevices": smart_devices,
        "ChartsEnabled": bool(charts_data),
        "ChartsData": charts_data,
        "DebugMessage": debug_message,
    }


def build_temp_probes_ws_update(state: dict) -> dict:
    """Fields for in-place DOM update from WebSocket."""
    probe_logging = state.get("TempProbeLogging") or {}
    probe_data = probe_logging.get("probes") or []
    return {
        "LastCheck": format_date_with_ordinal(state.get("LocalLastSaveTime"), show_time=True),
        "TempProbes": [_build_probe_entry(p) for p in probe_data],
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_probe_entry(probe: dict) -> dict:
    temp = probe.get("Temperature")
    has_temp = temp is not None
    temp_int = int(temp) if has_temp else 0
    temp_dec = int((temp - temp_int) * 10) if has_temp else 0

    last_time = probe.get("LastReadingTime") or probe.get("LastLoggedTime")
    last_str = last_time.strftime("%H:%M") if isinstance(last_time, dt.datetime) else "—"

    return {
        "Name": probe.get("DisplayName") or probe.get("Name") or "Unknown",
        "HaveTemperature": has_temp,
        "Temperature": temp if has_temp else None,
        "TemperatureInteger": temp_int,
        "TemperatureDecimal": temp_dec,
        "LastReadingTime": last_str,
    }


def _get_smart_devices(all_states: list[dict]) -> list[dict]:
    result = []
    for s in all_states:
        if s.get("StateFileType") == "PowerController":
            output = s.get("Output") or {}
            if output.get("Type") in {"shelly", "smart device"}:
                result.append({
                    "Name": s.get("DeviceName") or "Unknown",
                    "IsOn": bool(output.get("IsOn")),
                })
    return result


def _build_charts_data(charting: dict, probe_history: list[dict], probe_config: list[dict]) -> list[dict]:
    """Return a JSON-serialisable list of chart datasets for Chart.js."""
    chart_configs = charting.get("Charts") or []
    charts = []
    for chart_cfg in chart_configs:
        days_to_show = chart_cfg.get("DaysToShow") or 7
        cutoff = DateHelper.now() - dt.timedelta(days=days_to_show)
        probe_names_cfg = chart_cfg.get("Probes") or []

        # Build display metadata from probe config
        probe_meta: dict[str, dict] = {}
        for p in probe_config:
            name = p.get("Name")
            if name:
                probe_meta[name] = {
                    "display_name": p.get("DisplayName") or name,
                    "colour": p.get("Colour"),
                }

        # Gather time series per probe
        series: dict[str, tuple[list, list]] = {}
        for entry in probe_history:
            pname = entry.get("ProbeName")
            ts = entry.get("Timestamp")
            temp = entry.get("Temperature")
            if not pname or pname not in probe_names_cfg:
                continue
            if temp is None or not isinstance(ts, dt.datetime) or ts < cutoff:
                continue
            if pname not in series:
                series[pname] = ([], [])
            series[pname][0].append(ts.isoformat())
            series[pname][1].append(temp)

        datasets = []
        for pname in probe_names_cfg:
            if pname not in series:
                continue
            meta = probe_meta.get(pname) or {}
            timestamps, temps = series[pname]
            datasets.append({
                "probe_name": pname,
                "display_name": meta.get("display_name") or pname,
                "colour": meta.get("colour"),
                "timestamps": timestamps,
                "temperatures": temps,
            })

        if datasets:
            charts.append({
                "name": chart_cfg.get("Name") or "",
                "datasets": datasets,
            })

    return charts
