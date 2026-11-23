"""Per-occurrence reading gate: patchwriting, stance, qualification, quotation.

The gate runs before rendering and returns inspectable evidence, never a
verdict. Each check is `finding`, `pass`, `declined` or `inconclusive`.
A finding must carry the exact source words and locator that produced it; when
the source cannot support the check, the gate records the reason instead of
silently passing it.

    from reading_gate import analyze
    record = analyze(occurrence, references, source_evidence)

`source_evidence` is a list of {"r_id", "source_key", "locator", "evidence"}.
The batch script fills it from findings or from the extracted source copy.
"""

from __future__ import annotations

import hashlib
import math
import re

from . import script_detect, segmentation

VERSION = "reading-gate-v1"
CATEGORY_A = "A"
CATEGORY_B = "B"
QUALIFIERS = (
    "minimum", "partial", "partly", "only", "some ", "several", "may ",
    "might", "could", "often", "generally", "usually", "tends", "tend to",
    "approximately", "limited", "qualification", "however", "at face value",
    "would seem", "suggests", "arguably", "potentially", "incomplete",
    "anachronistic", "stricter", "more detailed", "not all", "rarely",
    "largely", "mostly", "broadly", "up to", "at least", "worry", "worries",
    "oppose", "fear", "claims", "argues", "contends", "according to",
)
ABSOLUTE = (
    "demonstrat", "prove", "ensure", "standardiz", "effective", "always",
    "never", "all ", "every ", "guarantee", "establish", "shows that",
)
ATTRIBUTION_SUBJECTS = re.compile(
    r"(?i)\b(executives|stations|broadcasters|critics|scholars|analysts|"
    r"officials|regulators|commission|industry|companies|firms|groups)\b")
ATTRIBUTION_VERBS = re.compile(
    r"(?i)\b(oppose|opposed|worry|worries|fear|fears|argue|argues|argued|"
    r"claim|claims|claimed|contend|contends|observe|observes|observed|"
    r"according to|believe|believes|say|says|said|note|notes|noted)\b")
QUOTED = re.compile(r"[“\"]([^”\"]{2,200})[”\"]")
MARKER_YEAR = re.compile(
    r"\(([A-Z][^,()]{1,40}?)\s*,\s*((?:19|20)\d{2})[a-z]?[^)]*\)")
MARKER_YEAR_NO_COMMA = re.compile(
    r"\(([A-Z][^,()]{1,40}?)\s+((?:19|20)\d{2})[a-z]?[^)]*\)")
PAGE = re.compile(r"\bpp?\.\s*(\d{1,5})(?:\s*[-\u2013]\s*(\d{1,5}))?", re.I)
STOP = {
    "that", "this", "with", "from", "their", "which", "have", "been", "into",
    "about", "also", "used", "using", "such", "these", "they", "them", "then",
    "than", "over", "under", "between", "where", "many", "more", "most", "only",
    "some", "after", "before", "during", "while", "however", "therefore",
    "according", "argues", "states", "shows", "notes", "suggests", "paper",
    "source", "recent", "years", "large", "small", "a", "an", "the", "of", "to", "in",
    "on", "by", "as", "at", "is", "it", "be", "or", "and", "for", "are", "but",
    "not", "its", "can", "has", "had", "was", "will", "would", "should", "may",
    "were", "we", "they", "he", "she", "his", "her", "our", "your",
}
WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]{2,}")
UNICODE_WORD = re.compile(r"[^\W\d_][\w'’\-]*", re.UNICODE)


