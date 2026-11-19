# Servis Sinyal AI — Penjaga Stop-Loss Agentik

Komponen **off-chain** dari entri Reactive Omni Buildathon (track *Reactive Meets AI*).
Servis ini adalah "otak" yang mengawasi harga dan memutuskan kapan posisi harus
diamankan. Keputusan itu ditulis ke kontrak `AISignalRegistry` di Base Sepolia —
dan dari sana kontrak penjaga (*StopLossGuardian*) di Lasna yang mengeksekusi
stop-loss-nya secara otomatis. Tidak ada manusia dan tidak ada keeper terpusat
di antaranya.

## Cara kerja (alur lengkap)

1. **Polling harga.** Setiap `poll_interval_seconds` (default 15 detik), servis
   membaca harga token demo dari `MockPriceFeed.priceOf(token)` di Base Sepolia
   lewat JSON-RPC (curl). Saat start, riwayat harga diisi dulu dari event
   `PriceUpdated` yang sudah ada di chain (best-effort).
2. **Model memutuskan.** Harga dimasukkan ke model sinyal (lihat di bawah).
   Hasilnya salah satu dari: `hold` (diam), `arm` (persenjatai stop-loss),
   `update` (geser trigger — trailing stop), atau `disarm` (matikan).
3. **Tanda tangan EIP-712.** Kalau keputusannya bukan `hold`, servis menyusun
   update sinyal dan **menandatanganinya secara off-chain** dengan kunci
   operator (persis format EIP-712 yang diminta kontrak).
4. **Simulasi dulu.** Update dikirim sebagai `eth_call` ke kontrak. Kalau
   kontrak akan menolaknya (tanda tangan/nonce/deadline salah), servis berhenti
   — tidak ada transaksi yang dikirim.
5. **Kirim (hanya mode `--live`).** Transaksi ditandatangani offline dengan
   `cast mktx`, lalu di-broadcast dengan `curl eth_sendRawTransaction`, dan
   receipt dipolling sampai `status = 0x1`. Jalur ini yang terbukti jalan dari
   VM ini (`cast send` tidak bisa dipakai — koneksi TLS-nya diputus).
6. **Efek on-chain.** Registry menerima update lewat `updateSignalSigned`
   (siapa pun boleh me-relay; yang mengesahkan adalah tanda tangan operator)
   dan memancarkan event `SignalUpdated`. Guardian di Lasna berlangganan event
   itu, jadi parameter baru langsung ter-mirror lintas chain. Saat harga
   menembus trigger, guardian meminta callback dan posisi ditutup di Base
   Sepolia — persis loop yang sudah dibuktikan di attempt 5 (lihat
   `../DEPLOY-REPORT.md`).

## Model sinyal (bahasa sederhana)

Model hanya melihat jendela harga terakhir (default 32 harga) dan mengukur dua hal:

- **Drawdown** — seberapa jauh harga jatuh dari puncak terbarunya.
  Contoh: puncak 100, harga sekarang 85 → drawdown 15%.
- **Volatilitas** — seberapa liar harga bergerak belakangan (simpangan baku
  dari perubahan persen per langkah, jendela 12 langkah).

Aturan mainnya (semua ambang dalam basis poin; 100 bps = 1%):

| Kondisi | Keputusan |
|---|---|
| Sampel < 5 | `hold` (pemanasan) |
| Belum armed + drawdown ≥ 8% **atau** volatilitas ≥ 3% | **ARM** |
| Sudah armed + risiko masih tinggi + trigger baru bergeser ≥ 3% dari trigger lama | **UPDATE** (trailing) |
| Sudah armed + drawdown ≤ 2% **dan** volatilitas ≤ 1% | **DISARM** |
| Selain itu | `hold` |

Saat ARM/UPDATE, **trigger** (harga penembus yang membuat guardian menembak)
dipasang di bawah harga sekarang dengan *buffer* pengaman:

```
buffer  = clamp(2 × volatilitas, 5%, 25%)     (default 10% bila volatilitas belum terukur)
trigger = harga × (1 − buffer)
```

Pasar yang liar mendapat stop yang lebih longgar (tidak gampang kena goyangan
kecil); pasar yang tenang mendapat stop yang lebih rapat. Semua keputusan
adalah fungsi murni dari deret harga + konfigurasi — deterministik, tanpa
acak, tanpa jam, tanpa API AI eksternal — sehingga perilakunya terkunci oleh
unit test.

## EIP-712 — persis seperti kontraknya

Diverifikasi field-per-field terhadap `project/src/origin/AISignalRegistry.sol`:

- Domain: name `AgenticStopLossSignal`, version `1`, chainId 84532,
  verifyingContract = alamat registry. Saat start, servis membaca
  `DOMAIN_SEPARATOR()` dan `SIGNAL_TYPEHASH()` dari chain dan **menolak jalan**
  bila hitungan lokalnya tidak sama.
