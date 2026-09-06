"""
utils/link_utils.py

Small, dependency-free URL helpers used by the crawler to resolve relative
links, decide what is "internal" (and therefore worth recursing into), and
filter out non-fetchable schemes like mailto: or javascript:.
"""

from urllib.parse import urljoin, urlsplit, urlunsplit


def normalize_url(url: str, base: str = None) -> str:
	"""
	Resolve a possibly-relative URL against a base page URL and strip the
	fragment identifier, since fragments never require a separate fetch and
	would otherwise cause the same page to be queued multiple times.
	"""
	if base:
		url = urljoin(base, url)
	parts = urlsplit(url)
	return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def is_internal(url: str, base_url: str) -> bool:
	"""An internal link shares the same host as the crawl's Base URL."""
	try:
		return urlsplit(url).netloc.lower() == urlsplit(base_url).netloc.lower()
	except Exception:
		return False


def is_crawlable_scheme(url: str) -> bool:
	"""Only http(s) pages can be fetched; mailto/tel/javascript/etc. are skipped."""
	try:
		return urlsplit(url).scheme in ("http", "https")
	except Exception:
		return False
