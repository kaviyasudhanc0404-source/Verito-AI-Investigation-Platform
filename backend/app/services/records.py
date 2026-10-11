"""
services/records.py
-------------------
Structured records extracted from cleaned evidence text.

Record sources:
  • record lines written by the parsers — "[Row 3] PO Number: PO-1 | Date: …"
    (labels Row / Record / Message / Event / Email; an Email line also owns its body)
  • Markdown tables inside documents (Docling output), including two-column
    "label | value" tables such as statement headers

Every record keeps its provenance (evidence ID, file, sheet/section, page, row,
format, extraction method) and understands its own fields generically:

  record.role("actor")     who performed the action (User, Employee ID, From, Caller …)
  record.role("action")    what was done (Action, Event Type, Operation …)
  record.dates()           every dated field with its type — created / approved /
                           executed / settled / updated / effective / sent / received / event
  record.when              the record's main timestamp (timezone kept when stated)

`kind` names well-known record shapes (purchase_order, invoice, payment, call,
audit_event, message, email, employee, vendor, bank_detail, vendor_event,
statement); anything else is "generic" and is still analysed through its roles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from app.services.values import Timestamp, find_ids, find_ips, id_type, parse_timestamp, with_clock

# ── Field roles ───────────────────────────────────────────────────────────────
# Compacted field names (lowercase alphanumerics) per role: exact names first,
# then a pattern for unseen column names.

_ROLE_NAMES: dict[str, tuple[set[str], str]] = {
    "actor": ({"user", "username", "userid", "useraccount", "account", "accountname", "login", "actor",
               "performedby", "employeeid", "employeename", "employee", "operator", "initiatedby", "createdby",
               "updatedby", "updatedbyemployeeid", "modifiedby", "calleremployeeid", "callername", "caller",
               "from", "sender", "sendername"}, r"(?:user|actor|operator|performedby|initiatedby)"),
    "counterparty": ({"to", "recipient", "receiver", "receivername", "vendor", "vendorid", "vendorname", "payee",
                      "beneficiary", "customer", "supplier", "destinationvendorid", "destinationname"},
                     r"(?:recipient|payee|beneficiary|counterparty|supplier)"),
    "action": ({"action", "event", "eventtype", "activity", "operation", "eventname", "command"},
               r"(?:action|activity|operation|eventtype|eventname)$"),
    "outcome": ({"status", "result", "outcome", "loginstatus", "eventstatus"}, r"(?:status|result|outcome)$"),
    "target": ({"host", "hostname", "device", "devicename", "computer", "workstation", "resource", "target",
                "object", "file", "filename", "filepath", "path", "url", "recordid", "application", "system",
                "server"}, r"(?:host|device|resource|target|filepath|url|server)"),
    "ip": ({"ip", "ipaddress", "sourceip", "srcip", "clientip", "remoteip", "ipreference", "destinationip"},
           r"(?:^ip|ipaddress$|ip$)"),
    "amount": ({"totalamount", "total", "amount", "invoiceamount", "totalpovalue", "value", "netamount",
                "grossamount"}, r"(?:amount|totalvalue)$"),
    "location": ({"location", "address", "site", "city", "country", "branch"}, r"(?:location|address|site)$"),
    "description": ({"description", "item", "details", "subject", "summary", "message", "notes"},
                    r"(?:description|details|summary)$"),
}
_ROLE_PATTERNS = {role: re.compile(pattern) for role, (_, pattern) in _ROLE_NAMES.items()}

# Date field → date type (first match wins).
_DATE_TYPES = (
    ("approved", re.compile(r"approv|sanction|authori[sz]")),
    ("settled", re.compile(r"paid|settle|cleared|valuedate")),
    ("executed", re.compile(r"transaction|payment|executed|released|posted")),
    ("updated", re.compile(r"updat|modif|changed")),
    ("effective", re.compile(r"effective|valid")),
    ("received", re.compile(r"receiv|deliver")),
    ("sent", re.compile(r"^sent")),
    ("created", re.compile(r"creat|raised|issued|^podate|orderdate|invoicedate|documentdate|registr|empanel|"
                           r"opened|submitted|joining|statement")),
)
_DATE_FIELD = re.compile(r"date|time|stamp|when|^(?:paid|sent|created|updated|approved|received|issued|"
                         r"modified|posted|occurred|logged)(?:on|at)$")
# Main-date preference when a record has several dated fields.
_MAIN_DATE_ORDER = ("event", "created", "executed", "sent", "approved", "settled", "received", "effective", "updated")
# Lifecycle order used for chronology checks (a later stage must not precede an earlier one).
LIFECYCLE = {"created": 0, "approved": 1, "executed": 2, "settled": 3}

_EXTRACTION_METHOD = {
    "Row": "spreadsheet / CSV row", "Record": "data record", "Message": "chat message",
    "Event": "log event", "Email": "email", "Table": "document table",
}

# Record kind → (singular, plural) for plain-language text.
KIND_NAMES = {
    "purchase_order": ("purchase order", "purchase orders"), "invoice": ("invoice", "invoices"),
    "payment": ("bank payment", "bank payments"), "call": ("call record", "call records"),
    "audit_event": ("activity log entry", "activity log entries"), "message": ("chat message", "chat messages"),
    "email": ("email", "emails"), "employee": ("employee record", "employee records"),
    "vendor": ("vendor record", "vendor records"), "bank_detail": ("vendor bank record", "vendor bank records"),
    "vendor_event": ("vendor change log entry", "vendor change log entries"),
    "statement": ("witness statement", "witness statements"), "generic": ("record", "records"),
}


def kind_name(kind: str, count: int = 1) -> str:
    singular, plural = KIND_NAMES.get(kind, KIND_NAMES["generic"])
    return singular if count == 1 else plural


_KIND_BY_ID_TYPE = {
    "purchase order": "purchase_order", "invoice": "invoice", "transaction": "payment",
    "call": "call", "audit event": "audit_event", "chat message": "message",
}


def compact(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def field_role(compact_key: str) -> str:
    """The role of a compacted field name, or '' when it has none."""
    for role, (names, _) in _ROLE_NAMES.items():
        if compact_key in names:
            return role
    for role, pattern in _ROLE_PATTERNS.items():
        if pattern.search(compact_key):
            return role
    return ""


def is_date_field(compact_key: str) -> bool:
    return bool(_DATE_FIELD.search(compact_key))


def date_type(compact_key: str) -> str:
    return next((name for name, pattern in _DATE_TYPES if pattern.search(compact_key)), "event")


@dataclass
class Record:
    evidence_id: str
    filename: str
    source: str
    label: str
    index: int
    fields: dict[str, str]
    body: str = ""
    page: int | None = None
    kind: str = field(init=False)

    def __post_init__(self) -> None:
        self._compact = {compact(key): value for key, value in self.fields.items()}
        self.kind = self._infer_kind()

    # ── Field access ──────────────────────────────────────────────────────────

    def value(self, *keys: str) -> str:
        """First non-empty value whose compacted field name is one of `keys`."""
        for key in keys:
            if self._compact.get(key):
                return self._compact[key]
        return ""

    def has(self, *keys: str) -> bool:
        return any(key in self._compact for key in keys)

    @property
    def compact_fields(self) -> dict[str, str]:
        return self._compact

    def role(self, role: str) -> str:
        """First non-empty value of a field with this role (exact names before patterns)."""
        exact, _ = _ROLE_NAMES[role]
        for key, value in self._compact.items():
            if key in exact and value:
                return value
        return next((value for key, value in self._compact.items() if value and field_role(key) == role), "")

    @cached_property
    def roles(self) -> dict[str, str]:
        return {role: value for role in _ROLE_NAMES if (value := self.role(role))}

    @cached_property
    def primary_id(self) -> str:
        """The record's own identifier: first ID-valued field named …id/number/no."""
        for key, value in self._compact.items():
            ids = find_ids(value)
            if len(ids) == 1 and ids[0] == value.strip() and re.search(r"(id|number|no|num|ref)$", key):
                return value.strip()
        return ""

    def identifiers(self) -> set[str]:
        """Every business identifier and IP address in the record's values."""
        text = self.text()
        return set(find_ids(text)) | set(find_ips(text))

    # ── Dates ─────────────────────────────────────────────────────────────────

    def dates(self) -> list[tuple[str, str, Timestamp]]:
        """(field name, date type, timestamp) for every dated field, joining a separate Time field."""
        clock = self._compact.get("time", "")
        found = []
        for key, value in self.fields.items():
            ckey = compact(key)
            stamp = parse_timestamp(value) if is_date_field(ckey) else None
            if stamp:                     # a bare clock ("14:26") does not parse and is joined below
                found.append((key, date_type(ckey), with_clock(stamp, clock) if clock else stamp))
        return found

    def date_of(self, kind: str) -> Timestamp | None:
        return next((stamp for _, dtype, stamp in self.dates() if dtype == kind), None)

    @cached_property
    def when(self) -> Timestamp | None:
        """The record's main timestamp."""
        dates = self.dates()
        for preferred in _MAIN_DATE_ORDER:
            for _, dtype, stamp in dates:
                if dtype == preferred:
                    return stamp
        return None

    # ── Provenance ────────────────────────────────────────────────────────────

    @property
    def ref(self) -> str:
        """Stable key of this record within the case."""
        return f"{self.evidence_id}:{self.source}:{self.label}{self.index}"

    @property
    def location(self) -> str:
        """Human-readable position used in citations, e.g. 'Purchase Orders, row 2'."""
        noun = {"Row": "row", "Record": "record", "Message": "message", "Email": "email", "Table": "table row",
                "Event": "event"}
        parts = [self.source] if self.source else []
        if self.page:
            parts.append(f"page {self.page}")
        parts.append(f"{noun.get(self.label, self.label.lower())} {self.index}")
        return ", ".join(parts)

    @property
    def citation(self) -> str:
        return f"{self.evidence_id} · {self.location}"

    @property
    def source_format(self) -> str:
        return Path(self.filename).suffix.lstrip(".").lower()

    @property
    def method(self) -> str:
        return _EXTRACTION_METHOD.get(self.label, "structured record")

    def text(self) -> str:
        return " | ".join(f"{key}: {value}" for key, value in self.fields.items())

    def excerpt(self, limit: int = 240) -> dict:
        text = self.text()
        return {"ref": self.evidence_id, "location": self.location,
                "text": text if len(text) <= limit else text[: limit - 1].rstrip() + "…"}

    # ── Kind ──────────────────────────────────────────────────────────────────

    def _infer_kind(self) -> str:
        if self.label == "Email":
            return "email"
        if self.label == "Message":
            return "message"
        if self.has("accountnumber", "ifsc", "bankname"):
            return "bank_detail"
        if self.has("eventtype") and self.has("vendorid"):
            return "vendor_event"
        if self.has("witness", "statementdate"):
            return "statement"
        own_type = id_type(self.primary_id) if self.primary_id else ""
        if own_type in _KIND_BY_ID_TYPE:
            return _KIND_BY_ID_TYPE[own_type]
        if self.role("action") and self.role("actor") and self.when:
            return "audit_event"
        if own_type == "employee" and self.has("fullname", "employeename", "name"):
            return "employee"
        if own_type == "vendor" and self.has("vendorname"):
            return "vendor"
        return "generic"


