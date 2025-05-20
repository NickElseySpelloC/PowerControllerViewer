"""AmberPowerController web app based on Flask."""
from pathlib import Path

from flask import Flask, request, send_from_directory

from utility import ConfigManager, UtilityFunctions
from views import register_utility_func, views

# Initialize the Flask application
# Import the views module

app = Flask(__name__)
app.register_blueprint(views, url_prefix="/")

# Create an instance of ConfigManager
system_config = ConfigManager()

# Create an instance of the PowerControllerState
utility_funcs = UtilityFunctions(system_config)

# Register the PowerControllerState with the views module
register_utility_func(utility_funcs)

@app.errorhandler(404)
def handle_404_error():
    """Handle 404 errors and log the requested URL."""
    requested_url = request.url  # Get the URL that caused the 404 error
    utility_funcs.log_message(f"Server error 404: The requested URL was not found: {requested_url}", "detailed")
    return "Invalid URL.", 404

@app.errorhandler(Exception)
def handle_exception(e):
    """Handle all uncaught exceptions."""
    # Log the exception (optional)
    error_message = utility_funcs.report_fatal_error(f"An error occurred: {e!s}", report_stack=True)

    # Return a custom error response
    return utility_funcs.generate_html_page(error_message), 500

@app.route("/favicon.ico")
def favicon():
    """Serve the favicon."""
    return send_from_directory(str(Path(app.root_path) / "static"), "favicon.ico", mimetype="image/vnd.microsoft.icon")

if __name__ == "__main__":
    HostingIP = utility_funcs.config["Website"]["HostingIP"] or "127.0.0.1"
    HostingPort = utility_funcs.config["Website"]["Port"] or 8000
    DebugMode = utility_funcs.config["Website"]["DebugMode"] or False

    utility_funcs.log_message(f"Starting the PowerController web application on {HostingIP}:{HostingPort} for process ID {utility_funcs.get('process_id')}", "summary")
    app.run(debug=DebugMode, host=HostingIP, port=HostingPort)
