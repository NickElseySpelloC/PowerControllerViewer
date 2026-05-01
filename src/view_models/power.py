"""View model for PowerController summary and daily pages."""
import datetime as dt

from sc_foundation import DateHelper

from view_models.common import format_date_with_ordinal, hours_to_string, nav_url


def build_power_view(
    state: dict,
    state_idx: int,
    next_idx: int | None,
    all_states: list[dict],
    key: str | None,
    refresh_delay: int,
    debug_message: str | None = None,
) -> dict:
    output = state.get("Output") or {}
    run_plan = output.get("RunPlan") or {}
    run_history = output.get("RunHistory") or {}
    last_save = state.get("LocalLastSaveTime") or DateHelper.now()

    is_on = bool(output.get("IsOn"))
    start_time: dt.datetime | None = None
    if is_on:
        start_time = (run_history.get("LastStartTime") if isinstance(run_history.get("LastStartTime"), dt.datetime) else None)

    daily_data_list = run_history.get("DailyData") or []
    actual_hours = 0.0
    target_hours: float | None = None
    prior_shortfall = 0.0
    if daily_data_list:
        today_data = daily_data_list[-1]
        actual_hours = today_data.get("ActualHours") or 0.0
        target_hours = today_data.get("TargetHours")
        prior_shortfall = today_data.get("PriorShortfall") or 0.0
    if target_hours == -1:
        target_hours = None
    planned_hours = (target_hours + prior_shortfall - actual_hours) if target_hours is not None else None

    avg_hourly = ((run_history.get("AlltimeTotals") or {}).get("HourlyEnergyUsed") or 0) / 1000
    avg_price = (run_history.get("AlltimeTotals") or {}).get("AveragePrice") or 0

    run_plan_entries = (run_plan.get("RunPlan") or [])
    run_plan_summary = []
    for event in run_plan_entries:
        if event.get("StartDateTime") and event.get("EndDateTime"):
            run_plan_summary.append({
                "From": event["StartDateTime"].strftime("%H:%M") if isinstance(event["StartDateTime"], dt.datetime) else "?",
                "To": event["EndDateTime"].strftime("%H:%M") if isinstance(event["EndDateTime"], dt.datetime) else "?",
                "Duration": hours_to_string((event.get("Minutes") or 0) / 60),
                "AveragePrice": "?" if event.get("Price") is None else f"{round(event['Price'], 1)}",
            })

    next_state = all_states[next_idx] if next_idx is not None else None

    pump_status = _pump_status(is_on, start_time)
    return {
        "home_url": nav_url("/", key),
        "AccessKey": key,
        "RefreshDelay": refresh_delay,
        "state_file_type": "PowerController",
        "DeviceName": output.get("Name") or state.get("DeviceName") or "Unknown",
        "next_url": nav_url("/summary", key, state_idx=next_idx) if next_idx is not None else None,
        "NextDeviceName": (next_state.get("Output", {}).get("Name") or next_state.get("DeviceName")) if next_state else None,
        "daily_url": nav_url("/daily", key, state_idx=state_idx),
        "LastCheck": format_date_with_ordinal(last_save, show_time=True),
        "TimeNow": DateHelper.now_str(),
        "IsDeviceRunning": is_on,
        "StatusMessage": output.get("Reason") or "",
        "PumpStatus": pump_status,
        "ShowPlannedRuntime": target_hours is not None,
        "TargetRuntime": hours_to_string(target_hours),
        "PriorShortfall": hours_to_string(prior_shortfall),
        "PlannedRuntime": hours_to_string(planned_hours),
        "ActualRuntime": hours_to_string(actual_hours),
        "RemainingRuntime": hours_to_string(run_plan.get("RemainingHours") or 0),
        "AverageDailyRuntime": hours_to_string((run_history.get("CurrentTotals") or {}).get("ActualHoursPerDay") or 0),
        "LivePrices": output.get("DeviceMode") == "BestPrice",
        "CurrentPrice": round((run_history.get("CurrentPrice") or 0), 1),
        "AverageEnergyPrice": round(avg_price, 1),
        "AverageDailyUsage": round(avg_hourly * 24, 2),
        "AverageDailyCost": f"${avg_hourly * 24 * avg_price / 100:.2f}",
        "HaveRunPlan": len(run_plan_summary) > 0,
        "RunPlan": run_plan_summary,
        "ForecastPrice": round(run_plan.get("ForecastAveragePrice") or 0, 1),
        "DebugMessage": debug_message,
    }


