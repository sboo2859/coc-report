import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from clashcommand.clash.client import ClashApiError
from clashcommand.clash.war import current_war_overview, stable_war_key
from clashcommand.formatting import missing_attack_lines


LOGGER = logging.getLogger("clashcommand.reminders")

REMINDER_CHECK_SECONDS = 300
THREE_HOURS_SECONDS = 3 * 60 * 60
ONE_HOUR_SECONDS = 60 * 60
THREE_HOUR_EARLY_SECONDS = 10 * 60
THREE_HOUR_LATE_SECONDS = 15 * 60
ONE_HOUR_EARLY_SECONDS = 10 * 60
ONE_HOUR_LATE_SECONDS = 15 * 60
REMINDER_ORDER = {
    "3h": 3,
    "1h": 1,
}


def normalize_clan_tag(clan_tag):
    normalized = str(clan_tag or "").strip().upper()
    if not normalized:
        return ""
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized


def due_reminder(seconds_left, sent_keys):
    reminder, _reason = reminder_decision(seconds_left, sent_keys)
    return reminder


def reminder_decision(seconds_left, sent_keys):
    sent_keys = set(sent_keys)
    if seconds_left <= 0:
        return None, "war already ended"

    if smaller_reminder_sent("3h", sent_keys):
        return None, "1h reminder already sent; suppressing stale 3h reminder"

    if "1h" in sent_keys:
        return None, "1h reminder already sent"

    if threshold_due(
        seconds_left,
        ONE_HOUR_SECONDS,
        early_seconds=ONE_HOUR_EARLY_SECONDS,
        late_seconds=ONE_HOUR_LATE_SECONDS,
    ):
        return ("1h", "1 hour"), "1h reminder due"

    if "3h" in sent_keys:
        return None, "3h reminder already sent"

    if threshold_due(
        seconds_left,
        THREE_HOURS_SECONDS,
        early_seconds=THREE_HOUR_EARLY_SECONDS,
        late_seconds=THREE_HOUR_LATE_SECONDS,
    ):
        return ("3h", "3 hours"), "3h reminder due"

    return None, "outside reminder windows"


def threshold_due(seconds_left, threshold_seconds, early_seconds=0, late_seconds=0):
    return (
        threshold_seconds - late_seconds
        <= seconds_left
        <= threshold_seconds + early_seconds
    )


def smaller_reminder_sent(reminder_key, sent_keys):
    reminder_order = REMINDER_ORDER.get(reminder_key)
    if reminder_order is None:
        return False

    return any(
        REMINDER_ORDER.get(sent_key, reminder_order) < reminder_order
        for sent_key in sent_keys
    )


def minutes_left(seconds_left):
    return round(seconds_left / 60, 1)


def sorted_reminder_keys(sent_keys):
    return sorted(str(sent_key) for sent_key in sent_keys)


def build_war_reminder_message(label, war, linked_players=None):
    missing_lines = missing_attack_lines(war, linked_players)
    lines = [f"⏰ {label} left in war", ""]

    if missing_lines:
        lines.append("🚨 Missing Attacks:")
        lines.extend(missing_lines)
    else:
        lines.append("Everyone has used all attacks.")

    return "\n".join(lines)


