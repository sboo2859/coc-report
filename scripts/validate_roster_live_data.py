import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import roster_samples
from weekly_report import (
    aggregate_wars,
    apply_donation_deltas,
    membership_label,
    render_delta,
    render_roster_table,
)


NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def clan_member(tag, name, role="member", trophies=1000, donations=0, received=0):
    return {
        "tag": tag,
        "name": name,
        "role": role,
        "townHallLevel": 16,
        "trophies": trophies,
        "donations": donations,
        "donationsReceived": received,
    }


def war(members, start="20260812T175736.000Z"):
    return (
        "war.json",
        {
            "state": "warEnded",
            "attacksPerMember": 2,
            "preparationStartTime": "20260811T185736.000Z",
            "startTime": start,
            "endTime": "20260813T175736.000Z",
            "clan": {
                "tag": "#CLAN", "name": "Active", "stars": 3,
                "destructionPercentage": 50.0, "members": members,
            },
            "opponent": {
                "tag": "#OPP", "name": "Opponent", "stars": 1,
                "destructionPercentage": 20.0, "members": [],
            },
        },
    )


def war_member(tag, name, attacks=0):
    return {
        "tag": tag, "name": name, "townhallLevel": 16, "mapPosition": 1,
        "attacks": [
            {"attackerTag": tag, "defenderTag": "#O1", "stars": 3,
             "destructionPercentage": 100}
        ] * attacks,
    }


def validate_clan_fields_reach_the_roster():
    """The whole point: role/trophies/donations exist only on the clan endpoint,
    and must survive being merged with war-snapshot members."""
    members = [clan_member("#P1", "Alice", role="admin", trophies=1234,
                           donations=500, received=250)]
    totals = aggregate_wars([war([war_member("#P1", "Alice", attacks=2)])],
                            clan_members=members)
    record = totals["players"]["#P1"]

    assert record["role"] == "admin"
    assert record["trophies"] == 1234
    assert record["donations"] == 500
    assert record["donations_received"] == 250
    assert record["in_clan"] is True
    # War stats still accumulate for the same player.
    assert record["attacks_used"] == 2
    assert record["stars"] == 6
    assert totals["clan_member_count"] == 1


def validate_current_members_without_wars_appear():
    """8 current members were missing from the live table because they had not
    warred in the window."""
    members = [clan_member("#P1", "Alice"), clan_member("#P2", "Benched")]
    totals = aggregate_wars([war([war_member("#P1", "Alice", attacks=1)])],
                            clan_members=members)

    assert set(totals["players"]) == {"#P1", "#P2"}
    benched = totals["players"]["#P2"]
    assert benched["in_clan"] is True
    assert benched["wars_participated"] == 0
    assert benched["attacks_used"] == 0


def validate_former_members_are_labelled_not_blank():
    members = [clan_member("#P1", "Alice")]
    totals = aggregate_wars([war([war_member("#P1", "Alice"),
                                  war_member("#P9", "Departed")])],
                            clan_members=members)

    departed = totals["players"]["#P9"]
    assert departed.get("in_clan") is False
    assert membership_label(departed, clan_roster_known=True) == "Former member"
    assert membership_label(totals["players"]["#P1"], clan_roster_known=True) == "member"
    # With no clan data at all we cannot claim someone left.
    assert membership_label(departed, clan_roster_known=False) == "N/A"


def validate_no_clan_data_degrades_quietly():
    """A failed clan fetch must leave the old behaviour intact, not crash."""
    totals = aggregate_wars([war([war_member("#P1", "Alice", attacks=1)])],
                            clan_members=None)
    assert totals["clan_member_count"] == 0
    assert totals["players"]["#P1"]["in_clan"] is False
    html = render_roster_table(totals["players"], clan_roster_known=False)
    assert "<table>" in html
    assert "Former member" not in html


def validate_delta_needs_two_readings():
    """One reading proves nothing about change and must not render as 0."""
    samples = roster_samples.record_clan_member_samples(
        [clan_member("#P1", "Alice", donations=100)], now=NOW, samples={}
    )
    assert roster_samples.donation_deltas(samples, now=NOW) == {}
    assert render_delta(None) == "—"


