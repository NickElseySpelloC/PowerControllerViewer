"""Contains the Flask views for the application."""
import datetime as dt
import gzip
import json
import operator

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from sc_utility import DateHelper
from werkzeug.datastructures import MultiDict

views = Blueprint(__name__, "views")

# Global for the config, logger and helper classes
config = None
logger = None
helper = None


def register_support_classes(new_config, new_logger, new_helper):
    """Register the PowerControllerState instance."""
    global config, logger, helper  # noqa: PLW0603, pylint: disable=global-statement
    config = new_config
    logger = new_logger
    helper = new_helper


def validate_access_key(args: MultiDict[str, str]) -> bool:
    """Validate the access key from the request arguments.

    Args:
        args (dict): The request arguments containing the access key.

    Returns:
        bool: True if the access key is valid, False otherwise.
    """
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."

    if config.get("Website", "AccessKey") is not None:
        access_key = args.get("key", default=None, type=str)
        if access_key != config.get("Website", "AccessKey"):
            logger.log_message(f"Invalid access key {access_key} used.", "warning")
            return False
    return True


@views.route("/")
def home():  # noqa: PLR0912, PLR0915
    """Render the homepage which shows a list of all the available states.

    Returns:
        Rendered HTML template with the summary data.
    """
    # Check if housekeeping is required
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."
    helper.housekeeping()

    args = request.args

    # Validate the access key if provided
    if not validate_access_key(args):
        return "Access forbidden.", 403

    # Deal with empty state_items array
    state_idx, _ = helper.validate_state_index(requested_state_idx=0)
    if state_idx is None:
        # Render the template with the summary data
        logger.log_message("Home: No states available.", "debug")
        return render_template("no_state.html")

    home_page_data = {
            "AccessKey": config.get("Website", "AccessKey"),
            "RefreshDelay": config.get("Website", "PageAutoRefresh") or 0,
            "CurrentIndex": state_idx,
            "TimeNow": DateHelper.now_str(),
            "LastStateUpdate": helper.format_date_with_ordinal(helper.get_latest_state_modification_time(), True),
            "LastStateReload": helper.format_date_with_ordinal(helper.get_last_state_reload(), True),
            "Devices": [],
    }

    # Now loop through the state_items and build the home page data
    for state_idx, _ in enumerate(helper.state_items):
        state_file_type = helper.get_state(state_idx, "StateFileType", default="PowerController")
        last_save_time = helper.get_state(state_idx, "LocalLastSaveTime")
        device_description = helper.get_state(state_idx, "DeviceDescription")

        device = {
            "StateIndex": state_idx,
            "StateFileType": state_file_type,
            "DeviceName": helper.get_state(state_idx, "DeviceName", default="Unknown"),
            "DeviceDescription": device_description,
            "LastCheck": helper.format_date_with_ordinal(last_save_time, True),
            "IsDeviceRunning": None,
            "Status": None,
        }
        # Figure out the IsDeviceRunning and Status
        if state_file_type == "LightingControl":
            # Device is running if any light is on
            device["IsDeviceRunning"] = False
            switch_states = helper.get_state(state_idx, "SwitchStates", default=[])
            on_count = 0
            for switch in switch_states:
                if switch.get("OutputState", "OFF") == "ON":
                    device["IsDeviceRunning"] = True
                    on_count += 1
            device["Status"] = f"{on_count} lights are on"
        elif state_file_type == "PowerController":
            device["IsDeviceRunning"] = helper.get_state(state_idx, "Output", "IsOn", default=False)
            if helper.get_state(state_idx, "Output", "Type", default="") == "shelly":
                # Fully support Shelly switched devices
                remaining_runtime = helper.hours_to_string(helper.get_state(state_idx, "Output", "RunPlan", "RemainingHours", default=0))
                output_start_time = None
                if device["IsDeviceRunning"]:
                    output_start_time = helper.get_state(state_idx, "Output", "RunHistory", "LastStartTime", default=None)
                    if output_start_time:
                        device["Status"] = f"On at {(output_start_time.strftime("%H:%M") if output_start_time else "Unknown")}, {remaining_runtime} remaining today."
                    else:
                        device["Status"] = f"On, {remaining_runtime} remaining today."
                else:
                    device["Status"] = f"Not running, {remaining_runtime} remaining today."
            else:  # noqa: PLR5501
                # A meter or TeslaMate device
                if device["IsDeviceRunning"]:
                    output_start_time = helper.get_state(state_idx, "Output", "RunHistory", "LastStartTime", default=None)
                    device["Status"] = f"On at {(output_start_time.strftime("%H:%M"))}." if output_start_time else "On."
                else:
                    device["Status"] = "Off."
        elif state_file_type == "TempProbes":
            temp_probes = helper.get_state(state_idx, "TempProbeLogging", "probes", default=[])
            device["Status"] = f"{len(temp_probes)} probes active."
        elif state_file_type == "OutputMetering":
            outputs = helper.get_state(state_idx, "Meters", default=[])
            device["Status"] = f"{len(outputs)} meters logged."

        home_page_data["Devices"].append(device)
    try:
        return render_template("home.html", page_data=home_page_data)

    except KeyError as e:
        logger.log_message(e, "error")
        return helper.generate_html_page(e), 500


