"""
# views.py
# This module contains the Flask views for the application."""
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify


views = Blueprint(__name__, 'views')

# Global for the utlity function object reference
utils = None

def register_utility_func(uf_ref):
    """Register the PowerControllerState instance."""
    global utils
    utils = uf_ref

@views.route('/home')
def home():
    """Render the home page which shows the summary."""

    # Check if housekeeping is required
    utils.housekeeping()

    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    args = request.args

    # Validate the access key if provided
    if utils.config['Website']['AccessKey'] is not None:
        access_key = args.get('key', default=None, type=str)
        if access_key != utils.config['Website']['AccessKey']:
            utils.log_message(f"Home: Invalid access key {access_key} used.", "warning")
            return "Access forbidden.", 403

    # Set the state index based on the query parameter
    new_state_idx = args.get('state_idx', default=None, type=int)
    state_idx = utils.get_selected_state(new_state_idx)
    if state_idx is None or len(utils.state_items) < 2:
        state_next_idx = None
    elif state_idx >= len(utils.state_items) - 1:
        state_next_idx = 0
    else:
        state_next_idx = state_idx + 1


    # Deal with empty state_items array
    if state_idx is None:
        # Render the template with the summary data
        utils.log_message("Home: No states available.", "debug")
        return render_template('no_state.html')
    else:
        last_save_time = datetime.strptime(utils[state_idx]['LastStateSaveTime'], "%Y-%m-%d %H:%M:%S")

        pump_start_time = None
        if utils[state_idx]['IsDeviceRunning']:
            pump_start_time = datetime.strptime(utils[state_idx]['DeviceLastStartTime'], "%Y-%m-%d %H:%M:%S")
        average_daily_usage = ((utils[state_idx]['EnergyUsed'] - utils[state_idx]['DailyData'][0]['EnergyUsed']) / 7) / 1000

        debug_message = None
        if utils.config['Website']['DebugMode'] and utils.config['Files']['LogFileVerbosity'] == "all":
            debug_message = f"Number of states: {len(utils.state_items)} <br>"
            debug_message += f"Logging level: {utils.config['Files']['LogFileVerbosity']} <br>"

        utils.log_message(f"Home: Process {utils.process_id} rendering device {utils[state_idx]['DeviceName']} for client {client_ip}. State timestamp: {utils[state_idx]['LastStateSaveTime']}", "all")

        # Build a dict object that we will use to pass the information to the web page
        summary_page_data = {
            'AccessKey': utils.config['Website']['AccessKey'],
            'RefreshDelay': utils.config['Website']['PageAutoRefresh'] or 0,
            'NextIndex': state_next_idx,
            'NextDeviceName': utils[state_next_idx]['DeviceName'] if state_next_idx is not None else None,
            'TimeNow': datetime.now().strftime("%H:%M:%S"),
            'DeviceName': utils[state_idx]['DeviceName'],
            'StatusMessage': utils[state_idx]['LastStatusMessage'] or "Unknown",
            'LastCheck': utils.format_date_with_ordinal(last_save_time, True),
            'PumpStatus': "Not running" if not utils[state_idx]['IsDeviceRunning'] else "Started at " + pump_start_time.strftime("%H:%M:%S"),
            'RemaningRuntime': utils.hours_to_string(utils[state_idx]['DailyData'][0]['RemainingRuntimeToday']),
            'AverageDailyRuntime': utils.hours_to_string(utils[state_idx]['AverageRuntimePriorDays']),
            'CurrentPrice': round(utils[state_idx]['CurrentPrice'], 1),
            'AverageEnergyPrice': round(utils[state_idx]['AveragePrice'], 1),
            'AverageDailyUsage': round(average_daily_usage, 2),
            'AverageDailyCost': f"${average_daily_usage * utils[state_idx]['AveragePrice'] / 100:.2f}",
            'HaveRunPlan': len(utils[state_idx]['TodayRunPlan']) > 0,
            'RunPlan': utils[state_idx]['TodayRunPlan'],
            'ForecastPrice': round(utils[state_idx]['AverageForecastPrice'], 1),
            'DebugMessage': debug_message,
        }

    # Render the template with the summary data
    return render_template('index.html', page_data=summary_page_data)


