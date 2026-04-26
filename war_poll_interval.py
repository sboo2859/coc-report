from datetime import datetime, timezone

from fetch_war import fetch_current_war
from schedule_war_snapshot import parse_coc_time


ERROR_INTERVAL_SECONDS = 3600
NOT_IN_WAR_INTERVAL_SECONDS = 7200
PREPARATION_INTERVAL_SECONDS = 3600
IN_WAR_INTERVAL_SECONDS = 1800
UNDER_THREE_HOURS_INTERVAL_SECONDS = 900
UNDER_ONE_HOUR_INTERVAL_SECONDS = 600
WAR_ENDED_INTERVAL_SECONDS = 3600


def next_poll_seconds(war):
    state = war.get("state")

    if state == "notInWar":
        return NOT_IN_WAR_INTERVAL_SECONDS
    if state == "preparation":
        return PREPARATION_INTERVAL_SECONDS
    if state == "warEnded":
        return WAR_ENDED_INTERVAL_SECONDS
    if state != "inWar":
        return ERROR_INTERVAL_SECONDS

    end_time_text = war.get("endTime")
    if not end_time_text:
        return ERROR_INTERVAL_SECONDS

    try:
        end_time = parse_coc_time(end_time_text)
    except ValueError:
        return ERROR_INTERVAL_SECONDS

    seconds_left = (end_time - datetime.now(timezone.utc)).total_seconds()
    if seconds_left <= 3600:
        return UNDER_ONE_HOUR_INTERVAL_SECONDS
    if seconds_left <= 10800:
        return UNDER_THREE_HOURS_INTERVAL_SECONDS
    return IN_WAR_INTERVAL_SECONDS


def main():
    try:
        war, _status_code = fetch_current_war()
    except Exception:
        print(ERROR_INTERVAL_SECONDS)
        return

    print(next_poll_seconds(war))


if __name__ == "__main__":
    main()
