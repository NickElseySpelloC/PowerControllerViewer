"""View model for OutputMetering summary page."""
import datetime as dt
from dataclasses import dataclass

from sc_foundation import DateHelper

from view_models.common import format_date_with_ordinal, nav_url


@dataclass
class ReportingPeriod:
    name: str
    start_date: dt.date
    end_date: dt.date
    is_custom: bool = False
    show: bool = False
    menu: bool = True
    have_global_data: bool = False
    global_energy_used: float = 0.0
    global_cost: float = 0.0
    output_energy_used: float = 0.0
    output_cost: float = 0.0
    other_energy_used: float = 0.0
    other_cost: float = 0.0


def build_metering_view(
    state: dict,
    state_idx: int,
    next_idx: int | None,
    all_states: list[dict],
    key: str | None,
    refresh_delay: int,
    period_idx: int | None = None,
    custom_start: dt.date | None = None,
    custom_end: dt.date | None = None,
    debug_message: str | None = None,
) -> dict:
    last_save = state.get("LocalLastSaveTime") or DateHelper.now()
    next_state = all_states[next_idx] if next_idx is not None else None

    reporting_data = build_metering_reporting_data(state, period_idx, custom_start, custom_end)

    # Format strings for the reporting totals
    totals = _format_totals(reporting_data.get("Totals") or [], reporting_data.get("Meters") or [])
    meters = reporting_data.get("Meters") or []

    # Build period selector list
    reporting_periods = reporting_data.get("ReportingPeriods") or []
    periods_choice_list = _build_period_choices(reporting_periods, period_idx)
    first_date = reporting_data.get("FirstDate")
    last_date = reporting_data.get("LastDate")

    return {
        "home_url": nav_url("/", key),
        "AccessKey": key,
        "RefreshDelay": refresh_delay,
        "state_file_type": "OutputMetering",
        "DeviceName": state.get("DeviceName") or "Unknown",
        "CurrentIndex": state_idx,
        "next_url": nav_url("/summary", key, state_idx=next_idx) if next_idx is not None else None,
        "NextDeviceName": next_state.get("DeviceName") if next_state else None,
        "LastCheck": format_date_with_ordinal(last_save, show_time=True),
        "TimeNow": DateHelper.now_str(),
        "FirstDate": first_date.isoformat() if first_date else None,
        "LastDate": last_date.isoformat() if last_date else None,
        "PeriodChoiceList": periods_choice_list,
        "CustomStartDate": custom_start.isoformat() if custom_start else None,
        "CustomEndDate": custom_end.isoformat() if custom_end else None,
        "Totals": totals,
        "Meters": meters,
        "DebugMessage": debug_message,
    }


def validate_metering_args(
    state: dict,
    url_args: dict,
) -> tuple[int | None, dt.date | None, dt.date | None]:
    """Parse and validate metering URL arguments. Returns (period_idx, start, end)."""
    summary = (state.get("Summary") or {})
    earliest = summary.get("FirstDate")
    latest = summary.get("LastDate")

    period_idx_raw = url_args.get("period_idx")
    start_str = url_args.get("start_date")
    end_str = url_args.get("end_date")

    if start_str and end_str:
        try:
            start = dt.datetime.strptime(start_str, "%Y-%m-%d").date()  # noqa: DTZ007
            end = dt.datetime.strptime(end_str, "%Y-%m-%d").date()  # noqa: DTZ007
            if (earliest and latest and
                    earliest <= start <= latest and
                    earliest <= end <= latest and
                    start <= end):
                return -1, start, end
        except ValueError:
            pass

    if period_idx_raw is not None:
        try:
            period_idx = int(period_idx_raw)
            periods = _build_reporting_periods(state, None)
            if 0 <= period_idx < len(periods):
                return period_idx, None, None
        except (ValueError, TypeError):
            pass

    return None, None, None


# ── Core reporting logic ──────────────────────────────────────────────────────

