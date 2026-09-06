"""
crawler/asset_manager.py

Implements the "Asset Size Safeguard": every network response Playwright
observes while a page is loaded is inspected here, and any image/media/font/
stylesheet/script asset larger than the user-configured threshold is skipped
(left pointing at its live URL) instead of being copied into the offline
archive. Assets within the limit are saved to disk once and reused for every
page that references the same URL, both within a run and across resumes
(via the 'assets' table in the database).
"""

import asyncio
from pathlib import Path
from typing import Optional

from utils import paths

# Playwright request.resource_type values worth treating as "assets" for the
# purpose of the size safeguard. Documents (the pages themselves) and
# XHR/fetch/websocket traffic are intentionally excluded.
ASSET_RESOURCE_TYPES = {"image", "media", "font", "stylesheet", "script"}

STATUS_SAVED = "saved"
STATUS_SKIPPED = "skipped_oversized"
STATUS_FAILED = "failed"


class AssetManager:
	"""Downloads or skips individual page assets according to the configured
	size limit, and remembers the outcome for every URL for the lifetime of
	the crawl (and across resumed sessions, via the database)."""

	def __init__(self, db, output_dir: Path, max_size_bytes: int, logger):
		self.db = db
		self.output_dir = Path(output_dir)
		self.max_size_bytes = max_size_bytes
		self.logger = logger
		# In-memory cache mirrors the DB so repeated references to the same
		# asset across many pages don't each require a sqlite round-trip.
		self._cache: dict = {}

	def on_response(self, response) -> None:
		"""Playwright 'response' event hook. Playwright's event emitter
		expects a synchronous callable, so the real (async) work is
		scheduled as a background task instead of awaited directly here."""
		task = asyncio.ensure_future(self._handle_response(response))
		task.add_done_callback(self._log_task_exception)

	def _log_task_exception(self, task: asyncio.Task) -> None:
		if task.cancelled():
			return
		exc = task.exception()
		if exc:
			self.logger(f"Asset capture error: {exc}")

	async def _handle_response(self, response) -> None:
		try:
			request = response.request
			if request.resource_type not in ASSET_RESOURCE_TYPES:
				return

			url = response.url
			if url in self._cache:
				return  # Already resolved earlier in this crawl.

			existing = self.db.get_asset(url)
			if existing is not None:
				self._cache[url] = existing
				return

			# Prefer the Content-Length header when present, so oversized
			# assets can be skipped without downloading a single byte.
			content_length = response.headers.get("content-length")
			if content_length is not None:
				try:
					declared_size = int(content_length)
				except ValueError:
					declared_size = None
				if declared_size is not None and declared_size > self.max_size_bytes:
					self._skip(url, declared_size)
					return

			try:
				body = await response.body()
			except Exception:
				return  # Body unavailable (redirect, opaque/cached response, etc.)

			if len(body) > self.max_size_bytes:
				self._skip(url, len(body))
				return

			self._save(url, body)

		except Exception as exc:
			self.logger(f"Unexpected asset handling error for {getattr(response, 'url', '?')}: {exc}")

	def _skip(self, url: str, size_bytes: int) -> None:
		self.db.mark_asset_skipped(url, size_bytes=size_bytes)
		self._cache[url] = {"status": STATUS_SKIPPED, "local_path": None}
		self.logger(f"Skipped oversized asset ({size_bytes // 1024} KB): {url}")

	def _save(self, url: str, body: bytes) -> None:
		local_path = paths.asset_local_path(url, self.output_dir)
		try:
			paths.ensure_parent_dir(local_path)
			local_path.write_bytes(body)
		except OSError as exc:
			self.db.mark_asset_failed(url)
			self._cache[url] = {"status": STATUS_FAILED, "local_path": None}
			self.logger(f"Could not save asset {url}: {exc}")
			return

		self.db.mark_asset_saved(url, str(local_path), len(body))
		self._cache[url] = {"status": STATUS_SAVED, "local_path": str(local_path)}

	def get_relative_path_for(self, asset_url: str, from_file: Path) -> Optional[str]:
		"""Return a relative href for an already-saved asset, or None if the
		asset was skipped/failed/never observed - in which case the caller
		should keep the original absolute URL so the page still renders."""
		entry = self._cache.get(asset_url) or self.db.get_asset(asset_url)
		if not entry or entry.get("status") != STATUS_SAVED or not entry.get("local_path"):
			return None
		return paths.relative_link(from_file, Path(entry["local_path"]))
