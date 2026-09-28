from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


DEFAULT_STATE_FILE = Path("workout_schedule.json")
DEFAULT_CONFIG_FILE = Path(__file__).with_name("workout_config.json")


@dataclass
class Day:
    date: date
    workouts: list[str]

    @property
    def skipped(self) -> bool:
        return "skip" in self.workouts


@dataclass(frozen=True)
class SchedulerConfig:
    horizon_days: int
    week_length_days: int
    candidate_attempts: int
    fixed_run_weekday: int | None
    skip_streak_exemption_length: int
    minimum_hip_workouts: int
    weekend_days: tuple[int, ...]
    skip_week_minimums: dict[str, int]
    weekly_targets: dict[str, dict[str, int]]
    cooldown_days: dict[str, int]
    preferred_hip_interval_days: int
    hip_interval_weights: tuple[tuple[int, int], ...]
    score_weights: dict[str, float]


def load_config(path: Path) -> SchedulerConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found at '{path}'.")

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    required_keys = {
        "horizon_days",
        "week_length_days",
        "candidate_attempts",
        "fixed_run_weekday",
        "skip_streak_exemption_length",
        "minimum_hip_workouts",
        "weekend_days",
        "skip_week_minimums",
        "weekly_targets",
        "cooldown_days",
        "preferred_hip_interval_days",
        "hip_interval_weights",
        "score_weights",
    }
    if not isinstance(data, dict) or set(data) != required_keys:
        raise ValueError(
            f"Config '{path}' must contain exactly these settings: "
            + ", ".join(sorted(required_keys))
        )

    expected_workouts = {"leg", "run", "upper", "hip"}
    expected_cooldowns = {"hip", "leg", "upper", "run", "core"}
    expected_targets = {"leg", "run", "upper"}
    expected_score_weights = {
        "hip_workout_reward",
        "hip_interval_deviation_penalty",
        "upper_shortfall_penalty",
        "upper_excess_penalty",
        "leg_target_deviation_penalty",
        "run_shortfall_penalty",
        "run_excess_penalty",
        "combined_workout_penalty",
        "weekend_yoga_bonus",
        "weekday_yoga_penalty",
    }

    if not isinstance(data["skip_week_minimums"], dict) or set(
        data["skip_week_minimums"]
    ) != expected_workouts:
        raise ValueError(
            "skip_week_minimums must define leg, run, upper, and hip."
        )
    if not isinstance(data["cooldown_days"], dict) or set(
        data["cooldown_days"]
    ) != expected_cooldowns:
        raise ValueError(
            "cooldown_days must define hip, leg, upper, run, and core."
        )
    if not isinstance(data["weekly_targets"], dict) or set(
        data["weekly_targets"]
    ) != expected_targets:
        raise ValueError("weekly_targets must define leg, run, and upper.")
    if not isinstance(data["weekly_targets"]["leg"], dict) or set(
        data["weekly_targets"]["leg"]
    ) != {"target"}:
        raise ValueError("weekly_targets.leg must define target.")
    for workout in ("run", "upper"):
        target = data["weekly_targets"][workout]
        if not isinstance(target, dict) or set(target) != {
            "preferred_min",
            "preferred_max",
        }:
            raise ValueError(
                f"weekly_targets.{workout} must define preferred_min "
                "and preferred_max."
            )
    if not isinstance(data["score_weights"], dict) or set(
        data["score_weights"]
    ) != expected_score_weights:
        raise ValueError("score_weights does not define the expected score keys.")

    try:
        config = SchedulerConfig(
            horizon_days=int(data["horizon_days"]),
            week_length_days=int(data["week_length_days"]),
            candidate_attempts=int(data["candidate_attempts"]),
            fixed_run_weekday=(
                None
                if data["fixed_run_weekday"] is None
                else int(data["fixed_run_weekday"])
            ),
            skip_streak_exemption_length=int(
                data["skip_streak_exemption_length"]
            ),
            minimum_hip_workouts=int(data["minimum_hip_workouts"]),
            weekend_days=tuple(int(day) for day in data["weekend_days"]),
            skip_week_minimums={
                workout: int(minimum)
                for workout, minimum in data["skip_week_minimums"].items()
            },
            weekly_targets={
                workout: {
                    key: int(value)
                    for key, value in target.items()
                }
                for workout, target in data["weekly_targets"].items()
            },
            cooldown_days={
                workout: int(days)
                for workout, days in data["cooldown_days"].items()
            },
            preferred_hip_interval_days=int(
                data["preferred_hip_interval_days"]
            ),
            hip_interval_weights=tuple(
                (int(item["days"]), int(item["weight"]))
                for item in data["hip_interval_weights"]
            ),
            score_weights={
                key: float(value)
                for key, value in data["score_weights"].items()
            },
        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid value in config '{path}': {error}") from error

    if config.horizon_days < 1 or config.week_length_days < 1:
        raise ValueError("horizon_days and week_length_days must be positive.")
    if config.candidate_attempts < 1:
        raise ValueError("candidate_attempts must be positive.")
    if config.skip_streak_exemption_length < 1:
        raise ValueError("skip_streak_exemption_length must be positive.")
    if config.minimum_hip_workouts < 0:
        raise ValueError("minimum_hip_workouts cannot be negative.")
    if (
        config.fixed_run_weekday is not None
        and not 0 <= config.fixed_run_weekday <= 6
    ):
        raise ValueError("fixed_run_weekday must be between 0 and 6 or null.")
    if any(not 0 <= day <= 6 for day in config.weekend_days):
        raise ValueError("weekend_days values must be between 0 and 6.")
    if any(value < 0 for value in config.skip_week_minimums.values()):
        raise ValueError("skip_week_minimums cannot contain negative values.")
    if any(value < 1 for value in config.cooldown_days.values()):
        raise ValueError("cooldown_days values must be positive.")
    if any(value < 0 for value in config.score_weights.values()):
        raise ValueError("score_weights cannot contain negative values.")
    if not config.hip_interval_weights:
        raise ValueError("hip_interval_weights cannot be empty.")
    if any(
        interval < config.cooldown_days["hip"] or weight < 1
        for interval, weight in config.hip_interval_weights
    ):
        raise ValueError(
            "Hip intervals must meet the hip cooldown and have positive weights."
        )
    if config.preferred_hip_interval_days not in {
        interval for interval, _ in config.hip_interval_weights
    }:
        raise ValueError("Preferred hip interval must be one of the weighted intervals.")

    for workout, target in config.weekly_targets.items():
        if any(value < 0 for value in target.values()):
            raise ValueError(f"weekly_targets.{workout} cannot be negative.")
        if workout != "leg" and target["preferred_min"] > target["preferred_max"]:
            raise ValueError(
                f"weekly_targets.{workout}.preferred_min cannot exceed "
                "preferred_max."
            )

    return config


class WorkoutScheduler:
    def __init__(
        self,
        start_date: date,
        skip_dates: set[date] | None = None,
        horizon: int | None = None,
        seed: int = 0,
        previous_days: list[Day] | None = None,
        config: SchedulerConfig | None = None,
    ):
        self.config = config or load_config(DEFAULT_CONFIG_FILE)
        self.start_date = start_date
        self.horizon = (
            self.config.horizon_days
            if horizon is None
            else horizon
        )
        self.skip_dates = skip_dates or set()
        self.seed = seed
        self.previous_days = previous_days or []

        self.dates = [
            start_date + timedelta(days=i)
            for i in range(self.horizon)
        ]

        invalid_skip_dates = [
            skipped_date
            for skipped_date in self.skip_dates
            if skipped_date not in self.dates
        ]

        if invalid_skip_dates:
            raise ValueError(
                "Skip dates must be within the schedule horizon: "
                + ", ".join(
                    str(skipped_date)
                    for skipped_date in sorted(invalid_skip_dates)
                )
            )

    def generate(self) -> list[Day]:
        """
        Generate a complete schedule.

        Several deterministic candidate schedules are generated from
        seed values derived from the original seed. The highest-scoring
        valid candidate is returned.
        """
        best_schedule = None
        best_score = float("-inf")

        # The number and order of attempts are fixed for the loaded config.
        for attempt in range(self.config.candidate_attempts):
            candidate_seed = self._candidate_seed(attempt)
            rng = random.Random(candidate_seed)

            schedule = self._create_candidate(rng)

            if not self._is_valid(schedule):
                continue

            score = self._score(schedule)

            if score > best_score:
                best_score = score
                best_schedule = schedule

        if best_schedule is None:
            raise RuntimeError(
                "No valid schedule could be generated. "
                "There may be too many skip dates for the current rules."
            )

        return best_schedule

    def _candidate_seed(self, attempt: int) -> int:
        """
        Derive a deterministic candidate seed.

        The original seed, start date, skip dates, and attempt number all
        affect the generated candidate.
        """
        skip_text = ",".join(
            skipped_date.isoformat()
            for skipped_date in sorted(self.skip_dates)
        )

        seed_text = (
            f"{self.seed}|"
            f"{self.start_date.isoformat()}|"
            f"{skip_text}|"
            f"{attempt}"
        )

        # Python's built-in hash() is intentionally not stable across
        # processes, so use a deterministic byte-based calculation.
        value = 0

        for character in seed_text:
            value = (value * 131 + ord(character)) & 0xFFFFFFFFFFFFFFFF

        return value

    def _create_candidate(self, rng: random.Random) -> list[Day]:
        schedule = [
            Day(
                scheduled_date,
                ["skip"] if scheduled_date in self.skip_dates else [],
            )
            for scheduled_date in self.dates
        ]

        # Place primary workouts before adding their secondary work.
        self._place_fixed_weekday_runs(schedule)

        # Place approximate weekly targets afterward.
        self._place_upper_body(schedule, rng)
        self._place_additional_runs(schedule, rng)
        self._place_legs(schedule, rng)

        # Convert any remaining empty day to Yoga.
        self._fill_remaining_days(schedule)
        self._place_hips(schedule, rng)
        self._place_core_on_yoga(schedule)

        return schedule

    def _place_legs(
        self,
        schedule: list[Day],
        rng: random.Random,
    ) -> None:
        """
        Target one leg workout per configured week with the configured cooldown.
        """
        cooldown = self.config.cooldown_days["leg"]
        last_leg_index = -cooldown
        
        for prev_day in reversed(self.previous_days):
            if "leg" in prev_day.workouts:
                # Calculate days between last leg and start of new schedule
                days_since_last_leg = (self.start_date - prev_day.date).days
                last_leg_index = -days_since_last_leg
                break
        
        week_length = self.config.week_length_days
        target = self.config.weekly_targets["leg"]["target"]
        for week_start in range(0, self.horizon, week_length):
            week_end = min(week_start + week_length, self.horizon)
            indices = list(range(week_start, week_end))
            rng.shuffle(indices)

            for _ in range(target):
                placed = False

                for index in indices:
                    if index - last_leg_index >= cooldown and self._can_place(
                        schedule=schedule,
                        index=index,
                        workout="leg",
                    ):
                        schedule[index].workouts.append("leg")
                        last_leg_index = index
                        placed = True
                        break

                if not placed:
                    break


    def _place_upper_body(
        self,
        schedule: list[Day],
        rng: random.Random,
    ) -> None:
        """
        Target the configured number of upper-body workouts per week.

        Upper-body workouts include core. Respect spacing from previous workouts.
        """
        # Find the last upper-body workout from previous days
        cooldown = self.config.cooldown_days["upper"]
        last_upper_index = -cooldown
        
        for prev_day in reversed(self.previous_days):
            if "upper" in prev_day.workouts:
                days_since_last_upper = (self.start_date - prev_day.date).days
                last_upper_index = -days_since_last_upper
                break
        
        week_length = self.config.week_length_days
        for week_start in range(0, self.horizon, week_length):
            week_end = min(week_start + week_length, self.horizon)
            _, upper_max = self._upper_targets_for_block(week_start, week_end)

            indices = list(range(week_start, week_end))
            rng.shuffle(indices)

            # Prefer less occupied days.
            indices.sort(
                key=lambda index: len(schedule[index].workouts)
            )

            placed = 0

            for index in indices:
                if placed >= upper_max:
                    break

                # Check spacing from last upper workout
                if index - last_upper_index < cooldown:
                    continue

                if not self._can_place(
                    schedule=schedule,
                    index=index,
                    workout="upper",
                ):
                    continue

                schedule[index].workouts.extend(["upper", "core"])
                last_upper_index = index
                placed += 1

    def _upper_targets_for_block(self, week_start: int, week_end: int) -> tuple[int, int]:
        target = self.config.weekly_targets["upper"]
        block_days = week_end - week_start
        if block_days == self.config.week_length_days:
            return target["preferred_min"], target["preferred_max"]

        scale = block_days / self.config.week_length_days
        scaled_minimum = int(target["preferred_min"] * scale)
        scaled_maximum = int(target["preferred_max"] * scale)
        if block_days > 0:
            scaled_maximum = max(1, scaled_maximum)
            scaled_minimum = min(scaled_maximum, scaled_minimum)
        return scaled_minimum, scaled_maximum


    def _place_additional_runs(
        self,
        schedule: list[Day],
        rng: random.Random,
    ) -> None:
        """
        Target the configured number of runs per week, including a fixed weekday run.
        Respect spacing from previous runs.
        """
        cooldown = self.config.cooldown_days["run"]
        last_run_index = -cooldown
        
        for prev_day in reversed(self.previous_days):
            if "run" in prev_day.workouts:
                days_since_last_run = (self.start_date - prev_day.date).days
                last_run_index = -days_since_last_run
                break
        
        week_length = self.config.week_length_days
        for week_start in range(0, self.horizon, week_length):
            week_end = min(week_start + week_length, self.horizon)

            run_count = sum(
                "run" in schedule[index].workouts
                for index in range(week_start, week_end)
            )

            run_target = self.config.weekly_targets["run"]
            desired_runs = rng.choice(
                list(
                    range(
                        run_target["preferred_min"],
                        run_target["preferred_max"] + 1,
                    )
                )
            )

            indices = list(range(week_start, week_end))
            rng.shuffle(indices)

            for index in indices:
                if run_count >= desired_runs:
                    break

                # Check spacing from last run
                if index - last_run_index < cooldown:
                    continue

                if not self._can_place(
                    schedule=schedule,
                    index=index,
                    workout="run",
                ):
                    continue

                schedule[index].workouts.append("run")
                last_run_index = index
                run_count += 1


    def _place_hips(
        self,
        schedule: list[Day],
        rng: random.Random,
    ) -> None:
        """
        Add hip work to run, leg, or yoga days, respecting hip cooldowns.
        """
        # Find the last hip workout from previous days
        cooldown = self.config.cooldown_days["hip"]
        last_hip_index = -cooldown
        
        for prev_day in reversed(self.previous_days):
            if "hip" in prev_day.workouts:
                days_since_last_hip = (self.start_date - prev_day.date).days
                last_hip_index = -days_since_last_hip
                break
        
        possible_sequences = []

        available_indices = [
            index
            for index, day in enumerate(schedule)
            if not day.skipped
            and "core" not in day.workouts
            and (
                "run" in day.workouts
                or "leg" in day.workouts
                or day.workouts == ["yoga"]
            )
        ]

        # Try several deterministic starting points.
        for first_index in available_indices:
            # Only try starting points that respect spacing from previous hips
            if first_index - last_hip_index < cooldown:
                continue
            
            sequence = self._build_hip_sequence(
                schedule=schedule,
                first_index=first_index,
                rng=rng,
            )

            if sequence:
                possible_sequences.append(sequence)

        if not possible_sequences:
            return

        def sequence_score(sequence: list[int]) -> tuple:
            intervals = [
                second - first
                for first, second in zip(sequence, sequence[1:])
            ]

            # Maximize number of hips, then prefer five-day intervals.
            interval_penalty = sum(
                abs(interval - self.config.preferred_hip_interval_days)
                for interval in intervals
            )

            return len(sequence), -interval_penalty

        best_sequence = max(
            possible_sequences,
            key=sequence_score,
        )

        for index in best_sequence:
            schedule[index].workouts.append("hip")

    def _build_hip_sequence(
        self,
        schedule: list[Day],
        first_index: int,
        rng: random.Random,
    ) -> list[int]:
        sequence = [first_index]
        current_index = first_index

        while True:
            candidates = []

            for interval, weight in self.config.hip_interval_weights:
                candidate_index = current_index + interval

                if candidate_index >= self.horizon:
                    continue

                if schedule[candidate_index].skipped:
                    continue

                candidate_day = schedule[candidate_index]
                if "core" in candidate_day.workouts or not (
                    "run" in candidate_day.workouts
                    or "leg" in candidate_day.workouts
                    or candidate_day.workouts == ["yoga"]
                ):
                    continue

                candidates.append((interval, candidate_index, weight))

            if not candidates:
                break

            weighted_candidates = []

            for interval, candidate_index, weight in candidates:
                for _ in range(weight):
                    weighted_candidates.append(
                        (interval, candidate_index)
                    )

            interval, next_index = rng.choice(weighted_candidates)

            sequence.append(next_index)
            current_index = next_index

        return sequence


    def _place_fixed_weekday_runs(
        self,
        schedule: list[Day],
    ) -> None:
        for day in schedule:
            if day.skipped:
                continue

            if (
                self.config.fixed_run_weekday is not None
                and day.date.weekday() == self.config.fixed_run_weekday
            ):
                day.workouts.append("run")

    def _fill_remaining_days(self, schedule: list[Day]) -> None:
        week_length = self.config.week_length_days

        for index, day in enumerate(schedule):
            if day.workouts:
                continue

            if self._is_weekend_spacer(schedule, index):
                day.workouts.append("yoga")
                continue

            week_start = index // week_length * week_length
            week_end = min(week_start + week_length, self.horizon)
            week = schedule[week_start:week_end]
            counts = {
                workout: sum(workout in item.workouts for item in week)
                for workout in ("upper", "run", "leg")
            }
            eligible = []

            upper_min, upper_max = self._upper_targets_for_block(
                week_start,
                week_end,
            )
            if (
                counts["upper"] < upper_max
                and self._can_place(schedule, index, "upper")
            ):
                deficit = max(
                    0,
                    upper_min - counts["upper"],
                )
                eligible.append((
                    deficit * self.config.score_weights[
                        "upper_shortfall_penalty"
                    ],
                    0,
                    "upper",
                ))

            run_target = self.config.weekly_targets["run"]
            if (
                counts["run"] < run_target["preferred_max"]
                and self._can_place(schedule, index, "run")
            ):
                deficit = max(
                    0,
                    run_target["preferred_min"] - counts["run"],
                )
                eligible.append((
                    deficit * self.config.score_weights[
                        "run_shortfall_penalty"
                    ],
                    1,
                    "run",
                ))

            leg_target = self.config.weekly_targets["leg"]["target"]
            if (
                counts["leg"] < leg_target
                and self._can_place(schedule, index, "leg")
            ):
                deficit = leg_target - counts["leg"]
                eligible.append((
                    deficit * self.config.score_weights[
                        "leg_target_deviation_penalty"
                    ],
                    2,
                    "leg",
                ))

            if not eligible:
                day.workouts.append("yoga")
                continue

            _, _, workout = max(eligible, key=lambda option: (option[0], -option[1]))
            if workout == "upper":
                day.workouts.extend(["upper", "core"])
            else:
                day.workouts.append(workout)

    def _place_core_on_yoga(self, schedule: list[Day]) -> None:
        cooldown = self.config.cooldown_days["core"]
        previous_core_dates = [
            day.date for day in self.previous_days if "core" in day.workouts
        ]

        for index, day in enumerate(schedule):
            if day.workouts != ["yoga"]:
                continue

            if any(
                (day.date - core_date).days < cooldown
                for core_date in previous_core_dates
            ):
                continue

            if not self._has_spacing(
                schedule,
                index,
                workout="core",
                minimum_gap=cooldown,
            ):
                continue

            day.workouts.append("core")

    def _is_weekend_spacer(
        self,
        schedule: list[Day],
        index: int,
    ) -> bool:
        day = schedule[index]
        if day.date.weekday() not in self.config.weekend_days:
            return False

        if index == 0 or index == len(schedule) - 1:
            return False

        previous_day = schedule[index - 1]
        next_day = schedule[index + 1]

        return all(
            not adjacent_day.skipped
            and bool(adjacent_day.workouts)
            and adjacent_day.workouts[0] not in {"yoga", "rest", "skip"}
            for adjacent_day in (previous_day, next_day)
        )

    def _can_place(
        self,
        schedule: list[Day],
        index: int,
        workout: str,
    ) -> bool:
        day = schedule[index]

        if day.skipped:
            return False

        if workout in day.workouts:
            return False

        if workout == "upper":
            # Upper body includes core.
            if day.workouts:
                return False

            # Upper/core cannot coincide with runs
            if "run" in day.workouts:
                return False

            if not self._has_spacing(
                schedule,
                index,
                workout="upper",
                minimum_gap=self.config.cooldown_days["upper"],
            ):
                return False

            # Core must also be spaced by at least one day.
            core_indices = self._indices_containing(
                schedule,
                "core",
            )

            if any(
                abs(index - other) < self.config.cooldown_days["core"]
                for other in core_indices
            ):
                return False

            if any(
                (self.start_date + timedelta(days=index) - previous_day.date).days
                < self.config.cooldown_days["core"]
                for previous_day in self.previous_days
                if "core" in previous_day.workouts
            ):
                return False

        elif workout == "run":
            if "upper" in day.workouts:
                return False

            if not self._has_spacing(
                schedule,
                index,
                workout="run",
                minimum_gap=self.config.cooldown_days["run"],
            ):
                return False

        elif workout == "leg":
            if day.workouts:
                return False

        return True

    def _is_valid(self, schedule: list[Day]) -> bool:
        # Skip days may not contain workouts.
        for day in schedule:
            if day.skipped and day.workouts != ["skip"]:
                return False

            if "upper" in day.workouts and "run" in day.workouts:
                return False

            if "hip" in day.workouts and not (
                "run" in day.workouts
                or "leg" in day.workouts
                or "yoga" in day.workouts
            ):
                return False

            if "hip" in day.workouts and "core" in day.workouts:
                return False

        for day in schedule:
            if (
                self.config.fixed_run_weekday is not None
                and day.date.weekday() == self.config.fixed_run_weekday
                and not day.skipped
            ):
                if "run" not in day.workouts:
                    return False

        # Core spacing.
        core_indices = self._indices_containing(
            schedule,
            "core",
        )

        if not self._has_minimum_spacing(
            core_indices,
            minimum_gap=self.config.cooldown_days["core"],
        ):
            return False

        if any(
            (schedule[index].date - previous_day.date).days
            < self.config.cooldown_days["core"]
            for index in core_indices
            for previous_day in self.previous_days
            if "core" in previous_day.workouts
        ):
            return False

        if any(
            "core" in day.workouts
            and not ("upper" in day.workouts or "yoga" in day.workouts)
            for day in schedule
        ):
            return False

        # Run spacing.
        run_indices = self._indices_containing(
            schedule,
            "run",
        )

        if not self._has_minimum_spacing(
            run_indices,
            minimum_gap=self.config.cooldown_days["run"],
        ):
            return False

        leg_indices = self._indices_containing(
            schedule,
            "leg",
        )

        if not self._has_minimum_spacing(
            leg_indices,
            minimum_gap=self.config.cooldown_days["leg"],
        ):
            return False

        # Upper-body spacing.
        upper_indices = self._indices_containing(
            schedule,
            "upper",
        )

        if not self._has_minimum_spacing(
            upper_indices,
            minimum_gap=self.config.cooldown_days["upper"],
        ):
            return False

        # Hip spacing.
        hip_indices = self._indices_containing(
            schedule,
            "hip",
        )

        if not self._has_minimum_spacing(
            hip_indices,
            minimum_gap=self.config.cooldown_days["hip"],
        ):
            return False

        if len(hip_indices) < self.config.minimum_hip_workouts:
            return False

        if self.skip_dates:
            week_length = self.config.week_length_days

            for week_start in range(0, self.horizon, week_length):
                week_end = min(week_start + week_length, self.horizon)

                if week_end - week_start < week_length:
                    continue

                if self._week_has_long_skip_streak(
                    schedule,
                    week_start,
                    week_end,
                ):
                    continue

                for workout, minimum in self.config.skip_week_minimums.items():
                    workout_count = sum(
                        workout in schedule[index].workouts
                        for index in range(week_start, week_end)
                    )
                    if workout_count < minimum:
                        return False

        return True

    def _week_has_long_skip_streak(
        self,
        schedule: list[Day],
        week_start: int,
        week_end: int,
    ) -> bool:
        streak_length = self.config.skip_streak_exemption_length
        first_possible_start = max(0, week_start - streak_length + 1)
        last_possible_start = min(
            len(schedule) - streak_length + 1,
            week_end,
        )

        return any(
            skip_start < week_end
            and skip_start + streak_length > week_start
            and all(
                schedule[index].skipped
                for index in range(skip_start, skip_start + streak_length)
            )
            for skip_start in range(
                first_possible_start,
                last_possible_start,
            )
        )

    def _score(self, schedule: list[Day]) -> float:
        score = 0.0

        weights = self.config.score_weights
        hip_indices = self._indices_containing(
            schedule,
            "hip",
        )

        score += len(hip_indices) * weights["hip_workout_reward"]

        for first, second in zip(
            hip_indices,
            hip_indices[1:],
        ):
            interval = second - first
            score -= (
                abs(interval - self.config.preferred_hip_interval_days)
                * weights["hip_interval_deviation_penalty"]
            )

        week_length = self.config.week_length_days
        for week_start in range(0, self.horizon, week_length):
            week_end = min(week_start + week_length, self.horizon)

            upper_count = sum(
                "upper" in schedule[index].workouts
                for index in range(week_start, week_end)
            )

            leg_count = sum(
                "leg" in schedule[index].workouts
                for index in range(week_start, week_end)
            )

            run_count = sum(
                "run" in schedule[index].workouts
                for index in range(week_start, week_end)
            )


            upper_min, upper_max = self._upper_targets_for_block(
                week_start,
                week_end,
            )
            if upper_count < upper_min:
                score -= (
                    upper_min - upper_count
                ) * weights["upper_shortfall_penalty"]
            elif upper_count > upper_max:
                score -= (
                    upper_count - upper_max
                ) * weights["upper_excess_penalty"]


            leg_target = self.config.weekly_targets["leg"]["target"]
            score -= (
                abs(leg_count - leg_target)
                * weights["leg_target_deviation_penalty"]
            )

            run_target = self.config.weekly_targets["run"]
            if run_count < run_target["preferred_min"]:
                score -= (
                    run_target["preferred_min"] - run_count
                ) * weights["run_shortfall_penalty"]
            elif run_count > run_target["preferred_max"]:
                score -= (
                    run_count - run_target["preferred_max"]
                ) * weights["run_excess_penalty"]

        # Prefer fewer combined workouts.
        for day in schedule:
            actual_workouts = [
                workout
                for workout in day.workouts
                if workout not in {"skip", "yoga", "hip", "core"}
            ]

            if len(actual_workouts) > 1:
                score -= (
                    (len(actual_workouts) - 1)
                    * weights["combined_workout_penalty"]
                )

            if day.workouts == ["yoga"]:
                if day.date.weekday() in self.config.weekend_days:
                    score += weights["weekend_yoga_bonus"]
                else:
                    score -= weights["weekday_yoga_penalty"]

        return score

    @staticmethod
    def _indices_containing(
        schedule: list[Day],
        workout: str,
    ) -> list[int]:
        return [
            index
            for index, day in enumerate(schedule)
            if workout in day.workouts
        ]

    @staticmethod
    def _has_spacing(
        schedule: list[Day],
        candidate_index: int,
        workout: str,
        minimum_gap: int,
    ) -> bool:
        existing_indices = [
            index
            for index, day in enumerate(schedule)
            if workout in day.workouts
        ]

        return all(
            abs(candidate_index - existing_index) >= minimum_gap
            for existing_index in existing_indices
        )

    @staticmethod
    def _has_minimum_spacing(
        indices: list[int],
        minimum_gap: int,
    ) -> bool:
        return all(
            second - first >= minimum_gap
            for first, second in zip(indices, indices[1:])
        )


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()
    except ValueError as error:
        raise ValueError(
            f"Invalid date '{value}'. Use YYYY-MM-DD."
        ) from error


def date_to_string(value: date) -> str:
    return value.isoformat()


def string_to_date(value: str) -> date:
    return parse_date(value)


def schedule_to_json(schedule: list[Day]) -> list[dict]:
    return [
        {
            "date": day.date.isoformat(),
            "workouts": day.workouts,
        }
        for day in schedule
    ]


def schedule_from_json(data: list[dict]) -> list[Day]:
    return [
        Day(
            date=parse_date(item["date"]),
            workouts=list(item["workouts"]),
        )
        for item in data
    ]


def save_state(
    path: Path,
    start_date: date,
    seed: int,
    skip_dates: set[date],
    schedule: list[Day],
) -> None:
    state = {
        "version": 1,
        "start_date": date_to_string(start_date),
        "horizon_days": len(schedule),
        "seed": seed,
        "skip_dates": [
            date_to_string(skipped_date)
            for skipped_date in sorted(skip_dates)
        ],
        "schedule": schedule_to_json(schedule),
    }

    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)
        file.write("\n")