# ── Extraction ────────────────────────────────────────────────────────────────

_RECORD_LINE = re.compile(r"^\[(?P<label>Row|Record|Message|Event|Email) (?P<index>\d+)\]\s*(?P<body>.*)$")
_SHEET_LINE = re.compile(r"^\[Sheet: (?P<name>.+)\]$")
_PAGE_LINE = re.compile(r"^\[Page (?P<page>\d+)\]$")
_HEADING = re.compile(r"^#{1,6}\s+(?P<name>.+)$")
_TABLE_LINE = re.compile(r"^\|.*\|$")
_TABLE_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")


def extract_records(evidence_id: str, filename: str, text: str) -> list[Record]:
    """Extract every structured record from one evidence item's cleaned text."""
    records: list[Record] = []
    lines = (text or "").split("\n")
    source, page, table_count = "", None, 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        sheet, heading, page_line = _SHEET_LINE.match(line), _HEADING.match(line), _PAGE_LINE.match(line)
        record = _RECORD_LINE.match(line)
        if page_line:
            page = int(page_line["page"])
        elif sheet or heading:
            source = (sheet or heading).group("name").strip()
        elif record:
            body_lines: list[str] = []
            if record["label"] == "Email":
                while i + 1 < len(lines) and not _RECORD_LINE.match(lines[i + 1].strip()):
                    i += 1
                    body_lines.append(lines[i].strip())
            records.append(Record(evidence_id, filename, source, record["label"], int(record["index"]),
                                  _fields(record["body"]), "\n".join(body_lines).strip(), page))
        elif _TABLE_LINE.match(line):
            j = i
            while j < len(lines) and _TABLE_LINE.match(lines[j].strip()):
                j += 1
            for fields in _table_rows([row.strip() for row in lines[i:j]]):
                table_count += 1
                records.append(Record(evidence_id, filename, source, "Table", table_count, fields, page=page))
            i = j
            continue
        i += 1
    return records


def _fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in body.split(" | "):
        key, sep, value = part.partition(":")
        if sep and key.strip() and value.strip():
            fields[key.strip()] = value.strip()
    return fields


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip("|").split("|")]


def _table_rows(rows: list[str]) -> list[dict[str, str]]:
    has_header = len(rows) > 1 and bool(_TABLE_SEPARATOR.match(rows[1]))
    body = [_cells(row) for row in (rows[2:] if has_header else rows) if not _TABLE_SEPARATOR.match(row)]
    header = _cells(rows[0]) if has_header else []
    all_rows = ([header] if header else []) + body
    if all_rows and all(len(r) == 2 for r in all_rows) and all(0 < len(r[0]) <= 30 for r in all_rows):
        # Vertical "label | value" table → one record
        return [{label.rstrip(":"): value for label, value in all_rows if label and value}]
    if not header:
        return []
    return [{key: value for key, value in zip(header, row) if key and value} for row in body if any(row)]
