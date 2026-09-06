"""
gui/main_window.py

PyQt6 main window: collects crawl configuration from the user and drives
CrawlEngine via qasync so that the (async) Playwright automation and the Qt
UI event loop share a single thread without either blocking the other. Long
awaits inside the engine (page loads, network-idle waits, jitter delays)
simply yield control back to Qt instead of freezing the interface, which is
why no separate QThread worker is needed here - qasync's event loop already
provides the decoupling that a manual thread would otherwise be for.
"""

import asyncio
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
	QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton,
	QSpinBox, QDoubleSpinBox, QCheckBox, QProgressBar, QPlainTextEdit,
	QLabel, QFileDialog, QGroupBox, QMessageBox,
)
from qasync import asyncSlot

from config import CrawlConfig, DB_FILENAME, REPORT_FILENAME
from database import CrawlDatabase
from crawler.engine import CrawlEngine
from crawler.auth import launch_manual_auth_session
from reporting.report_generator import generate_report


class MainWindow(QWidget):
	"""Top-level application window holding every control from the spec:
	Base URL, output folder, depth/size/jitter spinboxes, headless and
	auto-scroll toggles, and the four crawl control buttons."""

	def __init__(self):
		super().__init__()
		self.setWindowTitle("Offline Website Archiver")
		self.resize(860, 660)

		self.engine: Optional[CrawlEngine] = None
		self.db: Optional[CrawlDatabase] = None
		self._crawl_task: Optional[asyncio.Task] = None

		self._build_ui()

	# ------------------------------------------------------------------ #
	# UI construction
	# ------------------------------------------------------------------ #

	def _build_ui(self) -> None:
		root = QVBoxLayout(self)

		form_box = QGroupBox("Crawl Configuration")
		form = QFormLayout()

		self.base_url_input = QLineEdit()
		self.base_url_input.setPlaceholderText("https://example.com")
		form.addRow("Base URL:", self.base_url_input)

		output_row = QHBoxLayout()
		self.output_dir_input = QLineEdit()
		browse_btn = QPushButton("Browse...")
		browse_btn.clicked.connect(self._choose_output_dir)
		output_row.addWidget(self.output_dir_input)
		output_row.addWidget(browse_btn)
		form.addRow("Output Folder:", output_row)

		self.depth_spin = QSpinBox()
		self.depth_spin.setRange(0, 20)
		self.depth_spin.setValue(2)
		form.addRow("Crawl Depth:", self.depth_spin)

		self.max_size_spin = QDoubleSpinBox()
		self.max_size_spin.setRange(0.1, 2048.0)
		self.max_size_spin.setValue(20.0)
		self.max_size_spin.setSuffix(" MB")
		form.addRow("Max Asset Size:", self.max_size_spin)

		jitter_row = QHBoxLayout()
		self.jitter_min_spin = QDoubleSpinBox()
		self.jitter_min_spin.setRange(0.0, 60.0)
		self.jitter_min_spin.setValue(2.0)
		self.jitter_max_spin = QDoubleSpinBox()
		self.jitter_max_spin.setRange(0.0, 60.0)
		self.jitter_max_spin.setValue(5.0)
		jitter_row.addWidget(QLabel("Min (s):"))
		jitter_row.addWidget(self.jitter_min_spin)
		jitter_row.addWidget(QLabel("Max (s):"))
		jitter_row.addWidget(self.jitter_max_spin)
		form.addRow("Human Jitter:", jitter_row)

		toggle_row = QHBoxLayout()
		self.headless_check = QCheckBox("Headless")
		self.headless_check.setChecked(True)
		self.autoscroll_check = QCheckBox("Auto-Scroll")
		self.autoscroll_check.setChecked(True)
		toggle_row.addWidget(self.headless_check)
		toggle_row.addWidget(self.autoscroll_check)
		form.addRow("Options:", toggle_row)

		form_box.setLayout(form)
		root.addWidget(form_box)

		btn_row = QHBoxLayout()
		self.start_btn = QPushButton("Start Crawl")
		self.pause_btn = QPushButton("Pause")
		self.stop_btn = QPushButton("Stop")
		self.auth_btn = QPushButton("Manual Login / Auth Setup")

		self.start_btn.clicked.connect(self._on_start_clicked)
		self.pause_btn.clicked.connect(self._on_pause_clicked)
		self.stop_btn.clicked.connect(self._on_stop_clicked)
		self.auth_btn.clicked.connect(self._on_auth_clicked)

		self.pause_btn.setEnabled(False)
		self.stop_btn.setEnabled(False)

		for btn in (self.start_btn, self.pause_btn, self.stop_btn, self.auth_btn):
			btn_row.addWidget(btn)
		root.addLayout(btn_row)

		self.status_label = QLabel("Idle")
		root.addWidget(self.status_label)

		self.progress_bar = QProgressBar()
		self.progress_bar.setRange(0, 100)
		root.addWidget(self.progress_bar)

		self.log_output = QPlainTextEdit()
		self.log_output.setReadOnly(True)
		root.addWidget(self.log_output)

	# ------------------------------------------------------------------ #
	# Helpers
	# ------------------------------------------------------------------ #

	def _choose_output_dir(self) -> None:
		directory = QFileDialog.getExistingDirectory(self, "Select Output Folder")
		if directory:
			self.output_dir_input.setText(directory)

	def _append_log(self, message: str) -> None:
		self.log_output.appendPlainText(message)

	def _build_config(self) -> CrawlConfig:
		return CrawlConfig(
			base_url=self.base_url_input.text().strip(),
			output_dir=Path(self.output_dir_input.text().strip() or "./archive_output"),
			max_depth=self.depth_spin.value(),
			max_file_size_mb=self.max_size_spin.value(),
			jitter_min=self.jitter_min_spin.value(),
			jitter_max=self.jitter_max_spin.value(),
			headless=self.headless_check.isChecked(),
			auto_scroll=self.autoscroll_check.isChecked(),
		)

	def _set_controls_running(self, running: bool) -> None:
		self.start_btn.setEnabled(not running)
		self.pause_btn.setEnabled(running)
		self.stop_btn.setEnabled(running)
		for widget in (
			self.base_url_input, self.output_dir_input, self.depth_spin,
			self.max_size_spin, self.jitter_min_spin, self.jitter_max_spin,
			self.headless_check, self.autoscroll_check,
		):
			widget.setEnabled(not running)

	# ------------------------------------------------------------------ #
	# Button handlers
	# ------------------------------------------------------------------ #

	@asyncSlot()
	async def _on_start_clicked(self) -> None:
		config = self._build_config()
		if not config.base_url:
			QMessageBox.warning(self, "Missing Base URL", "Please enter a Base URL to crawl.")
			return
		if not self.output_dir_input.text().strip():
			QMessageBox.warning(self, "Missing Output Folder", "Please choose an output folder.")
			return

		try:
			config.output_dir.mkdir(parents=True, exist_ok=True)
			self.db = CrawlDatabase(config.output_dir / DB_FILENAME)
			# Recover any page left mid-flight by a prior crash or force-quit
			# so it re-enters the pending queue instead of being stuck.
			self.db.reset_stalled_pages()
		except Exception as exc:
			QMessageBox.critical(
				self, "Startup Error",
				f"Could not initialize output folder or database:\n{exc}",
			)
			return

		self.engine = CrawlEngine(config, self.db)
		self.engine.log_signal.connect(self._append_log)
		self.engine.status_signal.connect(self.status_label.setText)
		self.engine.progress_signal.connect(self._on_progress)
		self.engine.finished_signal.connect(self._on_crawl_finished)

		self._set_controls_running(True)
		self.pause_btn.setText("Pause")
		self._append_log(f"Starting/resuming crawl -> {config.output_dir}")

		self._crawl_task = asyncio.ensure_future(self.engine.run())

	def _on_pause_clicked(self) -> None:
		if not self.engine:
			return
		if self.pause_btn.text() == "Pause":
			self.engine.pause()
			self.pause_btn.setText("Resume")
		else:
			self.engine.resume()
			self.pause_btn.setText("Pause")

	def _on_stop_clicked(self) -> None:
		if self.engine:
			self.engine.stop()

	@asyncSlot()
	async def _on_auth_clicked(self) -> None:
		config = self._build_config()
		self.auth_btn.setEnabled(False)
		try:
			await launch_manual_auth_session(config.base_url, config.profile_dir, self._append_log)
		except Exception as exc:
			self._append_log(f"Auth session error: {exc}")
		finally:
			self.auth_btn.setEnabled(True)

	def _on_progress(self, done: int, total: int) -> None:
		if total > 0:
			self.progress_bar.setValue(int(done / total * 100))
		base_status = self.status_label.text().split(" (")[0]
		self.status_label.setText(f"{base_status} ({done}/{total} pages)")

	def _on_crawl_finished(self, completed_normally: bool) -> None:
		self._set_controls_running(False)
		self.pause_btn.setText("Pause")

		if self.db:
			try:
				report_path = self.db.db_path.parent / REPORT_FILENAME
				generate_report(self.db, report_path)
				self._append_log(f"Report written to {report_path}")
			except Exception as exc:
				self._append_log(f"Could not generate report: {exc}")

		self._append_log("Crawl finished." if completed_normally else "Crawl stopped by user.")
