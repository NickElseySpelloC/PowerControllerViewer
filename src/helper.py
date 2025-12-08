"""General helper functions for the project."""

import datetime as dt
import fcntl
import inspect
import json
import time
import traceback
from html import escape
from pathlib import Path
from typing import Any

from sc_utility import DateHelper, JSONEncoder, SCCommon, SCConfigManager, SCLogger

TEMP_PROBE_CHART_LOCATION = "static/dummy_chart.jpg"


class PowerControllerViewer:
    """General purpose helper functions for the PowerControllerViewer."""

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
        # Perform initial housekeeping which will include loading the state files
        self.housekeeping()

    def load_state_files(self):
        """Load the availabke state from the JSON files."""
        # Look in the state_data subdirectory for the all the available state files

        # Initialize the state list
        self.state_items.clear()

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
                        # Decode any datatype hints if it's a PowerController state file
                        if state_item.get("StateFileType") in {"PowerController", "TempProbes"}:
                            state_item = JSONEncoder.decode_object(state_item)
                            assert isinstance(state_item, dict), "Decoded data is not a dictionary."

                        self.state_items.append(state_item)
                        self.logger.log_message(f"Successfully loaded state item {idx + 1} from {file_path}.", "debug")

                        file_modified = Path(file_path).stat().st_mtime
                        if self.last_state_check is None or file_modified > self.last_state_check:
                            # If the file has been modified since the last check, update the last state check time
                            self.last_state_check = file_modified
                    else:
                        self.logger.log_message(f"Skipped empty or unreadable file: {file_path}.", "warning")

                except (OSError, json.JSONDecodeError) as e:
                    self.report_fatal_error(f"Error loading JSON from {file_path}: {e}")

    def validate_state_index(self, requested_state_idx: int | None = None) -> tuple[int | None, int | None]:
        """Validate that a requested state index is within the valid range.

        Args:
            requested_state_idx (int, optional): The requested state index to use. If None, the first available state will be returned.

        Returns:
            result(int, int): The actual state index to use and the next_state_idx to use. Returns None if there are no state items.
        """
        # Set the new state index if provided
        max_state_idx = len(self.state_items) - 1
        if len(self.state_items) == 0:
            return None, None

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
        if self.last_state_check is None:
            return True

        # Get the last modified time of the state files
        # Look in the state_data subdirectory for the all the available state files
        filename_concat = ""
        if self.state_data_dir.exists() and self.state_data_dir.is_dir():
            json_files = [f for f in self.state_data_dir.iterdir() if f.is_file() and f.name.endswith(".json")]

            for file_path in json_files:
                # This will be the concatentnation of all the state file names - used to check if files have been added or removed
                filename_concat += file_path.name

                if file_path.name.startswith("."):
                    # Skip hidden files
                    continue

                file_modified = file_path.stat().st_mtime
                if file_modified > self.last_state_check:
                    # We have a more recent state file
                    return True

            if self.last_state_filename_hash != filename_concat:
                self.logger.log_message(f"State files have changed. Reloading state files from {self.state_data_dir}.", "debug")
                self.last_state_filename_hash = filename_concat
                return True

        return False

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
    def format_date_with_ordinal(date: dt.date, show_time: bool | None = False):  # noqa: FBT001, FBT002
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

    def report_fatal_error(self, message, report_stack=False, calling_function=None) -> str:  # noqa: FBT002
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

    def generate_temp_probe_chart(self, probe_data: list[dict]) -> str | None:
        """Generate a temperature probe chart from the provided probe data.

        Args:
            probe_data (list[dict]): List of temperature probe data dictionaries.

        Returns:
            str | None: The name of the generated chart image in the static folder, or None if generation failed.
        """
        # TO DO: Implement the chart generation logic
        self.logger.log_message("Generating temperature probe chart... (not yet implemented)", "debug")

        chart_path = SCCommon.select_file_location(TEMP_PROBE_CHART_LOCATION)
        if chart_path is None:
            self.logger.log_message("Failed to determine chart path.", "error")
            return None
        return chart_path.name

    # ============ PRIVATE FUNCTIONS ========================================================================
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
