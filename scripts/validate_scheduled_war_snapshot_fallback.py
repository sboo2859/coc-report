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


def main():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        scheduler.STATE_FILE = str(tmp_path / "saved_wars.json")
        scheduler.SCHEDULED_WAR_FILE = str(tmp_path / "scheduled_war.json")
        scheduler.FINAL_WAR_DIR = str(tmp_path / "war_results")

        war = sample_war()
        key = scheduler.war_key(war)
        end_time = datetime(2026, 5, 23, 19, 22, 29, tzinfo=timezone.utc)
        snapshot_time = end_time + timedelta(minutes=2)
        scheduled_war = scheduler.write_scheduled_war(war, key, end_time, snapshot_time)

        saved_wars = scheduler.load_saved_wars()
        saved = scheduler.save_final_snapshot(
            {"state": "notInWar"},
            saved_wars,
            scheduled_war=scheduled_war,
        )

        assert saved is True
        assert key in saved_wars
        assert scheduler.load_scheduled_war() is None

        saved_files = list((tmp_path / "war_results").glob("final_war_*.json"))
        assert len(saved_files) == 1
        with saved_files[0].open() as f:
            saved_data = json.load(f)
        assert saved_data["state"] == "inWar"
        assert scheduler.war_key(saved_data) == key

        duplicate_saved = scheduler.save_final_snapshot(
            {"state": "notInWar"},
            saved_wars,
            scheduled_war=scheduled_war,
        )
        assert duplicate_saved is False

    print("Scheduled war snapshot fallback validation passed.")


if __name__ == "__main__":
    main()
