"""Textual filler detection (PRD 5.4).

Deliberately shallow: we count and locate candidates, we do not decide whether a
given "actually" was filler or content. Ambiguous terms are flagged so the skill
can judge them in context, which is exactly the split the PRD asks for - the
backend counts, Claude judges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Unambiguous hesitation sounds. Kept as canonical spellings for reference; matching
# itself goes through _HARD_FILLER_RE below.
HARD_FILLERS: tuple[str, ...] = (
    "um", "umm", "uhm", "uh", "uhh", "er", "erm", "ah", "ahh", "eh", "hmm", "hm", "mmm",
)  # fmt: skip

# Hesitation sounds are vocalized with arbitrary elongation - "um", "ummm" and
# "ummmmm" are the same sound to a listener, and to WhisperX's decoder, which is free
# to pick any of them. An enumerated spelling list silently misses whichever one it
# didn't guess, so hard fillers are matched as letter-repetition patterns instead.
_HARD_FILLER_RE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"u+h*m+",  # um, umm, uhm, ummm, uuhmm...
        r"u+h+",    # uh, uhh, uuhh...
        r"e+r+m*",  # er, err, erm, erm...
        r"a+h+",    # ah, ahh, aah...
        r"e+h+",    # eh, ehh, eeh...
        r"h+m+",    # hm, hmm, hhmm...
        r"m{2,}",   # bare mmm (2+ so a lone "m" initial isn't swept in)
    )
)  # fmt: skip


def _is_hard_filler(token: str) -> bool:
    return any(pattern.fullmatch(token) for pattern in _HARD_FILLER_RE)


# Discourse markers that are often filler but can be legitimate. Flagged, not condemned.
SOFT_FILLERS: tuple[str, ...] = (
    "like", "so", "well", "right", "okay", "ok", "basically", "actually", "literally",
    "obviously", "honestly",
)  # fmt: skip

# Multi-word crutches.
PHRASE_FILLERS: tuple[str, ...] = (
    "you know", "i mean", "kind of", "sort of", "you see", "let me think",
    "how do i say", "how to say", "or something", "and stuff", "and all that",
    "at the end of the day", "to be honest",
)  # fmt: skip

# Hyphens are kept: "de-risk" is one term, and splitting it would collapse every
# "de-" word onto the same token.
_WORD_RE = re.compile(r"[a-z']+(?:-[a-z']+)*")


@dataclass
class FillerHit:
    term: str
    start: float | None
    end: float | None
    word_index: int
    ambiguous: bool


def normalise(word: str) -> str:
    """Lowercase and strip punctuation so "Um," matches "um"."""
    match = _WORD_RE.findall(word.lower())
    return match[0] if match else ""


def find_textual_fillers(words: list) -> list[FillerHit]:
    """Scan aligned words for filler terms. `words` items need .word/.start/.end."""
    tokens = [normalise(w.word) for w in words]
    hits: list[FillerHit] = []
    consumed: set[int] = set()

    # Longest phrases first so "you know" isn't double-counted as "you" + "know".
    for phrase in sorted(PHRASE_FILLERS, key=lambda p: -len(p.split())):
        parts = phrase.split()
        span = len(parts)
        for i in range(len(tokens) - span + 1):
            if any(j in consumed for j in range(i, i + span)):
                continue
            if tokens[i : i + span] == parts:
                consumed.update(range(i, i + span))
                hits.append(
                    FillerHit(
                        term=phrase,
                        start=words[i].start,
                        end=words[i + span - 1].end,
                        word_index=i,
                        ambiguous=phrase in ("kind of", "sort of", "to be honest"),
                    )
                )

    for i, token in enumerate(tokens):
        if i in consumed or not token:
            continue
        if _is_hard_filler(token):
            hits.append(FillerHit(token, words[i].start, words[i].end, i, False))
        elif token in SOFT_FILLERS:
            hits.append(FillerHit(token, words[i].start, words[i].end, i, True))

    hits.sort(key=lambda h: h.word_index)
    return hits
