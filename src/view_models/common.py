"""Shared helpers used across all view model builders."""
import datetime as dt


def nav_url(path: str, key: str | None, **params) -> str:
    """Build a URL with optional query parameters and access key."""
    parts = [f"{k}={v}" for k, v in params.items() if v is not None]
    if key:
        parts.append(f"key={key}")
    return f"{path}?{'&'.join(parts)}" if parts else path


def hours_to_string(hours: float | None) -> str:
    """Convert a float number of hours to 'H:MM' string."""
    if hours is None:
        return "0:00"
    neg = "-" if hours < 0 else ""
    h = int(abs(hours))
    m = int((abs(hours) - h) * 60)
    return f"{neg}{h}:{m:02}"


def format_date_with_ordinal(date: dt.date | dt.datetime | None, show_time: bool = False) -> str:
    """Format a date as '1st May' or '1st May 12:00:00'."""
    if date is None:
        return "—"
    day = date.day
    if 11 <= day <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    result = date.strftime(f"%-d{suffix} %B")
    if show_time and isinstance(date, dt.datetime):
        result += date.strftime(" %H:%M:%S")
    return result


def fmt_time(t: dt.datetime | dt.time | None) -> str:
    """Return HH:MM string or empty string if None."""
    if t is None:
        return ""
    return t.strftime("%H:%M")
