"""
crawler/capture.py

Fallback page-bundling backend used when the external SingleFile CLI is not
installed or fails for a given URL (see crawler/singlefile_bridge.py).
Captures the fully-rendered DOM via Playwright and rewrites asset references
to point at locally-saved copies, subject to the max asset size safeguard
already applied by AssetManager.

Internal <a href> links are normalized to absolute URLs here; converting the
ones that point at other successfully-archived pages into relative local
paths is deferred to a later pass (see crawler/postprocess.py) that runs
once the full site map is known.
"""

from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from utils import paths

# HTML attributes that can reference a locally-downloadable asset, keyed by
# the tag name they appear on.
ASSET_ATTRS = {
	"img": ["src"],
	"source": ["src"],
	"link": ["href"],
	"script": ["src"],
	"video": ["src", "poster"],
	"audio": ["src"],
}


async def capture_page(page, url: str, output_dir: Path, asset_manager, logger) -> Path:
	"""Render the current page's DOM to a portable local HTML file."""
	html = await page.content()
	soup = BeautifulSoup(html, "lxml")

	# A pre-existing <base> tag would interfere with the relative asset/link
	# paths we are about to write, since browsers resolve every relative URL
	# in the document against it. Strip it and rely on absolute resolution
	# against the real page URL instead.
	for base_tag in soup.find_all("base"):
		base_tag.decompose()

	local_path = paths.url_to_local_file(url, output_dir)
	paths.ensure_parent_dir(local_path)

	_rewrite_assets(soup, url, local_path, asset_manager)
	_rewrite_anchor_hrefs(soup, url)

	try:
		local_path.write_text(str(soup), encoding="utf-8")
	except OSError as exc:
		logger(f"Could not write {local_path}: {exc}")
		raise

	return local_path


def _rewrite_assets(soup: BeautifulSoup, page_url: str, page_local_path: Path, asset_manager) -> None:
	"""Point asset attributes at their locally-saved copy when one exists."""
	for tag_name, attrs in ASSET_ATTRS.items():
		for tag in soup.find_all(tag_name):
			for attr in attrs:
				if not tag.get(attr):
					continue
				absolute = urljoin(page_url, tag[attr])
				local_rel = asset_manager.get_relative_path_for(absolute, page_local_path)
				# Not saved locally (oversized, failed, or never observed) -
				# leave pointing at the live URL so the page still renders.
				tag[attr] = local_rel if local_rel else absolute


def _rewrite_anchor_hrefs(soup: BeautifulSoup, page_url: str) -> None:
	"""Normalize every <a href> to an absolute URL for the later link pass."""
	for tag in soup.find_all("a", href=True):
		href = tag["href"].strip()
		if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
			continue
		tag["href"] = urljoin(page_url, href)
