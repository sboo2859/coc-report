import copy
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import schedule_war_snapshot as scheduler
from clashcommand.post_war_reports import build_post_war_report, snapshot_freshness_note


# Times are relative to "now" so the staleness guard on fallback payloads is
# actually exercised; fixed past dates make every capture look post-war.
NOW = datetime.now(timezone.utc)
END_TIME = NOW + timedelta(minutes=1)
SNAPSHOT_TIME = END_TIME + timedelta(minutes=2)


def coc_time(value):
    return value.strftime("%Y%m%dT%H%M%S.000Z")


def sample_war():
    # The payload as it looks the moment battle day starts: everyone is in the
    # roster, nobody has attacked yet.
    return {
        "state": "inWar",
        "teamSize": 2,
        "attacksPerMember": 2,
        "preparationStartTime": coc_time(END_TIME - timedelta(hours=47)),
        "startTime": coc_time(END_TIME - timedelta(hours=24)),
        "endTime": coc_time(END_TIME),
        "clan": {
            "tag": "#CLAN",
            "name": "Clan",
            "stars": 0,
            "destructionPercentage": 0.0,
            "members": [
                {"tag": "#P1", "name": "Alice", "mapPosition": 1, "attacks": []},
                {"tag": "#P2", "name": "Bob", "mapPosition": 2, "attacks": []},
            ],
        },
        "opponent": {
            "tag": "#OPP",
            "name": "Opponent",
            "stars": 0,
            "destructionPercentage": 0.0,
            "members": [
                {"tag": "#O1", "name": "X", "mapPosition": 1},
                {"tag": "#O2", "name": "Y", "mapPosition": 2},
            ],
        },
    }


def fought_war(state="inWar"):
    # The same war after the attacks have actually been used.
    war = copy.deepcopy(sample_war())
    war["state"] = state
    war["clan"]["stars"] = 5
    war["clan"]["destructionPercentage"] = 92.5
    war["opponent"]["stars"] = 3
    war["opponent"]["destructionPercentage"] = 61.0
    war["clan"]["members"][0]["attacks"] = [
        {"attackerTag": "#P1", "defenderTag": "#O1", "stars": 3, "destructionPercentage": 100},
        {"attackerTag": "#P1", "defenderTag": "#O2", "stars": 2, "destructionPercentage": 85},
    ]
    return war


def same_war_ended_payload():
    return fought_war(state="warEnded")


def next_war_payload(state="preparation"):
    war = copy.deepcopy(sample_war())
    war["state"] = state
    war["preparationStartTime"] = coc_time(END_TIME + timedelta(minutes=5))
    war["startTime"] = coc_time(END_TIME + timedelta(hours=23))
    war["endTime"] = coc_time(END_TIME + timedelta(hours=47))
    war["opponent"] = {
        "tag": "#NEXT",
        "name": "Next Opponent",
        "stars": 0,
        "destructionPercentage": 0.0,
        "members": [],
    }
    return war


def configure_scheduler(tmp_path):
    scheduler.STATE_FILE = str(tmp_path / "saved_wars.json")
    scheduler.SCHEDULED_WAR_FILE = str(tmp_path / "scheduled_war.json")
    scheduler.FINAL_WAR_DIR = str(tmp_path / "war_results")


def create_scheduled_war(war=None, captured_before_end=timedelta(hours=24)):
    """Persist a scheduled war whose payload was captured `captured_before_end`
    before the war ended, simulating how fresh the refresh loop kept it."""
    war = war if war is not None else sample_war()
    key = scheduler.war_key(war)
    scheduler.write_scheduled_war(war, key, END_TIME, SNAPSHOT_TIME)

    with open(scheduler.SCHEDULED_WAR_FILE) as f:
        scheduled_war = json.load(f)
    scheduled_war["captured_at"] = (END_TIME - captured_before_end).isoformat()
    with open(scheduler.SCHEDULED_WAR_FILE, "w") as f:
        json.dump(scheduled_war, f, indent=2)

    return war, key, scheduled_war


def saved_payloads(tmp_path):
    saved_files = list((tmp_path / "war_results").glob("final_war_*.json"))
    payloads = []
    for path in saved_files:
        with path.open() as f:
            payloads.append(json.load(f))
    return payloads


