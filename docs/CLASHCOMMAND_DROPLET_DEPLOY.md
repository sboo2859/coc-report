# ClashCommand Droplet Deployment

This guide runs the repo-based ClashCommand Discord bot on a DigitalOcean Ubuntu droplet. It does not replace the existing static report scripts; it only covers the bot entrypoint:

```bash
python -m clashcommand.bot
```

## Assumptions

- The Discord app and bot already exist.
- The bot has been invited to your test Discord server.
- The droplet public IP is allowlisted for your Clash API token.
- You have SSH access to the droplet.
- The repo is available on GitHub or can be copied to the droplet.

## 1. Install System Packages

On the droplet:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

## 2. Create App Directory

Use `/opt/clashcommand` as the app home:

```bash
sudo mkdir -p /opt/clashcommand
sudo chown "$USER":"$USER" /opt/clashcommand
cd /opt/clashcommand
```

## 3. Put The Repo On The Droplet

Recommended path:

```text
/opt/clashcommand/app
```

Option A, clone from GitHub:

```bash
cd /opt/clashcommand
git clone <your-repo-url> app
cd /opt/clashcommand/app
```

For later updates:

```bash
cd /opt/clashcommand/app
git pull
```

Option B, copy from your local machine:

```bash
rsync -av --exclude .git --exclude .env --exclude .venv ./ user@your-droplet-ip:/opt/clashcommand/app/
```

If using `rsync`, run the command from the repo root on your local machine. Replace `user` and `your-droplet-ip`.

## 4. Create `.env`

On the droplet:

```bash
cd /opt/clashcommand/app
cp .env.example .env
nano .env
```

Set these bot variables:

```text
DISCORD_BOT_TOKEN=your_discord_bot_token
CLASH_API_TOKEN=your_clash_api_token
CLAN_TAG="#22YY2LPV2"
DISCORD_TEST_GUILD_ID=your_test_server_id
```

Notes:

- Keep real tokens only in `.env` on the droplet.
- Do not commit `.env`.
- `DISCORD_TEST_GUILD_ID` is recommended for MVP because guild-only slash command sync appears quickly.
- `CLAN_TAG` should stay quoted because Clash tags begin with `#`.

The old static scripts still use `COC_API_TOKEN` and `COC_CLAN_TAG`. You can set those too if you want to run static scripts on the droplet, but the Discord bot uses `CLASH_API_TOKEN` and `CLAN_TAG`.

## 5. Install Python Dependencies

Create and activate a venv:

```bash
cd /opt/clashcommand/app
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 6. Run The Bot Manually

Use the venv Python:

```bash
cd /opt/clashcommand/app
. .venv/bin/activate
python -m clashcommand.bot
```

Expected startup behavior:

- The bot logs in to Discord.
- If `DISCORD_TEST_GUILD_ID` is set, logs should mention slash command sync to that test guild.
- If an env var is missing, the process exits and names the missing variable.

Leave this command running while testing. Stop it with `Ctrl+C`.

## 7. Confirm `/war` Works

In your Discord test server:

1. Type `/war`.
2. Select the ClashCommand command.
3. Run it.

Expected result:

- If the clan is in war, the bot posts the matchup, state, score, destruction, attack usage, end time, and members with attacks remaining.
- If the clan is not in war, the bot says no active war is in progress.
- If Clash API access is denied, the bot tells you to check the API token and droplet IP allowlist.

If `/war` does not appear:

- Confirm `DISCORD_TEST_GUILD_ID` is the Discord server ID, not a channel ID.
- Restart the bot.
- Confirm the bot was invited with application command scope.
- Check the manual-run logs for command sync messages.

## 8. Create A systemd Service

After manual testing works, create a service so the bot restarts automatically.

Create a dedicated Linux user:

```bash
sudo useradd --system --home /opt/clashcommand --shell /usr/sbin/nologin clashcommand
sudo chown -R clashcommand:clashcommand /opt/clashcommand
```

Create the service file:

```bash
sudo nano /etc/systemd/system/clashcommand.service
```

Paste:

```ini
[Unit]
Description=ClashCommand Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/clashcommand/app
EnvironmentFile=/opt/clashcommand/app/.env
ExecStart=/opt/clashcommand/app/.venv/bin/python -m clashcommand.bot
Restart=always
RestartSec=10
User=clashcommand
Group=clashcommand

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable clashcommand
sudo systemctl start clashcommand
sudo systemctl status clashcommand
```

## 9. View Logs

Follow live logs:

```bash
sudo journalctl -u clashcommand -f
```

Show recent logs:

```bash
sudo journalctl -u clashcommand -n 100 --no-pager
```

Useful things to look for:

- Successful Discord login.
- Slash command sync count.
- Missing environment variable errors.
- Clash API access denied errors.
- Unexpected exceptions from `/war`.

## 10. Restart After Code Updates

If using Git:

```bash
cd /opt/clashcommand/app
sudo -u clashcommand git pull
sudo -u clashcommand .venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart clashcommand
sudo journalctl -u clashcommand -f
```

If you originally cloned as your normal SSH user and changed ownership to `clashcommand`, either run pulls as `clashcommand` as shown above or temporarily adjust ownership. Avoid editing live files with `nano` unless you are changing droplet-only `.env`.

If copying with `rsync`:

```bash
rsync -av --exclude .git --exclude .env --exclude .venv ./ user@your-droplet-ip:/opt/clashcommand/app/
ssh user@your-droplet-ip
sudo chown -R clashcommand:clashcommand /opt/clashcommand
cd /opt/clashcommand/app
sudo -u clashcommand .venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart clashcommand
sudo journalctl -u clashcommand -f
```

## 11. Common Problems

Missing `DISCORD_BOT_TOKEN`:

```text
Missing required environment variable: DISCORD_BOT_TOKEN
```

Edit `/opt/clashcommand/app/.env`, then restart the service.

Slash command does not show up:

- Use `DISCORD_TEST_GUILD_ID` during MVP.
- Restart the bot after changing command code.
- Confirm the bot invite included application commands.

Clash API invalid IP or access denied:

- Confirm the droplet public IP is allowlisted in the Clash API developer portal.
- Confirm `CLASH_API_TOKEN` is the token tied to that allowlist.
- Restart the bot after editing `.env`.

Bot exits immediately under systemd:

```bash
sudo journalctl -u clashcommand -n 100 --no-pager
```

Most early failures are missing env vars, wrong file ownership, or missing venv dependencies.