- Struct: `SignalUpdate(address token,uint256 triggerPrice,bool armed,uint256 nonce,uint256 deadline)`.
  **Nonce yang ditandatangani harus nonce sinyal on-chain saat ini + 1** —
  kontrak menghitung sendiri nonce yang diharapkan dan memasukkannya ke hash;
  tanda tangan atas nonce lain akan menghasilkan signer yang salah dan revert.
- Digest: `keccak256("\x19\x01" ‖ DOMAIN_SEPARATOR ‖ structHash)`.
- Tanda tangan: 65 byte `r ‖ s ‖ v`, `v ∈ {27, 28}`, `s` rendah (low-s) —
  bentuk yang diterima `_recover` di kontrak.
- Implementasi Keccak-256 dan secp256k1 ditulis murni dengan Python stdlib
  (Python tidak punya Keccak — yang ada SHA3, itu beda), dan terkunci oleh
  test vector, termasuk DOMAIN_SEPARATOR on-chain yang tercatat.

## Menjalankan

Semua perintah dari direktori ini. **Default-nya dry-run** — tidak ada yang
di-broadcast tanpa flag `--live` secara eksplisit.

```bash
# Polling terus-menerus, dry-run (aman, hanya membaca chain + mencetak rencana)
python3 run.py

# Satu kali poll lalu keluar (dry-run)
python3 run.py --once

# Demo deterministik: umpankan deret harga sendiri ke model
# (state sinyal tetap dibaca dari chain; tetap dry-run)
python3 run.py --prices 100,100,100,100,100,92

# LIVE — broadcast sungguhan bila model memutuskan arm/update/disarm (testnet!)
python3 run.py --live --once
python3 run.py --live
```

Opsi lain: `--config <file>`, `--token <address>` (menimpa token dari config).

Output dry-run menunjukkan: keputusan + metrik model, digest EIP-712, tanda
tangan operator, hasil simulasi `eth_call`, dan transaksi mentah yang *akan*
dikirim (`cast mktx` menandatangani offline; di dry-run hasilnya hanya dicetak).

## Konfigurasi (`config.json`)

| Kunci | Arti |
|---|---|
| `rpc_urls` | Endpoint Base Sepolia; urutan = prioritas failover baca |
| `feed_address` / `registry_address` | Alamat kontrak hasil deploy attempt 5 |
| `token` | Token yang diawasi (demo: `0x…7e57`) |
| `key_file` | File kunci operator (burner) — dibaca saat runtime, tidak pernah dicetak |
| `cast_path` | Lokasi binary Foundry `cast` untuk `mktx` |
| `poll_interval_seconds` | Jeda antar poll |
| `signature_ttl_seconds` | Masa berlaku tanda tangan EIP-712 (deadline = waktu blok + ini) |
| `history_lookback_blocks` | Seberapa jauh event `PriceUpdated` ditarik saat bootstrap |
| `model.*` | Semua ambang model (lihat tabel di atas) |

Catatan RPC: `sepolia.base.org` membatasi `eth_getLogs` ke rentang 200 blok;
publicnode tidak. Kode bootstrap menyesuaikan ukuran chunk secara otomatis
bila kena batas itu.

## Test

```bash
python3 -m unittest discover -s tests -v
```

26 test offline (tanpa RPC): vektor Keccak-256 (termasuk yang dicocokkan ke
`cast keccak`), tanda tangan + recovery secp256k1, DOMAIN_SEPARATOR dan
SIGNAL_TYPEHASH yang dicocokkan ke nilai on-chain kontrak yang ter-deploy,
encoding calldata yang dicocokkan ke output `cast calldata`, dan seluruh
cabang keputusan model (warmup, calm, arm karena drawdown, arm karena
volatilitas, trailing update, hold saat trigger masih segar, disarm,
determinisme).

## Posisi servis ini dalam demo

Di bukti loop attempt 5, langkah "AI arm" (`registry.updateSignal(token, 90, true)`,
tx `0x6e343423…`) dilakukan manual sekali jalan. Dengan servis ini, langkah yang
sama — dan penyesuaian berikutnya — dihasilkan otomatis oleh model:
transaksinya berbentuk `updateSignalSigned(...)` dari alamat operator/relayer,
memancarkan `SignalUpdated` yang sama, dan guardian bereaksi dengan cara yang
sama persis. Untuk rekaman demo: jalankan servis (`--live`) di satu terminal,
lalu dorong harga menembus trigger dari terminal lain — urutan hash-nya akan
membaca: tx arm/update dari servis → tx reactive di Lasna → tx callback di
Base Sepolia → posisi tertutup.

## Keamanan

- **Testnet only.** Kunci yang dipakai adalah burner testnet khusus proyek ini.
- Kunci dibaca dari file saat runtime; tidak pernah ditulis ke log, tidak
  pernah di-hardcode. (Kunci diteruskan ke `cast mktx` sebagai argumen proses
  lokal, dan tidak dicetak di mana pun oleh servis.)
- Dry-run adalah default; satu-satunya jalur yang bisa mengubah state chain
  adalah fungsi broadcast yang menolak berjalan tanpa `--live`.
- Sebelum broadcast, selalu ada simulasi `eth_call`: update yang akan ditolak
  kontrak tidak pernah dikirim.
