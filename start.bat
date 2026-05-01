@echo off
echo Starting Zetri - SVX Player Server...
start https://localhost:8098/
python -m uvicorn main:app --host 0.0.0.0 --port 8098 --reload --ssl-keyfile localhost+3-key.pem --ssl-certfile localhost+3.pem
pause
