"""Configuration schemas for use with the SCConfigManager class."""


class ConfigSchema:
    """Base class for configuration schemas."""

    def __init__(self):
        self.default = {
            "Website": {
                "HostingIP": None,
                "Port": "8000",
                "PageAutoRefresh": 10,
                "DebugMode": False,
                "AccessKey": None,
            },
            "Files": {
                "LogfileName": "logfile.log",
                "LogfileMaxLines": 500,
                "LogProcessID": True,
                "LogfileVerbosity": "summary",
                "ConsoleVerbosity": "summary",
            },
        }

        self.placeholders = {
            "Website": {
                "WebsiteAccessKey": "<Your website API key here>",
            },
        }

        self.validation = {
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
                    "LogfileName": {"type": "string", "required": False, "nullable": True},
                    "LogfileMaxLines": {"type": "number", "required": False, "nullable": True, "min": 0, "max": 100000},
                    "LogProcessID": {"type": "boolean", "required": False, "nullable": True},
                    "LogfileVerbosity": {"type": "string", "required": True, "allowed": ["none", "error", "warning", "summary", "detailed", "debug", "all"]},
                    "ConsoleVerbosity": {"type": "string", "required": True, "allowed": ["error", "warning", "summary", "detailed", "debug"]},
                },
            },
        }
