# Panduan membuat skill untuk CLI Signal Generator

> **Untuk AI yang diminta membuat skill:** baca file ini utuh dulu, lalu baca `CONTOH_SKILL.md` di folder ini sebagai contoh bentuk, dan `..\FORMAT_SETUP.md` untuk format blok SETUP. Setelah itu tanyakan ke user skill seperti apa yang dia mau. Jangan mulai menulis skill sebelum paham bagian 3, 4, dan 10 (tool). Kalau skill butuh tool yang belum diizinkan, sebutkan ke user tool mana yang harus dibuka.
>
> Folder ini **bukan skill** (tidak ada file `SKILL.md`), jadi tidak muncul di menu. `CONTOH_SKILL.md` hanya contoh bentuk; untuk menguji alur tanpa skill, pakai **Nyalakan → Analisa sekarang → Mode uji fungsi (TES)**.

## 1. Apa itu skill di sini

Generator adalah program yang, pada waktu tertentu atau saat dipicu dari menu, menjalankan **Claude Code CLI** (`claude -p`) untuk menganalisa chart TradingView, lalu meneruskan **blok `SETUP`** dari jawabannya ke channel Telegram yang dibaca bot eksekutor (Automanage). Bot itulah yang memeriksa dan mengeksekusi order.

**Skill = instruksi analisa** yang dipakai Claude saat dipanggil. Satu skill = satu folder di `skill\`, dengan file utama **`SKILL.md`**.

Skill di sini **bukan** skill Claude Code biasa (yang dipanggil `/nama` di chat). Bedanya besar, lihat bagian 3.

## 2. Cara program memakai skill

Saat analisa, program menyusun satu perintah untuk Claude dari potongan berikut, berurutan:

1. `sistem\prompt\mode_otomatis.md`: aturan tetap mode otomatis (tidak ada manusia, cek TradingView dulu, cara menutup jawaban).
2. `skill\FORMAT_SETUP.md`: format blok SETUP. **Selalu ikut**, skill tidak perlu menyalinnya.
3. Isi **`SKILL.md`** dari skill yang aktif.
4. "Aturan dari program": nilai `Kode:` dan `Sumber:` yang wajib dipakai di blok SETUP.
5. Waktu sekarang (WIB).

Hanya `SKILL.md` yang diselipkan. File lain di folder skill (misal `SOP.md`, catatan, data) **tidak** ikut otomatis. Membacanya butuh tool `Read`, yang **ditutup** secara bawaan (bagian 10), dan menambah token. Utamakan semua yang penting ada di `SKILL.md`.

Jawaban Claude disimpan utuh di `sistem\data\hasil\` untuk ditinjau user. Program lalu:
- ada blok `SETUP` → tiap blok dikirim apa adanya sebagai satu pesan ke channel kotak masuk
- ada blok `SETUP RENDAH` → **tidak** ke kotak masuk; tiap blok dikirim sebagai pesan sendiri ke chat pribadi user, dan pesan status mengingatkan ada setup yang menunggu keputusan user
- tidak ada blok, ada baris `NO SETUP` / `NO ENTRY` → dilaporkan "tidak ada setup"
- ada baris `GAGAL TRADINGVIEW` atau tidak dikenali → dilaporkan gagal

Program **tidak memeriksa isi** setup. Kesalahan format atau angka akan ditolak bot eksekutor.

## 3. Kondisi saat Claude menjalankan skill (wajib dipahami)

| | |
|---|---|
| **Tidak ada manusia** | Claude tidak bisa bertanya atau menunggu jawaban. Semua keputusan harus bisa diambil dari isi skill. Kalau ragu → `NO SETUP` / `NO ENTRY`, bukan bertanya. |
| **Mulai bersih** | Tiap panggilan tanpa ingatan panggilan sebelumnya. Claude tidak tahu setup yang dikirim sesi lalu. |
| **Tidak tahu akun** | Tidak ada akses posisi, order, saldo, atau hasil trade. Aturan yang butuh info itu (misal "setelah kena SL...") tidak bisa dijalankan. |
| **TradingView** | Sudah dibuka program di **TradingView Desktop** (CDP port 9222). Chart yang dipakai = chart yang terbuka di aplikasi. Indikator yang dibaca skill harus **sudah terpasang dan terlihat** di chart. |
| **Batas waktu** | Diatur per skill di `setelan.yaml` (`batas_waktu_menit`, lewat menu Skill & analisa → Daftar skill). Kalau kosong, ikut bawaan global `claude.batas_waktu_menit` (10 menit). Lewat itu proses dihentikan. |
| **Model** | Diatur per skill di `setelan.yaml` (`model` dan `effort`, lewat menu yang sama). Kalau kosong, ikut bawaan global `claude.model` (`sonnet`) dan `claude.effort`. |
| **Bahasa jam** | Semua waktu WIB (UTC+7). Waktu sekarang diberikan program. |

**Tool:** Claude hanya diberi tool yang ada di **daftar izin** (15 tool secara bawaan). Daftar lengkap, tool yang ditutup, dan cara membukanya ada di **bagian 10**. Skill yang menyuruh memakai tool yang ditutup akan gagal di langkah itu.

**Yang jangan disuruh skill:**
- bertanya ke user, minta teks ditempel (misal teks VERA), minta izin
- mengirim ke Telegram atau channel apa pun (yang mengirim adalah program)
- menggambar di chart, membuat alert, mengubah indikator, Pine, layout
- membuka/menutup TradingView
- memberi saran lot atau risiko (diatur SOP bot)

**Yang wajib disuruh skill:**
- **atur simbol dan timeframe di awal analisa** (misal `chart_set_symbol OANDA:XAUUSD`). Chart bisa masih di simbol lain sisa analisa sebelumnya. Tidak perlu mengembalikan chart di akhir: user tidak memakai chart saat generator menganalisa (keputusan user, 13 Nov).

## 4. Kontrak jawaban (wajib)

Jawaban Claude **harus diakhiri** dengan salah satu:

1. **Satu blok `SETUP` per setup final**, format di `..\FORMAT_SETUP.md`. Baris `Kode:` dan `Sumber:` persis dari program.
2. Satu baris **`NO SETUP`** (tidak ada zona valid) atau **`NO ENTRY`** (zona ada, tapi harga di tempat jelek), diikuti alasan singkat.
3. Satu baris **`GAGAL TRADINGVIEW`** + alasan, kalau chart tidak bisa dibaca.

Di atasnya tulis analisanya. **Seluruh jawaban (analisa + blok SETUP) dikirim ke chat pribadi user** (setelan `telegram.kirim_analisa_ke_pm`) dan disimpan di `data\hasil`. Jadi tulis analisa yang enak dibaca di Telegram: teks biasa, tanpa tabel Markdown lebar (tabel tidak tampil rapi di Telegram), poin-poin pendek per bagian. Panjang analisa ditentukan skill; pesan panjang dipecah otomatis.

Kata `SETUP` sendirian di satu baris **selalu dianggap awal blok**. Jangan menulis baris yang isinya cuma `SETUP` di bagian analisa.

## 5. Mengubah skill chat lama jadi skill generator

Skill yang dibuat untuk ngobrol (misal `XAU-intraday` di repo MCP TradingView) perlu diubah. Daftar periksa:

- [ ] Hapus semua langkah yang meminta input user (teks VERA, screenshot, konfirmasi).
- [ ] Hapus bagian kirim Telegram, `kirim_setup.py`, dan tawaran menggambar.
- [ ] Ganti "tanya user" dengan aturan keputusan yang jelas, atau `NO SETUP`.
- [ ] Hapus aturan yang butuh info akun/posisi/hasil trade (atau tulis jelas bahwa tidak berlaku).
- [ ] Sumber fundamental: kalau dulu dari teks tempelan, ganti dengan web search dan tulis "jangan mengarang jadwal; kalau tidak bisa dipastikan, sebut tidak terkonfirmasi".
- [ ] Jam sesi: tulis dalam WIB dan sebut pergantian jam musim (UK/US) kalau berpengaruh.
- [ ] Tentukan jelas: setup dengan conviction rendah (misal < 50%) **dikirim jadi blok `SETUP`**, **jadi blok `SETUP RENDAH`** (diputuskan user lewat chat pribadi), atau **cukup disebut di analisa**. Tanpa manusia, ini harus diputuskan di skill.
- [ ] Tabel Markdown lebar diganti poin-poin (jawaban dibaca di Telegram). Isinya tetap lengkap.
- [ ] Hapus aturan format SETUP yang sudah ada di `FORMAT_SETUP.md`; sisakan yang khusus skill (misal `Ke:`, cara isi `Berlaku`, jumlah TP).
- [ ] Hapus perintah "kembalikan chart ke posisi awal"; ganti dengan perintah mengatur simbol/timeframe di awal analisa.
- [ ] Tentukan setup mana yang jadi `SETUP RENDAH` (diputuskan user, tidak ke bot), misal conviction < 50%.

## 6. Kedalaman analisa dulu, hemat token belakangan

**Prinsip: kualitas analisa tidak boleh dikorbankan demi hemat token.** Skill generator harus bisa menganalisa sedalam skill yang dipakai saat ngobrol dengan Claude Code. Yang dihemat hanya pemborosan yang tidak menambah apa-apa ke analisa.

**Boleh dihemat (tidak mengurangi kualitas):**
- Selalu pakai `study_filter` di `data_get_pine_*` (tanpa filter, data indikator lain ikut terbawa dan tidak dipakai).
- Jangan panggil `data_get_study_values` kalau chart punya indikator berinput besar (keluarannya bisa puluhan ribu karakter sampah input).
- Jangan menarik data yang sama dua kali.
- Tidak usah basa-basi pembuka/penutup di jawaban.

**Jangan dibatasi kalau skill membutuhkannya:**
- Bar mentah (`data_get_ohlcv` tanpa `summary`) untuk menentukan swing high/low presisi, zona, dan SL.
- Jumlah timeframe yang dibaca, cek DXY/yield/konfluensi lain.
- Web search untuk news, kalender, dan konteks fundamental.
- `capture_screenshot` kalau skill memang membaca chart secara visual.
- Panjang analisa.

**Faktor terbesar kedalaman justru setelan, bukan skill:**
- **Model & effort skill** (`model`, `effort` di `setelan.yaml` skill itu): kalau saat ngobrol kamu memakai model yang lebih kuat (misal Opus), samakan di sini untuk hasil yang setara (lebih banyak memakai kuota).
- **Batas waktu skill** (`batas_waktu_menit` di `setelan.yaml`): analisa dalam butuh waktu. Kalau proses sering dihentikan karena batas waktu, naikkan.
- Keduanya diatur lewat **MENU → Skill & analisa → Daftar skill → pilih skill**. Kalau kosong, ikut bawaan global di `config.yaml` (`claude.model`, `claude.effort`, `claude.batas_waktu_menit`).

## 7. Struktur folder dan aturan nama

```
skill\
  FORMAT_SETUP.md      ← format blok SETUP, ikut ke semua skill
  panduan-skill\       ← bukan skill (tidak ada SKILL.md), tidak muncul di menu
    PANDUAN.md         ← file ini
    CONTOH_SKILL.md    ← contoh bentuk skill
  nama-skill-baru\
    SKILL.md           ← wajib
    setelan.yaml       ← dibuat otomatis bila belum ada
    (file pendukung)   ← opsional
