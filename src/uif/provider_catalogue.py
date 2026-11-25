"""Offline projection of bounded model catalogues. Never route or grant proof."""

from dataclasses import dataclass
import re

from .provider_pool import ProviderRoute

_MAX_ROWS = 4096
_MAX_ID = 192
_ID = re.compile(r"[A-Za-z0-9@][A-Za-z0-9@./_:-]*")
# The parser is selected by an installed caller, never by upstream JSON.
_SHAPES = {
    "openai-models": ("data", "id", ""),
    "gemini-models": ("models", "name", "models/"),
    "cloudflare-workers-ai": ("result", "name", ""),
    "codex-app-server": ("data", "model", ""),
    "ollama-tags": ("models", "name", ""),
    "windmill-model-audit": ("model_ids", None, ""),
}


@dataclass(frozen=True)
class CatalogueObservation:
    models: tuple[str, ...]
    complete: bool

    def lists(self, route: ProviderRoute) -> bool:
        """Positive catalogue match only, not auth, policy, price or capacity."""
        return type(route) is ProviderRoute and route.model in self.models


def _valid_id(value):
    return (
        type(value) is str and 0 < len(value) <= _MAX_ID
        and _ID.fullmatch(value) is not None
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


def normalize_model_catalogue(protocol: str, payload: dict) -> CatalogueObservation:
    """Normalize only IDs; no arbitrary URL, command, cursor follow, or network.

    A partial page can support positive inclusion only. Unknown, failed and
    malformed input raises; callers must not turn a parse failure into an empty
    authoritative inventory. Even a complete catalogue is not an entitlement.
    """
    if (type(protocol) is not str or type(payload) is not dict
            or protocol not in _SHAPES and protocol not in ("openai-model", "gemini-model")):
        raise ValueError("unsupported model catalogue")
    if protocol in ("openai-model", "gemini-model"):
        list_protocol = protocol + "s"
        rows_key = _SHAPES[list_protocol][0]
        return normalize_model_catalogue(list_protocol, {rows_key: [payload]})
    rows_key, item_key, prefix = _SHAPES[protocol]
    rows = payload.get(rows_key)
    if type(rows) is not list or len(rows) > _MAX_ROWS:
        raise ValueError("malformed model catalogue")
    if protocol == "cloudflare-workers-ai" and payload.get("success") is not True:
        raise ValueError("failed Cloudflare model catalogue")
    if protocol == "windmill-model-audit" and (
        payload.get("status") != "catalog_verified_not_inference"
        or type(payload.get("inference_calls")) is not int
        or payload["inference_calls"] != 0
        or payload.get("provider") not in ("nous", "huggingface")
    ):
        raise ValueError("not an existing catalogue-only audit")
    models = set()
    for row in rows:
        value = row if item_key is None else row.get(item_key) if type(row) is dict else None
        if (type(value) is not str or not value.startswith(prefix)
                or not _valid_id(value[len(prefix):])):
            raise ValueError("malformed model identifier")
        models.add(value[len(prefix):])
    # Only Ollama tags has an unpaged, complete local response in these
    # supported shapes. No other parser guesses completeness from an empty page.
    return CatalogueObservation(tuple(sorted(models)), protocol == "ollama-tags")
