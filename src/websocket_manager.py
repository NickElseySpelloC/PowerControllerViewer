"""WebSocket connection manager."""
import contextlib
import datetime as dt
import json
import logging

from fastapi import WebSocket
from fastapi.websockets import WebSocketState

log = logging.getLogger(__name__)


def _ws_dumps(data: dict) -> str:
    """JSON-serialise a WebSocket payload, converting Python date/time types to strings."""
    def _default(obj):
        if isinstance(obj, dt.datetime):
            return obj.isoformat()
        if isinstance(obj, dt.date):
            return obj.isoformat()
        if isinstance(obj, dt.time):
            return obj.strftime("%H:%M:%S")
        return str(obj)
    return json.dumps(data, default=_default)


class ConnectionManager:
    """Tracks active WebSocket connections and broadcasts messages to all of them."""

    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)
        log.debug("WS client connected (%d total)", len(self._connections))

    def disconnect(self, ws: WebSocket):
        with contextlib.suppress(ValueError):
            self._connections.remove(ws)
        log.debug("WS client disconnected (%d remaining)", len(self._connections))

    async def broadcast(self, data: dict):
        """Send data to every connected client; silently drop dead connections."""
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(data)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def send(self, ws: WebSocket, data: dict):
        """Send to a single connection."""
        try:
            await ws.send_json(data)
        except Exception:  # noqa: BLE001
            self.disconnect(ws)
