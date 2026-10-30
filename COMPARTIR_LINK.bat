@echo off
title Sigma - Link publico (no cierres esta ventana)
echo.
echo  Crea un link publico gratis (localhost.run) para que otros usen tu Sigma.
echo  Requisito: INICIAR.bat debe estar abierto en otra ventana.
echo.
echo  Busca abajo la linea que termina en  .lhr.life  -  ese es el link para compartir.
echo  Funciona mientras esta ventana y la de INICIAR.bat sigan abiertas.
echo.
ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -R 80:127.0.0.1:8000 nokey@localhost.run
pause
