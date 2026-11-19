"""
================================================================================
 GENERATE.py  (VERSI OPSI A - GROUNDED PADA DATA LAPANGAN)
--------------------------------------------------------------------------------
 Perubahan mendasar dari versi lama:
 - Target dosis TIDAK lagi dikarang dari rumus Gaussian.
   Target dosis dibangun dengan MEMBALIK (invert) kurva dosis-respons NPK
   yang benar-benar TERUKUR di lapangan (6 polybag, dosis 0-5 g).
 - Prinsip agronomi: Hukum Minimum Liebig -> dosis ditentukan nutrien
   yang paling defisit terhadap target fase.
 - Augmentasi = menambah noise sensor pada pembacaan NPK asli (realistis),
   bukan menciptakan hubungan baru.

 CATATAN KEJUJURAN (WAJIB ditulis di skripsi):
 1. Data asli HANYA mengukur NPK & dosis. Suhu/kelembapan/musim TIDAK ada
    di data lapangan -> parameter lingkungan di sini DISIMULASIKAN sebagai
    kondisi operasi. Jadi relasi lingkungan->output belum tervalidasi lapangan.
 2. Yang benar-benar grounded pada data lapangan adalah relasi NPK->DOSIS.
    Volume air bersifat AUXILIARY (heuristik irigasi kapasitas-lapang),
    karena penyiraman di eksperimen bersifat protokol tetap (~40 ml).
================================================================================
"""

import pandas as pd
import numpy as np
import random
import math
import os
import glob

# ==========================================
# KONFIGURASI
# ==========================================
JUMLAH_DATA = 10000
SEED = 42
random.seed(SEED)
np.random.seed(SEED)

# Target NPK sebagai RENTANG kecukupan hara fase (bukan titik tunggal).
# Tiap baris data mengambil target ACAK dalam rentang ini -> kolom *_target
# menjadi FITUR HIDUP (tak lagi konstan) & model belajar beradaptasi ke
# target yang berbeda. Lebar/posisi rentang bebas Anda sesuaikan (sitir literatur).
# Contoh: N 30-40. Titik-tengah rentang menentukan level dosis rata-rata.
N_TARGET_RANGE = (30.0, 40.0)
P_TARGET_RANGE = (40.0, 50.0)
K_TARGET_RANGE = (100.0, 110.0)

# Presisi sensor (std noise augmentasi). Sesuaikan dengan datasheet sensor NPK Anda.
NOISE_NPK_N = 2.5
NOISE_NPK_P = 2.5
NOISE_NPK_K = 5.0

# --- Parameter MODEL IRIGASI (air pelarut/penyiraman) --------------------------
# Semua koefisien di bawah punya dasar agronomi & BISA DISITIR/DISESUAIKAN.
# (Dinaikkan agar respons air lebih agresif terhadap kekeringan.)
KAPASITAS_LAPANG = 85.0      # (%) target kelembapan tanah cabai: lembap, tak tergenang.
KAP_AIR_MEDIA    = 300.0     # (ml) air untuk menaikkan lengas media polybag 0->100%.
                            #      Kalibrasi dari volume & jenis media tanam Anda.
KOEF_ET_MAKS     = 70.0      # (ml) tambahan air maksimum akibat evapotranspirasi.
FAKTOR_HUJAN     = 0.6       # pengali saat musim hujan (kontribusi presipitasi alami).
ML_PELARUT_PER_GRAM = 10.0   # (ml/gram) air MINIMUM untuk melarutkan pupuk (air pelarut).
                            #      Mencegah larutan terlalu pekat / fertilizer burn.

# --- Tuning AGRESIVITAS DOSIS (preferensi output) ------------------------------
# < 1.0 = dosis lebih konservatif (koreksi bertahap/split -> hindari fertilizer burn).
#   1.0 = koreksi defisit penuh sekali aplikasi.
# 0.65 diselaraskan agar tingkat dosis mendekati v2 yang sudah Anda validasi.
GAIN_DOSIS = 1.3

# --- (Opsional) MODULASI LINGKUNGAN pada DOSIS -- diimpor dari v2 ---------------
# Dasar agronomi: Nutrient Use Efficiency (NUE) menurun pada kondisi ekstrem
# (tanah terlalu kering/basah, suhu ekstrem) -> dosis sedikit dikurangi.
# Koefisien Gaussian diambil dari v2 yang sudah Anda uji.
#   True  = dosis IKUT merespons lingkungan seperti v2 (perilaku lebih mirip v2,
#           tetapi feature-importance dosis jadi tak sebersih pemisahan NPK murni).
#   False = dosis MURNI berbasis NPK (pemisahan bersih: dosis<-NPK, air<-lingkungan).
PAKAI_MODULASI_LINGKUNGAN = False

