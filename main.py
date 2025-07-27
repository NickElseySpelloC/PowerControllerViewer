"""AmberPowerController web app based on Flask."""
import sys
from pathlib import Path

from flask import Flask, request, send_from_directory
from sc_utility import SCCommon, SCConfigManager, SCLogger

from config_schemas import ConfigSchema
from helper import AmberHelper
from views import register_support_classes, views

CONFIG_FILE = "config.yaml"

# Define globals for config and logger classes
config = None
logger = None
helper = None

# Initialize the Flask application
# Import the views module

app = Flask(__name__)
app.register_blueprint(views, url_prefix="/")


@app.errorhandler(404)
def handle_404_error(error) -> tuple[str, int]:
    """Handle 404 errors and log the requested URL.

    Args:
        error: The error object containing details about the 404 error.

    Returns:
        A tuple containing the error message and the HTTP status code.
    """
    assert logger is not None, "Logger instance is not initialized."
    requested_url = request.url  # Get the URL that caused the 404 error
    logger.log_message(f"Server error {error}: The requested URL was not found: {requested_url}", "detailed")
    return "Invalid URL.", 404


@app.errorhandler(Exception)
def handle_exception(e) -> tuple[str, int]:
    """Handle all uncaught exceptions.

    Args:
        e: The exception object containing details.

    Returns:
        A tuple containing the error message and the HTTP status code.
    """
    # Log the exception (optional)
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."
    error_message = helper.report_fatal_error(f"An error occurred: {e!s}", report_stack=True)  # type: ignore[attr-defined]

    # Return a custom error response
    return helper.generate_html_page(error_message), 500


@app.route("/favicon.ico")
def favicon():
    """Serve the favicon.

    Returns:
        The favicon file from the static directory.
    """
    return send_from_directory(str(Path(app.root_path) / "static"), "favicon.ico", mimetype="image/vnd.microsoft.icon")


def main():
    """Main function to run the Flask application."""
    # Get our default schema, validation schema, and placeholders
    global config, logger, helper   # noqa: PLW0603
    schemas = ConfigSchema()

    # Initialize the SC_ConfigManager class
    try:
        config = SCConfigManager(
            config_file=CONFIG_FILE,
            default_config=schemas.default,  # Replace with your default config if needed
            validation_schema=schemas.validation,  # Replace with your validation schema if needed
            placeholders=schemas.placeholders  # Replace with your placeholders if needed
        )
    except RuntimeError as e:
        print(f"Configuration file error: {e}", file=sys.stderr)
        return

    # Initialize the SC_Logger class
    try:
        logger = SCLogger(config.get_logger_settings())
    except RuntimeError as e:
        print(f"Logger initialisation error: {e}", file=sys.stderr)
        return

    # Setup email
    logger.register_email_settings(config.get_email_settings())

    # Create the AmberHelper class
    helper = AmberHelper(config, logger)

    # Register the support functions with the views module
    register_support_classes(config, logger, helper)
    assert logger is not None, "Logger instance is not initialized."
    assert helper is not None, "Helper instance is not initialized."

    hosting_ip = config.get("Website", "HostingIP", default="127.0.0.1")
    hosting_port = config.get("Website", "Port", default=8000)
    debug_mode = config.get("Website", "DebugMode", default=False) or False

    logger.log_message(f"Starting the PowerController web application on {hosting_ip}:{hosting_port} for process ID {SCCommon.get_process_id()}", "summary")
    app.run(debug=debug_mode, host=hosting_ip, port=hosting_port)  # type: ignore[attr-defined]


if __name__ == "__main__":
    """Run the main function to start the Flask application."""
    main()