def validate_delta_measures_real_increase():
    samples = {}
    for hours, donated in ((72, 100), (48, 250), (24, 400), (0, 700)):
        samples = roster_samples.record_clan_member_samples(
            [clan_member("#P1", "Alice", donations=donated, received=donated // 2)],
            now=NOW - timedelta(hours=hours),
            samples=samples,
        )

    deltas = roster_samples.donation_deltas(samples, now=NOW)
    assert deltas["#P1"]["donations"] == 600, deltas
    assert deltas["#P1"]["received"] == 300, deltas
    assert render_delta(deltas["#P1"]["donations"]) == "+600"


def validate_season_reset_does_not_go_negative():
    """Donation counters reset each season; a drop is a reset, not a refund."""
    samples = {}
    for hours, donated in ((72, 900), (48, 1200), (24, 50), (0, 300)):
        samples = roster_samples.record_clan_member_samples(
            [clan_member("#P1", "Alice", donations=donated)],
            now=NOW - timedelta(hours=hours),
            samples=samples,
        )

    deltas = roster_samples.donation_deltas(samples, now=NOW)
    # 900->1200 = +300, reset to 50 = +50 since reset, 50->300 = +250.
    assert deltas["#P1"]["donations"] == 600, deltas
    assert deltas["#P1"]["donations"] >= 0


def validate_sampling_is_rate_limited_and_pruned():
    base = roster_samples.record_clan_member_samples(
        [clan_member("#P1", "Alice", donations=10)], now=NOW - timedelta(days=9), samples={}
    )
    # A second reading minutes later is ignored.
    same_hour = roster_samples.record_clan_member_samples(
        [clan_member("#P1", "Alice", donations=20)],
        now=NOW - timedelta(days=9) + timedelta(minutes=5),
        samples=base,
    )
    assert len(same_hour.get("#P1", [])) == 1

    # Readings past the retention window are dropped entirely.
    fresh = roster_samples.record_clan_member_samples(
        [clan_member("#P1", "Alice", donations=30)], now=NOW, samples=same_hour
    )
    assert len(fresh["#P1"]) == 1
    assert roster_samples.parse_sample_time(fresh["#P1"][0]["at"]) == NOW


def validate_sample_file_roundtrip():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = str(Path(tmp_dir) / "state" / "roster_samples.json")
        samples = roster_samples.record_clan_member_samples(
            [clan_member("#P1", "Alice", donations=42)], now=NOW, samples={}
        )
        roster_samples.write_roster_samples(samples, path=path)
        assert roster_samples.load_roster_samples(path) == samples

        # Corrupt and missing files restart the series instead of raising.
        with open(path, "w") as f:
            f.write("{not json")
        assert roster_samples.load_roster_samples(path) == {}
        assert roster_samples.load_roster_samples(str(Path(tmp_dir) / "nope.json")) == {}


def validate_rendered_table_has_no_dead_columns():
    """The original defect: every roster row rendered N/A in 6 of 9 columns."""
    members = [clan_member("#P1", "Alice", role="leader", trophies=1500,
                           donations=800, received=600)]
    totals = aggregate_wars([war([war_member("#P1", "Alice", attacks=2)])],
                            clan_members=members)
    apply_donation_deltas(totals["players"], {"#P1": {"donations": 120, "received": 90}})
    html = render_roster_table(totals["players"], clan_roster_known=True)

    import re
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    cells = [c.strip() for c in re.findall(r"<td>(.*?)</td>", rows[1], re.S)]
    assert cells == ["Alice", "#P1", "leader", "16", "1500", "800", "600", "+120", "+90"], cells
    assert "N/A" not in html


def main():
    validate_clan_fields_reach_the_roster()
    validate_current_members_without_wars_appear()
    validate_former_members_are_labelled_not_blank()
    validate_no_clan_data_degrades_quietly()
    validate_delta_needs_two_readings()
    validate_delta_measures_real_increase()
    validate_season_reset_does_not_go_negative()
    validate_sampling_is_rate_limited_and_pruned()
    validate_sample_file_roundtrip()
    validate_rendered_table_has_no_dead_columns()
    print("Roster live data validation passed.")


if __name__ == "__main__":
    main()
