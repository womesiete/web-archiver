"""
reporting/report_generator.py

Builds the styled completion_report.html summary described in the spec:
counts of archived pages, the full list of external links encountered,
assets skipped for exceeding the size limit, and any failed/error URLs.
"""

import html
from pathlib import Path

REPORT_CSS = """
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; color: #1a1a1a; background: #fafafa; }
h1 { color: #1d4ed8; }
h2 { border-bottom: 2px solid #e5e7eb; padding-bottom: 0.3rem; margin-top: 2.5rem; }
.summary { display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 1.5rem 0; }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 1rem 1.5rem; min-width: 160px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
.card .num { font-size: 1.8rem; font-weight: 700; }
.card .label { color: #6b7280; font-size: 0.85rem; }
table { border-collapse: collapse; width: 100%; margin-top: 0.75rem; background: #fff; }
th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #eee; font-size: 0.9rem; word-break: break-all; }
th { background: #f3f4f6; }
.empty { color: #9ca3af; font-style: italic; padding: 0.5rem 0; }
a { color: #1d4ed8; text-decoration: none; }
a:hover { text-decoration: underline; }
"""


def _escape(value) -> str:
	return html.escape(str(value)) if value is not None else ""


def _table(headers, rows) -> str:
	if not rows:
		return "<p class='empty'>None.</p>"
	head_html = "".join(f"<th>{_escape(h)}</th>" for h in headers)
	body_html = ""
	for row in rows:
		cells = "".join(f"<td>{_escape(cell)}</td>" for cell in row)
		body_html += f"<tr>{cells}</tr>"
	return f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"


def generate_report(db, output_path: Path) -> Path:
	"""Query the crawl database and write a self-contained HTML report."""
	output_path = Path(output_path)

	pages = db.get_all_pages()
	completed_pages = [p for p in pages if p["status"] == "completed"]
	failed_pages = [p for p in pages if p["status"] == "failed"]
	external_links = db.get_external_links()
	skipped_assets = db.get_skipped_assets()

	summary_cards = [
		("Internal Pages Captured", len(completed_pages)),
		("External Links Found", len(external_links)),
		("Assets Skipped (Oversized)", len(skipped_assets)),
		("Failed / Error URLs", len(failed_pages)),
	]
	summary_html = "".join(
		f"<div class='card'><div class='num'>{num}</div><div class='label'>{_escape(label)}</div></div>"
		for label, num in summary_cards
	)

	pages_table = _table(
		["URL", "Local Path", "Title", "Depth"],
		[(p["url"], p["local_path"], p["title"], p["depth"]) for p in completed_pages],
	)
	external_table = _table(
		["External URL", "Found On Page", "Link Text"],
		[(e["url"], e["found_on_page"], e["link_text"]) for e in external_links],
	)
	skipped_table = _table(
		["Asset URL", "Size (bytes)"],
		[(a["url"], a["size_bytes"]) for a in skipped_assets],
	)
	errors_table = _table(
		["URL", "Error"],
		[(p["url"], p["error_message"]) for p in failed_pages],
	)

	document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Website Archive - Completion Report</title>
<style>{REPORT_CSS}</style>
</head>
<body>
<h1>Website Archive Completion Report</h1>
<div class="summary">{summary_html}</div>

<h2>Internal Pages Captured</h2>
{pages_table}

<h2>External Links (kept live)</h2>
{external_table}

<h2>Assets Skipped for Exceeding Size Limit</h2>
{skipped_table}

<h2>Failed / Error URLs</h2>
{errors_table}

</body>
</html>
"""

	try:
		output_path.write_text(document, encoding="utf-8")
	except OSError as exc:
		raise RuntimeError(f"Could not write report to {output_path}: {exc}")

	return output_path