def build_metering_reporting_data(
    state: dict,
    period_idx: int | None,
    custom_start: dt.date | None,
    custom_end: dt.date | None,
) -> dict:
    summary = state.get("Summary") or {}
    meter_data = state.get("Meters") or []

    reporting_periods = _build_reporting_periods(state, period_idx, custom_start, custom_end)
    for period in reporting_periods:
        _calc_global_totals(state, period)

    meters_out = []
    for meter in meter_data:
        display_name = meter.get("DisplayName") or meter.get("Output") or "Unknown"
        usage_list = []
        for period in reporting_periods:
            if not period.show:
                continue
            usage_entry = _calc_meter_usage(meter, period)
            period.output_energy_used += usage_entry["EnergyUsed"]
            period.output_cost += usage_entry["Cost"]
            if period.have_global_data:
                if period.global_energy_used > 0:
                    usage_entry["EnergyUsedPcnt"] = usage_entry["EnergyUsed"] / period.global_energy_used
                if period.global_cost > 0:
                    usage_entry["CostPcnt"] = usage_entry["Cost"] / period.global_cost
            usage_list.append(usage_entry)
        meters_out.append({"Name": display_name, "Usage": usage_list})

    totals_out = []
    for period in reporting_periods:
        if not period.show:
            continue
        period.other_energy_used = period.global_energy_used - period.output_energy_used
        period.other_cost = period.global_cost - period.output_cost
        if period.is_custom:
            period_and_date = f"Custom: {period.start_date.strftime('%d %b')} to {period.end_date.strftime('%d %b')}"
        else:
            period_and_date = period.name + f" (from {period.start_date.strftime('%d %b')})"
        totals_out.append({
            "Period": period.name,
            "PeriodAndDate": period_and_date,
            "HaveData": period.have_global_data,
            "GlobalEnergyUsed": period.global_energy_used,
            "GlobalCost": period.global_cost,
            "OtherEnergyUsed": period.other_energy_used,
            "OtherCost": period.other_cost,
        })

    return {
        "FirstDate": summary.get("FirstDate"),
        "LastDate": summary.get("LastDate"),
        "ReportingPeriods": reporting_periods,
        "Totals": totals_out,
        "Meters": meters_out,
    }


def _build_reporting_periods(
    state: dict,
    period_idx: int | None,
    custom_start: dt.date | None = None,
    custom_end: dt.date | None = None,
) -> list[ReportingPeriod]:
    summary = state.get("Summary") or {}
    today = DateHelper.today()
    yesterday = today - dt.timedelta(days=1)

    this_week_start = today - dt.timedelta(days=today.weekday())
    last_week_start = this_week_start - dt.timedelta(days=7)
    last_week_end = this_week_start - dt.timedelta(days=1)
    this_month_start = today.replace(day=1)
    last_month_end = this_month_start - dt.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    current_week_end = max(yesterday, this_week_start)
    current_month_end = max(yesterday, this_month_start)

    periods = [
        ReportingPeriod("All Dates", summary.get("FirstDate"), summary.get("LastDate")),
        ReportingPeriod("Last 30 Days", today - dt.timedelta(days=30), yesterday, show=True, menu=False),
        ReportingPeriod("Last Month", last_month_start, last_month_end),
        ReportingPeriod("This Month", this_month_start, current_month_end),
        ReportingPeriod("Last 7 Days", today - dt.timedelta(days=7), yesterday, show=True, menu=False),
        ReportingPeriod("Last Week", last_week_start, last_week_end),
        ReportingPeriod("This Week", this_week_start, current_week_end),
        ReportingPeriod("Yesterday", yesterday, yesterday, show=True, menu=False),
        ReportingPeriod("Today", today, today),
    ]

    if custom_start and custom_end:
        periods.append(ReportingPeriod("Custom Period", custom_start, custom_end, is_custom=True, show=True, menu=True))

    if period_idx is not None and 0 <= period_idx < len(periods):
        periods[period_idx].show = True

    return periods


def _calc_global_totals(state: dict, period: ReportingPeriod):
    if not period.show:
        return
    period.have_global_data = False
    for entry in (state.get("Totals") or []):
        entry_date = entry.get("Date")
        if not isinstance(entry_date, dt.date):
            continue
        if period.start_date and period.end_date and not (period.start_date <= entry_date <= period.end_date):
            continue
        period.have_global_data = True
        period.global_energy_used += entry.get("EnergyUsed") or 0.0
        period.global_cost += entry.get("Cost") or 0.0


