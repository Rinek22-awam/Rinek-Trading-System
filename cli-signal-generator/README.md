# CLI Signal Generator

Program Windows yang membuat **sinyal trading otomatis dengan Claude**. Pada jam yang dijadwalkan (atau saat dipicu), program menjalankan **Claude Code CLI** untuk membaca chart **TradingView** di aplikasi Desktop, menganalisa sesuai **skill** (instruksi analisa milikmu), lalu meneruskan setup yang ditemukan sebagai **blok `SETUP`** ke channel Telegram. Bot eksekutor (misalnya Automanage MT5/Bybit) membaca channel itu dan mengeksekusi order sesuai SOP-nya sendiri.

> **Prinsip:** Claude hanya menganalisa dan menulis setup. Order, lot, SL/TP, BE, dan trailing dikerjakan bot eksekutor. "Tidak ada setup" adalah jawaban yang sah, jadi Claude tidak dipaksa memberi sinyal.

## Mulai dari sini

| Kamu ingin… | Baca |
|---|---|
| Memasang di PC baru dan menyiapkan semuanya | [panduan/INSTALL.md](panduan/INSTALL.md) |
| Memakai sehari-hari (Dashboard, jadwal, jurnal, dll) | [panduan/PENGGUNAAN.md](panduan/PENGGUNAAN.md) |
| Membuat atau mengubah skill analisa | [skill/panduan-skill/PANDUAN.md](skill/panduan-skill/PANDUAN.md) |
| Melihat format blok SETUP | [skill/FORMAT_SETUP.md](skill/FORMAT_SETUP.md) |

Satu pintu untuk semuanya: **`MENU.bat`** (menu teks), yang juga membuka **Dashboard** (jendela Tkinter).

## Cara kerja

```
Jadwal / tombol "Analisa sekarang"
        │
        ▼
Program (Python) ── menyusun perintah: aturan mode otomatis + format SETUP + isi SKILL.md
        │
        ▼
Claude Code CLI (claude -p) ── hanya diberi 15 alat: baca chart lewat MCP TradingView + cari berita
        │                        (TradingView Desktop, port 9222)
        ▼
Jawaban: analisa + blok SETUP / SETUP RENDAH / NO SETUP
        │
        ├─► blok SETUP ──────────► channel Telegram "kotak masuk" ──► bot eksekutor (Automanage)
        ├─► analisa lengkap, SETUP RENDAH, status ──► chat pribadi kamu
        └─► jurnal Excel (otomatis) + file hasil mentah
```

## Fitur

- **Skill per folder** (`skill\<nama>\SKILL.md`), masing-masing dengan setelan sendiri: saklar NYALA/MATI, jadwal (**jam tertentu** atau **tiap sekian menit** di antara jam mulai dan sampai, boleh lewat tengah malam), hari aktif, model dan effort Claude, kode SETUP, dan batas waktu.
- **Kode `TES` sebagai pengaman.** Setup berkode TES ditolak bot eksekutor. Mengganti ke kode sungguhan wajib mengetik `REAL`.
- **`SETUP RENDAH`** (conviction rendah) tidak dikirim ke bot, tapi ke chat pribadi, supaya kamu yang memutuskan.
- **Analisa sekarang bisa diberi pesan untuk Claude** (Dashboard: jendela pesan; menu: Notepad). Pesan boleh mengalahkan aturan skill, jadi semua blok dari analisa itu otomatis jadi `SETUP RENDAH` dan masuk ke chat pribadi dulu. Lihat [panduan/PENGGUNAAN.md](panduan/PENGGUNAAN.md).
- **Daftar izin tool.** Claude hanya diberi alat yang dibutuhkan (hemat ~28 ribu token per panggilan dan lebih aman). Bisa diatur di menu.
- **Jurnal Excel otomatis** (`jurnal\Jurnal CLI.xlsx`): satu baris per setup, ringkasan, win rate, dan R yang dihitung otomatis dari kolom Hasil yang kamu isi.
- **Pesan rutin** "🟢 CLI masih nyala" ke chat pribadi di jam yang diatur. Pesan tidak datang berarti generator mati.
- **Dashboard** berisi semua isi menu dalam tujuh tab, dengan log langsung. Generator dari Dashboard jalan tanpa jendela cmd dan ikut mati saat Dashboard ditutup.

## Peta folder

