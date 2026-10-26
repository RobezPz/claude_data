import hashlib
import html
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from claude_data.archiver.files import safe_filename
from claude_data.archiver.store import atomic_write_bytes
from claude_data.curator.library import Library
from claude_data.curator.model import Asset, Collection, Pair
from claude_data.curator.pairs import message_text
from claude_data.curator.render import render_pair

EXPORT_CSS = """
body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto;
       padding: 0 1rem; line-height: 1.6; color: #1f2328; }
h1, h2 { line-height: 1.25; }
h2 { margin-top: 2.5rem; border-top: 1px solid #d0d7de; padding-top: 1rem; }
pre { overflow-x: auto; padding: 1rem; background: #f6f8fa; border-radius: 6px; }
code { font-family: ui-monospace, monospace; }
img { max-width: 100%; }
details { margin: 0.5rem 0; }
.question { font-weight: 600; }
.answer { margin-bottom: 1rem; }
.asset-note, .meta { color: #656d76; font-size: 0.9rem; }
"""


@dataclass(frozen=True)
class ExportResult:
    folder: Path
    zip_path: Path


def copy_assets(
    assets: list[Asset], archive_root: Path, assets_dir: Path
) -> tuple[dict[str, str], list[str]]:
    assets_dir.mkdir(parents=True, exist_ok=True)
    root = archive_root.resolve()
    mapping: dict[str, str] = {}
    missing: list[str] = []
    by_hash: dict[str, str] = {}
    for asset in assets:
        if not asset.local_path:
            missing.append(asset.asset_id)
            continue
        source = (archive_root / asset.local_path).resolve()
        try:
            source.relative_to(root)
        except ValueError:
            missing.append(asset.asset_id)
            continue
        if not source.is_file():
            missing.append(asset.asset_id)
            continue
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:12]
        if digest not in by_hash:
            label = safe_filename(asset.label or source.name, source.name)
            stem = Path(label).stem or "asset"
            destination = assets_dir / f"{digest}-{stem}{source.suffix}"
            if not destination.exists():
                atomic_write_bytes(destination, data)
            by_hash[digest] = f"assets/{destination.name}"
        mapping[asset.asset_id] = by_hash[digest]
    return mapping, missing


def rewrite_links(text: str, mapping: dict[str, str]) -> str:
    for source, target in sorted(
        mapping.items(), key=lambda item: len(item[0]), reverse=True
    ):
        text = text.replace(source, target)
    return text


def build_export_zip(folder: Path) -> Path:
    zip_path = folder.parent / f"{folder.name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder.parent).as_posix())
    return zip_path


def _asset_links_markdown(
    assets: list[Asset], mapping: dict[str, str]
) -> list[str]:
    lines: list[str] = []
    for asset in assets:
        path = mapping.get(asset.asset_id)
        if path:
            lines.append(f"- [{asset.label}]({quote(path, safe='/')})")
        else:
            lines.append(f"- {asset.label} (unavailable)")
    return lines


def _asset_links_html(assets: list[Asset], mapping: dict[str, str]) -> str:
    if not assets:
        return ""
    items = []
    for asset in assets:
        path = mapping.get(asset.asset_id)
        label = html.escape(asset.label)
        if path:
            href = html.escape(quote(path, safe="/"))
            items.append(f'<li><a href="{href}">{label}</a></li>')
        else:
            items.append(f"<li>{label} (unavailable)</li>")
    return '<h3 class="meta">Attachments</h3><ul>' + "".join(items) + "</ul>"


def _unique_assets(assets: list[Asset]) -> list[Asset]:
    seen: set[str] = set()
    result: list[Asset] = []
    for asset in assets:
        if asset.asset_id in seen:
            continue
        seen.add(asset.asset_id)
        result.append(asset)
    return result