def load_state(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"No state file found at '{path}'. "
            "Run the 'create' command first."
        )

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_schedule_state(path: Path) -> tuple[
    date,
    int,
    set[date],
    list[Day],
]:
    state = load_state(path)

    start_date = parse_date(state["start_date"])
    seed = int(state["seed"])

    skip_dates = {
        parse_date(value)
        for value in state.get("skip_dates", [])
    }

    schedule = schedule_from_json(state["schedule"])

    return start_date, seed, skip_dates, schedule


def generate_initial_schedule(
    start_date: date,
    seed: int,
    previous_days: list[Day] | None = None,
    config: SchedulerConfig | None = None,
) -> tuple[set[date], list[Day]]:
    skip_dates: set[date] = set()

    scheduler = WorkoutScheduler(
        start_date=start_date,
        skip_dates=skip_dates,
        seed=seed,
        previous_days=previous_days,
        config=config,
    )

    schedule = scheduler.generate()

    return skip_dates, schedule

def update_schedule(
    path: Path,
    new_skip_date: date,
    config: SchedulerConfig | None = None,
) -> tuple[date, int, set[date], list[Day]]:
    """
    Add a skip date while preserving all dates before it.

    Dates on or after the new skip date are regenerated with the original
    start date and seed. This makes the update deterministic.
    """
    state = load_state(path)

    start_date = parse_date(state["start_date"])
    seed = int(state["seed"])
    horizon = int(state["horizon_days"])

    old_skip_dates = {
        parse_date(value)
        for value in state.get("skip_dates", [])
    }

    old_schedule = schedule_from_json(state["schedule"])

    horizon_end = start_date + timedelta(days=horizon - 1)

    if not start_date <= new_skip_date <= horizon_end:
        raise ValueError(
            f"Skip date must be between {start_date} and {horizon_end}."
        )

    if new_skip_date in old_skip_dates:
        print(f"{new_skip_date} is already a skip day.")
        return start_date, seed, old_skip_dates, old_schedule

    # Add the new skip date.
    all_skip_dates = old_skip_dates | {new_skip_date}

    # Find the first affected date. Existing completed history before this
    # date remains untouched.
    first_affected_date = min(
        [new_skip_date, *old_skip_dates]
    )

    # Preserve all schedule entries strictly before the first affected date.
    preserved_schedule = [
        day
        for day in old_schedule
        if day.date < first_affected_date
    ]

    # Regenerate the complete schedule deterministically with all known skips.
    regenerated_scheduler = WorkoutScheduler(
        start_date=start_date,
        skip_dates=all_skip_dates,
        horizon=horizon,
        seed=seed,
        config=config,
    )

    regenerated_schedule = regenerated_scheduler.generate()

    regenerated_after_cutoff = [
        day
        for day in regenerated_schedule
        if day.date >= first_affected_date
    ]

    updated_schedule = (
        preserved_schedule
        + regenerated_after_cutoff
    )

    save_state(
        path=path,
        start_date=start_date,
        seed=seed,
        skip_dates=all_skip_dates,
        schedule=updated_schedule,
    )

    return start_date, seed, all_skip_dates, updated_schedule



