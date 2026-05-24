import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import schedule_war_snapshot as scheduler


def sample_war():
    return {
        "state": "inWar",
        "preparationStartTime": "20260522T192229.000Z",
        "startTime": "20260522T202229.000Z",
        "endTime": "20260523T192229.000Z",
        "clan": {"tag": "#CLAN", "name": "Clan", "stars": 20},
        "opponent": {"tag": "#OPP", "name": "Opponent", "stars": 19},
    }


def same_war_ended_payload():
    war = sample_war()
    war["state"] = "warEnded"
    war["clan"]["stars"] = 21
    return war


def next_war_payload(state="preparation"):
    war = sample_war()
    war["state"] = state
    war["preparationStartTime"] = "20260524T192229.000Z"
    war["startTime"] = "20260524T202229.000Z"
    war["endTime"] = "20260525T192229.000Z"
    war["opponent"] = {"tag": "#NEXT", "name": "Next Opponent", "stars": 0}
    return war


def configure_scheduler(tmp_path):
    scheduler.STATE_FILE = str(tmp_path / "saved_wars.json")
    scheduler.SCHEDULED_WAR_FILE = str(tmp_path / "scheduled_war.json")
    scheduler.FINAL_WAR_DIR = str(tmp_path / "war_results")


def create_scheduled_war():
    war = sample_war()
    key = scheduler.war_key(war)
    end_time = datetime(2026, 5, 23, 19, 22, 29, tzinfo=timezone.utc)
    snapshot_time = end_time + timedelta(minutes=2)
    scheduled_war = scheduler.write_scheduled_war(war, key, end_time, snapshot_time)
    return war, key, scheduled_war


def saved_payloads(tmp_path):
    saved_files = list((tmp_path / "war_results").glob("final_war_*.json"))
    payloads = []
    for path in saved_files:
        with path.open() as f:
            payloads.append(json.load(f))
    return payloads


def validate_live_not_in_war_fallback():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        configure_scheduler(tmp_path)
        _war, key, scheduled_war = create_scheduled_war()

        saved_wars = scheduler.load_saved_wars()
        saved = scheduler.save_final_snapshot(
            {"state": "notInWar"},
            saved_wars,
            scheduled_war=scheduled_war,
        )

        assert saved is True
        assert key in saved_wars
        assert scheduler.load_scheduled_war() is None

        payloads = saved_payloads(tmp_path)
        assert len(payloads) == 1
        saved_data = payloads[0]
        assert saved_data["state"] == "inWar"
        assert scheduler.war_key(saved_data) == key

        duplicate_saved = scheduler.save_final_snapshot(
            {"state": "notInWar"},
            saved_wars,
            scheduled_war=scheduled_war,
        )
        assert duplicate_saved is False


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
        assert saved_data["clan"]["stars"] == 21
        assert scheduler.war_key(saved_data) == key


def validate_live_next_war_rejected_with_fallback():
    for state in ("preparation", "inWar"):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            configure_scheduler(tmp_path)
            _war, key, scheduled_war = create_scheduled_war()
            next_war = next_war_payload(state=state)
            assert scheduler.war_key(next_war) != key

            saved_wars = scheduler.load_saved_wars()
            saved = scheduler.save_final_snapshot(
                next_war,
                saved_wars,
                scheduled_war=scheduled_war,
            )

            assert saved is True
            assert key in saved_wars
            assert scheduler.load_scheduled_war() is None

            payloads = saved_payloads(tmp_path)
            assert len(payloads) == 1
            saved_data = payloads[0]
            assert saved_data["state"] == "inWar"
            assert saved_data["opponent"]["tag"] == "#OPP"
            assert scheduler.war_key(saved_data) == key


def main():
    validate_live_not_in_war_fallback()
    validate_live_same_war_ended_accepted()
    validate_live_next_war_rejected_with_fallback()
    print("Scheduled war snapshot fallback validation passed.")


if __name__ == "__main__":
    main()
