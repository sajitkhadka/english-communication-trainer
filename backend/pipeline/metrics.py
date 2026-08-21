"""Everything deterministic that Claude would otherwise have to work out itself:
sentence segmentation, WPM, the pause map, acoustic hesitations, target-word hits
(PRD 5.4). Pure functions over words + VAD spans, so all of it is unit-testable
without a GPU.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from app.config import settings

from .fillers import FillerHit, find_textual_fillers, normalise
from .transcribe import Word
from .vad import Span, silence_spans, voiced_seconds

# A sentence ends at .?! unless the token is a common abbreviation or an initial.
_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "vs.", "etc.", "e.g.", "i.e.",
    "approx.", "no.", "st.",
}  # fmt: skip
_SENTENCE_END = re.compile(r"[.!?]+[\"')\]]*$")
_INITIAL = re.compile(r"^[A-Za-z]\.$")


@dataclass
class Sentence:
    index: int
    text: str
    start: float | None
    end: float | None
    word_count: int
    first_word_index: int
    last_word_index: int

    @property
    def duration(self) -> float | None:
        if self.start is None or self.end is None:
            return None
        return round(self.end - self.start, 3)


@dataclass
class Pause:
    start: float
    end: float
    duration: float
    after_word: str | None
    sentence_index: int | None
    mid_sentence: bool


@dataclass
class Hesitation:
    """A voiced span with (almost) no aligned words: a filler Whisper dropped."""

    start: float
    end: float
    duration: float
    word_coverage: float
    after_word: str | None
    sentence_index: int | None
    # Which sound this was, e.g. "um" - filled in later by `find_hidden_fillers`
    # when a second ASR pass identifies what Whisper only heard as silence.
    heard_as: str | None = None


@dataclass
class HiddenFiller:
    """A filler/hesitation sound a second ASR pass heard that Whisper's own
    output shows no trace of at all - not as transcript text, and not as an
    acoustic hesitation either. See `find_hidden_fillers`."""

    term: str
    start: float
    sentence_index: int | None


@dataclass
class TargetHit:
    term: str
    found: bool
    count: int
    occurrences: list[dict[str, Any]] = field(default_factory=list)
    # Which ASR pass(es) heard this term. "whisper" means it is located in the
    # transcript Claude reads; a term found only by a cross-check pass (e.g.
    # "parakeet") has no located occurrence - see `apply_cross_check`.
    confirmed_by: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# sentences
# --------------------------------------------------------------------------- #


def segment_sentences(words: list[Word], fallback_segments: list | None = None) -> list[Sentence]:
    """Split the aligned word stream into sentences on terminal punctuation.

    WhisperX keeps punctuation attached to words, so this is reliable. If the model
    returned no punctuation at all we fall back to the raw Whisper segments.
    """
    if not words:
        return []

    sentences: list[Sentence] = []
    buffer: list[tuple[int, Word]] = []

    def flush() -> None:
        if not buffer:
            return
        indices = [i for i, _ in buffer]
        items = [w for _, w in buffer]
        starts = [w.start for w in items if w.start is not None]
        ends = [w.end for w in items if w.end is not None]
        sentences.append(
            Sentence(
                index=len(sentences),
                text=" ".join(w.word for w in items).strip(),
                start=min(starts) if starts else None,
                end=max(ends) if ends else None,
                word_count=len(items),
                first_word_index=indices[0],
                last_word_index=indices[-1],
            )
        )
        buffer.clear()

    for i, word in enumerate(words):
        buffer.append((i, word))
        token = word.word.strip()
        if not _SENTENCE_END.search(token):
            continue
        if token.lower() in _ABBREVIATIONS or _INITIAL.match(token):
            continue
        flush()
    flush()

    if len(sentences) <= 1 and fallback_segments:
        rebuilt = _sentences_from_segments(fallback_segments)
        if len(rebuilt) > len(sentences):
            return rebuilt
    return sentences


def _sentences_from_segments(segments: list) -> list[Sentence]:
    out: list[Sentence] = []
    cursor = 0
    for seg in segments:
        count = len(getattr(seg, "words", []) or [])
        out.append(
            Sentence(
                index=len(out),
                text=seg.text.strip(),
                start=seg.start,
                end=seg.end,
                word_count=count or len(seg.text.split()),
                first_word_index=cursor,
                last_word_index=max(cursor, cursor + count - 1),
            )
        )
        cursor += count
    return out


def sentence_index_at(sentences: list[Sentence], time: float) -> int | None:
    """Index of the sentence containing `time`, else the sentence just before it."""
    best: int | None = None
    for s in sentences:
        if s.start is None:
            continue
        if s.end is not None and s.start <= time <= s.end:
            return s.index
        if s.start <= time:
            best = s.index
    return best


# --------------------------------------------------------------------------- #
# pace
# --------------------------------------------------------------------------- #


def wpm(word_count: int, seconds: float | None) -> float | None:
    if not seconds or seconds <= 0 or word_count <= 0:
        return None
    return round(word_count / (seconds / 60.0), 1)


def per_minute(count: int, seconds: float | None) -> float | None:
    if not seconds or seconds <= 0:
        return None
    return round(count / (seconds / 60.0), 2)


# --------------------------------------------------------------------------- #
# pauses & hesitations
# --------------------------------------------------------------------------- #


def word_at_or_before(words: list[Word], time: float) -> str | None:
    """The last word that *began* before `time`.

    Deliberately keyed on start rather than end: the VAD pads its boundaries by a few
    tens of milliseconds, so a gap can open slightly before the preceding word's end
    time, and keying on end then credits the pause to the word before last.
    """
    found: str | None = None
    for w in words:
        if w.start is None:
            continue
        if w.start > time:
            break
        found = w.word
    return found


def build_pauses(
    gaps: list[Span],
    words: list[Word],
    sentences: list[Sentence],
    *,
    min_pause: float | None = None,
) -> list[Pause]:
    threshold = settings.min_pause_sec if min_pause is None else min_pause
    pauses: list[Pause] = []
    for gap in gaps:
        if gap.duration < threshold:
            continue
        s_idx = sentence_index_at(sentences, gap.start)
        # Mid-sentence when the sentence containing the gap continues past it.
        mid = False
        if s_idx is not None and s_idx < len(sentences):
            sentence = sentences[s_idx]
            if sentence.end is not None and sentence.end > gap.end + 0.05:
                mid = True
        pauses.append(
            Pause(
                start=gap.start,
                end=gap.end,
                duration=gap.duration,
                after_word=word_at_or_before(words, gap.start),
                sentence_index=s_idx,
                mid_sentence=mid,
            )
        )
    return pauses


def pause_buckets(pauses: list[Pause]) -> dict[str, int]:
    long_threshold = settings.long_pause_sec
    buckets = {"short": 0, "medium": 0, "long": 0}
    for p in pauses:
        if p.duration < 0.7:
            buckets["short"] += 1
        elif p.duration < long_threshold:
            buckets["medium"] += 1
        else:
            buckets["long"] += 1
    return buckets


def find_hesitations(
    spans: list[Span],
    words: list[Word],
    sentences: list[Sentence],
    *,
    min_duration: float | None = None,
    max_coverage: float | None = None,
) -> list[Hesitation]:
    """Voiced spans the transcript does not account for (PRD 5.2).

    Whisper normalises "um"/"uh" away, but Silero still hears them. A span of
    speech whose duration is barely covered by aligned words is therefore a
    vocalization with no text - a hesitation we can report even though the word
    never made it into the transcript.
    """
    min_duration = settings.min_hesitation_sec if min_duration is None else min_duration
    max_coverage = settings.hesitation_max_word_coverage if max_coverage is None else max_coverage
    timed = [w for w in words if w.start is not None and w.end is not None]
    out: list[Hesitation] = []

    for span in spans:
        if span.duration < min_duration:
            continue
        covered = 0.0
        for w in timed:
            if w.end <= span.start:
                continue
            if w.start >= span.end:
                break
            covered += min(w.end, span.end) - max(w.start, span.start)
        coverage = round(covered / span.duration, 3) if span.duration else 1.0
        if coverage > max_coverage:
            continue
        out.append(
            Hesitation(
                start=span.start,
                end=span.end,
                duration=span.duration,
                word_coverage=coverage,
                after_word=word_at_or_before(words, span.start),
                sentence_index=sentence_index_at(sentences, span.start),
            )
        )
    return out


def find_hidden_fillers(
    parakeet_words: list[Word],
    whisper_fillers: list[FillerHit],
    whisper_hesitations: list[Hesitation],
    sentences: list[Sentence],
    *,
    match_window: float = 1.0,
) -> tuple[list[Hesitation], list[HiddenFiller]]:
    """Reconcile a second ASR pass's filler/hesitation sounds against what Whisper
    already reports, in two ways depending on what Whisper has for that moment:

    - An acoustic hesitation already flags *something* there, but only as an
      untranscribed span - Whisper never says what it was. The second pass's term
      enriches that same entry (`heard_as`) rather than adding a duplicate.
    - Nothing is there at all - the failure mode neither the transcript text nor
      the acoustic-hesitation layer catches, because a real self-correction
      ("and it's uh, this is...") got smoothed into clean prose *and* word-aligned
      across, so `find_hesitations` sees full coverage and reports nothing. That
      is genuinely new information, returned as a `HiddenFiller`.

    Nothing is ever dropped: every sound the second pass hears either enriches an
    existing entry or is reported as a new one.
    """
    # Padded: Whisper's and Parakeet's boundaries for the same acoustic event
    # routinely differ by tens of milliseconds since the two models decode
    # independently - an exact-window check would treat that as two events.
    filler_windows = [
        (h.start - match_window, h.start + match_window)
        for h in whisper_fillers
        if h.start is not None
    ]
    hesitations = list(whisper_hesitations)
    hidden: list[HiddenFiller] = []

    for hit in find_textual_fillers(parakeet_words):
        if hit.start is None:
            continue
        matched = next(
            (
                i
                for i, h in enumerate(hesitations)
                if h.heard_as is None
                and h.start - match_window <= hit.start <= h.end + match_window
            ),
            None,
        )
        if matched is not None:
            hesitations[matched] = replace(hesitations[matched], heard_as=hit.term)
        elif not any(lo <= hit.start <= hi for lo, hi in filler_windows):
            hidden.append(
                HiddenFiller(
                    term=hit.term,
                    start=hit.start,
                    sentence_index=sentence_index_at(sentences, hit.start),
                )
            )
    return hesitations, hidden


# --------------------------------------------------------------------------- #
# target words
# --------------------------------------------------------------------------- #


def _stem(token: str) -> str:
    """Crude suffix strip so "leveraged" matches the target "leverage".

    The trailing-e collapse is what makes it symmetric: "leveraged" strips to
    "leverag", so the target "leverage" has to reduce to the same thing or the two
    never meet. Length floors keep it off short words ("had" must not become "ha").
    """
    for suffix in ("ing", "ed", "es", "s", "d"):
        if len(token) - len(suffix) >= 4 and token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    if len(token) >= 4 and token.endswith("e"):
        token = token[:-1]
    return token


def check_target_words(
    terms: list[str], words: list[Word], sentences: list[Sentence]
) -> list[TargetHit]:
    """Locate each target term. Matching is lenient (stems, multi-word phrases);
    whether the usage was *correct* is Claude's call, not ours."""
    tokens = [normalise(w.word) for w in words]
    stems = [_stem(t) for t in tokens]
    hits: list[TargetHit] = []

    for term in terms:
        parts = [normalise(p) for p in term.split() if normalise(p)]
        if not parts:
            hits.append(TargetHit(term=term, found=False, count=0))
            continue
        span = len(parts)
        target_stems = [_stem(p) for p in parts]
        occurrences: list[dict[str, Any]] = []
        for i in range(len(tokens) - span + 1):
            window = tokens[i : i + span]
            window_stems = stems[i : i + span]
            if window == parts or window_stems == target_stems:
                word = words[i]
                occurrences.append(
                    {
                        "start": word.start,
                        "said_as": " ".join(w.word for w in words[i : i + span]),
                        "sentence": sentence_index_at(sentences, word.start)
                        if word.start is not None
                        else None,
                    }
                )
        hits.append(
            TargetHit(
                term=term,
                found=bool(occurrences),
                count=len(occurrences),
                occurrences=occurrences,
                confirmed_by=["whisper"] if occurrences else [],
            )
        )
    return hits