def validate_stale_fallback_refused():
    """The reported bug: a new war was matched before the final snapshot could be
    taken, so the scheduler fell back to the payload captured at battle-day start
    and published a '0-0 tie, everyone missed' recap."""
    for state in ("preparation", "inWar", "notInWar"):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            configure_scheduler(tmp_path)
            _war, key, scheduled_war = create_scheduled_war(
                captured_before_end=timedelta(hours=24)
            )

            saved_wars = scheduler.load_saved_wars()
            saved = scheduler.save_final_snapshot(
                next_war_payload(state=state),
                saved_wars,
                scheduled_war=scheduled_war,
            )

            assert saved is False, f"stale fallback was saved for live state {state}"
            assert key not in saved_wars, "stale war must stay recoverable"
            assert saved_payloads(tmp_path) == [], "no snapshot file may be written"
            # Scheduled identity is cleared so the loop cannot spin on it.
            assert scheduler.load_scheduled_war() is None


def validate_fresh_fallback_accepted():
    """With the refresh loop running, the fallback payload is minutes old and
    carries the real attacks, so it is still worth publishing."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        configure_scheduler(tmp_path)
        _war, key, scheduled_war = create_scheduled_war(
            war=fought_war(), captured_before_end=timedelta(minutes=3)
        )

        saved_wars = scheduler.load_saved_wars()
        saved = scheduler.save_final_snapshot(
            next_war_payload(),
            saved_wars,
            scheduled_war=scheduled_war,
        )

        assert saved is True
        assert key in saved_wars
        assert scheduler.load_scheduled_war() is None

        payloads = saved_payloads(tmp_path)
        assert len(payloads) == 1
        saved_data = payloads[0]
        assert saved_data["opponent"]["tag"] == "#OPP", "must not save the next war"
        assert scheduler.war_key(saved_data) == key
        assert saved_data["clan"]["stars"] == 5
        assert saved_data["_snapshot"]["source"] == "persisted"
        assert saved_data["_snapshot"]["attacksUsed"] == 2

        recap = build_post_war_report(saved_data)
        assert "**War Recap: Win**" in recap
        assert "`5-3` stars" in recap
        assert "MVP:" in recap
        assert "3 minutes before the war ended" in recap, "fallback recap must flag itself"


def validate_live_same_war_ended_accepted():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        configure_scheduler(tmp_path)
        _war, key, scheduled_war = create_scheduled_war()

        saved_wars = scheduler.load_saved_wars()
        saved = scheduler.save_final_snapshot(
            same_war_ended_payload(),
            saved_wars,
            scheduled_war=scheduled_war,
        )

        assert saved is True
        assert key in saved_wars
        assert scheduler.load_scheduled_war() is None

        payloads = saved_payloads(tmp_path)
        assert len(payloads) == 1
        saved_data = payloads[0]
        assert saved_data["state"] == "warEnded"
        assert saved_data["clan"]["stars"] == 5
        assert saved_data["_snapshot"]["source"] == "live"
        assert scheduler.war_key(saved_data) == key

        duplicate_saved = scheduler.save_final_snapshot(
            same_war_ended_payload(),
            saved_wars,
            scheduled_war=scheduled_war,
        )
        assert duplicate_saved is False


def validate_unscheduled_war_ended_accepted():
    """The plain warEnded branch (no scheduled identity on disk) still saves."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        configure_scheduler(tmp_path)

        ended = same_war_ended_payload()
        saved_wars = scheduler.load_saved_wars()
        saved = scheduler.save_final_snapshot(ended, saved_wars)

        assert saved is True
        payloads = saved_payloads(tmp_path)
        assert len(payloads) == 1
        assert payloads[0]["_snapshot"]["source"] == "live"
        assert payloads[0]["_snapshot"]["attacksUsed"] == 2
        # A live final payload is the real result, so it carries no caveat.
        assert snapshot_freshness_note(payloads[0]) is None


def validate_preparation_payload_never_final():
    """A preparation payload can never be a final result, however fresh it is."""
    prep = next_war_payload(state="preparation")
    reason = scheduler.snapshot_rejection_reason(
        prep,
        scheduler.war_key(prep),
        END_TIME,
        END_TIME,
        scheduler.DEFAULT_MAX_FALLBACK_STALENESS_MINUTES,
    )
    assert reason is not None and "cannot describe a finished war" in reason


def validate_refresh_cadence():
    """Refresh slowly mid-war, tightly near the end, and never overshoot the
    scheduled snapshot time."""
    refresh = {
        "live_refresh_minutes": 30,
        "final_refresh_minutes": 5,
        "final_window_minutes": 60,
    }

    mid_war = END_TIME - timedelta(hours=10)
    assert scheduler.next_refresh_delay_seconds(
        mid_war, END_TIME, SNAPSHOT_TIME, refresh
    ) == 30 * 60

    near_end = END_TIME - timedelta(minutes=20)
    assert scheduler.next_refresh_delay_seconds(
        near_end, END_TIME, SNAPSHOT_TIME, refresh
    ) == 5 * 60

    # Never sleep past the snapshot time.
    just_before = SNAPSHOT_TIME - timedelta(seconds=30)
    assert scheduler.next_refresh_delay_seconds(
        just_before, END_TIME, SNAPSHOT_TIME, refresh
    ) == 30

    # A misconfigured cadence must not become a busy loop.
    hot = {"live_refresh_minutes": 0, "final_refresh_minutes": 0, "final_window_minutes": 60}
    assert scheduler.next_refresh_delay_seconds(
        mid_war, END_TIME, SNAPSHOT_TIME, hot
    ) == 60


