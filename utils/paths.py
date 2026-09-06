"""
utils/paths.py

Helpers that translate remote URLs into stable, collision-resistant local
file paths inside the archive folder, and compute the relative links between
saved files that make the final output portable (no absolute paths baked in).
"""

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

SITE_DIRNAME = "site"
ASSETS_DIRNAME = "assets"

_UNSAFE_CHARS = re.compile(r'[<>:"|?*\\]')


def sanitize_filename(name: str, max_len: int = 100) -> str:
	"""Strip characters that are unsafe for filenames on common filesystems."""
	name = _UNSAFE_CHARS.sub("_", name)
	name = name.strip().strip(".")
	if not name:
		name = "index"
	return name[:max_len]


def url_to_relative_path(url: str) -> str:
	"""
	Convert an absolute URL into a stable, collision-resistant relative path
	(POSIX-style) used as the on-disk location for a saved page.

	Directory-style URLs (ending in '/' or with an empty path) map to
	'index.html' inside their own folder. Query strings are preserved as a
	short hash suffix so distinct query variants of the same path do not
	overwrite one another.
	"""
	parsed = urlsplit(url)
	host = sanitize_filename(parsed.netloc.replace(":", "_"))
	path = parsed.path or "/"

	segments = [sanitize_filename(seg) for seg in path.split("/") if seg]
	if not segments or path.endswith("/"):
		segments.append("index")

	# Every saved page is rendered/serialized HTML, regardless of the
	# original URL's apparent extension (e.g. an extensionless API route
	# that server-rendered a full page still gets a '.html' file locally).
	last = segments[-1]
	if not last.lower().endswith((".html", ".htm")):
		stem, _ext = os.path.splitext(last)
		last = f"{stem or last}.html"
	segments[-1] = last

	if parsed.query:
		q_hash = hashlib.sha1(parsed.query.encode("utf-8")).hexdigest()[:8]
		stem, ext = os.path.splitext(segments[-1])
		segments[-1] = f"{stem}_{q_hash}{ext}"

	return "/".join([host] + segments)


def url_to_local_file(url: str, output_dir: Path) -> Path:
	"""Return the absolute Path on disk where a crawled page should be saved."""
	rel = url_to_relative_path(url)
	return Path(output_dir) / SITE_DIRNAME / rel


def asset_local_path(url: str, output_dir: Path) -> Path:
	"""
	Build a flat, collision-resistant local path for a downloaded asset.
	A short hash of the full URL is prefixed to the basename so that two
	different URLs sharing a filename (e.g. multiple "logo.png") never
	overwrite each other.
	"""
	parsed = urlsplit(url)
	basename = sanitize_filename(os.path.basename(parsed.path) or "asset")
	url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
	return Path(output_dir) / SITE_DIRNAME / ASSETS_DIRNAME / f"{url_hash}_{basename}"


def relative_link(from_file: Path, to_file: Path) -> str:
	"""Compute a POSIX-style relative href from one saved file to another,
	so the archive works when copied anywhere on disk (no absolute paths)."""
	rel = os.path.relpath(to_file, start=Path(from_file).parent)
	return Path(rel).as_posix()


def ensure_parent_dir(path: Path) -> None:
	"""Create parent directories for a path if they do not already exist."""
	Path(path).parent.mkdir(parents=True, exist_ok=True)
