# Eksekutor Bybit

Ada dua bagian:

1. **Bot eksekutor Bybit** (`MENU.bat` → Nyalakan → Bot Bybit): mengerjakan postingan `Ke: bybit` di channel kotak masuk yang **sama** dengan bot MT5, dengan alur yang sama: gerbang, tombol konfirmasi, TP bertahap, BE, trailing, kedaluwarsa limit, loss harian, jurnal, dan semua perintah `/status`, `/limit`, `/tutupsemua`, dan seterusnya.
2. **Menu tes manual** (`MENU.bat` → Utilitas → Tes manual Bybit): mencoba fungsi dasar satu per satu secara manual (bagian bawah dokumen ini).

## Bot eksekutor Bybit

### Bedanya dengan MT5: satu posisi per koin

| | MT5 | Bybit |
|---|---|---|
| Entry | 1 order per kaki TP | **1 order** untuk total qty semua kaki, dengan SL |
| TP bertahap | TP di tiap order | Setelah entry terisi, bot memasang **LIMIT reduce-only** di TP1, TP2, dst. dengan qty kaki masing-masing |
| BE / trailing | SL tiap tiket digeser | **SL posisi** digeser (satu SL untuk semua kaki yang tersisa) |
| Setup bersamaan | Boleh pair sama | **Satu setup aktif per koin per akun**, termasuk yang menunggu konfirmasi |
| Ukuran | lot | qty dalam **koin** (bot menulis "koin" di Telegram) |

### Leverage: mentok per koin, risiko dari SL

- **Qty dihitung dari anggaran risiko dan jarak SL.** Risiko per setup menjadi dasar ukuran posisi. Hasil akhir dipengaruhi harga eksekusi dan biaya.
- **Leverage = tertinggi yang tersedia untuk koin dan ukuran posisi itu** (`leverage_mode: maks`, bawaan). Bot membaca tingkat *risk limit* untuk setiap setup.
- Leverage hanya menentukan **margin** yang dipakai. Konfirmasi Telegram menulis: `Leverage 75x (maks koin ini) · margin ≈ 21.66 USDT`.
- **Akun wajib cross margin.** Bot memeriksa mode margin saat startup dan mencatat alasan jika akun gagal pemeriksaan. Jika mode margin gagal terbaca saat eksekusi, bot menerapkan batas leverage cadangan.
- Mau angka tetap: MENU → Sumber sinyal → pilih sumber Bybit → SOP · Risiko dan leverage → Leverage = `tetap`, lalu isi angkanya.

### Setup mana yang diambil bot Bybit?

Hanya postingan berlabel **`Ke: bybit`**. Label itu ditulis pembaca menurut sumbernya (MENU → Sumber sinyal, tujuan *Bybit*), bersama `Kode:` sumbernya. Setup tanpa kode yang dikenal ditolak. Postingan `Ke: mt5` atau tanpa label diabaikan diam-diam. Bot MT5 juga hanya mengambil `Ke: mt5`, jadi satu sinyal tidak mungkin ditawarkan dua bot.

**Nama koin dicari sendiri** di daftar Bybit USDT Perpetual, tanpa perlu didaftar: `STXUSDT`, `STX`, `$STX`, `STX/USDT`, `STXUSDT.P`, `STXUSD` semuanya jadi `STXUSDT`. Koin yang tidak ada (atau tidak bisa ditrade) **tidak dieksekusi**, dan bot melapor ke chat pribadimu (chat yang sama dengan tombol konfirmasi): `🚫 Setup #N ... ditolak: koin XUSDT tidak ada di Bybit USDT Perpetual`. Koin berkelipatan seperti `1000PEPEUSDT` dipilih untuk sinyal `PEPE` **hanya kalau harga entry di sinyal dekat dengan harga pasar 1000PEPEUSDT** (selisih maks 30%), karena berarti angka sinyal memang skala koin itu; konfirmasinya diberi ⚠️ "Koin PEPE di Bybit adalah 1000PEPEUSDT", jadi selalu ditanya. Kalau angkanya skala PEPE biasa (dikali 1000 lebih kecil), tetap ditolak dan bot menyebutkan harganya di laporan. Sinyal TraderAwam yang punya link TradingView (`BYBIT:1000PEPEUSDT.P`) langsung memakai simbol dari link.

