"""General utility functions for the project."""

import inspect
import json
import os
import sys
import traceback
from datetime import datetime
from html import escape
from pathlib import Path

import yaml
from cerberus import Validator

CONFIG_FILE = "PowerControllerUIConfig.yaml"

def merge_configs(default, custom):
    """Merges two dictionaries recursively, with the custom dictionary."""
    for key, value in custom.items():
        if isinstance(value, dict) and key in default:
            merge_configs(default[key], value)
        else:
            default[key] = value
    return default

class ConfigManager:
    """Class to manage system configuration and file paths."""

    def __init__(self):
        self.config_file_path = self.select_file_location(CONFIG_FILE)
        self.config_last_modified = None
        self.default_config = {
            "Website": {
                "HostingIP": None,
                "Port": "8000",
                "PageAutoRefresh": 10,
                "DebugMode": False,
                "AccessKey": None,
            },
            "Files": {
                "MonitoringLogFile": "PowerControllerUI.log",
                "MonitoringLogFileMaxLines": 5000,
                "LogFileVerbosity": "summary",
                "ConsoleVerbosity": "summary",
            },
        }

        self.default_config_schema = {
            "Website": {
                "type": "dict",
                "schema": {
                    "HostingIP": {"type": "string", "required": False, "nullable": True},
                    "Port": {"type": "number", "required": False, "nullable": True, "min": 80, "max": 65535},
                    "PageAutoRefresh": {"type": "number", "required": False, "nullable": True, "min": 0, "max": 3600},
                    "DebugMode": {"type": "boolean", "required": False, "nullable": True},
                    "AccessKey": {"type": "string", "required": False, "nullable": True},
                },
            },
            "Files": {
                "type": "dict",
                "schema": {
                    "MonitoringLogFile": {"type": "string", "required": False, "nullable": True},
                    "MonitoringLogFileMaxLines": {"type": "number", "min": 0, "max": 100000},
                    "LogFileVerbosity": {
                        "type": "string",
                        "required": True,
                        "allowed": ["none", "error", "warning", "summary", "detailed", "debug", "all"],
                    },
                    "ConsoleVerbosity": {
                        "type": "string",
                        "required": True,
                        "allowed": ["error", "warning", "summary", "detailed", "debug", "all"],
                    },
                 },
            },
        }

        self.load_config()

    def load_config(self):
        """Load the configuration file. If it does not exist, create it with default values."""
        if not Path(self.config_file_path).exists():
            with Path(self.config_file_path).open("w", encoding="utf-8") as file:
                yaml.dump(self.default_config, file)

        with Path(self.config_file_path).open(encoding="utf-8") as file:
            v = Validator()
            config_doc = yaml.safe_load(file)

            self.validate_no_placeholders(config_doc)

            if not v.validate(config_doc, self.default_config_schema):
                print(f"Error in configuration file: {v.errors}", file=sys.stderr)
                sys.exit(1)

        self.active_config = merge_configs(self.default_config, config_doc)
        self.config_last_modified = Path(self.config_file_path).stat().st_mtime

    def check_for_config_changes(self):
        """
        Check if the configuration file has changed. If it has, reload the configuration.

        :return: True if the configuration has changed, False otherwise.
        """
        # get the last modified time of the config file
        last_modified = Path(self.config_file_path).stat().st_mtime

        if self.config_last_modified is None or last_modified > self.config_last_modified:
            # The config file has changed, reload it
            self.load_config()
            self.config_last_modified = last_modified
            return True

        return False

    def validate_no_placeholders(self, config_section, path=""):
        # Define expected placeholders
        placeholders = {
            "<Your website API key here>",
        }

        if isinstance(config_section, dict):
            for key, value in config_section.items():
                self.validate_no_placeholders(value, f"{path}.{key}" if path else key)
        elif isinstance(config_section, list):
            for idx, item in enumerate(config_section):
                self.validate_no_placeholders(item, f"{path}[{idx}]")
        elif str(config_section).strip() in placeholders:
            print(f"ERROR: Config value at '{path}' is still set to placeholder: '{config_section}'", file=sys.stderr)
            print(f"Please update {CONFIG_FILE} with your actual credentials.", file=sys.stderr)
            sys.exit(1)

    def select_file_location(self, file_name: str, sub_dir: str | None = None) -> str:
        """
        Selects the file location for the given file name.

        :param file_name: The name of the file to locate.
        :param sub_dir: The sub directory to look in, if any.
        :return: The full path to the file. If the file does not exist in the current directory, it will look in the script directory.
        """
        current_dir = Path.cwd()
        script_dir = Path(__file__).parent
        if sub_dir is None:
            file_path = Path(current_dir) / file_name
        else:
            file_path = Path(current_dir) / sub_dir / file_name
        if not file_path.exists():
            if sub_dir is None:
                file_path = Path(script_dir) / file_name
            else:
                file_path = Path(script_dir) / sub_dir / file_name
        return str(file_path)

