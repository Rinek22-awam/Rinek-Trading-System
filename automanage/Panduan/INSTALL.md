# Pasang di Device Baru

Panduan memasang sistem ini dari nol di PC Windows baru. Ditulis untuk dua pembaca:
**kamu** (yang mengerjakan langkah berisi akun, password, dan klik di aplikasi) dan
**Claude** (yang membantu: mengecek, memasang paket, menjalankan tes, dan menuntun).

> **Aturan tetap:** rahasia (token bot, API Telegram, password MT5, kode login) **tidak pernah**
> dikirim ke chat Claude. Kamu sendiri yang mengisinya ke file `.env`, dan file itu tidak ikut git.

---

## 0. Gambaran singkat

```
Grup sinyal gambar (gold/BTC)      Grup sinyal teks (crypto, TraderAwam)
              \                          /
     PEMBACA (akun Telegram-mu; gambar dibaca Claude, teks dibaca kode)
                          |  posting SETUP berlabel "Ke: mt5" / "Ke: bybit"
               Channel privat "kotak masuk"
              | Ke: mt5                       | Ke: bybit
     BOT MT5 (bot Telegram #1)        BOT BYBIT (bot Telegram #2)
     tombol Ya/Tidak -> MT5 Exness    tombol Ya/Tidak -> Bybit USDT Perpetual
```

Program bisa dijalankan dari **`MENU.bat` → Nyalakan**, atau dari Dashboard yang dibuka lewat menu maupun EXE. [Cara membuat EXE](BUAT_EXE.md)

- **Pembaca** (`sistem/reader/`): menonton daftar sumber sinyal, memposting SETUP berlabel ke kotak masuk.
- **Bot MT5** (`sistem/copier/`): mengerjakan `Ke: mt5`, memasang order di MT5, menjaga posisi (BE, trailing).
- **Bot Bybit** (`sistem/copier/app_bybit.py`): mengerjakan `Ke: bybit` dengan SOP yang sama di Bybit.
- **Bot Pengawas** (opsional): memantau tiga proses di atas, mengirim alert, dan menerima perintah kontrol lewat chat Telegram pribadi.

Cuma pakai salah satu broker? Boleh. Program yang tidak dipakai tidak perlu dinyalakan, dan kunci `.env`-nya boleh kosong.

Setup juga bisa diketik manual di kotak masuk: format dan contohnya di [FORMAT_SETUP.md](FORMAT_SETUP.md).

---

## 1. Yang harus ada di PC

| Bahan | Catatan | Cek |
|---|---|---|
| Windows 10/11 64-bit | Paket MetaTrader5 untuk Python hanya ada di Windows | |
| **Python 3.13** (64-bit) | Saat instal centang **"Add python.exe to PATH"** dan sertakan **Tcl/Tk** untuk dashboard. Versi teruji 3.13.5 | `python --version`, `python -m tkinter` |
| **MT5 desktop** dari Exness | Untuk bot MT5. Login ke akun Exness (demo untuk uji, cent untuk live) | buka aplikasinya |
| **Akun Bybit** | Untuk bot Bybit. Akun Unified, **cross margin**, mode posisi **one-way**. Uji di **Demo Trading** | buka bybit.com |
| **Claude Code** (CLI `claude`) | Mata pembaca. Ikut langganan Claude Pro, tanpa API key | `claude --version` |
| Telegram | Akun utama yang sudah join semua grup sinyal | |

## 2. Ekstrak arsip

Ekstrak seluruh isi arsip RAR atau ZIP. Buka folder yang berisi `MENU.bat`, lalu jalankan perintah di bagian berikut dari folder itu.

## 3. Pasang paket Python

```
cd sistem
python -m pip install -r requirements.txt
python -m pytest -q
cd ..
```

Kode program ada di folder `sistem`. Jalankan `MENU.bat` dari folder proyek. File akun lokal akan dibuat saat akun disimpan lewat menu.

Tes memakai MT5, Bybit, Telegram, dan Claude tiruan. Tes Qt untuk antarmuka PySide6 lama bisa dilewati jika dependensinya belum dipasang. Dashboard menu menggunakan Tkinter.

## 4. Isi file `.env` (kamu sendiri, bukan Claude)

```
copy .env.example .env
```

Buka `.env` dengan Notepad dan isi:

