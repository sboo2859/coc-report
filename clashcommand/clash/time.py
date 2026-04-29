from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


COC_TIME_FORMAT = "%Y%m%dT%H%M%S.%fZ"
CENTRAL_TIMEZONE_NAME = "America/Chicago"


def parse_coc_time(value):
    """Parse a Clash API UTC timestamp into an aware datetime."""
    return datetime.strptime(value, COC_TIME_FORMAT).replace(tzinfo=timezone.utc)


def parse_optional_coc_time(value):
    if not value:
        return None

    try:
        return parse_coc_time(value)
    except (TypeError, ValueError):
        return None


def central_timezone():
    if ZoneInfo is None:
        return timezone.utc

    try:
        return ZoneInfo(CENTRAL_TIMEZONE_NAME)
    except Exception:
        return timezone.utc


def format_central_time(value):
    if value is None:
        return "Unavailable"

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    local_value = value.astimezone(central_timezone())
    hour_text = local_value.strftime("%I").lstrip("0") or "0"
    return f"{local_value.strftime('%Y-%m-%d')} {hour_text}:{local_value.strftime('%M %p')} CT"
