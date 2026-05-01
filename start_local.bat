@echo off
echo Iniciando Zetri - SVX Player Server en modo HTTP...
start http://localhost:8098/
python -m uvicorn main:app --host 0.0.0.0 --port 8098 --reload
pause