class WarReminderScheduler:
    def __init__(self, bot, interval_seconds=REMINDER_CHECK_SECONDS):
        self.bot = bot
        self.interval_seconds = interval_seconds
        self.scheduler = None
        self.sent_reminders = set()

    def start(self):
        if self.scheduler and self.scheduler.running:
            return

        self.scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self.scheduler.add_job(
            self.check_current_war,
            "interval",
            seconds=self.interval_seconds,
            id="war_reminder_check",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        LOGGER.info("Started war reminder scheduler.")

    def shutdown(self):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            LOGGER.info("Stopped war reminder scheduler.")

    async def check_current_war(self):
        LOGGER.info("War reminder check started.")
        saved_channels = await asyncio.to_thread(
            self.bot.linked_player_store.reminder_channels
        )
        channel_by_guild = dict(self.bot.command_channels)
        channel_by_guild.update(saved_channels)

        if not self.bot.command_channels:
            LOGGER.info(
                "War reminder check has no recently seen command channels: "
                "saved_reminder_channels=%s",
                len(saved_channels),
            )

        if not saved_channels:
            LOGGER.info(
                "War reminder check has no reminder channels configured: "
                "recent_command_channels=%s",
                len(self.bot.command_channels),
            )

        if not channel_by_guild:
            LOGGER.info(
                "War reminder check skipped: reason=%s recent_command_channels=%s "
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
                    "War reminder evaluation skipped: guild_id=%s clan_tag=%s "
                    "war_state=%s war_key=%s war_end_time=%s seconds_left=%s "
                    "minutes_left=%s sqlite_sent=%s memory_sent=%s reason=%s",
                    guild_id,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    [],
                    [],
                    "no clan configured",
                )
                continue

            try:
                war = await asyncio.to_thread(
                    self.bot.clash_client.get_current_war,
                    clan_tag,
                )
            except ClashApiError as exc:
                LOGGER.info(
                    "War reminder evaluation skipped: guild_id=%s clan_tag=%s "
                    "reason=%s error=%s",
                    guild_id,
                    clan_tag,
                    "could not fetch current war",
                    exc,
                )
                continue
            except Exception:
                LOGGER.exception(
                    "Unexpected error while checking war reminders: guild_id=%s clan_tag=%s",
                    guild_id,
                    clan_tag,
                )
                continue

            overview = current_war_overview(war)
            key = stable_war_key(war)
            if overview["state"] != "inWar":
                LOGGER.info(
                    "War reminder evaluation skipped: guild_id=%s clan_tag=%s "
                    "war_state=%s war_key=%s war_end_time=%s seconds_left=%s "
                    "minutes_left=%s sqlite_sent=%s memory_sent=%s reason=%s",
                    guild_id,
                    clan_tag,
                    overview["state"],
                    key,
                    overview["end_time"],
                    None,
                    None,
                    [],
                    [],
                    "war is not inWar",
                )
                continue

            end_time = overview["end_time"]
            if end_time is None:
                LOGGER.info(
                    "War reminder evaluation skipped: guild_id=%s clan_tag=%s "
                    "war_state=%s war_key=%s war_end_time=%s seconds_left=%s "
                    "minutes_left=%s sqlite_sent=%s memory_sent=%s reason=%s",
                    guild_id,
                    clan_tag,
                    overview["state"],
                    None,
                    None,
                    None,
                    None,
                    [],
                    [],
                    "current war has no parseable end time",
                )
                continue

            if not key:
                LOGGER.info(
                    "War reminder evaluation skipped: guild_id=%s clan_tag=%s "
                    "war_state=%s war_key=%s war_end_time=%s seconds_left=%s "
                    "minutes_left=%s sqlite_sent=%s memory_sent=%s reason=%s",
                    guild_id,
                    clan_tag,
                    overview["state"],
                    None,
                    end_time.isoformat(),
                    None,
                    None,
                    [],
                    [],
                    "current war has no stable key",
                )
                continue

            seconds_left = int((end_time - datetime.now(timezone.utc)).total_seconds())
            db_sent_keys = await asyncio.to_thread(
                self.bot.linked_player_store.reminder_types_for_war,
                guild_id,
                key,
            )
            memory_sent_keys = {
                reminder_key
                for sent_guild_id, sent_war_key, reminder_key in self.sent_reminders
                if sent_guild_id == guild_id and sent_war_key == key
            }
            sent_keys = set(db_sent_keys)
            sent_keys.update(memory_sent_keys)
            reminder, decision_reason = reminder_decision(seconds_left, sent_keys)
            selected_reminder = reminder[0] if reminder else None
            LOGGER.info(
                "War reminder evaluation: guild_id=%s clan_tag=%s war_state=%s "
                "war_key=%s war_end_time=%s seconds_left=%s minutes_left=%s "
                "sqlite_sent=%s memory_sent=%s selected=%s reason=%s",
                guild_id,
                clan_tag,
                overview["state"],
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
            )

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

    async def send_reminder(self, guild_id, channel_id, fallback_channel_id, war_key, reminder_key, label, war):
        already_sent = await asyncio.to_thread(
            self.bot.linked_player_store.has_reminder_event,
            guild_id,
            war_key,
            reminder_key,
        )
        if already_sent:
            self.sent_reminders.add((guild_id, war_key, reminder_key))
            LOGGER.info(
                "War reminder send skipped: guild_id=%s war_key=%s "
                "reminder_type=%s reason=%s",
                guild_id,
                war_key,
                reminder_key,
                "reminder already recorded in SQLite",
            )
            return

        if (guild_id, war_key, reminder_key) in self.sent_reminders:
            LOGGER.info(
                "War reminder send skipped: guild_id=%s war_key=%s "
                "reminder_type=%s reason=%s",
                guild_id,
                war_key,
                reminder_key,
                "reminder already present in in-memory cache",
            )
            return

        channel = await self.resolve_sendable_channel(channel_id, fallback_channel_id)
        if channel is None:
            LOGGER.warning(
                "War reminder send skipped: guild_id=%s war_key=%s "
                "reminder_type=%s reason=%s",
                guild_id,
                war_key,
                reminder_key,
                "could not resolve a sendable reminder channel",
            )
            return

        linked_players = await asyncio.to_thread(
            self.bot.linked_player_store.linked_players_for_guild,
            guild_id,
        )
        message = build_war_reminder_message(label, war, linked_players)

        sent = await self.send_to_reminder_channel(channel_id, fallback_channel_id, message)
        if not sent:
            LOGGER.warning(
                "War reminder send skipped: guild_id=%s war_key=%s "
                "reminder_type=%s reason=%s",
                guild_id,
                war_key,
                reminder_key,
                "could not send to any reminder channel",
            )
            return

        await asyncio.to_thread(
            self.bot.linked_player_store.record_reminder_event,
            guild_id,
            war_key,
            reminder_key,
        )
        self.sent_reminders.add((guild_id, war_key, reminder_key))
        LOGGER.info(
            "War reminder sent: guild_id=%s war_key=%s reminder_type=%s",
            guild_id,
            war_key,
            reminder_key,
        )

    async def resolve_sendable_channel(self, channel_id, fallback_channel_id=None):
        candidate_ids = []
        for candidate_id in (channel_id, fallback_channel_id):
            if candidate_id and candidate_id not in candidate_ids:
                candidate_ids.append(candidate_id)

        for candidate_id in candidate_ids:
            try:
                channel = self.bot.get_channel(int(candidate_id))
                if channel is None:
                    channel = await self.bot.fetch_channel(int(candidate_id))
            except Exception:
                LOGGER.warning("Could not resolve reminder channel %s.", candidate_id)
                continue

            if hasattr(channel, "send"):
                return channel

            LOGGER.warning("Reminder channel %s is not sendable.", candidate_id)

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
                LOGGER.warning("Could not send war reminder to channel %s.", candidate_id)

        return False
