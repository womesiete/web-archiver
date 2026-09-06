"""
crawler/engine.py

Core crawl orchestration. Runs as an asyncio coroutine inside the Qt event
loop (via qasync), driving a single Playwright browser context through a
breadth-first crawl whose frontier is persisted in SQLite so that crawls can
be paused, stopped, and resumed without losing progress or re-downloading
completed pages.
"""

import datetime

from PyQt6.QtCore import QObject, pyqtSignal
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
import asyncio

from config import CrawlConfig
from database import CrawlDatabase
from crawler.stealth_utils import apply_stealth, random_user_agent, human_delay, auto_scroll
from crawler.asset_manager import AssetManager
from crawler.singlefile_bridge import capture_with_singlefile
from crawler import capture as capture_backend
from crawler import postprocess
from utils.link_utils import normalize_url, is_internal, is_crawlable_scheme
from utils import paths


class CrawlEngine(QObject):
	"""
	Drives the recursive crawl of a website into a portable local HTML
	archive. All mutable progress state lives in the SQLite database, so the
	engine can resume a previously interrupted crawl by simply being run
	again against the same output folder.
	"""

	log_signal = pyqtSignal(str)
	progress_signal = pyqtSignal(int, int)
	status_signal = pyqtSignal(str)
	finished_signal = pyqtSignal(bool)  # True = completed normally, False = stopped early

	def __init__(self, config: CrawlConfig, db: CrawlDatabase):
		super().__init__()
		self.config = config
		self.db = db

		# asyncio.Event doubles as our pause/resume gate: set() == running,
		# clear() == paused (the crawl loop awaits it before each new page).
		self._pause_event = asyncio.Event()
		self._pause_event.set()
		self._stop_requested = False

		self.asset_manager = AssetManager(
			db=self.db,
			output_dir=self.config.output_dir,
			max_size_bytes=int(self.config.max_file_size_mb * 1024 * 1024),
			logger=self._log,
		)

	# ------------------------------------------------------------------ #
	# Public controls (called from the GUI thread's event loop)
	# ------------------------------------------------------------------ #

	def pause(self) -> None:
		"""Pause the crawl; the in-flight page is allowed to finish first."""
		self._pause_event.clear()
		self.status_signal.emit("Paused")

	def resume(self) -> None:
		"""Resume a previously paused crawl."""
		self._pause_event.set()
		self.status_signal.emit("Running")

	def stop(self) -> None:
		"""Request a graceful stop; the in-flight page is allowed to finish."""
		self._stop_requested = True
		self._pause_event.set()  # unblock a paused loop so it can exit promptly
		self.status_signal.emit("Stopping...")

	# ------------------------------------------------------------------ #
	# Internal helpers
	# ------------------------------------------------------------------ #

	def _log(self, message: str) -> None:
		timestamp = datetime.datetime.now().strftime("%H:%M:%S")
		self.log_signal.emit(f"[{timestamp}] {message}")

	async def _launch_context(self, playwright):
		"""
		Launch a persistent Chromium context so cookies/local storage
		captured via the manual "Auth Setup" flow are automatically
		inherited by this crawl.
		"""
		profile_dir = str(self.config.profile_dir)
		context = await playwright.chromium.launch_persistent_context(
			user_data_dir=profile_dir,
			headless=self.config.headless,
			user_agent=random_user_agent(),
			viewport={"width": 1366, "height": 900},
			ignore_https_errors=True,
		)
		return context

	# ------------------------------------------------------------------ #
	# Main entry point
	# ------------------------------------------------------------------ #

	async def run(self) -> None:
		"""Main crawl loop. Emits finished_signal(True) on natural
		completion, or finished_signal(False) if stopped early by the user
		or a fatal error."""
		self._log(f"Starting crawl of {self.config.base_url}")
		self.status_signal.emit("Running")

		# Seed the frontier with the base URL. add_page() is a no-op if this
		# URL was already queued/completed in a prior session, which is what
		# makes re-running against the same output folder a "resume".
		self.db.add_page(normalize_url(self.config.base_url), depth=0)

		completed_normally = True

		try:
			async with async_playwright() as p:
				context = await self._launch_context(p)
				page = await context.new_page()

				try:
					await apply_stealth(page)
				except Exception as exc:
					self._log(f"Stealth setup skipped ({exc})")

				# Route every network response through the asset size safeguard.
				page.on("response", self.asset_manager.on_response)

				while True:
					if self._stop_requested:
						completed_normally = False
						break

					await self._pause_event.wait()
					if self._stop_requested:
						completed_normally = False
						break

					row = self.db.get_next_pending_page()
					if row is None:
						break  # Frontier exhausted - crawl is complete.

					await self._process_page(page, row)

					total = self.db.count_total_pages()
					done = self.db.count_completed_pages() + self.db.count_failed_pages()
					self.progress_signal.emit(done, total)

					# Human jitter between requests to reduce anti-bot risk.
					await human_delay(self.config.jitter_min, self.config.jitter_max)

				await context.close()
		except Exception as exc:
			self._log(f"Fatal crawler error: {exc}")
			completed_normally = False

		# Final pass: convert absolute hrefs between crawled pages into
		# relative local paths now that the full page map is known.
		try:
			self._log("Rewriting internal links for offline portability...")
			postprocess.rewrite_internal_links(self.db, self.config.output_dir, self._log)
		except Exception as exc:
			self._log(f"Link rewriting failed: {exc}")

		self.status_signal.emit("Completed" if completed_normally else "Stopped")
		self.finished_signal.emit(completed_normally)

	async def _process_page(self, page, row) -> None:
		"""Fetch, render, and archive a single page, then enqueue newly
		discovered internal links and log external ones."""
		page_id, url, depth = row["id"], row["url"], row["depth"]
		self.db.mark_in_progress(page_id)
		self._log(f"Crawling (depth {depth}): {url}")

		try:
			await page.goto(url, timeout=30000, wait_until="domcontentloaded")

			if self.config.auto_scroll:
				await auto_scroll(page)

			# Give lazy-loaded network requests a moment to settle before we
			# capture the DOM and inspect it for links/assets.
			try:
				await page.wait_for_load_state("networkidle", timeout=8000)
			except PlaywrightTimeoutError:
				pass  # Some sites never go idle (polling/websockets) - proceed anyway.

			title = await page.title()

			local_path = paths.url_to_local_file(url, self.config.output_dir)
			paths.ensure_parent_dir(local_path)

			# Prefer the external SingleFile bundler when available; fall
			# back to the built-in Playwright DOM capture otherwise.
			bundled = await capture_with_singlefile(
				url, local_path, self.config.profile_dir, self._log
			)
			if not bundled:
				await capture_backend.capture_page(
					page=page,
					url=url,
					output_dir=self.config.output_dir,
					asset_manager=self.asset_manager,
					logger=self._log,
				)

			# Discover links for recursion / external-link logging regardless
			# of which capture backend produced the saved file.
			links = await self._extract_links(page, url)
			for link_url, link_text in links:
				if not is_crawlable_scheme(link_url):
					continue
				if is_internal(link_url, self.config.base_url):
					if depth < self.config.max_depth:
						self.db.add_page(link_url, depth + 1)
				else:
					self.db.add_external_link(link_url, url, link_text)

			self.db.mark_completed(page_id, str(local_path), title)
			self._log(f"Saved: {url} -> {local_path}")

		except Exception as exc:
			self._log(f"Failed: {url} ({exc})")
			self.db.mark_failed(page_id, str(exc))
			self.db.log_error(url, str(exc))

	async def _extract_links(self, page, current_url):
		"""Return a list of (absolute_url, link_text) tuples found on the page."""
		try:
			html = await page.content()
		except Exception:
			return []

		soup = BeautifulSoup(html, "lxml")
		results = []
		for tag in soup.find_all("a", href=True):
			href = tag["href"].strip()
			if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
				continue
			absolute = normalize_url(href, base=current_url)
			results.append((absolute, tag.get_text(strip=True)[:120]))
		return results
