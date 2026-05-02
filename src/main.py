"""PowerControllerViewer — FastAPI application entry point."""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Ensure the src/ directory is on sys.path for sibling imports
sys.path.insert(0, str(Path(__file__).parent))

import asyncio

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sc_foundation import SCConfigManager, SCLogger

from config_schemas import ConfigSchema
from housekeeping import housekeeping_loop
from routes import register_routes
from state_store import StateStore
from websocket_manager import ConnectionManager

CONFIG_FILE = "config.yaml"

# ── Module-level singletons (initialised before lifespan) ────────────────────
_schemas = ConfigSchema()

try:
    config = SCConfigManager(
        config_file=CONFIG_FILE,
        default_config=_schemas.default,
        validation_schema=_schemas.validation,
        placeholders=_schemas.placeholders,
    )
except RuntimeError as e:
    print(f"Configuration error: {e}", file=sys.stderr)
    sys.exit(1)

try:
    logger = SCLogger(config.get_logger_settings())
except RuntimeError as e:
    print(f"Logger init error: {e}", file=sys.stderr)
    sys.exit(1)

logger.register_email_settings(config.get_email_settings())

state_store = StateStore(logger)
ws_manager = ConnectionManager()

# ── Lifespan (startup / shutdown) ────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.log_message("PowerControllerViewer starting up", "summary")
    await state_store.load_from_disk()
    hk_task = asyncio.create_task(housekeeping_loop(config, logger, state_store))
    yield
    hk_task.cancel()
    logger.log_message("PowerControllerViewer shut down", "summary")


# ── FastAPI app ───────────────────────────────────────────────────────────────

_src_dir = Path(__file__).parent
_root_dir = _src_dir.parent

app = FastAPI(title="PowerControllerViewer", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(_root_dir / "static")), name="static")

templates = Jinja2Templates(directory=str(_root_dir / "templates"))

register_routes(app, templates, config, logger, state_store, ws_manager)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    host = config.get("Website", "HostingIP") or "127.0.0.1"
    port = int(config.get("Website", "Port") or 8000)
    debug = bool(config.get("Website", "DebugMode") or False)

    logger.log_message(f"Listening on {host}:{port} (debug={debug})", "summary")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=debug,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
