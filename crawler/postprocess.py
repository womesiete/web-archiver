"""
crawler/postprocess.py

Second-pass link resolution. During capture, every anchor href is normalized
to an absolute URL (see crawler/capture.py _rewrite_anchor_hrefs). Once the
crawl frontier is exhausted we know exactly which of those absolute URLs
were themselves successfully archived, so this pass rewrites matching hrefs
into relative local file paths - the final step that makes the output
folder a fully self-contained, clickable offline copy of the site.

Internal links that point beyond the configured crawl depth, or that failed
to download, are intentionally left as live absolute URLs so the archive
degrades gracefully (the link still works if the reader is online) rather
than pointing at a file that was never created.
"""

from pathlib import Path

from bs4 import BeautifulSoup

from utils import paths


def rewrite_internal_links(db, output_dir, logger) -> None:
	"""Rewrite <a href> values across every archived page so that links
	between successfully-crawled pages become relative local paths.

	`output_dir` is accepted for interface symmetry with the rest of the
	pipeline (and potential future use, e.g. validating paths stay within
	the archive root) even though the current implementation only needs the
	per-page local_path values already stored in the database.
	"""
	url_to_local = {
		row["url"]: Path(row["local_path"])
		for row in db.get_all_pages()
		if row["status"] == "completed" and row["local_path"]
	}

	for url, local_path in url_to_local.items():
		if not local_path.exists():
			continue
		try:
			markup = local_path.read_text(encoding="utf-8")
		except OSError as exc:
			logger(f"Skipping link rewrite for {local_path}: {exc}")
			continue

		soup = BeautifulSoup(markup, "lxml")
		changed = False
		for tag in soup.find_all("a", href=True):
			target = url_to_local.get(tag["href"])
			if target is not None and target != local_path:
				tag["href"] = paths.relative_link(local_path, target)
				changed = True

		if changed:
			try:
				local_path.write_text(str(soup), encoding="utf-8")
			except OSError as exc:
				logger(f"Could not save rewritten links for {local_path}: {exc}")
