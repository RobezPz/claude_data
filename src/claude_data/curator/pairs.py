from collections import Counter
from dataclasses import dataclass

from claude_data.curator.model import Asset, Message, Pair, Source

ROOT_PARENT_UUID = "00000000-0000-4000-8000-000000000000"
EPOCH = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class PairBuild:
    pairs: list[Pair]
    messages: dict[str, list[Message]]
    path_fallback: bool


def message_text(message: Message, separator: str = " ") -> str:
    parts: list[str] = []
    for block in message.content or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    for attachment in message.attachments or []:
        if not isinstance(attachment, dict):
            continue
        name = str(attachment.get("file_name") or "attachment")
        extracted = attachment.get("extracted_content")
        if isinstance(extracted, str) and extracted.strip():
            parts.append(f"[Attachment: {name}]\n\n{extracted.strip()}")
        else:
            parts.append(f"[Attachment: {name}]")
    return separator.join(parts)


def preview_text(message: Message, limit: int = 200) -> str:
    return message_text(message)[:limit]


def _index_order(messages: list[Message]) -> list[Message]:
    return sorted(
        messages,
        key=lambda message: (message.index, message.created_at, message.external_id),
    )


def active_path(
    messages: list[Message], leaf_uuid: str | None
) -> tuple[list[Message], bool]:
    by_uuid = {message.external_id: message for message in messages}
    if leaf_uuid and leaf_uuid in by_uuid:
        path: list[Message] = []
        seen: set[str] = set()
        current = by_uuid[leaf_uuid]
        while True:
            if current.external_id in seen:
                return _index_order(messages), True
            path.append(current)
            seen.add(current.external_id)
            parent = current.parent_id
            if parent in (None, "", ROOT_PARENT_UUID):
                break
            if parent not in by_uuid:
                return _index_order(messages), True
            current = by_uuid[parent]
        path.reverse()
        return path, False
    return _index_order(messages), True


def _dedupe_assets(assets: list[Asset]) -> list[Asset]:
    seen: set[str] = set()
    result: list[Asset] = []
    for asset in assets:
        if asset.asset_id in seen:
            continue
        seen.add(asset.asset_id)
        result.append(asset)
    return result


def build_pairs(
    source: Source, messages: list[Message], leaf_uuid: str | None
) -> PairBuild:
    path, path_fallback = active_path(messages, leaf_uuid)
    child_counts = Counter(
        message.parent_id for message in messages if message.parent_id
    )
    pairs: list[Pair] = []
    pair_messages: dict[str, list[Message]] = {}
    question: Message | None = None
    answers: list[Message] = []

    def close_pair() -> None:
        nonlocal question, answers
        if question is None:
            return
        pair_id = f"{source.source_id}:{question.external_id}"
        date = question.created_at or source.created_at or EPOCH
        all_messages = [question, *answers]
        assets = _dedupe_assets(
            [asset for message in all_messages for asset in message.assets]
        )
        pairs.append(
            Pair(
                pair_id=pair_id,
                source_id=source.source_id,
                question_uuid=question.external_id,
                answer_uuids=[answer.external_id for answer in answers],
                date=date,
                question_text=preview_text(question),
                answer_text=" ".join(preview_text(answer) for answer in answers),
                has_branches=any(
                    child_counts[message.external_id] > 1
                    for message in all_messages
                ),
                missing_answer=not answers,
                assets=assets,
            )
        )
        pair_messages[pair_id] = all_messages
        question = None
        answers = []

    for message in path:
        if message.role == "human":
            close_pair()
            question = message
            answers = []
        elif message.role == "assistant" and question is not None:
            answers.append(message)

    close_pair()
    return PairBuild(pairs=pairs, messages=pair_messages, path_fallback=path_fallback)
