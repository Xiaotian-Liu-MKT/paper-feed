import json
import re

from paper_feed.notion_schema import PAGE_BODY_ANCHORS


RAW_ABSTRACT_SOURCE_OPTIONS = {
    "rss",
    "publisher",
    "crossref",
    "semantic_scholar",
    "user_pasted",
    "unknown",
}
METADATA_SCHEMA_VERSION = 1


class MetadataChunkLimitError(ValueError):
    """Raised when metadata JSON exceeds the supported chunk envelope."""


class MetadataManifestError(ValueError):
    """Raised when SYSTEM_METADATA_JSON cannot be parsed safely."""


def _text_fragment(text):
    return [{"type": "text", "text": {"content": text}}]


def _paragraph_block(text):
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _text_fragment(text)},
    }


def _code_block(text, language="json"):
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": _text_fragment(text),
            "language": language,
        },
    }


def build_anchor_heading_block(anchor_name):
    return {
        "object": "block",
        "type": "heading_1",
        "heading_1": {"rich_text": _text_fragment(anchor_name)},
    }


def build_audit_entry_block(message, *, occurred_at):
    content = f"[{occurred_at}] {(message or '').strip()[:500]}"
    return _paragraph_block(content)


def normalize_raw_abstract_source(source):
    normalized = (source or "").strip().lower()
    if normalized in RAW_ABSTRACT_SOURCE_OPTIONS:
        return normalized
    return "unknown"


def _split_text_chunks(text, *, chunk_size=1800):
    content = (text or "").strip()
    if not content:
        return []

    chunks = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        while len(line) > chunk_size:
            chunks.append(line[:chunk_size])
            line = line[chunk_size:]
        if line:
            chunks.append(line)
    return chunks


def build_raw_abstract_blocks(raw_abstract, raw_abstract_source):
    if not (raw_abstract or "").strip():
        return []

    blocks = [_paragraph_block(f"source: {normalize_raw_abstract_source(raw_abstract_source)}")]
    for chunk in _split_text_chunks(raw_abstract):
        blocks.append(_paragraph_block(chunk))
    return blocks


def build_metadata_blocks(creators, *, inline_limit=1800, chunk_size=1800, max_chunks=25):
    serialized = json.dumps(creators or [], ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= inline_limit:
        manifest = {
            "schema_version": METADATA_SCHEMA_VERSION,
            "creators_storage": "inline",
            "creators": creators or [],
        }
        return [_code_block(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))]

    chunks = [serialized[index : index + chunk_size] for index in range(0, len(serialized), chunk_size)]
    if len(chunks) > max_chunks:
        raise MetadataChunkLimitError(
            f"Creators payload requires {len(chunks)} chunks, exceeds max_chunks={max_chunks}"
        )

    manifest = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "creators_storage": "chunked",
        "creators_chunks": len(chunks),
    }
    blocks = [_code_block(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))]
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        blocks.append(_code_block(f"CREATORS_CHUNK {index}/{total}\n{chunk}"))
    return blocks


def _rich_text_plain_text(rich_text):
    parts = []
    for item in rich_text or []:
        plain = item.get("plain_text")
        if plain is not None:
            parts.append(plain)
            continue
        content = item.get("text", {}).get("content")
        if content is not None:
            parts.append(content)
    return "".join(parts)


def block_plain_text(block):
    if not isinstance(block, dict):
        return ""
    block_type = block.get("type", "")
    if not block_type:
        return ""
    value = block.get(block_type, {})
    return _rich_text_plain_text(value.get("rich_text", []))


def block_anchor_name(block):
    if block.get("type") != "heading_1":
        return ""
    name = block_plain_text(block)
    if name in PAGE_BODY_ANCHORS:
        return name
    return ""


def collect_anchor_sections(blocks):
    first_positions = {}
    duplicates = {}
    for index, block in enumerate(blocks):
        anchor_name = block_anchor_name(block)
        if not anchor_name:
            continue
        if anchor_name in first_positions:
            duplicates.setdefault(anchor_name, []).append(block)
            continue
        first_positions[anchor_name] = index

    sections = {}
    ordered = sorted(first_positions.items(), key=lambda item: item[1])
    for offset, (anchor_name, start_index) in enumerate(ordered):
        end_index = len(blocks)
        if offset + 1 < len(ordered):
            end_index = ordered[offset + 1][1]
        sections[anchor_name] = {
            "anchor": blocks[start_index],
            "children": blocks[start_index + 1 : end_index],
            "index": start_index,
            "duplicates": duplicates.get(anchor_name, []),
        }
    return sections


def _last_section_block_id(section):
    children = section.get("children", [])
    if children:
        return children[-1]["id"]
    return section["anchor"]["id"]


def _repair_note_blocks(repairs, *, occurred_at):
    return [build_audit_entry_block(repair, occurred_at=occurred_at) for repair in repairs]


