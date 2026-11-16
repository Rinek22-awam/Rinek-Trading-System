# Panduan instal dan setup awal CLI Signal Generator

Panduan ini untuk memasang CLI di PC Windows baru, dari nol sampai analisa pertama jalan. Kerjakan berurutan; tiap langkah ada cara mengeceknya.

> **Untuk AI yang membantu memasang:** kerjakan bagian 1–8 berurutan, cek dulu apakah langkah itu sudah beres sebelum mengerjakannya, dan laporkan ke user apa yang dilewati. **Jangan minta atau menerima rahasia di chat** (token bot, API hash Telegram, kode login, password). User mengisinya sendiri lewat menu. ID channel dan ID chat bukan rahasia.

## 0. Gambaran yang dibutuhkan

| Komponen | Untuk apa |
|---|---|
| Windows 10/11 | Program dan skripnya khusus Windows |
| Python 3.13 | Menjalankan program CLI |
| Node.js (LTS) | Menjalankan server MCP TradingView |
| TradingView Desktop | Chart dibuka dengan port debug 9222 |
| Akun TradingView | Chart yang dibaca Claude; indikator yang dipakai skill harus terpasang di chart |
| Claude Code CLI ≥ 2.1.280 + langganan Claude (Pro/Max) | "Otak" analisa. Versi lama tidak bisa memakai Opus 5.5 |
| Server MCP TradingView | Jembatan Claude ↔ chart di aplikasi Desktop |
| Bot Telegram + channel + chat pribadi | Channel = kotak masuk blok SETUP; chat pribadi = laporan |
| (Opsional) Bot eksekutor, misalnya Automanage | Yang membaca channel dan membuka order |

## 1. Ambil folder program

