from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    asset_id: str
    kind: str
    label: str
    local_path: str | None = None
    remote_url: str | None = None
    message_uuid: str | None = None
    artifact_id: str | None = None
    source_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Message:
    external_id: str
    parent_id: str | None
    role: str
    created_at: str
    index: int
    content: list[dict]
    attachments: list[dict]
    assets: list[Asset]


@dataclass(frozen=True)
class Source:
    source_id: str
    provider: str
    external_id: str
    title: str
    model: str
    created_at: str
    updated_at: str
    raw_path: str
    message_count: int
    assets: list[Asset]
    path_fallback: bool = False


@dataclass(frozen=True)
class Pair:
    pair_id: str
    source_id: str
    question_uuid: str
    answer_uuids: list[str]
    date: str
    question_text: str
    answer_text: str
    has_branches: bool
    missing_answer: bool
    assets: list[Asset]


@dataclass(frozen=True)
class CollectionItem:
    pair_id: str
    source_id: str
    question_uuid: str
    date: str
    preview: str
    note: str = ""


@dataclass(frozen=True)
class Collection:
    collection_id: str
    title: str
    description: str
    items: list[CollectionItem]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class State:
    revision: int
    collections: list[Collection]