@views.route("/summary")
def summary():
    """Render the summary page which shows the summary.

    Returns:
        Rendered HTML template with the summary data.
    """
    # Check if housekeeping is required
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."
    helper.housekeeping()

    args = request.args

    # Validate the access key if provided
    if not validate_access_key(args):
        return "Access forbidden.", 403

    # Set the state index based on the query parameter
    state_idx, state_next_idx = helper.validate_state_index(url_args=args)

    # Deal with empty state_items array
    if state_idx is None:
        # Render the template with the summary data
        logger.log_message("Home: No states available.", "debug")
        return render_template("no_state.html")

    try:
        debug_message = None
        if config.get("Website", "DebugMode") and config.get("Files", "LogFileVerbosity") == "all":
            debug_message = f"Number of states: {len(helper.state_items)} <br>"
            debug_message += f"Logging level: {config.get('Files', 'LogFileVerbosity')} <br>"

        # Build the summary data for the homepage
        state_type = helper.get_state(state_idx, "StateFileType", default="PowerController")
        if state_type == "LightingControl":
            summary_page_data = build_lightingcontrol_homepage(
                state_idx=state_idx,
                state_next_idx=state_next_idx,
                debug_message=debug_message,
            )
            return render_template("summary_lightingcontrol.html", page_data=summary_page_data)

        if state_type == "PowerController":
            summary_page_data = build_power_homepage(
                state_idx=state_idx,
                state_next_idx=state_next_idx,
                debug_message=debug_message,
            )
            return render_template("summary_power.html", page_data=summary_page_data)

        if state_type == "TempProbes":
            summary_page_data = build_temp_probes_homepage(
                state_idx=state_idx,
                state_next_idx=state_next_idx,
                debug_message=debug_message,
            )
            return render_template("temp_probes.html", page_data=summary_page_data)

        if state_type == "OutputMetering":
            summary_page_data = build_output_metering_homepage(
                state_idx=state_idx,
                state_next_idx=state_next_idx,
                url_args=args,
                debug_message=debug_message,
            )
            return render_template("summary_output_metering.html", page_data=summary_page_data)

        error_message = f"Unsupported state file type: {state_type}"
        logger.log_message(error_message, "error")
        return helper.generate_html_page(error_message), 500

    except KeyError as e:
        logger.log_message(e, "error")
        return helper.generate_html_page(e), 500


