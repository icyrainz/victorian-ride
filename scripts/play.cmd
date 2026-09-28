@echo off
rem Victorian Ride on one screen.
rem Force feedback runs at gain 0.2 with the sign Torque Hero's checklist verified
rem (the game copies bindings.json and ffb.json from Torque Hero on first start).
rem For no force feedback, change FFB below to --no-ffb and save.
set FFB=--ffb-gain 0.2

rem The game starts paused: click the game window, then press P or Enter.
cd /d "%~dp0.."
uv run victorian-ride play %FFB%
if errorlevel 1 pause