| Kunci | Dari mana | Rahasia? |
|---|---|---|
| `TG_BOT_TOKEN` | Token bot MT5 dari @BotFather → `/mybots` → pilih bot → *API Token* | **Ya** |
| `TG_INBOX_CHANNEL_ID` | ID channel kotak masuk (`-1001234567890`). Jika Reader sudah login, simpan lewat MENU → Utilitas → *Ganti channel kotak masuk sinyal* | Tidak |
| `TG_ADMIN_USER_ID` | ID numerik akun Telegram yang dipakai sebagai admin, misalnya `123456789` | Tidak |
| `TG_API_ID`, `TG_API_HASH` | https://my.telegram.org → *API development tools*. Dipakai Reader, MT5, Bybit, dan Pengawas untuk Telethon | **Ya** |
| `MT5_LOGIN` dst. | **Kosongkan.** Login langsung di aplikasi MT5 lebih aman | Ya |
| `BYBIT_TG_BOT_TOKEN` | Bot Telegram **kedua** dari @BotFather, khusus bot Bybit (satu token tidak bisa dipakai dua bot) | **Ya** |
| `SUPERVISOR_TG_BOT_TOKEN` | Opsional. Token BotFather khusus Pengawas | **Ya** |

Setelah `.env` dibuat, token MT5, Bybit, dan Pengawas juga bisa disimpan lewat **MENU.bat → Utilitas → Isi/ganti token bot Telegram**. Teks token dan API key terlihat saat diisi di jendela menu.

Masukkan API key tiap akun lewat **Setelan universal → Bybit → Akun Bybit**. Akun tersimpan di `akun-bybit.env` di samping `.env`. Buat key di mode yang sama dengan akunnya (demo/testnet/live) dengan izin **Contract: Orders + Positions**. Biarkan izin Withdraw/Transfer mati.

Kedua bot (MT5 dan Bybit) harus jadi **admin** di channel kotak masuk yang sama, dan akun Telegram-mu juga admin (pembaca memposting sebagai akunmu, karena bot tidak menerima postingannya sendiri). Kirim `/start` ke kedua bot di chat pribadi.

Jika memakai Pengawas, buka chat pribadi bot Pengawas dan tekan **Start**. Pengawas memantau proses dan log dari PC, sehingga cukup memakai chat pribadi admin.

## 5. Login Claude Code (kamu sendiri)

```
claude auth login
claude auth status
```

Harus `"loggedIn": true`. Aplikasi Claude desktop dan CLI punya login masing-masing.

## 6. Siapkan MT5

1. Login akun Exness di MT5 desktop. Akun harus tipe **hedging** (bot menolak akun netting).
2. Klik tombol **Algo Trading** sampai hijau.
3. Daftarkan folder terminal lewat **Setelan universal → MT5 → Akun MT5 → Tambah akun**. Buka akun tersebut, lalu pilih **Cocokkan otomatis dengan broker di MT5** untuk memeriksa nama simbol. Simbol bisa berbeda per akun, misalnya `XAUUSDm` atau `XAUUSDc`.
4. Setelan bawaan mengizinkan akun real. Untuk membatasi bot ke akun demo, aktifkan **Setelan universal → MT5 → Akun → Hanya boleh akun demo**.

## 6b. Siapkan Bybit

1. Login di bybit.com. Untuk uji: foto profil → **Demo Trading** (saldo demo gratis).
2. **Cross margin:** Aset → Akun Unified → Mode margin → **Cross Margin**. Bot Bybit memeriksa mode ini saat startup.
3. **One-way mode** (bawaan Bybit): bot menolak jalan di hedge mode.
4. Buat API key di mode yang sama, lalu masukkan lewat MENU → Setelan universal → Bybit → Akun Bybit.
5. Coba fungsi dasarnya tanpa sinyal: MENU → Utilitas → **Tes manual Bybit** (order satu per satu, demo saja). Langkah tes ada di [BYBIT.md](BYBIT.md).

## 7. Pertama kali menjalankan

Klik dua kali **`MENU.bat`**, lalu:

1. **Sumber sinyal → + Tambah sumber.** Login pertama lewat menu terminal meminta nomor HP, kode Telegram, dan password 2FA kalau ada. Pilih channel/grup, aturan topik, pengolah, platform, satu atau lebih akun tujuan yang sudah didaftarkan, SOP awal, dan nama. Tiap sumber mendapat kode tetap (S01, S02, ...). Ulangi untuk tiap sumber:
   - Contoh sumber gambar gold: pilih **Claude** dan tujuan **MT5**, lalu centang akun dan topik yang dipakai.
   - Contoh sinyal crypto berformat TraderAwam: pilih **bot pembaca kode (TraderAwam)** dan tujuan **Bybit**, lalu centang akun serta atur topik.
