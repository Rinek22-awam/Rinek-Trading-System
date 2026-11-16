@echo off
setlocal
title Menu CLI Signal Generator
chcp 65001 >nul
cd /d "%~dp0sistem"

where python >nul 2>nul
if errorlevel 1 (
    echo [X] Python belum terpasang atau tidak ditemukan.
    pause
    exit /b 1
)
python -c "import importlib.util as u; assert all(u.find_spec(p) for p in ('yaml','dotenv','telethon','psutil','pytest','openpyxl'))" >nul 2>nul
if errorlevel 1 (
    echo Memasang paket yang dibutuhkan...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

python -m generator.menu
if errorlevel 1 pause
