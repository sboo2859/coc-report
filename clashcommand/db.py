import os
import sqlite3
from datetime import datetime, timezone


def utc_now_text():
    return datetime.now(timezone.utc).isoformat()


class LinkedPlayerStore:
    def __init__(self, db_path):
        self.db_path = db_path

    def connect(self):
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self):
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS linked_players (
                    guild_id TEXT NOT NULL,
                    discord_user_id TEXT NOT NULL,
                    coc_player_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, discord_user_id)
                )
                """
            )

    def upsert_linked_player(self, guild_id, discord_user_id, coc_player_name):
        now = utc_now_text()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO linked_players (
                    guild_id,
                    discord_user_id,
                    coc_player_name,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, discord_user_id)
                DO UPDATE SET
                    coc_player_name = excluded.coc_player_name,
                    updated_at = excluded.updated_at
                """,
                (str(guild_id), str(discord_user_id), coc_player_name, now, now),
            )

    def linked_players_for_guild(self, guild_id):
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT discord_user_id, coc_player_name
                FROM linked_players
                WHERE guild_id = ?
                """,
                (str(guild_id),),
            ).fetchall()

        return {
            row["discord_user_id"]: row["coc_player_name"]
            for row in rows
        }
