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
        if not self.bot.command_channels:
            LOGGER.debug("Skipping reminder check; no command channel has been seen yet.")
            return

        try:
            war = await asyncio.to_thread(
                self.bot.clash_client.get_current_war,
                self.bot.settings.clan_tag,
            )
        except ClashApiError as exc:
            LOGGER.warning("Could not fetch current war for reminders: %s", exc)
            return
        except Exception:
            LOGGER.exception("Unexpected error while checking war reminders.")
            return

        overview = current_war_overview(war)
        if overview["state"] != "inWar":
            return

        end_time = overview["end_time"]
        if end_time is None:
            LOGGER.warning("Current war is inWar but has no parseable end time.")
            return

        key = stable_war_key(war)
        if not key:
            LOGGER.warning("Current war has no stable key; skipping reminders.")
            return

        seconds_left = int((end_time - datetime.now(timezone.utc)).total_seconds())
        for guild_id, channel_id in list(self.bot.command_channels.items()):
            sent_keys = {
                reminder_key
                for sent_guild_id, sent_war_key, reminder_key in self.sent_reminders
                if sent_guild_id == guild_id and sent_war_key == key
            }
            reminder = due_reminder(seconds_left, sent_keys)
            if reminder is None:
                continue

            reminder_key, label = reminder
            await self.send_reminder(guild_id, channel_id, key, reminder_key, label, war)

    async def send_reminder(self, guild_id, channel_id, war_key, reminder_key, label, war):
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except Exception:
                LOGGER.exception("Could not resolve reminder channel %s.", channel_id)
                return

        linked_players = await asyncio.to_thread(
            self.bot.linked_player_store.linked_players_for_guild,
            guild_id,
        )
        message = build_war_reminder_message(label, war, linked_players)

        try:
            await channel.send(message)
        except Exception:
            LOGGER.exception("Could not send war reminder to channel %s.", channel_id)
            return

        self.sent_reminders.add((guild_id, war_key, reminder_key))
        LOGGER.info("Sent %s war reminder for guild %s.", reminder_key, guild_id)
