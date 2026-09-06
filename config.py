"""
config.py

Central configuration objects and defaults for the Web Archiver application.
Keeping these in one place makes it easy to tune crawl behaviour without
hunting through the GUI or engine code.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class CrawlConfig:
	"""All user-configurable parameters for a single crawl session.

	Instances are built directly from the values entered in the GUI
	(see gui/main_window.py) and passed to CrawlEngine, so this class is the
	single source of truth for "what the user asked for" during a run.
	"""

	base_url: str
	output_dir: Path
	max_depth: int = 2
	max_file_size_mb: float = 20.0
	jitter_min: float = 2.0
	jitter_max: float = 5.0
	headless: bool = True
	auto_scroll: bool = True
	profile_dir: Path = Path("./browser_profile")

	def __post_init__(self):
		# Normalize string paths (e.g. typed into a QLineEdit) into Path objects.
		self.output_dir = Path(self.output_dir)
		self.profile_dir = Path(self.profile_dir)


# A small rotation of common, realistic desktop user-agents used to reduce
# the "this is definitely a bot" fingerprint that a single static UA gives off.
DEFAULT_USER_AGENTS = [
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/605.1.15 "
	"(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

# Well-known filenames written inside every output folder.
DB_FILENAME = "crawl_state.db"
REPORT_FILENAME = "completion_report.html"