def build_power_ws_update(state: dict) -> dict:
    """Minimal fields for in-place WebSocket DOM update."""
    output = state.get("Output") or {}
    run_plan = output.get("RunPlan") or {}
    run_history = output.get("RunHistory") or {}
    is_on = bool(output.get("IsOn"))
    start_time = run_history.get("LastStartTime") if is_on else None
    daily_data_list = run_history.get("DailyData") or []
    actual_hours = (daily_data_list[-1].get("ActualHours") or 0) if daily_data_list else 0

    return {
        "IsDeviceRunning": is_on,
        "PumpStatus": _pump_status(is_on, start_time),
        "StatusMessage": output.get("Reason") or "",
        "ActualRuntime": hours_to_string(actual_hours),
        "RemainingRuntime": hours_to_string(run_plan.get("RemainingHours") or 0),
        "CurrentPrice": round((run_history.get("CurrentPrice") or 0), 1),
        "LastCheck": format_date_with_ordinal(state.get("LocalLastSaveTime"), show_time=True),
    }


def build_power_daily_view(
    state: dict,
    state_idx: int,
    day: int,
    max_day: int,
    key: str | None,
    refresh_delay: int,
) -> dict:
    output = state.get("Output") or {}
    run_history = output.get("RunHistory") or {}
    daily_data = sorted(run_history.get("DailyData") or [], key=lambda d: d.get("Date") or "", reverse=True)
    day_data = daily_data[day] if day < len(daily_data) else {}

    page_date = day_data.get("Date")
    target_hours = day_data.get("TargetHours")
    prior_shortfall = day_data.get("PriorShortfall") or 0
    actual_hours = day_data.get("ActualHours") or 0
    if target_hours == -1:
        target_hours = None

    remaining = max(0, (target_hours or 0) + prior_shortfall - actual_hours) if target_hours else 0
    actual_str = hours_to_string(actual_hours) + " hrs run"
    if remaining > 0.0167:
        actual_str += f", {hours_to_string(remaining)} hrs remaining"

    energy_used = (day_data.get("EnergyUsed") or 0) / 1000
    avg_price = day_data.get("AveragePrice") or 0
    total_cost = day_data.get("TotalCost") or 0
    energy_str = f"{energy_used:.2f} kWh"
    if avg_price > 0:
        energy_str += f" at {avg_price:.1f} c/kWh"
    if total_cost > 0:
        energy_str += f" = ${total_cost:.2f}"

    device_runs = []
    for run in (day_data.get("DeviceRuns") or []):
        st = run.get("StartTime")
        et = run.get("EndTime")
        price = run.get("AveragePrice")
        device_runs.append({
            "Start": st.strftime("%H:%M") if isinstance(st, dt.datetime) else "?",
            "End": et.strftime("%H:%M") if isinstance(et, dt.datetime) else "Running",
            "Duration": hours_to_string(run.get("ActualHours") or 0),
            "Price": "?" if price is None else f"{round(price, 1)} c/kWh",
        })

    return {
        "home_url": nav_url("/", key),
        "AccessKey": key,
        "RefreshDelay": refresh_delay,
        "CurrentIndex": state_idx,
        "DeviceName": output.get("Name") or state.get("DeviceName") or "Unknown",
        "Date": page_date.strftime("%d/%m/%Y") if page_date else "Unknown",
        "DateLong": format_date_with_ordinal(page_date),
        "Shortfall": hours_to_string(prior_shortfall),
        "ShowPlannedRuntime": target_hours is not None,
        "TargetRuntime": hours_to_string(target_hours),
        "ActualRuntime": actual_str,
        "EnergyUsed": energy_str,
        "HaveRunPlan": len(device_runs) > 0,
        "DeviceRuns": device_runs,
        "CurrentDay": day,
        "PreviousDay": day + 1 if day < max_day else None,
        "NextDay": day - 1 if day > 0 else None,
        "summary_url": nav_url("/summary", key, state_idx=state_idx),
        "prev_day_url": nav_url("/daily", key, state_idx=state_idx, day=day + 1) if day < max_day else None,
        "next_day_url": nav_url("/daily", key, state_idx=state_idx, day=day - 1) if day > 0 else None,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _pump_status(is_on: bool, start_time: dt.datetime | None) -> str:
    if not is_on:
        return "Not running"
    started = start_time.strftime("%H:%M:%S") if isinstance(start_time, dt.datetime) else "Unknown"
    return f"Started at {started}"
