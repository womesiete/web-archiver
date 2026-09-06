"""
crawler/stealth_utils.py

Anti-bot evasion helpers: user-agent rotation, playwright-stealth patching,
human-like jitter delays between page requests, and lazy-load-triggering
auto-scroll used before capturing a page's DOM.
"""

import asyncio
import random

from config import DEFAULT_USER_AGENTS


def random_user_agent() -> str:
	"""Pick a plausible desktop browser user-agent string."""
	return random.choice(DEFAULT_USER_AGENTS)


async def apply_stealth(page) -> None:
	"""
	Apply playwright-stealth patches to reduce headless-browser fingerprints
	(navigator.webdriver, missing plugins, WebGL vendor strings, etc.) that
	many anti-bot systems check for before returning a 403.
	"""
	from playwright_stealth import Stealth

	await Stealth().apply_stealth_async(page)


async def human_delay(min_seconds: float, max_seconds: float) -> None:
	"""Sleep for a random duration to imitate human browsing pace between
	page requests. Values are sorted so the spinboxes can be set in either
	order without raising an exception."""
	low, high = sorted((max(0.0, min_seconds), max(0.0, max_seconds)))
	if high <= 0:
		return
	await asyncio.sleep(random.uniform(low, high))


async def auto_scroll(page, max_scrolls: int = 15, pause_seconds: float = 0.35) -> None:
	"""Incrementally scroll to the bottom of the page to trigger lazy-loaded
	images and infinite-scroll content before the DOM is captured."""
	previous_height = -1
	for _ in range(max_scrolls):
		try:
			current_height = await page.evaluate("document.body.scrollHeight")
			if current_height == previous_height:
				break
			await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
			previous_height = current_height
			await asyncio.sleep(pause_seconds)
		except Exception:
			# Page may have navigated away or the DOM is otherwise unavailable -
			# not fatal, just stop scrolling.
			break

	# Scroll back to top so the archived page opens in its natural state.
	try:
		await page.evaluate("window.scrollTo(0, 0)")
	except Exception:
		pass