class UtilityFunctions:
    """Class representing the state of the power controller."""

    def __init__(self, config_manager_object):
        self.config_manager = config_manager_object
        self.config = self.config_manager.active_config
        self.last_housekeeping = None
        self.last_state_check = None
        self.state_items = []
        self.selected_state = None
        self.process_id = os.getpid()

        # Perform initial housekeeping which will include loading the state files
        self.housekeeping()

    def load_state_files(self):
        """Load the availabke state from the JSON files."""
        # Look in the state_data subdirectory for the all the available state files
        state_data_dir = Path(__file__).resolve().parent / "state_data"

        # Initialize the state list
        self.state_items.clear()

        if Path(state_data_dir).exists() and Path(state_data_dir).is_dir():
            json_files = sorted([f.name for f in Path(state_data_dir).iterdir() if f.is_file() and f.name.endswith(".json")])

            # Show a warning if we have no state data
            if not json_files:
                self.log_message(f"No JSON files found in {state_data_dir}.", "warning")

            for idx, file_name in enumerate(json_files):
                if file_name.startswith("."):
                    # Skip hidden files
                    continue

                file_path = Path(state_data_dir) / file_name

                self.log_message(f"Attempting to load state file: {file_path}.", "debug")

                try:
                    with Path(file_path).open(encoding="utf-8") as file:
                        # Append the state item to the list
                        state_item = json.load(file)
                        self.state_items.append(state_item)
                        self.log_message(f"Successfully loaded state item {idx+1} from {file_path}.", "debug")

                        file_modified = Path(file_path).stat().st_mtime
                        if self.last_state_check is None or file_modified > self.last_state_check:
                            # If the file has been modified since the last check, update the last state check time
                            self.last_state_check = file_modified

                # To do
                except json.JSONDecodeError as e:
                    self.report_fatal_error(f"Error decoding JSON from {file_path}: {e}")

        # If we have loaded at least one state file, select the first one if our selector is None
        if self.selected_state is None and len(self.state_items) > 0:
            self.selected_state = 0

    def get_selected_state(self, new_state_idx=None):
        """Return the selected state index. Reset if it's invalid."""
        # Set the new state index if provided
        if new_state_idx is not None:
            self.selected_state = new_state_idx

        # No state items, nothing to do
        if len(self.state_items) == 0:
            self.selected_state = None
        # We have some state items in the array, make sure the new state index is valid
        elif self.selected_state is None or self.selected_state < 0:
            self.selected_state = 0
        elif self.selected_state >= len(self.state_items):
            # If the selected state is out of range, reset it to the first one
            self.selected_state = len(self.state_items) - 1

        return self.selected_state

    def save_state(self, state_item):
        """Save the current state to the JSON file. This assumes that the calling function has already validates the state file."""
        state_file_path = Path(__file__).resolve().parent / "state_data" / (state_item["DeviceName"] + ".json")
        try:
            with state_file_path.open("w", encoding="utf-8") as file:
                json.dump(state_item, file, indent=4)
                self.log_message(f"Successfully saved state to {state_file_path}.", "debug")
        except OSError as e:
            self.report_fatal_error(f"Error writing to {state_file_path}: {e}")

    def check_for_state_file_changes(self):
        """Check if the state files have changed since the last check. If they have, return true."""
        if self.last_state_check is None:
            return True

        # Get the last modified time of the state files
        # Look in the state_data subdirectory for the all the available state files
        state_data_dir = Path(__file__).resolve().parent / "state_data"

        if state_data_dir.exists() and state_data_dir.is_dir():
            json_files = [f for f in state_data_dir.iterdir() if f.is_file() and f.name.endswith(".json")]

            for file_path in json_files:
                if file_path.name.startswith("."):
                    # Skip hidden files
                    continue

                file_modified = file_path.stat().st_mtime
                if file_modified > self.last_state_check:
                    # We have a more recent state file
                    return True

        return False

    def housekeeping(self):
        """General housekeeping function to be called periodically. Will run every hours. Initialise the monitoring log file. If it exists, truncate it to the max number of lines. Returns True if changes were made, False otherwise."""
        local_tz = datetime.now().astimezone().tzinfo
        return_value = False
        # Check if the configuration file has changed. Reload if it has.
        if self.config_manager.check_for_config_changes():
            self.log_message("Reloading config file for new changes.", "detailed")
            return_value = True

        # Check if the state files have changed. Reload if they have.
        if self.check_for_state_file_changes():
            self.log_message("Reloading state files for new changes.", "detailed")
            self.load_state_files()
            return_value = True

        # Check if the last housekeeping was more than 1 hour ago
        if self.last_housekeeping is not None:
            now = datetime.now(local_tz)
            if (now - self.last_housekeeping).total_seconds() < 3600:  # noqa: PLR2004
                return return_value

        return_value = True

        # Truncate the log file if it exists
        if self.config["Files"]["MonitoringLogFile"] is not None:
            file_path = self.config_manager.select_file_location(self.config["Files"]["MonitoringLogFile"])

            if Path(file_path).exists():
                # Monitoring log file exists - truncate excess lines if needed.
                with Path(file_path).open(encoding="utf-8") as file:
                    max_lines = self.config["Files"]["MonitoringLogFileMaxLines"]

                    if max_lines > 0:
                        lines = file.readlines()

                        if len(lines) > max_lines:
                            # Keep the last max_lines rows
                            keep_lines = lines[-max_lines:] if len(lines) > max_lines else lines

                            # Overwrite the file with only the last 1000 lines
                            with Path(file_path).open("w", encoding="utf-8") as file2:
                                file2.writelines(keep_lines)

                            self.log_message("Housekeeping of log file completed.", "debug")

        # Set the last housekeeping time to now
        self.last_housekeeping = datetime.now(local_tz)
        return return_value

    def log_message(self, message: str, verbosity: str):
        """Writes a log message to the console and/or a file based on verbosity settings."""
        local_tz = datetime.now().astimezone().tzinfo
        config_file_setting_str = self.config["Files"]["LogFileVerbosity"]
        console_setting_str = self.config["Files"]["ConsoleVerbosity"]

        if verbosity not in ["error", "warning", "summary", "detailed", "debug", "all"]:
            print("Invalid verbosity setting passed to write_log_message().", file=sys.stderr)
            sys.exit(1)

        switcher = {
            "none": 0,
            "error": 1,
            "warning": 2,
            "summary": 3,
            "detailed": 4,
            "debug": 5,
            "all": 6,
        }

        config_file_setting = switcher.get(config_file_setting_str, 0)
        console_setting = switcher.get(console_setting_str, 0)
        message_level = switcher.get(verbosity, 0)

        process_str = ""
        if self.process_id is not None:
            process_str = f" Proc {self.process_id}"

        # Deal with console message first
        if console_setting >= message_level and console_setting > 0:
            if verbosity == "error":
                print("ERROR: " + message, file=sys.stderr)
            elif verbosity == "warning":
                print("WARNING: " + message)
            else:
                print(message)

        # Now write to the log file if needed
        if self.config["Files"]["MonitoringLogFile"] is not None:
            file_path = self.config_manager.select_file_location(self.config["Files"]["MonitoringLogFile"])
            error_str = " ERROR" if verbosity == "error" else " WARNING" if verbosity == "warning" else ""
            if config_file_setting >= message_level and config_file_setting > 0:
                with Path(file_path).open("a", encoding="utf-8") as file:
                    if message == "":
                        file.write("\n")
                    else:
                        file.write(f"{datetime.now(local_tz).strftime('%Y-%m-%d %H:%M:%S')}{process_str}{error_str}: {message}\n")

    def report_fatal_error(self, message, report_stack=False, calling_function=None):  # noqa: FBT002
        """Report a fatal error and exit the program."""
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
        self.log_message(return_str, "error")
        return return_str

    def hours_to_string(self, hours):
        """Convert hours to a string in the format HH:MM."""
        if hours is None:
            return "00:00"
        if hours < 0:
            return "00:00"
        hours_part = int(hours)
        minutes = int((hours - hours_part) * 60)
        return f"{hours_part}:{minutes:02}"

    def format_date_with_ordinal(self, date, show_time=False):  # noqa: FBT002
        """Format a date with an ordinal suffix for the day - for example 14th April."""
        time_str = date.strftime(" %H:%M:%S")

        day = date.day
        # Determine the ordinal suffix
        if 11 <= day <= 13:  # Special case for 11th, 12th, 13th  # noqa: PLR2004
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

        # Format the date with the ordinal suffix
        return_str = date.strftime(f"%d{suffix} %B")
        if show_time:
            return_str += time_str
        return return_str

    def generate_html_page(self, text):
        """
        Generate a complete HTML page with the given text properly formatted.

        Newlines in the text will be replaced with <br> tags.
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

    def __getitem__(self, key):
        """Allows access to the state dictionary using square brackets."""
        return self.state_items[key]

    def get(self, attribute_name):
        """
        Get the value of an attribute from the UtilityFunctions class.

        :param attribute_name: The name of the attribute to retrieve.
        :return: The value of the attribute, or None if it doesn't exist.
        """
        return getattr(self, attribute_name, None)

    def __setitem__(self, index, value):
        """Allows setting values in the state dictionary using square brackets."""
        self.state_items[index] = value
