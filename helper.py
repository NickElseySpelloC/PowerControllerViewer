"""General helper functions for the project."""

import json
from datetime import datetime
from html import escape
from pathlib import Path


class AmberHelper:
    """General purpose helper functions for the Amber Power Controller UI."""

    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.last_housekeeping = None
        self.last_state_check = None
        self.state_items = []
        self.selected_state = None
        # Perform initial housekeeping which will include loading the state files
        self.housekeeping()

    def load_state_files(self):
        """Load the availabke state from the JSON files."""
        # Look in the state_data subdirectory for the all the available state files
        state_data_dir = Path(__file__).resolve().parent / "state_data"

        # Initialize the state list
        self.state_items.clear()

        if state_data_dir.exists() and state_data_dir.is_dir():
            json_files = sorted([f.name for f in state_data_dir.iterdir() if f.is_file() and f.name.endswith(".json")])

            # Show a warning if we have no state data
            if not json_files:
                self.logger.log_message(f"No JSON files found in {state_data_dir}.", "warning")

            for idx, file_name in enumerate(json_files):
                if file_name.startswith("."):
                    # Skip hidden files
                    continue

                file_path = Path(state_data_dir) / file_name

                self.logger.log_message(f"Attempting to load state file: {file_path}.", "debug")

                try:
                    with Path(file_path).open(encoding="utf-8") as file:
                        # Append the state item to the list
                        state_item = json.load(file)
                        self.state_items.append(state_item)
                        self.logger.log_message(f"Successfully loaded state item {idx+1} from {file_path}.", "debug")

                        file_modified = Path(file_path).stat().st_mtime
                        if self.last_state_check is None or file_modified > self.last_state_check:
                            # If the file has been modified since the last check, update the last state check time
                            self.last_state_check = file_modified

                # To do
                except json.JSONDecodeError as e:
                    self.logger.log_fatal_error(f"Error decoding JSON from {file_path}: {e}")

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
                self.logger.log_message(f"Successfully saved state to {state_file_path}.", "debug")
        except OSError as e:
            self.logger.log_fatal_error(f"Error writing to {state_file_path}: {e}")

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
        # Check if the configuration file has changed. Reload if it has. Throws a RuntimeError if the config file is invalid.
        try:
            if self.config.check_for_config_changes():
                self.logger.log_message("Reloading config file for new changes.", "detailed")
                return_value = True
        except RuntimeError as e:
            self.logger.log_fatal_error(f"Error checking for config changes: {e}")

        # Check if the state files have changed. Reload if they have.
        if self.check_for_state_file_changes():
            self.logger.log_message("Reloading state files for new changes.", "detailed")
            self.load_state_files()
            return_value = True

        # Check if the last housekeeping was more than 1 hour ago
        if self.last_housekeeping is not None:
            now = datetime.now(local_tz)
            if (now - self.last_housekeeping).total_seconds() < 3600:
                return return_value

        return_value = True

        # Truncate the log file if it exists
        self.logger.trim_logfile()

        # Set the last housekeeping time to now
        self.last_housekeeping = datetime.now(local_tz)
        return return_value


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
        if 11 <= day <= 13:  # Special case for 11th, 12th, 13th
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
        value = self.state_items[key]
        return value

    def get_state(self, *keys, default=None):
        """
        Retrieve a value from the state dictionary using a sequence of nested keys.

        Example:
            value = get(state_idx, 'AveragePrice', default=0)

        :param keys: Sequence of keys to traverse the config dictionary.
        :param default: Value to return if the key path does not exist.
        :return: The value if found, otherwise the default.

        """
        value = self.state_items
        try:
            for key in keys:
                value = value[key]
        except (KeyError, TypeError):
            return default
        else:
            return value

    def __setitem__(self, index, value):
        """Allows setting values in the state dictionary using square brackets."""
        self.state_items[index] = value
