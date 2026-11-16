# Rinek Trading System

Kedua program ini dibuat dan dikembangkan oleh **rinek22** untuk kebutuhan pribadi. Alur kerja dan setelan bawaannya mengikuti cara pakai saya. Kalau kurang cocok, silakan edit sendiri sesuai kebutuhanmu dengan bantuan AI pilihanmu.

## Mulai pakai

Unduh atau clone repo ini di Windows. Buka folder program yang ingin kamu pakai, baca panduan instalasinya, lalu jalankan **MENU.bat** dari folder tersebut.

| Program | Fungsi | Panduan |
|---|---|---|
| **AutoManage** | Membaca sinyal Telegram, memvalidasi setup, serta mengeksekusi dan mengelola order di MT5 atau Bybit. | [README](automanage/README.md) · [Instalasi](automanage/Panduan/INSTALL.md) |
| **CLI Signal Generator** | Menjalankan analisa TradingView lewat Claude Code dan mengirim blok SETUP ke Telegram sesuai skill dan jadwal yang dipilih. | [README](cli-signal-generator/README.md) · [Instalasi](cli-signal-generator/panduan/INSTALL.md) |

## TradingView MCP

CLI Signal Generator membutuhkan **Claude Code**, **TradingView Desktop**, dan server MCP publik [tradesdontlie/tradingview-mcp](https://github.com/tradesdontlie/tradingview-mcp). Ikuti panduan instalasi Generator untuk memasang MCP dan membuka chart pada port debug 9222.

## Kalau dipakai bareng

Arahkan Generator dan AutoManage ke channel kotak masuk Telegram yang sama. Cocokkan kode SETUP dari Generator dengan kode sumber yang terdaftar di AutoManage, dan pastikan tujuan SETUP sesuai broker yang kamu pakai. Uji alurnya di akun demo dulu.

Isi token, API key, akun broker, channel, dan skill analisa milikmu sendiri lewat pengaturan masing-masing program. Simpan file setelan lokal dan kredensial di PC-mu.
