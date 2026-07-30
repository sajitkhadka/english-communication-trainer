"""The analysis layer. No GPU, no models: `analyse` takes plain words and VAD spans,
which is exactly why it was written to take them rather than reach for the audio."""

from __future__ import annotations

import pytest

from pipeline.fillers import find_textual_fillers
from pipeline.metrics import (
    analyse,
    check_target_words,
    find_hesitations,
    segment_sentences,
    word_at_or_before,
    wpm,
)
from pipeline.transcribe import Word
from pipeline.vad import Span, silence_spans, voiced_seconds


def words_from(spec: list[tuple[str, float, float]]) -> list[Word]:
    return [Word(word=text, start=start, end=end) for text, start, end in spec]


SAMPLE = words_from(
    [
        ("So,", 0.0, 0.3),
        ("um,", 0.5, 0.8),
        ("we", 1.0, 1.2),
        ("had", 1.2, 1.4),
        ("a", 1.4, 1.5),
        ("bottleneck.", 1.5, 2.2),
        ("I", 3.0, 3.1),
        ("leveraged", 3.1, 3.7),
        ("a", 3.7, 3.8),
        ("queue.", 3.8, 4.3),
    ]
)


class TestSentences:
    def test_splits_on_terminal_punctuation(self):
        sentences = segment_sentences(SAMPLE)
        assert [s.text for s in sentences] == [
            "So, um, we had a bottleneck.",
            "I leveraged a queue.",
        ]
        assert sentences[0].start == 0.0
        assert sentences[1].end == 4.3

    def test_abbreviations_do_not_end_a_sentence(self):
        words = words_from([("Dr.", 0.0, 0.4), ("Reed", 0.4, 0.8), ("shipped.", 0.8, 1.3)])
        assert len(segment_sentences(words)) == 1

    def test_unpunctuated_transcript_falls_back_to_segments(self):
        class FakeSegment:
            def __init__(self, text, start, end, count):
                self.text, self.start, self.end = text, start, end
                self.words = [None] * count

        words = words_from([("hello", 0.0, 0.4), ("there", 0.4, 0.9)])
        fallback = [FakeSegment("hello", 0.0, 0.4, 1), FakeSegment("there", 0.4, 0.9, 1)]
        assert len(segment_sentences(words, fallback)) == 2

    def test_no_words_is_not_an_error(self):
        assert segment_sentences([]) == []


class TestFillers:
    def test_finds_hard_and_ambiguous_terms(self):
        hits = find_textual_fillers(SAMPLE)
        terms = {hit.term: hit.ambiguous for hit in hits}
        assert terms["um"] is False
        assert terms["so"] is True, "'so' is context-dependent and must be flagged, not condemned"

    def test_multiword_phrases_are_not_double_counted(self):
        words = words_from([("you", 0.0, 0.2), ("know", 0.2, 0.5), ("it", 0.5, 0.7)])
        hits = find_textual_fillers(words)
        assert [hit.term for hit in hits] == ["you know"]

    def test_punctuation_and_case_are_normalised(self):
        words = words_from([("Um,", 0.0, 0.3), ("UH!", 0.3, 0.6)])
        assert {hit.term for hit in find_textual_fillers(words)} == {"um", "uh"}


class TestHesitations:
    def test_voiced_span_without_words_is_a_hesitation(self):
        """The core of ADR 0002: Whisper drops the sound, Silero still hears it."""
        spans = [Span(0.0, 2.2), Span(2.4, 2.9), Span(3.0, 4.3)]
        found = find_hesitations(spans, SAMPLE, segment_sentences(SAMPLE))
        assert len(found) == 1
        assert found[0].start == 2.4
        assert found[0].word_coverage == 0.0
        assert found[0].after_word == "bottleneck."

    def test_fully_transcribed_span_is_not_flagged(self):
        spans = [Span(0.0, 2.2), Span(3.0, 4.3)]
        assert find_hesitations(spans, SAMPLE, segment_sentences(SAMPLE)) == []

    def test_span_shorter_than_the_threshold_is_ignored(self):
        spans = [Span(0.0, 2.2), Span(2.3, 2.4), Span(3.0, 4.3)]
        assert find_hesitations(spans, SAMPLE, segment_sentences(SAMPLE)) == []


class TestPausesAndPace:
    def test_silence_spans_exclude_leading_and_trailing_air(self):
        gaps = silence_spans([Span(1.0, 2.0), Span(3.0, 4.0)])
        assert [(gap.start, gap.end) for gap in gaps] == [(2.0, 3.0)]

    def test_voiced_seconds_sums_spans(self):
        assert voiced_seconds([Span(0.0, 1.5), Span(2.0, 2.5)]) == 2.0

    def test_pause_is_credited_to_the_word_that_preceded_it(self):
        assert word_at_or_before(SAMPLE, 2.6) == "bottleneck."

    def test_pause_lands_on_the_last_word_that_began_before_it(self):
        # VAD padding can open a gap before the previous word's end time; keying on
        # start keeps the attribution on the right word.
        assert word_at_or_before(SAMPLE, 1.45) == "a"

    @pytest.mark.parametrize(
        "count,seconds,expected",
        [(150, 60.0, 150.0), (75, 30.0, 150.0), (10, 0.0, None), (0, 60.0, None)],
    )
    def test_wpm(self, count, seconds, expected):
        assert wpm(count, seconds) == expected


class TestTargetWords:
    def test_matches_inflected_forms(self):
        hits = check_target_words(["leverage"], SAMPLE, segment_sentences(SAMPLE))
        assert hits[0].found is True
        assert hits[0].occurrences[0]["said_as"] == "leveraged"

    def test_reports_missing_words(self):
        hits = check_target_words(["mitigate"], SAMPLE, segment_sentences(SAMPLE))
        assert hits[0].found is False and hits[0].count == 0

    def test_matches_multiword_phrases(self):
        words = words_from([("we", 0.0, 0.2), ("de-risked", 0.2, 0.8), ("it", 0.8, 1.0)])
        hits = check_target_words(["de-risked"], words, segment_sentences(words))
        assert hits[0].found is True


class TestAnalyse:
    def test_end_to_end_over_synthetic_input(self):
        spans = [Span(0.0, 2.2), Span(2.4, 2.9), Span(3.0, 4.3)]
        result = analyse(SAMPLE, spans, 4.5, target_words=["leverage", "mitigate"])

        assert result.word_count == 10
        assert len(result.sentences) == 2
        assert len(result.hesitations) == 1
        # Both gaps here (0.2s and 0.1s) sit under the 0.3s floor: silence is still
        # measured, but neither is reported as a pause.
        assert result.pauses == []
        assert result.silence_sec == pytest.approx(0.3, abs=0.01)
        assert [hit.found for hit in result.target_hits] == [True, False]
        assert result.speaking_sec == pytest.approx(4.0, abs=0.01)

    def test_gap_above_the_floor_becomes_a_pause(self):
        result = analyse(SAMPLE, [Span(0.0, 2.2), Span(3.0, 4.3)], 4.5)
        assert len(result.pauses) == 1
        assert result.pauses[0].duration == pytest.approx(0.8, abs=0.01)
        assert result.pauses[0].after_word == "bottleneck."

    def test_survives_a_vad_failure(self):
        """Silero returning nothing must not take the whole analysis down."""
        result = analyse(SAMPLE, [], 4.5)
        assert result.word_count == 10
        assert result.pauses == []
        assert result.speaking_sec > 0
