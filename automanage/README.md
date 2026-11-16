# Automanage MT5 + Bybit

Sistem yang mengeksekusi sinyal trading **tanpa emosi** ke dua broker sekaligus:

- **MT5 (Exness)**: gold dan BTC dari sinyal gambar chart.
- **Bybit (USDT Perpetual)**: koin crypto dari sinyal teks (format TraderAwam).

Reader mengubah sinyal sumber ke format `SETUP`. Eksekutor memvalidasi setup dan meminta konfirmasi Telegram sesuai SOP sumber. Setelah order dipasang, eksekutor menjaga posisi sesuai SOP, termasuk TP bertahap, SL ke BE, trailing, dan batas waktu limit.

Dikembangkan dari [MT5-Automation-Labs/mt5-telegram-signal-copier](https://github.com/MT5-Automation-Labs/mt5-telegram-signal-copier) (MIT). Yang dipakai ulang: perhitungan lot dan ide cek validasi. Pembaca, gerbang, eksekutor, penjaga posisi, dan bagian Bybit ditulis ulang.

```
Grup sinyal (gambar gold/BTC)   Grup sinyal (teks crypto)
            \                        /
             ▼                      ▼
   PEMBACA (akun Telegram-mu: gambar dibaca Claude, teks dibaca kode)
             │  posting SETUP berlabel "Ke: mt5" atau "Ke: bybit"
             ▼
   Channel privat "kotak masuk"
        │                         │
        ▼ Ke: mt5                 ▼ Ke: bybit
   BOT MT5                     BOT BYBIT
   gerbang → konfirmasi SOP    gerbang → konfirmasi SOP
   → order MT5 → penjaga       → order Bybit → penjaga
   jurnal sendiri              jurnal sendiri
```

> [Pasang di PC baru](Panduan/INSTALL.md) · [Buat Dashboard Bot.exe](Panduan/BUAT_EXE.md) · [Peta MENU.bat](Panduan/PETA_MENU.md) · [Format setup](Panduan/FORMAT_SETUP.md) · [Panduan Bybit](Panduan/BYBIT.md) · [Dashboard dan Pengawas](Panduan/DESKTOP.md)

## Isi folder

File utama di folder proyek

```
MENU.bat       ← menu terminal dan login pertama Pembaca
Dashboard Bot.exe ← launcher Dashboard, dibuat dengan skrip build
Panduan/       ← semua panduan: cara pasang, format setup, Bybit, dashboard
.env           ← rahasia (token, API Telegram), kamu isi sendiri dari .env.example
akun-bybit.env ← akun Bybit + API key, ditulis lewat menu (tidak ikut git)
akun-mt5.env   ← nama akun dan folder terminal MT5 (tidak ikut git)
README.md      ← panduan ini
CLAUDE.md      ← catatan untuk sesi Claude
sistem/        ← semua isi program (kode, setelan, jurnal, log)
```

## Program yang bisa dijalankan

| Program | Tugas | Butuh |
|---|---|---|
| **Pembaca sinyal** | Menonton daftar sumber sinyal, memposting `SETUP` berlabel ke kotak masuk | API Telegram akunmu, Claude Code (untuk sumber gambar) |
| **Bot MT5** | Mengerjakan `Ke: mt5` di akun tujuan yang dipilih per sumber | Bot Telegram #1 dan terminal MT5 terdaftar yang sudah login |
| **Bot Bybit** | Mengerjakan `Ke: bybit` di akun tujuan yang dipilih per sumber | Bot Telegram #2, API key akun Bybit **cross margin**, **one-way** |
| **Bot Pengawas** (opsional) | Melaporkan gangguan dan mengendalikan proses Reader, MT5, dan Bybit lewat chat pribadi | Bot Telegram #3, API ID/hash yang sama dengan Reader, ID akun admin |

Setiap proses bisa dinyalakan terpisah lewat menu atau dashboard. Program menggunakan pengunci lokal untuk mencegah proses yang sama berjalan ganda pada satu PC.

## Menu terminal

Di atas menu tampil status Reader, MT5, Bybit, dan Pengawas, jumlah sumber sinyal, dan status "hanya demo" MT5/Bybit.

```
1. Nyalakan            pembaca, bot MT5, bot Bybit, Pengawas (jendela terpisah)
2. Buka Dashboard      status, kontrol proses, dan empat panel log
3. Sumber sinyal       daftar sumber, + tambah, hapus, dan SOP PER SUMBER
4. Setelan universal
   1. MT5    hanya demo, magic, akun, direktori terminal, simbol per akun
   2. Bybit  hanya demo, batas nilai posisi, akun & API key Bybit (tanpa daftar pair)
5. Utilitas            coba baca gambar, cek kesiapan, tes manual Bybit, riwayat,
                       reset jurnal, reset bot, ganti token/kotak masuk, tes logika
```

- **Nyalakan:** nyalakan satu per satu, tutup jendelanya untuk mematikan. Menu menolak menyalakan program yang kuncinya belum diisi (atau pembaca tanpa sumber, bot Bybit tanpa akun), dan menyebut apa yang kurang.
- **SOP per sumber**, bukan per broker: tiap sumber punya file `sistem/sumber/<kode>.yaml` (risiko, pembagian TP, BE/trailing, entry, gerbang, pengaman, model Claude). Diubah lewat menu; nilai dicek dulu, komentar tetap utuh. **Bot membaca perubahan SOP sendiri dalam beberapa detik, tanpa restart.**
- **Setelan universal** (`sistem/config.yaml` untuk MT5, `sistem/config-bybit.yaml` untuk Bybit) menyimpan pengaturan bersama. Bawaan repo mengizinkan akun real. Aktifkan **Hanya boleh demo** jika ingin membatasi akun, dan ketik `REAL` jika menonaktifkannya lewat menu.
- **Riwayat setup** di Utilitas membuka jurnal MT5. Utilitas menyediakan **Reset jurnal MT5** dan **Reset jurnal Bybit** secara terpisah.
- **Reset bot** di `MENU.bat` menghapus log, jurnal dan cadangan MT5/Bybit, atau log dan ingatan Claude CLI untuk Reader. Hentikan proses yang dipilih, lalu ketik `RESET`. Akun, SOP, dan sesi Telegram tetap tersimpan. Order broker tetap berjalan, tetapi jurnal lama yang dipakai bot untuk melacaknya terhapus.

## Dashboard dan Bot Pengawas

Buka **Dashboard Bot.exe** atau pilih **MENU.bat → Buka Dashboard**. Tab Dashboard menampilkan status proses, log dan koneksi akun. Tab **Sumber sinyal**, **Setelan universal**, dan **Utilitas** ada di jendela yang sama. Cek kesiapan dan Tes logika menampilkan hasil sambil jendela tetap bisa digunakan. Kontrol trading ada di chat masing-masing eksekutor. [Cara membuat EXE](Panduan/BUAT_EXE.md)

Pengawas mengirim status saat nyala dan melaporkan gangguan selama prosesnya aktif. Di chat pribadinya, kirim `/status` untuk mengecek kondisi atau gunakan `/start`, `/stop`, dan `/restart` untuk memilih proses lewat tombol. Pengawas memakai koneksi Telethon dengan sesi tersendiri. Lihat [cara menyiapkan dashboard dan Pengawas](Panduan/DESKTOP.md).

## Sumber sinyal

`MENU.bat` → **Sumber sinyal** → *+ Tambah sumber* / *Hapus sumber*, atau pilih sumbernya untuk mengatur. Tiap sumber:

| | Pilihan |
|---|---|
| Kode | otomatis dan tetap: `S01`, `S02`, ... Kode sumber yang dihapus tidak dipakai ulang |
| Channel / grup | dipilih dari hasil deteksi sesi Telegram Pembaca |
| Topik | **semua**, **semua kecuali** beberapa (misal topik rekap/watcher), atau **hanya** topik tertentu |
| Diolah oleh | **Claude CLI** (semua chat ditafsirkan, cocok untuk channel apa pun) atau **bot pembaca kode** (teks format TraderAwam, tanpa Claude) |
| Tujuan | **MT5** atau **Bybit** (dipilih manual) |
| Akun tujuan | pilih satu atau lebih akun MT5 atau Bybit yang sudah didaftarkan, sesuai platform tujuan. Dua sumber boleh memakai akun yang sama |
| SOP | risiko, pembagian TP, BE/trailing, batas waktu limit, gerbang, **pengaman (dihitung untuk sumber ini saja)**, model Claude. Sumber baru mulai dari bawaan atau salinan SOP sumber lain |

Daftar disimpan di `sistem/sumber.yaml`, SOP di `sistem/sumber/`, dan daftar akun di `akun-mt5.env` serta `akun-bybit.env` di folder depan. File ini disimpan lokal. Hentikan Pembaca sebelum deteksi channel atau topik, lalu nyalakan ulang setelah mengubah sumber.

**MT5, terminal per akun.** Saat bot MT5 dinyalakan, bot menghubungkan terminal akun tujuan sesuai direktori yang terdaftar. Pemetaan simbol bisa diatur per akun. Dashboard menunjukkan status tiap akun, termasuk akun yang masih menjaga setup lama.

**Kode sumber di setiap setup.** Pembaca menulis `Kode: S01` di setiap `SETUP`/`UPDATE`. Bot memakai SOP sumber itu. **Setup tanpa kode yang dikenal ditolak.** Untuk setup manual, kirim ke kotak masuk dengan kode sumber yang sudah terdaftar.

**Pengaman per sumber:** maks setup aktif dan batas loss harian (persen dan/atau dolar) dihitung dari setup sumber itu saja. Tidak ada batas gabungan per akun (keputusan user).

**Bybit, akun per sumber:** bot Bybit memegang semua akun di `akun-bybit.env` sekaligus. Satu posisi per koin berlaku **per akun**: dua sumber di akun yang sama tidak bisa sama-sama memegang STX; di akun berbeda boleh. Tiap akun dicek saat dipakai: tersambung, API key **tanpa izin Withdraw**, one-way, **cross margin**, dan mode demo kalau "hanya demo" aktif. Konfirmasi di Telegram menulis akun yang dipakai.

**Diolah Claude** (cocok untuk channel apa pun, tanpa setting per channel). Claude membaca **semua** pesan di sumber itu, termasuk teks, gambar chart, caption, dan reply. Yang dilewati: GIF, stiker, video, voice, dan emoji doang. Claude lalu menafsirkan maksud trader:
- **Setup baru**, termasuk **beberapa setup dalam satu gambar/pesan** (masing-masing jadi `SETUP` sendiri).
- **Update** untuk setup lama: TP/SL kena, geser SL, cancel, close. Kalau trader me-reply sinyalnya, pembaca tahu setup mana yang dimaksud, dan `UPDATE` diberi `Ref:` ke postingan SETUP-nya, jadi tombol bot tepat sasaran. **Cancel**: di bot MT5 jadi tombol; di bot **Bybit** limit yang belum terisi **langsung dibatalkan otomatis**. **Close** tetap hanya tombol. BE dan trailing tetap ikut SOP bot.
- **Info** penting, atau **diabaikan** (obrolan, promosi, rekap).

Pesan yang datang berdekatan (gambar + caption + pesan susulan, album) ditampung beberapa detik lalu dibaca sekali jalan. **Ingatan per channel** (`sistem/data/ingatan.db`) berisi setup yang masih terbuka, 10 pesan terakhir, dan catatan "gaya channel" yang ditulis Claude sendiri (misalnya arti warna garis). Yang dikirim ke Claude hanya ringkasan ini, jadi hemat kuota. Setup hilang dari ingatan kalau Claude menilai sudah selesai, kalau jurnal bot mencatatnya selesai/ditolak/kedaluwarsa, atau setelah 7 hari. Model Claude diatur per sumber (bawaan: haiku untuk teks, sonnet untuk gambar).

Jawaban Claude tetap dicek **kode** sebelum diposting:
- **Arah:** SL di bawah entry = BUY, di atas = SELL. Kalau arah dari Claude beda, setup tidak dikirim.
- **TP** harus di sisi yang benar. **Rentang harga** (`ENTRY 74680-76500`, `TP 0.0064-0.0076`) dipecah jadi harga terpisah. **Dua entry / range:** dipakai batas yang paling dekat ke TP; yang lain jadi `Entry2`.
- **SL typo** (salah digit, salah desimal, atau di sisi yang salah untuk arah yang ditulis trader; dianggap mencurigakan kalau > 30% dari entry dan > 3× jarak TP terjauh): angka yang dimaksud **ditebak** (tebakan Claude dulu, lalu kode: geser desimal / buang satu digit), dikirim dengan baris `Ragu: SL di sinyal 772000 terlihat typo, ditebak 77200`. Tidak ada tebakan masuk akal → tidak dikirim, dilaporkan.
- **Simbol:** gold/BTC harus cocok dengan besar harganya (1.000–10.000 = gold, di atas 10.000 = BTC). Simbol lain (koin crypto) dipakai apa adanya. Tanpa simbol, ditebak dari harga.
- Claude **kurang yakin** → baris `Ragu:` → ⚠️. "High risk" → `Highrisk: ya`.
- Setup yang ditolak kode, atau Claude gagal/kuota habis, dilaporkan ke kotak masuk beserta alasannya.

Coba tanpa Telegram: MENU → Utilitas → **Coba baca gambar** (atau `python -m reader.main --coba-gambar FILE --teks "caption" --sumber S01` dari `sistem/`).

**Sumber teks** (crypto ke Bybit). Format TraderAwam lama (`NEW CALL · LONG`), baru (`🟢 LONG · STXUSDT`), dan bertanda dolar (`🟢 LONG $PEPE (link TradingView)`) dikenali:
- Yang diambil: arah, pair (kalau ada link TradingView, simbol dari link, misal `BYBIT:1000PEPEUSDT.P` → `1000PEPEUSDT`), Entry (limit/market), TP1–TP4, SL, "Risk High", masa berlaku ("Valid s/d …" / "Berlaku sampai …" / "⏳ s/d …"), dan batas harga (misal `batal jika < 0.71 sebelum entry`). Mode pembatalan di SOP sumber menentukan batas yang dipakai.
- Notifikasi "Setup dibatalkan / Waktu entry habis / LIMIT dibatalkan" → bot Bybit **langsung membatalkan limit koin itu** (setup yang masih menunggu konfirmasi ikut batal). "SL tersentuh" / "SL hit" → tombol tutup posisi; bot tidak menutup posisi sebelum tombol ditekan.
- Rekap/watcher, ringkasan pendek, "Entry filled", dan obrolan **diam**.
- Manajemen dari sumber (partial, BE, trailing) **tidak diikuti**: bot memakai SOP-mu sendiri.

Pembaca memposting pakai akunmu (bukan bot), karena bot Telegram tidak menerima postingannya sendiri. Akunmu harus admin di channel kotak masuk.

## Format setup

Pembaca menulisnya otomatis. Untuk setup manual, kirim ke channel kotak masuk dan isi `Kode:` dengan kode sumber yang sesuai. [Format kosong dan contoh](Panduan/FORMAT_SETUP.md)

```
SETUP
Kode: S01
Pair: XAUUSD
Arah: BUY
Entry: 3987.26
SL: 3981.26
TP1: 3993.26
TP2: 3999.26
Ke: mt5
Sumber: outlook_gold
```

- **`Kode:` wajib** (SOP sumber mana yang dipakai). Tanpa kode yang dikenal, setup ditolak.
- **`Ke: mt5` / `Ke: bybit` wajib.** Tanpa label, **semua bot diam**. Hanya bot tujuan yang mengerjakan.
- Baris lain: `Order` (LIMIT/STOP/MARKET), `Risk` (`0.5%` / `$20`, untuk setup ini saja), `Highrisk`, `Berlaku` (batas waktu limit dari sinyal), `Entry2`/`SL2` (Entry2 = batas range yang jauh), `Ragu` (peringatan dari pembaca), `Catatan`.
- `UPDATE` dengan `Aksi: CANCEL / CLOSE` (dan `Ke:`): CLOSE selalu jadi tombol tutup posisi; CANCEL jadi tombol di bot MT5, sedangkan bot Bybit langsung membatalkan limit yang belum terisi. `Ref:` (ditulis pembaca kalau trader me-reply sinyalnya) = tombol untuk setup itu persis; tanpa `Ref:`, kalau ada `Pair:`, untuk setup koin itu.
- Angka boleh `3987.26` atau `3.987,26`. Setup yang salah format dilaporkan ke chat pribadimu, tidak dieksekusi.

## Apa yang dicek gerbang

Sama untuk kedua bot:
- Simbol ada di broker, SL dan TP di sisi yang benar, SL belum terlewat harga. TP1 sudah terlewat → market ditolak, limit tetap ditanya dengan tanda ⚠️.
- Postingan tidak basi (30 menit), entry tidak terlalu jauh dari harga (bawaan 1%; sumber Bybit swing biasanya 10%).
- **Harga sudah lebih bagus dari entry** (SOP: `market`): langsung market. Untuk **sinyal range**: harga di dalam range atau lebih bagus → market dengan catatan ℹ️ (tidak memaksa konfirmasi); di luar range → limit di batas yang dekat TP. Sinyal tanpa range yang harganya sudah lewat entry → market dengan ⚠️ (ditanya, kecuali SOP sumber mematikan konfirmasi peringatan).
- **Dobel** (sama persis dalam 24 jam) ditolak. **Mirip** (pair & arah sama, entry dekat) ditanya dengan tanda ⚠️.
- Bot tidak sedang di-stop, setup aktif **sumber itu** belum penuh (bawaan 3), batas loss harian **sumber itu** belum kena (bawaan 3%), ukuran tidak di bawah minimum broker.
- **Konfirmasi** bisa diatur per sumber (SOP · Gerbang). Di mode otomatis, peringatan ⚠️ masih minta konfirmasi secara bawaan. Aktifkan **Izinkan tebakan Claude tanpa konfirmasi** agar `Ragu:` saja, termasuk SL tebakan, bisa lewat otomatis. Matikan **Konfirmasi jika ada peringatan** jika semua peringatan cukup dilaporkan. Setup tetap harus lolos gerbang.
- Setelah kamu tekan **Ya**, harga dicek ulang sekali lagi sebelum order dikirim.

Khusus Bybit:
- **Koin dicari sendiri** di daftar Bybit (`$STX`, `STX`, `STXUSDT.P` → `STXUSDT`). Tidak ketemu → tidak dieksekusi, **dilaporkan ke chat konfirmasi**. Koin berkelipatan (`1000PEPEUSDT`) dipakai **hanya kalau harga entry di sinyal dekat dengan harga pasar koin itu** (selisih maks 30%), jadi harganya yang membuktikan skalanya; konfirmasi diberi ⚠️ (ditanya, kecuali SOP sumber mematikan konfirmasi peringatan). Angka skala PEPE biasa tetap ditolak.
- **Satu setup per koin per akun** (Bybit hanya punya satu posisi per koin), termasuk yang masih menunggu konfirmasi. Cek dobel juga per akun.
- Sinyal yang masa berlakunya sudah lewat ditolak.

## SOP penjaga posisi

Sama untuk kedua bot; angkanya diatur **per sumber** (MENU → Sumber sinyal → pilih sumber):

- **Risiko per setup:** % saldo (bawaan 1%) atau nominal tetap. Leverage/margin yang ditulis trader tidak diikuti. Ukuran posisi = risiko ÷ jarak SL, jadi rugi kalau kena SL selalu sebesar itu.
- **Mode TP:** `partial` (ukuran dibagi per TP, bawaan 3 TP: 50/30/20; angka 0 = tanpa ukuran di TP itu) atau `satu` (semua di TP ke-n).
- **TP1 tersentuh** → SL ke entry + 10% jarak SL. **TP2** → SL ke TP1, **TP3** → SL ke TP2, dst. SL tidak pernah dilonggarkan. BE dan trailing jalan menurut **harga**, berdiri sendiri dari mode TP.
- Geser SL gagal → dicoba ulang tiap 15 detik. Harga sudah lewat SL baru sebelum sempat digeser → ditutup di harga pasar.
- **Mode pembatalan setup** ada di SOP sumber → Entry. **Manual** (`manual`) memakai jumlah jam di SOP (bawaan 12 jam). **Ikut sinyal (waktu saja)** (`sinyal_waktu`) memakai `Berlaku`. **Ikut sinyal (waktu dan batas harga)** (`sinyal`) memakai `Berlaku` dan memantau `Batal sebelum entry` sampai fill pertama. Kedua mode sinyal memakai jam SOP jika sinyal tidak mencantumkan batas waktu. Order baru menyimpan aturan yang dipakai saat dipasang.
- Semua status ada di jurnal. Setelah restart, bot membaca ulang kondisi broker dan melanjutkan, termasuk TP yang kena saat bot mati.

Bedanya di Bybit (detail di [BYBIT.md](Panduan/BYBIT.md)):

| | MT5 | Bybit |
|---|---|---|
| Entry | 1 order per kaki TP | 1 order untuk total ukuran, dengan SL |
| TP bertahap | TP di tiap order | Setelah terisi, bot memasang LIMIT reduce-only di tiap TP |
| BE / trailing | SL tiap tiket | SL posisi (satu untuk semua kaki) |
| Ukuran | lot | koin |
| Leverage | dari akun | **tertinggi untuk koin itu**, dibaca per setup; akun wajib **cross margin** |

## Perintah di chat pribadi dengan bot

Berlaku di **kedua** bot, masing-masing untuk brokernya sendiri:

| Perintah | Fungsi |
|---|---|
| `/status` | Saldo, setup aktif, TP yang sudah kena |
| `/stop` | Tolak setup baru + batalkan semua limit. Posisi yang jalan tetap dijaga |
| `/lanjut` | Terima setup lagi |
| `/limit` | Daftar limit yang menunggu, dengan tombol batal per setup |
| `/hapuslimit` | Hapus semua limit yang belum terisi (ada konfirmasi) |
| `/tutupsemua` | Tutup semua posisi dan limit bot (ada konfirmasi) |
| `/resetjurnal` | Hapus riwayat setup, ada cadangan (ada konfirmasi). Hanya kalau tidak ada setup aktif |

## Pasang dan uji

Langkah lengkap dari nol ada di [INSTALL.md](Panduan/INSTALL.md). Ringkasnya:

1. Pasang Python, MT5 desktop (jika dipakai), dan Claude Code. Lalu `pip install -r sistem/requirements.txt`.
2. Salin `.env.example` jadi `.env`, lalu isi ID kotak masuk, ID admin, dan API Telegram. Token bot juga bisa diisi lewat **Utilitas → Isi/ganti token bot Telegram**. Masukkan API key Bybit lewat **Setelan universal → Bybit → Akun Bybit**.
3. Daftarkan akun MT5 atau Bybit di **Setelan universal**, lalu buka `MENU.bat` → **Sumber sinyal** → **Tambah sumber** untuk login Pembaca pertama kali. Pilih channel, aturan topik, pengolah, platform, akun tujuan, dan SOP awal.
4. `MENU.bat` → Utilitas → **Cek kesiapan** → semua ✅.
5. Nyalakan Reader dan eksekutor yang dipakai dari menu atau dashboard. Jika memakai Pengawas, isi token khususnya lalu nyalakan proses Pengawas.

Uji di **akun demo** dulu: MT5 demo Exness, dan **Demo Trading** Bybit.

## Akun cent (MT5)

Bot mengenali akun cent Exness (mata uang **USC**). Nilai dolar di setelan dan baris `Risk: $20` otomatis dikali 100. Cocokkan simbol lewat **Setelan universal → MT5 → Akun MT5 → pilih akun → Cocokkan otomatis dengan broker di MT5**. Untuk akun real, pastikan **Hanya boleh akun demo** mati.

## Tes logika (tanpa broker dan Telegram)

`MENU.bat` → Utilitas → **Tes logika program**, atau:

```
cd sistem
python -m pytest -q
```

Tes memakai MT5, Bybit, Telegram, dan Claude tiruan. Cakupannya termasuk alur eksekusi, penjagaan posisi, pembatalan, serta kontrol dan alert Pengawas. Tes Qt opsional mencakup antarmuka PySide6 lama. Dashboard yang dibuka dari menu memakai Tkinter.

## Isi folder sistem/

```
copier/setup_format.py  teks postingan -> Setup (format standar, label Ke:, Berlaku:)
copier/gate.py          cek dasar, dobel/mirip di jurnal, pengaman
copier/executor.py      hitung ukuran (2 mode risiko), pasang order per TP, batas waktu limit
copier/guard.py         penjaga posisi: fill, BE, trailing, kedaluwarsa, retry
copier/journal.py       jurnal SQLite
copier/broker.py        pembungkus MetaTrader5
copier/broker_bybit.py  pembungkus pybit (Bybit USDT Perpetual): order, SL/TP, cari koin, leverage
copier/app.py           alur kotak masuk -> konfirmasi -> eksekusi, perintah (bot MT5)
copier/app_bybit.py     bot Bybit: adaptor ala MT5, TP parsial reduce-only, satu posisi per koin
copier/main.py          titik masuk bot MT5
copier/main_bybit.py    titik masuk bot Bybit
copier/supervisor.py    kontrol proses dan alert Telegram lewat Telethon
copier/executor_telegram.py  sesi Telethon dan antrean update eksekutor
copier/telegram.py      klien Bot API untuk utilitas cek Telegram dan cari ID
copier/process_control.py  start/stop dan status kesiapan proses
copier/envfile.py       letak .env (folder depan)
copier/single.py        pencegah program jalan dobel
reader/main.py          pembaca: menonton daftar sumber
reader/sources.py       daftar sumber (sumber.yaml)
reader/wizard.py        tambah sumber langkah demi langkah
reader/brain.py         otak Claude: instruksi, panggilan claude -p hemat, jawaban -> SETUP/UPDATE (dicek kode)
reader/memory.py        ingatan per channel (data/ingatan.db): konteks setup, pesan terakhir, gaya channel
reader/pipeline.py      saringan pesan, penampung paket, alur paket -> postingan
reader/chart.py         angka label -> harga; cara lama: hasil baca gambar -> SETUP
reader/vision.py        cara lama: panggilan Claude khusus gambar
reader/textsignal.py    sinyal teks TraderAwam -> SETUP, notifikasi -> UPDATE
reader/updates.py       pesan teks sumber gambar -> UPDATE
panel/                  menu terminal, Dashboard Tkinter, sumber, setelan, akun dan utilitas
copier/sopfiles.py      letak setelan (universal, sumber/<kode>.yaml) + migrasi dari susunan lama
copier/profiles.py      SOP per sumber di bot: gerbang/eksekutor/penjaga per sumber, baca ulang otomatis
copier/accounts.py      akun Bybit (akun-bybit.env)
copier/accounts_mt5.py  terminal dan metadata akun MT5
copier/multi.py         rute eksekusi dan penjagaan per sumber dan akun
panel/dashboard.py      dashboard Tkinter dari EXE atau MENU.bat
panel/reset_bot.py      reset data lokal dari menu terminal
build-dashboard.ps1     build EXE dan shortcut Windows
desktop/                kode Trading Manager lama (PySide6)
config.yaml             setelan universal MT5 (+ bagian pembaca)
config-bybit.yaml       setelan universal Bybit
sumber.yaml, sumber/    daftar sumber dan SOP per sumber (tidak ikut git)
tests/                  tes otomatis (broker, Telegram, Claude tiruan)
```

## Disclaimer

Trading forex, logam, indeks, CFD, dan futures crypto dengan margin/leverage berisiko tinggi dan bisa rugi melebihi deposit. Software ini tanpa garansi dan bukan nasihat keuangan. Selalu uji di akun demo dulu.

## Lisensi

MIT © 2026 StanleyDaniels (kode asli). Perubahan di repo ini mengikuti lisensi yang sama (`sistem/LICENSE`).
