"""Single-process in-memory state store with async change notification."""
import asyncio
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sc_foundation import DateHelper, JSONEncoder, SCCommon


class StateStore:
    """Holds all device states in memory, notifies WebSocket subscribers on update."""

    def __init__(self, logger):
        self._logger = logger
        self._states: dict[str, dict] = {}  # DeviceName -> processed state
        self._queues: list[asyncio.Queue] = []
        self.state_data_dir: Path = self._resolve_state_dir()

    # ── Path resolution ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_state_dir() -> Path:
        p = SCCommon.select_file_location("state_data/test.json")
        if p:
            return p.parent
        return Path("state_data")

    # ── Startup loading ──────────────────────────────────────────────────────

    async def load_from_disk(self):
        """Load all JSON state files from disk on startup."""
        if not self.state_data_dir.exists():
            self._logger.log_message(f"State data directory not found: {self.state_data_dir}", "warning")
            return
        json_files = sorted(
            f for f in self.state_data_dir.iterdir()
            if f.is_file() and f.name.endswith(".json") and not f.name.startswith(".")
        )
        for file_path in json_files:
            try:
                state = self._read_and_process(file_path)
                if state:
                    state["_file_mtime"] = file_path.stat().st_mtime
                    self._states[state["DeviceName"]] = state
                    self._logger.log_message(f"Loaded state file: {file_path.name}", "debug")
            except Exception as e:  # noqa: BLE001
                self._logger.log_message(f"Error loading {file_path}: {e}", "error")
        self._logger.log_message(f"Loaded {len(self._states)} state files from disk.", "summary")

    # ── File I/O ─────────────────────────────────────────────────────────────

    def _read_and_process(self, file_path: Path) -> dict | None:
        with file_path.open("r", encoding="utf-8") as f:
            if f.seek(0, 2) == 0:
                return None
            f.seek(0)
            raw = json.load(f)
        if not isinstance(raw, dict):
            return None
        decoded = JSONEncoder.decode_object(raw)
        return self._enrich(decoded)

    @staticmethod
    def _safe_write(file_path: Path, data: dict):
        """Atomic write via temp file."""
        tmp = file_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
        tmp.replace(file_path)

    # ── State enrichment ─────────────────────────────────────────────────────

    @staticmethod
    def _enrich(state: dict) -> dict:
        """Add LocalLastSaveTime, DeviceDescription, StateURLName to a decoded state dict."""
        state_type = state.get("StateFileType", "PowerController")

        if state_type == "LightingControl":
            last_save = state.get("LastStateSaveTime")
            description = "Lighting Controller"
        elif state_type == "PowerController":
            last_save = state.get("SaveTime")
            output_type = (state.get("Output") or {}).get("Type", "")
            if output_type == "teslamate":
                description = "Tesla Charging"
            elif output_type == "meter":
                description = "Energy Meter"
            else:
                description = "Power Controller"
        elif state_type == "TempProbes":
            last_save = state.get("SaveTime")
            description = "Temperature Probes"
        elif state_type == "OutputMetering":
            last_save = state.get("SaveTime")
            description = "Metered Outputs"
        else:
            last_save = None
            description = "Unknown Device"

        if last_save is None:
            last_save = DateHelper.now()
        if isinstance(last_save, dt.datetime):
            last_save = last_save.astimezone()

        device_name = state.get("DeviceName", "Device")
        stripped = device_name.replace(" ", "").replace("/", "").replace("\\", "").replace("-", "")
        state["LocalLastSaveTime"] = last_save
        state["DeviceDescription"] = description
        state["StateURLName"] = quote(stripped)
        return state

    # ── Public write API ─────────────────────────────────────────────────────

    async def save_and_update(self, raw_state: dict) -> dict:
        """Persist raw JSON state to disk, update in-memory store, notify subscribers."""
        device_name = raw_state["DeviceName"]
        file_path = self.state_data_dir / f"{device_name}.json"

        self._safe_write(file_path, raw_state)

        decoded = JSONEncoder.decode_object(raw_state)
        state = self._enrich(decoded)
        state["_file_mtime"] = file_path.stat().st_mtime
        self._states[device_name] = state

        await self._notify(device_name)
        self._logger.log_message(f"State updated for device: {device_name}", "debug")
        return state

    # ── Public read API ──────────────────────────────────────────────────────

    def get_all_states(self) -> list[dict]:
        """All device states sorted by DeviceName."""
        return sorted(self._states.values(), key=lambda s: s.get("DeviceName", ""))

    def get_by_device_name(self, device_name: str) -> dict | None:
        return self._states.get(device_name)

    def get_by_index(self, idx: int) -> dict | None:
        states = self.get_all_states()
        return states[idx] if 0 <= idx < len(states) else None

    def get_index_by_url_name(self, url_name: str) -> int | None:
        for i, s in enumerate(self.get_all_states()):
            if s.get("StateURLName") == url_name:
                return i
        return None

    def count(self) -> int:
        return len(self._states)

    # ── Housekeeping ─────────────────────────────────────────────────────────

    def delete_old_files(self, max_age_hours: int):
        cutoff = time.time() - max_age_hours * 3600
        for device_name in list(self._states):
            file_path = self.state_data_dir / f"{device_name}.json"
            if file_path.exists() and file_path.stat().st_mtime < cutoff:
                try:
                    file_path.unlink()
                    del self._states[device_name]
                    self._logger.log_message(f"Deleted old state file: {file_path.name}", "debug")
                except OSError as e:
                    self._logger.log_message(f"Error deleting {file_path}: {e}", "error")

    async def check_external_changes(self):
        """Pick up state files that were modified outside the app (e.g. manual drops)."""
        if not self.state_data_dir.exists():
            return
        for file_path in self.state_data_dir.iterdir():
            if not file_path.is_file() or not file_path.name.endswith(".json") or file_path.name.startswith("."):
                continue
            device_name = file_path.stem
            mtime = file_path.stat().st_mtime
            existing = self._states.get(device_name)
            if existing is None or mtime > existing.get("_file_mtime", 0):
                try:
                    state = self._read_and_process(file_path)
                    if state:
                        state["_file_mtime"] = mtime
                        self._states[device_name] = state
                        await self._notify(device_name)
                        self._logger.log_message(f"Reloaded externally changed: {file_path.name}", "debug")
                except Exception as e:  # noqa: BLE001
                    self._logger.log_message(f"Error reloading {file_path}: {e}", "error")

    # ── Subscriber notification ───────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    async def _notify(self, device_name: str):
        for q in list(self._queues):
            try:
                q.put_nowait(device_name)
            except asyncio.QueueFull:
                pass