def build_power_homepage(state_idx: int, state_next_idx: int | None, debug_message: str | None = None):  # noqa: PLR0914
    """Build the homepage for a PowerController state file.

    Args:
        state_idx (int): The index of the selected state which will be type PowerController.
        state_next_idx (int): The index of the next state.
        debug_message (str | None): Optional debug message to include in the response.

    Returns:
        dict: A dictionary containing the summary data for the homepage.

    Raises:
        KeyError: If a required key is missing in the state data.
    """
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "Unknown"

    try:
        state_data = helper.state_items[state_idx]
        assert isinstance(state_data, dict)
        output_data = state_data.get("Output", {}) or {}
        assert isinstance(output_data, dict)
        run_plan = output_data.get("RunPlan", []) or {}
        assert isinstance(run_plan, dict)
        run_history = output_data.get("RunHistory", {}) or {}
        assert isinstance(run_history, dict)
        last_save_time = state_data.get("LocalLastSaveTime", DateHelper.now())
        logger.log_message(f"Home: rendering device {state_data.get('DeviceName')} of type PowerController for client {client_ip}. State timestamp: {last_save_time.strftime('%Y-%m-%d %H:%M:%S')}", "all")  # pyright: ignore[reportOptionalMemberAccess]

        output_start_time = None
        if output_data.get("IsOn"):
            output_start_time = run_history.get("LastStartTime")
        average_hourly_usage = (run_history.get("AlltimeTotals", {}).get("HourlyEnergyUsed") or 0) / 1000
        average_daily_usage = average_hourly_usage * 24
        average_price = run_history.get("AlltimeTotals", {}).get("AveragePrice") or 0

        run_history_days = run_history.get("DailyData", [])
        actual_hours = 0
        target_hours = 0
        if run_history_days:
            run_history_today = run_history_days[-1]
            actual_hours = run_history_today.get("ActualHours", 0) or 0
            target_hours = run_history_today.get("TargetHours")
            prior_shortfall = run_history_today.get("PriorShortfall", 0) or 0
        if target_hours == -1:
            target_hours = None
        if target_hours is None:
            planned_hours = None
        else:
            planned_hours = target_hours + prior_shortfall - actual_hours

        # Build a summary of the run plan
        run_plan_summary = []
        if run_plan:
            run_plan_entries = run_plan.get("RunPlan", []) or []
            for event in run_plan_entries:
                if event.get("StartDateTime") and event.get("EndDateTime"):
                    entry = {
                        "From": event.get("StartDateTime").strftime("%H:%M") if isinstance(event.get("StartDateTime"), dt.datetime) else "Unknown",
                        "To": event.get("EndDateTime").strftime("%H:%M") if isinstance(event.get("EndDateTime"), dt.datetime) else "Unknown",
                        "Duration": helper.hours_to_string(event.get("Minutes", 0) / 60),
                        "AveragePrice": "Unknown" if event.get("Price") is None else f"{round(event.get('Price'), 1)}",
                    }
                    run_plan_summary.append(entry)

        # Build a dict object that we will use to pass the information to the web page
        summary_page_data = {
            "AccessKey": config.get("Website", "AccessKey"),
            "RefreshDelay": config.get("Website", "PageAutoRefresh") or 0,
            "CurrentIndex": state_idx,
            "NextIndex": state_next_idx,
            "NextDeviceName": helper.get_state(state_next_idx, "DeviceName", default="Unknown") if state_next_idx is not None else None,
            "TimeNow": DateHelper.now_str(),
            "DeviceName": output_data.get("Name", "Unknown"),
            "StatusMessage": output_data.get("Reason", "Unknown"),
            "LastCheck": helper.format_date_with_ordinal(last_save_time, True),
            "IsDeviceRunning": output_data.get("IsOn", False),
            "PumpStatus": "Not running" if not output_data.get("IsOn", False) else "Started at " + (output_start_time.strftime("%H:%M:%S") if output_start_time else "Unknown"),
            "ShowPlannedRuntime": target_hours is not None,
            "TargetRuntime": "All" if target_hours is None else helper.hours_to_string(target_hours),
            "PriorShortfall": helper.hours_to_string(prior_shortfall),
            "PlannedRuntime": "All" if planned_hours is None else helper.hours_to_string(planned_hours),
            "ActualRuntime": helper.hours_to_string(actual_hours),
            "RemainingRuntime": helper.hours_to_string(run_plan.get("RemainingHours", 0)),
            "AverageDailyRuntime": helper.hours_to_string(run_history.get("CurrentTotals", {}).get("ActualHoursPerDay", 0)),
            "LivePrices": output_data.get("DeviceMode") == "BestPrice",
            "CurrentPrice": round(run_history.get("CurrentPrice", 0), 1),
            "AverageEnergyPrice": round(average_price, 1),
            "AverageDailyUsage": round(average_daily_usage, 2),
            "AverageDailyCost": f"${average_daily_usage * average_price / 100:.2f}",
            "HaveRunPlan": len(run_plan.get("RunPlan", [])) > 0,
            "RunPlan": run_plan_summary,
            "ForecastPrice": round(run_plan.get("ForecastAveragePrice", 0), 1),
            "DebugMessage": debug_message,
            }
    except KeyError as e:
        error_message = f"An error occurred while rendering a PowerController summary page: {e}"
        raise KeyError(error_message) from e
    else:
        return summary_page_data


def build_lightingcontrol_homepage(state_idx: int, state_next_idx: int | None, debug_message: str | None = None):
    """Build the homepage for a LightingControl state file.

    Args:
        state_idx (int): The index of the selected state which will be type LightingControl.
        state_next_idx (int): The index of the next state.
        debug_message (str | None): Optional debug message to include in the response.

    Returns:
        dict: A dictionary containing the summary data for the homepage.

    Raises:
        KeyError: If a required key is missing in the state data.
    """
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "Unknown"

    try:  # noqa: PLR1702
        last_save_time = helper.get_state(state_idx, "LocalLastSaveTime", default=None) or DateHelper.now()
        logger.log_message(f"Home: rendering device {helper.get_state(state_idx, 'DeviceName', default='Unknown')} of type LightingControl for client {client_ip}. State timestamp: {last_save_time.strftime('%Y-%m-%d %H:%M:%S')}", "all")  # pyright: ignore[reportOptionalMemberAccess]

        # Build a dict object that we will use to pass the information to the web page
        # For now just copy the SwitchStates part of the state file
        summary_page_data = {
            "AccessKey": config.get("Website", "AccessKey"),
            "RefreshDelay": config.get("Website", "PageAutoRefresh") or 0,
            "CurrentIndex": state_idx,
            "NextIndex": state_next_idx,
            "DeviceName": helper.get_state(state_idx, "DeviceName", default="Unknown"),
            "LastStatusMessage": helper.get_state(state_idx, "LastStatusMessage", default="Unknown"),
            "NextDeviceName": helper.get_state(state_next_idx, "DeviceName", default="Unknown") if state_next_idx is not None else None,
            "TimeNow": DateHelper.now_str(),
            "DuskTime": helper.get_state(state_idx, "Dusk").strftime("%H:%M") if isinstance(helper.get_state(state_idx, "Dusk"), dt.datetime) else None,
            "DawnTime": helper.get_state(state_idx, "Dawn").strftime("%H:%M") if isinstance(helper.get_state(state_idx, "Dawn"), dt.datetime) else None,
            "LastCheck": helper.format_date_with_ordinal(last_save_time, True),
            "HaveSwitchStates": len(helper.get_state(state_idx, "SwitchStates", default=[])) > 0,
            "SwitchStates": helper.get_state(state_idx, "SwitchStates", default=[]),
            "HaveEvents": len(helper.get_state(state_idx, "SwitchEvents", default=[])) > 0,
            "DebugMessage": debug_message,
            "Schedules": helper.get_state(state_idx, "Schedules", default=[]),
            }

        # Now itterate through the days of week in the Schedules: Events: DaysOfWeek key
        # Add a new DaysEnabled list to each event, listing all the days of the week and a true/false flag for each
        for schedule in summary_page_data["Schedules"]:
            for event in schedule["Events"]:
                days_enabled = []
                day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

                # Parse the DaysOfWeek string (e.g., "Sat,Sun,Tue" or "All")
                days_of_week_str = event.get("DaysOfWeek", "")
                if days_of_week_str == "All":
                    enabled_days = day_names  # All days are enabled
                else:
                    enabled_days = [day.strip() for day in days_of_week_str.split(",") if day.strip()]

                for day in day_names:
                    days_enabled.append({
                        "Day": day,
                        "Enabled": day in enabled_days,
                    })
                event["DaysEnabled"] = days_enabled

                # If there is a DatesOff key in the event, we need to process the list of dates
                # Convert the date strings to datetime objects
                if "DatesOff" in event and isinstance(event["DatesOff"], list):
                    for rng in event["DatesOff"]:
                        if rng.get("StartDate") and rng.get("EndDate"):
                            rng["StartDateAU"] = rng.get("StartDate").strftime("%-d %b %y")  # type: ignore[attr-defined]
                            rng["EndDateAU"] = rng.get("EndDate").strftime("%-d %b %y")  # type: ignore[attr-defined]

    except KeyError as e:
        error_message = f"An error occurred while rendering a LightingControl summary page: {e}"
        raise KeyError(error_message) from e
    else:
        return summary_page_data


