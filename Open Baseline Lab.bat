@echo off
rem Double-click opens Baseline Lab. Drag .txt files onto this shortcut to open them already loaded.
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw main.py %*) || (python main.py %* || pause)
