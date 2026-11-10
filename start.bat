@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "launch_code=1"
pushd "%~dp0"
if errorlevel 1 goto directory_error
set "config_path=%CD%\qecho.toml"

if exist "qecho.toml" goto configured
copy /Y "qecho.example.toml" "qecho.toml" >nul
if errorlevel 1 goto copy_error
echo Created qecho.toml from qecho.example.toml.
echo Edit qecho.toml ^(model, QQ connection, friends and prompt^), then run this launcher again.
echo QEcho was not started.
set "launch_code=0"
goto finish

:configured
where uv >nul 2>nul
if errorlevel 1 goto missing_uv
call uv python install 3.12
if errorlevel 1 goto dependency_error
call uv sync --locked --no-dev
if errorlevel 1 goto dependency_error
call uv run --no-sync qecho --config "%config_path%"
set "launch_code=%errorlevel%"
if not "%launch_code%"=="0" echo QEcho stopped with an error. Check the message above.
goto finish

:dependency_error
set "launch_code=%errorlevel%"
echo Python preparation failed. QEcho was not started.
goto finish

:missing_uv
echo uv was not found. Install it from:
echo https://docs.astral.sh/uv/getting-started/installation/
echo Open a new terminal and run this launcher again.
set "launch_code=1"
goto finish

:copy_error
echo Cannot create qecho.toml. Check the sample file and directory permissions.
set "launch_code=1"
goto finish

:directory_error
echo Cannot open the QEcho project directory.
goto finish_without_directory

:finish
popd
:finish_without_directory
pause
exit /b %launch_code%
