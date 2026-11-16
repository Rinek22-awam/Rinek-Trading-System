# Panduan memakai CLI Signal Generator

Panduan ini untuk pemakaian sehari-hari, setelah [INSTALL.md](INSTALL.md) selesai.

## 1. Istilah penting

| Istilah | Arti |
|---|---|
| **Skill** | Instruksi analisa untuk Claude, satu folder di `skill\` dengan file `SKILL.md` dan `setelan.yaml`. Contoh: `xau-scalp`, `xau-intraday`. |
| **Blok `SETUP`** | Setup final dalam format standar ([skill/FORMAT_SETUP.md](../skill/FORMAT_SETUP.md)). Dikirim apa adanya ke **channel kotak masuk** untuk dibaca bot eksekutor. |
| **Blok `SETUP RENDAH`** | Setup dengan conviction rendah. **Tidak** dikirim ke bot, tapi ke chat pribadimu. Mau dieksekusi: salin bloknya, ganti baris pertama jadi `SETUP`, lalu posting ke channel. |
| **`NO SETUP` / `NO ENTRY`** | Claude tidak menemukan setup layak (jawaban yang sah). Alasannya ikut di pesan status. |
| **`GAGAL TRADINGVIEW`** | Chart tidak bisa dibaca. Dilaporkan sebagai gagal. |
| **Kode `TES`** | Kode bawaan tiap skill. Bot eksekutor menolak setup berkode TES, jadi aman untuk uji. Kode lain wajib dikonfirmasi dengan mengetik `REAL`. |
| **Generator jalan terus** | Proses yang menunggu jadwal dan menjalankan skill yang NYALA tepat waktu. |
| **Analisa sekarang** | Menjalankan satu analisa saat itu juga, tanpa menunggu jadwal. Bisa diberi **pesan untuk Claude** (lihat bagian 5b). |
| **Kotak masuk / chat pribadi** | Kotak masuk = channel Telegram tujuan blok SETUP. Chat pribadi = chat kamu dengan bot generator, tempat semua laporan. |

## 2. Pemakaian harian (ringkas)

1. Buka **`MENU.bat` → 2. Buka Dashboard**.
2. Di tab **Skill & analisa**, pastikan skill yang mau dipakai **NYALA** dan jadwalnya benar, lalu tekan **Simpan**.
3. Di tab **Dashboard**, tekan **Nyalakan** (generator jalan terus). Biarkan Dashboard terbuka.
4. Pantau di Telegram: chat pribadi menerima kabar mulai, analisa lengkap, dan status akhir. Channel menerima blok SETUP.
5. Setelah trade selesai, buka **Jurnal → Buka file Excel jurnal** dan isi kolom **Hasil**.

> **Menutup Dashboard mematikan generator** (ada peringatan dulu). Kalau mau generator jalan tanpa Dashboard, nyalakan dari menu teks: **1. Nyalakan → Generator jalan terus** (terbuka di jendela cmd sendiri; tutup jendela itu untuk menghentikan).

Syarat agar jadwal jalan: PC menyala (tidak sleep), TradingView Desktop bisa dibuka program, dan generator jalan terus menyala. Kalau PC sempat tersendat, jadwal yang lewat **sampai 3 menit** tetap dikejar (analisa mulai sedikit terlambat). Jadwal yang lewat lebih lama (misalnya PC sleep) **tidak dijalankan susulan**, karena pasarnya sudah lewat, tapi dicatat di log generator: *"Jadwal terlewat … xau-scalp 13/11 20:00"*.

## 3. Dashboard per tab

| Tab | Isi | Sama dengan menu |
|---|---|---|
| **Dashboard** | Nyalakan/Stop generator, status ("terakhir aktif", sedang analisa), baris status (skill nyala, cara kirim, kotak masuk, PM, TradingView, pesan rutin), **Analisa sekarang** (pilih skill atau mode uji, pesan opsional untuk Claude), tabel skill + jadwal + **jam berikutnya** + saklar NYALA/MATI, **log generator** langsung (mulai bersih tiap Dashboard dibuka; log lama ada di Utilitas → Lihat log terakhir) | 1. Nyalakan |
| **Skill & analisa** | Daftar skill. Setelan: NYALA, mode jadwal, jam/interval, hari, model, effort, kode SETUP, batas waktu. Tombol tambah, edit `SKILL.md` (Notepad), hapus (dipindah ke `skill\_terhapus`) | 3. Skill & analisa |
| **Tool** | Centang tool yang boleh dipakai Claude saat analisa | 3 → Tool yang diizinkan |
| **Telegram** | Cara kirim (bot/akun), token, API akun, login akun, channel kotak masuk, chat pribadi, analisa lengkap ke PM, pesan rutin + kirim contoh | 4. Telegram |
| **TradingView** | Status chart, buka TradingView, skrip peluncur, lama tunggu chart | 5. TradingView |
| **Jurnal** | Kartu ringkasan, 50 analisa terakhir (pilih lalu **Hapus dari jurnal**), buka Excel/folder | 6. Jurnal |
| **Utilitas** | Cek kesiapan, kirim pesan uji, log, riwayat hasil, tes logika, buka `config.yaml` | 7. Utilitas |

Membuka Dashboard dua kali hanya memunculkan jendela yang sudah ada. **Stop** menghentikan generator beserta analisa yang sedang jalan. Kalau Dashboard tertutup tidak wajar (crash), generator dari Dashboard berhenti sendiri dalam satu menit.

## 4. Jadwal skill

Setiap skill punya jadwal sendiri (tab **Skill & analisa**, atau menu **3 → Daftar skill → (skill) → Jadwal analisa**). Ada dua mode:

- **Jam tertentu** memakai daftar jam WIB yang dipisah koma. Simpan kolom kosong untuk ikut jadwal global. Untuk membuat jadwal sendiri, ketik jamnya atau klik **Salin jam global** di Dashboard lalu edit angkanya.
- **Tiap sekian menit** memakai angka bulat dari 15 sampai 1440. Misalnya `60` dari `20:00` sampai `04:00` menjadwalkan analisa pada 20:00, 21:00, dan seterusnya sampai 04:00.

**Hari aktif** berlaku untuk tanggal jam itu. Untuk interval yang lewat tengah malam, hari aktif mengikuti **hari jam mulai**. Jadwal Jumat 20:00–04:00 tetap jalan sampai Sabtu 04:00 walaupun Sabtu tidak dicentang.

**Setelan global skill** ada di **Skill & analisa** pada Dashboard dan `MENU.bat`. Di situ kamu bisa mengatur model, effort, batas waktu, dan jam WIB bawaan (`09:00, 14:00, 20:00`). Perubahan jam global berlaku untuk skill yang sudah disimpan dengan pilihan ikut global. Jadwal lama yang kosong mulai ikut global setelah kamu menyimpannya kembali dalam mode **Jam tertentu**.

Beberapa skill boleh NYALA sekaligus. Kalau jadwalnya bersamaan, dijalankan bergantian (antre). Tidak pernah ada dua analisa berjalan bersamaan.

**Analisa sekarang saat generator sedang menganalisa** tidak ikut antre. Program menunggu sampai 5 detik, lalu menyatakan analisanya **dilewati**: Dashboard menulis *"Analisa dilewati: generator sedang menganalisa"*, dan menu teks menampilkan peringatan yang sama. Pesan untuk Claude yang sudah kamu tulis **tidak hilang**. Di Dashboard, pesan itu muncul lagi di jendela pesan saat kamu menekan **Jalankan**. Di menu teks, kamu ditawari memakainya lagi.

## 5. Yang terjadi saat satu analisa

1. Chat pribadi: **"CLI mulai analisa."**
2. Kalau TradingView Desktop belum terbuka, program membukanya lewat skrip peluncur dan menunggu chart siap.
3. Claude bekerja (biasanya 2–5 menit). Jawaban mentahnya disimpan di `sistem\data\hasil\`.
4. Blok **SETUP** dikirim ke channel lebih dulu (untuk bot), lalu **analisa lengkap** ke chat pribadi (kalau setelan "analisa lengkap ke PM" nyala).
5. Blok **SETUP RENDAH** dikirim sebagai pesan terpisah ke chat pribadi.
6. **Status akhir** ke chat pribadi: ✅ terkirim / ⚪ tidak ada setup (plus alasannya) / ❌ gagal (plus sebabnya).
7. Satu baris (atau lebih) tercatat di jurnal Excel.

## 5b. Analisa sekarang dengan pesan untuk Claude

Seperti memanggil skill di chat Claude lalu menambahkan "perhatikan juga …". Hanya untuk **Analisa sekarang**; jadwal otomatis tidak pernah memakai pesan.

- **Dashboard:** tekan **Jalankan** → muncul jendela pesan. Tulis atau tempel pesan (boleh panjang), lalu **Jalankan**. Kosongkan untuk analisa biasa.
- **MENU.bat:** **1. Nyalakan → Analisa sekarang → (skill)** → ketik `P` lalu Enter → Notepad terbuka. Tulis/tempel pesan, simpan (Ctrl+S), tutup Notepad. Enter saja = tanpa pesan.

Kalau analisa memakai pesan:
- Pesan **boleh mengalahkan aturan skill** (aturan keras, conviction, news). Claude diminta menyebut aturan mana yang dilanggar. Baris `Kode:`/`Sumber:` tetap dari program.
- **Semua blok dijadikan `SETUP RENDAH` oleh program**: masuk ke chat pribadimu, **tidak** ke bot. Cek dulu, lalu salin ke channel kalau mau dieksekusi.
- Di Telegram dan jurnal hanya ada cap **"dengan pesan"** (judul analisa, status, kolom Keterangan). **Isi lengkap pesan** disimpan di atas file hasil analisa (`sistem\data\hasil\`), hanya di PC-mu.

## 6. Jurnal Excel

File: `jurnal\Jurnal CLI.xlsx` (dibuat otomatis). Tiga lembar:

- **Ringkasan:** jumlah analisa per status, hasil TP/SL/BE, win rate, total dan rata-rata R, tabel per skill, petunjuk. Dibuat ulang otomatis, jangan diisi.
- **Jurnal:** satu baris per blok setup (analisa tanpa setup atau gagal tetap satu baris). Kolom program: waktu, skill, model, effort, status, isi blok (entry, SL, TP1–TP5, berlaku), Risk, RR TP1, RR TP terjauh, conviction, keterangan, durasi, token, Bobot API, file hasil.
- **Analisa:** teks analisa lengkap per ID. Cocok untuk mengirim file ini ke AI untuk evaluasi.

**Kolom yang kamu isi (kuning):**
- **Hasil:** pilih dari daftar (Tidak dieksekusi, Tidak kebuka, SL, BE, TP1–TP5, Tutup manual).
- **Catatan kamu:** bebas.
- **R didapat** terisi otomatis dari Hasil: TPn = jarak Entry ke TPn ÷ Risk, SL = −1, BE = 0. Rumus ini menganggap seluruh posisi ditutup di TP itu. Kalau ada partial atau tutup manual, **ketik angka R sendiri** di atas rumusnya; program tidak menimpanya.

Kalau Excel sedang dibuka saat analisa selesai, catatan tetap aman di `sistem\data\jurnal.jsonl` dan masuk ke Excel setelah file ditutup (analisa berikutnya, atau **Jurnal → Buka file Excel jurnal**). "Bobot API ($)" hanya perkiraan biaya kalau lewat API untuk membandingkan berat analisa, bukan tagihan.

### Hapus analisa dan reset jurnal

Jangan hapus baris langsung di Excel: barisnya muncul lagi di sinkron berikutnya, karena catatan aslinya (`sistem\data\jurnal.jsonl`) masih ada. Pakai cara di bawah. Keduanya ditolak kalau **Excel sedang dibuka** atau **generator sedang menganalisa**.

- **Hapus beberapa analisa** (misal analisa tes): **Dashboard → Jurnal** → pilih baris (Ctrl/Shift+klik untuk banyak) → **Hapus dari jurnal**. Atau **MENU.bat → 6. Jurnal → Hapus analisa dari jurnal** → ketik nomornya (`1,3` atau `2-5`) → ketik `YA`. Yang dihapus selalu analisanya utuh (semua baris setup-nya), dari catatan asli dan Excel; isian Hasil/Catatan di baris lain tidak tersentuh. Cadangannya: `sistem\data\jurnal-terhapus.jsonl`, dan file hasil analisanya dipindah ke `sistem\data\hasil\terhapus\`.
- **Reset jurnal (mulai dari nol)**: hanya di **MENU.bat → 6. Jurnal → Reset jurnal**, ketik `RESET`. Catatan asli, Excel, cadangan hapusan, dan file hasil milik analisa di jurnal dipindah ke `jurnal\arsip\<tanggal-jam>\`. Analisa berikutnya membuat jurnal baru.
- Folder `arsip`, `hasil\terhapus`, dan file `jurnal-terhapus.jsonl` boleh kamu hapus manual kapan saja; program tidak membacanya lagi.

## 7. Pesan rutin (tanda masih hidup)

Selama generator jalan terus menyala, pesan **"🟢 CLI masih nyala"** dikirim ke chat pribadi di jam yang diatur (bawaan 07:00, boleh beberapa jam). Isinya skill yang NYALA, jadwal dan jam jalan berikutnya, serta ringkasan 24 jam terakhir. **Pesan tidak datang di jam biasanya = generator mati.** Atur di tab **Telegram** (atau menu **4 → Pesan rutin**): nyalakan/matikan, jam kirim, kirim contoh.

## 8. Tool yang diizinkan

Saat analisa, Claude hanya diberi tool yang dicentang di tab **Tool**. Bawaannya 15: WebSearch, WebFetch, dan 13 alat TradingView untuk membaca chart (termasuk zona/label indikator, tabel, dan screenshot). Setiap tool yang dibuka menambah token di setiap panggilan, jadi buka seperlunya. Daftar lengkap, kegunaan, dan tool yang sebaiknya tetap ditutup ada di [skill/panduan-skill/PANDUAN.md](../skill/panduan-skill/PANDUAN.md) bagian 10. `ui_evaluate` selalu ditolak.

## 9. Model, effort, dan jatah Claude

- Model dan effort diatur **per skill**. "Ikut global" memakai bawaan di `sistem\config.yaml` (bagian `claude`).
- Nama singkat (`opus`, `sonnet`) diarahkan Claude Code ke versi yang ia kenal. Untuk mengunci versi, tulis nama lengkap, misalnya `claude-opus-5-5`.
- Analisa memakai **jatah langganan Claude yang sama** dengan chat Claude-mu yang lain (batas per 5 jam). Chat panjang di aplikasi bisa menghabiskan jatah sebelum jadwal CLI jalan. Tips: analisa manual di aplikasi sebaiknya dilakukan di chat baru.
- Kalau analisa sering terpotong, naikkan **batas waktu** skill itu.

## 10. Menambah atau mengubah skill

- Repo GitHub **tidak menyertakan skill** (skill adalah milik pribadi pemakai). Salin skill-mu ke `skill\<nama>\` atau buat baru.
- Tambah: tab **Skill & analisa → + Tambah skill** (kerangka dibuat, `SKILL.md` dibuka di Notepad), atau buat folder sendiri berisi `SKILL.md`. Skill baru mulai dalam keadaan **MATI** dengan kode **TES**.
- Cara menulis skill yang benar untuk mode otomatis (tanpa manusia, harus diakhiri SETUP / NO SETUP, tool yang tersedia, dll): [skill/panduan-skill/PANDUAN.md](../skill/panduan-skill/PANDUAN.md). Panduan ini juga bisa diberikan ke AI untuk membantu menulis skill.
- Uji dulu lewat **Analisa sekarang** dengan kode TES sebelum menyalakan jadwal atau mengganti kode.

## 11. Peta menu teks (MENU.bat)

```
1. Nyalakan          Generator jalan terus (jendela cmd sendiri) · Analisa sekarang (skill / mode uji)
2. Buka Dashboard
3. Skill & analisa   Pilih skill aktif · + Tambah skill · Daftar skill (jadwal, model & effort, kode,
                     batas waktu, edit, hapus) · Tool yang diizinkan
