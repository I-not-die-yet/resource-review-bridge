"""Evidence Packet schema and lightweight validation."""

from typing import Any, Dict, List


READ_STATES = {"not_applicable", "not_attempted", "completed", "partial", "blocked"}
PACKET_STATUSES = {"completed", "partial", "blocked", "failed"}
EVIDENCE_KINDS = {
    "original_post",
    "carousel_item",
    "comment",
    "outbound_link",
    "page",
}


EVIDENCE_PACKET_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": sorted(PACKET_STATUSES)},
        "source": {
            "type": "object",
            "properties": {
                "original_url": {"type": "string"},
                "canonical_url": {"type": ["string", "null"]},
                "platform": {"type": "string"},
            },
            "required": ["original_url", "canonical_url", "platform"],
            "additionalProperties": False,
        },
        "read_state": {
            "type": "object",
            "properties": {
                "auth_prompt": {"type": "string", "enum": sorted(READ_STATES)},
                "original_content": {"type": "string", "enum": sorted(READ_STATES)},
                "carousel": {"type": "string", "enum": sorted(READ_STATES)},
                "comments": {"type": "string", "enum": sorted(READ_STATES)},
                "outbound_links": {"type": "string", "enum": sorted(READ_STATES)},
            },
            "required": [
                "auth_prompt",
                "original_content",
                "carousel",
                "comments",
                "outbound_links",
            ],
            "additionalProperties": False,
        },
        "summary": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["claim", "evidence_ids"],
                "additionalProperties": False,
            },
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string", "enum": sorted(EVIDENCE_KINDS)},
                    "text": {"type": "string"},
                    "url": {"type": ["string", "null"]},
                    "author": {"type": ["string", "null"]},
                    "published_at": {"type": ["string", "null"]},
                    "importance": {"type": "string", "enum": ["primary", "supporting"]},
                },
                "required": ["id", "kind", "text", "url", "author", "published_at", "importance"],
                "additionalProperties": False,
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "source", "read_state", "summary", "claims", "evidence", "limitations"],
    "additionalProperties": False,
}


class PacketValidationError(ValueError):
    pass


def _require_string(value: Any, path: str, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str):
        raise PacketValidationError("%s must be a string" % path)


def validate_packet(packet: Any, expected_url: str) -> Dict[str, Any]:
    if not isinstance(packet, dict):
        raise PacketValidationError("packet must be an object")
    required = set(EVIDENCE_PACKET_SCHEMA["required"])
    if set(packet) != required:
        raise PacketValidationError("packet fields do not match the Evidence Packet contract")
    if packet["status"] not in PACKET_STATUSES:
        raise PacketValidationError("invalid packet status")

    source = packet["source"]
    if not isinstance(source, dict) or set(source) != {"original_url", "canonical_url", "platform"}:
        raise PacketValidationError("invalid source")
    if source["original_url"] != expected_url:
        raise PacketValidationError("source.original_url does not match the requested URL")
    _require_string(source["canonical_url"], "source.canonical_url", allow_none=True)
    _require_string(source["platform"], "source.platform")

    read_state = packet["read_state"]
    expected_states = {"auth_prompt", "original_content", "carousel", "comments", "outbound_links"}
    if not isinstance(read_state, dict) or set(read_state) != expected_states:
        raise PacketValidationError("invalid read_state")
    if any(value not in READ_STATES for value in read_state.values()):
        raise PacketValidationError("invalid read_state value")

    _require_string(packet["summary"], "summary")
    if not isinstance(packet["limitations"], list) or not all(isinstance(item, str) for item in packet["limitations"]):
        raise PacketValidationError("limitations must be strings")

    evidence = packet["evidence"]
    if not isinstance(evidence, list):
        raise PacketValidationError("evidence must be an array")
    evidence_ids = set()
    evidence_fields = {"id", "kind", "text", "url", "author", "published_at", "importance"}
    for index, item in enumerate(evidence):
        if not isinstance(item, dict) or set(item) != evidence_fields:
            raise PacketValidationError("invalid evidence item at index %d" % index)
        _require_string(item["id"], "evidence.id")
        if not item["id"] or item["id"] in evidence_ids:
            raise PacketValidationError("evidence IDs must be non-empty and unique")
        evidence_ids.add(item["id"])
        if item["kind"] not in EVIDENCE_KINDS or item["importance"] not in {"primary", "supporting"}:
            raise PacketValidationError("invalid evidence classification")
        _require_string(item["text"], "evidence.text")
        for key in ("url", "author", "published_at"):
            _require_string(item[key], "evidence.%s" % key, allow_none=True)

    claims = packet["claims"]
    if not isinstance(claims, list):
        raise PacketValidationError("claims must be an array")
    for item in claims:
        if not isinstance(item, dict) or set(item) != {"claim", "evidence_ids"}:
            raise PacketValidationError("invalid claim")
        _require_string(item["claim"], "claim.claim")
        refs: List[Any] = item["evidence_ids"]
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            raise PacketValidationError("claim evidence_ids must be strings")
        if any(ref not in evidence_ids for ref in refs):
            raise PacketValidationError("claim refers to missing evidence")

    if packet["status"] == "completed" and read_state["original_content"] != "completed":
        raise PacketValidationError("completed packets require completed original_content")
    if packet["status"] == "completed" and not any(
        item["kind"] == "original_post" and item["importance"] == "primary" for item in evidence
    ):
        raise PacketValidationError("completed packets require primary original-post evidence")
    if packet["status"] == "partial" and read_state["original_content"] not in {"completed", "partial"}:
        raise PacketValidationError("partial packets require at least partial original content")
    if packet["status"] != "completed" and not packet["limitations"]:
        raise PacketValidationError("non-completed packets must explain their limitations")
    return packet
