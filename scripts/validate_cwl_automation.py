"""Validation for CWL recap + reminder automation.

Runs where the bot's runtime deps are installed (Droplet or a venv with
apscheduler/requests). Exercises the pure message builders, the warTag dedupe
key, the participation filter, and the recap scheduler's scan/dedupe/retry loop
against fixture snapshots using a fake bot (no Discord/API calls).

Usage:
    python3 scripts/validate_cwl_automation.py
"""

import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clashcommand.clash.cwl import (
    cwl_attacks_summary,
    cwl_participates,
    cwl_war_key,
    cwl_war_side,
)
from clashcommand.cwl_post_war_reports import (
    CWL_POST_WAR_REPORT_REMINDER_TYPE,
    CwlPostWarReportScheduler,
    build_cwl_post_war_report,
    cwl_result_label,
)
from clashcommand.cwl_reminders import (
    CwlReminderScheduler,
    base_reminder_keys,
    build_cwl_reminder_message,
    cwl_reminder_type,
)
from clashcommand.reminders import reminder_decision


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
OUR_CLAN = "#OURCLAN"


def load_fixture():
    with (FIXTURE_DIR / "cwl_war_opponent_side.json").open() as f:
        return json.load(f)


# --- Pure helper / builder checks -------------------------------------------

def validate_side_and_key():
    war = load_fixture()
    # Our clan sits on the opponent side; resolution must not assume war.clan.
    side_name, side = cwl_war_side(war, OUR_CLAN)
    assert side_name == "opponent", side_name
    assert side["name"] == "Our Clan"
    assert cwl_participates(war, OUR_CLAN) is True
    assert cwl_participates(war, "#SOMEONE_ELSE") is False
    # warTag is the dedupe identity.
    assert cwl_war_key(war) == "#WAR12345"
    # Fallback path when no _cwl/tag present.
    bare = {"clan": {"tag": "#X"}, "endTime": "20260101T000000.000Z"}
    assert cwl_war_key(bare) is not None


def validate_attacks_summary_cwl_defaults():
    war = load_fixture()
    # CWL omits attacksPerMember -> must default to 1.
    assert "attacksPerMember" not in war
    summary = cwl_attacks_summary(war, OUR_CLAN)
    assert summary["attacks_allowed"] == 1
    assert summary["possible_attacks"] == 3
    assert summary["used_attacks"] == 2
    remaining = summary["remaining_members"]
    assert [p["name"] for p in remaining] == ["Charlie"]
    # tag/used are needed by reminder mentions.
    assert remaining[0]["tag"] == "#P3"
    assert remaining[0]["used"] == 0


def validate_result_label():
    assert cwl_result_label({"stars": 18}, {"stars": 14}) == "Win"
    assert cwl_result_label({"stars": 10}, {"stars": 12}) == "Loss"
    assert (
        cwl_result_label(
            {"stars": 10, "destructionPercentage": 91},
            {"stars": 10, "destructionPercentage": 90},
        )
        == "Win"
    )
    assert (
        cwl_result_label(
            {"stars": 10, "destructionPercentage": 90},
            {"stars": 10, "destructionPercentage": 90},
        )
        == "Tie"
    )


def validate_recap_builder():
    war = load_fixture()
    message = build_cwl_post_war_report(war, OUR_CLAN)
    assert "CWL War Recap - Round 3: Win" in message
    assert "Our Clan vs Rival Clan" in message
    assert "Season: `2026-07`" in message
    assert "18-14" in message
    assert "`2/3` used, `1` missed" in message
    assert "Charlie: 1 missed attack" in message
    assert "MVP:** Alpha with 3 stars" in message
    # Non-participant clan yields no message.
    assert build_cwl_post_war_report(war, "#SOMEONE_ELSE") is None


def validate_reminder_message_mentions():
    war = load_fixture()
    war = copy.deepcopy(war)
    war["state"] = "inWar"
    linked = {"55501": {"player_name": "Charlie", "player_tag": "#P3"}}
    message = build_cwl_reminder_message("1 hour", war, OUR_CLAN, linked)
    assert "1 hour left in CWL war" in message
    assert "<@55501> (1 left)" in message  # linked -> mention by tag
    # A war with all attacks used -> everyone-done copy.
    done = copy.deepcopy(war)
    for member in done["opponent"]["members"]:
        member["attacks"] = [{"stars": 3, "destructionPercentage": 100, "order": 1}]
    assert "Everyone has used their CWL attack." in build_cwl_reminder_message(
        "1 hour", done, OUR_CLAN, {}
    )


