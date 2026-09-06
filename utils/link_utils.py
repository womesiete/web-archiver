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
	"""An internal link shares the same host as the crawl's Base URL,
	regardless of path (e.g. the site's homepage counts as internal even
	when Base URL points deep into a subsection)."""
	try:
		return urlsplit(url).netloc.lower() == urlsplit(base_url).netloc.lower()
	except Exception:
		return False


def path_scope_prefix(base_url: str) -> str:
	"""
	Return the directory-style path prefix that defines the crawl's scope,
	following the same convention as `wget --no-parent`: a URL must start
	with this prefix to be considered "within scope" of the Base URL.

	Examples:
		https://example.com/                         -> "/"
		https://example.com/docs/                    -> "/docs/"
		https://example.com/docs/page.html            -> "/docs/"
	"""
	path = urlsplit(base_url).path or "/"
	if path.endswith("/"):
		return path
	return path.rsplit("/", 1)[0] + "/"


def is_within_scope(url: str, base_url: str, restrict_to_path: bool) -> bool:
	"""
	True if `url` should be recursed into given the crawl's scope setting.

	Always requires the same host as `is_internal`. When `restrict_to_path`
	is enabled, also requires the URL's path to fall under the Base URL's
	own directory (or a subdirectory of it) - so a same-host link that
	jumps to a *higher* or unrelated part of the site (e.g. the homepage,
	or a different subreddit) is treated like an external link instead of
	being crawled: logged and left as a live URL, not downloaded.
	"""
	if not is_internal(url, base_url):
		return False
	if not restrict_to_path:
		return True
	prefix = path_scope_prefix(base_url)
	url_path = urlsplit(url).path or "/"
	return url_path.startswith(prefix)


def is_crawlable_scheme(url: str) -> bool:
	"""Only http(s) pages can be fetched; mailto/tel/javascript/etc. are skipped."""
	try:
		return urlsplit(url).scheme in ("http", "https")
	except Exception:
		return False
