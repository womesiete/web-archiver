"""
crawler/auth.py

Launches a headful, persistent Chromium context so the user can log in to a
site (or solve a CAPTCHA) by hand. Because the automated crawler reuses the
same user-data directory (see CrawlEngine._launch_context), any cookies or
storage state captured here are automatically inherited by future crawls -
no export/import step required.
"""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def launch_manual_auth_session(start_url: str, profile_dir: Path, logger) -> None:
	"""Open a visible browser window and block until the user closes every tab."""
	profile_dir = Path(profile_dir)

	try:
		profile_dir.mkdir(parents=True, exist_ok=True)
	except OSError as exc:
		logger(f"Could not create browser profile directory: {exc}")
		return

	logger("Launching manual authentication browser. Close the window when finished.")
	try:
		async with async_playwright() as p:
			context = await p.chromium.launch_persistent_context(
				user_data_dir=str(profile_dir),
				headless=False,
			)
			page = context.pages[0] if context.pages else await context.new_page()
			try:
				if start_url:
					await page.goto(start_url, timeout=30000)
			except Exception as exc:
				logger(f"Could not navigate to {start_url}: {exc}")

			await _wait_for_context_close(context)
	except Exception as exc:
		logger(f"Manual authentication session failed: {exc}")
		return

	logger("Authentication session closed. Cookies and storage saved to profile.")


async def _wait_for_context_close(context) -> None:
	"""Resolve once the user closes the persistent browser context/window."""
	future: asyncio.Future = asyncio.get_event_loop().create_future()

	def _on_close(*_args):
		if not future.done():
			future.set_result(True)

	context.on("close", _on_close)
	await future
