from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from claude_data.curator.adapters import ClaudeAdapter
from claude_data.curator.model import Collection, CollectionItem, Message, Pair, Source
from claude_data.curator.pairs import build_pairs


@dataclass
class Library:
    archive_root: Path
    sources: list[Source]
    pairs: list[Pair]
    pair_messages: dict[str, list[Message]]
    source_by_id: dict[str, Source] = field(init=False, repr=False)
    pair_by_id: dict[str, Pair] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.source_by_id = {source.source_id: source for source in self.sources}
        self.pair_by_id = {pair.pair_id: pair for pair in self.pairs}

    @classmethod
    def build(cls, archive_root: Path) -> "Library":
        adapter = ClaudeAdapter()
        sources: list[Source] = []
        pairs: list[Pair] = []
        pair_messages: dict[str, list[Message]] = {}
        conversations = archive_root / "conversations"
        if conversations.is_dir():
            for path in sorted(conversations.glob("*.json")):
                if path.name.endswith(".meta.json"):
                    continue
                bundle = adapter.load(path, archive_root)
                built = build_pairs(bundle.source, bundle.messages, bundle.leaf_uuid)
                sources.append(
                    replace(bundle.source, path_fallback=built.path_fallback)
                )
                pairs.extend(built.pairs)
                pair_messages.update(built.messages)
        sources.sort(key=lambda source: (source.created_at, source.source_id))
        pairs.sort(key=lambda pair: (pair.date, pair.pair_id))
        return cls(
            archive_root=archive_root,
            sources=sources,
            pairs=pairs,
            pair_messages=pair_messages,
        )

    def to_json(self) -> dict:
        return {
            "sources": [asdict(source) for source in self.sources],
            "pairs": [asdict(pair) for pair in self.pairs],
        }

    def pair_content(self, pair_id: str) -> dict:
        pair = self.pair_by_id[pair_id]
        messages = self.pair_messages[pair_id]
        return {
            "pair": asdict(pair),
            "question": asdict(messages[0]),
            "answers": [asdict(message) for message in messages[1:]],
        }

    def _relink(self, item: CollectionItem) -> CollectionItem:
        if item.pair_id in self.pair_by_id:
            return item
        for pair in self.pairs:
            if pair.source_id == item.source_id and pair.question_uuid == item.question_uuid:
                return replace(
                    item,
                    pair_id=pair.pair_id,
                    date=pair.date,
                    preview=pair.question_text,
                )
        prefix = item.preview[:40]
        if prefix:
            matches = [
                pair
                for pair in self.pairs
                if pair.source_id == item.source_id
                and pair.date == item.date
                and pair.question_text.startswith(prefix)
            ]
            if len(matches) == 1:
                pair = matches[0]
                return replace(
                    item,
                    pair_id=pair.pair_id,
                    date=pair.date,
                    preview=pair.question_text,
                )
        return item

    def reconcile(self, collections: list[Collection]) -> list[Collection]:
        return [
            replace(
                collection,
                items=[self._relink(item) for item in collection.items],
            )
            for collection in collections
        ]
