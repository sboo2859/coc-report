import json
import os
import time
from datetime import datetime, timedelta, timezone

from clashcommand.clash.time import parse_coc_time
from clashcommand.clash.war import (
    current_war_attack_summary,
    stable_war_key,
    war_key_fields,
)
from fetch_war import fetch_current_war, save_war_snapshot


STATE_FILE = "data/state/saved_wars.json"
SCHEDULED_WAR_FILE = "data/state/scheduled_war.json"
FINAL_WAR_DIR = os.environ.get("WAR_RESULTS_DIR", "data/war_results")
DEFAULT_BUFFER_MINUTES = 2
DEFAULT_PREP_POLL_MINUTES = 30
DEFAULT_IDLE_POLL_MINUTES = 60
DEFAULT_ENDED_POLL_MINUTES = 30
# During preparation the only event we need to catch is battle-day start, so we
# sleep until startTime instead of polling every prep_poll_minutes. Capped so a
# very long prep still re-verifies periodically (in case the war is cancelled).
DEFAULT_PREP_MAX_SLEEP_MINUTES = 360
# Battle day can last ~24h. The persisted scheduled-war payload is the fallback
# used when the API has already rolled over to the next war by the time we take
# the final snapshot, so it has to be refreshed as the war progresses: a payload
# captured at battle-day start has zero attacks and would publish a "0-0 tie,
# everyone missed" recap.
DEFAULT_LIVE_REFRESH_MINUTES = 30
DEFAULT_FINAL_REFRESH_MINUTES = 5
DEFAULT_FINAL_REFRESH_WINDOW_MINUTES = 60
# A fallback payload captured longer than this before endTime cannot represent
# the finished war, so it is refused rather than published as a final result.
DEFAULT_MAX_FALLBACK_STALENESS_MINUTES = 15
# States a payload can be in and still plausibly describe a finished war.
FINAL_CAPABLE_STATES = ("warEnded", "inWar")


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


def normalize_saved_war_key(key):
    # Keys written before `endTime` was dropped from the war identity carry an
    # extra endTime field. Rewrite them so previously saved wars still match and
    # are not captured a second time.
    try:
        fields = json.loads(key)
    except (TypeError, ValueError):
        return key

    if not isinstance(fields, dict) or "endTime" not in fields:
        return key

    fields.pop("endTime", None)
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))


def load_saved_wars():
    if not os.path.exists(STATE_FILE):
        return set()

    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Could not read saved war state: {exc}. Starting with empty state.")
        return set()

    stored = state.get("saved_wars", [])
    saved_wars = {normalize_saved_war_key(key) for key in stored}

    if saved_wars != set(stored):
        log(
            "Migrated saved war keys to the endTime-independent format: "
            f"before={len(set(stored))} after={len(saved_wars)}"
        )
        write_saved_wars(saved_wars)

    return saved_wars


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


def attacks_used(war):
    try:
        return current_war_attack_summary(war or {})["used_attacks"]
    except (AttributeError, KeyError, TypeError):
        return 0


def snapshot_rejection_reason(war, key, end_time, captured_at, max_staleness_minutes):
    # Guards the one failure mode that silently publishes a wrong recap: saving a
    # payload that predates the result it claims to report. Returns None when the
    # payload is safe to publish as the final snapshot.
    if not war:
        return "no payload available"

    state = war.get("state", "unknown")
    if state not in FINAL_CAPABLE_STATES:
        return f"payload state {state!r} cannot describe a finished war"

    payload_key = war_key(war)
    if payload_key != key:
        return f"payload belongs to a different war (payload_key={payload_key})"

    if state == "warEnded":
        # The API already declared this war finished; the payload is the result.
        return None

    if end_time is None or captured_at is None:
        return "capture time or war end time is unknown for a pre-final payload"

    staleness_seconds = (end_time - captured_at).total_seconds()
    if staleness_seconds > max_staleness_minutes * 60:
        return (
            "payload was captured "
            f"{format_duration(staleness_seconds)} before the war ended "
            f"(limit {max_staleness_minutes:g}m), so it cannot hold the final result"
        )

    return None


def snapshot_with_provenance(war, source, captured_at, end_time, live_state):
    # Mirrors the `_cwl` metadata block on CWL snapshots so a bad recap can be
    # traced back to which payload produced it.
    enriched = dict(war)
    staleness_seconds = None
    if end_time is not None and captured_at is not None:
        staleness_seconds = max(0, int((end_time - captured_at).total_seconds()))

    enriched["_snapshot"] = {
        "source": source,
        "capturedAt": captured_at.isoformat() if captured_at else None,
        "savedAt": datetime.now(timezone.utc).isoformat(),
        "warEndTime": end_time.isoformat() if end_time else None,
        "capturedBeforeEndSeconds": staleness_seconds,
        "liveState": live_state,
        "attacksUsed": attacks_used(war),
    }
    return enriched


