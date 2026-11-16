---
name: contoh
description: Skill contoh. Membaca harga dan ringkasan H1 XAUUSD lalu selalu menutup dengan NO SETUP. Dipakai untuk menguji alur skill, bukan untuk trading.
---

# Skill contoh

> Contoh bentuk skill untuk generator. Cara membuat skill baru ada di `PANDUAN.md` di folder yang sama. Untuk dipakai, salin ke folder skill baru dan beri nama `SKILL.md`. Saat analisa, yang dikirim ke Claude hanya `SKILL.md` skill itu.

## Tujuan

Menguji bahwa skill terbaca dan alurnya jalan. Skill ini **tidak pernah membuat setup**.

## Instrumen dan data

- `OANDA:XAUUSD`, semua jam WIB.

## Langkah

1. `mcp__tradingview__chart_set_symbol` ke `OANDA:XAUUSD` (chart bisa masih di simbol lain).
2. `mcp__tradingview__quote_get` untuk harga terakhir.
3. `mcp__tradingview__chart_set_timeframe` ke `60`, lalu `mcp__tradingview__data_get_ohlcv` dengan `summary: true`.

## Jawaban

Tulis 2–3 baris saja: harga terakhir, high/low H1 dari ringkasan, dan sesi yang sedang berjalan.

Lalu tutup dengan persis satu baris:

```
NO SETUP - skill contoh tidak membuat setup
```
