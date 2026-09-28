# Workout Scheduler Configuration

The default settings are in `workout_config.json`. Generation commands read
that file automatically; pass `--config path/to/file.json` to use another one.
The selected config is used by `create`, `update`, `edit`, and `continue`.

Use `python excignment.py edit YYYY-MM-DD PRIMARY [--secondary SECONDARY]` to
replace a date's workouts. Primary choices are `leg`, `run`, `upper`, and
`yoga`; the optional secondary is `hip` or `core`. The selected date is counted
as a workout day, dates before it are preserved without revalidating their
workout conflicts, and that date onward is regenerated. The chosen combination
is kept even if it conflicts with the scheduler's normal same-day pairing rules.

Weekday numbers follow Python's convention: Monday is `0`, Sunday is `6`.
Set `fixed_run_weekday` to `null` to disable the recurring weekday run.

`horizon_days` controls the length of a newly created or continued schedule;
`week_length_days` sets the blocks used for weekly targets. `candidate_attempts`
controls how many deterministic candidate schedules are compared.

`weekly_targets` are preferred placement and scoring targets. `skip_week_minimums`
are hard minimums for each complete week block when a schedule contains skips. An
incomplete trailing block is exempt, as is a block touched by a consecutive skip
run at least `skip_streak_exemption_length` days long.
`minimum_hip_workouts` is a separate total minimum for the full generated horizon.
Hip work is secondary: it is added only to run or leg days. A four-day hip
cooldown and preferred interval yields roughly 1.5-2 hip sessions per week.

`cooldown_days` values are minimum date-index gaps between workouts of a type. For
example, a run cooldown of `2` means run dates are at least two days apart.
`hip_interval_weights` controls the relative chance of each hip interval while
building candidates; `preferred_hip_interval_days` controls scoring preference.
Core is paired with upper-body workouts and may also be added to yoga days when
the core cooldown allows it. Hip sessions can be paired with a run, leg, or
yoga day. Hip and core are never scheduled on the same day. Upper-body workouts
target three per week, with at least two days between sessions. Over the
configured 32-day horizon this is approximately 2.75 sessions per week.

`score_weights` change how valid candidates are ranked. They are score points, not
probabilities. `weekend_days` determines which days receive the
`weekend_yoga_bonus` when yoga lands there; `weekday_yoga_penalty` slightly lowers
the score of candidates with weekday yoga.
After the planned workout passes, eligible workouts are used up to their preferred
weekly maxima before yoga is assigned. An empty weekend day between workouts can be
kept as a yoga spacer; other empty days receive yoga only when no targeted workout
fits.

Changing the config changes future generation, updates, and continuations. Keep the
same config alongside a saved schedule when you want to reproduce its generation.