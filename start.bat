@echo off
REM ============================================================
REM  CustoJusto Leads — arranca backend (Python) + frontend (Astro)
REM  Faz duplo clique neste ficheiro. Abre 2 janelas e o browser.
REM  Para PARAR: fecha as duas janelas que se abrirem.
REM ============================================================
cd /d "%~dp0"

echo A arrancar o backend (API Python) na porta 8000...
start "CustoJusto - Backend (NAO FECHAR)" cmd /k python -m uvicorn app:app --port 8000

echo A arrancar o frontend (Astro) na porta 4321...
start "CustoJusto - Frontend (NAO FECHAR)" cmd /k "cd frontend && npm run dev"

echo A aguardar que os servidores arranquem...
timeout /t 7 /nobreak >nul

echo A abrir o browser...
start "" http://localhost:4321

echo.
echo Pronto! Painel em http://localhost:4321
echo (Se vires "API offline", espera uns segundos e recarrega a pagina.)
echo Esta janela pode ser fechada. NAO feches as outras duas.
timeout /t 6 /nobreak >nul
