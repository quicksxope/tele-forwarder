# Summary trade bot — 16–25 Sep 2026

Bot trader rugi karena sinyal DEX VIP membayar 1:1 sementara yang menang cuma 22%. Itu yang menghabiskan akun. Window 4 jam bukan penyebab utamanya.

Sumber: `data/okx_trades.db` di VM (76 trade live) dan income Binance futures. Semua order ke Binance.

## Hasil

69 trade sudah tutup: 15 menang, 54 kalah. Win rate 21.7%. Ekspektasi −0.56R per trade. Profit factor 0.36.

Kas di Binance, 16–25 Sep:

| Jenis | USDT |
|---|---|
| Realized PnL | −4597 |
| Komisi | −439 |
| Funding | −46 |
| Hasil trading | **−5082** |
| Transfer masuk | +1260 |
| Perubahan bersih akun | −3822 |

Equity snapshot: 5000 (16 Sep) → 4066 (19 Sep) → 2254 (23 Sep). Wallet sekitar 1046 pada 25 Sep.

Database menjumlahkan PnL trade −6297. Skor itu lebih buruk daripada kas karena harga exit sering dicatat setelah harga melewati SL, bukan di harga fill. 39 dari 54 loss tercatat lebih buruk dari −1R.

| Channel | Trade | Menang | Kalah | PnL di DB |
|---|---|---|---|---|
| DEX VIP | 69 | 14 | 51 | −5983 |
| Cryptocium | 7 | 1 | 3 | −314 |

- Rata-rata menang +232 USDT (median +1.0R). Rata-rata kalah −181 USDT (median −1.05R).
- Long 63 trade tertutup, −4736 di DB. Short 6 trade, 0 menang, −1561 di DB.
- Loss paling dalam: TST −513 (−2.05R), ONE −386, SPX −385.
- Profit paling besar: AR +698 (+4.20R), ONE +499, DRIFT +271.
- Hampir setiap hari realized-nya negatif. Satu-satunya hari positif: 19 Sep. 20 Sep sendiri −1266 realized.

## Statistik resmi DEX VIP vs bot

Angka resmi channel: 347 menang, 363 kalah, 0 batal, 710 trade, win rate 48.9%.

Itu skor sinyal: harga menyentuh TP atau SL, dengan asumsi entry keisi di harga yang tertulis. Cancel 0 berarti setiap sinyal dipaksa jadi menang atau kalah. Pada jarak TP = SL, 48.9% hampir impas sebelum komisi: 347 menang − 363 kalah = −16R atas 710 sinyal, sekitar −0.02R per sinyal.

Bot mengukur hal lain: order yang benar-benar keisi lalu ditutup di exchange. DEX yang tertutup di database: 14 menang dari 65 (22%). Selisih ini terlalu lebar untuk disebut minggu sial semata (ekspektasi kalau win rate benar 48.9% adalah sekitar 32 menang dari 65).

Penyebab selisihnya:

- Sinyal yang langsung lari ke TP sering tidak mengisi limit di entry. Channel menghitung itu sebagai menang. Bot tidak punya posisi, jadi tidak masuk win rate.
- Sinyal yang balik ke entry lalu jatuh ke SL mengisi limit. Itu yang tercatat sebagai kalah. Loser tertutup dengan median setengah jam.
- Sebagian chart-win tidak ikut diambil karena TP gagal terpasang (qty di atas batas Binance, atau cuma TP setengah). PARTI harga sudah lewat TP dan posisinya masih terbuka.

Jadi channel-nya dekat coin-flip. Eksekusi bot mengubah coin-flip itu menjadi win rate 22%.

## Kenapa hasil bot jelek

DEX VIP memasang TP dan SL dengan jarak yang sama. Impas butuh menang lebih dari separuh trade. Yang terjadi 15 dari 69. Risiko tiap 1R adalah 5% equity, jadi seminggu cukup untuk menjatuhkan saldo dari sekitar 5000 ke sekitar 1000.

Cryptocium targetnya 2R (impas di win rate sekitar 33%), tapi baru 4 trade tutup: 1 menang, 3 kalah. Sampel itu belum menunjukkan edge.

Loser tertutup dengan median setengah jam. Fill sering terjadi saat harga sedang jatuh ke arah SL.

## Masih terbuka (25 Sep)

| Pair | Channel | Catatan |
|---|---|---|
| PARTI long | Cryptocium | Harga sudah lewat TP. Sisa posisi tanpa TP |
| ZEN short | DEX VIP | Posisi hidup, SL masih terpasang |
| FARTCOIN | Cryptocium | Limit belum fill sejak 19 Sep. Tidak punya window, jadi tidak di-cancel |
| ARB | Cryptocium | Limit belum fill sejak 21 Sep. Sama, tanpa window |

FARTCOIN dan ARB mengunci sekitar 617 USDT margin. Unrealized PARTI dan ZEN sekitar +289 saat dicek, belum masuk realized.

## Solusi

Aturan ini sudah jalan di bot sejak 25 Sep 2026. DEX VIP tetap di-trade. Angka resmi 48.9% adalah skor chart pada payoff 1:1, hampir impas sebelum biaya (−16R dari 710 sinyal). Tugas bot adalah merealisasikan skor itu, bukan bertaruh 5% pada win rate 22%.

Buku yang sudah terbuka: limit FARTCOIN dan ARB dibatalkan (margin ~617 USDT kembali). PARTI ditutup market karena harga sudah lewat TP dan tidak ada order TP. ZEN dibiarkan ke SL-nya.

Risiko:

- 1R = 2% equity (rugi di SL).
- Maksimal 6 posisi sekaligus (termasuk limit yang belum keisi). Satu simbol satu posisi. Kalau keenamnya kena SL bersamaan, itu sekitar −12% akun.
- Stop harian **−5R**. Pada 1R = 2% equity, itu batas sekitar **−10% akun** per hari. Di sampel 16–23 Sep, −5R memotong tiga hari terburuk (16, 20, 22 Sep) dan mengubah total dari −38.6R menjadi −30.7R. Tidak ada stop mingguan. Hari berikutnya mulai dari nol.
- Full 1R atau skip. Aturan modal yang sekarang tetap.

Entry, supaya fill tidak cuma yang jatuh ke SL:

- Pasang limit begitu sinyal masuk (ini sudah terjadi, keterlambatan median di bawah 1 menit).
- Batalkan limit kalau harga sudah bergerak ~0.3R dari entry ke arah SL sebelum keisi.
- DEX yang tidak keisi tetap dibatalkan di akhir window 4 jam.

Exit, supaya menang di chart tidak jadi kalah di akun:

- SL dan TP dipasang di exchange untuk seluruh qty. Kalau qty melewati batas Binance, TP dipecah, bukan dibiarkan setengah.
- Kalau TP gagal terpasang, posisi tidak dibiarkan telanjang.

Cryptocium tetap book terpisah (target 2R). Limit tanpa window berlaku 1 hari. Kuotanya ikut batas posisi bersamaan dan stop harian.

Tiap minggu dibandingkan tiga angka: hasil gaya channel pada sinyal yang diambil (TP tercetak sebelum SL), hasil realisasi bot, dan selisihnya. Size tidak naik dari 2% sebelum realisasi setelah komisi mendekati skor channel itu selama beberapa puluh trade.
