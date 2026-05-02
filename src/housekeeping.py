"""Periodic async housekeeping task (config reload, log trimming, stale file deletion)."""
import asyncio
import contextlib
import logging

from sc_foundation import DateHelper

log = logging.getLogger(__name__)

_INTERVAL_SECONDS = 10


async def housekeeping_loop(config, logger, state_store):
    """Run indefinitely; performs maintenance every _INTERVAL_SECONDS seconds."""
    config_last_check = DateHelper.now()

    while True:
        await asyncio.sleep(_INTERVAL_SECONDS)
        try:
            # Reload config if it changed on disk
            try:
                ts = config.check_for_config_changes(config_last_check)
                if ts:
                    logger.initialise_settings(config.get_logger_settings())
                    email = config.get_email_settings()
                    if email:
                        logger.register_email_settings(email)
                    config_last_check = DateHelper.now()
                    log.info("Config reloaded.")
            except Exception as e:  # noqa: BLE001
                logger.log_message(f"Housekeeping: config check error: {e}", "warning")

            # Trim log file
            with contextlib.suppress(Exception):
                logger.trim_logfile()

            # Delete old state files
            try:
                max_age = config.get("Files", "DeleteOldStateFiles")
                if isinstance(max_age, (int, float)) and max_age > 0:
                    state_store.delete_old_files(int(max_age))
            except Exception as e:  # noqa: BLE001
                logger.log_message(f"Housekeeping: file deletion error: {e}", "warning")

            # Pick up externally added/modified/deleted state files
            try:
                await state_store.check_external_changes()
            except Exception as e:  # noqa: BLE001
                logger.log_message(f"Housekeeping: external change check error: {e}", "warning")

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Housekeeping loop error")
