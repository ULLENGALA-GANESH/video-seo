"""Deterministic keyword relevance from the words in an English transcript."""

import math
import re
from collections import Counter


STOP_WORDS = set("""
a about above across after again against all almost along already also although always am
an and another any are around as at away back be because been before being below between
both but by can cannot come could did do does doing done down during each either enough
even ever every few first for from further get gets getting give given go goes going good
got had happens has have having he her here hers herself him himself his how however i if
in into is it its itself just keep kind know last let lets like likely look looking made
make makes making many may me might more most much must my myself near need next no nor
now of off often on once one only onto or other our ours ourselves out over own part
perhaps put quite really right said same say says see seem set several she should since
so some something sometimes still such take takes than that thats the their theirs them
themselves then there therefore these they thing things think this those though through
thus to today together too toward two under until up us use used using usually very via
want was way we well were what whatever when where whether which while who whom whose
why will with within without would yes yet you your yours yourself yourselves
""".split())

# Keep Unicode letters and internal hyphens in terms such as "on-page".
TOKEN = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|[\r\n]+")


def transcript_segments(text):
    return [
        {"text": part.strip(), "start_seconds": None, "end_seconds": None}
        for part in SENTENCE_BREAK.split(text) if part.strip()
    ]


def collect_phrases(segments):
    """Count contiguous phrases and remember where each one first appeared."""
    counts = Counter()
    first = {}
    spread = Counter()
    for index, segment in enumerate(segments):
        sentence = segment["text"]
        segment_phrases = set()
        words = []
        previous_end = 0
        for token in TOKEN.finditer(sentence):
            word = token.group().casefold()
            if not sentence[previous_end:token.start()].isspace():
                words.clear()
            previous_end = token.end()
            if word in STOP_WORDS or word.isnumeric() or not 2 <= len(word) <= 48:
                words.clear()
                continue
            words.append((word, token.start()))
            words = words[-3:]
            for start in range(len(words)):
                phrase = " ".join(value for value, _ in words[start:])
                counts[phrase] += 1
                first.setdefault(phrase, (index, words[start][1]))
                segment_phrases.add(phrase)
        spread.update(segment_phrases)
    return counts, first, spread


LENGTH_BONUS = {1: 0, 2: 0.55, 3: 0.4}


def relevance_weight(phrase, mentions, segment_count, title_terms):
    terms = phrase.split()
    title_overlap = len(title_terms.intersection(terms)) / len(terms)
    return (
        math.log2(1 + mentions) * (1 + LENGTH_BONUS[len(terms)])
        + 0.45 * math.log2(1 + segment_count)
        + 0.9 * title_overlap
    )


def rank_keywords(segments, title, limit=12):
    """Rank transcript phrases, suppress redundant matches and attach first-use evidence."""
    counts, first, spread = collect_phrases(segments)
    title_terms = {token.group().casefold() for token in TOKEN.finditer(title)} - STOP_WORDS
    weights = {
        phrase: relevance_weight(phrase, mentions, spread[phrase], title_terms)
        for phrase, mentions in counts.items()
    }
    ranked = sorted(weights, key=lambda p: (-weights[p], first[p], p))
    result = []
    chosen = []
    for phrase in ranked:
        # A shorter phrase with identical occurrences adds no new evidence.
        framed = f" {phrase} "
        if any(counts[phrase] == counts[other] and
               (framed in f" {other} " or f" {other} " in framed) for other in chosen):
            continue
        index, offset = first[phrase]
        segment = segments[index]
        source = segment["text"]
        left = max(0, offset - 100)
        right = min(len(source), max(left + 250, offset + len(phrase)))
        result.append({
            "phrase": phrase,
            "score": max(1, round(100 * weights[phrase] / weights[ranked[0]])),
            "occurrences": counts[phrase],
            "evidence": source[left:right].strip(),
            "start_seconds": segment["start_seconds"],
        })
        chosen.append(phrase)
        if len(result) == limit:
            break
    return result


def summarize(segments, title):
    transcript = " ".join(item["text"] for item in segments)
    keywords = rank_keywords(segments, title)
    return {
        "transcript": transcript,
        "segments": segments,
        "keywords": keywords,
        "word_count": sum(1 for _ in TOKEN.finditer(transcript)),
        "keyword_count": len(keywords),
    }
