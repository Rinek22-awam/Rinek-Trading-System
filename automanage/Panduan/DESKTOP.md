# Dashboard Bot untuk Windows

Buka **Dashboard Bot.exe** di folder proyek atau pilih **MENU.bat → Buka Dashboard**. Dashboard adalah tab awal. Kalau jendelanya sudah terbuka, pembukaan berikutnya membawa jendela itu ke depan. Untuk login Telegram Pembaca pertama kali, gunakan **MENU.bat → Sumber sinyal → Tambah sumber**. [Cara membuat EXE](BUAT_EXE.md)

Pembaca, eksekutor MT5, eksekutor Bybit, dan Bot Pengawas punya status, tombol Nyalakan/Stop/Restart, dan panel log masing-masing. Saat menyalakan bot, Dashboard memeriksa kebutuhan bot yang dipilih. Tiap log bisa digulir sendiri dan mengikuti baris terbaru saat posisi scroll ada di bawah.

Panel log mulai kosong setiap kali dashboard dibuka. **Bersihkan log** mengosongkan panel yang dipilih, lalu baris baru tampil seperti biasa. Riwayat lengkap tetap tersimpan di folder `sistem/logs`.

Proses dari menu juga muncul di dashboard. Restart menunggu proses lama selesai sebelum menyalakan proses baru. Saat menutup dashboard dengan proses yang masih berjalan, pilih **Tutup dashboard saja**, **Matikan semua & tutup**, atau **Batal**.

## Tab lainnya

- **Sumber sinyal** untuk tambah, edit, hapus, dan atur SOP. Hentikan Pembaca sebelum mendeteksi channel atau topik, lalu pilih akun tujuan melalui checklist.
- **Setelan universal** untuk direktori terminal dan simbol per akun MT5, pengaturan akun dan API Bybit, serta pengaturan broker.
- **Utilitas** untuk diagnostik, tes logika, dan pengelolaan jurnal. Hasil diagnostik dan tes ditampilkan di dalam tab. Menu **Reset bot** tersedia di terminal. [Peta MENU.bat](PETA_MENU.md)

Jika Dashboard menampilkan **Dipakai (lihat menu)** untuk proses dari versi lama, hentikan dari jendela asal lalu nyalakan kembali.

## Siapkan Bot Pengawas

1. Buat bot khusus di BotFather. Simpan tokennya lewat **MENU.bat → Utilitas → Isi/ganti token bot Telegram → Pengawas**.
2. Pastikan `.env` berisi `TG_ADMIN_USER_ID`, `TG_API_ID`, dan `TG_API_HASH` yang dipakai Reader. Pengawas masuk sebagai bot dengan sesi lokalnya sendiri.
3. Buka chat pribadi bot Pengawas di Telegram dan tekan **Start** dari akun admin. Nyalakan Pengawas dari menu atau dashboard. Pesan **🤖 Bot Pengawas nyala** berisi status Reader, MT5, dan Bybit.

## Perintah Telegram

| Perintah | Fungsi |
|---|---|
| `/status` | Menampilkan kondisi Reader, MT5, dan Bybit |
| `/start` | Menawarkan tombol untuk menyalakan proses yang mati |
| `/stop` | Menawarkan tombol untuk menghentikan proses yang jalan |
| `/restart` | Menawarkan tombol untuk menghentikan lalu menyalakan kembali proses. Proses yang sudah mati langsung dinyalakan |

Pesan yang tidak dikenali memunculkan daftar perintah. Pengawas melaporkan hasil kontrol setelah proses berhenti atau proses baru siap. Jika proses macet dan tidak merespons permintaan stop, Pengawas melaporkan bahwa proses belum berhenti. Pengawas sendiri dikendalikan dari menu atau dashboard.

## Alert dan koneksi

- Pengawas melaporkan peringatan, error, dan proses yang terdeteksi berhenti mendadak selama Pengawas aktif. Kejadian berulang dirangkum. Restart dilakukan lewat pilihan admin.
- Pengawas menerima pesan dan klik tombol lewat sambungan Telethon yang menetap. Jika koneksi putus, Pengawas mencoba menyambung kembali. Kontrol jarak jauh tersedia selama PC, Pengawas, dan koneksi Telegram aktif.
- Saat API Bybit dijeda setelah HTTP 403, Pengawas menampilkan sisa waktu. Setelah jeda selesai, kirim perintah lagi bila perlu.
- Untuk tetap menerima alert setelah menutup dashboard, pilih **Tutup dashboard saja**. **Matikan semua & tutup** juga menghentikan Pengawas.

## Kode dashboard lama

Folder `sistem/desktop/` dan `sistem/desktop_entry.py` menyimpan kode Trading Manager PySide6 untuk referensi. EXE dan menu **Buka Dashboard** menjalankan `sistem/panel/dashboard.py`.
