from datetime import datetime, timezone


COC_TIME_FORMAT = "%Y%m%dT%H%M%S.%fZ"


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

