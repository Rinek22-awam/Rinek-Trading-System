# Format blok SETUP (generator)

> File ini **otomatis diselipkan program** ke setiap analisa, untuk skill apa pun. Skill tidak perlu menyalin isinya. Ubah file ini hanya kalau format bot eksekutor Automanage berubah.

Tutup jawaban dengan **satu blok `SETUP` per setup final**. Tidak ada setup = tanpa blok (tulis `NO SETUP` / `NO ENTRY`, lihat aturan program).

## Kerangka

```
SETUP
Kode: <diberikan program>
Pair: XAUUSD
Arah: BUY
Entry: 4005.5
SL: 3995.5
TP1: 4015.5
TP2: 4025.5
Berlaku: 2026-11-14 05:00 WIB
Ke: mt5
Sumber: <diberikan program>
```

## Baris wajib

- `SETUP` di baris **pertama** blok.
- `Kode:` dan `Sumber:` **persis** seperti yang diberikan program di bagian "Aturan dari program". Jangan diganti.
- `Pair`: nama polos tanpa prefix broker (`XAUUSD`, bukan `OANDA:XAUUSD`).
- `Arah`: `BUY` atau `SELL`.
- `Entry`: harga entry (kecuali `Order: MARKET`).
- `SL`: BUY di **bawah** entry, SELL di **atas** entry.
- `TP1` sampai maksimal `TP5`: BUY semua di **atas** entry, SELL semua di **bawah** entry, tidak ada yang sama.
- `Ke`: tujuan bot. `mt5` untuk forex/logam (Exness), `bybit` untuk crypto USDT Perpetual. Ditentukan skill.

## Baris opsional

- `Order: STOP` (breakout) atau `Order: MARKET` (masuk sekarang, baris `Entry` dihapus). Tanpa baris ini = limit.
- `Entry2`: ujung zona yang satunya. `Entry` = batas yang **paling dekat ke TP**. Kalau harga pasang di tengah zona, **jangan** tulis `Entry2` (bot menaruh limit di `Entry`).
- `Berlaku: YYYY-MM-DD HH:MM WIB`: batas waktu limit/stop. **Selalu tulis** untuk limit/stop.
- `Batal sebelum entry: > 4040` atau `< 3960`: harga invalidasi satu sentuhan sebelum entry terisi. Syarat close candle tidak bisa dibaca bot, jangan ditulis di sini.
- Tulis conviction, alasan, dan pengingat manual di analisa di atas blok. Jangan tulis baris `Catatan:` di dalam blok.
- **Jangan tulis `Risk`.** Risiko diatur SOP bot, bukan skill.

## Blok untuk diputuskan user: `SETUP RENDAH`

Kalau skill menyuruh sebuah setup **diputuskan user** (misal conviction di bawah 50%), tulis bloknya persis sama, hanya baris pertamanya **`SETUP RENDAH`**, bukan `SETUP`. Program **tidak** mengirim blok ini ke kotak masuk (bot tidak mengeksekusinya). Blok ini dikirim ke chat pribadi user sebagai pesan sendiri; user yang memutuskan memposting atau tidak.

- Isinya lengkap dan siap pakai seperti blok `SETUP` biasa (termasuk `Kode`, `Sumber`, `Berlaku`), supaya user cukup mengganti baris pertama.
- Kalau hasil analisa cuma berisi blok `SETUP RENDAH`, tidak perlu menulis `NO SETUP` / `NO ENTRY`.

## Aturan angka

- Titik untuk desimal, **tanpa pemisah ribuan**: `4005.5`, bukan `4,005.5` atau `4.005,5`.
- Satu harga per baris. Zona = `Entry` + `Entry2`, bukan `3998-4005`.
- Tidak ada teks lain di dalam blok selain baris-baris di atas.
- Blok boleh dibungkus pagar ```` ``` ````. Pisahkan antar blok dengan baris kosong.
