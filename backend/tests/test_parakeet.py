"""Chunk merging and token-to-word merging are pure and GPU-free, unlike the
rest of pipeline/parakeet.py."""

from __future__ import annotations

from pipeline.parakeet import _merge_chunks, _words_from_tokens
from pipeline.vad import Span


class TestMergeChunks:
    def test_empty_spans_yield_no_chunks(self):
        assert _merge_chunks([]) == []

    def test_short_spans_merge_into_one_chunk(self):
        spans = [Span(0.0, 2.0), Span(2.5, 4.0), Span(4.5, 6.0)]
        assert _merge_chunks(spans, max_chunk_sec=20.0) == [Span(0.0, 6.0)]

    def test_splits_once_the_window_would_exceed_the_cap(self):
        spans = [Span(0.0, 5.0), Span(6.0, 9.0), Span(20.0, 22.0)]
        # third span starts 20s after the first chunk's start: over a 10s cap, so it
        # must start a new chunk rather than stretch the first one.
        chunks = _merge_chunks(spans, max_chunk_sec=10.0)
        assert chunks == [Span(0.0, 9.0), Span(20.0, 22.0)]

    def test_always_cuts_between_spans_never_inside_one(self):
        spans = [Span(0.0, 15.0), Span(16.0, 17.0)]
        chunks = _merge_chunks(spans, max_chunk_sec=10.0)
        # The first span alone already exceeds the cap, but it is never split -
        # only the boundary before the next span is a valid cut point.
        assert chunks == [Span(0.0, 15.0), Span(16.0, 17.0)]


class TestWordsFromTokens:
    def test_merges_subword_pieces_into_whole_words(self):
        # "demo" split as "dem" + "o", "recording," as "rec"+"ord"+"ing"+",".
        tokens = [" dem", "o", " rec", "ord", "ing", ","]
        timestamps = [1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
        words = _words_from_tokens(tokens, timestamps)
        assert [w.word for w in words] == ["demo", "recording,"]
        assert [w.start for w in words] == [1.0, 1.4]

    def test_applies_a_chunk_offset(self):
        words = _words_from_tokens([" hi"], [0.5], offset=20.0)
        assert words == [words[0]]  # sanity: single word
        assert words[0].word == "hi"
        assert words[0].start == 20.5

    def test_empty_tokens_yield_no_words(self):
        assert _words_from_tokens([], []) == []
