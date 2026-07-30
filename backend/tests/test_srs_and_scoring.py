"""SM-2 and the score weighting - the arithmetic deliberately kept out of prompts."""

from __future__ import annotations

from datetime import date

import pytest

from app.db import SCORE_WEIGHTS, compute_overall
from app.srs import DEFAULT_EASE, MIN_EASE, compute_mastery, grade_from_usage, review


class TestGrading:
    @pytest.mark.parametrize(
        "used,correct,expected",
        [(True, True, 5), (True, False, 2), (False, False, 1), (False, True, 1)],
    )
    def test_usage_maps_to_quality(self, used, correct, expected):
        assert grade_from_usage(used, correct) == expected


class TestReview:
    def test_first_success_schedules_one_day_out(self):
        state = review(None, 5, on_date=date(2026, 7, 30))
        assert state.interval_days == 1
        assert state.repetitions == 1
        assert state.due_date == "2026-07-31"
        assert state.ease > DEFAULT_EASE

    def test_second_success_jumps_to_six_days(self):
        word = {"ease": 2.6, "repetitions": 1, "interval_days": 1, "times_seen": 1}
        assert review(word, 5, on_date=date(2026, 7, 30)).interval_days == 6

    def test_later_successes_multiply_by_ease(self):
        word = {"ease": 2.5, "repetitions": 4, "interval_days": 10, "times_seen": 4}
        assert review(word, 5, on_date=date(2026, 7, 30)).interval_days == 25

    def test_misuse_brings_the_word_straight_back(self):
        word = {"ease": 2.5, "repetitions": 6, "interval_days": 40, "times_seen": 6}
        state = review(word, 2, on_date=date(2026, 7, 30))
        assert state.interval_days == 1
        assert state.repetitions == 0
        assert state.due_date == "2026-07-31"
        assert state.ease < 2.5, "a lapse must also make the word easier to trigger next time"

    def test_ease_never_falls_below_the_floor(self):
        word = {"ease": 1.3, "repetitions": 0, "interval_days": 1, "times_seen": 9}
        for _ in range(5):
            word = {**word, "ease": review(word, 0).ease}
        assert word["ease"] == MIN_EASE

    def test_quality_is_clamped_to_the_valid_range(self):
        assert review(None, 99).ease == review(None, 5).ease


class TestMastery:
    def test_new_word_is_unmastered(self):
        assert compute_mastery(
            repetitions=0, ease=2.5, interval_days=0, times_seen=0, times_used_correctly=0
        ) == 0.0

    def test_mastery_rises_with_streak_and_interval(self):
        low = compute_mastery(
            repetitions=1, ease=2.5, interval_days=1, times_seen=1, times_used_correctly=1
        )
        high = compute_mastery(
            repetitions=6, ease=2.8, interval_days=60, times_seen=6, times_used_correctly=6
        )
        assert 0 < low < high <= 1.0

    def test_misuse_history_holds_mastery_down(self):
        reliable = compute_mastery(
            repetitions=4, ease=2.5, interval_days=30, times_seen=4, times_used_correctly=4
        )
        shaky = compute_mastery(
            repetitions=4, ease=2.5, interval_days=30, times_seen=8, times_used_correctly=4
        )
        assert shaky < reliable


class TestOverall:
    def test_weights_sum_to_one(self):
        assert sum(SCORE_WEIGHTS.values()) == pytest.approx(1.0)

    def test_uniform_scores_average_to_themselves(self):
        assert compute_overall(dict.fromkeys(SCORE_WEIGHTS, 7.0)) == 7.0

    def test_missing_target_usage_renormalises(self):
        """Free-form has no target words, so the remaining dimensions must still
        produce a number on the same 0-10 scale."""
        scores = {key: 7.0 for key in SCORE_WEIGHTS if key != "target_usage"}
        assert compute_overall(scores) == 7.0

    def test_weighting_actually_applies(self):
        scores = dict.fromkeys(SCORE_WEIGHTS, 5.0)
        scores["vocab_range"] = 10.0  # heaviest dimension
        assert compute_overall(scores) > 5.0

    def test_no_scores_is_zero_not_a_crash(self):
        assert compute_overall({}) == 0.0