Gerbang Bybit memakai jarak entry maks **10%** dari harga (MT5 1%), karena sinyal swing sering memasang limit jauh dari harga. Ubah di SOP sumbernya → Gerbang kalau perlu.

### SOP per sumber, akun per sumber

SOP Bybit diatur **per sumber**: `MENU.bat` → **Sumber sinyal** → pilih sumber Bybit (file `sistem/sumber/<kode>.yaml`). Isinya sama dengan MT5 (risiko, pembagian qty per TP, BE, trailing, gerbang, pengaman untuk sumber itu, model Claude) plus leverage dan batas waktu limit. Bot membaca perubahan sendiri, tanpa restart.

Yang sama untuk semua sumber Bybit ada di **Setelan universal → Bybit** (`sistem/config-bybit.yaml`): hanya demo, nilai posisi maksimal, dan **Akun Bybit**. **Tidak ada daftar pair**: koin selalu dicari dan dicocokkan sendiri (keputusan user, koinnya terlalu banyak untuk didaftar).

**Akun per sumber.** Daftarkan akun di **Setelan universal → Bybit → Akun Bybit**, lalu pilih satu atau lebih akun di pengaturan sumber. Dua sumber boleh berbagi akun. Satu posisi per koin dan cek dobel berlaku **per akun**. Bot memeriksa koneksi, izin API, one-way, cross margin, dan batas demo bila diaktifkan. Akun yang gagal dicek ditandai di Dashboard. Akun tujuan lain yang lolos tetap bisa menerima setup.

| Pengaturan | Di mana | Arti |
|---|---|---|
| Leverage | SOP sumber | **maks** (bawaan): tertinggi untuk koin itu, dibaca per setup. **tetap**: angka di baris berikutnya. Risiko tetap dari jarak SL |
| Leverage tetap (x) | SOP sumber | dipakai kalau leverage = tetap |
| Nilai posisi maksimal (USDT) | Setelan universal | qty × harga satu setup tidak lebih dari ini |
| **Batas waktu limit** | SOP sumber | **Manual** memakai jam SOP. **Ikut sinyal (waktu saja)** memakai `Berlaku`. **Ikut sinyal (waktu dan batas harga)** juga memakai `Batal sebelum entry` sampai fill pertama. Kedua mode sinyal memakai jam SOP jika waktu tidak tersedia. |
| Limit dibatalkan setelah (jam) | SOP sumber | dipakai di mode manual, atau kalau sinyal tidak menulis masa berlaku |

Di kedua mode **Ikut sinyal**, pembaca menyalin masa berlaku ke baris `Berlaku: 2026-11-11 01:08 WIB` di SETUP. Konfirmasi di Telegram menulis `Batas limit: 11/11 01:08 (dari sinyal)` atau `(12 jam)`. Sinyal yang masa berlakunya sudah lewat ditolak gerbang.

### Pasang bot Bybit