2. **Utilitas → Coba baca gambar sinyal** → seret satu gambar sinyal ke jendela → harus jadi `SETUP` dengan angka yang sama dengan gambar.
3. **Utilitas → Cek kesiapan**. Periksa hasil untuk komponen yang kamu pakai. MT5 memeriksa terminal terdaftar, sedangkan Bybit memeriksa akun API termasuk cross margin dan one-way.
4. **Sumber sinyal → pilih sumber → SOP.** Sumber baru mulai dari angka bawaan yang aman (risiko 1%, loss harian 3%, maks 3 setup) atau salinan SOP sumber lain. Setelan di PC lama tidak ikut pindah kecuali kamu salin filenya (bagian 9).
5. **Nyalakan → Pembaca sinyal, lalu Bot MT5 dan/atau Bot Bybit.** Di Telegram muncul "🤖 Bot nyala" dan "🤖 Bot Bybit nyala".

## 8. (Opsional) Dashboard dan Bot Pengawas

Dari `MENU.bat`, pilih **Buka Dashboard**, atau buat launcher lewat [panduan EXE](BUAT_EXE.md). Dashboard punya empat tab untuk status proses, sumber sinyal, setelan universal, dan utilitas. Jika memakai Pengawas, nyalakan setelah token disimpan dan coba `/status` di chat pribadinya. [Panduan Dashboard dan Pengawas](DESKTOP.md)

Saat pertama pindah dari eksekutor lama yang memakai Bot API, selesaikan dulu sinyal dan perintah yang masih menunggu di jalur lama. Sesi Telethon baru menerima update lewat jalurnya sendiri. Jurnal dan token eksekutor tetap dipakai.

## 9. Setelan dan data saat pindah device

| File | Isi | Di device baru |
|---|---|---|
| `.env` | Rahasia Telegram (token, API) | Isi ulang (langkah 4) |
| `akun-bybit.env` | Akun Bybit + API key | Masukkan ulang lewat menu, atau salin dari device lama (sama rahasianya dengan `.env`) |
| `akun-mt5.env` dan `sistem/data/akun-mt5.json` | Folder terminal, identitas dan pemetaan simbol per akun | Daftarkan akun lewat menu, atau salin kedua file dan sesuaikan folder terminal |
| `sistem/pembaca.session` (+ `-journal`) | Sesi login Telegram Pembaca | **Login ulang** (langkah 7.1). Simpan file sesi secara privat |
| `sistem/sumber.yaml` + folder `sistem/sumber/` | Daftar sumber + SOP tiap sumber | Salin keduanya dari device lama (kode dan SOP tetap), atau tambah ulang lewat menu |
| `config.yaml`, `config-bybit.yaml` versi PC lama | Setelan universal (nama pair, hanya demo) | Yang di git berisi bawaan; setel ulang lewat Setelan universal |
| `sistem/data/jurnal.db`, `sistem/data/jurnal-bybit.db` | Riwayat setup dan kaitannya dengan order di broker | Salin setelah bot lama berhenti jika ingin membawa riwayat atau melanjutkan pengelolaan setup yang masih aktif |
| `sistem/data/pengawas-*.session` | Sesi Telegram lokal Bot Pengawas | Dibuat ulang saat Pengawas nyala dengan token yang valid |
| `sistem/data/telegram-*.session` dan `telegram-*.db` | Sesi dan antrean update lokal eksekutor | Dibuat otomatis di PC baru. Selesaikan sinyal/perintah yang masih menunggu di PC lama sebelum pindah |
| `sistem/data/jurnal*-cadangan-*.db` | Cadangan reset jurnal MT5 dan Bybit | Salin jika cadangan masih diperlukan |
| `sistem/data/ingatan.db` | Konteks setup, pesan, dan catatan gaya channel untuk Claude CLI | Opsional jika ingin melanjutkan ingatan Reader |
| `sistem/logs/` | Log Reader, MT5, Bybit, dan Pengawas | Salin jika riwayat log masih diperlukan |

**Jangan jalankan bot di dua device sekaligus.** Keduanya akan membaca kotak masuk yang sama dan bisa mengeksekusi satu sinyal dua kali. Matikan di device lama dulu. (Pengaman anti-dobel hanya bekerja di satu PC.)

