@echo off
cd /d "%~dp0"
python story_generator_ui.py
if errorlevel 1 pause
