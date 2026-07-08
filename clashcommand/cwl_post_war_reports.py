import asyncio
import glob
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from clashcommand.clash.cwl import (
    cwl_attacks_summary,
    cwl_opponent_side,
    cwl_participates,
    cwl_war_key,
)


LOGGER = logging.getLogger("clashcommand.cwl_post_war_reports")

CWL_POST_WAR_REPORT_CHECK_SECONDS = 300
DEFAULT_CWL_WAR_RESULTS_DIR = "data/cwl_war_results"
CWL_POST_WAR_REPORT_REMINDER_TYPE = "cwl_post_war_report"


@dataclass
class CwlPostWarReportResult:
    sent_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    already_posted_count: int = 0
    reasons: list = field(default_factory=list)

    def add_skip(self, reason):
        self.skipped_count += 1
        self.reasons.append(reason)

    def add_failure(self, reason):
        self.failed_count += 1
        self.reasons.append(reason)

    def add_sent(self):
        self.sent_count += 1

    def add_already_posted(self):
        self.already_posted_count += 1
        self.add_skip("already posted according to SQLite")

    def should_mark_seen(self):
        return self.sent_count > 0 or self.already_posted_count > 0


def text_or_default(value, default="Unknown"):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def format_percent(value):
    if isinstance(value, (int, float)):
        return f"{float(value):.1f}%"
    return "N/A"


def normalize_clan_tag(clan_tag):
    normalized = str(clan_tag or "").strip().upper()
    if not normalized:
        return ""
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized


def load_war_file(path):
    with open(path) as f:
        return json.load(f)


def iter_cwl_snapshot_paths(data_dir=DEFAULT_CWL_WAR_RESULTS_DIR):
    pattern = os.path.join(data_dir, "cwl_war_*.json")
    return sorted(glob.glob(pattern))


def war_file_mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
    except OSError:
        return None


def load_cwl_snapshots(data_dir=DEFAULT_CWL_WAR_RESULTS_DIR):
    snapshots = []
    for path in iter_cwl_snapshot_paths(data_dir):
        try:
            war = load_war_file(path)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Skipping unreadable CWL war snapshot %s: %s", path, exc)
            continue
        snapshots.append((path, war))
    return snapshots


def cwl_meta(war):
    meta = war.get("_cwl")
    return meta if isinstance(meta, dict) else {}


def cwl_result_label(our_side, opponent):
    our_stars = our_side.get("stars", 0) or 0
    opponent_stars = opponent.get("stars", 0) or 0
    if our_stars > opponent_stars:
        return "Win"
    if our_stars < opponent_stars:
        return "Loss"

    our_destruction = our_side.get("destructionPercentage")
    opponent_destruction = opponent.get("destructionPercentage")
    if isinstance(our_destruction, (int, float)) and isinstance(opponent_destruction, (int, float)):
        if our_destruction > opponent_destruction:
            return "Win"
        if our_destruction < opponent_destruction:
            return "Loss"

    return "Tie"


def cwl_member_stats(our_side):
    players = []
    members = our_side.get("members", [])
    if not isinstance(members, list):
        members = []

    for member in members:
        attacks = member.get("attacks", [])
        if not isinstance(attacks, list):
            attacks = []
        stars = sum(attack.get("stars", 0) for attack in attacks if isinstance(attack, dict))
        destruction_values = [
            float(attack["destructionPercentage"])
            for attack in attacks
            if isinstance(attack, dict) and isinstance(attack.get("destructionPercentage"), (int, float))
        ]
        avg_destruction = (
            sum(destruction_values) / len(destruction_values)
            if destruction_values
            else None
        )
        players.append(
            {
                "name": text_or_default(member.get("name")),
                "attacks": len(attacks),
                "stars": stars,
                "avg_destruction": avg_destruction,
                "perfect_attacks": sum(
                    1
                    for attack in attacks
                    if isinstance(attack, dict) and attack.get("stars") == 3
                ),
            }
        )
    return players


def cwl_top_performers(our_side, limit=3):
    players = [player for player in cwl_member_stats(our_side) if player["attacks"] > 0]
    players.sort(
        key=lambda player: (
            -player["stars"],
            -(player["avg_destruction"] or 0),
            player["name"].lower(),
        )
    )
    return players[:limit]