def save_final_snapshot(data, saved_wars, scheduled_war=None):
    live_key = war_key(data)
    scheduled_key = (scheduled_war or {}).get("war_key")

    if scheduled_key:
        keys_matched = live_key == scheduled_key
        rejected_mismatched_live = live_key is not None and not keys_matched
        used_persisted_identity = not keys_matched
        key = scheduled_key

        log(
            "Resolving scheduled final snapshot identity: "
            f"scheduled_key={scheduled_key} live_key={live_key} "
            f"keys_matched={keys_matched} "
            f"persisted_payload_fallback={used_persisted_identity} "
            f"rejected_mismatched_live={rejected_mismatched_live}"
        )

        if rejected_mismatched_live:
            log(
                "Rejected live payload for scheduled final snapshot because it "
                f"belongs to a different war: scheduled_key={scheduled_key} "
                f"live_key={live_key} live_state={data.get('state', 'unknown')}"
            )

        if key in saved_wars:
            log(f"Already saved this war; skipping. war_key={key}")
            clear_scheduled_war(key)
            return False

        if keys_matched:
            snapshot_data = data
            snapshot_source = "live"
            captured_at = datetime.now(timezone.utc)
        elif scheduled_war and scheduled_war.get("war"):
            snapshot_data = scheduled_war["war"]
            snapshot_source = "persisted"
            captured_at = parse_iso_datetime(scheduled_war.get("captured_at"))
        else:
            log(
                "Could not save scheduled final snapshot: live payload did not "
                "match scheduled war and no persisted scheduled payload is available. "
                f"scheduled_key={scheduled_key} live_key={live_key}"
            )
            clear_scheduled_war(scheduled_key)
            return False

        end_time = parse_iso_datetime(scheduled_war.get("end_time")) if scheduled_war else None
        rejection = snapshot_rejection_reason(
            snapshot_data,
            key,
            end_time,
            captured_at,
            env_minutes(
                "WAR_MAX_FALLBACK_STALENESS_MINUTES",
                DEFAULT_MAX_FALLBACK_STALENESS_MINUTES,
            ),
        )
        if rejection:
            # Publishing a stale payload as the final result is worse than
            # publishing nothing: it posts a recap that credits nobody. Drop the
            # scheduled identity but leave the war out of saved_wars so a later
            # warEnded payload can still recover it.
            log(
                "Refusing to save final war snapshot: "
                f"war_key={key} snapshot_source={snapshot_source} "
                f"live_key={live_key} live_state={data.get('state', 'unknown')} "
                f"attacks_used={attacks_used(snapshot_data)} reason={rejection}"
            )
            clear_scheduled_war(scheduled_key)
            return False

        filename = save_war_snapshot(
            snapshot_with_provenance(
                snapshot_data,
                snapshot_source,
                captured_at,
                end_time,
                data.get("state", "unknown"),
            ),
            output_dir=FINAL_WAR_DIR,
            prefix="final_war",
        )
        saved_wars.add(key)
        write_saved_wars(saved_wars)
        clear_scheduled_war(key)
        log(
            "Saved final war snapshot: "
            f"war_key={key} scheduled_key={scheduled_key} live_key={live_key} "
            f"keys_matched={keys_matched} snapshot_source={snapshot_source} "
            f"attacks_used={attacks_used(snapshot_data)} "
            f"persisted_identity_fallback={used_persisted_identity} "
            f"rejected_mismatched_live={rejected_mismatched_live} "
            f"path={filename}"
        )
        return True

    key = live_key

    if not key:
        log("Could not build a stable war key; skipping save to avoid duplicate snapshots.")
        return False

    if key in saved_wars:
        log(f"Already saved this war; skipping. war_key={key}")
        clear_scheduled_war(scheduled_key or key)
        return False

    captured_at = datetime.now(timezone.utc)
    try:
        end_time = parse_coc_time(data.get("endTime")) if data.get("endTime") else None
    except ValueError:
        end_time = None

    rejection = snapshot_rejection_reason(
        data,
        key,
        end_time,
        captured_at,
        env_minutes(
            "WAR_MAX_FALLBACK_STALENESS_MINUTES",
            DEFAULT_MAX_FALLBACK_STALENESS_MINUTES,
        ),
    )
    if rejection:
        log(f"Refusing to save final war snapshot: war_key={key} reason={rejection}")
        return False

    filename = save_war_snapshot(
        snapshot_with_provenance(
            data,
            "live",
            captured_at,
            end_time,
            data.get("state", "unknown"),
        ),
        output_dir=FINAL_WAR_DIR,
        prefix="final_war",
    )
    saved_wars.add(key)
    write_saved_wars(saved_wars)
    clear_scheduled_war(scheduled_key or key)
    log(
        "Saved final war snapshot: "
        f"war_key={key} snapshot_source=live attacks_used={attacks_used(data)} "
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


def refresh_config():
    return {
        "live_refresh_minutes": env_minutes(
            "WAR_LIVE_REFRESH_MINUTES", DEFAULT_LIVE_REFRESH_MINUTES
        ),
        "final_refresh_minutes": env_minutes(
            "WAR_FINAL_REFRESH_MINUTES", DEFAULT_FINAL_REFRESH_MINUTES
        ),
        "final_window_minutes": env_minutes(
            "WAR_FINAL_REFRESH_WINDOW_MINUTES", DEFAULT_FINAL_REFRESH_WINDOW_MINUTES
        ),
    }


def next_refresh_delay_seconds(now, end_time, snapshot_time, refresh):
    # Poll slowly through the bulk of battle day, then tighten to a few minutes
    # near the end so the persisted fallback payload is close to the final result.
    seconds_to_end = (end_time - now).total_seconds()
    if seconds_to_end <= refresh["final_window_minutes"] * 60:
        cadence_minutes = refresh["final_refresh_minutes"]
    else:
        cadence_minutes = refresh["live_refresh_minutes"]

    cadence_seconds = max(60.0, cadence_minutes * 60)
    seconds_to_snapshot = (snapshot_time - now).total_seconds()
    return max(0.0, min(cadence_seconds, seconds_to_snapshot))


def payload_end_time(war):
    end_time_text = (war or {}).get("endTime")
    if not end_time_text:
        return None
    try:
        return parse_coc_time(end_time_text)
    except ValueError:
        return None


def track_scheduled_war(
    scheduled_war,
    saved_wars,
    fallback_minutes,
    buffer_minutes,
    refresh,
    end_time,
    snapshot_time,
):
    # Follows one war from battle day to its final snapshot. Two things make
    # this more than a sleep:
    #   1. The persisted payload is the fallback when the live war no longer
    #      matches, so it is refreshed as the war progresses. Left unrefreshed
    #      it holds the battle-day-start capture: zero attacks, 0-0 stars.
    #   2. The API moves `endTime` when a war is extended. Snapshotting at the
    #      original end would capture a war that is still being fought, so the
    #      new end time is adopted and tracking continues.
    key = scheduled_war.get("war_key")

    while True:
        now = datetime.now(timezone.utc)
        if now < snapshot_time:
            delay = next_refresh_delay_seconds(now, end_time, snapshot_time, refresh)
            log(
                f"Tracking war until snapshot time: war_key={key} "
                f"war_ends_in={format_duration((end_time - now).total_seconds())} "
                f"next_check_in={format_duration(delay)}"
            )
            time.sleep(delay)

        data = fetch_war_safely()
        if data is None:
            if datetime.now(timezone.utc) >= snapshot_time:
                # Past the snapshot and the API is unreachable. Leave the
                # scheduled identity on disk so the next loop retries it.
                sleep_minutes(fallback_minutes)
                return
            # Keep the last good persisted payload and try again next cycle.
            continue

        live_key = war_key(data)
        live_state = data.get("state", "unknown")

        if live_key != key:
            log(
                "Live war payload no longer matches the scheduled war; saving the "
                f"freshest captured payload. war_key={key} live_key={live_key} "
                f"live_state={live_state}"
            )
            save_final_snapshot(data, saved_wars, scheduled_war=scheduled_war)
            return

        new_end_time = payload_end_time(data) or end_time
        if new_end_time != end_time:
            log(
                f"War end time moved: war_key={key} "
                f"previous_end={format_datetime(end_time)} "
                f"new_end={format_datetime(new_end_time)} "
                f"shift={format_duration(abs((new_end_time - end_time).total_seconds()))}"
            )
            end_time = new_end_time
            snapshot_time = end_time + timedelta(minutes=buffer_minutes)
            log(f"Rescheduled final snapshot for: {format_datetime(snapshot_time)}")

        scheduled_war = write_scheduled_war(data, key, end_time, snapshot_time)
        log(
            f"Refreshed scheduled war payload: war_key={key} state={live_state} "
            f"attacks_used={attacks_used(data)}"
        )

        if live_state == "warEnded":
            log("War reported as ended; saving final snapshot now.")
            save_final_snapshot(data, saved_wars, scheduled_war=scheduled_war)
            return

        if datetime.now(timezone.utc) >= snapshot_time:
            log(
                "Snapshot time reached; saving the live payload for this war. "
                f"war_key={key} api_state={live_state}"
            )
            save_final_snapshot(data, saved_wars, scheduled_war=scheduled_war)
            return


def resolve_due_scheduled_war(saved_wars, fallback_minutes, buffer_minutes, refresh=None):
    scheduled_war = load_scheduled_war()
    if not scheduled_war:
        return False

    scheduled_key = scheduled_war.get("war_key")
    snapshot_time = parse_iso_datetime(scheduled_war.get("snapshot_time"))
    end_time = parse_iso_datetime(scheduled_war.get("end_time"))
    if not scheduled_key or snapshot_time is None:
        log("Scheduled war identity is incomplete; clearing it.")
        clear_scheduled_war()
        return False

    log(
        "Pending scheduled war snapshot: "
        f"war_key={scheduled_key} scheduled_end_time={scheduled_war.get('end_time')} "
        f"snapshot_time={scheduled_war.get('snapshot_time')}"
    )
    track_scheduled_war(
        scheduled_war,
        saved_wars,
        fallback_minutes,
        buffer_minutes,
        refresh or refresh_config(),
        end_time or snapshot_time,
        snapshot_time,
    )
    return True


def handle_in_war(data, saved_wars, buffer_minutes, fallback_minutes, refresh=None):
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

    track_scheduled_war(
        scheduled_war,
        saved_wars,
        fallback_minutes,
        buffer_minutes,
        refresh or refresh_config(),
        end_time,
        snapshot_time,
    )


def handle_preparation(data, fallback_minutes, max_sleep_minutes):
    start_time_text = data.get("startTime")
    start_time = None
    if start_time_text:
        try:
            start_time = parse_coc_time(start_time_text)
        except ValueError:
            start_time = None

    if start_time is None:
        log(
            "Current war state: preparation, but startTime is unavailable; "
            f"checking again in {fallback_minutes:g} minutes."
        )
        sleep_minutes(fallback_minutes)
        return

    now = datetime.now(timezone.utc)
    seconds_until_start = (start_time - now).total_seconds()
    if seconds_until_start <= 0:
        log(
            "Current war state: preparation, but battle day should have started; "
            f"checking again in {fallback_minutes:g} minutes."
        )
        sleep_minutes(fallback_minutes)
        return

    sleep_seconds = min(seconds_until_start, max_sleep_minutes * 60)
    log(
        f"Current war state: preparation; battle day starts at {format_datetime(start_time)}; "
        f"sleeping for {format_duration(sleep_seconds)}."
    )
    time.sleep(sleep_seconds)


def run_scheduler():
    buffer_minutes = env_minutes("WAR_END_BUFFER_MINUTES", DEFAULT_BUFFER_MINUTES)
    prep_poll_minutes = env_minutes("WAR_PREP_POLL_MINUTES", DEFAULT_PREP_POLL_MINUTES)
    prep_max_sleep_minutes = env_minutes(
        "WAR_PREP_MAX_SLEEP_MINUTES", DEFAULT_PREP_MAX_SLEEP_MINUTES
    )
    idle_poll_minutes = env_minutes("WAR_IDLE_POLL_MINUTES", DEFAULT_IDLE_POLL_MINUTES)
    ended_poll_minutes = env_minutes("WAR_ENDED_POLL_MINUTES", DEFAULT_ENDED_POLL_MINUTES)
    refresh = refresh_config()
    saved_wars = load_saved_wars()

    log("Starting war snapshot scheduler.")
    log(f"Final snapshot buffer: {buffer_minutes:g} minutes.")
    log(
        "Battle-day payload refresh: "
        f"every {refresh['live_refresh_minutes']:g}m, "
        f"every {refresh['final_refresh_minutes']:g}m within "
        f"{refresh['final_window_minutes']:g}m of war end."
    )

    while True:
        if resolve_due_scheduled_war(
            saved_wars, prep_poll_minutes, buffer_minutes, refresh=refresh
        ):
            continue

        data = fetch_war_safely()
        if data is None:
            sleep_minutes(idle_poll_minutes)
            continue

        state = data.get("state", "unknown")

        if state == "inWar":
            handle_in_war(
                data, saved_wars, buffer_minutes, prep_poll_minutes, refresh=refresh
            )
        elif state == "warEnded":
            log("Current war state: warEnded")
            saved = save_final_snapshot(data, saved_wars)
            if saved:
                sleep_minutes(ended_poll_minutes)
            else:
                log(
                    "Final snapshot already saved for this war; backing off to "
                    f"{idle_poll_minutes:g} minutes until the next war."
                )
                sleep_minutes(idle_poll_minutes)
        elif state == "preparation":
            handle_preparation(data, prep_poll_minutes, prep_max_sleep_minutes)
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
