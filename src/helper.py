"""General helper functions for the project."""

import datetime as dt
import fcntl
import inspect
import json
import os
import threading
import time
import traceback
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

import matplotlib as mpl
from sc_utility import DateHelper, JSONEncoder, SCCommon, SCConfigManager, SCLogger
from werkzeug.datastructures import MultiDict

mpl.use("Agg")  # Use non-interactive backend for server environments
from collections import defaultdict

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

TEMP_PROBE_CHART_LOCATION = "static/temp_probes_chart.jpg"


# Metered output usage =======================================
@dataclass
class UsageReportingPeriod:
    """Define a reporting period for metered output usage."""
    name: str
    start_date: dt.date
    end_date: dt.date
    is_custom: bool = False
    show: bool = False
    menu: bool = not show
    have_global_data: bool = False
    global_energy_used: float = 0.0
    global_cost: float = 0.0
    output_energy_used: float = 0.0
    output_cost: float = 0.0
    other_energy_used: float = 0.0
    other_cost: float = 0.0


class PowerControllerViewer:
    """General purpose helper functions for the PowerControllerViewer."""

    # Class-level state cache and locks (per-process)
    _state_cache = None
    _state_cache_timestamp = None
    _state_latest_save_timestamp = None
    _state_lock = threading.RLock()
    _chart_generation_lock = threading.Lock()
    _worker_thread = None
    _worker_stop_event = threading.Event()
    _reload_lock_file = None
    _cache_metadata_file = None  # Tracks which process last loaded and when

    def __init__(self, config: SCConfigManager, logger: SCLogger):
        self.config = config
        self.logger = logger
        self.last_housekeeping = None
        self.last_state_check = None
        self.last_state_filename_hash = None
        state_file_path = SCCommon.select_file_location("state_data/test.json")
        self.state_data_dir = state_file_path.parent  # pyright: ignore[reportOptionalMemberAccess]

        self.state_items = []   # List of state items loaded from JSON files
        self.config_last_check = DateHelper.now()

        # Initialize file-based lock and cache metadata for cross-process coordination
        if PowerControllerViewer._reload_lock_file is None:
            lock_path = SCCommon.select_file_location("state_data/.reload.lock")
            PowerControllerViewer._reload_lock_file = lock_path

        if PowerControllerViewer._cache_metadata_file is None:
            cache_meta_path = SCCommon.select_file_location("state_data/.cache_metadata.json")
            PowerControllerViewer._cache_metadata_file = cache_meta_path

        # Start worker thread if not already running (only one per process)
        if PowerControllerViewer._worker_thread is None or not PowerControllerViewer._worker_thread.is_alive():
            PowerControllerViewer._worker_stop_event.clear()
            PowerControllerViewer._worker_thread = threading.Thread(
                target=self._state_loader_worker,
                daemon=True,
                name="StateLoaderWorker"
            )
            PowerControllerViewer._worker_thread.start()
            self.logger.log_message(f"Started state loader worker thread (PID: {os.getpid()})", "debug")

        # Perform initial housekeeping which will include loading the state files
        self.housekeeping()

    @classmethod
    def shutdown_worker(cls):
        """Stop the worker thread gracefully."""
        if cls._worker_thread and cls._worker_thread.is_alive():
            cls._worker_stop_event.set()
            cls._worker_thread.join(timeout=5)

    def load_state_files(self):
        """Load the available state from the JSON files (thread-safe)."""
        # Check if we have a recent in-process cached copy
        with PowerControllerViewer._state_lock:
            if PowerControllerViewer._state_cache is not None:
                self.state_items = PowerControllerViewer._state_cache.copy()
                self.logger.log_message(
                    f"Using in-process cached state data ({len(self.state_items)} items)",
                    "debug"
                )
                return

        # Check if another process recently loaded
        cache_meta = self._get_cache_metadata()
        if cache_meta:
            last_load_time = cache_meta.get("last_load_time", 0)
            last_load_pid = cache_meta.get("last_load_pid")
            time_since_load = time.time() - last_load_time

            if time_since_load < 15 and last_load_pid != os.getpid():
                self.logger.log_message(
                    f"Initial load (PID {os.getpid()}) waiting - process {last_load_pid} "
                    f"loaded {time_since_load:.1f}s ago, waiting for worker...",
                    "debug"
                )
                # Wait for worker thread to populate cache
                for _ in range(10):  # Wait up to 5 seconds
                    time.sleep(0.5)
                    with PowerControllerViewer._state_lock:
                        if PowerControllerViewer._state_cache is not None:
                            self.state_items = PowerControllerViewer._state_cache.copy()
                            self.logger.log_message(
                                f"Using worker-populated cache ({len(self.state_items)} items)",
                                "debug"
                            )
                            return

        # No cache available or too old, try to load
        lock_acquired = False
        lock_file = None
        try:
            assert isinstance(PowerControllerViewer._reload_lock_file, Path)
            lock_file = PowerControllerViewer._reload_lock_file.open("w")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_acquired = True

            self.logger.log_message(f"Initial load (PID {os.getpid()}) acquired lock", "debug")
            self._load_state_files_internal()
            self._update_cache_metadata(time.time())

        except BlockingIOError:
            self.logger.log_message(
                f"Initial load (PID {os.getpid()}) waiting for other process...",
                "debug"
            )
            time.sleep(3)
            with PowerControllerViewer._state_lock:
                if PowerControllerViewer._state_cache is not None:
                    self.state_items = PowerControllerViewer._state_cache.copy()

        finally:
            if lock_acquired and lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()

    def validate_state_index(self, url_args: MultiDict[str, str] | None = None, requested_state_idx: int | None = None) -> tuple[int | None, int | None]:
        """Validate that a requested state index is within the valid range.

        Args:
            url_args (MultDict[str, str], optional): The URL arguments to extract the state index from. If provided, it takes precedence over requested_state_idx.
            requested_state_idx (int, optional): The requested state index to use. If None, the first available state will be returned.

        Returns:
            result(int, int): The actual state index to use and the next_state_idx to use. Returns None if there are no state items.
        """
        # Set the new state index if provided
        max_state_idx = len(self.state_items) - 1
        if len(self.state_items) == 0:
            return None, None

        if url_args is not None:
            requested_state_idx = url_args.get("state_idx", default=None, type=int)
            if requested_state_idx is None:
                # If a state_idx wasn't provided in the URL, see if they passed a state_name
                requested_state_name = url_args.get("state_name", default=None, type=str)
                if requested_state_name is not None:
                    # Find the state index with the matching device name
                    for idx, state_item in enumerate(self.state_items):
                        device_name = state_item.get("StateURLName")
                        if device_name == requested_state_name:
                            requested_state_idx = idx
                            break

                    if requested_state_idx is None:
                        self.logger.log_message(f"State name '{requested_state_name}' not found, defaulting to first state.", "error")
                        requested_state_idx = 0

        # Set the actual state index based on the requested one
        if requested_state_idx is None:
            new_state_idx = 0
        elif not isinstance(requested_state_idx, int):
            self.logger.log_message(f"Invalid state index of type {type(requested_state_idx)} passed in url args, expected int.", "error")
            new_state_idx = 0
        elif requested_state_idx < 0:
            new_state_idx = max_state_idx
        elif requested_state_idx > max_state_idx:
            new_state_idx = 0
        else:
            new_state_idx = requested_state_idx

        # Now figure out the next state index. If we have 0 or 1 state items, there is no next state.
        if max_state_idx < 1:
            next_state_idx = None
        else:
            next_state_idx = new_state_idx + 1 if new_state_idx < max_state_idx else 0

        return new_state_idx, next_state_idx

    def validate_day_index(self, requested_state_idx: int | None = None, requested_day_idx: int | None = None) -> tuple[int | None, int | None, int | None]:
        """Validate that a requested state index and day is within the valid range for a state entry.

        Args:
            requested_state_idx (int, optional): The requested state index to use. If None, the first valid state will be returned.
            requested_day_idx (int, optional): The requested day index to use. If None, the first valid day will be returned.

        Returns:
            result(int, int, int): The actual state index; the day index to use; the maximum day index. Returns None if there are no state items of the required type.
        """
        # Validate that the requested state index is OK
        state_idx, _ = self.validate_state_index(requested_state_idx=requested_state_idx)

        # If there are no valid state items, return None
        if state_idx is None:
            return None, None, None

        # Validate state type and count how many day entries we have
        state_type = self.get_state(state_idx, "StateFileType", default="PowerController")
        if state_type == "PowerController":
            max_day_idx = len(self.get_state(state_idx, "Output", "RunHistory", "DailyData", default=[])) - 1
        elif state_type == "LightingControl":
            max_day_idx = len(self.get_state(state_idx, "SwitchEvents", default=[])) - 1
        else:
            self.logger.log_message(f"Unknown state type {state_type} for state index {state_idx}.", "error")
            return None, None, None

        # If there are no valid day entries, return None
        if max_day_idx < 0:
            return state_idx, None, None

        # Validate the requested day index
        if requested_day_idx is None:
            day_idx = 0
        elif not isinstance(requested_day_idx, int):
            self.logger.log_message(f"Invalid day index of type {type(requested_day_idx)} passed in url args, expected int.", "error")
            day_idx = 0
        elif requested_day_idx < 0:
            day_idx = max_day_idx
        elif requested_day_idx > max_day_idx:
            day_idx = 0
        else:
            day_idx = requested_day_idx

        return state_idx, day_idx, max_day_idx

    def validate_metering_args(self, requested_state_idx: int, url_args: MultiDict[str, str] | None) -> tuple[int | None, dt.date | None, dt.date | None]:
        """Validate and extract custom start and end dates from URL arguments.

        Args:
            requested_state_idx (int): The index of the state item to validate against.
            url_args (MultiDict[str, str] | None): The URL arguments to extract the dates from.

        Returns:
            tuple(int | None, dt.date | None, dt.date | None): The validated period index, start and end dates, or None if not provided or invalid.
        """
        if url_args is None:
            return None, None, None

        state_data = self.state_items[requested_state_idx]
        if not isinstance(state_data, dict) or state_data.get("StateFileType") != "OutputMetering":
            return None, None, None

        # Get the data range from the state data
        earliest_date = state_data.get("Summary", {}).get("FirstDate")
        latest_date = state_data.get("Summary", {}).get("LastDate")

        period_idx = url_args.get("period_idx", default=None, type=int)
        custom_start_date_str = url_args.get("start_date", default=None, type=str)
        custom_end_date_str = url_args.get("end_date", default=None, type=str)

        # If we have a date range passed, we don't care about the period_idx
        if custom_start_date_str and custom_end_date_str:
            try:
                custom_start_date = dt.datetime.strptime(custom_start_date_str, "%Y-%m-%d").date()  # noqa: DTZ007
                custom_end_date = dt.datetime.strptime(custom_end_date_str, "%Y-%m-%d").date()  # noqa: DTZ007
            except ValueError:
                pass
            else:
                if earliest_date and latest_date and \
                    earliest_date <= custom_start_date <= latest_date and \
                    earliest_date <= custom_end_date <= latest_date and \
                    custom_start_date <= custom_end_date:
                    return -1, custom_start_date, custom_end_date

        # Otherwise try to get the period from the period_idx
        reporting_periods = self._get_meter_reporting_periods(requested_state_idx)
        if period_idx is not None and 0 <= period_idx < len(reporting_periods):
            return period_idx, None, None

        return None, None, None

    def save_state(self, state_item: dict):
        """Save the current state to the JSON file. This assumes that the calling function has already validates the state file.

        Args:
            state_item (dict): The state item to save. It should contain the "DeviceName" key to determine the filename.

        Returns:
            bool: True if the state was saved successfully, False if the file was empty or unreadable.
        """
        try:
            # TO DO: Decode the datatype and enum hints using json_encoder
            file_path = Path(self.state_data_dir) / f"{state_item['DeviceName']}.json"
            self._safe_write_json(file_path, state_item)  # pyright: ignore[reportArgumentType]
            self.logger.log_message(f"Successfully saved state to {self.state_data_dir}.", "debug")
        except (RuntimeError, OSError) as e:
            self.report_fatal_error(f"Error writing to {self.state_data_dir}: {e}")
        else:
            return True

    def check_for_state_file_changes(self) -> bool:
        """Check if the state files have changed since the last check.

        Returns:
            result(bool): True if any state file has been modified since the last check, False otherwise.
        """
        # Get the last modified time of the state files
        # Look in the state_data subdirectory for the all the available state files
        filename_concat = ""
        return_value = False
        if self.state_data_dir.exists() and self.state_data_dir.is_dir():
            json_files = [f for f in self.state_data_dir.iterdir() if f.is_file() and f.name.endswith(".json")]

            for file_path in json_files:
                # This will be the concatentnation of all the state file names - used to check if files have been added or removed
                filename_concat += file_path.name

                if file_path.name.startswith("."):
                    # Skip hidden files
                    continue

                file_modified = file_path.stat().st_mtime
                if not self.last_state_check or file_modified > self.last_state_check:
                    # We have a more recent state file
                    return_value = True
                    self.last_state_check = file_modified

            if self.last_state_filename_hash != filename_concat:
                self.logger.log_message(f"State files have changed. Reloading state files from {self.state_data_dir}.", "debug")
                self.last_state_filename_hash = filename_concat
                return_value = True

        return return_value

    def housekeeping(self) -> bool:
        """General housekeeping function to be called periodically.

           Will run every hour. Initialise the monitoring log file. If it exists, truncate it to the max number of lines.

        Returns:
            result(bool): True if changes were made, False otherwise.
        """
        return_value = False
        # Check if the configuration file has changed. Reload if it has. Throws a RuntimeError if the config file is invalid.
        try:
            config_timestamp = self.config.check_for_config_changes(self.config_last_check)
            if config_timestamp:
                self.reload_config()
                return_value = True
        except RuntimeError as e:
            self.report_fatal_error(f"Error checking for config changes: {e}")

        # Check if the state files have changed. Reload if they have.
        if self.check_for_state_file_changes():
            self.logger.log_message("Reloading state files for new changes.", "debug")
            self.load_state_files()
            return_value = True

        # Check if the last housekeeping was more than 1 hour ago
        if self.last_housekeeping is not None:
            now = DateHelper.now()
            if (now - self.last_housekeeping).total_seconds() < 3600:
                return return_value

        return_value = True

        # Truncate the log file if it exists
        self.logger.trim_logfile()

        # Set the last housekeeping time to now
        self.last_housekeeping = DateHelper.now()
        return return_value

    def reload_config(self):
        """Apply the updated configuration settings."""
        self.logger.log_message("Reloading configuration...", "detailed")

        try:
            # First update the logger
            logger_settings = self.config.get_logger_settings()
            self.logger.initialise_settings(logger_settings)

            # Then email settings
            email_settings = self.config.get_email_settings()
            if email_settings:
                self.logger.register_email_settings(email_settings)

        except RuntimeError as e:
            self.logger.log_fatal_error(f"Error reloading and applying configuration changes: {e}")
            return
        else:
            self.config_last_check = DateHelper.now()

    @staticmethod
    def hours_to_string(hours: float | None) -> str:
        """Convert hours to a string in the format HH:MM.

        Args:
            hours (float | None): The number of hours to convert. If None, returns "00:00".

        Returns:
            hours_str(str): The formatted string representing the hours in HH:MM format.
        """
        if hours is None:
            return "00:00"
        neg_symbol = "-" if hours < 0 else ""
        hours_part = int(abs(hours))
        minutes = int((abs(hours) - hours_part) * 60)
        return f"{neg_symbol}{hours_part}:{minutes:02}"

    @staticmethod
    def format_date_with_ordinal(date: dt.date, show_time: bool | None = False):
        """Format a date with an ordinal suffix for the day - for example 14th April.

        Args:
            date (dt.date): The date to format.
            show_time (bool, optional): If True, include the time in the format. Defaults to False.

        Returns:
            return_str (str): The formatted date string with the ordinal suffix.
        """
        time_str = date.strftime(" %H:%M:%S")

        day = date.day
        # Determine the ordinal suffix
        if 11 <= day <= 13:  # Special case for 11th, 12th, 13th
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

        # Format the date with the ordinal suffix
        return_str = date.strftime(f"%-d{suffix} %B")
        if show_time:
            return_str += time_str
        return return_str

    def report_fatal_error(self, message, report_stack=False, calling_function=None) -> str:
        """Report a fatal error and exit the program.

        Args:
            message (str): The error message to report.
            report_stack (bool, optional): If True, include the stack trace in the error message. Defaults to False.
            calling_function (str, optional): The name of the calling function. If None, it will be determined automatically.

        Returns:
            return_str (str): The formatted error message including the function name and stack trace if requested
        """
        function_name = None
        if calling_function is None:
            stack = inspect.stack()
            # Get the frame of the calling function
            calling_frame = stack[1]
            # Get the function name
            function_name = calling_frame.function
            if function_name == "<module>":
                function_name = "main"
            # Get the class name (if it exists)
            class_name = None
            if "self" in calling_frame.frame.f_locals:
                class_name = calling_frame.frame.f_locals["self"].__class__.__name__
                full_reference = f"{class_name}.{function_name}()"
            else:
                full_reference = function_name + "()"
        else:
            full_reference = calling_function + "()"

        stack_trace = traceback.format_exc()
        if report_stack:
            message += f"\n\nStack trace:\n{stack_trace}"

        return_str = f"Function {full_reference}: FATAL ERROR: {message}"
        self.logger.log_message(return_str, "error")
        return return_str

    @staticmethod
    def generate_html_page(text: str) -> str:
        """Generate a complete HTML page with the given text properly formatted. Newlines in the text will be replaced with <br> tags.

        Args:
            text (str): The text to include in the HTML page.

        Returns:
            html_page (str): The complete HTML page as a string.
        """
        # Escape special HTML characters and replace newlines with <br>
        formatted_text = escape(text).replace("\n", "<br>")

        # Build the HTML page
        html_page = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Formatted Text</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 20px;
                    line-height: 1.6;
                }}
                pre {{
                    background-color: #f4f4f4;
                    padding: 10px;
                    border-radius: 5px;
                    overflow-x: auto;
                }}
            </style>
        </head>
        <body>
            <pre>{formatted_text}</pre>
        </body>
        </html>
        """
        return html_page

    def get_state(self, *keys, default=None) -> Any:
        """Retrieve a value from the state dictionary using a sequence of nested keys.

        If the key path does not exist, it returns the default value.
        If the key path does exists, but the key value is None and the default provided is not None, it returns the default value.

        Example:
            value = get_state(state_idx, 'AveragePrice', default=0)

        Args:
            keys: Sequence of keys to traverse the config dictionary.
            default: Value to return if the key path does not exist.

        Returns:
            The value if found, otherwise the default.

        """
        value = self.state_items
        try:
            for key in keys:
                value = value[key]
        except (KeyError, TypeError):
            return default
        else:
            if value is None and default is not None:
                return default
            return value

    @staticmethod
    def get_last_state_reload() -> dt.datetime | None:
        """Get the timestamp of the last state reload.

        Returns:
            dt.datetime: The timestamp of the last state reload, or None if never reloaded.
        """
        return PowerControllerViewer._state_cache_timestamp

    @staticmethod
    def get_latest_state_modification_time() -> dt.datetime | None:
        """Get the latest modification time of any state file in the state data directory.

        Returns:
            dt.datetime: The latest modification time, or None if no state files exist.
        """
        return PowerControllerViewer._state_latest_save_timestamp

    @staticmethod
    def url_encode_device_name(device_name: str) -> str:
        r"""URL-encode a device name for safe inclusion in URLs.

        First removed the following characters: space  /  \  - and encodes the rest.

        Args:
            device_name (str): The device name to encode.

        Returns:
            str: The URL-encoded device name.
        """
        # Strip unwanted characters and URL-encode the rest
        stripped_name = device_name.replace(" ", "").replace("/", "").replace("\\", "").replace("-", "")
        return quote(stripped_name)

    def build_metering_reporting_data(self, state_idx: int, period_idx: int | None = None, custom_start_date: dt.date | None = None, custom_end_date: dt.date | None = None) -> dict:  # noqa: PLR0912
        """Builds the dict containing the data for OutputMetering.

        The data includes a list of UsageReportingPeriods obejcts for the OutputMetering state type,
        populated with the state data.

        This will be cached in the self.output_metering_data dict for the given state index.

        Args:
            state_idx (int): The index of the state item to build the reporting data for.
            period_idx (int | None, optional): If provided, use this as the index of the additional reporting period to use.
            custom_start_date (dt.date | None, optional): If provided, use this as the start date for a custom reporting period.
            custom_end_date (dt.date | None, optional): If provided, use this as the end date for a custom reporting period.

        Returns:
            dict: the reporting data structure for the OutputMetering state type, or an empty dict on error.
        """
        # TO DO: caching of the output metering data per state index
        # Validate that the state index keys to a OutputMetering state type
        state_data = self.get_state(state_idx)
        if state_data.get("StateFileType") != "OutputMetering":
            self.logger.log_message(f"State index {state_idx} is not of type OutputMetering, cannot build metering reporting data.", "error")
            return {}

        # If a custom date range is provided, make sure both the start and end dates are provided
        if (custom_start_date is not None and custom_end_date is None) or (custom_start_date is None and custom_end_date is not None):
            self.logger.log_message("Both custom_start_date and custom_end_date must be provided for a custom reporting period.", "error")
            return {}

        state_summary = state_data.get("Summary", {})
        meter_data = state_data.get("Meters", [])

        if not state_summary or not meter_data:
            self.logger.log_message(f"State index {state_idx} is missing Summary or Meters data, cannot build metering reporting data.", "error")
            return {}

        reporting_data = {
            "FirstDate": state_summary.get("FirstDate"),
            "LastDate": state_summary.get("LastDate"),
            "NumberOfMeters": len(meter_data),
            "ReportingPeriods": [],     # The reporting periods
            "NumberOfPeriods": 0,
            "NumberOfVisiblePeriods": 0,
            "Totals": [],                # The total usage by period
            "Meters": [],                # The usage data per meters and period
        }

        # Add the reporting periods to the reporting data
        reporting_periods = self._get_meter_reporting_periods(state_idx, period_idx, custom_start_date=custom_start_date, custom_end_date=custom_end_date)
        reporting_data["ReportingPeriods"] = reporting_periods
        reporting_data["NumberOfPeriods"] = len(reporting_periods)
        reporting_data["NumberOfVisiblePeriods"] = sum(1 for period in reporting_periods if period.show)
        # Get the global totals for each reporting period and save to the period object
        for period in reporting_periods:
            self._get_global_usage_totals(state_idx, period)

        # Now build the usage data for each meter and reporting period
        # Now get the totals for each output and reporting period from the csv_data
        for meter in meter_data:
            display_name = meter.get("DisplayName") or meter.get("Output")
            meter_entry = {
                "Name": display_name,
                "Usage": []
            }

            # Loop through each reporting period and get the totals for this output
            for period in reporting_periods:
                # Skip if not show
                if not period.show:
                    continue
                # Setup the default object for this output and period
                usage_entry = {
                    "Period": period.name,
                    "StartDate": period.start_date,
                    "EndDate": period.end_date,
                    "HaveData": False,
                    "EnergyUsed": 0.0,
                    "EnergyUsedPcnt": None,
                    "Cost": 0.0,
                    "CostPcnt": None,
                }

                # Make sure this meter has data on or before the start of this period
                if meter.get("FirstDate") > period.start_date:
                    meter_entry["Usage"].append(usage_entry)
                    continue  # Skip this period for this output
                usage_entry["HaveData"] = True

                # Now calculate the totals for this output and period from the CSV data
                for item in meter.get("Usage", []):
                    if period.start_date <= item["Date"] <= period.end_date:
                        usage_entry["HaveData"] = True
                        usage_entry["EnergyUsed"] += item.get("EnergyUsed", 0.0)
                        usage_entry["Cost"] += item.get("Cost", 0.0)

                # Add usage for this output to the global output totals for this period
                period.output_energy_used += usage_entry["EnergyUsed"]
                period.output_cost += usage_entry["Cost"]

                # Now calculate the percentages if we have global usage data
                if period.have_global_data:
                    if period.global_energy_used > 0:
                        usage_entry["EnergyUsedPcnt"] = usage_entry["EnergyUsed"] / period.global_energy_used
                    if period.global_cost > 0:
                        usage_entry["CostPcnt"] = usage_entry["Cost"] / period.global_cost

                meter_entry["Usage"].append(usage_entry)

            # And finally append this usage to the system state
            reporting_data["Meters"].append(meter_entry)

        # Finally write out the totals section
        for period in reporting_periods:
            # Skip if not show
            if not period.show:
                continue
            period.other_energy_used = period.global_energy_used - period.output_energy_used
            period.other_cost = period.global_cost - period.output_cost

            if period.is_custom:
                period_and_date = f"Custom: {period.start_date.strftime('%d %b')} to {period.end_date.strftime('%d %b')}"  # type: ignore[attr-defined]
            else:
                period_and_date = period.name + f" (from {period.start_date.strftime('%d %b')})"  # type: ignore[attr-defined]
            reporting_data["Totals"].append({
                "Period": period.name,
                "PeriodAndDate": period_and_date,  # type: ignore[name-defined]
                "StartDate": period.start_date,
                "EndDate": period.end_date,
                "HaveData": period.have_global_data,
                "GlobalEnergyUsed": period.global_energy_used,
                "GlobalCost": period.global_cost,
                "OutputEnergyUsed": period.output_energy_used,
                "OutputCost": period.output_cost,
                "OtherEnergyUsed": period.other_energy_used,
                "OtherCost": period.other_cost,
            })

        return reporting_data

    # ============ PRIVATE FUNCTIONS ========================================================================

    def _get_cache_metadata(self):  # noqa: PLR6301
        """Read cache metadata to see when state was last loaded.

        Returns:
            dict | None: The cache metadata dictionary if available, None otherwise.
        """
        try:
            assert isinstance(PowerControllerViewer._cache_metadata_file, Path)
            if PowerControllerViewer._cache_metadata_file.exists():
                with PowerControllerViewer._cache_metadata_file.open("r") as f:
                    return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _update_cache_metadata(self, timestamp: float):
        """Update cache metadata with current load time and process ID."""
        try:
            metadata = {
                "last_load_time": timestamp,
                "last_load_pid": os.getpid(),
                "last_load_datetime": DateHelper.now().isoformat()
            }
            assert isinstance(PowerControllerViewer._cache_metadata_file, Path)
            with PowerControllerViewer._cache_metadata_file.open("w") as f:
                json.dump(metadata, f)
        except (OSError, json.JSONDecodeError) as e:
            self.logger.log_message(f"Error updating cache metadata: {e!s}", "warning")

    def _load_state_files_internal(self):  # noqa: PLR0912, PLR0915
        """Internal method that does the actual file loading and chart generation."""
        # Acquire both locks to ensure exclusive access for loading and chart generation
        with PowerControllerViewer._state_lock, PowerControllerViewer._chart_generation_lock:  # noqa: PLR1702
            # Initialize the state list
            temp_state_items = []

            if self.state_data_dir.exists() and self.state_data_dir.is_dir():
                json_files = sorted([f.name for f in self.state_data_dir.iterdir() if f.is_file() and f.name.endswith(".json")])

                # Show a warning if we have no state data
                if not json_files:
                    self.logger.log_message(f"No JSON files found in {self.state_data_dir}.", "warning")

                for idx, file_name in enumerate(json_files):
                    if file_name.startswith("."):
                        # Skip hidden files
                        continue

                    file_path = Path(self.state_data_dir) / file_name

                    self.logger.log_message(f"Attempting to load state file: {file_path}.", "debug")

                    try:
                        state_item = self._safe_read_json(file_path)

                        if state_item is not None:
                            # Decode any datatype hints - all file types now
                            state_item = JSONEncoder.decode_object(state_item)
                            assert isinstance(state_item, dict), "Decoded data is not a dictionary."

                            last_save_time = None
                            state_file_type = state_item.get("StateFileType")
                            if state_file_type == "LightingControl":
                                last_save_time = state_item.get("LastStateSaveTime")
                                device_description = "Lighting Controller"
                            elif state_file_type == "PowerController":
                                last_save_time = state_item.get("SaveTime")
                                if state_item.get("Output", {}).get("Type") == "teslamate":
                                    device_description = "Tesla Charging"
                                elif state_item.get("Output", {}).get("Type") == "meter":
                                    device_description = "Energy Meter"
                                else:
                                    device_description = "Power Controller"
                            elif state_file_type == "TempProbes":
                                last_save_time = state_item.get("SaveTime")
                                device_description = "Temperature Probes"
                            elif state_file_type == "OutputMetering":
                                last_save_time = state_item.get("SaveTime")
                                device_description = "Metered Outputs"
                            else:
                                device_description = "Unknown Device"

                            if last_save_time is None:
                                last_save_time = DateHelper.now()

                            # Convert the last_save_time to the local timezone
                            if isinstance(last_save_time, dt.datetime):
                                last_save_time = last_save_time.astimezone()

                            # Update the latest save timestamp if needed
                            if (PowerControllerViewer._state_latest_save_timestamp is None or last_save_time > PowerControllerViewer._state_latest_save_timestamp):
                                self.logger.log_message(f"Updating latest state save timestamp to {last_save_time} for {file_path}.", "debug")
                                PowerControllerViewer._state_latest_save_timestamp = last_save_time

                            # record the last save time and device description
                            state_item["LocalLastSaveTime"] = last_save_time
                            state_item["DeviceDescription"] = device_description
                            state_item["StateURLName"] = self.url_encode_device_name(state_item.get("DeviceName", f"Device{idx + 1}"))

                            if state_file_type == "TempProbes":
                                # Generate any required temp probe charts
                                self._generate_state_data_charts(idx, state_item)

                            temp_state_items.append(state_item)
                            self.logger.log_message(f"Successfully loaded state item {idx + 1} from {file_path}.", "debug")
                        else:
                            self.logger.log_message(f"Skipped empty or unreadable file: {file_path}.", "warning")

                    except (OSError, json.JSONDecodeError) as e:
                        self.report_fatal_error(f"Error loading JSON from {file_path}: {e}")

                self.logger.log_message(f"Loaded {len(temp_state_items)} state items from {self.state_data_dir}.", "debug")

            # Update both instance and class-level cache atomically
            self.state_items = temp_state_items
            PowerControllerViewer._state_cache = temp_state_items.copy()
            PowerControllerViewer._state_cache_timestamp = DateHelper.now()

    def _state_loader_worker(self):
        """Background worker thread that monitors and reloads state files."""
        check_interval = 5  # Check every 5 seconds

        while not PowerControllerViewer._worker_stop_event.is_set():
            try:
                if self.check_for_state_file_changes():
                    # Check if another process recently loaded (within last 10 seconds)
                    cache_meta = self._get_cache_metadata()
                    if cache_meta:
                        last_load_time = cache_meta.get("last_load_time", 0)
                        last_load_pid = cache_meta.get("last_load_pid")
                        time_since_load = time.time() - last_load_time

                        if time_since_load < 10 and last_load_pid != os.getpid():
                            self.logger.log_message(
                                f"Worker (PID {os.getpid()}) skipping reload - process {last_load_pid} "
                                f"loaded {time_since_load:.1f}s ago",
                                "debug"
                            )
                            # Wait a bit longer before next check
                            PowerControllerViewer._worker_stop_event.wait(2)
                            continue

                    # Try to acquire lock
                    lock_acquired = False
                    lock_file = None
                    try:
                        assert isinstance(PowerControllerViewer._reload_lock_file, Path)
                        lock_file = PowerControllerViewer._reload_lock_file.open("w")
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        lock_acquired = True

                        self.logger.log_message(
                            f"Worker (PID {os.getpid()}) acquired reload lock, reloading state...",
                            "debug"
                        )
                        self._load_state_files_internal()
                        self._update_cache_metadata(time.time())

                    except BlockingIOError:
                        self.logger.log_message(
                            f"Worker (PID {os.getpid()}) detected reload in progress by another process",
                            "debug"
                        )
                        time.sleep(2)

                    finally:
                        if lock_acquired and lock_file:
                            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                            lock_file.close()

            except (OSError, json.JSONDecodeError, RuntimeError, ValueError, TypeError) as e:
                self.logger.log_message(f"Error in state loader worker: {e!s}", "error")

            # Wait for the specified interval or until stop event is set
            PowerControllerViewer._worker_stop_event.wait(check_interval)

    def _safe_read_json(self, file_path: Path, max_retries: int = 3, retry_delay: float = 0.1):
        """Safely read JSON file with locking and retries.

        Args:
            file_path (Path): The path to the JSON file to read.
            max_retries (int): Maximum number of retries for reading the file.
            retry_delay (float): Delay in seconds between retries.

        Raises:
            OSError: If the file cannot be read after the maximum number of retries.
            json.JSONDecodeError: If the file is not a valid JSON.

        Returns:
            dict | None: The parsed JSON data if successful, None if the file is empty or unreadable.
        """
        for attempt in range(max_retries):
            try:
                with file_path.open("r", encoding="utf-8") as file:
                    fcntl.flock(file.fileno(), fcntl.LOCK_SH)
                    try:
                        file.seek(0, 2)  # Seek to end
                        if file.tell() == 0:
                            self.logger.log_message(f"File {file_path} is empty, skipping.", "warning")
                            return None
                        file.seek(0)  # Reset to beginning
                        return json.load(file)
                    finally:
                        fcntl.flock(file.fileno(), fcntl.LOCK_UN)
            except (OSError, json.JSONDecodeError) as e:
                if attempt < max_retries - 1:
                    self.logger.log_message(f"Read failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying...", "warning")
                    time.sleep(retry_delay)
                    continue
                raise
        return None

    def _safe_write_json(self, file_path: Path, data: dict, max_retries: int = 3, retry_delay: float = 0.1):
        """Safely write JSON file with atomic operations and locking.

        Args:
            file_path (Path): The path to the JSON file to write.
            data (dict): The data to write to the JSON file.
            max_retries (int): Maximum number of retries for writing the file.
            retry_delay (float): Delay in seconds between retries.

        Raises:
            OSError: If the file cannot be written after the maximum number of retries.

        Returns:
            bool: True if the write was successful, False if the file was empty or unreadable
        """
        for attempt in range(max_retries):
            try:
                temp_file_path = file_path.with_suffix(".tmp")
                with temp_file_path.open("w", encoding="utf-8") as file:
                    fcntl.flock(file.fileno(), fcntl.LOCK_EX)
                    try:
                        json.dump(data, file, indent=4)
                        file.flush()
                    finally:
                        fcntl.flock(file.fileno(), fcntl.LOCK_UN)

                temp_file_path.replace(file_path)
            except OSError as e:
                if attempt < max_retries - 1:
                    self.logger.log_message(f"Write failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying...", "warning")
                    time.sleep(retry_delay)
                    continue
                raise
            else:
                return True
        return False

    def __setitem__(self, index, value):
        """Allows setting values in the state dictionary using square brackets."""
        self.state_items[index] = value

    def _generate_state_data_charts(self, state_idx: int, state_item: dict):  # noqa: PLR0914
        """Generate any required state data charts.

        Generates the required charts for the provided state item and saves them to the static directory.
        This method should only be called while holding _chart_generation_lock.

        Args:
            state_idx (int): The index of the state item.
            state_item (dict): The state item to generate charts for.
        """
        # This method is now only called from _load_state_files_internal which holds the lock
        # Delete all existing temp probe charts for this state index
        static_path = SCCommon.select_file_location("static/dummy.jpg")
        if static_path is None:
            self.logger.log_message("Failed to determine static path for temp probe charts.", "error")
            return

        static_dir = static_path.parent
        state_name = state_item.get("DeviceName") or f"State {state_idx}"
        self.logger.log_message(f"Generating temp probe charts for state '{state_name}'.", "debug")

        # Use proper glob pattern with the directory
        chart_pattern = f"Chart_{state_name}*.jpg"
        existing_charts = list(static_dir.glob(chart_pattern))
        for chart_file in existing_charts:
            try:
                chart_file.unlink()
            except OSError as e:
                self.logger.log_message(f"Error deleting existing temp probe chart {chart_file}: {e}", "warning")

        # if existing_charts:
        #     self.logger.log_message(f"Found {len(existing_charts)} existing charts for '{state_name}': {[c.name for c in existing_charts]}", "debug")

        # If the state item is temp probe data, generate the temp probe charts now
        probe_history = state_item.get("TempProbeLogging", {}).get("history", []) or []
        probe_config = state_item.get("TempProbeLogging", {}).get("probes", []) or []
        if not probe_history or "TempProbeLogging" not in state_item or not state_item.get("Charting", {}).get("Enable"):
            return

        charting_config = state_item.get("Charting", {}).get("Charts", []) or []
        # Loop through the Charts in the charting config and generate a chart for each one
        state_item["TempProbeCharts"] = []
        chart_count = len(charting_config)
        for config_idx, chart_config in enumerate(charting_config):
            chart_name = chart_config.get("Name", f"Chart {state_name}-{config_idx}")
            chart_file_name = f"Chart_{state_name}-{config_idx}.jpg"
            days_to_show = chart_config.get("DaysToShow", 7)

            # Issue 14: probe_names are now a list of string pairs - the probe name and display name
            probe_names = []
            for probe in chart_config.get("Probes", []):
                display_name = probe
                colour = None
                for probe_cfg in probe_config:
                    if probe_cfg["Name"] == probe:
                        display_name = probe_cfg.get("DisplayName", probe)
                        colour = probe_cfg.get("Colour")
                        break
                probe_name_entry = {
                    "Name": probe,
                    "DisplayName": display_name,
                    "Colour": colour,
                }
                probe_names.append(probe_name_entry)

            if self._generate_temp_probe_chart(probe_history, chart_file_name, chart_name=chart_name, probe_names=probe_names, days_to_show=days_to_show, chart_count=chart_count):
                state_item["TempProbeCharts"].append(chart_file_name)

    def _generate_temp_probe_chart(self, probe_data: list[dict], file_name: str, chart_name: str | None = None, probe_names: list[dict] | None = None, days_to_show: int | None = None, chart_count: int = 0) -> bool:  # noqa: PLR0912, PLR0914, PLR0915
        """Generate a temperature probe chart from the provided probe data.

        Args:
            probe_data (list[dict]): List of temperature probe data dictionaries containing
                                     'Timestamp', 'ProbeName', and 'Temperature' keys.
            file_name (str): The file name to use for the generated chart image.
            chart_name (str | None): Optional name for the chart.
            probe_names (list[dict] | None): Optional list of probe name dicts to include in the chart. If None, all probes are included.
            days_to_show (int | None): Optional number of days to show in the chart. If None, all data is shown.
            chart_count (int): Optional total number of charts being generated (for scaling purposes). Use 0 for default.

        Returns:
            bool: True if the chart was generated successfully, False otherwise.
        """
        if not probe_data:
            self.logger.log_message("No probe data provided for chart generation.", "warning")
            return False

        try:
            # Organize data by probe name
            probe_series: dict[str, dict] = defaultdict(lambda: {"timestamps": [], "temperatures": []})
            all_temps = []

            earlist_time = None
            if days_to_show is not None:
                earlist_time = DateHelper.now() - dt.timedelta(days=days_to_show)

            for entry in probe_data:
                probe_name = entry.get("ProbeName")
                timestamp = entry.get("Timestamp")
                temperature = entry.get("Temperature")

                if (probe_name  # noqa: PLR0916
                    and (not probe_names or any(probe["Name"] == probe_name for probe in probe_names))
                    and timestamp and (not earlist_time or timestamp >= earlist_time)
                    and temperature is not None):
                    probe_series[probe_name]["timestamps"].append(timestamp)
                    probe_series[probe_name]["temperatures"].append(temperature)
                    all_temps.append(temperature)

            if not all_temps:
                self.logger.log_message("No valid temperature data found in probe_data.", "warning")
                return False

            # Calculate Y-axis range
            min_temp = min(all_temps) - 2
            max_temp = max(all_temps) + 2

            # Create the plot
            if chart_count == 2:
                plot_height = 3.5
            elif chart_count >= 3:
                plot_height = 2.5
            else:
                plot_height = 6
            fig, ax = plt.subplots(figsize=(15, plot_height))

            # Define a color map to ensure consistent colors per probe
            cmap = plt.cm.get_cmap("tab10")
            colors = [cmap(i) for i in range(10)]  # Get the first 10 colors from tab10 colormap
            probe_colors = {name: colors[i % len(colors)] for i, name in enumerate(sorted(probe_series.keys()))}

            # Plot each probe's data
            for probe_name, data in probe_series.items():
                timestamps = data["timestamps"]
                temperatures = data["temperatures"]

                # Find matching probe entry to get DisplayName
                display_name = probe_name
                line_colour = probe_colors[probe_name]
                if probe_names:
                    for probe_entry in probe_names:
                        if probe_entry.get("Name") == probe_name:
                            display_name = probe_entry.get("DisplayName", probe_name)
                            line_colour = probe_entry.get("Colour", probe_colors[probe_name])
                            break

                if len(timestamps) <= 1:
                    # Single point or no data
                    ax.plot(timestamps, temperatures,
                           marker="o", linestyle="", markersize=4,
                           label=display_name)
                else:
                    # Find gaps larger than 24 hours
                    segments_x = []
                    segments_y = []
                    current_x = [timestamps[0]]
                    current_y = [temperatures[0]]

                    for i in range(1, len(timestamps)):
                        time_gap = (timestamps[i] - timestamps[i - 1]).total_seconds() / 3600  # hours

                        if time_gap > 24:  # Gap larger than 24 hours
                            # End current segment
                            segments_x.append(current_x)
                            segments_y.append(current_y)
                            # Start new segment
                            current_x = [timestamps[i]]
                            current_y = [temperatures[i]]
                        else:
                            # Continue current segment
                            current_x.append(timestamps[i])
                            current_y.append(temperatures[i])

                    # Add the last segment
                    segments_x.append(current_x)
                    segments_y.append(current_y)

                    # Plot each segment separately
                    for j, (seg_x, seg_y) in enumerate(zip(segments_x, segments_y, strict=False)):
                        label_name = display_name if j == 0 else None  # Only label first segment
                        ax.plot(seg_x,
                                seg_y,
                                marker="o",
                                linestyle="-",
                                linewidth=2,
                                markersize=4,
                                label=label_name,
                                color=line_colour)

            # Configure axes
            # ax.set_xlabel("Date", fontsize=12)
            ax.set_ylabel("Temperature °C", fontsize=12)
            ax.set_ylim(min_temp, max_temp)
            ax.grid(True, alpha=0.3)

            # Format X-axis dates
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b %-I %p"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate()

            # Add legend if we have more than one probe
            if len(probe_series) > 1:
                ax.legend(loc="best", framealpha=0.9)

            # Add chart title
            if chart_name:
                ax.text(0.01, 0.95, chart_name, transform=ax.transAxes, fontsize=14,
                       verticalalignment="top", horizontalalignment="left",
                       bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.8})

            # Save the chart
            chart_path = SCCommon.select_file_location(f"static/{file_name}")
            if chart_path is None:
                self.logger.log_message("Failed to determine chart path.", "error")
                return False

            chart_path.unlink(missing_ok=True)  # Remove existing file if it exists
            plt.tight_layout()
            plt.savefig(chart_path, dpi=100, bbox_inches="tight")

            plt.close(fig)

        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as e:
            self.logger.log_message(f"Error generating temperature probe chart: {e!s}", "error")
            return False
        return True

    def _get_global_usage_totals(self, state_idx: int, reporting_period: UsageReportingPeriod):
        """Gets the total energy usage between the specified dates.

        The passed reporting_period object is updated with the totals. If no data is available, the object isn't updated.

        Note: Energy usage is returned in kWh.

        Args:
            state_idx (int): The index of the state item to get usage totals for.
            reporting_period (UsageReportingPeriod): The reporting period to get usage totals for.
        """
        # Skip if this period is set to don't show
        if not reporting_period.show:
            return

        # By default return no data
        reporting_period.have_global_data = False

        # First scan self.usage_data and make sure we have entries on or before the start_date and on or after the end_date
        global_data = self.get_state(state_idx).get("Totals", [])
        if not global_data:
            return

        for entry in global_data:
            entry_date = entry.get("Date")
            if not isinstance(entry_date, dt.date):
                continue
            if entry_date < reporting_period.start_date or entry_date > reporting_period.end_date:
                continue

            # Now aggregate the usage data for the specified date range
            reporting_period.have_global_data = True
            reporting_period.global_energy_used += entry.get("EnergyUsed", 0.0) or 0.0
            reporting_period.global_cost += entry.get("Cost", 0.0) or 0.0

    def _get_meter_reporting_periods(self, state_idx: int, period_idx: int | None = None, custom_start_date: dt.date | None = None, custom_end_date: dt.date | None = None) -> list[UsageReportingPeriod]:
        """Get the list of standard reporting periods for meter usage analysis.

        Args:
            state_idx (int): The index of the state item to get reporting periods for.
            period_idx (int | None): Optional index of a specific reporting period to return.
            custom_start_date (dt.date | None): Optional custom start date for a custom reporting period.
            custom_end_date (dt.date | None): Optional custom end date for a custom reporting period.

        Returns:
            list[UsageReportingPeriod]: List of reporting periods.
        """
        # First build a list of reporting periods that we want to analyse
        reporting_periods = []
        today = DateHelper.today()
        state_summary = self.get_state(state_idx).get("Summary", {})

        # Calendar-based reporting periods
        # Weeks are Monday-Sunday (Python's weekday(): Monday=0 .. Sunday=6)
        this_week_start = today - dt.timedelta(days=today.weekday())
        last_week_start = this_week_start - dt.timedelta(days=7)
        last_week_end = this_week_start - dt.timedelta(days=1)

        # Month boundaries
        this_month_start = today.replace(day=1)
        last_month_end = this_month_start - dt.timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)

        yesterday = today - dt.timedelta(days=1)

        # Clamp "to yesterday" periods so end_date is never before start_date
        current_week_end = max(yesterday, this_week_start)
        current_month_end = max(yesterday, this_month_start)

        reporting_periods.append(UsageReportingPeriod("All Dates", state_summary.get("FirstDate"), state_summary.get("LastDate")))  # noqa: FURB113
        reporting_periods.append(UsageReportingPeriod("Last 30 Days", today - dt.timedelta(days=30), today - dt.timedelta(days=1), show=True, menu=False))
        reporting_periods.append(UsageReportingPeriod("Last Month", last_month_start, last_month_end))
        reporting_periods.append(UsageReportingPeriod("This Month", this_month_start, current_month_end))
        reporting_periods.append(UsageReportingPeriod("Last 7 Days", today - dt.timedelta(days=7), today - dt.timedelta(days=1), show=True, menu=False))
        reporting_periods.append(UsageReportingPeriod("Last Week", last_week_start, last_week_end))
        reporting_periods.append(UsageReportingPeriod("This Week", this_week_start, current_week_end))
        reporting_periods.append(UsageReportingPeriod("Yesterday", today - dt.timedelta(days=1), today - dt.timedelta(days=1), show=True, menu=False))
        reporting_periods.append(UsageReportingPeriod("Today", today, today))

        if custom_start_date and custom_end_date:
            reporting_periods.append(UsageReportingPeriod("Custom Period", custom_start_date, custom_end_date, is_custom=True, show=True, menu=True))

        # Now, if we have a specific period_idx, set the show flag for that period as well
        if period_idx is not None and 0 <= period_idx < len(reporting_periods):
            reporting_periods[period_idx].show = True

        return reporting_periods