# ==========================================
# 1. MEMBACA DATA LAPANGAN (robust terhadap nama file)
# ==========================================
kandidat = (glob.glob('dataset_bersih_vertikal*day*4*.csv') +
            glob.glob('*bersih*vertikal*.csv'))
if not kandidat:
    print("[ERROR] File data asli lapangan tidak ditemukan di folder ini.")
    print("        Letakkan 'dataset_bersih_vertikal_day_4.csv' di sini.")
    raise SystemExit(1)

NAMA_FILE_CSV = kandidat[0]
print(f"Membaca data lapangan dari '{NAMA_FILE_CSV}'...")
df_asli = pd.read_csv(NAMA_FILE_CSV)

# Deteksi nama kolom NPK
if 'nitrogen' in df_asli.columns:
    col_n, col_p, col_k = 'nitrogen', 'fosfor', 'kalium'
elif 'N_awal' in df_asli.columns:
    col_n, col_p, col_k = 'N_awal', 'P_awal', 'K_awal'
else:
    col_n, col_p, col_k = 'N', 'P', 'K'

# ==========================================
# 2. MEMBANGUN KURVA DOSIS-RESPONS EMPIRIS + INVERSI
# ==========================================
# Fase 'akhir' = NPK tanah setelah diberi dosis -> inilah respons nyata.
if 'fase' in df_asli.columns:
    df_resp = df_asli[df_asli['fase'] == 'akhir']
else:
    df_resp = df_asli

resp = (df_resp.groupby('dosis_pupuk_gram')[[col_n, col_p, col_k]]
        .mean().sort_index())
doses = resp.index.values.astype(float)

# Monotonisasi kurva dosis-respons dengan RUNNING MAXIMUM (aritmetika murni,
# bukan algoritma regresi). Menjamin: dosis lebih tinggi tak pernah memberi
# hara lebih rendah daripada dosis di bawahnya -> sekaligus memperbaiki anomali
# dosis=4 secara sah tanpa menghapus data.
kurva = {}
for c in [col_n, col_p, col_k]:
    kurva[c] = np.maximum.accumulate(resp[c].values)

print("Kurva dosis-respons empiris (setelah dimonotonkan):")
for c in [col_n, col_p, col_k]:
    print(f"  {c:9s}: {np.round(kurva[c], 1)}")

def dosis_ekuivalen(nutrient, nilai_npk):
    """Balik kurva: dari nilai NPK -> 'dosis ekuivalen' yang sudah ada di tanah."""
    return float(np.interp(nilai_npk, kurva[nutrient], doses))

# Kumpulan pembacaan NPK asli sebagai "DNA" untuk di-augmentasi (semua fase,
# agar mencakup rentang kondisi tanah yang luas dari sangat miskin s/d subur)
npk_pool = df_asli[[col_n, col_p, col_k]].values

# ==========================================
# 3. LOOP AUGMENTASI
# ==========================================
data_sintesis = []
print(f"\nMulai augmentasi {JUMLAH_DATA} baris (Opsi A - grounded)...")