```
CLI-signal-generator\
  MENU.bat              ← pintu masuk (memasang paket Python yang kurang, lalu membuka menu)
  .env                  ← RAHASIA: token bot / API Telegram (dibuat lewat menu, jangan dibagikan)
  .env.example          ← contoh isi .env
  README.md             ← file ini
  panduan\              ← INSTALL.md dan PENGGUNAAN.md
  skill\                ← perpustakaan skill
    FORMAT_SETUP.md     ← format blok SETUP (ikut ke semua skill)
    panduan-skill\      ← panduan membuat skill + contoh (bukan skill)
    <nama-skill>\        ← skill milikmu: SKILL.md + setelan.yaml (tidak ikut repo)
  jurnal\               ← Jurnal CLI.xlsx (dibuat otomatis)
  sistem\               ← program
    config.example.yaml ← contoh setelan global (ikut repo)
    config.yaml         ← setelan global milikmu, dibuat otomatis dari contohnya (tidak ikut repo)
    mcp-tradingview.json← lokasi server MCP TradingView, dibuat otomatis (tidak ikut repo)
    generator\          ← kode Python
    prompt\             ← aturan mode otomatis, kerangka skill baru, prompt uji fungsi
    tests\              ← tes logika (pytest)
    data\               ← hasil mentah Claude, jurnal.jsonl, sesi, detak (dibuat otomatis)
    logs\               ← generator.log
```

## Untuk AI atau pengembang

**Lingkungan:** Windows 10/11, Python 3.13, Node.js, TradingView Desktop, Claude Code CLI ≥ 2.1.280 (login langganan), server MCP TradingView ([tradesdontlie/tradingview-mcp](https://github.com/tradesdontlie/tradingview-mcp)), bot Telegram.

**Peta kode** (`sistem\generator\`):

| Modul | Isi |
|---|---|
| `main.py` | Titik masuk perintah (`python -m generator.main daemon/once/test/ready/login/rutin`), alur satu analisa (`run_analysis`), generator jalan terus (`daemon`, antrean, detak, pesan rutin, file PID), cek kesiapan |
| `claude.py` | Menyusun prompt dan perintah `claude -p` (model, effort, daftar izin tool, MCP), menjalankan Claude, membaca blok SETUP, data pemakaian token |
| `skills.py` | Baca/tulis `setelan.yaml` per skill, mode jadwal (jam / interval lintas tengah malam), tambah/arsipkan skill, konfirmasi REAL |
| `settings.py` | `config.yaml` → `Config`, `.env`, daftar lengkap tool TradingView dan bawaan, penulis YAML yang menjaga komentar |
| `telegram.py` | Pengirim mode bot (Bot API) dan mode akun (Telethon), percobaan ulang |
| `journal.py` | Jurnal: `data\jurnal.jsonl` (asli) → `jurnal\Jurnal CLI.xlsx` (openpyxl dimuat saat perlu), ringkasan, rumus R |
| `heartbeat.py` | Pesan rutin dan detak "terakhir aktif" |
| `actions.py` | Aksi bersama menu dan Dashboard (token, channel, chat pribadi, TradingView, nyala/stop generator) |
| `menu.py` | Menu teks MENU.bat |
| `dashboard.py` | Dashboard Tkinter (tujuh tab, satu jendela, generator latar ikut mati saat ditutup) |

**Aturan yang dipegang kode:**
- Blok `SETUP` diambil apa adanya dari jawaban Claude. Program tidak memeriksa isi setup; validasi dikerjakan bot eksekutor.
- `ui_evaluate` selalu ditolak. Tool di luar daftar izin tidak dimuat ke Claude.
- Rahasia hanya di `.env` dan disamarkan di log dan pesan.
- Menu dan Dashboard memakai fungsi yang sama (`actions.py`, `skills.py`, dll). Jangan menulis ulang logika di salah satunya.
- Jurnal tidak boleh menggagalkan analisa; error jurnal hanya jadi peringatan di log.
- Kunci "satu analisa sekaligus" = port lokal 47657 (generator jalan terus: 47656). Dashboard dan menu mengecek status dengan memegang port itu sepersekian detik, jadi siapa pun yang mau mengambil kunci memakai `wait_for_lock` (menunggu sebentar), bukan `local_lock` langsung. Analisa sekarang yang tetap tidak dapat kunci keluar dengan kode 3 (`EXIT_SKIPPED`), dan file pesannya tidak dihapus.
- Penjadwal (`pending_minutes`) memproses setiap menit sejak menit terakhir. Yang lewat ≤ 3 menit dikejar, yang lebih lama hanya dicatat di log (tidak susulan), dan jam mundur tidak mengulang jadwal.

**Tes:** dari folder `sistem`, jalankan `python -m pytest -q` (tanpa Telegram, Claude, atau TradingView sungguhan), atau pilih **MENU → Utilitas → Tes logika program**.

## Jangan dibagikan

Repo GitHub sudah tidak menyertakan data pribadi, skill, dan setelan pribadi (lihat `.gitignore`). Kalau folder ini **disalin langsung** untuk orang lain, **buang dulu**: `.env`, `sistem\data\`, `sistem\logs\`, `jurnal\`, `sistem\config.yaml`, `sistem\mcp-tradingview.json`, dan skill pribadimu di `skill\`. Isinya token, sesi Telegram, hasil analisa, jurnal, ID channel/chat, dan path PC-mu. Langkah lengkapnya ada di [panduan/INSTALL.md](panduan/INSTALL.md).

## Lisensi

MIT. Lihat file [LICENSE](LICENSE).
