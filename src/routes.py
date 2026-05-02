"""FastAPI route handlers: GET pages, WebSocket, and POST /api/submit."""
import asyncio
import contextlib
import logging
import traceback

from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ingest import handle_submit
from view_models.home import build_home_device_ws, build_home_view
from view_models.lighting import (
    build_lighting_daily_view,
    build_lighting_view,
    build_lighting_ws_update,
)
from view_models.metering import build_metering_view, validate_metering_args
from view_models.power import (
    build_power_daily_view,
    build_power_view,
    build_power_ws_update,
)
from view_models.temp_probes import build_temp_probes_view, build_temp_probes_ws_update

log = logging.getLogger(__name__)


def register_routes(app, templates: Jinja2Templates, config, logger, state_store, ws_manager):
    """Attach all routes to the FastAPI app instance."""
    # ── Helpers ───────────────────────────────────────────────────────────────

    def _key() -> str | None:
        return config.get("Website", "AccessKey")

    def _refresh() -> int:
        return int(config.get("Website", "PageAutoRefresh") or 0)

    def _debug() -> bool:
        return bool(config.get("Website", "DebugMode"))

    def _check_key(request: Request) -> bool:
        required = _key()
        if required is None:
            return True
        return request.query_params.get("key") == required

    def _resolve_state_idx(request: Request) -> tuple[int | None, int | None]:
        """Return (state_idx, next_idx) from query params; wraps/defaults correctly."""
        states = state_store.get_all_states()
        # Attach _idx so view models can read it
        for i, s in enumerate(states):
            s["_idx"] = i

        n = len(states)
        if n == 0:
            return None, None

        idx_str = request.query_params.get("state_idx")
        name_str = request.query_params.get("state_name")

        if idx_str is not None:
            try:
                idx = int(idx_str)
            except ValueError:
                idx = 0
        elif name_str is not None:
            found = state_store.get_index_by_url_name(name_str)
            idx = found if found is not None else 0
        else:
            idx = 0

        # Wrap out-of-range
        idx = max(0, min(idx, n - 1))
        next_idx = (idx + 1) % n if n > 1 else None
        return idx, next_idx

    def _resolve_day(request: Request, state_idx: int) -> tuple[int | None, int]:
        """Return (day_idx, max_day) from query params."""
        state = state_store.get_by_index(state_idx)
        if not state:
            return None, 0
        stype = state.get("StateFileType", "PowerController")
        if stype == "PowerController":
            daily = ((state.get("Output") or {}).get("RunHistory") or {}).get("DailyData") or []
            max_day = len(daily) - 1
        elif stype == "LightingControl":
            max_day = len(state.get("SwitchEvents") or []) - 1
        else:
            return None, 0

        if max_day < 0:
            return None, 0

        day_str = request.query_params.get("day")
        if day_str is None:
            return 0, max_day
        try:
            day = int(day_str)
        except ValueError:
            return 0, max_day
        return max(0, min(day, max_day)), max_day

    def _all_states_indexed() -> list[dict]:
        states = state_store.get_all_states()
        for i, s in enumerate(states):
            s["_idx"] = i
        return states

    def _debug_message() -> str | None:
        if _debug() and config.get("Files", "LogFileVerbosity") == "all":
            n = state_store.count()
            return f"Devices: {n} | Log level: all"
        return None

    # ── GET / ────────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse, name="home")
    def home(request: Request):
        if not _check_key(request):
            return HTMLResponse("Access forbidden.", status_code=403)

        all_states = _all_states_indexed()
        if not all_states:
            return templates.TemplateResponse(request, "no_state.html", {"home_url": "/"})

        page_data = build_home_view(all_states, _key(), _refresh())
        return templates.TemplateResponse(request, "home.html", {"page_data": page_data})

    # ── GET /summary ─────────────────────────────────────────────────────────

    @app.get("/summary", response_class=HTMLResponse, name="summary")
    def summary(request: Request):
        if not _check_key(request):
            return HTMLResponse("Access forbidden.", status_code=403)

        state_idx, next_idx = _resolve_state_idx(request)
        if state_idx is None:
            return templates.TemplateResponse(request, "no_state.html", {"home_url": "/"})

        state = state_store.get_by_index(state_idx)
        if not state:
            return RedirectResponse(url="/")

        all_states = _all_states_indexed()
        key = _key()
        refresh = _refresh()
        dbg = _debug_message()
        stype = state.get("StateFileType", "PowerController")

        if stype == "PowerController":
            page_data = build_power_view(state, state_idx, next_idx, all_states, key, refresh, dbg)
            return templates.TemplateResponse(request, "summary_power.html", {"page_data": page_data})

        if stype == "LightingControl":
            page_data = build_lighting_view(state, state_idx, next_idx, all_states, key, refresh, dbg)
            return templates.TemplateResponse(request, "summary_lightingcontrol.html", {"page_data": page_data})

        if stype == "TempProbes":
            page_data = build_temp_probes_view(state, state_idx, next_idx, all_states, key, refresh, dbg)
            return templates.TemplateResponse(request, "temp_probes.html", {"page_data": page_data})

        if stype == "OutputMetering":
            period_idx, custom_start, custom_end = validate_metering_args(state, dict(request.query_params))
            page_data = build_metering_view(state, state_idx, next_idx, all_states, key, refresh,
                                            period_idx, custom_start, custom_end, dbg)
            return templates.TemplateResponse(request, "summary_output_metering.html", {"page_data": page_data})

        return HTMLResponse(f"Unsupported state type: {stype}", status_code=500)

    # ── GET /daily ────────────────────────────────────────────────────────────

    @app.get("/daily", response_class=HTMLResponse, name="daily")
    def daily(request: Request):
        if not _check_key(request):
            return HTMLResponse("Access forbidden.", status_code=403)

        state_idx, _ = _resolve_state_idx(request)
        if state_idx is None:
            return RedirectResponse(url="/")

        state = state_store.get_by_index(state_idx)
        if not state:
            return RedirectResponse(url="/")

        day, max_day = _resolve_day(request, state_idx)
        if day is None:
            return RedirectResponse(url=f"/summary?state_idx={state_idx}")

        key = _key()
        refresh = _refresh()
        stype = state.get("StateFileType", "PowerController")

        if stype == "PowerController":
            page_data = build_power_daily_view(state, state_idx, day, max_day, key, refresh)
            return templates.TemplateResponse(request, "daily_power.html", {"page_data": page_data})

        if stype == "LightingControl":
            page_data = build_lighting_daily_view(state, state_idx, day, max_day, key, refresh)
            return templates.TemplateResponse(request, "daily_lightingcontrol.html", {"page_data": page_data})

        return RedirectResponse(url=f"/summary?state_idx={state_idx}")

    # ── POST /api/submit ──────────────────────────────────────────────────────

    @app.post("/api/submit", name="submit")
    async def submit(request: Request):
        required = _key()
        if required is not None and request.query_params.get("key") != required:
            logger.log_message("Submit: invalid access key", "warning")
            return JSONResponse({"error": "Access forbidden."}, status_code=403)
        return await handle_submit(request, state_store, logger)

    # ── WebSocket /ws ─────────────────────────────────────────────────────────

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        required = _key()
        if required is not None:
            key_param = websocket.query_params.get("key")
            if key_param != required:
                await websocket.close(code=1008)
                return

        await ws_manager.connect(websocket)
        q = state_store.subscribe()

        # Send initial home-page snapshot so clients can update immediately
        with contextlib.suppress(Exception):
            all_states = _all_states_indexed()
            await ws_manager.send(websocket, {
                "type": "initial",
                "devices": [build_home_device_ws(s) for s in all_states],
            })

        async def _sender():
            """Read state-change notifications and push them to this connection only."""
            while True:
                notification = await q.get()
                try:
                    # Handle deletion notifications
                    if notification.startswith("__deleted__:"):
                        deleted_name = notification[len("__deleted__:"):]
                        await websocket.send_json({
                            "type": "device_deleted",
                            "device_name": deleted_name,
                        })
                        continue

                    device_name = notification
                    state = state_store.get_by_device_name(device_name)
                    if not state:
                        continue
                    stype = state.get("StateFileType")
                    msg: dict = {
                        "type": "state_update",
                        "device_name": device_name,
                        "state_file_type": stype,
                        "home_device": build_home_device_ws(state),
                    }
                    if stype == "PowerController":
                        msg["summary"] = build_power_ws_update(state)
                    elif stype == "LightingControl":
                        msg["summary"] = build_lighting_ws_update(state)
                    elif stype == "TempProbes":
                        msg["summary"] = build_temp_probes_ws_update(state)
                    log.debug("WS send: %s → %s", msg["type"], device_name)
                    await websocket.send_json(msg)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("WS sender error (notification=%r)", notification)

        sender_task = asyncio.create_task(_sender())
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            sender_task.cancel()
            ws_manager.disconnect(websocket)
            state_store.unsubscribe(q)

    # ── Error handlers ────────────────────────────────────────────────────────

    @app.exception_handler(404)
    async def not_found(request: Request, _exc: Exception):
        logger.log_message(f"404: {request.url}", "detailed")
        return HTMLResponse("Page not found.", status_code=404)

    @app.exception_handler(Exception)
    async def server_error(_request: Request, exc: Exception):
        tb = traceback.format_exc()
        logger.log_message(f"Unhandled exception: {exc}\n{tb}", "error")
        return HTMLResponse(f"Internal server error: {exc}", status_code=500)