def print_schedule(schedule: list[Day]) -> None:
    print()
    print("Workout Schedule")
    print("================")

    for day in schedule:
        weekday = day.date.strftime("%A")
        formatted_date = day.date.strftime("%Y-%m-%d")

        if day.workouts == ["skip"]:
            workout_text = "SKIP DAY"
        elif day.workouts == ["yoga"]:
            workout_text = "yoga"
        else:
            workout_text = ", ".join(day.workouts)

        print(
            f"{weekday:>9} {formatted_date}: {workout_text}"
        )

def command_continue(args: argparse.Namespace) -> None:
    path = Path(args.file)
    config = load_config(Path(args.config))

    start_date, seed, skip_dates, schedule = load_schedule_state(path)

    context_days = max(
        config.week_length_days,
        max(config.cooldown_days.values()),
    )
    previous_days = schedule[-context_days:]

    next_schedule = generate_initial_schedule(
        start_date=schedule[-1].date + timedelta(days=1),
        seed=seed,
        previous_days=previous_days,
        config=config,
    )

    # Append the new schedule to the existing one
    schedule.extend(next_schedule[1])  # next_schedule returns (skip_dates, schedule)

    # Save the updated state
    save_state(
        path=path,
        start_date=start_date,
        seed=seed,
        skip_dates=skip_dates,
        schedule=schedule,
    )

    print(f"Extended {path}")
    print(f"Total days: {len(schedule)}")
    print_schedule(schedule[-config.horizon_days:])