def build_temp_probes_homepage(state_idx: int, state_next_idx: int | None, debug_message: str | None = None):
    """Build the homepage for a TempProbes state file.

    Args:
        state_idx (int): The index of the selected state which will be type TempProbes.
        state_next_idx (int): The index of the next state.
        debug_message (str | None): Optional debug message to include in the response.

    Returns:
        dict: A dictionary containing the summary data for the homepage.

    Raises:
        KeyError: If a required key is missing in the state data.
    """
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "Unknown"

    try:
        state_data = helper.state_items[state_idx]
        assert isinstance(state_data, dict)
        probe_data = state_data.get("TempProbeLogging", {}).get("probes", []) or []
        assert isinstance(probe_data, list)
        last_save_time = state_data.get("LocalLastSaveTime", DateHelper.now())
        logger.log_message(f"Home: rendering device {state_data.get('DeviceName')} of type PowerController for client {client_ip}. State timestamp: {last_save_time.strftime('%Y-%m-%d %H:%M:%S')}", "all")  # pyright: ignore[reportOptionalMemberAccess]

        # Build a summary of the temp probes
        temp_probe_summary = []
        for probe in probe_data:
            temp = probe.get("Temperature")
            has_temp = temp is not None

            # Split temperature into integer and decimal parts for display
            temp_integer = int(temp) if has_temp else 0
            temp_decimal = int((temp - temp_integer) * 10) if has_temp else 0

            # Get last updated time
            if isinstance(probe.get("LastReadingTime"), dt.datetime):
                last_reading_str = probe.get("LastReadingTime").strftime("%H:%M")
            elif isinstance(probe.get("LastLoggedTime"), dt.datetime):
                last_reading_str = probe.get("LastLoggedTime").strftime("%H:%M")
            else:
                last_reading_str = "Unknown"

            entry = {
                "Name": probe.get("DisplayName", probe.get("Name", "Unknown")),
                "HaveTemperature": has_temp,
                "Temperature": temp if has_temp else "N/A",
                "TemperatureInteger": temp_integer,
                "TemperatureDecimal": temp_decimal,
                "LastReadingTime": last_reading_str,
            }
            temp_probe_summary.append(entry)

        # Build a dict object that we will use to pass the information to the web page
        summary_page_data = {
            "AccessKey": config.get("Website", "AccessKey"),
            "RefreshDelay": config.get("Website", "PageAutoRefresh") or 0,
            "CurrentIndex": state_idx,
            "NextIndex": state_next_idx,
            "NextDeviceName": helper.get_state(state_next_idx, "DeviceName", default="Unknown") if state_next_idx is not None else None,
            "TimeNow": DateHelper.now_str(),
            "DeviceName": state_data.get("DeviceName", "Unknown"),
            "DebugMessage": debug_message,
            "LastCheck": helper.format_date_with_ordinal(last_save_time, True),
            "TempProbes": temp_probe_summary,
            "ShellyDevices": get_shelly_output_info(),
            "TempProbeCharts": state_data.get("TempProbeCharts"),
            "CurrentTime": DateHelper.now().strftime("Time: %H:%M:%S"),
            }
    except KeyError as e:
        error_message = f"An error occurred while rendering a TempProbes summary page: {e}"
        raise KeyError(error_message) from e
    else:
        return summary_page_data


