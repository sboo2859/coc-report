import json
import os
import time
from datetime import datetime, timedelta, timezone

from clashcommand.clash.time import parse_coc_time
from clashcommand.clash.war import stable_war_key, war_key_fields
from fetch_war import fetch_current_war, save_war_snapshot


STATE_FILE = "data/state/saved_wars.json"
SCHEDULED_WAR_FILE = "data/state/scheduled_war.json"
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


def parse_iso_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def load_scheduled_war():
    if not os.path.exists(SCHEDULED_WAR_FILE):
        return None

    try:
        with open(SCHEDULED_WAR_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Could not read scheduled war identity: {exc}. Ignoring scheduled identity.")
        return None


def write_scheduled_war(data, key, end_time, snapshot_time):
    os.makedirs(os.path.dirname(SCHEDULED_WAR_FILE), exist_ok=True)
    scheduled_war = {
        "war_key": key,
        "war_key_fields": war_key_fields(data),
        "end_time": end_time.isoformat(),
        "snapshot_time": snapshot_time.isoformat(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "war": data,
    }
    tmp_file = f"{SCHEDULED_WAR_FILE}.tmp"
    with open(tmp_file, "w") as f:
        json.dump(scheduled_war, f, indent=2)
    os.replace(tmp_file, SCHEDULED_WAR_FILE)
    log(f"Persisted scheduled war identity: war_key={key}")
    return scheduled_war


def clear_scheduled_war(expected_key=None):
    scheduled_war = load_scheduled_war()
    if expected_key and scheduled_war and scheduled_war.get("war_key") != expected_key:
        return

    try:
        os.remove(SCHEDULED_WAR_FILE)
    except FileNotFoundError:
        return
    except OSError as exc:
        log(f"Could not clear scheduled war identity: {exc}")


def war_key(data):
    return stable_war_key(data)


def save_final_snapshot(data, saved_wars, scheduled_war=None):
    live_key = war_key(data)
    scheduled_key = (scheduled_war or {}).get("war_key")
    key = live_key or scheduled_key
    used_persisted_identity = live_key is None and scheduled_key is not None

    if not key:
        log("Could not build a stable war key; skipping save to avoid duplicate snapshots.")
        return False

    if key in saved_wars:
        log(f"Already saved this war; skipping. war_key={key}")
        clear_scheduled_war(scheduled_key or key)
        return False

    snapshot_data = data
    if used_persisted_identity and scheduled_war and scheduled_war.get("war"):
        snapshot_data = scheduled_war["war"]

    filename = save_war_snapshot(snapshot_data, output_dir=FINAL_WAR_DIR, prefix="final_war")
    saved_wars.add(key)
    write_saved_wars(saved_wars)
    clear_scheduled_war(scheduled_key or key)
    log(
        "Saved final war snapshot: "
        f"war_key={key} persisted_identity_fallback={used_persisted_identity} "
        f"path={filename}"
    )
    return True


def fetch_war_safely():
    try:
        data, _status_code = fetch_current_war()
    except Exception as exc:
        log(f"Could not fetch current war: {exc}")
        return None
    return data


def resolve_due_scheduled_war(saved_wars, fallback_minutes):
    scheduled_war = load_scheduled_war()
    if not scheduled_war:
        return False

    scheduled_key = scheduled_war.get("war_key")
    snapshot_time = parse_iso_datetime(scheduled_war.get("snapshot_time"))
    if not scheduled_key or snapshot_time is None:
        log("Scheduled war identity is incomplete; clearing it.")
        clear_scheduled_war()
        return False

    sleep_seconds = (snapshot_time - datetime.now(timezone.utc)).total_seconds()
    if sleep_seconds > 0:
        log(
            "Pending scheduled war snapshot: "
            f"war_key={scheduled_key} scheduled_end_time={scheduled_war.get('end_time')} "
            f"snapshot_time={scheduled_war.get('snapshot_time')} "
            f"sleeping_for={format_duration(sleep_seconds)}"
        )
        time.sleep(sleep_seconds)

    final_data = fetch_war_safely()
    if final_data is None:
        sleep_minutes(fallback_minutes)
        return True

    final_state = final_data.get("state", "unknown")
    final_key = war_key(final_data)
    persisted_identity_fallback = final_key is None
    log(
        "Fetched scheduled final snapshot: "
        f"api_state={final_state} scheduled_war_key={scheduled_key} "
        f"scheduled_end_time={scheduled_war.get('end_time')} "
        f"persisted_identity_fallback={persisted_identity_fallback}"
    )
    save_final_snapshot(final_data, saved_wars, scheduled_war=scheduled_war)
    return True


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
    key = war_key(data)

    if not key:
        log(
            "Current war state: inWar, but a stable war key could not be built; "
            f"checking again in {fallback_minutes:g} minutes."
        )
        sleep_minutes(fallback_minutes)
        return

    scheduled_war = write_scheduled_war(data, key, end_time, snapshot_time)

    log("Current war state: inWar")
    log(f"Scheduled war key: {key}")
    log(f"War ends at: {format_datetime(end_time)}")
    log(f"Scheduling final snapshot for: {format_datetime(snapshot_time)}")
    log(f"Sleeping for: {format_duration(sleep_seconds)}")
    time.sleep(sleep_seconds)

    final_data = fetch_war_safely()
    if final_data is None:
        sleep_minutes(fallback_minutes)
        return

    final_state = final_data.get("state", "unknown")
    final_key = war_key(final_data)
    persisted_identity_fallback = final_key is None
    log(
        "Fetched scheduled final snapshot: "
        f"api_state={final_state} scheduled_war_key={scheduled_war.get('war_key')} "
        f"scheduled_end_time={scheduled_war.get('end_time')} "
        f"persisted_identity_fallback={persisted_identity_fallback}"
    )
    save_final_snapshot(final_data, saved_wars, scheduled_war=scheduled_war)


def run_scheduler():
    buffer_minutes = env_minutes("WAR_END_BUFFER_MINUTES", DEFAULT_BUFFER_MINUTES)
    prep_poll_minutes = env_minutes("WAR_PREP_POLL_MINUTES", DEFAULT_PREP_POLL_MINUTES)
    idle_poll_minutes = env_minutes("WAR_IDLE_POLL_MINUTES", DEFAULT_IDLE_POLL_MINUTES)
    ended_poll_minutes = env_minutes("WAR_ENDED_POLL_MINUTES", DEFAULT_ENDED_POLL_MINUTES)
    saved_wars = load_saved_wars()

    log("Starting war snapshot scheduler.")
    log(f"Final snapshot buffer: {buffer_minutes:g} minutes.")

    while True:
        if resolve_due_scheduled_war(saved_wars, prep_poll_minutes):
            continue

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
