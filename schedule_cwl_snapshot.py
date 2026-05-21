import json
import os
import time
from datetime import datetime, timezone

from fetch_war import fetch_current_league_group, fetch_cwl_war


STATE_FILE = os.environ.get("CWL_STATE_FILE", "data/state/saved_cwl_wars.json")
CWL_WAR_DIR = os.environ.get("CWL_RESULTS_DIR", "data/cwl_war_results")
DEFAULT_CWL_POLL_MINUTES = 30


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

    if minutes <= 0:
        log(f"Invalid {name}={value!r}; using {default} minutes.")
        return default

    return minutes


def sleep_minutes(minutes):
    seconds = minutes * 60
    log(f"Sleeping for {int(seconds)} seconds.")
    time.sleep(seconds)


def load_saved_cwl_wars():
    if not os.path.exists(STATE_FILE):
        return set()

    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Could not read saved CWL state: {exc}. Starting with empty state.")
        return set()

    return set(state.get("saved_cwl_wars", []))


def write_saved_cwl_wars(saved_wars):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp_file = f"{STATE_FILE}.tmp"
    with open(tmp_file, "w") as f:
        json.dump({"saved_cwl_wars": sorted(saved_wars)}, f, indent=2)
    os.replace(tmp_file, STATE_FILE)


def safe_tag_for_filename(tag):
    return tag.strip("#").replace("/", "_") or "unknown"


def save_cwl_war_snapshot(war, war_tag, season=None, round_index=None):
    os.makedirs(CWL_WAR_DIR, exist_ok=True)
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
    safe_tag = safe_tag_for_filename(war_tag)
    filename = os.path.join(CWL_WAR_DIR, f"cwl_war_{timestamp}_{safe_tag}.json")

    payload = dict(war)
    payload["_cwl"] = {
        "warTag": war_tag,
        "season": season,
        "round": round_index,
        "capturedAt": captured_at,
    }

    with open(filename, "w") as f:
        json.dump(payload, f, indent=2)

    return filename


def iter_war_tags(group):
    rounds = group.get("rounds", [])
    if not isinstance(rounds, list):
        return

    for round_index, round_data in enumerate(rounds, start=1):
        war_tags = round_data.get("warTags", [])
        if not isinstance(war_tags, list):
            continue

        for war_tag in war_tags:
            if not war_tag or war_tag == "#0":
                continue
            yield round_index, war_tag


def fetch_group_safely():
    try:
        group, _status_code = fetch_current_league_group()
    except Exception as exc:
        log(f"Could not fetch CWL league group: {exc}")
        return None
    return group


def fetch_cwl_war_safely(war_tag):
    try:
        war, _status_code = fetch_cwl_war(war_tag)
    except Exception as exc:
        log(f"Could not fetch CWL war {war_tag}: {exc}")
        return None
    return war


def capture_finished_cwl_wars(saved_wars):
    group = fetch_group_safely()
    if group is None:
        return

    state = group.get("state", "unknown")
    season = group.get("season")
    log(f"Current CWL league group state: {state}")

    if state == "notInWar":
        return

    for round_index, war_tag in iter_war_tags(group):
        if war_tag in saved_wars:
            continue

        war = fetch_cwl_war_safely(war_tag)
        if war is None:
            continue

        war_state = war.get("state", "unknown")
        log(f"CWL round {round_index} war {war_tag} state: {war_state}")

        if war_state != "warEnded":
            continue

        filename = save_cwl_war_snapshot(
            war,
            war_tag=war_tag,
            season=season,
            round_index=round_index,
        )
        saved_wars.add(war_tag)
        write_saved_cwl_wars(saved_wars)
        log(f"Saved CWL war snapshot: {filename}")


def run_scheduler():
    poll_minutes = env_minutes("CWL_POLL_MINUTES", DEFAULT_CWL_POLL_MINUTES)
    saved_wars = load_saved_cwl_wars()

    log("Starting CWL snapshot scheduler.")
    log(f"CWL poll interval: {poll_minutes:g} minutes.")

    while True:
        capture_finished_cwl_wars(saved_wars)
        sleep_minutes(poll_minutes)


def main():
    try:
        run_scheduler()
    except KeyboardInterrupt:
        log("CWL snapshot scheduler stopped.")


if __name__ == "__main__":
    main()