def build_output_metering_homepage(  # noqa: PLR0912, PLR0914, PLR0915
    state_idx: int,
    state_next_idx: int | None,
    url_args: MultiDict[str, str] | None = None,
    debug_message: str | None = None,
):
    """Build the homepage for a OutputMeteru=ing state file.

    Args:
        state_idx (int): The index of the selected state which will be type PowerController.
        state_next_idx (int): The index of the next state.
        url_args (MultDict[str, str], optional): The URL arguments to extract the state index from. If provided, it takes precedence over requested_state_idx.
        debug_message (str | None): Optional debug message to include in the response.

    Returns:
        dict: A dictionary containing the summary data for the homepage.

    Raises:
        KeyError: If a required key is missing in the state data.
    """
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "Unknown"

    try:
        state_data = helper.state_items[state_idx]
        assert isinstance(state_data, dict)
        # See if a custom start and end date is provided in the URL args
        if url_args is not None:
            period_idx, custom_start_date, custom_end_date = helper.validate_metering_args(state_idx, url_args)
        reporting_data = helper.build_metering_reporting_data(state_idx, period_idx, custom_start_date, custom_end_date)
        assert isinstance(reporting_data, dict)

        logger.log_message(reporting_data, "debug")

        reporting_periods = reporting_data.get("ReportingPeriods", []) or []
        assert isinstance(reporting_periods, list)
        totals_data = reporting_data.get("Totals", []) or []
        assert isinstance(totals_data, list)
        meters_data = reporting_data.get("Meters", []) or []
        assert isinstance(meters_data, list)

        # totals_data is the overall totals for each reporting period. This lists only includes those periods where show=True
        for idx, period in enumerate(totals_data):

            have_global_data = period.get("HaveData")
            if have_global_data:
                global_energy_used = period.get("GlobalEnergyUsed", 0)
                period["GlobalEnergyUsedStr"] = f"{global_energy_used:.1f} kWh"
                global_cost = period.get("GlobalCost", 0)
                period["GlobalCostStr"] = f"${global_cost:.2f}"

                other_energy_used = period.get("OtherEnergyUsed", 0)
                period["OtherEnergyUsedStr"] = f"{other_energy_used:.1f} kWh"
                period["OtherEnergyUsedStr"] += f" ({(other_energy_used / global_energy_used * 100):.1f}%)" if global_energy_used > 0 else ""

                other_cost = period.get("OtherCost", 0)
                period["OtherCostStr"] = f"${other_cost:.2f}"
                period["OtherCostStr"] += f" ({(other_cost / global_cost * 100):.1f}%)" if global_cost > 0 else ""
            else:
                period["GlobalEnergyUsedStr"] = "N/A"
                period["GlobalCostStr"] = "N/A"
                period["OtherEnergyUsedStr"] = "N/A"
                period["OtherCostStr"] = "N/A"

            # Now format the meter totals
            for meter in meters_data:
                meter_usage = meter.get("Usage", [])[idx]
                if meter_usage.get("HaveData"):
                    energy_used = meter_usage.get("EnergyUsed", 0)
                    if energy_used > 0.1:
                        meter_usage["EnergyUsedStr"] = f"{energy_used:.1f} kWh"
                        meter_usage["EnergyUsedStr"] += f" ({meter_usage.get('EnergyUsedPcnt', 0) * 100:.1f}%)" if meter_usage.get("EnergyUsedPcnt") is not None else ""
                        meter_usage["CostStr"] = f"${meter_usage.get('Cost', 0):.2f}"
                        meter_usage["CostStr"] += f" ({meter_usage.get('CostPcnt', 0) * 100:.1f}%)" if meter_usage.get("CostPcnt") is not None else ""
                    else:
                        meter_usage["EnergyUsedStr"] = "-"
                        meter_usage["CostStr"] = "-"

                else:
                    meter_usage["EnergyUsedStr"] = "N/A"
                    meter_usage["CostStr"] = "N/A"

        # Now build up the periods choice list for the webpage
        periods_choice_list = []
        added_custom = False
        for idx, period in enumerate(reporting_periods):
            if not period.menu:
                continue    # Don't add to the choice list if menu flag is false
            if period.is_custom:
                choice = {
                    "ID": idx,
                    "Custom": True,
                    "Selected": period_idx in {idx, -1},
                    "Name": "Custom",
                    "Description": f"{period.start_date.strftime('%d %b')} to {period.end_date.strftime('%d %b')}",
                }
                added_custom = True
            else:
                choice = {
                    "ID": idx,
                    "Custom": False,
                    "Selected": period_idx == idx,
                    "Name": period.name,
                    "Description": period.name + f" (from {period.start_date.strftime('%d %b')})",
                }
            periods_choice_list.append(choice)

        # Add a custom choice at the end if we don't already have one
        if not added_custom:
            periods_choice_list.append({
                "ID": len(reporting_periods),
                "Custom": True,
                "Selected": period_idx in {idx, -1},
                "Name": "Custom",
                "Description": "Custom period",
            })

        last_save_time = state_data.get("LocalLastSaveTime", DateHelper.now())
        logger.log_message(f"Home: rendering device {state_data.get('DeviceName')} of type OutputMetering for client {client_ip}. State timestamp: {last_save_time.strftime('%Y-%m-%d %H:%M:%S')}", "all")  # pyright: ignore[reportOptionalMemberAccess]

        # Build a dict object that we will use to pass the information to the web page
        summary_page_data = {
            "AccessKey": config.get("Website", "AccessKey"),
            "RefreshDelay": config.get("Website", "PageAutoRefresh") or 0,
            "CurrentIndex": state_idx,
            "DeviceName": state_data.get("DeviceName", "Unknown"),
            "NextIndex": state_next_idx,
            "NextDeviceName": helper.get_state(state_next_idx, "DeviceName", default="Unknown") if state_next_idx is not None else None,
            "TimeNow": DateHelper.now_str(),
            "LastCheck": helper.format_date_with_ordinal(last_save_time, True),
            "FirstDate": reporting_data.get("FirstDate"),
            "LastDate": reporting_data.get("LastDate"),
            "PeriodChoiceList": periods_choice_list,
            "CustomStartDate": custom_start_date,
            "CustomEndDate": custom_end_date,
            "Totals": totals_data,
            "Meters": meters_data,
            "DebugMessage": debug_message,
            }
    except KeyError as e:
        error_message = f"An error occurred while rendering a OutputMetering summary page: {e}"
        raise KeyError(error_message) from e
    else:
        return summary_page_data