4. Telegram          Cara kirim · Token bot · API akun · Login akun · Channel kotak masuk ·
                     Chat pribadi laporan · Analisa lengkap ke PM · Pesan rutin
5. TradingView       Buka TradingView · Skrip peluncur · Lama tunggu chart
6. Jurnal            Ringkasan · Daftar analisa terakhir · Buka file Excel · Buka folder · Hapus analisa · Reset jurnal
7. Utilitas          Cek kesiapan · Kirim pesan uji (TES) · Log · Riwayat analisa · config.yaml · Tes logika
```

Baris status di atas menu: generator NYALA/MATI (dan terakhir aktif), skill yang nyala, cara kirim, kotak masuk, **PM** (✅ = chat pribadi siap menerima laporan), dan status TradingView.

## 12. Pertanyaan umum

**Kenapa ada setup di Telegram tapi tidak dieksekusi bot?** Kodenya masih `TES`. Lihat INSTALL bagian 8 untuk menyambung ke bot eksekutor.

**Claude kadang menulis baris `Catatan:` di blok SETUP.** Tidak berbahaya: bot eksekutor hanya menyimpannya untuk jurnal.

**Chart tidak dikembalikan ke XAU setelah analisa.** Disengaja: tiap analisa selalu mulai dengan memindah chart ke simbol yang dibutuhkan skill.

**Ingin melihat jawaban mentah Claude?** Tab **Utilitas → Riwayat analisa → Buka hasil terbaru**, atau folder `sistem\data\hasil\`.

**Setelan rusak setelah diedit manual?** Menu atau Dashboard akan menampilkan "Setelan … tidak valid". Kembalikan bagian yang diubah di `sistem\config.yaml` atau `skill\<nama>\setelan.yaml` (komentar di file menjelaskan nilai yang boleh).
