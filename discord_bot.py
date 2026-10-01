from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands, tasks


ROOT = Path(__file__).resolve().parent
SCHEDULER = ROOT / "excignment.py"
SCHEDULE_FILE = Path(
    os.environ.get("WORKOUT_SCHEDULE_FILE", ROOT / "workout_schedule.json")
).expanduser()
CONFIG_FILE = Path(
    os.environ.get("WORKOUT_CONFIG_FILE", ROOT / "workout_config.json")
).expanduser()
BOT_STATE_FILE = Path(
    os.environ.get("DISCORD_BOT_STATE_FILE", ROOT / "discord_bot_state.json")
).expanduser()
TIMEZONE_NAME = os.environ.get("DISCORD_TIMEZONE", "UTC")
try:
    SCHEDULE_TIMEZONE = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError as error:
    raise SystemExit(f"Unknown timezone in DISCORD_TIMEZONE: {TIMEZONE_NAME}") from error

WORKOUT_NAMES = {
    "hip": "Hip",
    "core": "Core",
    "leg": "Leg",
    "run": "Run",
    "upper": "Upper body",
    "yoga": "Yoga",
    "skip": "Skip day",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("workout-discord")


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


SCHEDULE_FILE = resolve_path(SCHEDULE_FILE)
CONFIG_FILE = resolve_path(CONFIG_FILE)
BOT_STATE_FILE = resolve_path(BOT_STATE_FILE)


def run_scheduler(*arguments: str) -> None:
    subprocess.run(
        [
            sys.executable,
            str(SCHEDULER),
            *arguments,
            "--file",
            str(SCHEDULE_FILE),
            *(
                ["--config", str(CONFIG_FILE)]
                if any(
                    argument in {"create", "update", "continue"}
                    for argument in arguments
                )
                else []
            ),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def read_schedule_state() -> dict:
    with SCHEDULE_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def ensure_schedule_contains(target_date: date) -> list[list[dict]]:
    continued_schedules = []
    while True:
        state = read_schedule_state()
        last_date = date.fromisoformat(state["schedule"][-1]["date"])
        if last_date > target_date:
            return continued_schedules
        previous_length = len(state["schedule"])
        run_scheduler("continue")
        continued_state = read_schedule_state()
        continued_schedules.append(continued_state["schedule"][previous_length:])


def continue_schedule() -> list[dict]:
    previous_length = len(read_schedule_state()["schedule"])
    run_scheduler("continue")
    return read_schedule_state()["schedule"][previous_length:]


def upcoming_schedule(target_date: date) -> list[dict]:
    state = read_schedule_state()
    with CONFIG_FILE.open("r", encoding="utf-8") as file:
        display_days = int(json.load(file)["horizon_days"])
    completed_dates = set(read_bot_state().get("completed_dates", []))
    completed_entries = [
        entry
        for entry in state["schedule"]
        if entry["date"] in completed_dates
        and date.fromisoformat(entry["date"]) < target_date
    ]
    upcoming_entries = [
        entry
        for entry in state["schedule"]
        if date.fromisoformat(entry["date"]) >= target_date
    ][:display_days]
    return [
        *completed_entries,
        *upcoming_entries,
    ]


def format_schedule(entries: list[dict], heading: str) -> str:
    if not entries:
        return f"**{heading}**\nNo scheduled days."

    completed_dates = set(read_bot_state().get("completed_dates", []))
    lines = []
    for entry in entries:
        scheduled_date = date.fromisoformat(entry["date"])
        workouts = entry["workouts"]
        if workouts == ["skip"]:
            description = "Skip day"
        else:
            description = " + ".join(
                WORKOUT_NAMES.get(workout, workout.title()) for workout in workouts
            ) or "Rest day"
        checkmark = (
            "\N{WHITE HEAVY CHECK MARK} "
            if entry["date"] in completed_dates
            else ""
        )
        skip_marker = "\N{CROSS MARK} " if workouts == ["skip"] else ""
        lines.append(
            f"{skip_marker}{checkmark}{scheduled_date:%a %b %-d}: {description}"
        )

    return f"**{heading}**\n" + "\n".join(lines)


def workout_for(target_date: date) -> list[str]:
    with SCHEDULE_FILE.open("r", encoding="utf-8") as file:
        schedule = json.load(file)["schedule"]
    for entry in schedule:
        if entry["date"] == target_date.isoformat():
            return list(entry["workouts"])
    raise ValueError(f"No workout scheduled for {target_date}.")


def format_workout(target_date: date) -> str:
    workouts = workout_for(target_date)
    if workouts == ["skip"]:
        description = "Skip day"
    else:
        description = " + ".join(
            WORKOUT_NAMES.get(workout, workout.title()) for workout in workouts
        ) or "Rest day"
    return f"**Workout for {target_date:%A, %B %-d}**\n{description}"


def read_bot_state() -> dict:
    try:
        with BOT_STATE_FILE.open("r", encoding="utf-8") as file:
            contents = file.read()
            return json.loads(contents) if contents.strip() else {}
    except FileNotFoundError:
        return {}


def write_bot_state(state: dict) -> None:
    BOT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = BOT_STATE_FILE.with_suffix(BOT_STATE_FILE.suffix + ".tmp")
    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)
        file.write("\n")
    temporary_file.replace(BOT_STATE_FILE)


def daily_workout_view(workout_date: date, completed: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    complete_button = CompleteWorkoutButton(workout_date)
    skip_button = SkipWorkoutButton(workout_date)
    complete_button.disabled = completed
    skip_button.disabled = completed
    view.add_item(complete_button)
    view.add_item(skip_button)
    return view


def skip_date(target_date: date) -> list[list[dict]]:
    continued_schedules = ensure_schedule_contains(target_date)
    if workout_for(target_date) == ["skip"]:
        return continued_schedules
    run_scheduler("update", target_date.isoformat())
    return continued_schedules


class WorkoutBot(commands.Bot):
    def __init__(self, timezone: ZoneInfo):
        super().__init__(command_prefix="!", intents=discord.Intents.none())
        self.timezone = timezone
        self.schedule_lock = asyncio.Lock()
        self.channel_id = int(os.environ["DISCORD_CHANNEL_ID"])
        self._register_schedule_commands()

    def _register_schedule_commands(self) -> None:
        @self.tree.command(
            name="start",
            description="Post the current workout schedule",
        )
        async def start_command(interaction: discord.Interaction) -> None:
            await self.start_schedule(interaction)

        @self.tree.command(
            name="continue",
            description="Extend and post the workout schedule",
        )
        async def continue_command(interaction: discord.Interaction) -> None:
            await self.continue_schedule_command(interaction)

    async def setup_hook(self) -> None:
        self.add_dynamic_items(CompleteWorkoutButton, SkipWorkoutButton)
        try:
            await self.tree.sync()
        except discord.HTTPException:
            logger.exception("Could not sync Discord application commands")
        self.daily_workout.start()
        self.schedule_edit_poll.start()

    async def on_ready(self) -> None:
        logger.info("Connected as %s", self.user)
        if datetime.now(self.timezone).hour >= 5:
            try:
                await self.send_todays_workout()
            except Exception:
                logger.exception("Could not post today's workout")

    @tasks.loop(time=time(hour=5, minute=0, tzinfo=SCHEDULE_TIMEZONE))
    async def daily_workout(self) -> None:
        try:
            await self.send_todays_workout()
        except Exception:
            logger.exception("Could not post today's workout")

    @tasks.loop(minutes=15)
    async def schedule_edit_poll(self) -> None:
        if not SCHEDULE_FILE.exists():
            return

        async with self.schedule_lock:
            try:
                await self.update_tracked_schedule()
            except (OSError, ValueError, discord.HTTPException):
                logger.exception("Could not poll for schedule edits")

    @schedule_edit_poll.before_loop
    async def before_schedule_edit_poll(self) -> None:
        await self.wait_until_ready()

    async def send_todays_workout(self) -> None:
        target_date = datetime.now(self.timezone).date()
        async with self.schedule_lock:
            state = read_bot_state()
            if state.get("last_sent_date") == target_date.isoformat():
                return

            continued_schedules = await asyncio.to_thread(
                ensure_schedule_contains, target_date
            )
            for entries in continued_schedules:
                await self.post_tracked_schedule(
                    entries,
                    "Continued workout schedule",
                )

            channel = self.get_channel(self.channel_id)
            if channel is None:
                channel = await self.fetch_channel(self.channel_id)
            await channel.send(
                content=format_workout(target_date),
                view=daily_workout_view(target_date),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            state["last_sent_date"] = target_date.isoformat()
            write_bot_state(state)
            logger.info("Posted workout for %s", target_date)

    async def start_schedule(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self.schedule_lock:
            try:
                if not SCHEDULE_FILE.exists():
                    await asyncio.to_thread(run_scheduler, "create")
                target_date = datetime.now(self.timezone).date()
                continued_schedules = await asyncio.to_thread(
                    ensure_schedule_contains, target_date
                )
                for entries in continued_schedules:
                    await self.post_tracked_schedule(
                        entries,
                        "Continued workout schedule",
                    )
                entries = await asyncio.to_thread(upcoming_schedule, target_date)
                message = await self.post_tracked_schedule(
                    entries,
                    "Workout schedule",
                )
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                await interaction.followup.send(
                    f"Could not start the schedule: {self.error_detail(error)}",
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                f"Schedule posted and tracked as message {message.id}.",
                ephemeral=True,
            )

    async def continue_schedule_command(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self.schedule_lock:
            try:
                if not SCHEDULE_FILE.exists():
                    await interaction.followup.send(
                        "No schedule exists yet. Run /start first.", ephemeral=True
                    )
                    return
                entries = await asyncio.to_thread(continue_schedule)
                message = await self.post_tracked_schedule(
                    entries,
                    "Continued workout schedule",
                )
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                await interaction.followup.send(
                    f"Could not continue the schedule: {self.error_detail(error)}",
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                f"Continuation posted and tracked as message {message.id}.",
                ephemeral=True,
            )

    async def post_tracked_schedule(
        self,
        entries: list[dict],
        heading: str,
    ) -> discord.Message:
        channel = self.get_channel(self.channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.channel_id)
        message = await channel.send(
            content=format_schedule(entries, heading),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        state = read_bot_state()
        state["schedule_message_id"] = message.id
        write_bot_state(state)
        return message

    async def update_tracked_schedule(self) -> None:
        state = read_bot_state()
        message_id = state.get("schedule_message_id")
        if message_id is None:
            return

        channel = self.get_channel(self.channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.channel_id)
        message = await channel.fetch_message(int(message_id))
        entries = await asyncio.to_thread(
            upcoming_schedule,
            datetime.now(self.timezone).date(),
        )
        content = format_schedule(entries, "Workout schedule")
        if message.content == content:
            return

        await message.edit(
            content=content,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        logger.info("Updated tracked workout schedule message %s", message_id)

    @staticmethod
    def error_detail(error: Exception) -> str:
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            return error.stderr.strip()
        return str(error)


class SkipWorkoutButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"skip-workout:(?P<workout_date>\d{4}-\d{2}-\d{2})",
):
    def __init__(self, workout_date: date):
        self.workout_date = workout_date
        super().__init__(
            discord.ui.Button(
                label="Skip today",
                style=discord.ButtonStyle.danger,
                custom_id=f"skip-workout:{workout_date.isoformat()}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> SkipWorkoutButton:
        return cls(date.fromisoformat(match["workout_date"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        if not isinstance(bot, WorkoutBot):
            await interaction.response.send_message(
                "The workout bot is unavailable.", ephemeral=True
            )
            return

        if self.workout_date.isoformat() in read_bot_state().get("completed_dates", []):
            await interaction.response.send_message(
                "This workout is already marked complete.", ephemeral=True
            )
            return

        today = datetime.now(bot.timezone).date()
        if self.workout_date != today:
            await interaction.response.send_message(
                "This button is only available on its scheduled day.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with bot.schedule_lock:
            try:
                continued_schedules = await asyncio.to_thread(
                    skip_date, self.workout_date
                )
                for entries in continued_schedules:
                    await bot.post_tracked_schedule(
                        entries,
                        "Continued workout schedule",
                    )
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                logger.exception("Could not mark %s as a skip day", self.workout_date)
                await interaction.followup.send(
                    f"Could not update the schedule: {bot.error_detail(error)}",
                    ephemeral=True,
                )
                return

            try:
                await bot.update_tracked_schedule()
            except discord.HTTPException:
                logger.exception("Could not refresh the tracked schedule message")

            if interaction.message is not None:
                await interaction.message.edit(
                    content=format_workout(self.workout_date), view=None
                )
            await interaction.followup.send(
                f"{self.workout_date:%A, %B %-d} is now a skip day, and the schedule has been updated.",
                ephemeral=True,
            )


class CompleteWorkoutButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"complete-workout:(?P<workout_date>\d{4}-\d{2}-\d{2})",
):
    def __init__(self, workout_date: date):
        self.workout_date = workout_date
        super().__init__(
            discord.ui.Button(
                label="Complete",
                style=discord.ButtonStyle.success,
                custom_id=f"complete-workout:{workout_date.isoformat()}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> CompleteWorkoutButton:
        return cls(date.fromisoformat(match["workout_date"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        if not isinstance(bot, WorkoutBot):
            await interaction.response.send_message(
                "The workout bot is unavailable.", ephemeral=True
            )
            return

        today = datetime.now(bot.timezone).date()
        if self.workout_date > today:
            await interaction.response.send_message(
                "A future workout cannot be marked complete.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with bot.schedule_lock:
            try:
                state = read_bot_state()
                completed_dates = set(state.get("completed_dates", []))
                completed_dates.add(self.workout_date.isoformat())
                state["completed_dates"] = sorted(completed_dates)
                write_bot_state(state)

                if interaction.message is not None:
                    await interaction.message.edit(
                        content=format_workout(self.workout_date),
                        view=daily_workout_view(self.workout_date, completed=True),
                    )
                await bot.update_tracked_schedule()
            except (OSError, ValueError, discord.HTTPException) as error:
                logger.exception(
                    "Could not mark %s as complete", self.workout_date
                )
                await interaction.followup.send(
                    f"Could not update the schedule: {bot.error_detail(error)}",
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                f"{self.workout_date:%A, %B %-d} is marked complete.",
                ephemeral=True,
            )


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set the DISCORD_BOT_TOKEN environment variable.")
    if not os.environ.get("DISCORD_CHANNEL_ID"):
        raise SystemExit("Set the DISCORD_CHANNEL_ID environment variable.")
    bot = WorkoutBot(SCHEDULE_TIMEZONE)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()