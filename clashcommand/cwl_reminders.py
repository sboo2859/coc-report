import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from clashcommand.clash.client import ClashApiError
from clashcommand.clash.cwl import (
    cwl_attacks_summary,
    cwl_group_war_tags,
    cwl_opponent_side,
    cwl_participates,
    cwl_war_key,
)
from clashcommand.clash.time import parse_optional_coc_time
from clashcommand.formatting import missed_player_label
from clashcommand.reminders import (
    minutes_left,
    reminder_decision,
    sorted_reminder_keys,
)


LOGGER = logging.getLogger("clashcommand.cwl_reminders")

CWL_REMINDER_CHECK_SECONDS = 300
CWL_REMINDER_PREFIX = "cwl_"


def normalize_clan_tag(clan_tag):
    normalized = str(clan_tag or "").strip().upper()
    if not normalized:
        return ""
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized


def cwl_reminder_type(base_reminder_key):
    """Namespace CWL reminder events so they never collide with regular ones."""
    return f"{CWL_REMINDER_PREFIX}{base_reminder_key}"


def base_reminder_keys(stored_types):
    """Strip the CWL prefix so stored events feed reminder_decision's base logic."""
    base_keys = set()
    for stored_type in stored_types:
        if stored_type.startswith(CWL_REMINDER_PREFIX):
            base_keys.add(stored_type[len(CWL_REMINDER_PREFIX):])
    return base_keys


def build_cwl_reminder_message(label, war, clan_tag, linked_players=None):
    linked_players = linked_players or {}
    summary = cwl_attacks_summary(war, clan_tag)
    remaining = summary.get("remaining_members", []) if summary else []

    lines = [f"⏰ {label} left in CWL war", ""]
    if remaining:
        lines.append("🚨 Missing Attacks:")
        for player in remaining:
            lines.append(
                f"{missed_player_label(player, linked_players)} ({player['remaining']} left)"
            )
    else:
        lines.append("Everyone has used their CWL attack.")

    return "\n".join(lines)