def validate_reminder_namespacing_and_decision():
    assert cwl_reminder_type("1h") == "cwl_1h"
    assert cwl_reminder_type("3h") == "cwl_3h"
    # Stored CWL types map back to base keys the decision logic understands.
    assert base_reminder_keys({"cwl_3h", "cwl_1h"}) == {"3h", "1h"}
    assert base_reminder_keys({"post_war_report"}) == set()
    # Reused decision engine: 1h window fires, stale 3h suppressed once 1h sent.
    assert reminder_decision(3600, set())[0] == ("1h", "1 hour")
    assert reminder_decision(3 * 3600, set())[0] == ("3h", "3 hours")
    assert reminder_decision(3 * 3600, {"1h"})[0] is None


# --- Scheduler scan loop (fake bot, no Discord/API) -------------------------

class FakeStore:
    def __init__(self, clan_tag=OUR_CLAN, channels=None):
        self._clan_tag = clan_tag
        self._channels = channels or {"guild1": "1000"}
        self.events = set()

    def reminder_channels(self):
        return dict(self._channels)

    def guild_settings_map(self):
        return {
            guild_id: {"channel_id": channel_id, "clan_tag": self._clan_tag}
            for guild_id, channel_id in self._channels.items()
        }

    def get_clan_tag(self, guild_id):
        return self._clan_tag

    def has_reminder_event(self, guild_id, war_key, reminder_type):
        return (str(guild_id), war_key, reminder_type) in self.events

    def record_reminder_event(self, guild_id, war_key, reminder_type):
        self.events.add((str(guild_id), war_key, reminder_type))


class FakeChannel:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []

    async def send(self, message):
        if self.fail:
            raise RuntimeError("simulated Discord failure")
        self.messages.append(message)


class FakeSettings:
    clan_tag = ""


class FakeBot:
    def __init__(self, store, channel):
        self.linked_player_store = store
        self.command_channels = {}
        self.settings = FakeSettings()
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        return self._channel


def write_snapshot(data_dir, war, name="cwl_war_2026-07-05_12-02_WAR12345.json"):
    path = Path(data_dir) / name
    with path.open("w") as f:
        json.dump(war, f)
    return path


def make_scheduler(bot, data_dir):
    scheduler = CwlPostWarReportScheduler(bot, data_dir=str(data_dir))
    # Treat all fixture files as "fresh" (post-startup) so the scan evaluates them.
    scheduler.startup_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    return scheduler


def validate_scan_posts_and_dedupes():
    with tempfile.TemporaryDirectory() as tmp:
        write_snapshot(tmp, load_fixture())
        channel = FakeChannel()
        store = FakeStore()
        scheduler = make_scheduler(FakeBot(store, channel), tmp)

        asyncio.run(scheduler.check_completed_wars())
        assert len(channel.messages) == 1, channel.messages
        assert "CWL War Recap - Round 3: Win" in channel.messages[0]
        assert ("guild1", "#WAR12345", CWL_POST_WAR_REPORT_REMINDER_TYPE) in store.events
        assert "#WAR12345" in scheduler.seen_war_keys

        # Second scan must not double-post.
        asyncio.run(scheduler.check_completed_wars())
        assert len(channel.messages) == 1, channel.messages


def validate_scan_skips_nonparticipant():
    with tempfile.TemporaryDirectory() as tmp:
        write_snapshot(tmp, load_fixture())
        channel = FakeChannel()
        store = FakeStore(clan_tag="#NOT_IN_THIS_WAR")
        scheduler = make_scheduler(FakeBot(store, channel), tmp)

        asyncio.run(scheduler.check_completed_wars())
        assert channel.messages == []
        # Not participating is not a "seen" success -> stays retryable.
        assert "#WAR12345" not in scheduler.seen_war_keys


def validate_scan_retries_on_send_failure():
    with tempfile.TemporaryDirectory() as tmp:
        write_snapshot(tmp, load_fixture())
        channel = FakeChannel(fail=True)
        store = FakeStore()
        scheduler = make_scheduler(FakeBot(store, channel), tmp)

        asyncio.run(scheduler.check_completed_wars())
        # Send failed -> not recorded, not marked seen, will retry.
        assert ("guild1", "#WAR12345", CWL_POST_WAR_REPORT_REMINDER_TYPE) not in store.events
        assert "#WAR12345" not in scheduler.seen_war_keys

        # Recover: a later scan with a working channel posts and records.
        channel.fail = False
        asyncio.run(scheduler.check_completed_wars())
        assert len(channel.messages) == 1
        assert "#WAR12345" in scheduler.seen_war_keys


