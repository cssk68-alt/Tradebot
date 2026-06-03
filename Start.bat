@echo off
title Tradebot Dashboard
cd /d "%~dp0"
echo.
echo  =============================================
echo   Tradebot Dashboard
echo  =============================================
echo.

REM --- Find a working Python command (python or py) ---
set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  py --version >nul 2>&1 && set "PY=py"
)
if not defined PY (
  echo  FEHLER: Python wurde nicht gefunden.
  echo.
  echo  Installiere Python von  python.org/downloads
  echo  und setze beim Installer den Haken bei
  echo  "Add python.exe to PATH". Danach PowerShell neu oeffnen.
  echo.
  pause
  exit /b 1
)
echo  Python gefunden ^(%PY%^).
echo.

REM --- Install/refresh dependencies (fast when already installed) ---
echo  Pruefe Abhaengigkeiten ^(einmalig, dauert beim ersten Mal etwas^)...
%PY% -m pip install -e . --quiet
if errorlevel 1 (
  echo.
  echo  FEHLER bei der Installation der Abhaengigkeiten.
  echo  Pruefe deine Internetverbindung und versuche es erneut.
  echo.
  pause
  exit /b 1
)
echo  Abhaengigkeiten bereit.
echo.

echo  Starte Server auf  http://localhost:8080
echo  Dein Browser oeffnet sich automatisch.
echo.
echo  Zum Beenden: dieses Fenster schliessen oder Strg+C.
echo  =============================================
echo.

%PY% -m tradebot.cli serve

if errorlevel 1 (
  echo.
  echo  FEHLER: Server konnte nicht gestartet werden.
  echo  Moegliche Ursache: Port 8080 ist belegt.
  echo  Warte kurz und starte erneut.
  echo.
  pause
)
