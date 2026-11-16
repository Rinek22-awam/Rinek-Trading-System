# Format Setup untuk Bot

Untuk setup manual, kirim format ini dari akunmu ke **channel kotak masuk** yang disetel di `TG_INBOX_CHANNEL_ID`. Isi `Kode:` dengan kode yang terdaftar di **Sumber sinyal** pada Dashboard atau menu terminal. Cocokkan `Ke:` dengan platform tujuan. Eksekutor memakai SOP dan akun tujuan dari sumber tersebut. Chat Pengawas dipakai untuk status, alert, dan kontrol proses. [Panduan dashboard dan Pengawas](DESKTOP.md)

## Format kosong

```
SETUP
Kode:
Pair:
Arah:
Order:
Entry:
Entry2:
SL:
TP1:
TP2:
TP3:
Risk:
Highrisk:
Ragu:
Catatan:
Berlaku:
Batal sebelum entry:
Ke:
Sumber:
```

| Baris | Wajib? | Isi |
|---|---|---|
| `SETUP` | **wajib, baris pertama** | Tanpa ini postingan diabaikan |
| `Kode` | **wajib** | Kode sumber, misal `S01`. Reader mengisinya otomatis. Untuk setup manual, salin kode sumber yang sesuai dari menu |
| `Pair` | **wajib** | MT5: `XAUUSD` / `GOLD` / `BTCUSD` (cek pemetaan di Setelan universal → MT5 → Akun MT5 → pilih akun → Nama simbol). Bybit: koin apa pun yang ada di USDT Perpetual (`STXUSDT`, `STX`, `$STX`), dicari sendiri oleh bot |
| `Arah` | **wajib** | `BUY` / `SELL` (boleh `LONG`/`SHORT`/`BELI`/`JUAL`) |
| `Order` | boleh dihapus | `LIMIT` (bawaan), `STOP`, `MARKET` |
| `Entry` | **wajib**, kecuali MARKET | Harga entry |
| `Entry2` | boleh dihapus | Batas entry kedua jika setup memakai rentang harga |
| `SL` | **wajib** | Stop loss |
| `TP1`, `TP2`, ... | **minimal 1** | Boleh sampai TP5 |
| `Risk` | boleh dihapus | Untuk setup ini saja: `0.5%` atau `$20` |
| `Highrisk` | boleh dihapus | `ya` = diberi tanda ⚠️; ditanya kecuali SOP sumber mematikan konfirmasi peringatan |
| `Ragu` | boleh dihapus | Peringatan kalau Claude kurang yakin atau kalau SL terlihat typo lalu ditebak. Konfirmasi mengikuti SOP sumber. Di mode otomatis, **Izinkan tebakan Claude tanpa konfirmasi** membolehkan setup lanjut dengan tanda ⚠️ |
| `Berlaku` | boleh dihapus | Batas waktu limit dari sinyal, misal `2026-11-11 01:08 WIB`. Dipakai di kedua mode **Ikut sinyal**. Kalau sudah lewat, setup ditolak |
| `Batal sebelum entry` | boleh dihapus | Contoh `< 0.71` atau `> 0.90`. Dipakai di mode **Ikut sinyal (waktu dan batas harga)**. Waktu atau harga yang tercapai dulu membatalkan pending order |
| `Ke` | **wajib** | `mt5` atau `bybit`: bot mana yang mengerjakan. **Tanpa baris ini postingan diabaikan semua bot** (diam). Pembaca menulisnya otomatis menurut sumbernya |
| `Catatan`, `Sumber` | boleh dihapus | Untuk jurnal |

Baris yang tidak dipakai **dihapus**, jangan dibiarkan kosong.

## Contoh

Ganti kode contoh dengan kode sumber yang terdaftar untuk platform tujuan. Untuk `Berlaku`, pakai batas waktu sinyal yang masih berlaku.

**1. BUY limit gold**
```
SETUP
Kode: S01
Pair: XAUUSD
Arah: BUY
Entry: 4005.50
SL: 3995.50
TP1: 4015.50
TP2: 4025.50
TP3: 4035.50
Ke: mt5
Sumber: trabar
```

