@echo off
cd /d "%~dp0"
title BoS Verifier

echo Starting the BoS Verifier...
echo.

rem Pick the Python that actually has the packages installed.  "py -3" often
rem resolves to a different installation than the one pip put them in, so the
rem interpreter is chosen by asking each one whether it can import them.
set "PYEXE="

python -c "import flask, pymupdf, cv2, pytesseract" >nul 2>&1
if not errorlevel 1 set "PYEXE=python"

if not defined PYEXE (
    py -3 -c "import flask, pymupdf, cv2, pytesseract" >nul 2>&1
    if not errorlevel 1 set "PYEXE=py -3"
)

if not defined PYEXE (
    echo No Python installation was found with the required packages.
    echo.
    echo Open a command prompt in this folder and run:
    echo     pip install -r requirements.txt
    echo.
    echo If Python is not installed at all, get it from python.org first.
    echo.
    pause
    exit /b 1
)

%PYEXE% launcher.py

echo.
echo The BoS Verifier has stopped.
pause
