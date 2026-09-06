"""
crawler/singlefile_bridge.py

Optional integration with the SingleFile command-line tool
(npm i -g single-file-cli), which can bundle a fully-rendered page - including
inlined CSS, fonts, and images as data URIs - into one self-contained HTML
file. When SingleFile is not installed, or fails for a particular URL, the
caller (CrawlEngine) falls back to the built-in Playwright DOM capture
backend in crawler/capture.py, which offers finer-grained control over the
asset size safeguard and internal-link rewriting.
"""

import asyncio
import shutil
from pathlib import Path


def is_singlefile_available() -> bool:
	"""Check whether the SingleFile CLI binary is discoverable on PATH."""
	return shutil.which("single-file") is not None


async def capture_with_singlefile(url: str, output_path: Path, profile_dir: Path, logger) -> bool:
	"""
	Attempt to bundle a page into a single self-contained HTML file using the
	SingleFile CLI, reusing cookies from the persistent auth profile so
	authenticated pages can still be archived.

	Returns True on success, False if SingleFile is unavailable or fails, in
	which case the caller should use the Playwright DOM capture fallback.
	"""
	if not is_singlefile_available():
		return False

	try:
		output_path.parent.mkdir(parents=True, exist_ok=True)
	except OSError as exc:
		logger(f"Could not prepare output directory for SingleFile: {exc}")
		return False

	cmd = [
		"single-file",
		url,
		str(output_path),
		f"--browser-user-data-dir={profile_dir}",
		"--browser-headless=true",
	]

	try:
		proc = await asyncio.create_subprocess_exec(
			*cmd,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
		)
		stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
	except asyncio.TimeoutError:
		logger(f"SingleFile timed out capturing {url}")
		return False
	except Exception as exc:
		logger(f"SingleFile capture failed for {url}: {exc}")
		return False

	if proc.returncode == 0 and output_path.exists():
		return True

	logger(
		"SingleFile exited with code "
		f"{proc.returncode} for {url}: {stderr.decode(errors='ignore')[:300]}"
	)
	return False
