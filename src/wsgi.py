"""Used to launch the app from systemd or directly."""
from src.main import app  # adjust import if your app file has a different name

if __name__ == "__main__":
    app.run()