1. **Buat bot Telegram kedua** di @BotFather. Satu token tidak bisa dipakai dua program sekaligus.
2. **Jadikan bot itu admin** di channel kotak masuk, sama seperti bot MT5.
3. **Simpan token Bybit** lewat **MENU.bat → Utilitas → Isi/ganti token bot Telegram → Bybit**. Menu menyimpan token sebagai `BYBIT_TG_BOT_TOKEN` di `.env`. ID channel dan ID admin memakai setelan yang sama dengan MT5. Masukkan API key dan secret lewat **Setelan universal → Bybit → Akun Bybit**. Akun tersimpan di `akun-bybit.env`.
4. **Pastikan akun Bybit cross margin dan one-way.** Akun dengan mode yang tidak sesuai diblokir. Setelan universal bawaan mengizinkan live. Aktifkan **Hanya boleh demo/testnet** untuk membatasi akun.
5. Tambah sumber Bybit di **Sumber sinyal**, pilih akun tujuannya, lalu cek risiko, leverage, dan batas waktu limit. Nilai posisi maksimal ada di **Setelan universal → Bybit**.
6. Nyalakan Bot Bybit dari Dashboard atau `MENU.bat`. Cek status tiap akun tujuan di Dashboard dan hasil pemeriksaan di **Utilitas → Cek kesiapan**.

Jurnal Bybit ada di `sistem/data/jurnal-bybit.db` dan log di `sistem/logs/bot-bybit.log`. Bot MT5 dan Bybit bisa nyala bersamaan. Panel Bybit di dashboard menampilkan log baru dan kontrol proses. Konfirmasi setup dan hasil order muncul di chat Bybit. Saat Pengawas aktif, peringatan dan error yang dipantau dikirim ke chat Pengawas. [Panduan dashboard dan Pengawas](DESKTOP.md)

### HTTP 403 dan restart

Setelah HTTP 403, eksekutor Bybit menjeda request API selama 10 menit. Jeda disimpan di PC agar tetap berlaku saat proses dinyalakan ulang. Log mencatat endpoint dan jumlah percobaan request dalam lima detik menjelang 403. Jika memakai Pengawas, `/status` menunjukkan sisa jeda dan `/start` atau `/restart` meminta kamu menunggu sampai jeda selesai.

Timeout API tercatat sebagai **Bybit API terganggu** untuk akun dan operasi terkait. Setelah pembacaan yang sama berhasil, log dan chat Pengawas menampilkan **Bybit API pulih**. Untuk request perubahan order yang timeout, pembacaan berikutnya bisa membuktikan koneksi pulih. Status order tersebut tetap perlu dicocokkan dengan data broker.

### Yang perlu diuji di demo

- Setup MARKET 3 TP: muncul 1 posisi, lalu 3 order limit reduce-only di TP1–TP3.
- TP1 kena: SL posisi pindah ke BE. TP2 kena: SL pindah ke TP1.
- Setup LIMIT: TP muncul **setelah** limit terisi. Interval pengecekan bisa diatur di **Setelan universal → Bybit → Bot**.
- Limit tidak terisi sampai masa berlaku sinyal ("Valid s/d"): dibatalkan. Tanpa masa berlaku: setelah jam cadangan (12 jam).
- Konfirmasi menulis leverage dan margin (`Leverage 75x (maks koin ini) · margin ≈ …`); di aplikasi Bybit leverage koin itu ikut berubah.
- Setup koin yang tidak ada (misal `FAKEUSDT`): tidak ada order, muncul laporan ditolak di chat.
- Tombol tutup / `/tutupsemua`: posisi tertutup, sisa order TP hilang.
- Restart bot di tengah posisi berjalan: penjagaan lanjut.

Kalau bot mati saat limit terisi, posisi hanya punya SL sampai bot nyala lagi; begitu nyala, bot langsung memasang TP parsial yang belum ada (teruji di skenario).

Profit per kaki yang ditulis bot adalah **perkiraan dari harga, belum termasuk fee**. Angka pasti ada di riwayat Bybit.

## Menu tes (fungsi dasar)

Isinya fungsi dasar saja, supaya kamu bisa mengecek di akun demo bahwa koneksi dan setiap perintah jalan.

- Pasar: **USDT Perpetual** (futures, contoh `BTCUSDT`, `ETHUSDT`)
- Mode posisi: **one-way** (satu posisi per koin). Ini bawaan Bybit. Kalau akunmu diubah ke hedge mode, order akan ditolak dengan kode `10001`.
- Qty diisi dalam **jumlah koin** (contoh `0.001` BTC), bukan lot.

