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


def normalize_clan_tag(clan_tag):
    normalized = str(clan_tag or "").strip().upper()
    if not normalized:
        return ""
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized


def due_reminder(seconds_left, sent_keys):
    if seconds_left <= 0:
        return None
    if seconds_left <= ONE_HOUR_SECONDS and "1h" not in sent_keys:
        return ("1h", "1 hour")
    if seconds_left <= THREE_HOURS_SECONDS and "3h" not in sent_keys:
        return ("3h", "3 hours")
    return None


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
        saved_channels = await asyncio.to_thread(
            self.bot.linked_player_store.reminder_channels
        )
        channel_by_guild = dict(self.bot.command_channels)
        channel_by_guild.update(saved_channels)

        if not channel_by_guild:
            LOGGER.debug("Skipping reminder check; no command channel has been seen yet.")
            return

        for guild_id, channel_id in list(channel_by_guild.items()):
            clan_tag = await self.clan_tag_for_guild(guild_id)
            if not clan_tag:
                LOGGER.debug("Skipping reminder check for guild %s; no clan configured.", guild_id)
                continue

            try:
                war = await asyncio.to_thread(
                    self.bot.clash_client.get_current_war,
                    clan_tag,
                )
            except ClashApiError as exc:
                LOGGER.warning("Could not fetch current war for reminders: %s", exc)
                continue
            except Exception:
                LOGGER.exception("Unexpected error while checking war reminders.")
                continue

            overview = current_war_overview(war)
            if overview["state"] != "inWar":
                continue

            end_time = overview["end_time"]
            if end_time is None:
                LOGGER.warning("Current war is inWar but has no parseable end time.")
                continue

            key = stable_war_key(war)
            if not key:
                LOGGER.warning("Current war has no stable key; skipping reminders.")
                continue

            seconds_left = int((end_time - datetime.now(timezone.utc)).total_seconds())
            sent_keys = {
                reminder_key
                for sent_guild_id, sent_war_key, reminder_key in self.sent_reminders
                if sent_guild_id == guild_id and sent_war_key == key
            }
            reminder = due_reminder(seconds_left, sent_keys)
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
            LOGGER.debug(
                "Skipping already-recorded %s war reminder for guild %s.",
                reminder_key,
                guild_id,
            )
            return

        channel = await self.resolve_sendable_channel(channel_id, fallback_channel_id)
        if channel is None:
            LOGGER.warning(
                "Could not resolve a sendable reminder channel for guild %s.",
                guild_id,
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
                "Could not send war reminder to any channel for guild %s.",
                guild_id,
            )
            return

        await asyncio.to_thread(
            self.bot.linked_player_store.record_reminder_event,
            guild_id,
            war_key,
            reminder_key,
        )
        self.sent_reminders.add((guild_id, war_key, reminder_key))
        LOGGER.info("Sent %s war reminder for guild %s.", reminder_key, guild_id)

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
