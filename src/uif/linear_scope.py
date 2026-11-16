"""Match an issue presentation to a caller-authenticated native scope digest.

This pure projection neither authenticates the expected digest nor grants scope,
claim, admission, checkpoint, review or publication authority.
"""

from collections.abc import Mapping
import hashlib
import json
import re


class ScopeNormalizationError(ValueError):
    """The input or supported presentation does not match the expected scope."""


_FIELDS = ("id", "identifier", "title", "description")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ISSUE_RENDERER = re.compile(
    r'<issue id="[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}" '
    r'label="(?P<label>[A-Z][A-Z0-9]*-[1-9][0-9]*)" '
    r'href="(?P<href>https://linear\.app/[A-Za-z0-9_-]+/issue/(?P=label)'
    r'(?:/[A-Za-z0-9._~%+-]+)?(?:[?#][^"<>\\()\s]*)?)" />'
)


def native_scope_sha256(issue: Mapping) -> str:
    """Use native field order, UTF-8 and the default JSON separators exactly."""
    if not isinstance(issue, Mapping) or not all(key in issue for key in _FIELDS):
        raise ScopeNormalizationError("invalid native issue shape")
    if not all(isinstance(issue[key], str) for key in _FIELDS[:3]):
        raise ScopeNormalizationError("invalid native issue identity or title")
    if issue["description"] is not None and not isinstance(issue["description"], str):
        raise ScopeNormalizationError("invalid native issue description")
    try:
        payload = json.dumps([issue[key] for key in _FIELDS], ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except UnicodeError as exc:
        raise ScopeNormalizationError("invalid native issue Unicode") from exc


def normalize_issue_scope(issue: Mapping, expected_native_sha256: str) -> Mapping:
    """Return the original or a description-only copy on an exact digest match.

    The caller must authenticate the expected digest for this comparison. Only
    the proven MCP issue-renderer grammar is projected; other bytes stay intact.
    A mismatch is never repaired by guessing requirements or historical text.
    """
    if not isinstance(expected_native_sha256, str) or not _SHA256.fullmatch(expected_native_sha256):
        raise ScopeNormalizationError("invalid expected native SHA-256")
    if native_scope_sha256(issue) == expected_native_sha256:
        return issue
    if not isinstance(issue["description"], str):
        raise ScopeNormalizationError("native scope mismatch")

    description, count = _ISSUE_RENDERER.subn(
        lambda token: f'[{token["label"]}]({token["href"]})', issue["description"]
    )
    if not count:
        raise ScopeNormalizationError("native scope mismatch")
    projected = dict(issue)
    projected["description"] = description
    if native_scope_sha256(projected) != expected_native_sha256:
        raise ScopeNormalizationError("native scope mismatch")
    return projected
