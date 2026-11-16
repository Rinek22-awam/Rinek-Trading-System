@echo off
rem Menu terminal untuk maintenance, login Pembaca, dan kontrol bot.
rem Isi program ada di folder "sistem"; yang perlu kamu isi sendiri hanya file .env di sini.
title Menu Bot Eksekutor
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [X] Python belum terpasang atau tidak ditemukan.
    pause
    exit /b
)

if not exist ".env" (
    echo [X] File .env belum ada. Salin .env.example jadi .env lalu isi.
    pause
    exit /b
)

cd /d "%~dp0sistem"

python -c "import MetaTrader5, dotenv, yaml, cryptg, telethon, pytest, pybit" >nul 2>nul
if errorlevel 1 (
    echo Memasang paket yang dibutuhkan, tunggu sebentar...
    python -m pip install -q -r requirements.txt
)

python -m panel.main
if errorlevel 1 pause
