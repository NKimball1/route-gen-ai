@echo off
rem Route Gen AI web app -> http://localhost:8903
rem Start start_brouter.cmd (C:\Users\me\brouter) first for routing.
cd /d "%~dp0"
.venv\Scripts\python.exe -m uvicorn api:app --port 8903
