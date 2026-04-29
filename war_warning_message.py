import argparse
import os
import sys
from datetime import datetime, timezone

from clashcommand.clash.time import parse_coc_time
from clashcommand.clash.war import remaining_attack_members
from fetch_war import fetch_current_war


DEFAULT_TARGET_HOURS = 3
DEFAULT_INCLUDE_COUNTS = True


def env_bool(name, default):
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default

    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False

    print(f"Invalid {name}={value!r}; using {str(default).lower()}.")
    return default


def env_float(name, default):
    value = os.environ.get(name, "").strip()
    if not value:
        return default

    try:
        return float(value)
    except ValueError:
        print(f"Invalid {name}={value!r}; using {default:g}.")
        return default


def pluralize_attack(count):
    if count == 1:
        return "1 attack left"
    return f"{count} attacks left"


def format_time_left(end_time, target_hours):
    seconds_left = int((end_time - datetime.now(timezone.utc)).total_seconds())

    if seconds_left <= 0:
        return "⚠️ War reminder — war ending now."

    minutes_left = max(1, round(seconds_left / 60))
    hours = minutes_left // 60
    minutes = minutes_left % 60

    if minutes_left < 60:
        return "⚠️ War reminder — less than 1 hour left."

    if abs(minutes_left - int(target_hours * 60)) <= 10:
        rounded_hours = max(1, round(minutes_left / 60))
        label = "hour" if rounded_hours == 1 else "hours"
        return f"⚠️ War reminder — about {rounded_hours} {label} left."

    if minutes == 0:
        label = "hour" if hours == 1 else "hours"
        return f"⚠️ War reminder — about {hours} {label} left."

    return f"⚠️ War reminder — about {hours}h {minutes:02d}m left."


def members_with_remaining_attacks(war):
    return [
        (player["name"], player["remaining"])
        for player in remaining_attack_members(war)
    ]


def build_warning_message(war, include_counts=True, target_hours=DEFAULT_TARGET_HOURS):
    state = war.get("state", "unknown")
    if state != "inWar":
        return f"No active war is currently in progress.\nCurrent state: {state}"

    end_time_text = war.get("endTime")
    header = "⚠️ War reminder."
    if end_time_text:
        try:
            header = format_time_left(parse_coc_time(end_time_text), target_hours)
        except ValueError:
            pass

    remaining_members = members_with_remaining_attacks(war)
    if not remaining_members:
        return "✅ Everyone has used all available attacks."

    lines = [header, "", "Still need attacks from:"]
    for name, remaining in remaining_members:
        if include_counts:
            lines.append(f"{name} — {pluralize_attack(remaining)}")
        else:
            lines.append(name)

    lines.extend(["", "Please use your attacks before war ends."])
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="Generate a copy/paste war attacks warning message.")
    count_group = parser.add_mutually_exclusive_group()
    count_group.add_argument("--counts", action="store_true", help="Include remaining attack counts.")
    count_group.add_argument("--no-counts", action="store_true", help="Only list player names.")
    return parser.parse_args()


def main():
    args = parse_args()
    include_counts = env_bool("WAR_WARNING_INCLUDE_COUNTS", DEFAULT_INCLUDE_COUNTS)
    if args.counts:
        include_counts = True
    elif args.no_counts:
        include_counts = False

    target_hours = env_float("WAR_WARNING_TARGET_HOURS", DEFAULT_TARGET_HOURS)

    try:
        war, _status_code = fetch_current_war()
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    print(build_warning_message(war, include_counts=include_counts, target_hours=target_hours))


if __name__ == "__main__":
    main()
