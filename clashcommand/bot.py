import asyncio
import logging
import sys
from datetime import datetime, timezone

import discord
from discord.ext import commands

from clashcommand.clash.client import ClashApiError, ClashClient
from clashcommand.clash.time import format_central_time
from clashcommand.clash.war import current_war_overview, remaining_attack_members
from clashcommand.config import ConfigError, load_settings
from clashcommand.db import LinkedPlayerStore


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


def normalize_player_name(name):
    return str(name or "").strip().lower()


def linked_user_for_player(player_name, linked_players):
    normalized_player_name = normalize_player_name(player_name)
    for discord_user_id, linked_player_name in linked_players.items():
        if normalize_player_name(linked_player_name) == normalized_player_name:
            return discord_user_id
    return None


def missed_player_label(player, linked_players):
    discord_user_id = linked_user_for_player(player["name"], linked_players)
    if discord_user_id is not None:
        return f"<@{discord_user_id}>"
    return player["name"]


def build_missed_response(war, linked_players=None):
    linked_players = linked_players or {}
    overview = current_war_overview(war)
    state = overview["state"]

    if state == "notInWar":
        return f"No active war is currently in progress for `{overview['clan']['name']}`."

    remaining_members = remaining_attack_members(war)
    if not remaining_members:
        return "Everyone has used all attacks."

    lines = ["**Players with attacks remaining:**"]
    for player in remaining_members:
        lines.append(f"- {missed_player_label(player, linked_players)} ({player['remaining']} left)")

    return "\n".join(lines)


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

    async def setup_hook(self):
        await asyncio.to_thread(self.linked_player_store.initialize)

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


async def fetch_current_war(bot):
    return await asyncio.to_thread(
        bot.clash_client.get_current_war,
        bot.settings.clan_tag,
    )


def guild_id_for_interaction(interaction):
    if interaction.guild_id is None:
        return "dm"
    return str(interaction.guild_id)


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
