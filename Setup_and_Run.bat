@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Local Register Search - Setup and Run
pushd "%~dp0"
if errorlevel 1 (
    echo ERROR: Cannot open the extracted project folder.
    pause
    exit /b 1
)
set "PYTHONHOME="
set "PYTHONPATH="
set "MAIN_FILE=run.py"
set "REQUIREMENTS_FILE=requirements.txt"
set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"
set "VENV_PYTHONW=%CD%\.venv\Scripts\pythonw.exe"
set "PYTHON_EXE="
set "PYTHON_DIR=%LOCALAPPDATA%\Programs\Python\Python312"
set "INSTALLER=%CD%\.bootstrap\python-3.12.10-amd64.exe"
set "FAIL_MESSAGE="

if not exist "%MAIN_FILE%" (
    set "FAIL_MESSAGE=run.py is missing. Extract the complete ZIP before running this file."
    goto :fail
)
if not exist "%REQUIREMENTS_FILE%" (
    set "FAIL_MESSAGE=requirements.txt is missing. Extract the complete ZIP again."
    goto :fail
)

rem Fast path: no downloads, environment creation or pip on repeat runs.
if not exist "installed.flag" goto :check_python
if not exist "%VENV_PYTHONW%" goto :check_python
call :usable_python "%VENV_PYTHON%"
if errorlevel 1 goto :check_python
"%VENV_PYTHON%" -c "import hashlib,pathlib,sys; p=pathlib.Path; sys.exit(0 if p('installed.flag').read_text().strip()==hashlib.sha256(p('requirements.txt').read_bytes()).hexdigest() else 1)" >nul 2>&1
if errorlevel 1 goto :check_python
goto :launch

:check_python
echo [1/4] Checking Python...
rem Print the executable only if it is a compatible, 64-bit interpreter.
for /f "delims=" %%P in ('py -3 -c "import sys,struct; sys.exit(1) if not (sys.version_info.major==3 and sys.version_info.minor in (12,13,14) and struct.calcsize('P')==8) else print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if defined PYTHON_EXE goto :python_found
for /f "delims=" %%P in ('python -c "import sys,struct; sys.exit(1) if not (sys.version_info.major==3 and sys.version_info.minor in (12,13,14) and struct.calcsize('P')==8) else print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if defined PYTHON_EXE goto :python_found
rem PATH is not refreshed inside an already open CMD after installation.
call :usable_python "%PYTHON_DIR%\python.exe"
if errorlevel 1 goto :download_python
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
goto :python_found

:download_python
echo [2/4] Downloading Python...
where curl.exe >nul 2>&1
if errorlevel 1 (
    set "FAIL_MESSAGE=Windows curl.exe is unavailable. Update Windows 10/11 and retry."
    goto :fail
)
if not exist ".bootstrap" mkdir ".bootstrap"
if not exist ".bootstrap" (
    set "FAIL_MESSAGE=Cannot write to this folder. Extract the ZIP into Documents or Desktop."
    goto :fail
)
curl.exe --fail --location --progress-bar --retry 3 --connect-timeout 30 --max-time 900 --output "%INSTALLER%" "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
if errorlevel 1 (
    set "FAIL_MESSAGE=Python download failed. Check your internet connection and run this file again."
    goto :fail
)
echo Installing Python 3.12.10 64-bit for the current user. Administrator rights are not required.
start "" /wait "%INSTALLER%" /passive InstallAllUsers=0 PrependPath=1 Include_launcher=0 InstallLauncherAllUsers=0 Include_pip=1 Include_test=0 Include_tcltk=0 Include_doc=0 Shortcuts=0 TargetDir="%PYTHON_DIR%"
set "INSTALL_RESULT=%ERRORLEVEL%"
if "%INSTALL_RESULT%"=="0" goto :verify_install
if "%INSTALL_RESULT%"=="3010" goto :verify_install
set "FAIL_MESSAGE=Python installation failed or was cancelled. Installer exit code: %INSTALL_RESULT%."
goto :fail

:verify_install
call :usable_python "%PYTHON_DIR%\python.exe"
if errorlevel 1 (
    set "FAIL_MESSAGE=Python did not become available after installation. Restart Windows and retry."
    goto :fail
)
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
goto :environment

:python_found
echo Compatible 64-bit Python found: "%PYTHON_EXE%"
echo [2/4] Downloading Python... skipped; compatible Python is already installed.

:environment
echo [3/4] Creating environment...
if exist ".venv" goto :verify_environment
"%PYTHON_EXE%" -m venv ".venv"
if errorlevel 1 (
    set "FAIL_MESSAGE=Could not create .venv. Check folder permissions and available disk space."
    goto :fail
)

:verify_environment
call :usable_python "%VENV_PYTHON%"
if errorlevel 1 (
    set "FAIL_MESSAGE=The .venv environment is incomplete or incompatible. Remove .venv and installed.flag from this extracted package, then retry."
    goto :fail
)
if not exist "%VENV_PYTHONW%" (
    set "FAIL_MESSAGE=pythonw.exe is missing from .venv. Remove .venv and installed.flag, then retry."
    goto :fail
)

echo [4/4] Installing libraries...
"%VENV_PYTHON%" -m pip --version >nul 2>&1
if not errorlevel 1 goto :install_libraries
"%VENV_PYTHON%" -m ensurepip --upgrade
if errorlevel 1 (
    set "FAIL_MESSAGE=Could not prepare pip inside .venv. Remove .venv and retry."
    goto :fail
)

:install_libraries
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --progress-bar on -r "%REQUIREMENTS_FILE%"
if errorlevel 1 (
    set "FAIL_MESSAGE=Library installation failed. Check your internet connection and disk space, then retry."
    goto :fail
)
"%VENV_PYTHON%" -c "from local_register_search.ui import main; import xlrd"
if errorlevel 1 (
    set "FAIL_MESSAGE=The installed libraries or application could not be loaded. Read the error above and retry setup."
    goto :fail
)
"%VENV_PYTHON%" -c "import hashlib,pathlib; p=pathlib.Path; p('installed.flag').write_text(hashlib.sha256(p('requirements.txt').read_bytes()).hexdigest()+'\n')"
if errorlevel 1 (
    set "FAIL_MESSAGE=Cannot save installed.flag. Move the extracted package to a writable folder and retry."
    goto :fail
)

:launch
echo Starting Local Register Search...
"%VENV_PYTHON%" -c "from local_register_search.ui import main; import xlrd" >nul 2>&1
if errorlevel 1 (
    set "FAIL_MESSAGE=The application or libraries cannot be loaded. Remove installed.flag and retry; setup will repair the libraries."
    goto :fail
)
start "" "%VENV_PYTHONW%" "%MAIN_FILE%" %*
if errorlevel 1 (
    set "FAIL_MESSAGE=Could not start the application. Check that the complete ZIP was extracted."
    goto :fail
)
popd
exit /b 0

:usable_python
if not exist "%~1" exit /b 1
"%~1" -c "import sys,struct; sys.exit(0 if (3,12)<=sys.version_info[:2]<(3,15) and struct.calcsize('P')==8 else 1)" >nul 2>&1
exit /b %ERRORLEVEL%

:fail
echo.
echo ERROR: %FAIL_MESSAGE%
echo.
echo Setup did not finish. Keep this message visible and run Setup_and_Run.bat again after fixing the problem.
pause
popd
exit /b 1
