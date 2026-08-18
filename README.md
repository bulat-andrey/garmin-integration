# Garmin Recovery Local

Small local training and recovery workflow for Codex that reads Garmin Connect data through a local MCP server and combines it with manual RPE.

## What this project does

- Uses the existing `garmin` MCP entry from `C:\Users\bulat\.codex\config.toml`
- Reads Garmin recovery data locally through the Garmin MCP server
- Keeps manual RPE in a simple local CSV: `data/manual_rpe.csv`
- Produces simple command-line reports:
  - `analyze-recovery`
  - `analyze-week`
  - `add-rpe <activity> <rpe>`
  - `analyze-kite-history`
  - `send-telegram-recovery`

## Current local setup

- Codex MCP config is preserved in `C:\Users\bulat\.codex\config.toml`
- Garmin MCP server is configured there as `garmin`
- The upstream Garmin MCP checkout currently lives in:
  `C:\Users\bulat\Projects\Garmin_integration_Andrei\garmin-connect-mcp`
- Garmin credentials and tokens stay outside this repo:
  - `C:\Users\bulat\.garminconnect.env`
  - `C:\Users\bulat\.garminconnect`
  - `C:\Users\bulat\.garminconnect_base64`

## Security notes

- Do not commit Garmin credentials or tokens
- Do not paste passwords into source files
- `data/manual_rpe.csv` is ignored by Git because it is personal health/training data
- This workflow is local-only unless you explicitly choose otherwise

## Install

From `C:\Users\bulat\Projects\Garmin_integration_Andrei`:

```powershell
uv sync
```

Then run commands with `uv run ...`:

```powershell
uv run analyze-recovery
uv run analyze-week
uv run add-rpe latest 7 --notes "kite downwind"
uv run analyze-kite-history
uv run send-telegram-recovery --dry-run
```

If you activate the virtual environment, the same commands can be run directly:

```powershell
.\.venv\Scripts\Activate.ps1
analyze-recovery
```

## Authentication

Garmin authentication is handled by the upstream Garmin MCP server.

Current command:

```powershell
uv run --directory C:\Users\bulat\Projects\Garmin_integration_Andrei\garmin-connect-mcp garmin-connect-mcp auth
```

If Garmin expires the session, re-run the auth command. If Garmin asks for MFA or an interactive login step, complete it locally in the terminal.

## Commands

### `analyze-recovery`

Reads today's recovery metrics and the last 7 days of activity/recovery context, then classifies recovery as:

- `GREEN`
- `YELLOW`
- `RED`

It also returns a simple training suggestion:

- `A) recovery/rest`
- `B) easy Zone 2`
- `C) normal aerobic training`
- `D) strength training`
- `E) hard training`

This is a coaching heuristic, not a medical decision tool.

### `analyze-week`

Summarizes the last 7 days:

- recent activities
- Garmin load fields that are available
- manual sRPE load
- missing-HR kite sessions
- sleep / HRV / resting-HR trend

### `add-rpe <activity> <rpe>`

Adds or updates a row in `data/manual_rpe.csv`.

Supported activity selectors:

- Garmin activity ID, for example `23901392556`
- `latest`
- a loose name/type match, for example `kite`

CSV schema:

```text
date,activity_id,activity_type,duration_min,rpe,srpe,notes
```

`duration_min` is rounded to the nearest minute when pulled from Garmin, and:

```text
srpe = duration_min * rpe
```

### `analyze-kite-history`

Shows recent kite sessions together with:

- duration
- distance
- Garmin load
- intensity minutes
- HR availability
- manual RPE / sRPE

This makes it easier to see when Garmin underestimates load because HR was missing.

### `send-telegram-recovery`

Sends the daily recovery summary to a Telegram chat.

Required environment variables:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Optional:

```text
ATHLETE_NAME
MESSAGE_LANGUAGE
ENABLE_WOMENS_HEALTH
PREFERRED_STRENGTH_DAYS
```

Example:

```powershell
$env:TELEGRAM_BOT_TOKEN="123456:abc"
$env:TELEGRAM_CHAT_ID="123456789"
$env:ATHLETE_NAME="Andrei"
$env:MESSAGE_LANGUAGE="ru"
$env:PREFERRED_STRENGTH_DAYS="any"
uv run send-telegram-recovery --dry-run
uv run send-telegram-recovery
```

The command reuses the same recovery heuristic as `analyze-recovery`, but formats it for a Telegram message.
If `ATHLETE_NAME` is set, the title becomes `Garmin recovery for <name>`.
If `MESSAGE_LANGUAGE=ru`, the Telegram text is sent in Russian.
If `ENABLE_WOMENS_HEALTH=true`, the command also queries Garmin women's health data and includes menstrual-cycle context when Garmin returns it.
If `PREFERRED_STRENGTH_DAYS` is set, strength is recommended only on those weekdays. Use values like `mon,thu,sat,sun` or `any`.

You can also use named profiles:

```powershell
uv run analyze-recovery --profile andrei
uv run send-telegram-recovery --profile andrei
uv run send-telegram-recovery --profile vika --dry-run
```

## Hetzner deployment

One simple approach for a Linux desktop or dev box is:

1. Install `uv`
2. Clone this repo to a stable path, for example `~/garmin-integration`
3. Clone the upstream Garmin MCP repo alongside it or inside it
4. Copy or recreate the Garmin MCP config in `~/.codex/config.toml`
5. Copy your Garmin token files to the Linux machine only if you trust that host:
   - `~/.garminconnect.env`
   - `~/.garminconnect`
   - `~/.garminconnect_base64`
