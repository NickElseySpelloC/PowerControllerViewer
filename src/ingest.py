"""POST /api/submit — ingest state data from PowerController / LightingControl devices."""
import gzip
import json
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

_REQUIRED_KEYS: dict[str, dict[str, type]] = {
    "PowerController": {
        "SaveTime": str, "SchemaVersion": int, "DeviceName": str,
        "Output": dict, "Scheduler": dict,
    },
    "LightingControl": {
        "LastStateSaveTime": str, "SchemaVersion": int, "DeviceName": str,
        "RandomOffsets": dict, "SwitchStates": list,
    },
    "TempProbes": {
        "SaveTime": str, "SchemaVersion": int, "DeviceName": str,
        "TempProbeLogging": dict,
    },
    "OutputMetering": {
        "SaveTime": str, "SchemaVersion": int, "DeviceName": str,
        "Summary": dict, "Meters": list,
    },
}


async def handle_submit(request: Request, state_store, logger) -> JSONResponse:
    """Validate and store an inbound state JSON payload."""
    if not request.headers.get("content-type", "").startswith("application/json"):
        logger.log_message("Submit: content-type is not application/json", "warning")
        return JSONResponse({"error": "Expected application/json"}, status_code=400)

    # Decompress if gzip-encoded
    if request.headers.get("content-encoding", "").lower() == "gzip":
        try:
            raw_bytes = await request.body()
            data = json.loads(gzip.decompress(raw_bytes).decode("utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.log_message(f"Submit: gzip decompression failed: {e}", "warning")
            return JSONResponse({"error": "Failed to decompress payload"}, status_code=400)
    else:
        try:
            data = await request.json()
        except Exception as e:  # noqa: BLE001
            logger.log_message(f"Submit: JSON parse error: {e}", "warning")
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    if not isinstance(data, dict):
        logger.log_message("Submit: payload is not a JSON object", "warning")
        return JSONResponse({"error": "Expected a JSON object"}, status_code=400)

    state_type = data.get("StateFileType", "PowerController")
    if state_type not in _REQUIRED_KEYS:
        logger.log_message(f"Submit: unknown StateFileType '{state_type}'", "warning")
        return JSONResponse({"error": f"Unknown StateFileType: {state_type}"}, status_code=400)

    for key, expected_type in _REQUIRED_KEYS[state_type].items():
        if key not in data:
            logger.log_message(f"Submit: missing required key '{key}'", "warning")
            return JSONResponse({"error": f"Missing required key: {key}"}, status_code=400)
        if not isinstance(data[key], expected_type):
            logger.log_message(f"Submit: key '{key}' has wrong type", "warning")
            return JSONResponse(
                {"error": f"Key '{key}' must be {expected_type.__name__}"},
                status_code=400,
            )

    device_name = data["DeviceName"]
    logger.log_message(f"Submit: received valid state from device '{device_name}'", "debug")

    await state_store.save_and_update(data)

    return JSONResponse({"message": "Data received and validated successfully."}, status_code=200)