def ensure_page_body_anchors(client, page_id, *, return_repairs=False):
    children = list(client.iter_block_children(page_id))
    sections = collect_anchor_sections(children)
    repairs = []

    for anchor_name in PAGE_BODY_ANCHORS:
        if anchor_name in sections:
            continue

        after_id = children[-1]["id"] if children else None
        for previous_anchor in reversed(PAGE_BODY_ANCHORS[: PAGE_BODY_ANCHORS.index(anchor_name)]):
            if previous_anchor in sections:
                after_id = _last_section_block_id(sections[previous_anchor])
                break

        client.append_block_children(
            page_id,
            [build_anchor_heading_block(anchor_name)],
            after=after_id,
        )
        repairs.append(f"missing_anchor_recreated: {anchor_name}")
        children = list(client.iter_block_children(page_id))
        sections = collect_anchor_sections(children)

    for anchor_name in PAGE_BODY_ANCHORS:
        duplicate_count = len(sections.get(anchor_name, {}).get("duplicates", []))
        if duplicate_count:
            repairs.append(f"duplicate_anchor_ignored: {anchor_name} ({duplicate_count} ignored)")

    if return_repairs:
        return sections, repairs
    return sections


def rewrite_anchor_section(client, page_id, anchor_name, blocks):
    sections = ensure_page_body_anchors(client, page_id)
    section = sections[anchor_name]
    for child in section.get("children", []):
        client.delete_block(child["id"])
    if blocks:
        client.append_block_children(page_id, blocks, after=section["anchor"]["id"])


def append_audit_entry(client, page_id, anchor_name, message, *, occurred_at, max_entries=50):
    sections, repairs = ensure_page_body_anchors(client, page_id, return_repairs=True)
    section = sections[anchor_name]
    blocks = _repair_note_blocks(repairs, occurred_at=occurred_at)
    blocks.append(build_audit_entry_block(message, occurred_at=occurred_at))
    client.append_block_children(
        page_id,
        blocks,
        after=_last_section_block_id(section),
    )

    refreshed = ensure_page_body_anchors(client, page_id)
    audit_children = refreshed[anchor_name].get("children", [])
    overflow = max(0, len(audit_children) - max_entries)
    for child in audit_children[:overflow]:
        client.delete_block(child["id"])


def resolve_creators_from_metadata_blocks(blocks):
    manifest = None
    manifest_index = None
    for index, block in enumerate(blocks):
        if block.get("type") != "code":
            continue
        try:
            candidate = json.loads(block_plain_text(block))
        except json.JSONDecodeError:
            continue
        if not isinstance(candidate, dict):
            continue
        if "schema_version" not in candidate or "creators_storage" not in candidate:
            continue
        manifest = candidate
        manifest_index = index
        break

    if manifest is None:
        raise MetadataManifestError("Missing SYSTEM_METADATA_JSON manifest")

    storage = manifest.get("creators_storage")
    if storage == "inline":
        creators = manifest.get("creators")
        if not isinstance(creators, list):
            raise MetadataManifestError("Inline metadata manifest is missing creators list")
        return creators

    if storage != "chunked":
        raise MetadataManifestError(f"Unsupported creators_storage value: {storage}")

    expected_chunks = manifest.get("creators_chunks")
    if not isinstance(expected_chunks, int) or expected_chunks < 1:
        raise MetadataManifestError("Chunked metadata manifest is missing creators_chunks")

    payload_by_index = {}
    pattern = re.compile(r"^CREATORS_CHUNK (\d+)/(\d+)\n(.*)$", re.DOTALL)
    for block in blocks[manifest_index + 1 :]:
        if block.get("type") != "code":
            continue
        match = pattern.match(block_plain_text(block))
        if not match:
            continue
        current_index = int(match.group(1))
        total = int(match.group(2))
        if total != expected_chunks:
            raise MetadataManifestError("Chunk manifest size does not match creators_chunks")
        payload_by_index[current_index] = match.group(3)

    if sorted(payload_by_index.keys()) != list(range(1, expected_chunks + 1)):
        raise MetadataManifestError("Creator chunks are missing or out of order")

    serialized = "".join(payload_by_index[index] for index in range(1, expected_chunks + 1))
    creators = json.loads(serialized)
    if not isinstance(creators, list):
        raise MetadataManifestError("Resolved creators payload is not a list")
    return creators


def build_initial_page_body_blocks(
    raw_abstract,
    raw_abstract_source,
    creators,
    *,
    occurred_at,
    max_block_count=100,
):
    metadata_blocks = []
    ingest_audit_blocks = []
    try:
        metadata_blocks = build_metadata_blocks(creators)
    except MetadataChunkLimitError as error:
        ingest_audit_blocks.append(
            build_audit_entry_block(f"authors_too_large: {error}", occurred_at=occurred_at)
        )

    blocks = []
    blocks.append(build_anchor_heading_block("SYSTEM_RAW_ABSTRACT"))
    blocks.extend(build_raw_abstract_blocks(raw_abstract, raw_abstract_source))
    blocks.append(build_anchor_heading_block("SYSTEM_METADATA_JSON"))
    blocks.extend(metadata_blocks)
    blocks.append(build_anchor_heading_block("INGEST_AUDIT"))
    blocks.extend(ingest_audit_blocks)
    blocks.append(build_anchor_heading_block("EXPORT_AUDIT"))
    blocks.append(build_anchor_heading_block("USER_NOTES"))

    if len(blocks) > max_block_count:
        return None
    return blocks
