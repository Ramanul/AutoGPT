@echo off
setlocal
set "DASHBOARD_URL=https://laughing-memory-6jgrgv4wjqvc4ggq-8765.app.github.dev"
set "AUTOGPT_URL=https://laughing-memory-6jgrgv4wjqvc4ggq-8000.app.github.dev"

if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
	start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%DASHBOARD_URL%" "%AUTOGPT_URL%"
	exit /b 0
)

if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
	start "" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%DASHBOARD_URL%" "%AUTOGPT_URL%"
	exit /b 0
)

start "" chrome "%DASHBOARD_URL%" "%AUTOGPT_URL%"
if %errorlevel%==0 exit /b 0

start "" "%DASHBOARD_URL%"
start "" "%AUTOGPT_URL%"