1. Clone atau unduh ZIP repo ini dari GitHub, lalu buka folder `cli-signal-generator`. Lokasi bebas, misalnya `D:\CLI-signal-generator`. Repo tidak menyertakan skill, setelan pribadi, dan data; `sistem\config.yaml` dan `sistem\mcp-tradingview.json` dibuat otomatis saat program pertama dibuka.
2. **Kalau folder disalin langsung dari PC orang lain**, pastikan yang berikut **tidak ikut** (atau hapus): `.env`, `sistem\data\`, `sistem\logs\`, `jurnal\`, `sistem\config.yaml`, `sistem\mcp-tradingview.json`. Isinya rahasia dan data pribadi pemilik lama. Program membuat ulang semuanya sendiri.
3. Kalau ada folder yang menolak dihapus atau disalin ("Access denied"), lewati saja. Itu sisa tes, tidak dipakai program.

## 2. Pasang Python, Node.js, TradingView Desktop

1. **Python 3.13** dari python.org. Saat memasang, centang **"Add python.exe to PATH"**.
   Cek: buka Command Prompt, ketik `python --version` → harus `Python 3.13.x`.
2. **Node.js LTS** dari nodejs.org. Cek: `node --version`.
3. **TradingView Desktop** dari situs TradingView.

Paket Python (PyYAML, python-dotenv, Telethon, psutil, pytest, openpyxl) dipasang otomatis oleh `MENU.bat` saat pertama dibuka.

## 3. Pasang Claude Code CLI dan login

1. Pasang Claude Code (lihat petunjuk resmi Anthropic untuk Windows), lalu login dengan akun Claude berlangganan:
   ```
   claude
   ```
   Ikuti langkah login di jendela itu sampai selesai, lalu tutup.
2. Pastikan versinya cukup baru:
   ```
   claude update
   claude --version
   ```
   Harus **2.1.280 atau lebih baru**. Kalau tidak, analisa dengan `claude-opus-5-5` gagal dengan pesan "does not support this model".

## 4. Pasang server MCP TradingView

CLI memakai [tradesdontlie/tradingview-mcp](https://github.com/tradesdontlie/tradingview-mcp) untuk membaca chart di TradingView Desktop.

1. Clone atau unduh repo MCP ke folder Dokumen, dengan nama `tradingview-mcp`.
2. Pasang dependensinya. Buka Command Prompt di folder repo itu:
   ```
   npm install
   ```
3. **Arahkan CLI ke folder itu.** Saat pertama jalan, program membuat `sistem\mcp-tradingview.json` yang menunjuk ke `Dokumen\tradingview-mcp\src\server.js` milik user Windows-mu. Kalau repo MCP ada di tempat lain, buka file itu dengan Notepad (setelah MENU.bat dibuka sekali) dan ganti path di `"args"` (garis miring terbalik ditulis dua kali):
   ```json
   {
     "mcpServers": {
       "tradingview": {
         "command": "node",
         "args": ["C:\\Users\\<nama>\\Documents\\tradingview-mcp\\src\\server.js"],
         "env": { "TV_CDP_HOST": "127.0.0.1", "TV_CDP_PORT": "9222" }
       }
     }
   }
   ```
   CLI tidak memerlukan MCP ini didaftarkan global di Claude Code; CLI memuatnya sendiri dari file ini.

## 5. Siapkan Telegram

1. **Buat bot generator:** buka @BotFather di Telegram → `/newbot` → ikuti langkahnya → simpan **token** (rahasia, jangan dikirim ke siapa pun).
2. **Channel kotak masuk:** pakai channel yang dibaca bot eksekutormu (untuk Automanage: channel kotak masuknya), atau buat channel privat baru untuk uji. Tambahkan bot generator sebagai **admin** dengan izin **Post messages**.
3. **Chat pribadi:** buka chat dengan bot generator dan tekan **Start**. Semua laporan dikirim ke sini.

> Mode **akun** (memposting lewat akun Telegram pribadi, butuh API ID/hash dari my.telegram.org) hanya cadangan kalau bot eksekutor tidak mau membaca postingan bot. Mode bawaan adalah **bot**.

## 6. Buka MENU.bat dan isi setelan

Klik dua kali **`MENU.bat`**. Pertama kali, paket Python dipasang dulu. Lalu menu utama muncul (tekan angka lalu Enter; `0` atau Enter = kembali):

```
1. Nyalakan   2. Buka Dashboard   3. Skill & analisa   4. Telegram
5. TradingView   6. Jurnal   7. Utilitas
```

Semua langkah di bawah juga bisa lewat **2. Buka Dashboard** (tab dengan nama yang sama).

1. **4. Telegram → Isi / ganti token bot**: tempel token dari BotFather. Program mengecek token ke Telegram dulu, lalu menyimpannya ke `.env`.
2. **4. Telegram → Ganti channel kotak masuk**: pilih **Channel tempat bot dijadikan admin** (atau teruskan satu postingan channel ke bot, atau ketik ID `-100…`). Program mengecek bot bisa memposting di sana.
3. **4. Telegram → Chat pribadi laporan → Deteksi ulang otomatis**: ID chat pribadimu diambil dari Start tadi.
4. **5. TradingView → Letak skrip peluncur**: kalau repo MCP **tidak** di `Dokumen\tradingview-mcp`, arahkan ke `scripts\launch_tv_debug.bat` di repo MCP (langkah 4). Kalau di sana, lewati: itu lokasi bawaan.
5. **5. TradingView → Buka TradingView sekarang**: **Login di TradingView Desktop**, buka chart, dan pasang indikator yang disebut di skill-mu. Peluncur repo MCP dapat membuka ulang aplikasi dengan port debug 9222.
6. **7. Utilitas → Cek kesiapan**: semua baris harus ✅. Baris "•" hanya informasi.

## 7. Uji pertama (aman)

1. **7. Utilitas → Kirim pesan uji ke kotak masuk (Kode TES)**: channel harus menerima satu blok SETUP berkode TES. Kalau bot eksekutor menyala, ia menolaknya karena kode TES tidak terdaftar. Itu memang yang diharapkan.
2. **1. Nyalakan → Analisa sekarang → Mode uji fungsi (TES)**: Claude membaca harga XAUUSD dari chart lalu menulis satu blok SETUP uji berkode TES (harga sekarang ada di baris Catatan). Blok itu masuk ke channel, dan chat pribadi menerima "CLI mulai analisa" lalu status akhir. Ini membuktikan Claude, MCP TradingView, dan Telegram tersambung. Mode uji tidak dicatat di jurnal.
3. **Siapkan skill** (repo tidak menyertakan skill): salin skill milikmu ke `skill\<nama>\`, atau buat baru lewat **3. Skill & analisa → + Tambah skill** mengikuti `skill\panduan-skill\PANDUAN.md`. Lalu **1. Nyalakan → Analisa sekarang → (pilih skill)** **saat pasar buka**. Hasilnya masuk ke chat pribadi, blok SETUP (kalau ada) ke channel dengan kode `TES`, dan barisnya tercatat di `jurnal\Jurnal CLI.xlsx`.

Kalau ketiganya lolos, pemasangan selesai. Lanjut ke [PENGGUNAAN.md](PENGGUNAAN.md).

## 8. Menyambung ke bot eksekutor (kalau sudah yakin)

Selama kode skill `TES`, setup hanya lewat dan ditolak bot. Untuk dieksekusi sungguhan:
1. Di bot eksekutor (Automanage: **MENU → Sumber sinyal**), daftarkan satu kode sumber untuk CLI. Saran: arahkan ke **akun demo** dulu dan nyalakan **konfirmasi**.
2. Di CLI: **3. Skill & analisa → Daftar skill → (skill) → Kode SETUP**, isi kode itu, lalu ketik `REAL` saat diminta.

## Masalah yang sering muncul

| Gejala | Penyebab dan solusi |
|---|---|
| `Claude Code 2.1.xxx does not support this model` | CLI terlalu lama. Jalankan `claude update`, cek `claude --version` ≥ 2.1.280. |
| `You've hit your session limit` | Jatah 5 jam langganan Claude habis (dipakai bersama chat Claude lainnya). Tunggu jam reset yang disebut di pesan. |
| Cek kesiapan: chart belum terdeteksi di port 9222 | Buka TradingView lewat menu Generator, lalu buka chart. Pastikan port 9222 tersedia untuk TradingView. |
| `Chart TradingView tidak siap` | Belum login TradingView di TradingView Desktop, atau chart belum termuat. Naikkan **Lama tunggu chart** kalau PC lambat. |
| Analisa jalan tapi tidak membaca indikator | Indikator harus terpasang **dan terlihat** di chart yang terbuka. |
| `GAGAL TRADINGVIEW` di jawaban | Server MCP tidak tersambung. Cek path di `sistem\mcp-tradingview.json` dan `npm install` di repo MCP. |
| Token ditolak / bot belum bisa mengirim ke chat pribadi | Salin ulang token dari BotFather; tekan Start di chat bot. |
| Bot belum punya izin posting di channel | Jadikan bot admin dengan izin Post messages. |
| Jurnal Excel tidak bertambah | File sedang dibuka di Excel. Tutup Excel; baris masuk saat analisa berikutnya atau lewat **Jurnal → Buka file Excel jurnal**. |
| Skill tidak muncul di menu | Folder harus berisi file `SKILL.md`, nama huruf kecil/angka/`-`, tidak diawali `_`. |
