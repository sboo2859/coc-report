import asyncio
import logging
import sys
from datetime import datetime, timezone

import discord
from discord.ext import commands

from clashcommand.clash.client import ClashApiError, ClashClient
from clashcommand.clash.time import format_central_time
from clashcommand.clash.war import current_war_overview
from clashcommand.config import ConfigError, load_settings
from clashcommand.db import LinkedPlayerStore
from clashcommand.formatting import build_missed_response, normalize_player_name
from clashcommand.reminders import WarReminderScheduler


LOGGER = logging.getLogger("clashcommand")


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def format_duration_until(value):
    if value is None:
        return "Unavailable"

    seconds = int((value - datetime.now(timezone.utc)).total_seconds())
    if seconds <= 0:
        return "ended"

    minutes = max(1, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours <= 0:
        return f"{minutes}m"
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h {minutes}m"


def format_percent(value):
    if isinstance(value, (int, float)):
        return f"{float(value):.1f}%"
    return "N/A"


def build_war_response(war):
    overview = current_war_overview(war)
    state = overview["state"]

    if state == "notInWar":
        return f"No active war is currently in progress for `{overview['clan']['name']}`."

    if state == "preparation":
        opponent = overview["opponent"]["name"]
        start_time = format_central_time(overview["start_time"])
        return "\n".join(
            [
                f"War is in preparation against **{opponent}**.",
                f"Battle day starts: `{start_time}`",
            ]
        )

    clan = overview["clan"]
    opponent = overview["opponent"]
    attacks = overview["attack_summary"]
    time_left = format_duration_until(overview["end_time"])

    lines = [
        f"**{clan['name']} vs {opponent['name']}**",
        f"State: `{state}`",
        f"Score: `{clan['stars']}-{opponent['stars']}` stars",
        (
            "Destruction: "
            f"`{format_percent(clan['destruction_percentage'])}` / "
            f"`{format_percent(opponent['destruction_percentage'])}`"
        ),
        (
            "Attacks: "
            f"`{attacks['used_attacks']}/{attacks['possible_attacks']}` used, "
            f"`{attacks['unused_attacks']}` remaining"
        ),
        f"Battle started: `{format_central_time(overview['start_time'])}`",
        f"Ends: `{format_central_time(overview['end_time'])}` ({time_left})",
    ]

    remaining_members = attacks["remaining_members"]
    if remaining_members:
        lines.extend(["", "**Members with attacks remaining:**"])
        for player in remaining_members[:15]:
            attack_label = "attack" if player["remaining"] == 1 else "attacks"
            lines.append(f"- {player['name']}: {player['remaining']} {attack_label}")

        extra_count = len(remaining_members) - 15
        if extra_count > 0:
            lines.append(f"- and {extra_count} more")
    else:
        lines.extend(["", "Everyone has used all available attacks."])

    return "\n".join(lines)


def build_roster_unlinked_response(clan_members, linked_players=None):
    linked_players = linked_players or {}
    if not clan_members:
        return "No clan members were returned by the Clash API."

    linked_names = {
        normalize_player_name(player_name)
        for player_name in linked_players.values()
    }
    unlinked_names = []

    for member in clan_members:
        name = str(member.get("name") or "").strip()
        if not name:
            continue
        if normalize_player_name(name) not in linked_names:
            unlinked_names.append(name)

    if not unlinked_names:
        return "All players are linked."

    return "\n".join(["⚠️ Unlinked Players:", *unlinked_names])


def clash_api_error_message(exc):
    if exc.is_access_denied:
        return (
            "Clash API access was denied. Check that the API token is valid "
            "and that this server's public IP is allowlisted for the token."
        )
    return f"Could not fetch current war: {exc}"


class ClashCommandBot(commands.Bot):
    def __init__(self, settings):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.clash_client = ClashClient(settings.clash_api_token)
        self.linked_player_store = LinkedPlayerStore(settings.db_path)
        self.command_channels = {}
        self.reminder_scheduler = WarReminderScheduler(self)

    async def setup_hook(self):
        await asyncio.to_thread(self.linked_player_store.initialize)
        self.reminder_scheduler.start()

        if self.settings.discord_test_guild_id is None:
            synced = await self.tree.sync()
            LOGGER.info("Synced %s global slash command(s).", len(synced))
            return

        guild = discord.Object(id=self.settings.discord_test_guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        LOGGER.info(
            "Synced %s slash command(s) to test guild %s.",
            len(synced),
            self.settings.discord_test_guild_id,
        )

    async def on_ready(self):
        LOGGER.info("Logged in as %s (%s).", self.user, self.user.id if self.user else "unknown")

    async def close(self):
        self.reminder_scheduler.shutdown()
        await super().close()


async def fetch_current_war(bot):
    return await asyncio.to_thread(
        bot.clash_client.get_current_war,
        bot.settings.clan_tag,
    )


async def fetch_clan_members(bot):
    return await asyncio.to_thread(
        bot.clash_client.get_clan_members,
        bot.settings.clan_tag,
    )


def guild_id_for_interaction(interaction):
    if interaction.guild_id is None:
        return "dm"
    return str(interaction.guild_id)


def remember_command_channel(bot, interaction):
    if interaction.guild_id is None or interaction.channel_id is None:
        return
    bot.command_channels[str(interaction.guild_id)] = str(interaction.channel_id)


def has_manage_server_permission(interaction):
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.manage_guild)


async def load_linked_players(bot, guild_id):
    return await asyncio.to_thread(
        bot.linked_player_store.linked_players_for_guild,
        guild_id,
    )


async def save_linked_player(bot, guild_id, discord_user_id, player_name):
    await asyncio.to_thread(
        bot.linked_player_store.upsert_linked_player,
        guild_id,
        discord_user_id,
        player_name,
    )


def create_bot(settings):
    bot = ClashCommandBot(settings)

    @bot.tree.command(name="war", description="Show current Clash of Clans war status.")
    async def war(interaction: discord.Interaction):
        remember_command_channel(bot, interaction)
        await interaction.response.defer(thinking=True)

        try:
            current_war = await fetch_current_war(bot)
        except ClashApiError as exc:
            await interaction.followup.send(clash_api_error_message(exc))
            return
        except Exception as exc:
            LOGGER.exception("Unexpected error while fetching current war.")
            await interaction.followup.send(
                f"Unexpected error while fetching current war: {exc}",
            )
            return

        await interaction.followup.send(build_war_response(current_war))

    @bot.tree.command(name="missed", description="List players with attacks remaining.")
    async def missed(interaction: discord.Interaction):
        remember_command_channel(bot, interaction)
        await interaction.response.defer(thinking=True)

        try:
            current_war = await fetch_current_war(bot)
        except ClashApiError as exc:
            await interaction.followup.send(clash_api_error_message(exc))
            return
        except Exception as exc:
            LOGGER.exception("Unexpected error while fetching current war.")
            await interaction.followup.send(
                f"Unexpected error while fetching current war: {exc}",
            )
            return

        linked_players = await load_linked_players(bot, guild_id_for_interaction(interaction))
        await interaction.followup.send(build_missed_response(current_war, linked_players))

    @bot.tree.command(name="link-player", description="Link your Discord user to a Clash player name.")
    async def link_player(interaction: discord.Interaction, player_name: str):
        remember_command_channel(bot, interaction)
        cleaned_name = player_name.strip()
        if not cleaned_name:
            await interaction.response.send_message("Please provide a Clash player name.")
            return

        await save_linked_player(
            bot,
            guild_id_for_interaction(interaction),
            str(interaction.user.id),
            cleaned_name,
        )
        await interaction.response.send_message(
            f"Linked {interaction.user.mention} to Clash player '{cleaned_name}'"
        )

    @bot.tree.command(name="link-member", description="Link another Discord user to a Clash player name.")
    async def link_member(interaction: discord.Interaction, user: discord.Member, player_name: str):
        remember_command_channel(bot, interaction)
        if not has_manage_server_permission(interaction):
            await interaction.response.send_message(
                "You need Manage Server permission to use this command."
            )
            return

        cleaned_name = player_name.strip()
        if not cleaned_name:
            await interaction.response.send_message("Please provide a Clash player name.")
            return

        await save_linked_player(
            bot,
            guild_id_for_interaction(interaction),
            str(user.id),
            cleaned_name,
        )
        await interaction.response.send_message(
            f"Linked {user.mention} to Clash player '{cleaned_name}'"
        )

    @bot.tree.command(name="roster-unlinked", description="List clan members not linked to Discord users.")
    async def roster_unlinked(interaction: discord.Interaction):
        remember_command_channel(bot, interaction)
        await interaction.response.defer(thinking=True)

        try:
            clan_members = await fetch_clan_members(bot)
        except ClashApiError as exc:
            await interaction.followup.send(clash_api_error_message(exc))
            return
        except Exception as exc:
            LOGGER.exception("Unexpected error while fetching clan members.")
            await interaction.followup.send(
                f"Unexpected error while fetching clan members: {exc}",
            )
            return

        linked_players = await load_linked_players(bot, guild_id_for_interaction(interaction))
        await interaction.followup.send(build_roster_unlinked_response(clan_members, linked_players))

    return bot


def main():
    configure_logging()

    try:
        settings = load_settings()
    except ConfigError as exc:
        LOGGER.error("%s", exc)
        sys.exit(1)

    bot = create_bot(settings)
    bot.run(settings.discord_bot_token)


if __name__ == "__main__":
    main()