def validate_startup_preload_marks_seen():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_snapshot(tmp, load_fixture())
        channel = FakeChannel()
        store = FakeStore()
        scheduler = CwlPostWarReportScheduler(FakeBot(store, channel), data_dir=str(tmp))
        # Startup preload marks existing snapshot files seen by path (no parse),
        # so a mid-season deploy does not re-post ended rounds.
        scheduler.mark_existing_wars_seen()
        assert str(path) in scheduler.seen_paths

        asyncio.run(scheduler.check_completed_wars())
        assert channel.messages == []


def validate_scan_skips_pre_startup_files_without_parsing():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_snapshot(tmp, load_fixture())
        # Backdate the file so it predates the scheduler's startup_time.
        old = 946684800  # 2000-01-01 UTC
        os.utime(path, (old, old))
        channel = FakeChannel()
        store = FakeStore()
        # startup_time defaults to "now" at construction -> file is historical.
        scheduler = CwlPostWarReportScheduler(FakeBot(store, channel), data_dir=str(tmp))
        pending = scheduler.collect_pending_snapshots()
        assert pending == []
        assert str(path) in scheduler.seen_paths
        asyncio.run(scheduler.check_completed_wars())
        assert channel.messages == []


class FakeClashClient:
    def __init__(self, group, wars):
        self._group = group
        self._wars = wars
        self.group_calls = 0
        self.war_calls = 0

    def reset_counts(self):
        self.group_calls = 0
        self.war_calls = 0

    def get_cwl_league_group(self, clan_tag):
        self.group_calls += 1
        return self._group

    def get_cwl_war(self, war_tag):
        self.war_calls += 1
        return self._wars[war_tag]


class ReminderFakeBot:
    def __init__(self, clash_client):
        self.clash_client = clash_client
        self.command_channels = {}
        self.settings = FakeSettings()


def validate_reminder_round_cache():
    # Round 1 ended (our clan in #w1a); round 2 active (our clan on the OPPONENT
    # side of #w2a); round 3 not yet matched (#0 placeholders).
    group = {
        "state": "inWar",
        "rounds": [
            {"warTags": ["#w1a", "#w1b"]},
            {"warTags": ["#w2a", "#w2b"]},
            {"warTags": ["#0", "#0"]},
        ],
    }
    wars = {
        "#w1a": {"state": "warEnded", "clan": {"tag": "#OURCLAN"}, "opponent": {"tag": "#E1"}},
        "#w1b": {"state": "warEnded", "clan": {"tag": "#X"}, "opponent": {"tag": "#Y"}},
        "#w2a": {"state": "inWar", "clan": {"tag": "#Z"}, "opponent": {"tag": "#OURCLAN"}},
        "#w2b": {"state": "inWar", "clan": {"tag": "#P"}, "opponent": {"tag": "#Q"}},
    }
    client = FakeClashClient(group, wars)
    scheduler = CwlReminderScheduler(ReminderFakeBot(client))

    # First call: full scan. Round 1 -> fetch #w1a (ours, ended, stop round);
    # Round 2 -> fetch #w2a (ours, inWar) -> cache + return. 1 group, 2 wars.
    war = scheduler.active_cwl_war(OUR_CLAN)
    assert war is wars["#w2a"]
    assert cwl_war_side(war, OUR_CLAN)[0] == "opponent"
    assert client.group_calls == 1
    assert client.war_calls == 2, client.war_calls
    assert scheduler._active_war_tag_by_clan[OUR_CLAN] == "#w2a"

    # Second call: cache hit -> only the cached war is fetched, no group walk.
    client.reset_counts()
    war2 = scheduler.active_cwl_war(OUR_CLAN)
    assert war2 is wars["#w2a"]
    assert client.group_calls == 0
    assert client.war_calls == 1

    # Round 2 ends -> cached war no longer inWar -> invalidate + rescan finds no
    # active round for us -> None, and the stale cache entry is cleared.
    wars["#w2a"]["state"] = "warEnded"
    client.reset_counts()
    war3 = scheduler.active_cwl_war(OUR_CLAN)
    assert war3 is None
    assert client.group_calls == 1  # rescanned after cache miss
    assert OUR_CLAN not in scheduler._active_war_tag_by_clan


def main():
    validate_side_and_key()
    validate_attacks_summary_cwl_defaults()
    validate_result_label()
    validate_recap_builder()
    validate_reminder_message_mentions()
    validate_reminder_namespacing_and_decision()
    validate_scan_posts_and_dedupes()
    validate_scan_skips_nonparticipant()
    validate_scan_retries_on_send_failure()
    validate_startup_preload_marks_seen()
    validate_scan_skips_pre_startup_files_without_parsing()
    validate_reminder_round_cache()
    print("CWL automation validation passed.")


if __name__ == "__main__":
    main()
