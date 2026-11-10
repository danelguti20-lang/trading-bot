@echo off
title Mis bots - actualizando (cierra esta ventana para parar)
cd /d C:\Users\Administrator\trading-bot
git pull -q
start "" panel.html
echo Panel abierto. Esta ventana descarga datos nuevos cada minuto.
echo El panel se recarga solo. Cierra esta ventana cuando acabes.
:bucle
timeout /t 60 /nobreak >nul
git pull -q
echo %time% actualizado
goto bucle