```

- Nama folder = nama skill. Huruf kecil, angka, dan tanda `-` saja, tanpa spasi. Contoh: `xau-intraday`, `xau-scalp`.
- Folder yang diawali `_` (misal `_draft-xau`) **tidak muncul** di menu pilih. Pakai untuk skill yang belum siap.
- Nama file wajib **`SKILL.md`**. Jangan pakai `CLAUDE.md`: nama itu punya arti khusus di Claude Code.

## 8. Langkah membuat skill baru

1. Pilih **Skill & analisa → + Tambah skill**, lalu masukkan nama seperti `xau-intraday`. Menu membuat kerangka dari bagian 9. Kamu juga bisa membuat folder dan `SKILL.md` sendiri.
2. Lengkapi isi `SKILL.md`. Menu otomatis membuat `setelan.yaml` bila belum ada. Skill baru mulai dalam keadaan MATI.
3. Cek tool yang dipakai skill sudah ada di daftar izin (bagian 10). Kalau belum, buka lewat **Skill & analisa → Tool yang diizinkan**.
4. Di **Skill & analisa → Daftar skill**, pilih skill lalu atur jadwal (jam tertentu, atau tiap sekian jam di antara jam mulai dan sampai), hari aktif, model/effort, kode SETUP, serta batas waktu. Model, effort, dan batas waktu yang bernilai null mengikuti bawaan global.
5. Uji lewat **Nyalakan → Analisa sekarang** dan baca jawaban di **Utilitas → Riwayat analisa** atau di jurnal Excel.
6. Untuk jadwal otomatis, balik saklarnya lewat **Skill & analisa → Pilih skill aktif**. Beberapa skill bisa NYALA sekaligus. Jadwal yang bertepatan antre.

**Uji aman:** kode bawaan setiap skill adalah `TES`. Dengan kode TES belum terdaftar, Automanage menolak setup tersebut. Kode lain perlu konfirmasi `REAL` karena setup bisa jadi order beneran di Automanage.

## 9. Kerangka `SKILL.md`

```markdown
---
name: nama-skill
description: Satu kalimat: instrumen, gaya (intraday/scalp/swing), kapan dipakai.
---

