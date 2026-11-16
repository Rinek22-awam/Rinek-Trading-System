# Peta MENU.bat

Peta ini mengikuti menu program saat ini. Tekan `0` atau Enter untuk kembali. Nomor sumber dan akun mengikuti urutan yang tampil di layar.

## Menu utama

```text
MENU.bat
├─ 1. Nyalakan
│  ├─ 1. Pembaca sinyal
│  ├─ 2. Bot MT5 (Exness)
│  ├─ 3. Bot Bybit
│  └─ 4. Bot Pengawas
├─ 2. Buka Dashboard
├─ 3. Sumber sinyal
│  ├─ Pilih sumber → channel, topik, tujuan, akun tujuan, dan SOP
│  ├─ + Tambah sumber
│  └─ Hapus sumber
├─ 4. Setelan universal (MT5 / Bybit)
│  ├─ 1. MT5
│  └─ 2. Bybit
└─ 5. Utilitas
```

## 3. Sumber sinyal → pilih sumber

| Bagian menu | Yang bisa diatur |
| --- | --- |
| Channel & topik | Channel Telegram dan topik yang dipantau |
| Diolah oleh | Pengolah sinyal |
| Tujuan | MT5 atau Bybit |
| Akun tujuan | Pilih akun MT5 atau Bybit dengan nomor, misalnya `1, 3` |
| Nama | Nama sumber |
| SOP · Risiko / Risiko dan leverage | Risiko persen atau nominal tetap, batas lot MT5, leverage Bybit |
| SOP · Pembagian lot per TP / Pembagian qty per TP | TP tunggal atau pembagian posisi ke beberapa TP |
| SOP · SOP posisi (BE dan trailing) | Kapan SL digeser ke BE, jarak aman, trailing, dan tindakan saat harga sudah melewati SL baru |
| SOP · Entry | Mode pembatalan, umur pending limit, dan tindakan saat harga lebih bagus dari entry |
| SOP · Gerbang (cek sebelum eksekusi) | **Wajib konfirmasi Ya/Tidak**, konfirmasi jika ada peringatan, izin tebakan otomatis, batas umur setup, jarak entry, dan deteksi dobel |
| SOP · Pengaman (untuk sumber ini saja) | Maksimal setup aktif dan batas loss harian |
| SOP · Model Claude (sumber diolah Claude) | Model untuk pesan bergambar dan pesan teks |

## 4. Setelan universal (MT5 / Bybit)

| Pilihan | MT5 | Bybit |
| --- | --- | --- |
| 1 | Akun → Hanya boleh akun demo | Akun → Hanya boleh demo/testnet |
| 2 | MT5 → Magic number dan toleransi slippage | Batas posisi → Nilai posisi maksimal (USDT) |
| 3 | Bot → Interval pengecekan posisi | Bot → Interval pengecekan posisi |
| 4 | Akun MT5 → Tambah akun, ganti direktori terminal, nama simbol, dan cocokkan otomatis dengan broker | Akun Bybit (API key per akun) → Tambah akun, pilih akun untuk ganti API key + secret atau mode, dan hapus akun |

Untuk mencocokkan pair, buka **Akun MT5 → pilih akun → Cocokkan otomatis dengan broker di MT5**.

Di Dashboard, panel **Sumber sinyal dan akun** menampilkan akun tujuan per sumber beserta status koneksinya. Akun yang masih menjaga setup lama ditandai di bawah sumber asal. Tab **Sumber sinyal** memakai checklist akun MT5 atau Bybit. [Panduan Dashboard](DESKTOP.md)

## 5. Utilitas

1. Coba baca gambar sinyal (tanpa Telegram)
2. Cek kesiapan (MT5, Bybit, Telegram, Claude)
3. Tes manual Bybit (demo saja)
4. Riwayat setup
5. Reset jurnal MT5
6. Reset jurnal Bybit
7. Reset bot (MT5 / Bybit / Reader)
8. Isi/ganti token bot Telegram (MT5 / Bybit / Pengawas)
9. Ganti channel kotak masuk sinyal
10. Tes logika program

**Reset bot** menghapus data lokal bot yang dipilih tanpa membuat cadangan. Pilihan MT5 dan Bybit menghapus log, jurnal, serta cadangan jurnal. Pilihan Reader menghapus log dan ingatan Claude CLI. Hentikan proses bot yang dipilih sebelum reset. Order dan posisi broker tetap berjalan, tetapi jurnal yang diperlukan untuk melacak setup lama dihapus.