def _calc_meter_usage(meter: dict, period: ReportingPeriod) -> dict:
    entry: dict = {
        "Period": period.name,
        "HaveData": False,
        "EnergyUsed": 0.0,
        "EnergyUsedPcnt": None,
        "Cost": 0.0,
        "CostPcnt": None,
    }
    first_date = meter.get("FirstDate")
    if first_date and period.start_date and first_date > period.start_date:
        return entry
    entry["HaveData"] = True
    for item in (meter.get("Usage") or []):
        item_date = item.get("Date")
        if period.start_date and period.end_date and isinstance(item_date, dt.date) and period.start_date <= item_date <= period.end_date:
            entry["EnergyUsed"] += item.get("EnergyUsed") or 0.0
            entry["Cost"] += item.get("Cost") or 0.0
    return entry


def _format_totals(totals: list[dict], meters: list[dict]) -> list[dict]:
    """Add *Str display fields to totals and meter usage entries."""
    for idx, period in enumerate(totals):
        if period.get("HaveData"):
            ge = period.get("GlobalEnergyUsed") or 0
            gc = period.get("GlobalCost") or 0
            oe = period.get("OtherEnergyUsed") or 0
            oc = period.get("OtherCost") or 0
            period["GlobalEnergyUsedStr"] = f"{ge:.1f} kWh"
            period["GlobalCostStr"] = f"${gc:.2f}"
            period["OtherEnergyUsedStr"] = f"{oe:.1f} kWh"
            if ge > 0:
                period["OtherEnergyUsedStr"] += f" ({oe / ge * 100:.1f}%)"
            period["OtherCostStr"] = f"${oc:.2f}"
            if gc > 0:
                period["OtherCostStr"] += f" ({oc / gc * 100:.1f}%)"
            for meter in meters:
                usage = (meter.get("Usage") or [])[idx] if idx < len(meter.get("Usage") or []) else {}
                _format_meter_usage(usage)
        else:
            period["GlobalEnergyUsedStr"] = "N/A"
            period["GlobalCostStr"] = "N/A"
            period["OtherEnergyUsedStr"] = "N/A"
            period["OtherCostStr"] = "N/A"
    return totals


def _format_meter_usage(usage: dict) -> None:
    """Add EnergyUsedStr and CostStr display fields to a meter usage entry in-place."""
    if not usage.get("HaveData"):
        usage["EnergyUsedStr"] = "N/A"
        usage["CostStr"] = "N/A"
        return
    eu = usage.get("EnergyUsed") or 0
    if eu > 0.1:
        usage["EnergyUsedStr"] = f"{eu:.1f} kWh"
        if usage.get("EnergyUsedPcnt") is not None:
            usage["EnergyUsedStr"] += f" ({usage['EnergyUsedPcnt'] * 100:.1f}%)"
        usage["CostStr"] = f"${usage.get('Cost', 0):.2f}"
        if usage.get("CostPcnt") is not None:
            usage["CostStr"] += f" ({usage['CostPcnt'] * 100:.1f}%)"
    else:
        usage["EnergyUsedStr"] = "-"
        usage["CostStr"] = "-"


def _build_period_choices(periods: list[ReportingPeriod], period_idx: int | None) -> list[dict]:
    choices = []
    added_custom = False
    for idx, period in enumerate(periods):
        if not period.menu:
            continue
        if period.is_custom:
            choices.append({
                "ID": idx, "Custom": True,
                "Selected": period_idx in {idx, -1},
                "Name": "Custom",
                "Description": f"{period.start_date.strftime('%d %b')} to {period.end_date.strftime('%d %b')}",
            })
            added_custom = True
        else:
            choices.append({
                "ID": idx, "Custom": False,
                "Selected": period_idx == idx,
                "Name": period.name,
                "Description": f"{period.name} (from {period.start_date.strftime('%d %b')})",
            })
    if not added_custom:
        choices.append({
            "ID": len(periods), "Custom": True,
            "Selected": False, "Name": "Custom", "Description": "Custom period",
        })
    return choices
