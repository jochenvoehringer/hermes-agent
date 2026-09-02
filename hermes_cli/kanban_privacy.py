"""Target-aware PII pseudonymization for Kanban worker contexts."""

from __future__ import annotations

import re
import unicodedata


_EMAIL_RE = re.compile(
    r"(?<![\w.+-])([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+)",
    re.IGNORECASE,
)
_INTERNATIONAL_PHONE_RE = re.compile(
    r"(?<![\w+])(\+[1-9](?:[\s()./-]*\d){6,14})(?!\w)"
)
_LABELED_LOCAL_PHONE_RE = re.compile(
    r"(?i)\b(telefon|tel\.?|mobil|mobile|handy|fax)"
    r"(\s*:?\s*)(0\d(?:[\s()./-]*\d){6,14})(?!\w)"
)
_NAME_WORD = r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüßÀ-ÖØ-öø-ÿ'’-]+"
_LABELED_NAME_RE = re.compile(
    rf"(?i:\b(?:herr|frau|dr\.?|prof\.?|name|kontakt|"
    rf"ansprechpartner(?:in)?|kontaktdaten\s+von)\s*:?\s+)"
    rf"({_NAME_WORD}(?:\s+{_NAME_WORD}){{1,3}})"
)
_OFFER_RECIPIENT_NAME_RE = re.compile(
    rf"(?i:\b(?:angebot|preis)\s+(?:für|fuer)\s+)"
    rf"({_NAME_WORD}(?:\s+{_NAME_WORD}){{1,3}})"
)
_CAPITALIZED_NAME_RE = re.compile(
    rf"(?<!\w)({_NAME_WORD}\s+{_NAME_WORD})(?!\w)"
)
_COMPANY_SUFFIXES = {
    "ag", "eg", "gbr", "gmbh", "kg", "kgaa", "mbh", "ohg", "se", "ug",
}
_GENERIC_EMAIL_PARTS = {
    "admin", "billing", "buero", "contact", "info", "office", "sales", "service",
}


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _email_name_candidates(text: str) -> set[str]:
    candidates: set[str] = set()
    locals_: list[str] = []
    for match in _EMAIL_RE.finditer(text):
        local = match.group(1).split("@", 1)[0]
        locals_.append(_fold(local))
        parts = [part for part in re.split(r"[._-]+", local) if len(part) > 1]
        if (
            len(parts) in (2, 3)
            and not any(_fold(part) in _GENERIC_EMAIL_PARTS for part in parts)
        ):
            candidates.add(" ".join(part.capitalize() for part in parts))

    # Common corporate shape: first initial + surname (czrenner@...). Match
    # it only against a capitalized two-word span already present in the task.
    for match in _CAPITALIZED_NAME_RE.finditer(text):
        full = match.group(1)
        first, last = full.split()
        folded_first = _fold(first)
        folded_last = _fold(last)
        if folded_last in _COMPANY_SUFFIXES:
            continue
        if any(local == folded_first[:1] + folded_last for local in locals_):
            candidates.add(full)
    return candidates


def _explicit_name_candidates(text: str) -> set[str]:
    candidates: set[str] = set()
    for match in _LABELED_NAME_RE.finditer(text):
        value = match.group(1).strip()
        words = value.split()
        if _fold(words[-1].rstrip(".")) not in _COMPANY_SUFFIXES:
            candidates.add(value)
    for match in _OFFER_RECIPIENT_NAME_RE.finditer(text):
        value = match.group(1).strip()
        words = value.split()
        has_organization_token = any(
            len(word) > 1 and word.isupper() for word in words
        )
        if not has_organization_token:
            candidates.add(value)
    return candidates


def pseudonymize_price_context(text: str) -> str:
    """Replace contact PII in text handed to the ``preis`` profile.

    The heuristic intentionally favors a small, explainable rule set. It does
    not block a task when a possible person name cannot be classified.
    """
    if not text:
        return text

    names = _explicit_name_candidates(text) | _email_name_candidates(text)
    counters = {"EMAIL": 0, "PHONE": 0, "PERSON": 0}
    mappings: dict[str, dict[str, str]] = {
        "EMAIL": {}, "PHONE": {}, "PERSON": {},
    }

    def placeholder(kind: str, key: str) -> str:
        known = mappings[kind].get(key)
        if known is not None:
            return known
        counters[kind] += 1
        value = f"[{kind}_{counters[kind]}]"
        mappings[kind][key] = value
        return value

    result = _EMAIL_RE.sub(
        lambda match: placeholder("EMAIL", _fold(match.group(1))),
        text,
    )

    # Replace longer names first so a shorter candidate cannot fragment one.
    for name in sorted(names, key=len, reverse=True):
        result = re.sub(
            rf"(?<!\w){re.escape(name)}(?!\w)",
            placeholder("PERSON", _fold(name)),
            result,
            flags=re.IGNORECASE,
        )

    def replace_international_phone(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(1))
        return placeholder("PHONE", digits)

    result = _INTERNATIONAL_PHONE_RE.sub(replace_international_phone, result)

    def replace_local_phone(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(3))
        return f"{match.group(1)}{match.group(2)}{placeholder('PHONE', digits)}"

    return _LABELED_LOCAL_PHONE_RE.sub(replace_local_phone, result)