# Judul skill

## Tujuan
Apa yang dicari skill ini, dalam 1–3 kalimat.

## Instrumen dan data
Simbol TradingView (misal OANDA:XAUUSD), timeframe yang dibaca, indikator yang harus ada di chart.
Tool yang dipakai (harus ada di daftar izin, lihat bagian 10 PANDUAN).

## Langkah analisa
Urutan tool dan apa yang dibaca di tiap langkah. Sebut study_filter, jumlah bar, timeframe.

## Aturan keras
Larangan dan syarat mutlak (misal: tidak entry di tengah range, SL di luar swing + buffer).

## Kapan NO SETUP / NO ENTRY
Kondisi yang membuat skill tidak mengeluarkan setup.

## Conviction
Cara menilai dan apa yang dilakukan kalau rendah (dikirim jadi blok atau tidak).

## Isi blok SETUP
Yang khusus skill ini: Pair, Ke (mt5/bybit), jumlah TP dan cara menentukannya, cara isi Berlaku, kapan pakai Entry2 / Order / Batal sebelum entry, isi Catatan.

## Jawaban
Bentuk analisa di atas blok: bagian apa saja yang ditulis dan urutannya. Ingat jawaban ini dibaca user di Telegram (hindari tabel lebar).
```

## 10. Tool: yang diizinkan, yang ditutup, cara membuka

Saat analisa, Claude **hanya diberi tool di daftar izin**. Daftar tool ikut terbaca di setiap panggilan, jadi tiap tool yang dibuka menambah token di setiap bolak-balik (diukur 14 Nov: semua tool ~36,9 rb token per panggilan, daftar izin ~8,3 rb). Daftar izin juga membuat Claude yang jalan tanpa diawasi tidak bisa menjalankan perintah atau menulis file.

**Cara membuka atau menutup tool:** MENU → **Skill & analisa → Tool yang diizinkan**. Bisa juga langsung di `sistem\config.yaml` (`claude.tools_bawaan` dan `claude.tools_tradingview`). Daftar ini berlaku untuk **semua skill**. Buka seperlunya saja.

### Diizinkan (bawaan)

Tool bawaan Claude Code:
- `WebSearch`: cari di internet (news, kalender ekonomi).
- `WebFetch`: buka satu halaman web.

Tool TradingView (nama lengkapnya diawali `mcp__tradingview__`):
- `tv_health_check`: cek chart tersambung. Wajib di awal (sudah diperintahkan program).
- `chart_get_state`: simbol, timeframe, dan daftar indikator di chart.
- `chart_set_symbol`: ganti simbol (misal `OANDA:XAUUSD`, `TVC:DXY`, `TVC:US10Y`).
- `chart_set_timeframe`: ganti timeframe.
- `data_get_ohlcv`: data candle (`summary: true` untuk ringkasan, tanpa itu untuk bar mentah).
- `quote_get`: harga sekarang.
- `data_get_pine_boxes`: zona dari indikator, misal FVG/OB dari ICT (pakai `study_filter`).
- `data_get_pine_labels`: label dari indikator, misal OB+/OB−/BOS dari ICT.
- `data_get_pine_lines`: garis level dari indikator.
- `data_get_pine_tables`: tabel indikator, misal UvH (`study_filter: "Usaha"`).
- `tab_list`, `tab_switch`: pilih tab chart kalau TradingView punya beberapa tab.
- `capture_screenshot`: gambar chart. **Mahal token**, pakai hanya kalau skill memang membaca chart secara visual.

### Ditutup tapi mungkin berguna (buka kalau skill butuh)

| Tool | Gunanya | Catatan |
|---|---|---|
| `Read` (bawaan) | Membaca file pendukung skill (misal `SOP.md`) | Lebih hemat kalau isinya dimasukkan ke `SKILL.md`. |
| `batch_run` | Satu aksi di banyak simbol/timeframe sekaligus | Hasilnya bisa besar. |
| `symbol_search`, `symbol_info` | Mencari kode simbol, info kontrak | Berguna untuk skill pair baru. |
| `chart_get_visible_range`, `chart_set_visible_range`, `chart_scroll_to_date` | Mengatur rentang tanggal yang terlihat | Untuk skill yang melihat periode lampau. |
| `chart_set_type` | Ganti jenis chart (candle, Heikin Ashi, dll) | |
| `data_get_indicator` | Info satu indikator | |
| `depth_get` | Order book / market depth | Tidak tersedia untuk semua simbol. |
| `data_get_study_values` | Nilai terakhir **semua** indikator sekaligus (RSI, EMA, dll) | **Jangan dibuka** selama ICT terpasang: keluarannya ±59 rb karakter karena ikut menyalin input ICT. Nilai MA cukup dibaca dari garis ICT. |

### Ditutup dan tidak boleh disuruh skill

- **Menggambar di chart:** `draw_shape`, `draw_list`, `draw_clear`, `draw_remove_one`, `draw_get_properties`.
- **Pine Script:** `pine_new`, `pine_open`, `pine_save`, `pine_set_source`, `pine_get_source`, `pine_compile`, `pine_smart_compile`, `pine_check`, `pine_analyze`, `pine_get_errors`, `pine_get_console`, `pine_list_scripts`.
- **Mengubah indikator:** `indicator_add`, `indicator_search`, `indicator_set_inputs`, `indicator_toggle_visibility`, `chart_manage_indicator`. Indikator dipasang user sendiri; Claude cukup membacanya.
- **Alert, replay, watchlist:** `alert_*`, `replay_*`, `watchlist_*`.
- **Klik/ketik di layar:** `ui_click`, `ui_mouse_click`, `ui_type_text`, `ui_keyboard`, `ui_hover`, `ui_scroll`, `ui_find_element`, `ui_open_panel`, `ui_fullscreen`.
- **Layout, panel, tab baru:** `layout_*`, `pane_*`, `tab_new`, `tab_close`.
- **Strategi/backtest:** `data_get_strategy_results`, `data_get_trades`, `data_get_equity`.
- **Lain-lain:** `tv_discover`, `tv_ui_state`.
- **Selalu ditolak** (juga kalau diizinkan): `ui_evaluate` (terkunci), `tv_update`, `tv_launch` (TradingView dibuka program lewat skrip peluncur).
- **Tool bawaan Claude Code lainnya:** `Bash`, `PowerShell` (menjalankan perintah), `Write`, `Edit`, `NotebookEdit` (menulis file; jurnal sudah ditulis program), `Glob`, `Grep`, `Task` dan sub-agent, jadwal (`Cron*`, `ScheduleWakeup`), dan tool coding lain.

Kalau update TradingView MCP menambah tool baru, tool itu **masih terbuka** sampai daftar lengkap di program (`sistem\generator\settings.py`, `TV_TOOLS`) diperbarui.
