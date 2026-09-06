"""
main.py

Application entry point. Wires PyQt6's event loop together with asyncio (via
qasync) so that Playwright's async API can drive the crawl directly on the
UI thread without ever blocking it - long-running awaits (page loads,
network-idle waits, jitter delays) simply yield back to Qt instead of
requiring a separate worker thread.

Also registers the organization/application name QSettings uses to locate
its backing store (Windows Registry, macOS plist, or an INI file on Linux)
so form values persist between sessions - see gui/main_window.py for the
load/save logic itself.
"""

import sys
import asyncio

from PyQt6.QtWidgets import QApplication
from qasync import QEventLoop

from config import ORG_NAME, APP_NAME
from gui.main_window import MainWindow


def main() -> None:
	app = QApplication(sys.argv)

	# QSettings() calls anywhere in the app (with no arguments) will use
	# these names to find/create their backing store automatically.
	app.setOrganizationName(ORG_NAME)
	app.setApplicationName(APP_NAME)

	# Replace the default asyncio event loop with one driven by Qt's own
	# event loop, so `await` calls anywhere in the app (crawler engine, auth
	# flow) cooperate with the GUI instead of freezing it.
	loop = QEventLoop(app)
	asyncio.set_event_loop(loop)

	window = MainWindow()
	window.show()

	with loop:
		loop.run_forever()


if __name__ == "__main__":
	main()