def load_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def normalize(text):
    value = str(text or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def content_words(text):
    language = script_detect.detect(text)["language"]
    if language in ("zh", "ja", "ko"):
        return [item["term"] for item in segmentation.unique_terms(text, language)]
    out = []
    pattern = WORD if language == "latin" else UNICODE_WORD
    for word in pattern.findall(normalize(text)):
        value = word.casefold().strip("'’")
        if value not in STOP:
            out.append(value)
    return out


def _is_cjk(text):
    return script_detect.detect(text)["language"] in ("zh", "ja", "ko")


def _scripts_differ(statement, sources):
    statement_language = script_detect.detect(statement)["language"]
    source_languages = {script_detect.detect(source.get("evidence", ""))["language"]
                        for source in sources}
    return bool(statement_language and source_languages
                and statement_language not in source_languages)


def ngrams(words, size):
    return {tuple(words[index:index + size])
            for index in range(max(0, len(words) - size + 1))}


def longest_common_subsequence(left, right):
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    for item in left:
        current = [0]
        for index, other in enumerate(right, 1):
            current.append(previous[index - 1] + 1 if item == other else
                           max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def strip_marker(statement, marker):
    """Claim text without the citation marker, for search and comparison."""
    value = normalize(statement)
    if marker and marker in value:
        value = normalize(value.replace(marker, " "))
    value = re.sub(r"[（(][^）)]{0,100}?(?:19|20)\d{2}[^）)]*[）)]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _best_sentence(text, claim_words):
    sentences = re.split(r"(?<=[.!?])\s+", normalize(text))
    if not sentences:
        return normalize(text), set()
    claim = set(claim_words)
    scored = []
    for index, sentence in enumerate(sentences):
        words = set(content_words(sentence))
        score = len(claim & words)
        scored.append((score, -index, sentence))
    scored.sort(reverse=True)
    return scored[0][2], set(content_words(scored[0][2]))


def _result(check, state, reason="", **extra):
    return {"check": check, "state": state, "reason": reason, **extra}


def _finding(check, category, issue_type, evidence, source, locator="", **extra):
    return {"check": check, "state": "finding", "category": category,
            "issue_type": issue_type, "evidence": normalize(evidence),
            "r_id": source.get("r_id", ""), "source_key": source.get("source_key", ""),
            "locator": locator or source.get("locator", ""), **extra}


def check_patchwriting(occurrence, sources):
    """B when a paraphrase keeps distinctive words and clause order without quotation marks."""
    statement = strip_marker(occurrence.get("statement", ""), occurrence.get("marker", ""))
    if not statement:
        return _result("patchwriting", "declined", "empty statement")
    if QUOTED.search(occurrence.get("statement", "")):
        return _result("patchwriting", "pass", "quotation owns the wording")
    if _scripts_differ(statement, sources):
        return _result("patchwriting", "inconclusive",
                       "source language differs; wording comparison requires model reading")
    claim = content_words(statement)
    if len(claim) < 6:
        return _result("patchwriting", "declined", "claim shorter than six content words")
    if len(re.findall(r"\d", statement)) > 2:
        return _result("patchwriting", "pass", "data-heavy claim; patchwriting is not a wording finding")
    if not sources:
        return _result("patchwriting", "declined", "no source evidence")
    best = None
    for source in sources:
        sentence = normalize(source.get("evidence", ""))
        source_words = content_words(sentence)
        if len(source_words) < 5:
            continue
        left, right = set(claim), set(source_words)
        shared = left & right
        overlap = len(shared) / len(left)
        bigrams = len(ngrams(claim, 2) & ngrams(source_words, 2))
        trigrams = len(ngrams(claim, 3) & ngrams(source_words, 3))
        lcs = longest_common_subsequence(claim, source_words)
        distinctive = sum(1 for word in shared if len(word) >= 6)
        score = (overlap, bigrams, trigrams, lcs / max(1, len(claim)), distinctive)
        if best is None or score > best[0]:
            best = (score, sentence, source, shared, source_words)
    if best is None:
        return _result("patchwriting", "declined", "no comparable source sentence")
    (overlap, bigrams, trigrams, lcs_ratio, distinctive), sentence, source, shared, _ = best
    strong = (overlap >= 0.42 and (bigrams >= 2 or trigrams >= 1))
    structured = lcs_ratio >= 0.28 and bigrams >= 1 and distinctive >= 3
    if strong or structured:
        return _finding(
            "patchwriting", CATEGORY_B, "close paraphrase retaining source wording and structure",
            sentence, source,
            metrics={"overlap": round(overlap, 2), "shared_bigrams": bigrams,
                     "shared_trigrams": trigrams, "lcs_ratio": round(lcs_ratio, 2),
                     "distinctive_terms": distinctive,
                     "shared_terms": sorted(shared)[:12]})
    if overlap >= 0.32 and lcs_ratio >= 0.22:
        return _result("patchwriting", "inconclusive",
                       "moderate wording overlap; confirm the distinctive structure",
                       overlap=round(overlap, 2), lcs_ratio=round(lcs_ratio, 2))
    return _result("patchwriting", "pass", "no close wording/structure overlap")


def check_qualification(occurrence, sources):
    """B when the source qualifies the claim and the claim states it absolutely."""
    statement = strip_marker(occurrence.get("statement", ""), occurrence.get("marker", ""))
    if not sources:
        return _result("lost_qualification", "declined", "no source evidence")
    if (_scripts_differ(statement, sources) or _is_cjk(statement)
            or any(_is_cjk(source.get("evidence", "")) for source in sources)):
        return _result("lost_qualification", "inconclusive",
                       "source language differs or CJK reading is required; semantic check needs the model")
    claim = normalize(statement).casefold()
    claim_absolute = any(marker in claim for marker in ABSOLUTE)
    claim_qualified = any(marker in claim for marker in QUALIFIERS)
    if not claim_absolute:
        return _result("lost_qualification", "pass", "claim makes no absolute conclusion")
    best = None
    claim_words = set(content_words(statement))
    for source in sources:
        passage = normalize(source.get("evidence", ""))
        hits = [marker.strip() for marker in QUALIFIERS if marker in passage.casefold()]
        if not hits:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", passage):
            sentence_hits = [marker.strip() for marker in QUALIFIERS
                             if marker in sentence.casefold()]
            if not sentence_hits:
                continue
            substantive = [hit for hit in sentence_hits
                           if hit in ("minimum", "partial", "qualification", "incomplete",
                                      "anachronistic", "would seem", "at face value",
                                      "however", "limited", "stricter", "more detailed")]
            overlap = len(claim_words & set(content_words(sentence)))
            score = len(substantive) * 10 + len(sentence_hits) + overlap
            if best is None or score > best[0]:
                best = (score, sentence, source, hits)
    if best is None:
        return _result("lost_qualification", "pass", "source qualifications do not touch the claim")
    _score, sentence, source, hits = best
    if claim_qualified and not any(marker in claim for marker in
                                   ("minimum", "partial", "qualification", "incomplete",
                                    "however", "limited")):
        return _result("lost_qualification", "inconclusive",
                       "claim carries a hedge but not the source's substantive qualification",
                       qualifiers=hits[:6])
    return _finding(
        "lost_qualification", CATEGORY_B, "source qualification omitted",
        sentence, source, qualifiers=hits[:6])


def check_reported_viewpoint(occurrence, sources):
    """B when a stakeholder's reported position is presented as the author's assertion."""
    statement = strip_marker(occurrence.get("statement", ""), occurrence.get("marker", ""))
    if not sources:
        return _result("reported_viewpoint", "declined", "no source evidence")
    if (_scripts_differ(statement, sources) or _is_cjk(statement)
            or any(_is_cjk(source.get("evidence", "")) for source in sources)):
        return _result("reported_viewpoint", "inconclusive",
                       "source language differs or CJK reading is required; semantic check needs the model")
    claim = normalize(statement)
    claim_subjects = {match.casefold() for match in ATTRIBUTION_SUBJECTS.findall(claim)}
    if not claim_subjects:
        return _result("reported_viewpoint", "declined",
                       "claim names no stakeholder whose stance could be checked")
    for source in sources:
        passage = normalize(source.get("evidence", ""))
        subjects = {match.casefold() for match in ATTRIBUTION_SUBJECTS.findall(passage)}
        if not subjects or not ATTRIBUTION_VERBS.search(passage):
            continue
        omitted = sorted(subjects - claim_subjects)
        if not omitted:
            continue
        sentence, _ = _best_sentence(passage, content_words(statement))
        if not any(subject in sentence.casefold() for subject in omitted):
            continue
        return _finding(
            "reported_viewpoint", CATEGORY_B,
            "stakeholder's reported position presented as the author's own",
            sentence, source, omitted_subjects=omitted[:6])
    return _result("reported_viewpoint", "pass", "no omitted stakeholder attribution")


def check_quoted_terms(occurrence, sources):
    """B when a direct quotation changes the source's wording."""
    statement = normalize(occurrence.get("statement", ""))
    quotes = [normalize(match) for match in QUOTED.findall(statement)]
    if not quotes:
        return _result("quoted_term", "pass", "no direct quotation")
    if not sources:
        return _result("quoted_term", "declined", "no source evidence")
    quote_language = script_detect.detect(quotes[0])["language"]
    source_languages = {script_detect.detect(source.get("evidence", ""))["language"]
                        for source in sources}
    if quote_language not in source_languages:
        return _result(
            "quoted_term", "inconclusive",
            "source language differs; a quotation cannot be compared word for word",
            outcome="source_language_differs", quote_language=quote_language,
            source_languages=sorted(source_languages))
    findings, matched = [], 0
    for quote in quotes:
        quote = quote.strip(" ,.;:")
        quote_words = content_words(quote)
        if len(quote_words) < 2:
            continue
        best = None
        for source in sources:
            passage = normalize(source.get("evidence", ""))
            if quote.casefold() in passage.casefold():
                matched += 1
                best = ("matched", passage, source)
                break
            for sentence in re.split(r"(?<=[.!?])\s+", passage):
                words = content_words(sentence)
                for size in (len(quote_words), len(quote_words) + 1):
                    for start in range(max(0, len(words) - size + 1)):
                        candidate_words = words[start:start + size]
                        shared = set(quote_words) & set(candidate_words)
                        if not shared:
                            continue
                        score = len(shared) / len(quote_words)
                        if score >= 0.5 and (best is None or score > best[0]):
                            best = (score, sentence, source)
        if best is None:
            findings.append(None)
            continue
        if best[0] == "matched":
            continue
        findings.append(best)
    valid = [item for item in findings if item]
    if valid:
        best = max(valid, key=lambda item: item[0])
        _score, sentence, source = best
        quote = quotes[0]
        variant = _closest_phrase(quote, sentence)
        return _finding(
            "quoted_term", CATEGORY_B, "quoted term altered",
            sentence, source, quote=quote, source_phrase=variant)
    if matched:
        return _result("quoted_term", "pass", "quotation wording matches the source")
    return _result("quoted_term", "inconclusive",
                   "quotation not found; cannot separate omission from paraphrase")


def _closest_phrase(quote, sentence):
    if _is_cjk(quote):
        return _closest_cjk_phrase(quote, sentence)
    words = [word.strip("'’") for word in UNICODE_WORD.findall(sentence)]
    quoted = [word.casefold() for word in
              re.findall(r"[A-Za-z][A-Za-z'’\-]*", quote)]
    if not quoted:
        return ""
    for index, word in enumerate(words):
        if word.casefold() == quoted[-1]:
            start = max(0, index - len(quoted) + 1)
            candidate = " ".join(words[start:index + 1])
            if candidate.casefold() != quote.casefold():
                return candidate
    return ""


def _quote_verifiable(quote, sources):
    """True when the quoted phrase occurs, or differs by one function word, in a source."""
    clean = normalize(quote).strip(" ,.;:")
    words = content_words(clean)
    if len(words) < 2:
        return False
    for source in sources or []:
        passage = normalize(source.get("evidence", ""))
        if clean.casefold() in passage.casefold():
            return True
        if words[-1] in content_words(passage):
            return True
        for sentence in re.split(r"(?<=[.!?])\s+", passage):
            sentence_words = content_words(sentence)
            for start in range(max(0, len(sentence_words) - len(words))):
                candidate = sentence_words[start:start + len(words)]
                if candidate and candidate[-1] == words[-1] and len(set(words) & set(candidate)) >= 1:
                    if len(set(words) & set(candidate)) / len(words) >= 0.5:
                        return True
    return False


def _closest_cjk_phrase(quote, sentence):
    """Best source substring of similar length for a CJK quotation."""
    target = normalize(quote).strip(" ,.;:，。；：")
    if not target:
        return ""
    source = normalize(sentence)
    size = len(target)
    best = (0.0, "")
    for width in (size, size + 1, size + 2):
        for start in range(0, max(1, len(source) - width + 1)):
            candidate = source[start:start + width].strip()
            if not candidate or candidate == target:
                continue
            target_terms = {item["term"] for item in segmentation.cjk_ngrams(target)}
            candidate_terms = {item["term"] for item in segmentation.cjk_ngrams(candidate)}
            shared = len(target_terms & candidate_terms)
            if shared:
                score = shared / max(1, len(target_terms))
                if score > best[0]:
                    best = (score, candidate)
    return best[1] if best[0] >= 0.5 else ""


def check_citation_form(occurrence, references, sources=None):
    """A/B checks that do not need source text: reference presence, comma, quote locator."""
    findings = []
    marker = normalize(occurrence.get("marker", ""))
    statement = normalize(occurrence.get("statement", ""))
    cited = occurrence.get("cited_r_ids") or []
    refs = references or []
    if not cited:
        author_year = MARKER_YEAR.search(marker) or MARKER_YEAR_NO_COMMA.search(marker)
        surname = author_year.group(1).split()[0].casefold() if author_year else ""
        year = author_year.group(2) if author_year else ""
        present = any(surname and surname in (ref.get("verbatim") or "").casefold()
                      and year in (ref.get("verbatim") or "")
                      for ref in refs)
        if surname and not present:
            findings.append({
                "check": "citation_form", "state": "finding", "category": CATEGORY_B,
                "issue_type": "work cited in the text but absent from the reference list",
                "evidence": marker, "r_id": "", "source_key": "", "locator": ""})
    no_comma = MARKER_YEAR_NO_COMMA.search(marker)
    if no_comma and not MARKER_YEAR.search(marker):
        findings.append({
            "check": "citation_form", "state": "finding", "category": CATEGORY_A,
            "issue_type": "misplaced or missing comma in an in-text citation",
            "evidence": no_comma.group(0), "r_id": "", "source_key": "", "locator": ""})
    quotes = QUOTED.findall(statement)
    if (quotes and not re.search(r"(?i)(?:pp?\.|¶|para(?:graph)?)\s*\d", marker)
            and any(_quote_verifiable(quote, sources) for quote in quotes)):
        findings.append({
            "check": "citation_form", "state": "finding", "category": CATEGORY_A,
            "issue_type": "direct quotation without a page or paragraph locator",
            "evidence": (quotes[0] + " " + marker).strip(),
            "r_id": "", "source_key": "", "locator": ""})
    return findings or [_result("citation_form", "pass", "citation form has no clear defect")]


def check_locator(occurrence, sources):
    """A when the submitted page locator contradicts the observed source page."""
    marker = normalize(occurrence.get("marker", ""))
    match = PAGE.search(marker)
    if not match:
        return _result("locator", "pass", "no page locator to check")
    member = match.group(1)
    observed = []
    for source in sources:
        for found in PAGE.finditer(source.get("locator", "")):
            first = int(found.group(1))
            last = int(found.group(2) or first)
            observed.append((first, last))
    if not observed:
        return _result("locator", "declined", "no observed source page")
    if any(first <= int(member) <= last for first, last in observed):
        return _result("locator", "pass", "locator matches the observed page")
    observed_text = ", ".join(str(first) if first == last else f"{first}-{last}"
                               for first, last in observed)
    return _finding(
        "locator", CATEGORY_A, "stated page locator differs from the observed source page",
        f"submitted {match.group(0).strip()}; observed {observed_text}",
        {}, locator=observed_text)


def analyze(occurrence, references=None, sources=None):
    """Run every reading-gate check for one occurrence."""
    sources = sources or []
    checks = []
    citation = check_citation_form(occurrence, references or [], sources)
    checks.extend(citation)
    checks.append(check_locator(occurrence, sources))
    checks.append(check_quoted_terms(occurrence, sources))
    patch = check_patchwriting(occurrence, sources)
    checks.append(patch)
    statement = strip_marker(occurrence.get("statement", ""), occurrence.get("marker", ""))
    cjk = _is_cjk(statement) or any(_is_cjk(source.get("evidence", ""))
                                    for source in sources)
    if patch["state"] != "finding" or cjk:
        checks.append(check_qualification(occurrence, sources))
    checks.append(check_reported_viewpoint(occurrence, sources))
    findings = [check for check in checks if check.get("state") == "finding"]
    declined = [{"check": check["check"], "reason": check.get("reason", "")}
                for check in checks if check.get("state") == "declined"]
    inconclusive = [{"check": check["check"], "reason": check.get("reason", "")}
                    for check in checks if check.get("state") == "inconclusive"]
    state = ("finding" if findings else "inconclusive" if inconclusive
             else "declined" if declined else "pass")
    return {
        "schema": VERSION,
        "occurrence": occurrence.get("id", ""),
        "marker": occurrence.get("marker", ""),
        "statement_sha256": hashlib.sha256(
            normalize(occurrence.get("statement", "")).encode("utf-8")).hexdigest(),
        "judgment": occurrence.get("judgment", ""),
        "checks": checks,
        "findings": findings,
        "declined": declined,
        "inconclusive": inconclusive,
        "state": state,
    }
