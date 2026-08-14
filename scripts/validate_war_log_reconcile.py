import json
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import war_log
from weekly_report import aggregate_wars, build_notes, report_summary


def snapshot(opponent_tag="#OPP", end="20260811T184959.000Z", attacks_each=0,
             members=30, clan_stars=0, opponent_stars=6):
    clan_members = []
    for i in range(members):
        atks = [
            {"attackerTag": f"#P{i}", "defenderTag": f"#O{i}", "stars": 3,
             "destructionPercentage": 100}
        ] * attacks_each
        clan_members.append(
            {"tag": f"#P{i}", "name": f"Player{i}", "townhallLevel": 16,
             "mapPosition": i + 1, "attacks": atks}
        )
    return (
        "snap.json",
        {
            "state": "inWar", "teamSize": members, "attacksPerMember": 2,
            "preparationStartTime": "20260809T194959.000Z",
            "startTime": "20260810T184959.000Z", "endTime": end,
            "clan": {"tag": "#CLAN", "name": "Active", "stars": clan_stars,
                     "destructionPercentage": 0.0, "members": clan_members},
            "opponent": {"tag": opponent_tag, "name": "Foe",
                         "stars": opponent_stars, "destructionPercentage": 6.67,
                         "members": []},
        },
    )


def log_entry(opponent_tag="#OPP", end="20260811T184959.000Z", result="tie",
              clan_stars=90, opponent_stars=90, attacks=51, team_size=30):
    return {
        "result": result, "endTime": end, "teamSize": team_size,
        "attacksPerMember": 2, "battleModifier": "none",
        "clan": {"tag": "#CLAN", "name": "Active", "attacks": attacks,
                 "stars": clan_stars, "destructionPercentage": 100.0},
        "opponent": {"tag": opponent_tag, "name": "Foe", "stars": opponent_stars,
                     "destructionPercentage": 96.5},
    }


def validate_cwl_entries_are_excluded():
    """CWL rounds appear in the log with no opponent tag and season totals."""
    cwl = {"result": "tie", "endTime": "20260809T194558.000Z", "teamSize": 30,
           "attacksPerMember": 1,
           "clan": {"tag": "#CLAN", "stars": 490, "attacks": 100},
           "opponent": {"name": None, "stars": 563}}
    assert war_log.is_regular_war_entry(cwl) is False
    assert war_log.is_regular_war_entry(log_entry()) is True
    merged = war_log.merge_war_log_entries([], [cwl, log_entry()])
    assert len(merged) == 1


def validate_matching_tolerates_end_time_drift():
    """Snapshot and log endTime differed by 1 second in production."""
    _p, war = snapshot(end="20260731T213339.000Z")
    entry = log_entry(end="20260731T213340.000Z")
    assert war_log.match_war_log_entry(war, [entry]) is entry

    # A different opponent never matches, however close in time.
    assert war_log.match_war_log_entry(war, [log_entry(opponent_tag="#OTHER")]) is None
    # The same opponent far outside the window does not match either.
    assert war_log.match_war_log_entry(war, [log_entry(end="20260901T213340.000Z")]) is None


def validate_broken_snapshot_is_corrected():
    """Aug 11: snapshot said 0-6 loss with 0 attacks; the log says 90-90 tie
    with 51 attacks."""
    wars = [snapshot(attacks_each=0, clan_stars=0, opponent_stars=6)]
    entries = [log_entry(result="tie", clan_stars=90, opponent_stars=90, attacks=51)]
    totals = aggregate_wars(wars, war_log_entries=entries)

    assert (totals["wins"], totals["losses"], totals["ties"]) == (0, 0, 1)
    assert totals["stars"] == 90
    assert totals["used_attacks"] == 51
    assert totals["possible_attacks"] == 60
    # No per-member attribution: nobody may be blamed for attacks we cannot see.
    assert totals["wars_without_member_data"] == ["Foe"]
    assert all(p["attacks_missed"] == 0 for p in totals["players"].values())
    assert all(p["wars_participated"] == 0 for p in totals["players"].values())

    summary = report_summary(totals)
    assert summary["usage_percent_number"] == 85.0

    notes = build_notes(totals, summary["usage_percent_number"])
    assert any("war log only" in n for n in notes), notes


