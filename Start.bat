@echo off
title Tradebot Dashboard
echo.
echo  =============================================
echo   Tradebot Dashboard
echo  =============================================
echo.
echo  Starte Server auf http://localhost:8080
echo  Dein Browser offnet sich automatisch.
echo.
echo  Zum Beenden: Dieses Fenster schliessen
echo  oder Strg+C druecken.
echo  =============================================
echo.

python -m tradebot.cli serve

if %errorlevel% neq 0 (
  echo.
  echo  FEHLER: Server konnte nicht gestartet werden.
  echo.
  echo  Mogliche Ursachen:
  echo    1. Python ist nicht installiert
  echo       -> python.org/downloads
  echo    2. Tradebot ist nicht installiert
  echo       -> pip install -e .  (einmalig im Tradebot-Ordner)
  echo    3. Port 8080 ist bereits belegt
  echo       -> Warte kurz und versuche es erneut
  echo.
  pause
)