@views.route('/daily')
def day_detail():
    """Render the daily date page for a given day passed as a query arg, for example: 
    http://127.0.0.1:8000/daily?day=1
    """

    # Check if housekeeping is required
    utils.housekeeping()

    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    args = request.args

    # Validate the access key if provided
    if utils.config['Website']['AccessKey'] is not None:
        access_key = args.get('key', default=None, type=str)
        if access_key != utils.config['Website']['AccessKey']:
            utils.log_message(f"Day Detail: Invalid access key {access_key} used.", "warning")
            return "Access forbidden.", 403

    # Set the state index based on the query parameter
    day = args.get('day', default=None, type=int)
    if day is None or day < 0 or day > 7:
        utils.log_message(f"Day Detail: Invalid day parameter {day} passed.", "warning")
        return "Invalid day parameter. Must be between 0 and 7.", 400

    state_idx = utils.get_selected_state()
    if state_idx is None:
        # Render the template with the summary data
        return render_template('no_state.html')
    else:
        # Build the dict object that we will use to pass the information to the web page
        day_data = utils[state_idx]['DailyData'][day]
        page_date = datetime.strptime(day_data['Date'], "%Y-%m-%d")
        actual_runtime = utils.hours_to_string(day_data['RuntimeToday']) + " hours run"
        if day_data['RemainingRuntimeToday'] > 0:
            actual_runtime += ", " + utils.hours_to_string(day_data['RemainingRuntimeToday']) + " hours remaining"

        energy_usage = f"{day_data['EnergyUsed'] / 1000:.2f} kWh"
        if (day_data['AveragePrice'] or 0) > 0:
            energy_usage += f" at {day_data['AveragePrice']:.1f} c/kWh"
        if (day_data['TotalCost'] or 0) > 0:
            energy_usage += f" = ${day_data['TotalCost'] / 100:.2f}"

        utils.log_message(f"Daily: rendering device {utils[state_idx]['DeviceName']} and day {page_date.strftime('%d/%m/%Y')} for client {client_ip}. State timestamp: {utils[state_idx]['LastStateSaveTime']}", "all")

        daily_data = {
            'AccessKey': utils.config['Website']['AccessKey'],
            'RefreshDelay': utils.config['Website']['PageAutoRefresh'] or 0,
            'DeviceName': utils[state_idx]['DeviceName'],
            'Date': page_date.strftime("%d/%m/%Y"),
            'DateLong': utils.format_date_with_ordinal(page_date),
            'Shortfall': utils.hours_to_string(day_data['PriorShortfall']),
            'TargetRuntime': utils.hours_to_string(day_data['TargetRuntime']),
            'ActualRuntime': actual_runtime,
            'EnergyUsed': energy_usage,
            'HaveRunPlan': len(day_data['DeviceRuns']) > 0,
            'CurrentDay': day,
            'PreviousDay': day + 1 if day < 7 else None,
            'NextDay': day - 1 if day > 0 else None
            }

        # Build the device_runs array
        device_runs = []
        for run in day_data['DeviceRuns']:
            start_time = datetime.strptime(run['StartTime'], "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
            if run['EndTime'] is None:
                end_time = "Running"
            else:
                end_time = datetime.strptime(run['EndTime'], "%Y-%m-%d %H:%M:%S").strftime("%H:%M")

            if run['Price'] is None:
                price = "Unknown"
            else:
                price = f"{round(run['Price'], 1)} c/kWh"
            device_runs.append({
                'Start': start_time,
                'End': end_time,
                'Price': price
            })

        # Add the device_runs array to the daily_data dict
        daily_data['DeviceRuns'] = device_runs

        return render_template('daily.html', page_data=daily_data)


@views.route('/api/submit', methods=['POST'])
def submit_data():
    """Accept a JSON object via POST and validate it."""
    if not request.is_json:
        utils.log_message("Submit Data: Content posted is not JSON data", "warning")
        return jsonify({"error": "Invalid content type. Expected JSON."}), 400

    args = request.args

    # Validate the access key if provided
    if utils.config['Website']['AccessKey'] is not None:
        access_key = args.get('key', default=None, type=str)
        if access_key != utils.config['Website']['AccessKey']:
            utils.log_message(f"Submit Data: Invalid access key {access_key} used.", "warning")
            return jsonify({"error": "Access forbidden."}), 403

    data = request.get_json()

    # Perform general checks on the JSON object
    if not isinstance(data, dict):
        utils.log_message("Submit Data: Invalid JSON format. Expected a JSON object", "warning")
        return jsonify({"error": "Invalid JSON format. Expected a JSON object."}), 400

    # Check for some required required keys and their types
    required_keys = {
        "LastStateSaveTime": str,
        "ForecastRuntimeToday": (int, float),
        "CurrentPrice": (int, float, None),
        "EnergyUsed": (int, float, None),
        "TodayRunPlan": (list, None),
        "DailyData": list
    }

    for key, expected_type in required_keys.items():
        if key not in data:
            utils.log_message(f"Submit Data: Missing required key: {key}", "warning")
            return jsonify({"error": f"Missing required key: {key}"}), 400
        if not isinstance(data[key], expected_type):
            utils.log_message(f"Submit Data: Invalid type for key: {key}. Expected {expected_type.__name__}.", "warning")
            return jsonify({"error": f"Invalid type for key: {key}. Expected {expected_type.__name__}."}), 400

    # Process the valid data (example: log it or save it)
    utils.log_message(f"Received valid state data for device: {data['DeviceName']}", "debug")

    # Save the state file
    utils.save_state(data)

    # Check if housekeeping is required
    utils.housekeeping()

    # Display a success message
    return jsonify({"message": "Data received and validated successfully."}), 200