@views.route("/daily")
def day_detail():
    """
    Render the daily date page for a given day passed as a query arg.

    For example: http://127.0.0.1:8000/daily?day=1

    Returns:
        Rendered HTML template with the summary data.
    """
    # Check if housekeeping is required
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."
    helper.housekeeping()

    args = request.args

    # Validate the access key if provided
    if not validate_access_key(args):
        return "Access forbidden.", 403

    # Set the state index and day based on the query parameter
    requested_state_idx = args.get("state_idx", default=None, type=int)
    requested_day = args.get("day", default=None, type=int)
    state_idx, day, max_day = helper.validate_day_index(requested_state_idx, requested_day)

    # If the state index is None, we cannot render the page, redirect to the summary page
    if state_idx is None:
        logger.log_message("Daily: No valid state index, returning to home", "all")
        return redirect(url_for("views.home"))

    # If the day index is None, we cannot render the page, redirect to the home page but with a state index arg
    if day is None:
        logger.log_message(f"Daily: No valid day index, returning to home for state {state_idx}", "all")
        return redirect(url_for("views.home", state_idx=state_idx))

    try:
        # Build the summary data for the daily page
        state_type = helper.get_state(state_idx, "StateFileType", default="PowerController")
        if state_type == "LightingControl":
            daily_data = build_lightingcontrol_daily_data(
                state_idx=state_idx,
                day=day,
                max_day=max_day,
            )
            return render_template("daily_lightingcontrol.html", page_data=daily_data)

        if state_type == "PowerController":
            daily_data = build_power_daily_data(
                state_idx=state_idx,
                day=day,
                max_day=max_day,
            )
            return render_template("daily_power.html", page_data=daily_data)

        error_message = f"Unsupported state file type: {state_type}"
        logger.log_message(error_message, "error")
        return helper.generate_html_page(error_message), 500

    except KeyError as e:
        logger.log_message(e, "error")
        return helper.generate_html_page(e), 500


