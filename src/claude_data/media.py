import hashlib
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from claude_data.store import media_path


@dataclass(frozen=True)
class DownloadPlan:
    to_download: list[tuple[str, str]]
    present: dict[str, str]


def rewrite_urls(obj: object, mapping: dict[str, str]) -> object:
    if isinstance(obj, str):
        for remote, local in mapping.items():
            if remote in obj:
                obj = obj.replace(remote, local)
        return obj
    if isinstance(obj, dict):
        return {key: rewrite_urls(value, mapping) for key, value in obj.items()}
    if isinstance(obj, list):
        return [rewrite_urls(item, mapping) for item in obj]
    return obj


def derive_media_filename(url: str, taken: set[str] = frozenset()) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    path = urllib.parse.urlsplit(url).path
    basename = path.rsplit("/", 1)[-1] if path else ""
    if not basename:
        stem, suffix = f"file-{digest[:12]}", ""
    elif "." in basename:
        stem, _, ext = basename.rpartition(".")
        suffix = f".{ext}"
    else:
        stem, suffix = basename, ""
    plain = f"{stem}{suffix}"
    if plain not in taken:
        return plain
    for length in range(8, len(digest) + 1):
        candidate = f"{stem}-{digest[:length]}{suffix}"
        if candidate not in taken:
            return candidate
    raise ValueError(f"no free filename for {url}")


def plan_downloads(urls: list[str], root: Path) -> DownloadPlan:
    to_download: list[tuple[str, str]] = []
    present: dict[str, str] = {}
    taken: set[str] = set()
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        filename = derive_media_filename(url, taken=taken)
        taken.add(filename)
        if media_path(root, filename).exists():
            present[url] = f"media/{filename}"
        else:
            to_download.append((url, filename))
    return DownloadPlan(to_download=to_download, present=present)


MEDIA_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".avif",
        ".bmp",
        ".pdf",
        ".mp4",
        ".webm",
        ".mov",
        ".mp3",
        ".m4a",
        ".wav",
        ".ogg",
    }
)

MEDIA_HOST_SUFFIXES = ("claude.ai", "claudeusercontent.com")


def _is_media_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.netloc.lower()
    if host.endswith(MEDIA_HOST_SUFFIXES):
        return True
    suffix = Path(parts.path).suffix.lower()
    return suffix in MEDIA_EXTENSIONS


def _iter_urls(node: object):
    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_urls(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_urls(item)
    elif isinstance(node, str) and node.startswith(("http://", "https://")):
        yield node


def extract_media_urls(payload: dict) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for url in _iter_urls(payload):
        if url in seen or not _is_media_url(url):
            continue
        seen.add(url)
        urls.append(url)
    return urls
