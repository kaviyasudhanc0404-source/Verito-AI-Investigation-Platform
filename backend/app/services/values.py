"""
services/values.py
------------------
Value parsers shared by every analysis stage: identifiers, network identifiers,
amounts, dates/timestamps and sentences. Nothing here knows about a particular
case or domain.

Timestamps keep what the source said:
  Timestamp.local      wall-clock time exactly as written
  Timestamp.offset     UTC offset when the source states one ("Z", "+05:30", "UTC"),
                       otherwise None ("timezone not specified")
  Timestamp.approximate  "March 2026", "around 5 March 2026" …
Comparisons across sources use Timestamp.sort_key(case_offset), which normalises to
UTC when an offset is known and otherwise assumes the case's dominant offset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# ── Identifiers ───────────────────────────────────────────────────────────────

_ID = r"[A-Z][A-Z0-9]{1,9}(?:-[A-Z0-9]{2,}){1,4}"
_ID_RE = re.compile(rf"\b{_ID}\b")
# "PO-2026-0342, 0349 and 0355" → also PO-2026-0349 and PO-2026-0355
_ID_LIST_RE = re.compile(rf"\b({_ID})((?:\s*(?:,|and|&)\s*\d{{3,}}\b)+)")
_IPV4_RE = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")

# Common identifier prefixes → readable type. Unknown prefixes are "reference".
_ID_TYPES = {
    "PO": "purchase order", "INV": "invoice", "TXN": "transaction", "TRX": "transaction",
    "VEN": "vendor", "EMP": "employee", "CALL": "call", "AUD": "audit event", "CHAT": "chat message",
    "CC": "cost centre", "POL": "policy", "SES": "session", "WS": "witness statement",
    "INC": "incident", "TKT": "ticket", "CASE": "case", "ACC": "account", "CUST": "customer",
    "CLM": "claim", "ORD": "order", "GRN": "goods receipt", "CON": "contract", "ALERT": "alert",
}


def find_ids(text: str) -> list[str]:
    """Return unique identifiers (with at least one digit) in order of appearance."""
    found: list[str] = []
    for match in _ID_LIST_RE.finditer(text or ""):
        prefix, last = match.group(1).rsplit("-", 1)
        for number in re.findall(r"\d{3,}", match.group(2)):
            if len(number) == len(last):
                found.append(f"{prefix}-{number}")
    found = [m.group(0) for m in _ID_RE.finditer(text or "")] + found
    return list(dict.fromkeys(item for item in found if any(ch.isdigit() for ch in item)))


def find_ips(text: str) -> list[str]:
    return list(dict.fromkeys(_IPV4_RE.findall(text or "")))


def id_shape(identifier: str) -> str:
    """'INV-2026-0101' → 'INV-9999-9999': identifiers of the same kind of document share a shape."""
    return re.sub(r"\d", "9", identifier)


def id_type(identifier: str) -> str:
    """Classify an identifier by its segments, e.g. 'INV-KBS-0091' → 'invoice'."""
    if _IPV4_RE.fullmatch(identifier):
        return "IP address"
    for segment in identifier.upper().split("-"):
        if segment in _ID_TYPES:
            return _ID_TYPES[segment]
    return "reference"


# ── Amounts ───────────────────────────────────────────────────────────────────

_CURRENCY = r"(?:₹|\$|€|£|\b(?:Rs\.?|INR|USD|EUR|GBP))"     # \b: "appears 2" is not Rs 2
_AMOUNT_TEXT_RE = re.compile(rf"{_CURRENCY}\s?(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_PLAIN_NUMBER_RE = re.compile(r"^-?\d[\d,]*(?:\.\d+)?$")


def parse_amount(value: str) -> float | None:
    """Parse '₹2,34,230', 'Rs.2,00,000', '$1,200.50' or '198500' into a number."""
    text = (value or "").strip()
    match = _AMOUNT_TEXT_RE.search(text)
    number = match.group(1) if match else text
    if not _PLAIN_NUMBER_RE.match(number):
        return None
    return float(number.replace(",", ""))


def find_amounts(text: str) -> list[float]:
    return [float(m.group(1).replace(",", "")) for m in _AMOUNT_TEXT_RE.finditer(text or "")]


def detect_currency(text: str) -> str:
    """The currency symbol used most in the text (₹ when none is found)."""
    counts = {
        "₹": len(re.findall(r"₹|\bRs\.?|\bINR\b", text)),
        "$": len(re.findall(r"\$|\bUSD\b", text)),
        "€": len(re.findall(r"€|\bEUR\b", text)),
        "£": len(re.findall(r"£|\bGBP\b", text)),
    }
    symbol, count = max(counts.items(), key=lambda item: item[1])
    return symbol if count else "₹"


def format_amount(amount: float, symbol: str = "₹") -> str:
    """Format with Indian digit grouping for ₹ (1,98,500), Western otherwise."""
    if symbol != "₹":
        whole = f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"
        return f"{symbol}{whole}"
    integer, _, decimals = f"{amount:.2f}".partition(".")
    head, tail = integer[:-3], integer[-3:]
    groups = re.findall(r"\d{1,2}(?=(?:\d{2})*$)", head) if head else []
    text = ",".join([*groups, tail]) if groups else tail
    return f"₹{text}" + (f".{decimals}" if decimals != "00" else "")


# ── Timestamps ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Timestamp:
    local: datetime                    # naive wall-clock time as written in the source
    has_time: bool
    offset: timedelta | None = None    # None = the source does not state a timezone
    approximate: bool = False
    raw: str = ""
    month_only: bool = False           # "March 2026" — the day is unknown

    def sort_key(self, case_offset: timedelta | None = None) -> datetime:
        """Naive UTC time for ordering; unspecified zones assume the case's dominant offset."""
        offset = self.offset if self.offset is not None else case_offset
        return self.local - offset if offset is not None else self.local

    @property
    def zone(self) -> str:
        if self.offset is None:
            return ""
        if not self.offset:
            return "UTC"
        sign = "+" if self.offset > timedelta(0) else "-"
        minutes = int(abs(self.offset).total_seconds() // 60)
        return f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"

    @property
    def day_label(self) -> str:
        """'07 Mar 2026', '~07 Mar 2026' (approximate) or '~Mar 2026' (month only)."""
        if self.month_only:
            return f"~{self.local:%b %Y}"
        return ("~" if self.approximate else "") + self.local.strftime("%d %b %Y")

    def display(self) -> str:
        """'07 Mar 2026', '07 Mar 2026, 14:26 UTC+05:30' or '~Mar 2026'."""
        text = self.day_label
        if self.has_time:
            text += f", {self.local:%H:%M}" + (f" {self.zone}" if self.zone else "")
        return text


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_ZONE = r"(?:\s?(?P<z>Z|UTC|GMT|[+-](?:[01]\d|2[0-3]):?[0-5]\d)(?![\w:]))?"
_TIME = rf"(?:[ T,]+(?P<h>[01]?\d|2[0-3]):(?P<mi>[0-5]\d)(?::(?P<s>[0-5]\d)(?:\.\d+)?)?{_ZONE})?"
_DATE_PATTERNS = [
    re.compile(rf"\b(?P<y>(?:19|20)\d{{2}})-(?P<m>0?[1-9]|1[0-2])-(?P<d>0?[1-9]|[12]\d|3[01]){_TIME}"),
    re.compile(rf"\b(?P<d>0?[1-9]|[12]\d|3[01])[-\s](?P<mon>{_MONTH})[-\s,]+(?P<y>(?:19|20)?\d{{2}})\b{_TIME}", re.IGNORECASE),
    re.compile(rf"\b(?P<mon>{_MONTH})\s+(?P<d>0?[1-9]|[12]\d|3[01]),?\s+(?P<y>(?:19|20)\d{{2}})\b{_TIME}", re.IGNORECASE),
    re.compile(rf"\b(?P<d>0?[1-9]|[12]\d|3[01])/(?P<m>0?[1-9]|1[0-2])/(?P<y>(?:19|20)\d{{2}})\b{_TIME}"),
    # Month and year only — always approximate
    re.compile(rf"\b(?P<mon>{_MONTH})\s+(?P<y>(?:19|20)\d{{2}})\b", re.IGNORECASE),
]
_APPROXIMATE_BEFORE = re.compile(r"\b(?:around|about|approximately|approx\.?|circa|c\.|on or about|early|mid|late|end of|beginning of)\s*$", re.IGNORECASE)
_CLOCK_RE = re.compile(rf"^(?P<h>[01]?\d|2[0-3]):(?P<mi>[0-5]\d)(?::(?P<s>[0-5]\d))?{_ZONE}$")


@dataclass(frozen=True)
class DateMention:
    text: str
    timestamp: Timestamp
    start: int = 0

    @property
    def when(self) -> datetime:
        return self.timestamp.local

    @property
    def has_time(self) -> bool:
        return self.timestamp.has_time


def find_dates(text: str) -> list[DateMention]:
    """Find explicit calendar dates (optionally with time and zone) in free text."""
    text = text or ""
    mentions: list[DateMention] = []
    taken: list[range] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            if any(match.start() in span or match.end() - 1 in span for span in taken):
                continue
            approximate = "d" not in match.groupdict() or bool(_APPROXIMATE_BEFORE.search(text[:match.start()]))
            stamp = _from_match(match, approximate)
            if stamp:
                mentions.append(DateMention(match.group(0).strip(" ,"), stamp, match.start()))
                taken.append(range(match.start(), match.end()))
    return sorted(mentions, key=lambda m: m.start)


def parse_timestamp(value: str) -> Timestamp | None:
    """Parse one field value (ISO 8601 or any find_dates format)."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        offset = parsed.utcoffset()
        return Timestamp(parsed.replace(tzinfo=None), len(text) > 10, offset, raw=text)
    except ValueError:
        pass
    mentions = find_dates(text)
    return mentions[0].timestamp if mentions else None


def with_clock(stamp: Timestamp, clock: str) -> Timestamp:
    """Join a date with a separate 'HH:MM[:SS][ zone]' field value."""
    match = _CLOCK_RE.match((clock or "").strip())
    if stamp.has_time or not match:
        return stamp
    local = stamp.local.replace(hour=int(match["h"]), minute=int(match["mi"]), second=int(match["s"] or 0))
    offset = _offset(match["z"]) if match["z"] else stamp.offset
    return Timestamp(local, True, offset, stamp.approximate, f"{stamp.raw} {clock}".strip())


def dominant_offset(stamps: list[Timestamp]) -> timedelta | None:
    """The most common stated UTC offset among timestamps (None if none states one)."""
    offsets = [s.offset for s in stamps if s.offset is not None]
    return max(set(offsets), key=offsets.count) if offsets else None


def _from_match(match: re.Match, approximate: bool) -> Timestamp | None:
    groups = match.groupdict()
    month = int(groups["m"]) if groups.get("m") else _MONTHS.get((groups.get("mon") or "")[:3].lower())
    year = int(groups["y"])
    year += 2000 if year < 100 else 0
    try:
        local = datetime(year, month or 0, int(groups.get("d") or 1),
                         int(groups.get("h") or 0), int(groups.get("mi") or 0), int(groups.get("s") or 0))
    except (TypeError, ValueError):
        return None
    zone = groups.get("z")
    return Timestamp(local, groups.get("h") is not None, _offset(zone) if zone else None, approximate,
                     match.group(0).strip(" ,"), month_only="d" not in groups)


def _offset(zone: str) -> timedelta:
    if zone.upper() in {"Z", "UTC", "GMT"}:
        return timedelta(0)
    sign = -1 if zone[0] == "-" else 1
    digits = zone[1:].replace(":", "")
    return sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))


# ── Sentences ─────────────────────────────────────────────────────────────────

_SENTENCE_BREAK = re.compile(r"(?<!\bMr\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bMrs\.)(?<!\bNo\.)(?<!\bSt\.)(?<=[.!?])\s+(?=[\"'“A-Z0-9])")
_STRUCTURAL_LINE = re.compile(r"^(?:\[|\||#|Columns:|Total rows:|Total records:)")


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences without breaking after titles such as 'Mr.'."""
    return [s.strip() for s in _SENTENCE_BREAK.split(" ".join((text or "").split())) if s.strip()]


def prose_sentences(text: str) -> list[str]:
    """Sentences from the narrative lines of a document (skips headings, tables and record lines)."""
    return [
        sentence.strip(" -")
        for line in (text or "").split("\n")
        if line.strip() and not _STRUCTURAL_LINE.match(line.strip())
        for sentence in split_sentences(line)
    ]


def normalise_space(text: str) -> str:
    return " ".join((text or "").split())