def target_words_in_tokens(terms: list[str], tokens: list[str]) -> set[str]:
    """Which of `terms` appear anywhere in a flat word list, using the same
    lenient stem/phrase matching as `check_target_words` - for cross-checking
    against a second ASR pass that carries no per-word timing."""
    norm_tokens = [normalise(t) for t in tokens]
    stems = [_stem(t) for t in norm_tokens]
    found: set[str] = set()
    for term in terms:
        parts = [normalise(p) for p in term.split() if normalise(p)]
        if not parts:
            continue
        span = len(parts)
        target_stems = [_stem(p) for p in parts]
        for i in range(len(norm_tokens) - span + 1):
            if norm_tokens[i : i + span] == parts or stems[i : i + span] == target_stems:
                found.add(term)
                break
    return found


def apply_cross_check(
    hits: list[TargetHit], heard_tokens: list[str], *, source: str = "parakeet"
) -> list[TargetHit]:
    """Mark target words a second ASR pass heard that Whisper's transcript missed.

    Whisper stays the transcript Claude reads: this only flips `found` for a term
    Whisper dropped, and never invents a sentence location for it - the brief
    renders those as unverified so Claude does not treat a phantom citation as
    real context.
    """
    present = target_words_in_tokens([h.term for h in hits], heard_tokens)
    return [
        replace(h, found=True, count=max(h.count, 1), confirmed_by=[*h.confirmed_by, source])
        if h.term in present and source not in h.confirmed_by
        else h
        for h in hits
    ]


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #


@dataclass
class Analysis:
    sentences: list[Sentence]
    pauses: list[Pause]
    hesitations: list[Hesitation]
    textual_fillers: list[FillerHit]
    target_hits: list[TargetHit]
    speaking_sec: float
    silence_sec: float
    duration_sec: float
    word_count: int
    # Populated after the fact by the cross-check pass (see pipeline/runner.py) -
    # `analyse()` itself never touches a second ASR model.
    hidden_fillers: list[HiddenFiller] = field(default_factory=list)


def analyse(
    words: list[Word],
    spans: list[Span],
    duration_sec: float,
    *,
    fallback_segments: list | None = None,
    target_words: list[str] | None = None,
) -> Analysis:
    sentences = segment_sentences(words, fallback_segments)
    gaps = silence_spans(spans)
    speaking = voiced_seconds(spans)
    if not spans and duration_sec:
        # No VAD available: fall back to the span the words themselves occupy.
        timed = [w for w in words if w.start is not None and w.end is not None]
        speaking = round(timed[-1].end - timed[0].start, 3) if timed else duration_sec
    return Analysis(
        sentences=sentences,
        pauses=build_pauses(gaps, words, sentences),
        hesitations=find_hesitations(spans, words, sentences),
        textual_fillers=find_textual_fillers(words),
        target_hits=check_target_words(target_words or [], words, sentences),
        speaking_sec=speaking,
        silence_sec=round(sum(g.duration for g in gaps), 3),
        duration_sec=round(duration_sec, 3),
        word_count=len(words),
    )
