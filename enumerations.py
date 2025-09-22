"""Holds all the enumerations used in the project. Saved here to avoid circular imports."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

CONFIG_FILE = "config.yaml"
PRICES_DATA_FILE = "latest_prices.json"
PRICE_SLOT_INTERVAL = 5   # Length in minutes for each price slot
WEEKDAY_ABBREVIATIONS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_PRICE = 30.0
PRICES_DATA_FILE = "latest_prices.json"
PRICE_SLOT_INTERVAL = 5   # Length in minutes for each price slot


# Mode for Amber API
class AmberAPIMode(StrEnum):
    LIVE = "Live"
    OFFLINE = "Offline"
    DISABLED = "Disabled"


# Mode for Amber API
class AmberChannel(StrEnum):
    GENERAL = "general"
    CONTROLLED_LOAD = "controlledLoad"


# Get prices mode
class PriceFetchMode(StrEnum):
    NORMAL = "normal"
    SORTED = "sorted"


# Mode for creating run plans
class RunPlanMode(StrEnum):
    BEST_PRICE = "BestPrice"
    SCHEDULE = "Schedule"


# Mode for run plan target hours
class RunPlanTargetHours(StrEnum):
    NORMAL = "run for target hours"
    ALL_HOURS = "all available hours"


class RunPlanStatus(StrEnum):
    NOTHING = "The required_hours were zero, so the run plan is empty."
    FAILED = "Unable to create the run plan. Could not allocate all required priority hours."
    PARTIAL = "The run plan was only partially filled, but the priority hours were allocated."
    READY = "The run plan was filled successfully."


# Enumerate the overall system state
class SystemState(StrEnum):
    DATE_OFF = "DateOff condition met for today"
    INPUT_OVERRIDE = "Input has overridden the mode"
    APP_OVERRIDE = "App has overridden the mode"
    AUTO = "Automatic control based on schedule or best price"


# Override modes for the mobile app
class AppMode(StrEnum):
    ON = "on"
    OFF = "off"
    AUTO = "auto"


class InputMode(StrEnum):
    IGNORE = "Ignore"
    TURN_ON = "TurnOn"
    TURN_OFF = "TurnOff"


# Enumerate the reasons why the Output is off
class StateReasonOff(StrEnum):
    NO_RUN_PLAN = "No run plan available"
    RUN_PLAN_COMPLETE = "No more run time required today"
    INACTIVE_RUN_PLAN = "Run plan dictates that the output should be off"
    APP_MODE_OFF = "App has overridden the mode to off"
    INPUT_SWITCH_OFF = "Device input has overridden the mode to off"
    DATE_OFF = "DateOff condition met for today"
    PARENT_OFF = "Parent output is off"
    STATUS_CHANGE = "Mode remains on but the status has changed"
    DAY_END = "A new day has started"
    SHUTDOWN = "System is shutting down"


# Enumerate the reasons why the Output is on
class StateReasonOn(StrEnum):
    APP_MODE_ON = "App has overridden the mode to on"
    INPUT_SWITCH_ON = "Device input has overridden the mode to on"
    ACTIVE_RUN_PLAN = "Run plan dictates that the output should be on"


# Used to pass status data into RunHistory
@dataclass
class OutputStatusData:
    meter_reading: float
    target_hours: float | None
    current_price: float


# Define the structure for commands to be posted to Controller
@dataclass
class Command:
    kind: str
    payload: dict[str, Any]


# Lookup mode used for PowerController._find_output()
class LookupMode(StrEnum):
    ID = "id"
    NAME = "name"
    OUTPUT = "output"
    METER = "meter"
    INPUT = "input"