def validate_slightly_stale_snapshot_keeps_member_data():
    """4 wars were 1-2 attacks short. Totals follow the log; per-member rows
    still come from the snapshot rather than being discarded."""
    wars = [snapshot(attacks_each=2, clan_stars=90, opponent_stars=86)]
    entries = [log_entry(result="win", clan_stars=90, opponent_stars=86, attacks=61)]
    totals = aggregate_wars(wars, war_log_entries=entries)

    assert totals["wins"] == 1
    assert totals["used_attacks"] == 61           # war log wins for totals
    assert totals["wars_without_member_data"] == []
    assert all(p["wars_participated"] == 1 for p in totals["players"].values())
    assert sum(p["attacks_used"] for p in totals["players"].values()) == 60


def validate_agreeing_snapshot_is_unchanged():
    wars = [snapshot(attacks_each=2, clan_stars=90, opponent_stars=86)]
    entries = [log_entry(result="win", clan_stars=90, opponent_stars=86, attacks=60)]
    totals = aggregate_wars(wars, war_log_entries=entries)

    assert totals["war_log_corrections"] == 0
    assert totals["used_attacks"] == 60
    assert totals["possible_attacks"] == 60


def validate_no_war_log_falls_back_to_snapshot():
    """The war log is optional; without it the report behaves as before."""
    wars = [snapshot(attacks_each=2, clan_stars=90, opponent_stars=86)]
    totals = aggregate_wars(wars, war_log_entries=None)

    assert totals["wins"] == 1
    assert totals["used_attacks"] == 60
    assert totals["possible_attacks"] == 60
    assert totals["wars_without_member_data"] == []
    assert totals["war_log_corrections"] == 0


def validate_result_comes_from_log_not_stars():
    """A snapshot that disagrees on the winner must not override the log."""
    wars = [snapshot(attacks_each=1, clan_stars=50, opponent_stars=99)]
    entries = [log_entry(result="win", clan_stars=105, opponent_stars=66, attacks=55)]
    totals = aggregate_wars(wars, war_log_entries=entries)
    assert (totals["wins"], totals["losses"], totals["ties"]) == (1, 0, 0)
    assert totals["war_summaries"][0]["result"] == "Win"
    assert totals["war_summaries"][0]["clan_stars"] == 105


def validate_cache_roundtrip_and_merge():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = str(Path(tmp_dir) / "state" / "war_log.json")
        first = [log_entry(end="20260811T184959.000Z")]
        war_log.write_cached_war_log(first, path=path)
        assert war_log.load_cached_war_log(path) == first

        # Re-fetching overlapping entries must not duplicate them.
        second = [log_entry(end="20260811T184959.000Z"),
                  log_entry(end="20260813T175736.000Z", result="win")]
        merged = war_log.merge_war_log_entries(war_log.load_cached_war_log(path), second)
        assert len(merged) == 2

        # Older entries survive even when the API stops returning them.
        merged2 = war_log.merge_war_log_entries(merged, [])
        assert len(merged2) == 2

        with open(path, "w") as f:
            f.write("{broken")
        assert war_log.load_cached_war_log(path) == []


def main():
    validate_cwl_entries_are_excluded()
    validate_matching_tolerates_end_time_drift()
    validate_broken_snapshot_is_corrected()
    validate_slightly_stale_snapshot_keeps_member_data()
    validate_agreeing_snapshot_is_unchanged()
    validate_no_war_log_falls_back_to_snapshot()
    validate_result_comes_from_log_not_stars()
    validate_cache_roundtrip_and_merge()
    print("War log reconciliation validation passed.")


if __name__ == "__main__":
    main()