class CwlReminderScheduler:
    def __init__(self, bot, interval_seconds=CWL_REMINDER_CHECK_SECONDS):
        self.bot = bot
        self.interval_seconds = interval_seconds
        self.scheduler = None
        self.sent_reminders = set()

    def start(self):
        if self.scheduler and self.scheduler.running:
            return

        self.scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self.scheduler.add_job(
            self.check_current_cwl_war,
            "interval",
            seconds=self.interval_seconds,
            id="cwl_reminder_check",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        LOGGER.info("Started CWL reminder scheduler.")

    def shutdown(self):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            LOGGER.info("Stopped CWL reminder scheduler.")

    async def check_current_cwl_war(self):
        LOGGER.info("CWL reminder check started.")
        saved_channels = await asyncio.to_thread(
            self.bot.linked_player_store.reminder_channels
        )
        channel_by_guild = dict(self.bot.command_channels)
        channel_by_guild.update(saved_channels)

        if not channel_by_guild:
            LOGGER.info(
                "CWL reminder check skipped: reason=%s recent_command_channels=%s "
                "saved_reminder_channels=%s",
                "no command channel seen and no reminder channels configured",
                len(self.bot.command_channels),
                len(saved_channels),
            )
            return

        for guild_id, channel_id in list(channel_by_guild.items()):
            clan_tag = await self.clan_tag_for_guild(guild_id)
            if not clan_tag:
                LOGGER.info(
                    "CWL reminder evaluation skipped: guild_id=%s reason=%s",
                    guild_id,
                    "no clan configured",
                )
                continue

            try:
                war = await asyncio.to_thread(self.active_cwl_war, clan_tag)
            except ClashApiError as exc:
                LOGGER.info(
                    "CWL reminder evaluation skipped: guild_id=%s clan_tag=%s reason=%s error=%s",
                    guild_id,
                    clan_tag,
                    "could not fetch CWL data",
                    exc,
                )
                continue
            except Exception:
                LOGGER.exception(
                    "Unexpected error while checking CWL reminders: guild_id=%s clan_tag=%s",
                    guild_id,
                    clan_tag,
                )
                continue

            if war is None:
                LOGGER.info(
                    "CWL reminder evaluation skipped: guild_id=%s clan_tag=%s reason=%s",
                    guild_id,
                    clan_tag,
                    "no active inWar CWL round for this clan",
                )
                continue

            key = cwl_war_key(war)
            end_time = parse_optional_coc_time(war.get("endTime"))
            if end_time is None:
                LOGGER.info(
                    "CWL reminder evaluation skipped: guild_id=%s clan_tag=%s war_key=%s reason=%s",
                    guild_id,
                    clan_tag,
                    key,
                    "CWL war has no parseable end time",
                )
                continue

            if not key:
                LOGGER.info(
                    "CWL reminder evaluation skipped: guild_id=%s clan_tag=%s reason=%s",
                    guild_id,
                    clan_tag,
                    "CWL war has no stable key",
                )
                continue

            seconds_left = int((end_time - datetime.now(timezone.utc)).total_seconds())
            db_stored_types = await asyncio.to_thread(
                self.bot.linked_player_store.reminder_types_for_war,
                guild_id,
                key,
            )
            db_sent_keys = base_reminder_keys(db_stored_types)
            memory_sent_keys = {
                base_reminder_key
                for sent_guild_id, sent_war_key, base_reminder_key in self.sent_reminders
                if sent_guild_id == guild_id and sent_war_key == key
            }
            sent_keys = set(db_sent_keys)
            sent_keys.update(memory_sent_keys)

            reminder, decision_reason = reminder_decision(seconds_left, sent_keys)
            selected_reminder = reminder[0] if reminder else None
            LOGGER.info(
                "CWL reminder evaluation: guild_id=%s clan_tag=%s war_key=%s "
                "war_end_time=%s seconds_left=%s minutes_left=%s "
                "sqlite_sent=%s memory_sent=%s selected=%s reason=%s",
                guild_id,
                clan_tag,
                key,
                end_time.isoformat(),
                seconds_left,
                minutes_left(seconds_left),
                sorted_reminder_keys(db_sent_keys),
                sorted_reminder_keys(memory_sent_keys),
                selected_reminder,
                decision_reason,
            )
            if reminder is None:
                continue

            reminder_key, label = reminder
            fallback_channel_id = self.bot.command_channels.get(guild_id)
            await self.send_reminder(
                guild_id,
                channel_id,
                fallback_channel_id,
                key,
                reminder_key,
                label,
                war,
                clan_tag,
            )

    def active_cwl_war(self, clan_tag):
        """Return the clan's current inWar CWL round war, or None.

        CWL rounds are staggered so a clan has at most one inWar round war at a
        time; this fetches the league group and scans round war tags for it.
        """
        group = self.bot.clash_client.get_cwl_league_group(clan_tag)
        if not group or group.get("state") == "notInWar":
            return None

        for _round_index, war_tag in cwl_group_war_tags(group):
            try:
                war = self.bot.clash_client.get_cwl_war(war_tag)
            except ClashApiError as exc:
                LOGGER.warning("Could not fetch CWL war %s: %s", war_tag, exc)
                continue
            if not cwl_participates(war, clan_tag):
                continue
            if war.get("state") == "inWar":
                return war
        return None

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

    async def send_reminder(
        self,
        guild_id,
        channel_id,
        fallback_channel_id,
        war_key,
        reminder_key,
        label,
        war,
        clan_tag,
    ):
        stored_type = cwl_reminder_type(reminder_key)
        already_sent = await asyncio.to_thread(
            self.bot.linked_player_store.has_reminder_event,
            guild_id,
            war_key,
            stored_type,
        )
        if already_sent:
            self.sent_reminders.add((guild_id, war_key, reminder_key))
            LOGGER.info(
                "CWL reminder send skipped: guild_id=%s war_key=%s reminder_type=%s reason=%s",
                guild_id,
                war_key,
                stored_type,
                "reminder already recorded in SQLite",
            )
            return

        if (guild_id, war_key, reminder_key) in self.sent_reminders:
            LOGGER.info(
                "CWL reminder send skipped: guild_id=%s war_key=%s reminder_type=%s reason=%s",
                guild_id,
                war_key,
                stored_type,
                "reminder already present in in-memory cache",
            )
            return

        linked_players = await asyncio.to_thread(
            self.bot.linked_player_store.linked_players_for_guild,
            guild_id,
        )
        message = build_cwl_reminder_message(label, war, clan_tag, linked_players)

        sent = await self.send_to_reminder_channel(channel_id, fallback_channel_id, message)
        if not sent:
            LOGGER.warning(
                "CWL reminder send skipped: guild_id=%s war_key=%s reminder_type=%s reason=%s",
                guild_id,
                war_key,
                stored_type,
                "could not send to any reminder channel",
            )
            return

        await asyncio.to_thread(
            self.bot.linked_player_store.record_reminder_event,
            guild_id,
            war_key,
            stored_type,
        )
        self.sent_reminders.add((guild_id, war_key, reminder_key))
        LOGGER.info(
            "CWL reminder sent: guild_id=%s war_key=%s reminder_type=%s",
            guild_id,
            war_key,
            stored_type,
        )

    async def resolve_sendable_channel(self, channel_id):
        try:
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id))
        except Exception:
            LOGGER.warning("Could not resolve CWL reminder channel %s.", channel_id)
            return None

        if hasattr(channel, "send"):
            return channel
        LOGGER.warning("CWL reminder channel %s is not sendable.", channel_id)
        return None

    async def send_to_reminder_channel(self, channel_id, fallback_channel_id, message):
        candidate_ids = []
        for candidate_id in (channel_id, fallback_channel_id):
            if candidate_id and candidate_id not in candidate_ids:
                candidate_ids.append(candidate_id)

        for candidate_id in candidate_ids:
            channel = await self.resolve_sendable_channel(candidate_id)
            if channel is None:
                continue

            try:
                await channel.send(message)
                return True
            except Exception:
                LOGGER.warning("Could not send CWL reminder to channel %s.", candidate_id)

        return False
