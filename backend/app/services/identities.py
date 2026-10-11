"""
services/identities.py
----------------------
Identity registry: one entry per real person, organisation or user account, with
every way the evidence refers to them (full name, initials, surname with title,
employee/vendor ID, email, phone, acronym, login name).

    registry.resolve("A. Kumaras wamy (0587)")   → the person with employee number 0587
    registry.resolve("j.doe@corp.example")       → the account "j.doe" seen in a login log
    registry.mentions(text)                      → {entity keys mentioned}

Built from master-data records (employee/vendor sheets), person-valued and
actor fields in any record, and names found in prose.

Aliasing is cautious. Matches by ID, email or full name are certain; matches by
surname only, organisation-name prefix, acronym or email domain are recorded in
`uncertain_aliases` so the report can say the identity needs confirming.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.records import Record, compact, field_role
from app.services.values import find_ids, find_ips, id_type

_TITLES = re.compile(r"\b(?:mr|mrs|ms|miss|dr|prof|shri|smt)\.?\s+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PAREN_NUMBER = re.compile(r"\((\d{3,})\)")
# Generic words that mark a name as an organisation rather than a person.
_ORG_SUFFIX = re.compile(
    r"\b(?:ltd|limited|llp|llc|inc|corp|corporation|company|co|plc|gmbh|pvt|private|group|holdings|bank|"
    r"services|industries|enterprises|solutions|systems|technologies|traders|trading|associates|partners|"
    r"consulting|international|agency|foundation|trust|university|hospital|ministry|department|supplies|"
    r"logistics|constructions|builders|electricals|hardware|distributors|packaging|exports|imports)\b",
    re.IGNORECASE,
)
_NAME_WORD = re.compile(r"^[A-Z][a-z]+$|^[A-Z]\.?$")
_ACCOUNT = re.compile(r"^[A-Za-z][\w.\\-]{1,63}$")

# Compacted field names whose values name a person.
PERSON_FIELDS = {
    "fullname", "employeename", "name", "callername", "requestedby", "approvedby",
    "from", "to", "cc", "witness", "recordedby", "preparedby", "reviewedby",
    "contactperson", "accountholdername", "sendername", "recipientname",
}


def normalise_name(text: str) -> str:
    text = _TITLES.sub("", re.sub(r"\(.*?\)", " ", text or ""))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _squash(text: str) -> str:
    return normalise_name(text).replace(" ", "")


def looks_like_person_name(text: str) -> bool:
    words = (text or "").replace(".", ". ").split()
    return 2 <= len(words) <= 4 and all(_NAME_WORD.match(w) for w in words) and not _ORG_SUFFIX.search(text)


@dataclass(eq=False)
class Entity:
    key: str
    kind: str                      # "person" | "organisation" | "account"
    name: str
    title: str = ""                # designation / vendor type
    department: str = ""
    organisation: str = ""         # employer or represented company
    ids: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)

    @property
    def acronym(self) -> str:
        words = [w for w in re.findall(r"[A-Za-z]+", self.name) if w.lower() not in {"and", "of", "the"}]
        return "".join(w[0] for w in words).upper() if self.kind == "organisation" and len(words) >= 3 else ""


class IdentityRegistry:
    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.uncertain_aliases: dict[str, set[str]] = {}
        self._by_id: dict[str, str] = {}
        self._by_email: dict[str, str] = {}
        self._by_name: dict[str, str] = {}       # squashed full/initial forms
        self._by_number: dict[str, set[str]] = {}
        self._patterns: dict[str, re.Pattern] = {}

    # ── Building ──────────────────────────────────────────────────────────────

    def add_records(self, records: list[Record]) -> None:
        for record in records:
            if record.kind == "employee":
                self._add_employee(record)
            elif record.kind in {"vendor", "bank_detail"}:
                self._add_vendor(record)
        for record in records:
            for key, value in record.fields.items():
                ckey = compact(key)
                if ckey in PERSON_FIELDS:
                    self.add_person_mention(value)
                elif field_role(ckey) == "actor":
                    self.add_person_mention(value) or self._add_account(value)

    def add_person_mention(self, value: str, organisation: str = "") -> Entity | None:
        """Register a person named in a field or prose, unless already known."""
        existing = self.resolve(value, record_uncertain=False)
        if existing:
            return existing
        ids = [i for i in find_ids(value) if id_type(i) == "employee"]
        name = _TITLES.sub("", re.sub(r"\(.*?\)|<.*?>", "", value).split(",")[0].strip())
        if not looks_like_person_name(name):
            return None
        return self._merge("person", name, ids=ids, organisation=organisation)

    def _add_account(self, value: str) -> Entity | None:
        """Register a login / user account name that is not a known person (e.g. 'j.doe')."""
        value = value.strip()
        if not _ACCOUNT.match(value) or find_ids(value) or find_ips(value) or self.resolve(value, record_uncertain=False):
            return None
        return self._merge("account", value)

    def _add_employee(self, record: Record) -> None:
        name = record.value("fullname", "employeename", "name")
        if not name:
            return
        self._merge(
            "person", name,
            ids=[record.primary_id] if record.primary_id else [],
            emails=[record.value("workemail", "email")],
            phones=[record.value("workphone", "phone")],
            title=record.value("designation", "role"),
            department=record.value("department"),
        )

    def _add_vendor(self, record: Record) -> None:
        name = record.value("vendorname", "name")
        if not name:
            return
        vendor = self._merge(
            "organisation", name,
            ids=[record.value("vendorid")],
            emails=[record.value("email")],
            phones=[record.value("phone")],
            title=record.value("vendortype"),
        )
        contact = record.value("contactperson")
        if contact:
            self.add_person_mention(contact, organisation=vendor.name)

    def _merge(self, kind: str, name: str, *, ids=(), emails=(), phones=(), **attrs: str) -> Entity:
        ids = [i for i in ids if i]
        entity = next((self.entities[self._by_id[i]] for i in ids if i in self._by_id), None) \
            or self.entities.get(self._by_name.get(_squash(name), ""))
        if entity is None:
            key = ids[0] if ids else f"{kind[:3].upper()}-{len(self.entities) + 1:03d}"
            entity = Entity(key=key, kind=kind, name=" ".join(name.split()))
            self.entities[key] = entity
        for attr, value in attrs.items():
            if value and not getattr(entity, attr):
                setattr(entity, attr, value)
        entity.ids.update(ids)
        entity.emails.update(e.lower() for e in emails if e)
        entity.phones.update(p for p in phones if p)
        self._index(entity)
        return entity

    def _index(self, entity: Entity) -> None:
        for identifier in entity.ids:
            self._by_id[identifier] = entity.key
            number = identifier.rsplit("-", 1)[-1]
            if number.isdigit():
                self._by_number.setdefault(number, set()).add(entity.key)
        for email in entity.emails:
            self._by_email[email] = entity.key
        for form in self._name_forms(entity):
            self._by_name.setdefault(form, entity.key)
        self._patterns.pop(entity.key, None)

    def _name_forms(self, entity: Entity) -> set[str]:
        words = normalise_name(entity.name).split()
        forms = {"".join(words)}
        if entity.kind == "person" and len(words) >= 2:
            forms.add(words[0][0] + words[-1])            # a.kumaraswamy
            forms.add("".join(reversed(words)))
        if entity.acronym:
            forms.add(entity.acronym.lower())
        return forms

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, key: str) -> Entity | None:
        return self.entities.get(key)

    def resolve(self, value: str, record_uncertain: bool = True) -> Entity | None:
        """Return the entity a field value refers to, or None."""
        entity, certain = self._resolve(value)
        if entity and not certain and record_uncertain:
            self.uncertain_aliases.setdefault(entity.key, set()).add(" ".join(value.split())[:80])
        return entity

    def _resolve(self, value: str) -> tuple[Entity | None, bool]:
        if not value:
            return None, True
        for identifier in find_ids(value):
            if identifier in self._by_id:
                return self.entities[self._by_id[identifier]], True
        for email in _EMAIL_RE.findall(value):
            email = email.lower()
            if email in self._by_email:
                return self.entities[self._by_email[email]], True
            local, _, domain = email.partition("@")
            found = self.entities.get(self._by_name.get(_squash(local), ""))
            if found:
                return found, found.kind == "account" or "." in local or "_" in local
            found = self._by_org_prefix(domain.split(".")[0])
            if found:
                return found, False
        for number in _PAREN_NUMBER.findall(value):
            keys = self._by_number.get(number, set())
            if len(keys) == 1:
                return self.entities[next(iter(keys))], True
        found = self._by_name.get(_squash(value))
        if found:
            return self.entities[found], True
        for token in re.findall(r"\b[A-Z]{3,}\b", value):
            found = self._by_name.get(token.lower())
            if found and self.entities[found].kind == "organisation":
                return self.entities[found], False
        weak = self._by_org_prefix(value) or self._by_unique_surname(value)
        return weak, False

    def _by_org_prefix(self, value: str) -> Entity | None:
        """Match abbreviations such as 'Acme Building Sol.' or an email domain like 'acme-build'."""
        prefix = _squash(value)
        if len(prefix) < 8:
            return None
        matches = [e for e in self.entities.values()
                   if e.kind == "organisation" and _squash(e.name).startswith(prefix)]
        return matches[0] if len(matches) == 1 else None

    def _by_unique_surname(self, value: str) -> Entity | None:
        """'Mr. Kumaraswamy' or 'A. Kumaraswamy' → the only person with that surname and a compatible
        given name; 'Anita Kumar' never matches 'Ravi Kumar'."""
        words = normalise_name(value).split()
        if not 1 <= len(words) <= 3 or len(words[-1]) < 4:
            return None
        given = words[0] if len(words) > 1 else ""
        matches = []
        for entity in self.entities.values():
            names = normalise_name(entity.name).split()
            if entity.kind == "person" and names[-1:] == words[-1:] and \
                    (not given or names[0].startswith(given) or given.startswith(names[0])):
                matches.append(entity)
        return matches[0] if len(matches) == 1 else None

    def display(self, value: str) -> str:
        """Readable name for a field value (an employee/vendor ID becomes the name)."""
        entity = self.resolve(value)
        return entity.name if entity else value

    def mentions(self, text: str) -> set[str]:
        """Keys of every entity mentioned anywhere in `text`."""
        return {key for key in self.entities if self._pattern(key).search(text or "")}

    def _pattern(self, key: str) -> re.Pattern:
        if key not in self._patterns:
            entity = self.entities[key]
            words = entity.name.split()
            terms = {re.escape(entity.name), *map(re.escape, entity.ids), *map(re.escape, entity.emails)}
            if entity.kind == "person" and len(words) >= 2:
                first, surname = re.escape(words[0]), re.escape(words[-1])
                terms.add(rf"{re.escape(words[0][0])}\.?\s*{surname}")
                terms.add(rf"(?:Mr|Mrs|Ms|Dr)\.?\s+{surname}")
                terms.add(rf"{first}[._]{surname}(?=@)")
            pattern = rf"(?<![\w-])(?:{'|'.join(sorted(terms, key=len, reverse=True))})(?![\w-])"
            if entity.acronym:
                pattern = rf"{pattern}|(?<![\w-])(?-i:{entity.acronym})(?![\w-])"
            self._patterns[key] = re.compile(pattern, re.IGNORECASE)
        return self._patterns[key]

    def subjects(self) -> list[Entity]:
        """People and user accounts — everyone whose actions can be profiled."""
        return [e for e in self.entities.values() if e.kind in {"person", "account"}]