for i in range(JUMLAH_DATA):
    # --- 3a. AUGMENTASI NPK: ambil baris asli + noise sensor ---
    base = npk_pool[np.random.randint(len(npk_pool))]
    N_awal = round(max(0.0, base[0] + np.random.normal(0, NOISE_NPK_N)), 1)
    P_awal = round(max(0.0, base[1] + np.random.normal(0, NOISE_NPK_P)), 1)
    K_awal = round(max(0.0, base[2] + np.random.normal(0, NOISE_NPK_K)), 1)

    # --- 3b. SIMULASI KONDISI LINGKUNGAN (DISCLOSED: bukan data lapangan) ---
    suhu       = round(random.uniform(22.0, 36.0), 1)   # DHT22
    kel_udara  = round(random.uniform(45.0, 95.0), 1)   # DHT22
    kel_tanah  = round(random.uniform(15.0, 95.0), 1)   # SEN-0016
    if suhu > 33.0 or kel_udara < 55.0:
        musim = 0                                        # kemarau
    elif suhu < 26.0 and kel_udara > 85.0:
        musim = 1                                        # hujan
    else:
        musim = random.choice([0, 1])

    # --- 3c. REKOMENDASI DOSIS = INVERSI KURVA EMPIRIS + HUKUM LIEBIG ---
    # Target fase diambil ACAK dalam rentang -> menjadikan *_target fitur hidup.
    N_target = round(random.uniform(*N_TARGET_RANGE), 1)
    P_target = round(random.uniform(*P_TARGET_RANGE), 1)
    K_target = round(random.uniform(*K_TARGET_RANGE), 1)
    # Kebutuhan tiap nutrien = (dosis ekuivalen target) - (dosis ekuivalen kini)
    need_n = dosis_ekuivalen(col_n, N_target) - dosis_ekuivalen(col_n, N_awal)
    need_p = dosis_ekuivalen(col_p, P_target) - dosis_ekuivalen(col_p, P_awal)
    need_k = dosis_ekuivalen(col_k, K_target) - dosis_ekuivalen(col_k, K_awal)
    # Nutrien paling defisit yang menentukan (Hukum Minimum Liebig),
    # dikalikan GAIN_DOSIS agar tingkat agresivitas dosis bisa diatur.
    dosis_dasar = max(need_n, need_p, need_k, 0.0) * GAIN_DOSIS

    # Modulasi lingkungan pada DOSIS (opsional, koefisien diimpor dari v2 = NUE).
    if PAKAI_MODULASI_LINGKUNGAN:
        k_tanah = max(0.90, math.exp(-0.5 * ((kel_tanah - 60.0) / 80.0) ** 2))
        k_suhu  = max(0.95, math.exp(-0.5 * ((suhu - 28.0) / 25.0) ** 2))
        k_udara = max(0.95, math.exp(-0.5 * ((kel_udara - 70.0) / 50.0) ** 2))
        k_musim = 0.90 if musim == 1 else 1.0
        dosis_final = dosis_dasar * (k_tanah * k_suhu * k_udara * k_musim)
    else:
        dosis_final = dosis_dasar

    # --- 3d. VOLUME AIR = MODEL IRIGASI BERBASIS PRINSIP AGRONOMI ---------------
    # DISCLOSED: relasi ini berdasar LITERATUR irigasi (belum tervalidasi lapangan,
    # karena eksperimen tidak mengukur lengas vs kebutuhan air). Tiap suku bisa
    # dijustifikasi & disitir:
    #
    #  (1) Pengisian defisit lengas hingga KAPASITAS LAPANG.
    #      Tanah makin kering -> makin banyak air. Di atas kapasitas lapang = 0.
    defisit_lengas = max(0.0, KAPASITAS_LAPANG - kel_tanah) / 100.0
    air_lengas = defisit_lengas * KAP_AIR_MEDIA
    #
    #  (2) Tambahan EVAPOTRANSPIRASI via proxy Vapor Pressure Deficit (VPD):
    #      udara panas & kering (RH rendah) -> VPD tinggi -> penguapan tinggi.
    vpd_proxy = (suhu / 40.0) * (1.0 - kel_udara / 100.0)   # rentang ~0..1
    air_et = vpd_proxy * KOEF_ET_MAKS
    #
    #  (3) Total kebutuhan IRIGASI tanah, dikurangi bila musim HUJAN.
    air_irigasi = air_lengas + air_et
    if musim == 1:
        air_irigasi *= FAKTOR_HUJAN
    #  (4) SYARAT AIR PELARUT: minimal cukup untuk melarutkan dosis pupuk.
    #      Mencegah kasus tak-fisik (mis. 4.4 g NPK dalam 5 ml air) saat tanah basah.
    dosis_utk_pelarut = min(5.0, max(0.0, dosis_final))
    air_pelarut = dosis_utk_pelarut * ML_PELARUT_PER_GRAM
    #  Air dipakai = TERBESAR antara kebutuhan irigasi & syarat pelarut.
    volume_air = max(air_irigasi, air_pelarut)

    # --- 3e. NOISE EVALUASI (variansi tak-terjelaskan yang realistis) ---
    dosis_final += np.random.normal(0, 0.20)
    volume_air  += np.random.normal(0, 8.0)

    # --- 3f. PEMBATASAN FISIK ---
    dosis_final = round(float(np.clip(dosis_final, 0.0, 5.0)), 1)
    volume_air  = int(np.clip(volume_air, 0, 250))

    data_sintesis.append([
        N_awal, P_awal, K_awal, N_target, P_target, K_target,
        suhu, kel_udara, kel_tanah, musim,
        dosis_final, volume_air
    ])

# ==========================================
# 4. SIMPAN
# ==========================================
kolom = ['N_awal', 'P_awal', 'K_awal', 'N_target', 'P_target', 'K_target',
         'Suhu_Udara', 'Kelembapan_Udara', 'Kelembapan_Tanah', 'Musim',
         'Target_Dosis_Gram', 'Target_Air_ml']
df_hasil = pd.DataFrame(data_sintesis, columns=kolom)
nama_output = 'dataset_multi_v1.csv'
df_hasil.to_csv(nama_output, index=False)

print(f"\n[SUKSES] {JUMLAH_DATA} baris dibuat -> '{nama_output}'")
print("Sebaran dosis (cek: harus tersebar 0-5, bukan menumpuk di 4):")
print(df_hasil['Target_Dosis_Gram'].value_counts().sort_index().to_string())