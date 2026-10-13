LOCAL REGISTER SEARCH - WINDOWS ONE-CLICK SETUP

1. Extract the entire ZIP into a writable folder, such as Documents or Desktop.
2. Open the extracted Windows_OneClick folder.
3. Double-click Setup_and_Run.bat. Do not run it from inside the ZIP.

First run needs an internet connection. Setup displays its progress, downloads
Python 3.12.10 (64-bit) from python.org if compatible Python is unavailable,
installs it for your Windows account without administrator rights, creates a
local .venv and installs the pinned application libraries.

Later runs skip setup and open the app using pythonw, with no persistent black
terminal window. The setup console may appear briefly before the app opens.
Use the same Setup_and_Run.bat each time. Keep the folder in the same location.

If Windows SmartScreen appears for this trusted package, select:
More info -> Run anyway.

If setup fails, the console displays an error and waits for a key press.
Read the error, resolve the problem, and double-click the BAT again.
The installed.flag is written only after successful dependency installation.
If libraries are damaged, remove installed.flag and retry. If the environment
is damaged or this folder was moved, remove .venv and installed.flag and retry.

Requires 64-bit Windows 10/11. Existing 64-bit Python 3.12-3.14 is reused.
Downloads and pip progress stay visible on the first run. The application
itself searches local workbooks and does not need internet access after setup.

App entry point: run.py
Pinned runtime requirements: PySide6 6.11.1, openpyxl 3.1.5, xlrd 2.0.2.
Python's standard library needs no separate installation. Development-only
PyInstaller and the test-only xlwt package are not required to run the app.

Keep run.py, requirements.txt and the entire local_register_search folder
together with Setup_and_Run.bat. Do not send .venv, installed.flag or .bootstrap
to another user: their first launch creates these locally.
