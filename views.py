"""Contains the Flask views for the application."""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

views = Blueprint(__name__, "views")

# Global for the config, logger and helper classes
config = None
logger = None
helper = None

def register_support_classes(new_config, new_logger, new_helper):
    """Register the PowerControllerState instance."""
    global config  # noqa: PLW0603
    global logger  # noqa: PLW0603
    global helper  # noqa: PLW0603
    config = new_config
    logger = new_logger
    helper = new_helper

@views.route("/home")
def home():
    """Render the home page which shows the summary."""
    # Check if housekeeping is required
    local_tz = datetime.now().astimezone().tzinfo
    helper.housekeeping()

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    args = request.args

    # Validate the access key if provided
    if config.get("Website", "AccessKey") is not None:
        access_key = args.get("key", default=None, type=str)
        if access_key != config.get("Website", "AccessKey"):
            logger.log_message(f"Home: Invalid access key {access_key} used.", "warning")
            return "Access forbidden.", 403

    # Set the state index based on the query parameter
    new_state_idx = args.get("state_idx", default=None, type=int)
    state_idx = helper.get_selected_state(new_state_idx)
    if state_idx is None or len(helper.state_items) < 2:
        state_next_idx = None
    elif state_idx >= len(helper.state_items) - 1:
        state_next_idx = 0
    else:
        state_next_idx = state_idx + 1


    # Deal with empty state_items array
    if state_idx is None:
        # Render the template with the summary data
        logger.log_message("Home: No states available.", "debug")
        return render_template("no_state.html")
    last_save_time = datetime.strptime(helper[state_idx]["LastStateSaveTime"], "%Y-%m-%d %H:%M:%S").astimezone(local_tz)

    pump_start_time = None
    if helper[state_idx]["IsDeviceRunning"]:
        pump_start_time = datetime.strptime(helper[state_idx]["DeviceLastStartTime"], "%Y-%m-%d %H:%M:%S").astimezone(local_tz)
    average_daily_usage = ((helper[state_idx]["EnergyUsed"] - helper[state_idx]["DailyData"][0]["EnergyUsed"]) / 7) / 1000

    debug_message = None
    if config.get("Website", "DebugMode") and config.get("Files", "LogFileVerbosity") == "all":
        debug_message = f"Number of states: {len(helper.state_items)} <br>"
        debug_message += f"Logging level: {config.get('Files', 'LogFileVerbosity')} <br>"

    logger.log_message(f"Home: Process {logger.get_process_id()} rendering device {helper[state_idx]['DeviceName']} for client {client_ip}. State timestamp: {helper[state_idx]['LastStateSaveTime']}", "all")
    average_price = helper[state_idx]["AveragePrice"] if helper[state_idx]["AveragePrice"] is not None else 0

    # Build a dict object that we will use to pass the information to the web page
    summary_page_data = {
        "AccessKey": config.get("Website", "AccessKey"),
        "RefreshDelay": config.get("Website", "PageAutoRefresh") or 0,
        "NextIndex": state_next_idx,
        "NextDeviceName": helper[state_next_idx]["DeviceName"] if state_next_idx is not None else None,
        "TimeNow": datetime.now(local_tz).strftime("%H:%M:%S"),
        "DeviceName": helper[state_idx]["DeviceName"],
        "StatusMessage": helper[state_idx]["LastStatusMessage"] or "Unknown",
        "LastCheck": helper.format_date_with_ordinal(last_save_time, True),
        "PumpStatus": "Not running" if not helper[state_idx]["IsDeviceRunning"] else "Started at " + pump_start_time.strftime("%H:%M:%S"),
        "RemaningRuntime": helper.hours_to_string(helper[state_idx]["DailyData"][0]["RemainingRuntimeToday"]),
        "AverageDailyRuntime": helper.hours_to_string(helper[state_idx]["AverageRuntimePriorDays"]),
        "CurrentPrice": round(helper[state_idx]["CurrentPrice"], 1),
        "AverageEnergyPrice": round(average_price, 1),
        "AverageDailyUsage": round(average_daily_usage, 2),
        "AverageDailyCost": f"${average_daily_usage * average_price / 100:.2f}",
        "HaveRunPlan": len(helper[state_idx]["TodayRunPlan"]) > 0,
        "RunPlan": helper[state_idx]["TodayRunPlan"],
        "ForecastPrice": round(helper[state_idx]["AverageForecastPrice"], 1),
        "DebugMessage": debug_message,
    }

    # Render the template with the summary data
    return render_template("index.html", page_data=summary_page_data)


