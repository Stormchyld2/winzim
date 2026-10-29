@echo off
REM Builds dist\WinZim.exe - a single file with Python bundled inside.
REM You only need Python on THIS machine to build; the exe itself needs nothing.
REM (Prefer not to install Python? Use the GitHub Actions build instead - see README.)
python -m pip install --upgrade pyinstaller -r requirements.txt
python -m PyInstaller --onefile --windowed --name WinZim app.py
echo.
echo Done. Your app is at dist\WinZim.exe
pause
