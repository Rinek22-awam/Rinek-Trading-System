# Cara Membuat Dashboard Bot.exe

1. Kalau mengunduh repo sebagai ZIP, ekstrak dulu isinya.
2. Buka folder proyek yang berisi `MENU.bat`. Klik bilah alamat File Explorer, ketik `powershell`, lalu tekan **Enter**. PowerShell akan terbuka di folder itu.
3. Salin perintah berikut ke PowerShell, lalu tekan **Enter**.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File sistem/build-dashboard.ps1 -Shortcut
```

Kalau build berhasil, **Dashboard Bot.exe** dan **Dashboard Bot.lnk** muncul di samping `MENU.bat`. Klik dua kali salah satunya untuk membuka Dashboard.

Simpan EXE di folder proyek karena Dashboard memakai Python, paket, dan file program yang sama dengan `MENU.bat`. Shortcut boleh dipindahkan ke Desktop selama lokasi EXE tetap sama.

Kalau ini PC baru, siapkan Python dengan Tcl/Tk dan paket proyek sesuai [panduan instalasi](INSTALL.md). Setelah mengubah `logo/logo.ico`, jalankan kembali perintah yang sama untuk memperbarui ikon EXE dan shortcut.
