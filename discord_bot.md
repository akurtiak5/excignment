# Discord Workout Bot

The bot posts the day's workout at 5:00 AM in the configured timezone. Its
button marks that date as a skip day in `workout_schedule.json` and regenerates
the affected schedule. The bot extends the schedule automatically whenever the
current day is its final scheduled day, so the next block is ready before the
schedule runs out. Use the `/start` application
command to publish and track the upcoming schedule, or `/continue` to append and
publish another schedule block manually. Automatic continuations are handled by
the daily backend. The bot checks the saved schedule every minute and edits the
tracked schedule post when its displayed content changes, including after manual
`edit` commands. Skip-button updates also refresh the post immediately. Each
continuation replaces the tracked message ID with its new post.

## Setup

1. Create a Discord application and bot in the Discord Developer Portal, then
   invite it to your server with permission to view the target channel, send
   messages, and use application commands.
2. Create the initial schedule if `workout_schedule.json` does not already
   exist:

   ```sh
   python excignment.py create
   ```

3. Install the Discord dependency:

   ```sh
   python -m pip install -r discord_requirements.txt
   ```

4. Set the environment variables and start the bot from this directory:

   ```sh
   export DISCORD_BOT_TOKEN="your-bot-token"
   export DISCORD_CHANNEL_ID="123456789012345678"
   export DISCORD_TIMEZONE="America/Toronto"
   python discord_bot.py
   ```

   `DISCORD_TIMEZONE` accepts an IANA timezone such as `Europe/London` or
   `America/Los_Angeles`; it defaults to `UTC`. Keep the process running for
   scheduled posts and button interactions to work.

To start the bot only when it is not already running, invoke
`bash ensure_discord_bot.sh`. The script inherits its environment and appends
bot output to `discord_bot.log`; run it periodically with a scheduler such as
cron to have it bring the bot back after an unexpected exit.

Optional path overrides are `WORKOUT_SCHEDULE_FILE`, `WORKOUT_CONFIG_FILE`,
and `DISCORD_BOT_STATE_FILE`. They default to the files in this directory. The
bot state file records the last posted date to avoid reposting after a restart.