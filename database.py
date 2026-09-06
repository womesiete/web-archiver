"""
database.py

SQLite-backed persistence layer for crawl state, discovered external links,
asset bookkeeping, and error logging.

Using a real database (rather than in-memory structures) is what makes
paused/interrupted crawls resumable: the crawl frontier itself lives in the
'pages' table, so re-opening the same output folder and calling
get_next_pending_page() simply continues wherever the queue left off,
without re-downloading anything already marked 'completed'.
"""

import datetime
import sqlite3
from pathlib import Path
from typing import Optional


def _now() -> str:
	"""UTC timestamp string used consistently across all tables."""
	return datetime.datetime.utcnow().isoformat()


class CrawlDatabase:
	"""Thin wrapper around a single sqlite3 connection dedicated to one
	crawl session's output folder (crawl_state.db)."""

	def __init__(self, db_path: Path):
		self.db_path = Path(db_path)
		try:
			self.db_path.parent.mkdir(parents=True, exist_ok=True)
			self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
			self._conn.row_factory = sqlite3.Row
			self._conn.execute("PRAGMA foreign_keys = ON")
			self._init_schema()
		except sqlite3.Error as exc:
			raise RuntimeError(f"Could not open crawl database at {self.db_path}: {exc}")

	def _init_schema(self) -> None:
		try:
			with self._conn:
				self._conn.executescript(
					"""
					CREATE TABLE IF NOT EXISTS meta (
						key TEXT PRIMARY KEY,
						value TEXT
					);

					CREATE TABLE IF NOT EXISTS pages (
						id INTEGER PRIMARY KEY AUTOINCREMENT,
						url TEXT UNIQUE NOT NULL,
						local_path TEXT,
						depth INTEGER NOT NULL DEFAULT 0,
						status TEXT NOT NULL DEFAULT 'pending',
						title TEXT,
						error_message TEXT,
						discovered_at TEXT NOT NULL,
						completed_at TEXT
					);

					CREATE TABLE IF NOT EXISTS external_links (
						id INTEGER PRIMARY KEY AUTOINCREMENT,
						url TEXT NOT NULL,
						found_on_page TEXT NOT NULL,
						link_text TEXT,
						discovered_at TEXT NOT NULL,
						UNIQUE(url, found_on_page)
					);

					CREATE TABLE IF NOT EXISTS assets (
						id INTEGER PRIMARY KEY AUTOINCREMENT,
						url TEXT UNIQUE NOT NULL,
						local_path TEXT,
						size_bytes INTEGER,
						status TEXT NOT NULL,
						discovered_at TEXT NOT NULL
					);

					CREATE TABLE IF NOT EXISTS errors (
						id INTEGER PRIMARY KEY AUTOINCREMENT,
						url TEXT NOT NULL,
						error_message TEXT NOT NULL,
						timestamp TEXT NOT NULL
					);
					"""
				)
		except sqlite3.Error as exc:
			raise RuntimeError(f"Failed to initialize database schema: {exc}")

	# ------------------------------------------------------------------ #
	# Session metadata (base URL, depth, etc. - useful for a future "resume
	# with original settings" feature; safe to ignore if unused)
	# ------------------------------------------------------------------ #

	def set_meta(self, key: str, value: str) -> None:
		try:
			with self._conn:
				self._conn.execute(
					"INSERT INTO meta (key, value) VALUES (?, ?) "
					"ON CONFLICT(key) DO UPDATE SET value = excluded.value",
					(key, str(value)),
				)
		except sqlite3.Error as exc:
			raise RuntimeError(f"Failed to write meta '{key}': {exc}")

	def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
		try:
			row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
			return row["value"] if row else default
		except sqlite3.Error:
			return default

	# ------------------------------------------------------------------ #
	# Pages / crawl frontier
	# ------------------------------------------------------------------ #

	def add_page(self, url: str, depth: int, status: str = "pending") -> bool:
		"""Insert a newly discovered URL into the frontier.

		Returns False (no-op) if the URL has already been seen - this is
		what makes the crawl loop-safe and idempotent, both within a single
		run and across resumed sessions.
		"""
		try:
			with self._conn:
				cur = self._conn.execute(
					"INSERT OR IGNORE INTO pages (url, depth, status, discovered_at) "
					"VALUES (?, ?, ?, ?)",
					(url, depth, status, _now()),
				)
			return cur.rowcount > 0
		except sqlite3.Error as exc:
			raise RuntimeError(f"Failed to enqueue page '{url}': {exc}")

	def get_next_pending_page(self) -> Optional[sqlite3.Row]:
		try:
			return self._conn.execute(
				"SELECT * FROM pages WHERE status = 'pending' "
				"ORDER BY depth ASC, id ASC LIMIT 1"
			).fetchone()
		except sqlite3.Error as exc:
			raise RuntimeError(f"Failed to read crawl frontier: {exc}")

	def mark_in_progress(self, page_id: int) -> None:
		self._safe_execute("UPDATE pages SET status = 'in_progress' WHERE id = ?", (page_id,))

	def mark_completed(self, page_id: int, local_path: str, title: Optional[str] = None) -> None:
		self._safe_execute(
			"UPDATE pages SET status = 'completed', local_path = ?, title = ?, "
			"completed_at = ? WHERE id = ?",
			(local_path, title, _now(), page_id),
		)

	def mark_failed(self, page_id: int, error_message: str) -> None:
		self._safe_execute(
			"UPDATE pages SET status = 'failed', error_message = ?, completed_at = ? "
			"WHERE id = ?",
			(error_message, _now(), page_id),
		)

	def count_total_pages(self) -> int:
		return self._scalar("SELECT COUNT(*) FROM pages")

	def count_completed_pages(self) -> int:
		return self._scalar("SELECT COUNT(*) FROM pages WHERE status = 'completed'")

	def count_failed_pages(self) -> int:
		return self._scalar("SELECT COUNT(*) FROM pages WHERE status = 'failed'")

	def count_pending_pages(self) -> int:
		return self._scalar(
			"SELECT COUNT(*) FROM pages WHERE status IN ('pending', 'in_progress')"
		)

	def get_all_pages(self):
		try:
			return self._conn.execute("SELECT * FROM pages ORDER BY id ASC").fetchall()
		except sqlite3.Error as exc:
			raise RuntimeError(f"Failed to read pages table: {exc}")

	def reset_stalled_pages(self) -> None:
		"""On resume, any page left 'in_progress' from a prior crash/close
		should be requeued as 'pending' instead of being stuck forever."""
		self._safe_execute("UPDATE pages SET status = 'pending' WHERE status = 'in_progress'")

	# ------------------------------------------------------------------ #
	# External links (kept live, never downloaded - just logged)
	# ------------------------------------------------------------------ #

	def add_external_link(self, url: str, found_on_page: str, link_text: str = "") -> None:
		self._safe_execute(
			"INSERT OR IGNORE INTO external_links (url, found_on_page, link_text, discovered_at) "
			"VALUES (?, ?, ?, ?)",
			(url, found_on_page, link_text, _now()),
		)

	def get_external_links(self):
		try:
			return self._conn.execute(
				"SELECT * FROM external_links ORDER BY discovered_at ASC"
			).fetchall()
		except sqlite3.Error as exc:
			raise RuntimeError(f"Failed to read external links: {exc}")

	# ------------------------------------------------------------------ #
	# Assets (subject to the max-size safeguard)
	# ------------------------------------------------------------------ #

	def get_asset(self, url: str) -> Optional[dict]:
		try:
			row = self._conn.execute("SELECT * FROM assets WHERE url = ?", (url,)).fetchone()
			return dict(row) if row else None
		except sqlite3.Error:
			return None

	def mark_asset_saved(self, url: str, local_path: str, size_bytes: int) -> None:
		self._upsert_asset(url, local_path, size_bytes, "saved")

	def mark_asset_skipped(self, url: str, size_bytes: Optional[int] = None) -> None:
		self._upsert_asset(url, None, size_bytes, "skipped_oversized")

	def mark_asset_failed(self, url: str) -> None:
		self._upsert_asset(url, None, None, "failed")

	def _upsert_asset(self, url: str, local_path, size_bytes, status: str) -> None:
		self._safe_execute(
			"INSERT INTO assets (url, local_path, size_bytes, status, discovered_at) "
			"VALUES (?, ?, ?, ?, ?) "
			"ON CONFLICT(url) DO UPDATE SET local_path = excluded.local_path, "
			"size_bytes = excluded.size_bytes, status = excluded.status",
			(url, local_path, size_bytes, status, _now()),
		)

	def get_skipped_assets(self):
		try:
			return self._conn.execute(
				"SELECT * FROM assets WHERE status = 'skipped_oversized' ORDER BY discovered_at ASC"
			).fetchall()
		except sqlite3.Error as exc:
			raise RuntimeError(f"Failed to read skipped assets: {exc}")

	# ------------------------------------------------------------------ #
	# Errors
	# ------------------------------------------------------------------ #

	def log_error(self, url: str, message: str) -> None:
		self._safe_execute(
			"INSERT INTO errors (url, error_message, timestamp) VALUES (?, ?, ?)",
			(url, message, _now()),
		)

	def get_errors(self):
		try:
			return self._conn.execute("SELECT * FROM errors ORDER BY timestamp ASC").fetchall()
		except sqlite3.Error as exc:
			raise RuntimeError(f"Failed to read error log: {exc}")

	# ------------------------------------------------------------------ #
	# Plumbing
	# ------------------------------------------------------------------ #

	def _safe_execute(self, sql: str, params: tuple = ()) -> None:
		try:
			with self._conn:
				self._conn.execute(sql, params)
		except sqlite3.Error as exc:
			raise RuntimeError(f"Database write failed ({sql[:40]}...): {exc}")

	def _scalar(self, sql: str, params: tuple = ()) -> int:
		try:
			row = self._conn.execute(sql, params).fetchone()
			return int(row[0]) if row else 0
		except sqlite3.Error as exc:
			raise RuntimeError(f"Database read failed ({sql[:40]}...): {exc}")

	def close(self) -> None:
		try:
			self._conn.close()
		except sqlite3.Error:
			pass
