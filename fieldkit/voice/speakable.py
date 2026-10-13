"""Fix text so a synthesiser says what you meant.

    from fieldkit.voice.speakable import speakable
    text, problems = speakable("Running the dual-track suite")

Every rule here was found on 2026-10-13 by LISTENING, not by review, and every
one of them had already shipped past a code review that could not hear.

# ============================================================================
# THE THREE FINDINGS
# ============================================================================
#
# 1. A HYPHEN BECOMES A PAUSE.
#    piper does not read letters. espeak-ng turns text into phonemes first, and
#    it treats "-" as a word boundary. So "Yoshie-chan" is spoken as two
#    utterances with a gap wedged between them. The owner, hearing it: "it can
#    not pronounce yoshie chan when it is joined by a -. it sounded halted like
#    Yohsh-eeh-eh cha".
#
#    This is not about names. "dual-track", "pre-commit", "2.4-gigahertz" and
#    "read-only" all get a hole punched in them. Written text keeps its hyphen;
#    spoken text does not.
#
# 2. A SPOKEN LINE HAS NO SPELLING.
#    The owner heard "the full Fieldkit suite" as "sweet", correctly, because
#    that is how the word is pronounced. The synthesiser was right and the WORD
#    was wrong. A homophone that is obvious on paper is invisible by ear, and
#    the listener gets no second reading.
#
# 3. A SPOKEN LINE HAS NO PUNCTUATION YOU CAN SEE, AND NO SCROLLBACK.
#    The owner: "on long sentences the voice gets a bit weird and hard to
#    understand". Measured: the worst line was 88 characters with a four-digit
#    number read out longhand in the middle, so the number was gone by the end.
#    One clause, the number early, nothing after the full stop that matters.
#
# ============================================================================
# WHAT THIS DOES NOT DO
# ============================================================================
# It does not translate, guess at foreign pronunciation, or respell proper
# nouns. espeak-ng applies ENGLISH letter-to-sound rules, so a name from
# another language is mangled with complete confidence and no warning. No
# amount of model quality fixes that, because the fault is upstream of the
# model. A name that matters needs a phonetic spelling kept beside the written
# one, and only a person can supply it.
"""
import re

# The ceiling a listener can hold in one breath group. See finding 3.
SPOKEN_LIMIT = 60

# Words whose homophone would change or destroy the meaning in the kind of
# short, number-heavy line this harness speaks. Deliberately NOT a general
# English homophone list: context decides, and a huge list would be noise.
HOMOPHONES = {
    "suite": "sweet",
    "won": "one",
    "ate": "eight",
    "for": "four",
    "to": "two",
    "weight": "wait",
    "sum": "some",
    "whole": "hole",
    "hour": "our",
}


def dehyphenate(text):
    """A hyphen BETWEEN LETTERS becomes a space. See finding 1.

    Only between letters: a minus sign in "-40 dBm" and a leading dash in a
    flag like "--json" are left alone, because they are not compounds and
    mangling them would be a different bug.
    """
    return re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", text)


def homophones_in(text):
    """-> [(word, what it sounds like)] for anything ambiguous by ear."""
    words = [w.strip(".,:;!?\"'()").lower() for w in text.split()]
    return [(w, HOMOPHONES[w]) for w in words if w in HOMOPHONES]


def speakable(text, limit=SPOKEN_LIMIT):
    """-> (text fixed for speech, [problems a human must decide about]).

    Hyphens are FIXED automatically, because removing them is always right and
    never changes meaning. Homophones and length are REPORTED, not fixed,
    because both need a human to choose different words.
    """
    fixed = dehyphenate(text)
    problems = []

    for word, sounds_like in homophones_in(fixed):
        problems.append(f"{word!r} sounds exactly like {sounds_like!r} and a listener "
                        f"cannot tell them apart. Choose another word.")

    if len(fixed) > limit:
        problems.append(f"{len(fixed)} characters, over the {limit} a listener can follow "
                        f"in one go. Split it, and put any number first.")

    if re.search(r"\b(?:one|two|three|four|five|six|seven|eight|nine)\s+(?:hundred|thousand)\b",
                 fixed, re.I):
        problems.append("a number is spelled out in words, which takes a long time to say "
                        "and is forgotten by the end of the sentence. Use digits.")

    return fixed, problems


def check(text, limit=SPOKEN_LIMIT):
    """-> [problems], including the hyphens, for use in a test rather than a fix."""
    problems = []
    if dehyphenate(text) != text:
        problems.append("contains a hyphen between letters, which espeak-ng speaks as a "
                        "pause, so the word is broken in two")
    return problems + speakable(text, limit)[1]