def build_power_daily_data(state_idx: int, day: int, max_day: int) -> dict:  # noqa: PLR0914, PLR0915
    """Build the daily data for a PowerController state file.

    Args:
        state_idx (int): The index of the selected state which will be type PowerController.
        day (int): The day index to retrieve data for, already validated to be in range.
        max_day (int): The maximum day index for validation.

    Raises:
        KeyError: If a required key is missing in the state data.

    Returns:
        dict: A dictionary containing the daily data for the specified day.
    """
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)

    # Get the daily data for the specified day
    try:
        # Build the dict object that we will use to pass the information to the web page
        state_data = helper.state_items[state_idx]
        assert isinstance(state_data, dict)
        output_data = state_data.get("Output", {}) or {}
        assert isinstance(output_data, dict)
        run_plan = output_data.get("RunPlan", {}) or {}
        assert isinstance(run_plan, dict)
        run_history = output_data.get("RunHistory", {}) or {}
        assert isinstance(run_history, dict)

        # We want to page throught the events in reverse order. Make a deep copy of the DailyData list and reverse sort by Date
        daily_data = run_history.get("DailyData", [])
        daily_data.sort(key=operator.itemgetter("Date"), reverse=True)
        day_data = daily_data[day] or {}
        assert isinstance(day_data, dict)

        page_date = day_data.get("Date")
        target_hours = day_data.get("TargetHours")
        prior_shortfall = day_data.get("PriorShortfall", 0) or 0
        actual_hours = day_data.get("ActualHours", 0) or 0
        actual_runtime = helper.hours_to_string(actual_hours) + " hours run"
        remaining_hours = 0
        if target_hours == -1:
            target_hours = None
        if target_hours:
            remaining_hours = max(0, target_hours + prior_shortfall - actual_hours)
        if remaining_hours > 0.01666667:  # 1 minute
            actual_runtime += ", " + helper.hours_to_string(remaining_hours) + " hours remaining"

        energy_usage = f"{(day_data.get('EnergyUsed', 0) or 0) / 1000:.2f} kWh"
        average_price = day_data.get("AveragePrice", 0) or 0
        if average_price > 0:
            energy_usage += f" at {average_price:.1f} c/kWh"
        if (day_data.get("TotalCost", 0) or 0) > 0:
            energy_usage += f" = ${(day_data.get('TotalCost', 0) or 0):.2f}"

        logger.log_message(f"Daily: rendering device {helper.get_state(state_idx, 'DeviceName', default='Unknown')} and day {(page_date.strftime('%d/%m/%Y') if page_date else 'Unknown')} for client {client_ip}. State timestamp: {state_data.get('SaveTime').strftime('%Y-%m-%d %H:%M:%S')}", "all")  # pyright: ignore[reportOptionalMemberAccess]

        daily_data = {
            "AccessKey": config.get("Website", "AccessKey"),
            "RefreshDelay": config.get("Website", "PageAutoRefresh") or 0,
            "CurrentIndex": state_idx,
            "DeviceName": output_data.get("Name", "Unknown"),
            "Date": (page_date.strftime("%d/%m/%Y") if page_date else "Unknown"),
            "DateLong": helper.format_date_with_ordinal(page_date),
            "Shortfall": helper.hours_to_string(prior_shortfall),
            "ShowPlannedRuntime": target_hours is not None,
            "TargetRuntime": "All" if target_hours is None else helper.hours_to_string(target_hours or 0),
            "ActualRuntime": actual_runtime,
            "EnergyUsed": energy_usage,
            "HaveRunPlan": len(day_data.get("DeviceRuns", [])) > 0,
            "CurrentDay": day,
            "PreviousDay": day + 1 if day < max_day else None,
            "NextDay": day - 1 if day > 0 else None,
            }

        # Build the device_runs array
        device_runs = []
        for run in day_data["DeviceRuns"]:
            start_time = run.get("StartTime").strftime("%H:%M")
            if run.get("EndTime") is None:
                end_time = "Running"
            else:
                end_time = run.get("EndTime").strftime("%H:%M")
            duration_str = helper.hours_to_string(run.get("ActualHours", 0))

            price = "Unknown" if run.get("AveragePrice") is None else f"{round(run.get('AveragePrice'), 1)} c/kWh"
            device_runs.append({
                "Start": start_time,
                "End": end_time,
                "Duration": duration_str,
                "Price": price,
            })

        # Add the device_runs array to the daily_data dict
        daily_data["DeviceRuns"] = device_runs
    except KeyError as e:
        error_message = f"An error occurred while rendering the daily data page: {e}"
        raise KeyError(error_message) from e
    else:
        return daily_data


def build_lightingcontrol_daily_data(state_idx: int, day: int, max_day: int) -> dict:
    """Build the daily data for a LightingControl state file.

    Args:
        state_idx (int): The index of the selected state which will be type LightingControl.
        day (int): The day index to retrieve data for, already validated to be in range.
        max_day (int): The maximum day index for validation.

    Raises:
        KeyError: If a required key is missing in the state data.

    Returns:
        dict: A dictionary containing the daily data for the specified day.
    """
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)

    # We want to page throught the events in reverse order. Make a deep copy of the SwitchEvents list and reverse sort by Date
    switch_events = helper.get_state(state_idx, "SwitchEvents", default=[])
    switch_events.sort(key=operator.itemgetter("Date"), reverse=True)

    # Get the daily data for the specified day
    try:
        # Build the dict object that we will use to pass the information to the web page
        day_data = switch_events[day] or {}
        page_date = day_data.get("Date")  # pyright: ignore[reportArgumentType]

        logger.log_message(f"Daily: rendering device {helper.get_state(state_idx, 'DeviceName', default='Unknown')} and day {(page_date.strftime('%d/%m/%Y') if page_date else 'Unknown')} for client {client_ip}. State timestamp: {helper.get_state(state_idx, 'LastStateSaveTime').strftime('%d/%m/%Y')}", "all")

        # Loop through the Events and format the StartTime and EndTime
        event_data = []
        for event in day_data.get("Events", []):
            if event.get("Schedule"):
                trigger = event.get("Schedule")
            elif event.get("Input"):
                trigger = event.get("Input")
            else:
                trigger = "No Trigger"

            new_item = {
                "Time": event.get("Time").strftime("%H:%M") if isinstance(event.get("Time"), dt.time) else "Unknown",
                "Switch": event.get("Switch", "Unknown"),
                "Trigger": trigger,
                "State": event.get("State", "OFF"),
            }
            event_data.append(new_item)

        daily_data = {
            "AccessKey": config.get("Website", "AccessKey"),
            "RefreshDelay": config.get("Website", "PageAutoRefresh") or 0,
            "CurrentIndex": state_idx,
            "DeviceName": helper.get_state(state_idx, "DeviceName", default="Unknown"),
            "Date": (page_date.strftime("%d/%m/%Y") if page_date else "Unknown"),
            "DateLong": helper.format_date_with_ordinal(page_date),
            "HaveEvents": len(day_data.get("Events", [])) > 0,
            "Events": event_data,
            "CurrentDay": day,
            "PreviousDay": day + 1 if day < max_day else None,
            "NextDay": day - 1 if day > 0 else None,
            }

    except KeyError as e:
        error_message = f"An error occurred while rendering the daily data page: {e}"
        raise KeyError(error_message) from e
    else:
        return daily_data


