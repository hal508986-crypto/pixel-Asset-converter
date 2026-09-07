@echo off
setlocal EnableExtensions

rem Resolve the repository root so double-clicking this file works from anywhere.
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%src"
set "PYTHONUTF8=1"

rem Prefer the repository-local environment, then the Python 3.10 launcher.
if exist "%ROOT%.venv\Scripts\python.exe" goto :run_venv

where py >nul 2>&1
if errorlevel 1 goto :try_python
py -3.10 -c "import sys" >nul 2>&1
if errorlevel 1 goto :try_python
goto :run_py310

:run_venv
"%ROOT%.venv\Scripts\python.exe" -m pixel_tile_compiler gui
set "EXIT_CODE=%ERRORLEVEL%"
goto :finish

:run_py310
py -3.10 -m pixel_tile_compiler gui
set "EXIT_CODE=%ERRORLEVEL%"
goto :finish

:try_python
where python >nul 2>&1
if errorlevel 1 goto :missing_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :missing_python
python -m pixel_tile_compiler gui
set "EXIT_CODE=%ERRORLEVEL%"
goto :finish

:missing_python
echo Python 3.10 or newer was not found. Install Python or create .venv.
set "EXIT_CODE=1"
goto :finish

:finish
if not "%EXIT_CODE%"=="0" (
    echo GUI failed with exit code %EXIT_CODE%.
    pause
)
endlocal & exit /b %EXIT_CODE%