def export_collection(
    collection: Collection,
    library: Library,
    archive_root: Path,
    out_dir: Path,
) -> ExportResult:
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    final = (out_dir / collection.collection_id).resolve()
    if not final.is_relative_to(out_dir):
        raise ValueError("collection id escapes the export directory")
    temp = Path(
        tempfile.mkdtemp(prefix=f".{collection.collection_id}-", dir=out_dir)
    )
    assets_dir = temp / "assets"
    assets_dir.mkdir()
    try:
        resolved: list[tuple[object, tuple[Pair, list] | None]] = []
        all_assets: dict[str, Asset] = {}
        for item in collection.items:
            pair = library.pair_by_id.get(item.pair_id)
            if pair is None:
                resolved.append((item, None))
                continue
            messages = library.pair_messages[item.pair_id]
            assets = list(pair.assets)
            source = library.source_by_id.get(item.source_id)
            if source is not None:
                assets.extend(source.assets)
            for asset in assets:
                all_assets[asset.asset_id] = asset
            resolved.append((item, (pair, messages)))

        mapping, _missing = copy_assets(
            list(all_assets.values()), archive_root, assets_dir
        )
        local_map = {
            asset.local_path: mapping[asset.asset_id]
            for asset in all_assets.values()
            if asset.local_path and asset.asset_id in mapping
        }

        lines = [
            "---",
            f"collection_id: {collection.collection_id}",
            f"title: {collection.title}",
            f"item_count: {len(collection.items)}",
            f"exported_at: {datetime.now(UTC).isoformat()}",
            "---",
            "",
            f"# {collection.title}",
            "",
        ]
        if collection.description:
            lines.extend([collection.description, ""])
        for item, resolved_pair in resolved:
            source = library.source_by_id.get(item.source_id)
            source_title = source.title if source is not None else item.source_id
            if resolved_pair is None:
                lines.extend(
                    [
                        f"## {item.date} — missing source",
                        "",
                        item.preview,
                        "",
                        "- _This pair is missing from the current archive._",
                        "",
                    ]
                )
                continue
            pair, messages = resolved_pair
            lines.extend([f"## {item.date} — {source_title}", ""])
            question_text = message_text(messages[0])
            if question_text:
                lines.extend([rewrite_links(question_text, local_map), ""])
            for answer in messages[1:]:
                answer_text = message_text(answer)
                if answer_text:
                    lines.extend([rewrite_links(answer_text, local_map), ""])
            pair_assets = _unique_assets(list(pair.assets))
            if pair_assets:
                lines.append("**Attachments**")
                lines.extend(_asset_links_markdown(pair_assets, mapping))
                lines.append("")
        (temp / "collection.md").write_text("\n".join(lines), encoding="utf-8")

        html_map = {
            f"assets/{asset.local_path}": mapping[asset.asset_id]
            for asset in all_assets.values()
            if asset.local_path and asset.asset_id in mapping
        }
        html_parts = [
            "<!doctype html>",
            '<html><head><meta charset="utf-8">',
            f"<title>{html.escape(collection.title)}</title>",
            f"<style>{EXPORT_CSS}</style></head><body>",
            f"<h1>{html.escape(collection.title)}</h1>",
        ]
        if collection.description:
            html_parts.append(f"<p>{html.escape(collection.description)}</p>")
        for item, resolved_pair in resolved:
            source = library.source_by_id.get(item.source_id)
            source_title = source.title if source is not None else item.source_id
            if resolved_pair is None:
                html_parts.append(
                    f"<h2>{html.escape(item.date)} — missing source</h2>"
                    f"<p>{html.escape(item.preview)}</p>"
                    '<p class="meta">This pair is missing from the current archive.</p>'
                )
                continue
            pair, messages = resolved_pair
            rendered = render_pair(
                pair, messages[0], messages[1:], link_prefix="assets/"
            )
            html_parts.append(
                f"<h2>{html.escape(item.date)} — {html.escape(source_title)}</h2>"
            )
            html_parts.append(
                '<section class="question">'
                + rewrite_links(rendered["question_html"], html_map)
                + "</section>"
            )
            html_parts.append(
                '<section class="answer">'
                + rewrite_links(rendered["answer_html"], html_map)
                + "</section>"
            )
            html_parts.append(
                _asset_links_html(_unique_assets(list(pair.assets)), mapping)
            )
        html_parts.append("</body></html>")
        (temp / "collection.html").write_text(
            "\n".join(html_parts), encoding="utf-8"
        )

        pairs_json = []
        for _item, resolved_pair in resolved:
            if resolved_pair is None:
                continue
            pair, messages = resolved_pair
            pairs_json.append(
                {
                    "pair": asdict(pair),
                    "question": asdict(messages[0]),
                    "answers": [asdict(message) for message in messages[1:]],
                }
            )
        payload = {
            "collection": asdict(collection),
            "pairs": pairs_json,
            "missing": [
                item.pair_id for item, resolved_pair in resolved
                if resolved_pair is None
            ],
        }
        (temp / "collection.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise

    if final.exists():
        shutil.rmtree(final)
    os.replace(temp, final)
    zip_path = build_export_zip(final)
    return ExportResult(folder=final, zip_path=zip_path)