## 10. Bersihkan riwayat lokal

**MENU.bat → Utilitas → Reset bot** menyediakan reset terpisah untuk MT5, Bybit, dan Reader. Hentikan proses yang dipilih sebelum mengetik `RESET`. Reset MT5/Bybit menghapus jurnal, cadangan, dan log. Reset Reader menghapus log dan ingatan Claude CLI. Akun, sumber, SOP, dan sesi Telegram tetap tersimpan. Order broker tetap berjalan, tetapi bot kehilangan jurnal setup lama. [Peta menu terminal](PETA_MENU.md)

## 11. Masalah umum

| Gejala | Penyebab | Jalan keluar |
|---|---|---|
| Konfirmasi telat 10–30 detik, log "Telegram lambat/putus" | Koneksi ke Telegram tersendat | Sudah ditangani bot (coba lagi otomatis); cek jaringan/VPN |
| "Batas loss harian tercapai" terus saat uji | Pengaman 3% sudah kena | Masa uji: Pengaturan SOP → Pengaman → naikkan sementara (misal 20), kembalikan ke 3 sebelum live |
| Pembaca: "Claude gagal membaca: OAuth ..." | CLI `claude` belum login / sesi habis | `claude auth login` |
| "Bot sudah jalan di jendela lain" | Bot sudah menyala | Pakai jendela yang ada, atau tutup dulu |
| Dashboard gagal membuka Tkinter | Tcl/Tk belum tersedia di Python yang dipakai menu | Jalankan `python -m tkinter`. Tambahkan fitur Tcl/Tk pada instalasi Python Windows jika tes gagal |
| Pengawas aktif, pesan awal belum masuk | Chat pribadi belum dibuka, ID admin salah, atau koneksi Telegram terganggu | Tekan **Start** di chat Pengawas, cek `TG_ADMIN_USER_ID`, lalu lihat panel log Pengawas |
| Setup MT5 ditolak "Simbol ... tidak ada di MT5" | Nama simbol beda (akhiran `m`/`c`, ganti broker) | Setelan universal → MT5 → Cocokkan nama pair dengan broker |
| Gambar contoh lama ditolak "Entry terlalu jauh" / "TP1 sudah terlewat" (market) | Harga di gambar sudah basi | Normal; gerbang bekerja |
| Setup manual ditolak "Entry terlalu jauh", padahal angkanya benar | Ditulis pakai titik ribuan (`112.500` dibaca 112,5) | Tulis tanpa pemisah ribuan: `112500`. Lihat [FORMAT_SETUP.md](FORMAT_SETUP.md) |
| Bot Bybit: "⛔ Mode margin akun Bybit: isolated" | Akun bukan cross margin | Bybit: Aset → Akun Unified → Mode margin → Cross Margin, nyalakan ulang bot |
| Bot Bybit: "Akun Bybit dalam hedge mode" | Mode posisi hedge | Ubah ke one-way di pengaturan derivatif Bybit |
| Bybit `[10003]` API key tidak dikenal | Key dibuat di mode lain (key asli dipakai di demo, atau sebaliknya) | Buat key di mode yang sama dengan `BYBIT_MODE` |
| Bybit `[10002]` jam tidak sinkron | Jam Windows meleset | Settings → Time → **Sync now** |
| Bybit HTTP 403 | API Bybit menolak akses | Tunggu jeda yang ditampilkan Pengawas. Detail request ada di `sistem/logs/bot-bybit.log` |
| Setup Bybit ditolak "koin X tidak ada di Bybit" | Koin memang tidak ada di USDT Perpetual, atau versi berkelipatan (1000PEPE) tapi harga sinyal tidak cocok dengan skalanya | Normal: tidak dieksekusi, hanya dilaporkan |
| Setup Bybit ditolak "gagal terhubung ke Bybit (koneksi putus/timeout)… setup belum dieksekusi" | Internet ke server Bybit tersendat. Pembacaan data sudah dicoba ulang 2× otomatis; order tidak pernah diulang otomatis (supaya tidak dobel) | Cek koneksi, lalu kirim ulang setupnya |
| Setup Bybit ditolak "satu posisi per koin" | Sudah ada setup aktif / menunggu konfirmasi di koin yang sama | Normal; tunggu selesai atau batalkan yang lama |