**2. SELL limit BTC**
```
SETUP
Kode: S01
Pair: BTCUSD
Arah: SELL
Entry: 112500
SL: 113500
TP1: 111500
TP2: 110500
Ke: mt5
Sumber: trabar
```

**3. Langsung masuk market (tanpa Entry)**
```
SETUP
Kode: S01
Pair: XAUUSD
Arah: SELL
Order: MARKET
SL: 4015
TP1: 3995
TP2: 3985
Ke: mt5
Sumber: manual
```

**4. Crypto ke Bybit (limit long, berlaku sampai tanggal tertentu)**
```
SETUP
Kode: S02
Pair: STXUSDT
Arah: BUY
Entry: 0.3314
SL: 0.311
TP1: 0.3518
TP2: 0.3722
TP3: 0.3875
TP4: 0.4079
Berlaku: 2026-11-11 01:08 WIB
Ke: bybit
Sumber: manual
```
Nama koin boleh `STX`, `$STX`, `STX/USDT`, atau `STXUSDT.P`: bot Bybit mencarinya sendiri. Ukuran dalam koin dan leverage dihitung bot (leverage maks koin itu, risiko dari jarak SL).

**5. Buy stop, risiko khusus, highrisk**
```
SETUP
Kode: S01
Pair: XAUUSD
Arah: BUY
Order: STOP
Entry: 4012
SL: 4004
TP1: 4020
TP2: 4028
Risk: $20
Highrisk: ya
Catatan: breakout
Ke: mt5
Sumber: manual
```

## Batalkan atau tutup

`CLOSE` selalu jadi tombol (posisi tidak pernah ditutup sendiri). `CANCEL`: bot MT5 memunculkan tombol, bot **Bybit langsung membatalkan** limit yang belum terisi (dan setup yang masih menunggu konfirmasi).

```
UPDATE
Kode: S01
Ke: mt5
Sumber: trabar
Aksi: CANCEL
Ref: 1234
Teks: cancel setup gold
```

`Aksi`: `INFO` (diteruskan saja), `CANCEL` (MT5: tombol batalkan limit; Bybit: langsung dibatalkan), `CLOSE` (tombol tutup posisi). Yang kena: setup yang postingan SETUP-nya bernomor `Ref` di kotak masuk (pembaca menulisnya otomatis kalau trader me-reply sinyalnya). Tanpa `Ref`, bot mencari setup yang masih bisa ditindak dari kode sumber dan pair yang cocok. Kalau setup ber-`Ref` sudah selesai, bot hanya memberi tahu. Tambahkan baris `Pair: STXUSDT` supaya tombolnya untuk setup koin itu (pembaca menulisnya otomatis untuk notifikasi TraderAwam). Untuk bot Bybit pakai `Ke: bybit`.

## Aturan penting

1. **Jangan pakai titik ribuan.** `112.500` dibaca **112,5** dan `4.005` dibaca **4,005**. Tulis `112500` dan `4005.50`. Format Indonesia lengkap (`4.005,50`) masih terbaca benar, tapi paling aman tanpa pemisah ribuan. Kalau salah tulis, setup ditolak "Entry terlalu jauh dari harga", tidak sampai jadi order.
2. **Kirim saat harga masih relevan.** Batas bawaan usia setup adalah 30 menit. Batas jarak entry bawaan adalah 1% untuk MT5 dan 10% untuk Bybit. Atur keduanya di **Sumber sinyal → pilih sumber → SOP · Gerbang**.
3. **Sisi harus benar.** BUY: SL di bawah entry, TP di atas entry. SELL: kebalikannya.
4. **Huruf besar/kecil bebas**, pemisah boleh `:` atau `=`. Urutan TP bebas, bot mengurutkan dari yang terdekat.
5. Setup yang **salah format** dilaporkan ke chat pribadimu beserta alasannya, tidak dieksekusi.
