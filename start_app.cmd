@echo off
rem Route Gen AI web app -> http://localhost:8903
rem Start your BRouter server (start_brouter.cmd) first for routing.
cd /d "%~dp0"
.venv\Scripts\python.exe -m uvicorn api:app --port 8903