## Fungsi yang bisa dites

| Menu | Fungsi | Di kode |
|---|---|---|
| 1 | Cek koneksi, saldo, izin API key | `connect`, `balance`, `key_info` |
| 2 | Harga dan aturan simbol (kelipatan harga, qty minimum, leverage maks) | `tick`, `spec` |
| 3 | Ganti simbol untuk tes berikutnya | Menu tes manual |
| 4 | Atur leverage | `set_leverage` |
| 5 | Buka posisi MARKET, SL/TP opsional | `place(..., "MARKET", ...)` |
| 6 | Pasang LIMIT, SL/TP opsional (aktif setelah terisi) | `place(..., "LIMIT", ...)` |
| 7 | Pasang / geser SL-TP posisi (0 = hapus) | `set_sl_tp` |
| 8 | Ubah LIMIT: harga, qty, SL, TP | `amend` |
| 9, 10 | Batalkan satu / semua LIMIT | `cancel`, `cancel_all` |
| 11 | Tutup posisi penuh atau sebagian (market reduce-only) | `close` |
| 12 | Lihat posisi dan order terbuka | `positions`, `open_orders` |

Harga dibulatkan otomatis ke kelipatan harga simbol, dan qty dibulatkan ke **bawah** ke kelipatan qty. Penolakan Bybit tidak membuat menu mati; yang muncul adalah kode error dan penjelasannya.

## Langkah tes

1. **Buka Demo Trading Bybit.** Login di bybit.com, klik foto profil, lalu pilih **Demo Trading**. Saldo demo diberikan gratis.
2. **Buat API key di mode demo itu juga.** Tetap di Demo Trading, buka foto profil, lalu **API**, lalu **Create New Key** (System-generated).
   - Izin: centang **Contract: Orders + Positions** saja.
   - **Jangan centang Withdraw** atau Transfer.
   - Key dari akun asli **tidak berlaku** di demo, begitu juga sebaliknya.
3. **Masukkan akunnya**: `MENU.bat` → Setelan universal → Bybit → **Akun Bybit** → + Tambah akun (nama, mode `demo`, key, secret). Ketik sendiri di jendela menu, jangan tempel ke chat. Tersimpan di `akun-bybit.env`.
4. **`MENU.bat` → Utilitas → Tes manual Bybit** (memilih akun demo/testnet kalau ada lebih dari satu). Coba berurutan:
   1. Menu 1, cek koneksi. Harus muncul `[OK] terhubung ke Bybit DEMO`.
   2. Menu 2, lihat qty minimum simbol.
   3. Menu 6, pasang LIMIT jauh dari harga (tidak akan terisi) dengan SL/TP, lalu menu 8 untuk mengubahnya, dan menu 9 untuk membatalkannya.
   4. Menu 5, buka MARKET qty minimum tanpa SL/TP, lalu menu 7 untuk memasang SL/TP, dan menu 7 lagi untuk menggesernya (cek di aplikasi Bybit apakah garisnya pindah).
   5. Menu 11, tutup setengah, lalu tutup sisanya.
5. Cocokkan setiap langkah dengan yang terlihat di aplikasi atau web Bybit (tab Demo Trading).

Tes manual tersedia dari menu terminal dan Dashboard, khusus akun `demo` atau `testnet`. Setiap order ditanya `y/n` dulu sebelum dikirim.

## Error yang sering muncul

| Kode | Arti |
|---|---|
| 10003 | API key tidak dikenal. Biasanya key dibuat di mode lain (key asli dipakai di demo) |
| 10004 | API secret salah atau terpotong waktu disalin |
| 10005 | Izin Contract belum dicentang |
| 10002 | Jam Windows tidak sinkron. Buka Settings, Time, lalu **Sync now** |
| 10010 | API key dikunci ke IP lain |
| 110007 | Saldo tidak cukup |
| 10001 | Angka ditolak, atau akun dalam hedge mode |