@views.route("/api/submit", methods=["POST"])
def submit_data():  # noqa: PLR0912
    """Accept a JSON object via POST and validate it.

    Returns:
        Rendered HTML template with the summary data.
    """
    assert config is not None, "Config instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."
    if not request.is_json:
        logger.log_message("Submit Data: Content posted is not JSON data", "warning")
        return jsonify({"error": "Invalid content type. Expected JSON."}), 400

    args = request.args

    # Validate the access key if provided
    if config.get("Website", "AccessKey") is not None:
        access_key = args.get("key", default=None, type=str)
        if access_key != config.get("Website", "AccessKey"):
            logger.log_message(f"Submit Data: Invalid access key {access_key} used.", "warning")
            return jsonify({"error": "Access forbidden."}), 403

    if request.headers.get("Content-Encoding", "").lower() == "gzip":
        try:
            raw_data = gzip.decompress(request.get_data())
            data = json.loads(raw_data.decode("utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.log_message(f"Submit Data: Failed to decompress gzip payload: {e}", "warning")
            return jsonify({"error": "Failed to decompress gzip payload."}), 400
    else:
        data = request.get_json()

    # Perform general checks on the JSON object
    if not isinstance(data, dict):
        logger.log_message("Submit Data: Invalid JSON format. Expected a JSON object", "warning")
        return jsonify({"error": "Invalid JSON format. Expected a JSON object."}), 400

    try:
        state_type = data.get("StateFileType", "PowerController")
        required_keys = {}
        if state_type not in {"PowerController", "LightingControl", "TempProbes", "OutputMetering"}:
            logger.log_message(f"Submit Data: Invalid state type: {state_type}", "warning")
            return jsonify({"error": f"Invalid state file type: {state_type}"}), 400

        if state_type == "PowerController":
            # Check for some required required keys and their types
            required_keys = {
                "SaveTime": str,
                "SchemaVersion": int,
                "DeviceName": str,
                "Output": dict,
                "Scheduler": dict,
                }
        elif state_type == "LightingControl":
            # Check for some required required keys and their types
            required_keys = {
                "LastStateSaveTime": str,
                "SchemaVersion": int,
                "DeviceName": str,
                "RandomOffsets": dict,
                "SwitchStates": list,
                }

        elif state_type == "TempProbes":
            # Check for some required required keys and their types
            required_keys = {
                "SaveTime": str,
                "SchemaVersion": int,
                "DeviceName": str,
                "TempProbeLogging": dict,
                }

        elif state_type == "OutputMetering":
            # Check for some required required keys and their types
            required_keys = {
                "SaveTime": str,
                "SchemaVersion": int,
                "DeviceName": str,
                "Summary": dict,
                "Meters": list,
                }

        for key, expected_type in required_keys.items():
            if key not in data:
                logger.log_message(f"Submit Data: Missing required key: {key}", "warning")
                return jsonify({"error": f"Missing required key: {key}"}), 400
            if not isinstance(data[key], expected_type):
                logger.log_message(f"Submit Data: Invalid type for key: {key}. Expected {expected_type.__name__}.", "warning")
                return jsonify({"error": f"Invalid type for key: {key}. Expected {expected_type.__name__}."}), 400
    except KeyError as e:
        logger.log_message(f"Submit Data: Missing required key: {e}", "warning")
        return jsonify({"error": f"Missing required key: {e}"}), 400

    # Process the valid data (example: log it or save it)
    logger.log_message(f"Received valid state data for device: {data['DeviceName']}", "debug")

    # Save the state file
    helper.save_state(data)

    # Check if housekeeping is required
    helper.housekeeping()

    # Display a success message
    return jsonify({"message": "Data received and validated successfully."}), 200


def get_shelly_output_info() -> list[dict]:
    """Get a list of Shelly device output information.

    Returns:
        list[dict]: A list of dictionaries containing Shelly device output information.
    """
    assert helper is not None, "Helper instance is not initialized."

    shelly_devices = []
    for state in helper.state_items:
        assert isinstance(state, dict)
        if state.get("StateFileType") == "PowerController" and state.get("Output", {}).get("Type") == "shelly":
            shelly_info = {
                "Name": state.get("DeviceName", "Unknown"),
                "IsOn": state.get("Output", {}).get("IsOn", False),
            }
            shelly_devices.append(shelly_info)
    return shelly_devices
