"""SM-2 spaced repetition (PRD 10).

Deliberately kept out of the skills: Claude judges *whether a word was used well*,
the arithmetic that turns that judgement into an interval is deterministic and lives
here, so review scheduling stays consistent regardless of which session it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

MIN_EASE = 1.3
DEFAULT_EASE = 2.5
PASS_THRESHOLD = 3  # SM-2: quality < 3 is a lapse


@dataclass
class Review:
    ease: float
    interval_days: int
    repetitions: int
    due_date: str
    mastery: float


def grade_from_usage(used: bool, used_correctly: bool) -> int:
    """Map the only signal we have (did the target word get used, and well?) to SM-2 quality.

    5 = used correctly and unprompted, 2 = attempted but misused (a lapse worth
    resurfacing fast), 1 = not used at all despite being a target.
    """
    if used and used_correctly:
        return 5
    if used:
        return 2
    return 1


def compute_mastery(
    *, repetitions: int, ease: float, interval_days: int, times_seen: int, times_used_correctly: int
) -> float:
    """Derived 0..1 display value (PRD 10).

    Blends how long the word survives between reviews, how reliably it has been used,
    and how many successful reviews it has accumulated.
    """
    if repetitions == 0 and times_seen == 0:
        # Never practised. The default ease factor is a starting point, not evidence,
        # so it must not lend a brand-new word any mastery on the vocabulary page.
        return 0.0
    retention = min(interval_days / 60.0, 1.0)
    accuracy = (times_used_correctly / times_seen) if times_seen else 0.0
    streak = min(repetitions / 5.0, 1.0)
    ease_factor = min(max((ease - MIN_EASE) / (3.0 - MIN_EASE), 0.0), 1.0)
    score = 0.40 * retention + 0.30 * accuracy + 0.20 * streak + 0.10 * ease_factor
    return round(min(max(score, 0.0), 1.0), 3)


def review(
    word: dict[str, Any] | None,
    quality: int,
    *,
    on_date: date | None = None,
) -> Review:
    """Apply one SM-2 review to a word row and return its new scheduling state."""
    today = on_date or date.today()  # noqa: DTZ011 - local date; see db.today()
    quality = max(0, min(5, int(quality)))

    ease = float((word or {}).get("ease") or DEFAULT_EASE)
    repetitions = int((word or {}).get("repetitions") or 0)
    interval = int((word or {}).get("interval_days") or 0)
    times_seen = int((word or {}).get("times_seen") or 0) + 1
    times_correct = int((word or {}).get("times_used_correctly") or 0) + (
        1 if quality >= 4 else 0
    )

    if quality < PASS_THRESHOLD:
        # Lapse: bring it straight back. A misuse (q=2) is worth one more day than a
        # complete no-show (q<=1), which should return tomorrow at the latest.
        repetitions = 0
        interval = 1
    else:
        if repetitions == 0:
            interval = 1
        elif repetitions == 1:
            interval = 6
        else:
            interval = max(1, round(interval * ease))
        repetitions += 1

    ease = max(MIN_EASE, ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
    ease = round(ease, 3)

    return Review(
        ease=ease,
        interval_days=interval,
        repetitions=repetitions,
        due_date=(today + timedelta(days=interval)).isoformat(),
        mastery=compute_mastery(
            repetitions=repetitions,
            ease=ease,
            interval_days=interval,
            times_seen=times_seen,
            times_used_correctly=times_correct,
        ),
    )