6. Run `uv sync`
7. Test locally:

```bash
uv run analyze-recovery
uv run send-telegram-recovery --dry-run
```

If you do not want to install the full Codex desktop app on Hetzner, this project still works as long as the local Garmin MCP server exists and `~/.codex/config.toml` contains the `garmin` MCP entry that points to it.

You can override the Codex config path with:

```bash
export GARMIN_CODEX_CONFIG=/home/youruser/.codex/config.toml
```

## Telegram setup

Create a bot with BotFather, start a chat with the bot, and capture the target chat ID. The Telegram Bot API `sendMessage` method requires a bot token plus a `chat_id`. See the official Telegram Bot API docs for `sendMessage`:

- [Telegram Bot API](https://core.telegram.org/bots/api)

Store secrets outside Git. A simple Linux layout is:

```bash
mkdir -p ~/.config/garmin-recovery
cp deploy/telegram.env.example ~/.config/garmin-recovery/telegram.env
chmod 600 ~/.config/garmin-recovery/telegram.env
```

For multiple people, use one env file per profile:

```bash
mkdir -p ~/.config/garmin-recovery/profiles
cp deploy/profile.env.example ~/.config/garmin-recovery/profiles/andrei.env
cp deploy/profile.env.example ~/.config/garmin-recovery/profiles/vika.env
chmod 600 ~/.config/garmin-recovery/profiles/*.env
```

Each profile can point to a different Garmin MCP config and a different Telegram chat.
For Vika, set:

```text
ATHLETE_NAME=Vika
MESSAGE_LANGUAGE=ru
ENABLE_WOMENS_HEALTH=true
PREFERRED_STRENGTH_DAYS=mon,thu,sat,sun
```

## Telegram commands

You can also run on-demand recovery reports from Telegram by sending commands to the bot:

```text
/andrei
/vika
```

Recommended setup:

- use a direct chat with the bot
- allow only your trusted Telegram user ID and chat ID
- keep the listener local on the Hetzner VM

Install the listener env:

```bash
cp deploy/telegram-listener.env.example ~/.config/garmin-recovery/telegram-listener.env
chmod 600 ~/.config/garmin-recovery/telegram-listener.env
```

Example:

```text
TELEGRAM_BOT_TOKEN=123456:abc
TELEGRAM_COMMAND_ALLOWED_USER_IDS=123456789
TELEGRAM_COMMAND_ALLOWED_CHAT_IDS=123456789
TELEGRAM_COMMAND_SKIP_EXISTING_UPDATES=true
```

Install and start the listener service:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/garmin-recovery-telegram-listener.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now garmin-recovery-telegram-listener.service
systemctl --user status garmin-recovery-telegram-listener.service
```

To follow logs:

```bash
journalctl --user -u garmin-recovery-telegram-listener.service -f
```

## Daily 08:10 notification

This repo includes `systemd --user` templates in `deploy/`.

Suggested install on the Hetzner machine:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/garmin-recovery-notify.service ~/.config/systemd/user/
cp deploy/garmin-recovery-notify.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now garmin-recovery-notify.timer
systemctl --user list-timers garmin-recovery-notify.timer
```

For per-person timers, use the templated units:

```bash
cp deploy/garmin-recovery-notify@.service ~/.config/systemd/user/
cp deploy/garmin-recovery-notify@.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now garmin-recovery-notify@andrei.timer
systemctl --user enable --now garmin-recovery-notify@vika.timer
```

The template defaults to:

```text
OnCalendar=*-*-* 08:10:00 Europe/Warsaw
```

If each profile should fire at a different time, add a per-instance override:

```bash
systemctl --user edit garmin-recovery-notify@andrei.timer
```

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 08:20:00 Europe/Warsaw
```

And for Vika:

```bash
systemctl --user edit garmin-recovery-notify@vika.timer
```

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 08:00:00 Europe/Warsaw
```

If the Hetzner desktop may stay logged out, also enable lingering for the user so the timer can run without an active session:

```bash
sudo loginctl enable-linger $USER
```

You can test the notification end to end with:

```bash
systemctl --user start garmin-recovery-notify.service
journalctl --user -u garmin-recovery-notify.service -n 50 --no-pager
```

## GitHub publish

GitHub's docs for existing local projects use the normal flow of adding a remote and then pushing commits:

- [Managing remote repositories](https://docs.github.com/en/get-started/git-basics/managing-remote-repositories)
- [Pushing commits to a remote repository](https://docs.github.com/en/get-started/using-git/pushing-commits-to-a-remote-repository)

## Known limitations

- This depends on Garmin's unofficial API through the upstream `garminconnect` library
- Garmin tool availability is uneven:
  - sleep score, sleep stages, overnight HRV, resting HR, Body Battery, stress, recent activities, duration, calories, and activity load are currently available
  - the `get_training_effect` MCP tool is currently exposed but returns an API-method error on this setup
  - per-activity HR is available for some activities and missing for recent kite sessions
- Garmin's `query_sleep_data` behaves best when you query the wake-up date
- Recovery output is intentionally conservative when recent kite sessions have no HR

## Upstream maintenance notes

The local Garmin MCP checkout was patched to work cleanly on this Windows setup:

- upgraded `garminconnect` to `>=0.3.9`
- forced UTF-8 stdio for Windows MCP startup
- switched auth password input to `getpass()`

If you re-clone or update the upstream server, re-check those compatibility changes before replacing the working copy.
