"""Target-aware PII pseudonymization for Kanban worker contexts."""

from __future__ import annotations

import re
import unicodedata

_EMAIL_RE = re.compile(
    r"(?<![\w.+-])([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+)", re.IGNORECASE,
)
_INTERNATIONAL_PHONE_RE = re.compile(r"(?<![\w+])(\+[1-9](?:[\s()./-]*\d){6,14})(?!\w)")
_LABELED_LOCAL_PHONE_RE = re.compile(
    r"(?i)\b(telefon|tel\.?|mobil|mobile|handy|fax)(\s*:?\s*)(0\d(?:[\s()./-]*\d){6,14})(?!\w)"
)
_NAME_WORD = r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüßÀ-ÖØ-öø-ÿ'’-]+"
_LABELED_NAME_RE = re.compile(
    rf"(?i:\b(?:herr|frau|dr\.?|prof\.?|name|kontakt|ansprechpartner(?:in)?|kontaktdaten\s+von)\s*:?\s+)"
    rf"({_NAME_WORD}(?:\s+{_NAME_WORD}){{1,3}})"
)
_OFFER_RECIPIENT_NAME_RE = re.compile(
    rf"(?i:\b(?:angebot|preis)\s+(?:für|fuer)\s+)({_NAME_WORD}(?:\s+{_NAME_WORD}){{1,3}})"
)
_CAPITALIZED_NAME_RE = re.compile(rf"(?<!\w)({_NAME_WORD}\s+{_NAME_WORD})(?!\w)")
_COMPANY_SUFFIXES = {"ag", "eg", "gbr", "gmbh", "kg", "kgaa", "mbh", "ohg", "se", "ug"}
_GENERIC_EMAIL_PARTS = {"admin", "billing", "buero", "contact", "info", "office", "sales", "service"}


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _email_name_candidates(text: str) -> set[str]:
    candidates: set[str] = set()
    locals_: list[str] = []
    for match in _EMAIL_RE.finditer(text):
        local = match.group(1).split("@", 1)[0]
        locals_.append(_fold(local))
        parts = [part for part in re.split(r"[._-]+", local) if len(part) > 1]
        if len(parts) in (2, 3) and not any(_fold(part) in _GENERIC_EMAIL_PARTS for part in parts):
            candidates.add(" ".join(part.capitalize() for part in parts))
    for match in _CAPITALIZED_NAME_RE.finditer(text):
        first, last = match.group(1).split()
        if _fold(last) not in _COMPANY_SUFFIXES and any(
            local == _fold(first)[:1] + _fold(last) for local in locals_
        ):
            candidates.add(match.group(1))
    return candidates


def _explicit_name_candidates(text: str) -> set[str]:
    candidates: set[str] = set()
    for pattern in (_LABELED_NAME_RE, _OFFER_RECIPIENT_NAME_RE):
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            words = value.split()
            if _fold(words[-1].rstrip(".")) in _COMPANY_SUFFIXES:
                continue
            if pattern is _OFFER_RECIPIENT_NAME_RE and any(
                len(word) > 1 and word.isupper() for word in words
            ):
                continue
            candidates.add(value)
    return candidates


def pseudonymize_price_context(text: str) -> str:
    """Replace recognizable contact PII in context supplied to the ``preis`` worker."""
    if not text:
        return text
    names = _explicit_name_candidates(text) | _email_name_candidates(text)
    counters = {"EMAIL": 0, "PHONE": 0, "PERSON": 0}
    mappings: dict[str, dict[str, str]] = {kind: {} for kind in counters}

    def placeholder(kind: str, key: str) -> str:
        if key not in mappings[kind]:
            counters[kind] += 1
            mappings[kind][key] = f"[{kind}_{counters[kind]}]"
        return mappings[kind][key]

    result = _EMAIL_RE.sub(lambda m: placeholder("EMAIL", _fold(m.group(1))), text)
    for name in sorted(names, key=len, reverse=True):
        result = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)",
                        placeholder("PERSON", _fold(name)), result, flags=re.IGNORECASE)
    result = _INTERNATIONAL_PHONE_RE.sub(
        lambda m: placeholder("PHONE", re.sub(r"\D", "", m.group(1))), result
    )
    def replace_local_phone(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(3))
        return f"{match.group(1)}{match.group(2)}{placeholder('PHONE', digits)}"

    return _LABELED_LOCAL_PHONE_RE.sub(replace_local_phone, result)