class FakeDatetime(datetime):
    """Lets the battle-day tracking loop run instantly under a controlled clock."""

    _current = None

    @classmethod
    def now(cls, tz=None):
        return cls._current


class FakeTime:
    def sleep(self, seconds):
        FakeDatetime._current = FakeDatetime._current + timedelta(seconds=seconds)


def validate_war_key_ignores_end_time():
    """The Clash API moves endTime when a war is extended. Two payloads of the
    same war must still share one identity (observed 2026-06-16 +72m and
    2026-07-31 +32m, each producing a duplicate snapshot and a false recap)."""
    original = sample_war()
    extended = copy.deepcopy(original)
    extended["state"] = "warEnded"
    extended["endTime"] = coc_time(END_TIME + timedelta(minutes=32))

    assert scheduler.war_key(original) == scheduler.war_key(extended)
    # A genuinely different war still gets a different key.
    assert scheduler.war_key(next_war_payload()) != scheduler.war_key(original)


def validate_saved_war_key_migration():
    """Keys written before endTime was dropped must still match, so previously
    captured wars are not saved a second time."""
    legacy = json.dumps(
        {
            "clan_tag": "#CLAN",
            "endTime": "20260523T192229.000Z",
            "opponent_tag": "#OPP",
            "preparationStartTime": "20260522T192229.000Z",
            "startTime": "20260522T202229.000Z",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    migrated = scheduler.normalize_saved_war_key(legacy)
    assert "endTime" not in migrated
    assert json.loads(migrated) == {
        "clan_tag": "#CLAN",
        "opponent_tag": "#OPP",
        "preparationStartTime": "20260522T192229.000Z",
        "startTime": "20260522T202229.000Z",
    }
    # Already-migrated and unparseable keys pass through untouched.
    assert scheduler.normalize_saved_war_key(migrated) == migrated
    assert scheduler.normalize_saved_war_key("not json") == "not json"

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        configure_scheduler(tmp_path)
        with open(scheduler.STATE_FILE, "w") as f:
            json.dump({"saved_wars": [legacy]}, f)

        saved_wars = scheduler.load_saved_wars()
        assert saved_wars == {migrated}
        # The migration is persisted, not recomputed every start.
        with open(scheduler.STATE_FILE) as f:
            assert json.load(f)["saved_wars"] == [migrated]


def run_tracking_loop(tmp_path, fetch_responses, battle_hours=24):
    """Drive track_scheduled_war from battle-day start through the final
    snapshot, returning the snapshots it saved."""
    configure_scheduler(tmp_path)

    start = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    end_time = start + timedelta(hours=battle_hours)
    snapshot_time = end_time + timedelta(minutes=2)

    real_datetime, real_time, real_fetch = (
        scheduler.datetime,
        scheduler.time,
        scheduler.fetch_war_safely,
    )
    FakeDatetime._current = start
    scheduler.datetime = FakeDatetime
    scheduler.time = FakeTime()
    scheduler.fetch_war_safely = lambda: fetch_responses(FakeDatetime._current, end_time)

    try:
        war = sample_war()
        war["endTime"] = coc_time(end_time)
        war["startTime"] = coc_time(start)
        war["preparationStartTime"] = coc_time(start - timedelta(hours=23))
        key = scheduler.war_key(war)
        scheduled_war = scheduler.write_scheduled_war(war, key, end_time, snapshot_time)

        saved_wars = scheduler.load_saved_wars()
        scheduler.track_scheduled_war(
            scheduled_war,
            saved_wars,
            fallback_minutes=30,
            buffer_minutes=2,
            refresh=scheduler.refresh_config(),
            end_time=end_time,
            snapshot_time=snapshot_time,
        )
        return saved_payloads(tmp_path), key, saved_wars
    finally:
        scheduler.datetime = real_datetime
        scheduler.time = real_time
        scheduler.fetch_war_safely = real_fetch


def validate_tracking_loop_keeps_fallback_fresh():
    """End-to-end: attacks land late in battle day and a new war is matched the
    moment the old one ends. The recap must still report the real result."""
    def responses(now, end_time):
        war = sample_war() if now < end_time - timedelta(minutes=45) else fought_war()
        war["endTime"] = coc_time(end_time)
        war["startTime"] = coc_time(end_time - timedelta(hours=24))
        war["preparationStartTime"] = coc_time(end_time - timedelta(hours=47))
        if now >= end_time:
            # Clan re-queued instantly; the API now describes a different war.
            nxt = next_war_payload()
            nxt["preparationStartTime"] = coc_time(end_time)
            nxt["startTime"] = coc_time(end_time + timedelta(hours=23))
            nxt["endTime"] = coc_time(end_time + timedelta(hours=47))
            return nxt
        return war

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        payloads, key, saved_wars = run_tracking_loop(tmp_path, responses)

        assert len(payloads) == 1, f"expected one snapshot, got {len(payloads)}"
        saved_data = payloads[0]
        assert key in saved_wars
        assert saved_data["opponent"]["tag"] == "#OPP", "saved the wrong war"
        assert saved_data["clan"]["stars"] == 5
        assert saved_data["_snapshot"]["attacksUsed"] == 2

        recap = build_post_war_report(saved_data)
        assert "**War Recap: Win**" in recap
        assert "`5-3` stars" in recap
        assert "`2/4` used" in recap


def validate_tracking_loop_follows_extended_end_time():
    """Replays the 2026-07-31 incident: at the original endTime the war was not
    over, the API had moved endTime out by 32 minutes, and the old code snapshot
    the pre-battle payload and posted a 0-0 recap. One war must produce exactly
    one snapshot, taken after it truly ended."""
    def responses(now, original_end):
        extended_end = original_end + timedelta(minutes=32)
        if now >= extended_end:
            war = same_war_ended_payload()
        elif now >= original_end - timedelta(minutes=45):
            war = fought_war()
        else:
            war = sample_war()

        war["startTime"] = coc_time(original_end - timedelta(hours=24))
        war["preparationStartTime"] = coc_time(original_end - timedelta(hours=47))
        # The API reports the original end until the extension is applied.
        war["endTime"] = coc_time(original_end if now < original_end else extended_end)
        return war

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        payloads, key, saved_wars = run_tracking_loop(tmp_path, responses)

        assert len(payloads) == 1, f"expected one snapshot, got {len(payloads)}"
        saved_data = payloads[0]
        assert saved_data["state"] == "warEnded", "snapshot taken before the war ended"
        assert saved_data["_snapshot"]["attacksUsed"] == 2
        assert key in saved_wars

        recap = build_post_war_report(saved_data)
        assert "**War Recap: Win**" in recap
        assert "`5-3` stars" in recap
        assert snapshot_freshness_note(saved_data) is None


def validate_tracking_loop_refuses_when_api_is_down():
    """If the API is unreachable for all of battle day, the only payload on hand
    is the battle-day-start capture. Post nothing rather than a false recap."""
    def responses(now, end_time):
        return None

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        payloads, key, saved_wars = run_tracking_loop(tmp_path, responses)

        assert payloads == [], "a zero-attack snapshot must never be published"
        assert key not in saved_wars


def validate_tracking_loop_saves_war_ended_immediately():
    """A matching warEnded payload is the real result and is saved on sight."""
    def responses(now, end_time):
        if now < end_time - timedelta(minutes=10):
            war = sample_war()
        else:
            war = same_war_ended_payload()
        war["endTime"] = coc_time(end_time)
        war["startTime"] = coc_time(end_time - timedelta(hours=24))
        war["preparationStartTime"] = coc_time(end_time - timedelta(hours=47))
        return war

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        payloads, key, saved_wars = run_tracking_loop(tmp_path, responses)

        assert len(payloads) == 1
        assert payloads[0]["state"] == "warEnded"
        assert payloads[0]["_snapshot"]["source"] == "live"
        assert key in saved_wars


def main():
    validate_war_key_ignores_end_time()
    validate_saved_war_key_migration()
    validate_stale_fallback_refused()
    validate_fresh_fallback_accepted()
    validate_live_same_war_ended_accepted()
    validate_unscheduled_war_ended_accepted()
    validate_preparation_payload_never_final()
    validate_refresh_cadence()
    validate_tracking_loop_keeps_fallback_fresh()
    validate_tracking_loop_follows_extended_end_time()
    validate_tracking_loop_refuses_when_api_is_down()
    validate_tracking_loop_saves_war_ended_immediately()
    print("Scheduled war snapshot fallback validation passed.")


if __name__ == "__main__":
    main()
