"""Shared building blocks for all versioned data contracts.

Every persisted record is a ``StrictModel``: unknown fields are rejected, assignments are
validated, and string fields are stripped. Top-level contracts additionally carry a fixed
``contract`` name and ``contract_version`` so stored files and JSON schemas are self-describing.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

CONTRACT_VERSION = "1.0"
ContractVersion = Literal["1.0"]

ID_PATTERN = r"^[a-z][a-z0-9]*_[a-z0-9][a-z0-9_-]{2,79}$"

Id = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


Timestamp = Annotated[datetime, AfterValidator(_require_utc)]


def canonical_json(data: Any) -> bytes:
    """Deterministic JSON bytes used for digests and secret scans."""
    return json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def model_digest(model: BaseModel, exclude: set[str] | None = None) -> str:
    """SHA-256 over the canonical JSON form of a model, without the excluded fields."""
    return sha256_hex(canonical_json(model.model_dump(mode="json", exclude=exclude or set())))


def normalize_text(value: str) -> str:
    """Normalisation used for duplicate detection of topics and keywords.

    Lowercase, Unicode NFKC, German umlauts folded, punctuation removed, whitespace collapsed.
    """
    text = unicodedata.normalize("NFKC", value).casefold()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _canonical_https_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme != "https":
        raise ValueError("only https URLs are allowed")
    if not parts.hostname:
        raise ValueError("URL needs a host")
    if parts.username or parts.password:
        raise ValueError("URL must not contain credentials")
    if parts.port not in (None, 443):
        raise ValueError("only the default https port is allowed")
    host = parts.hostname.lower().rstrip(".")
    path = parts.path or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


HttpsUrl = Annotated[str, AfterValidator(_canonical_https_url)]


def canonical_url(value: str) -> str:
    """Canonical form of an https URL: lowercase host, no fragment, ``/`` for an empty path."""
    return _canonical_https_url(value)


def url_host(value: str) -> str:
    return urlsplit(value).hostname or ""


def host_in_domain(host: str, domain: str) -> bool:
    host, domain = host.lower().rstrip("."), domain.lower().rstrip(".")
    return host == domain or host == f"www.{domain}"


def make_id(prefix: str, *parts: str) -> str:
    """Stable id derived from its parts, e.g. ``make_id("pg", url)``."""
    digest = sha256_hex("\x1f".join(parts).encode("utf-8"))[:16]
    return f"{prefix}_{digest}"
