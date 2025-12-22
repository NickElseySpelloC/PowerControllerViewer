"""PowerControllerViewer web app based."""
import signal
import sys
from pathlib import Path

from flask import Flask, request, send_from_directory
from sc_utility import SCConfigManager, SCLogger

from config_schemas import ConfigSchema
from helper import PowerControllerViewer
from views import register_support_classes, views

CONFIG_FILE = "config.yaml"

# Define globals for config and logger classes
config = None
logger = None
helper = None

# Initialize the Flask application
# Import the views module

app = Flask(__name__, template_folder="../templates", static_folder="../static")
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


def _graceful_shutdown(sig: int, frame) -> None:  # noqa: ARG001
    """Handle SIGINT/SIGTERM for clean shutdown."""
    global logger, helper  # noqa: PLW0602
    try:  # noqa: PLR1702
        if logger is not None:
            logger.log_message(f"Received signal {sig}. Shutting down gracefully...", "summary")

        # Shutdown the state loader worker thread
        if helper is not None:
            PowerControllerViewer.shutdown_worker()

        # Try common shutdown methods on helper
        if helper is not None:
            for method_name in ("shutdown", "stop", "shutdown_threads", "stop_threads", "close"):
                if hasattr(helper, method_name):
                    try:
                        getattr(helper, method_name)()
                        if logger is not None:
                            logger.log_message(f"Invoked helper.{method_name}()", "detailed")
                        break
                    except Exception as e:  # noqa: BLE001
                        if logger is not None:
                            logger.log_message(f"Error during helper.{method_name}(): {e!s}", "detailed")
    finally:
        # Exit to stop Flask dev server loop cleanly
        sys.exit(0)


def _register_signal_handlers() -> None:
    """Register SIGINT/SIGTERM handlers."""
    try:
        signal.signal(signal.SIGINT, _graceful_shutdown)
        signal.signal(signal.SIGTERM, _graceful_shutdown)
    except Exception:  # noqa: BLE001
        # Some environments (e.g., threads or certain servers) may restrict signals
        if logger is not None:
            logger.log_message("Could not register signal handlers.", "detailed")


def create_app():
    """Create and configure the Flask application.

    Returns:
        The configured Flask application instance.
    """
    # Get our default schema, validation schema, and placeholders
    global config, logger, helper   # noqa: PLW0603, pylint: disable=global-statement
    schemas = ConfigSchema()

    # Initialize the SC_ConfigManager class
    try:
        config = SCConfigManager(
            config_file=CONFIG_FILE,
            default_config=schemas.default,
            validation_schema=schemas.validation,
            placeholders=schemas.placeholders
        )
    except RuntimeError as e:
        print(f"Configuration file error: {e}", file=sys.stderr)
        sys.exit(1)     # Exit with errorcode 1 so that launch.sh can detect it

    # Initialize the SC_Logger class
    try:
        logger = SCLogger(config.get_logger_settings())
    except RuntimeError as e:
        print(f"Logger initialisation error: {e}", file=sys.stderr)
        sys.exit(1)     # Exit with errorcode 1 so that launch.sh can detect it

    logger.log_message("\n\nStarting the PowerController web application", "summary")

    # Setup email
    logger.register_email_settings(config.get_email_settings())

    # Create the PowerControllerViewer class
    helper = PowerControllerViewer(config, logger)

    # Register the support functions with the views module
    register_support_classes(config, logger, helper)

    # Register signal handlers for clean shutdown
    _register_signal_handlers()

    return app


def main_loop():
    """Main function to run the Flask application directly."""
    create_app()
    assert config is not None, "Configuration instance is not initialized."
    assert logger is not None, "Logger instance is not initialized."
    hosting_ip = config.get("Website", "HostingIP", default="127.0.0.1")
    hosting_port = config.get("Website", "Port", default=8000)
    debug_mode = config.get("Website", "DebugMode", default=False) or False

    logger.log_message(f"PowerController web application running on {hosting_ip}:{hosting_port}", "detailed")

    try:
        app.run(debug=debug_mode, host=hosting_ip, port=hosting_port)  # type: ignore[call-arg]
    except KeyboardInterrupt:
        # Fallback if signals not registered or on dev server Ctrl-C
        _graceful_shutdown(signal.SIGINT, None)


# Initialize the app when the module is imported (for Gunicorn)
if config is None:
    create_app()

if __name__ == "__main__":
    main_loop()