def build_cwl_post_war_report(war, clan_tag, website_url=None):
    our_side, opponent = cwl_opponent_side(war, clan_tag)
    if not our_side:
        return None

    meta = cwl_meta(war)
    round_index = meta.get("round")
    season = meta.get("season")
    result = cwl_result_label(our_side, opponent)
    attacks = cwl_attacks_summary(war, clan_tag) or {}

    heading = "**CWL War Recap"
    if round_index:
        heading += f" - Round {round_index}"
    heading += f": {result}**"

    lines = [
        heading,
        f"**{text_or_default(our_side.get('name'), 'Clan')} vs "
        f"{text_or_default(opponent.get('name'), 'Opponent')}**",
    ]
    if season:
        lines.append(f"Season: `{season}`")
    lines.extend(
        [
            f"Final score: `{our_side.get('stars', 0) or 0}-{opponent.get('stars', 0) or 0}` stars",
            (
                "Destruction: "
                f"`{format_percent(our_side.get('destructionPercentage'))}` / "
                f"`{format_percent(opponent.get('destructionPercentage'))}`"
            ),
        ]
    )

    if attacks:
        lines.append(
            "Attacks: "
            f"`{attacks.get('used_attacks', 0)}/{attacks.get('possible_attacks', 0)}` used, "
            f"`{sum(player['remaining'] for player in attacks.get('remaining_members', []))}` missed"
        )

    remaining_members = attacks.get("remaining_members", []) if attacks else []
    if remaining_members:
        lines.extend(["", "**Missed attacks:**"])
        for player in remaining_members[:10]:
            attack_label = "attack" if player["remaining"] == 1 else "attacks"
            lines.append(f"- {player['name']}: {player['remaining']} missed {attack_label}")
        extra_count = len(remaining_members) - 10
        if extra_count > 0:
            lines.append(f"- and {extra_count} more")
    else:
        lines.extend(["", "No missed attacks."])

    performers = cwl_top_performers(our_side)
    if performers:
        lines.extend(["", "**Top performers:**"])
        for player in performers:
            destruction = (
                f", {player['avg_destruction']:.1f}% avg"
                if player["avg_destruction"] is not None
                else ""
            )
            lines.append(f"- {player['name']}: {player['stars']} stars{destruction}")

        mvp = performers[0]
        lines.extend(["", f"**MVP:** {mvp['name']} with {mvp['stars']} stars."])

    if website_url:
        lines.extend(["", f"Full report: {website_url}"])

    return "\n".join(lines)