def command_create(args: argparse.Namespace) -> None:
    path = Path(args.file)
    config = load_config(Path(args.config))

    if path.exists() and not args.force:
        raise RuntimeError(
            f"{path} already exists. Use --force to overwrite it."
        )

    if args.start_date:
        start_date = parse_date(args.start_date)
    else:
        start_date = date.today()

    if args.seed is None:
        # This seed is saved, allowing all future updates to reproduce
        # the same schedule.
        seed = random.SystemRandom().randrange(0, 2**63)
    else:
        seed = args.seed

    skip_dates, schedule = generate_initial_schedule(
        start_date=start_date,
        seed=seed,
        config=config,
    )

    save_state(
        path=path,
        start_date=start_date,
        seed=seed,
        skip_dates=skip_dates,
        schedule=schedule,
    )

    print(f"Created {path}")
    print(f"Start date: {start_date}")
    print(f"Seed: {seed}")
    print_schedule(schedule)


def command_show(args: argparse.Namespace) -> None:
    path = Path(args.file)

    _, _, _, schedule = load_schedule_state(path)
    print_schedule(schedule)


def command_update(args: argparse.Namespace) -> None:
    path = Path(args.file)
    config = load_config(Path(args.config))
    skip_date = parse_date(args.skip_date)

    start_date, seed, skip_dates, schedule = update_schedule(
        path=path,
        new_skip_date=skip_date,
        config=config,
    )

    print(f"Updated {path}")
    print(f"Start date: {start_date}")
    print(f"Seed: {seed}")
    print(
        "Skip dates: "
        + ", ".join(
            str(value)
            for value in sorted(skip_dates)
        )
    )
    print_schedule(schedule)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic rolling workout scheduler."
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    create_parser = subparsers.add_parser(
        "create",
        help="Create a schedule using the configured horizon.",
    )

    create_parser.add_argument(
        "--start-date",
        help="Start date in YYYY-MM-DD format. Defaults to today.",
    )

    create_parser.add_argument(
        "--seed",
        type=int,
        help="Optional seed. A seed is generated if omitted.",
    )

    create_parser.add_argument(
        "--file",
        default=str(DEFAULT_STATE_FILE),
        help="State file path.",
    )

    create_parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_FILE),
        help="Scheduler config JSON path.",
    )

    create_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing state file.",
    )

    create_parser.set_defaults(function=command_create)

    show_parser = subparsers.add_parser(
        "show",
        help="Show the saved schedule.",
    )

    show_parser.add_argument(
        "--file",
        default=str(DEFAULT_STATE_FILE),
        help="State file path.",
    )

    show_parser.set_defaults(function=command_show)

    continue_parser = subparsers.add_parser(
        "continue",
        help="Extend the schedule by the configured horizon.",
    )

    continue_parser.add_argument(
        "--file",
        default=str(DEFAULT_STATE_FILE),
        help="State file path.",
    )

    continue_parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_FILE),
        help="Scheduler config JSON path.",
    )

    continue_parser.set_defaults(function=command_continue)

    update_parser = subparsers.add_parser(
        "update",
        help="Add a skip day and regenerate afterward.",
    )

    update_parser.add_argument(
        "skip_date",
        help="New skip date in YYYY-MM-DD format.",
    )

    update_parser.add_argument(
        "--file",
        default=str(DEFAULT_STATE_FILE),
        help="State file path.",
    )

    update_parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_FILE),
        help="Scheduler config JSON path.",
    )

    update_parser.set_defaults(function=command_update)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.function(args)
    except (
        FileNotFoundError,
        RuntimeError,
        ValueError,
    ) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
