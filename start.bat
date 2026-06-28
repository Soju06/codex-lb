@echo off
title Codex LB - localhost:2455
cd /d "%~dp0"

REM Clear PYTHONPATH to avoid conflicts with other Python environments (e.g. Hermes Agent)
set PYTHONPATH=

where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    uv run python -c "import asyncio;asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy());from app.cli import main;main()"
) else (
    echo uv not found, falling back to python...
    python -c "import asyncio;asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy());from app.cli import main;main()"
)

pause
