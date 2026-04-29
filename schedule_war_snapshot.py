import json
import os
import time
from datetime import datetime, timedelta, timezone

from clashcommand.clash.time import parse_coc_time
from clashcommand.clash.war import stable_war_key
from fetch_war import fetch_current_war, save_war_snapshot


STATE_FILE = "data/state/saved_wars.json"
FINAL_WAR_DIR = os.environ.get("WAR_RESULTS_DIR", "data/war_results")
DEFAULT_BUFFER_MINUTES = 2
DEFAULT_PREP_POLL_MINUTES = 30
DEFAULT_IDLE_POLL_MINUTES = 60
DEFAULT_ENDED_POLL_MINUTES = 30


def log(message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {message}", flush=True)


def env_minutes(name, default):
    value = os.environ.get(name, "").strip()
    if not value:
        return default

    try:
        minutes = float(value)
    except ValueError:
        log(f"Invalid {name}={value!r}; using {default} minutes.")
        return default

    if minutes < 0:
        log(f"Invalid {name}={value!r}; using {default} minutes.")
        return default

    return minutes


def format_datetime(value):
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_duration(seconds):
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def sleep_minutes(minutes):
    seconds = minutes * 60
    log(f"Sleeping for: {format_duration(seconds)}")
    time.sleep(seconds)


def load_saved_wars():
    if not os.path.exists(STATE_FILE):
        return set()

    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Could not read saved war state: {exc}. Starting with empty state.")
        return set()

    return set(state.get("saved_wars", []))


def write_saved_wars(saved_wars):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp_file = f"{STATE_FILE}.tmp"
    with open(tmp_file, "w") as f:
        json.dump({"saved_wars": sorted(saved_wars)}, f, indent=2)
    os.replace(tmp_file, STATE_FILE)


def war_key(data):
    return stable_war_key(data)


def save_final_snapshot(data, saved_wars):
    key = war_key(data)
    if not key:
        log("Could not build a stable war key; skipping save to avoid duplicate snapshots.")
        return False

    if key in saved_wars:
        log("Already saved this war; skipping.")
        return False

    filename = save_war_snapshot(data, output_dir=FINAL_WAR_DIR, prefix="final_war")
    saved_wars.add(key)
    write_saved_wars(saved_wars)
    log(f"Saved final war snapshot: {filename}")
    return True


def fetch_war_safely():
    try:
        data, _status_code = fetch_current_war()
    except Exception as exc:
        log(f"Could not fetch current war: {exc}")
        return None
    return data


def handle_in_war(data, saved_wars, buffer_minutes, fallback_minutes):
    end_time_text = data.get("endTime")
    if not end_time_text:
        log(f"Current war state: inWar, but endTime is missing; checking again in {fallback_minutes:g} minutes.")
        sleep_minutes(fallback_minutes)
        return

    try:
        end_time = parse_coc_time(end_time_text)
    except ValueError:
        log(
            f"Current war state: inWar, but endTime is malformed ({end_time_text!r}); "
            f"checking again in {fallback_minutes:g} minutes."
        )
        sleep_minutes(fallback_minutes)
        return

    snapshot_time = end_time + timedelta(minutes=buffer_minutes)
    now = datetime.now(timezone.utc)
    sleep_seconds = max(0, (snapshot_time - now).total_seconds())

    log("Current war state: inWar")
    log(f"War ends at: {format_datetime(end_time)}")
    log(f"Scheduling final snapshot for: {format_datetime(snapshot_time)}")
    log(f"Sleeping for: {format_duration(sleep_seconds)}")
    time.sleep(sleep_seconds)

    final_data = fetch_war_safely()
    if final_data is None:
        sleep_minutes(fallback_minutes)
        return

    final_state = final_data.get("state", "unknown")
    log(f"Fetched scheduled final snapshot; current war state: {final_state}")
    save_final_snapshot(final_data, saved_wars)


def run_scheduler():
    buffer_minutes = env_minutes("WAR_END_BUFFER_MINUTES", DEFAULT_BUFFER_MINUTES)
    prep_poll_minutes = env_minutes("WAR_PREP_POLL_MINUTES", DEFAULT_PREP_POLL_MINUTES)
    idle_poll_minutes = env_minutes("WAR_IDLE_POLL_MINUTES", DEFAULT_IDLE_POLL_MINUTES)
    ended_poll_minutes = env_minutes("WAR_ENDED_POLL_MINUTES", DEFAULT_ENDED_POLL_MINUTES)
    saved_wars = load_saved_wars()

    log("Starting war snapshot scheduler.")
    log(f"Final snapshot buffer: {buffer_minutes:g} minutes.")

    while True:
        data = fetch_war_safely()
        if data is None:
            sleep_minutes(idle_poll_minutes)
            continue

        state = data.get("state", "unknown")

        if state == "inWar":
            handle_in_war(data, saved_wars, buffer_minutes, prep_poll_minutes)
        elif state == "warEnded":
            log("Current war state: warEnded")
            save_final_snapshot(data, saved_wars)
            sleep_minutes(ended_poll_minutes)
        elif state == "preparation":
            log(f"Current war state: preparation; checking again in {prep_poll_minutes:g} minutes.")
            sleep_minutes(prep_poll_minutes)
        elif state == "notInWar":
            log(f"No active war; checking again in {idle_poll_minutes:g} minutes.")
            sleep_minutes(idle_poll_minutes)
        else:
            log(f"Current war state: {state}; checking again in {idle_poll_minutes:g} minutes.")
            sleep_minutes(idle_poll_minutes)


def main():
    try:
        run_scheduler()
    except KeyboardInterrupt:
        log("War snapshot scheduler stopped.")


if __name__ == "__main__":
    main()
