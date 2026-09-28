@echo off
rem Victorian Ride across the three monitors.
rem Edit the "set" lines below in Notepad (right-click this file, Edit), save,
rem then double-click this file to play.

rem SPAN: 5760x1080 followed by the position of the LEFT monitor.
rem Use the same SPAN as Torque Hero's scripts\play-triples.cmd.
set SPAN=5760x1080-1920+0

rem FFB: force feedback at gain 0.2 (] raises it 0.05 per press, [ lowers it).
rem For no force feedback, change it to --no-ffb and save.
set FFB=--ffb-gain 0.2

rem The game starts paused: click the game window, then press P or Enter.
cd /d "%~dp0.."
uv run victorian-ride play --span %SPAN% %FFB%
if errorlevel 1 pause