class CwlPostWarReportScheduler:
    def __init__(
        self,
        bot,
        interval_seconds=CWL_POST_WAR_REPORT_CHECK_SECONDS,
        data_dir=DEFAULT_CWL_WAR_RESULTS_DIR,
    ):
        self.bot = bot
        self.interval_seconds = interval_seconds
        self.data_dir = data_dir
        self.scheduler = None
        self.startup_time = datetime.now(timezone.utc)
        self.seen_war_keys = set()

    def start(self):
        if self.scheduler and self.scheduler.running:
            return

        self.mark_existing_wars_seen()
        self.scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self.scheduler.add_job(
            self.check_completed_wars,
            "interval",
            seconds=self.interval_seconds,
            id="cwl_post_war_report_check",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        LOGGER.info("Started CWL post-war report scheduler.")

    def shutdown(self):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            LOGGER.info("Stopped CWL post-war report scheduler.")

    def mark_existing_wars_seen(self):
        for _path, war in load_cwl_snapshots(self.data_dir):
            key = cwl_war_key(war)
            if key:
                self.seen_war_keys.add(key)
        LOGGER.info(
            "CWL post-war recap startup preload marked %s existing completed war(s) as seen: "
            "reason=%s",
            len(self.seen_war_keys),
            "historical snapshot anti-spam guard",
        )

    async def check_completed_wars(self):
        for path, war in load_cwl_snapshots(self.data_dir):
            key = cwl_war_key(war)
            if not key:
                LOGGER.warning("Skipping CWL post-war recap for %s: no stable war key.", path)
                continue

            if key in self.seen_war_keys:
                continue

            mtime = war_file_mtime(path)
            if mtime is not None and mtime < self.startup_time:
                self.seen_war_keys.add(key)
                LOGGER.info(
                    "CWL post-war recap seen marker added: war_key=%s path=%s reason=%s",
                    key,
                    path,
                    "snapshot predates scheduler startup",
                )
                continue

            result = await self.post_war_to_configured_guilds(key, war)
            if result.should_mark_seen():
                self.seen_war_keys.add(key)
                LOGGER.info(
                    "CWL post-war recap seen marker added: war_key=%s path=%s "
                    "sent_count=%s already_posted_count=%s skipped_count=%s "
                    "failed_count=%s reasons=%s",
                    key,
                    path,
                    result.sent_count,
                    result.already_posted_count,
                    result.skipped_count,
                    result.failed_count,
                    result.reasons,
                )
            else:
                LOGGER.warning(
                    "CWL post-war recap not marked seen; will retry: war_key=%s path=%s "
                    "sent_count=%s skipped_count=%s failed_count=%s reasons=%s",
                    key,
                    path,
                    result.sent_count,
                    result.skipped_count,
                    result.failed_count,
                    result.reasons,
                )

    async def post_war_to_configured_guilds(self, war_key, war):
        result = CwlPostWarReportResult()
        saved_channels = await asyncio.to_thread(
            self.bot.linked_player_store.reminder_channels
        )
        channel_by_guild = dict(self.bot.command_channels)
        channel_by_guild.update(saved_channels)

        if not channel_by_guild:
            reason = "no configured recap channels"
            result.add_skip(reason)
            LOGGER.warning(
                "Skipping CWL post-war report: war_key=%s reason=%s saved_channels=%s "
                "recent_command_channels=%s",
                war_key,
                reason,
                len(saved_channels),
                len(self.bot.command_channels),
            )
            return result

        matched_guild = False

        for guild_id, channel_id in list(channel_by_guild.items()):
            clan_tag = await self.clan_tag_for_guild(guild_id)
            if not clan_tag:
                result.add_skip(f"guild {guild_id}: no clan configured")
                LOGGER.info(
                    "Skipping CWL post-war report for guild: guild_id=%s war_key=%s reason=%s",
                    guild_id,
                    war_key,
                    "no clan configured",
                )
                continue
            if not cwl_participates(war, clan_tag):
                result.add_skip(f"guild {guild_id}: clan not in this CWL war")
                LOGGER.info(
                    "Skipping CWL post-war report for guild: guild_id=%s war_key=%s "
                    "reason=%s configured_clan=%s war_clan=%s war_opponent=%s",
                    guild_id,
                    war_key,
                    "clan not in this CWL war",
                    clan_tag,
                    war.get("clan", {}).get("tag"),
                    war.get("opponent", {}).get("tag"),
                )
                continue

            matched_guild = True
            already_posted = await asyncio.to_thread(
                self.bot.linked_player_store.has_reminder_event,
                guild_id,
                war_key,
                CWL_POST_WAR_REPORT_REMINDER_TYPE,
            )
            if already_posted:
                result.add_already_posted()
                LOGGER.info(
                    "Skipping CWL post-war report send: guild_id=%s war_key=%s reason=%s",
                    guild_id,
                    war_key,
                    "already recorded in SQLite",
                )
                continue

            sent, reason = await self.send_report(guild_id, channel_id, war_key, war, clan_tag)
            if sent:
                result.add_sent()
                await asyncio.to_thread(
                    self.bot.linked_player_store.record_reminder_event,
                    guild_id,
                    war_key,
                    CWL_POST_WAR_REPORT_REMINDER_TYPE,
                )
            else:
                result.add_failure(f"guild {guild_id}: {reason}")

        if not matched_guild:
            result.add_skip("no configured guild participated in this CWL war")
            LOGGER.warning(
                "Skipping CWL post-war report: war_key=%s reason=%s war_clan=%s war_opponent=%s",
                war_key,
                "no configured guild participated in this CWL war",
                war.get("clan", {}).get("tag"),
                war.get("opponent", {}).get("tag"),
            )

        return result

    async def clan_tag_for_guild(self, guild_id):
        stored_clan_tag = await asyncio.to_thread(
            self.bot.linked_player_store.get_clan_tag,
            guild_id,
        )
        stored_clan_tag = normalize_clan_tag(stored_clan_tag)
        if stored_clan_tag:
            return stored_clan_tag

        fallback_clan_tag = normalize_clan_tag(self.bot.settings.clan_tag)
        return fallback_clan_tag or None

    async def send_report(self, guild_id, channel_id, war_key, war, clan_tag):
        channel = await self.resolve_sendable_channel(channel_id)
        if channel is None:
            reason = "no sendable recap channel"
            LOGGER.warning(
                "Could not send CWL post-war report: guild_id=%s war_key=%s channel_id=%s reason=%s",
                guild_id,
                war_key,
                channel_id,
                reason,
            )
            return False, reason

        website_url = os.environ.get("REPORT_SITE_URL", "").strip()
        message = build_cwl_post_war_report(war, clan_tag, website_url=website_url)
        if message is None:
            reason = "configured clan not found in CWL war"
            LOGGER.warning(
                "Could not build CWL post-war report: guild_id=%s war_key=%s reason=%s",
                guild_id,
                war_key,
                reason,
            )
            return False, reason

        try:
            await channel.send(message)
        except Exception as exc:
            reason = f"Discord send failed: {exc}"
            LOGGER.warning(
                "Could not send CWL post-war report: guild_id=%s war_key=%s channel_id=%s reason=%s",
                guild_id,
                war_key,
                channel_id,
                reason,
            )
            return False, reason

        LOGGER.info(
            "Posted CWL war recap: guild_id=%s war_key=%s channel_id=%s",
            guild_id,
            war_key,
            channel_id,
        )
        return True, "sent"

    async def resolve_sendable_channel(self, channel_id):
        try:
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id))
        except Exception:
            LOGGER.warning("Could not resolve CWL post-war report channel %s.", channel_id)
            return None

        if hasattr(channel, "send"):
            return channel
        return None