@views.route("/daily")
def day_detail():
    """
    Render the daily date page for a given day passed as a query arg.

    For example: http://127.0.0.1:8000/daily?day=1
    """
    # Check if housekeeping is required
    local_tz = datetime.now().astimezone().tzinfo
    helper.housekeeping()

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    args = request.args

    # Validate the access key if provided
    if config.get("Website", "AccessKey") is not None:
        access_key = args.get("key", default=None, type=str)
        if access_key != config.get("Website", "AccessKey"):
            logger.log_message(f"Day Detail: Invalid access key {access_key} used.", "warning")
            return "Access forbidden.", 403

    # Set the state index based on the query parameter
    day = args.get("day", default=None, type=int)
    if day is None or day < 0 or day > 7:
        logger.log_message(f"Day Detail: Invalid day parameter {day} passed.", "warning")
        return "Invalid day parameter. Must be between 0 and 7.", 400

    state_idx = helper.get_selected_state()
    if state_idx is None:
        # Render the template with the summary data
        return render_template("no_state.html")
    # Build the dict object that we will use to pass the information to the web page
    day_data = helper[state_idx]["DailyData"][day]
    page_date = datetime.strptime(day_data["Date"], "%Y-%m-%d").astimezone(local_tz)
    actual_runtime = helper.hours_to_string(day_data["RuntimeToday"]) + " hours run"
    if day_data["RemainingRuntimeToday"] > 0:
        actual_runtime += ", " + helper.hours_to_string(day_data["RemainingRuntimeToday"]) + " hours remaining"

    energy_usage = f"{day_data['EnergyUsed'] / 1000:.2f} kWh"
    average_price = day_data["AveragePrice"] if day_data["AveragePrice"] is not None else 0
    if average_price > 0:
        energy_usage += f" at {average_price:.1f} c/kWh"
    if (day_data["TotalCost"] or 0) > 0:
        energy_usage += f" = ${day_data['TotalCost'] / 100:.2f}"

    logger.log_message(f"Daily: rendering device {helper[state_idx]['DeviceName']} and day {page_date.strftime('%d/%m/%Y')} for client {client_ip}. State timestamp: {helper[state_idx]['LastStateSaveTime']}", "all")

    daily_data = {
        "AccessKey": config.get("Website", "AccessKey"),
        "RefreshDelay": config.get("Website", "PageAutoRefresh") or 0,
        "DeviceName": helper[state_idx]["DeviceName"],
        "Date": page_date.strftime("%d/%m/%Y"),
        "DateLong": helper.format_date_with_ordinal(page_date),
        "Shortfall": helper.hours_to_string(day_data["PriorShortfall"]),
        "TargetRuntime": helper.hours_to_string(day_data["TargetRuntime"]),
        "ActualRuntime": actual_runtime,
        "EnergyUsed": energy_usage,
        "HaveRunPlan": len(day_data["DeviceRuns"]) > 0,
        "CurrentDay": day,
        "PreviousDay": day + 1 if day < 7 else None,
        "NextDay": day - 1 if day > 0 else None,
        }

    # Build the device_runs array
    device_runs = []
    for run in day_data["DeviceRuns"]:
        start_time = datetime.strptime(run["StartTime"], "%Y-%m-%d %H:%M:%S").astimezone(local_tz).strftime("%H:%M")
        if run["EndTime"] is None:
            end_time = "Running"
        else:
            end_time = datetime.strptime(run["EndTime"], "%Y-%m-%d %H:%M:%S").astimezone(local_tz).strftime("%H:%M")

        price = "Unknown" if run["Price"] is None else f"{round(run['Price'], 1)} c/kWh"
        device_runs.append({
            "Start": start_time,
            "End": end_time,
            "Price": price,
        })

    # Add the device_runs array to the daily_data dict
    daily_data["DeviceRuns"] = device_runs

    return render_template("daily.html", page_data=daily_data)


@views.route("/api/submit", methods=["POST"])
def submit_data():
    """Accept a JSON object via POST and validate it."""
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

    data = request.get_json()

    # Perform general checks on the JSON object
    if not isinstance(data, dict):
        logger.log_message("Submit Data: Invalid JSON format. Expected a JSON object", "warning")
        return jsonify({"error": "Invalid JSON format. Expected a JSON object."}), 400

    # Check for some required required keys and their types
    required_keys = {
        "LastStateSaveTime": str,
        "ForecastRuntimeToday": (int, float),
        "CurrentPrice": (int, float, None),
        "EnergyUsed": (int, float, None),
        "TodayRunPlan": (list, None),
        "DailyData": list,
    }

    for key, expected_type in required_keys.items():
        if key not in data:
            logger.log_message(f"Submit Data: Missing required key: {key}", "warning")
            return jsonify({"error": f"Missing required key: {key}"}), 400
        if not isinstance(data[key], expected_type):
            logger.log_message(f"Submit Data: Invalid type for key: {key}. Expected {expected_type.__name__}.", "warning")
            return jsonify({"error": f"Invalid type for key: {key}. Expected {expected_type.__name__}."}), 400

    # Process the valid data (example: log it or save it)
    logger.log_message(f"Received valid state data for device: {data['DeviceName']}", "debug")

    # Save the state file
    helper.save_state(data)

    # Check if housekeeping is required
    helper.housekeeping()

    # Display a success message
    return jsonify({"message": "Data received and validated successfully."}), 200
