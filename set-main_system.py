import firebase_admin
from firebase_admin import credentials, db
import joblib
import pandas as pd
import time
import warnings

warnings.simplefilter(action='ignore', category=FutureWarning)

print("="*60)
print("=== AI PRECISION AGRICULTURE SYSTEM AKTIF ===")
print("="*60)
print("1. Memuat Model AI XGBoost Multi-Variabel...")

nama_model = 'model_rekomendasi_agrikultur_v1.pkl' # Pastikan nama model sesuai
try:
    model_ai = joblib.load(nama_model) 
    print(f"-> [SUKSES] Model AI '{nama_model}' Berhasil Dimuat!")
except FileNotFoundError:
    print(f"-> [ERROR] File {nama_model} tidak ditemukan!")
    exit()

print("\n2. Menghubungkan ke Database Firebase...")
cred = credentials.Certificate("kredensial_firebase.json")
URL_DB = 'https://rekomendasi-pupuk-default-rtdb.asia-southeast1.firebasedatabase.app/'

firebase_admin.initialize_app(cred, {'databaseURL': URL_DB})
print(f"-> [SUKSES] Terhubung ke: {URL_DB}")
print("-> Menunggu data masuk dari ESP32 di kebun...\n")

def proses_data_baru(event):
    data_masuk = event.data
    
    if data_masuk and isinstance(data_masuk, dict):
        try:
            # Mengambil waktu dari ESP32 (Jika gagal, pakai waktu komputer backend)
            timestamp_sensor = data_masuk.get('waktu', time.strftime("%Y-%m-%d %H:%M:%S"))
            
            n_sekarang = float(data_masuk.get('nitrogen', 0))
            p_sekarang = float(data_masuk.get('fosfor', 0))
            k_sekarang = float(data_masuk.get('kalium', 0))
            suhu = float(data_masuk.get('suhu_udara', 30.0))
            kel_udara = float(data_masuk.get('kelembapan_udara', 70.0))
            kel_tanah = float(data_masuk.get('kelembapan_tanah', 60.0))
            musim = int(data_masuk.get('musim_cuaca', 0))
            
            n_target, p_target, k_target = 30.0, 40.0, 100.0
            
            print(f"\n[{timestamp_sensor}] DATA BARU DITERIMA:")
            print(f"-> NPK  : N:{n_sekarang} | P:{p_sekarang} | K:{k_sekarang}")
            print(f"-> Cuaca: Suhu {suhu}C | Kel Udara {kel_udara}% | Tanah {kel_tanah}%")

            # 1. HARDWARE FAIL-SAFE (Sensor Dicabut/Rusak)
            if n_sekarang == 0.0 and p_sekarang == 0.0 and k_sekarang == 0.0:
                print("-> [CRITICAL ERROR] Data NPK bernilai 0. Kabel Sensor Putus/Mati!")
                print("-> [INFO] Sistem dimatikan sementara mencegah penyiraman membabi-buta.")
                dosis_final = 0.0
                volume_air_ml = 0
                status_sistem = "FAIL_SAFE_HARDWARE"
                
            # 2. RULE-BASED DEADBAND (Tanah Ideal)
            elif (20.0 <= n_sekarang <= 40.0) and (30.0 <= p_sekarang <= 50.0) and (80.0 <= k_sekarang <= 120.0):
                print("-> Status Tanah: IDEAL. Memasuki Zona Toleransi (Bypass AI).")
                dosis_final = 0.0
                volume_air_ml = 100 
                status_sistem = "ZONA_IDEAL"
                
            else:
                print("-> Status Tanah: TIDAK SEIMBANG. Memanggil AI XGBoost...")
                input_ai = pd.DataFrame([{
                    'N_awal': n_sekarang, 'P_awal': p_sekarang, 'K_awal': k_sekarang,
                    'N_target': n_target, 'P_target': p_target, 'K_target': k_target,
                    'Suhu_Udara': suhu, 'Kelembapan_Udara': kel_udara,
                    'Kelembapan_Tanah': kel_tanah, 'Musim': musim
                }])
                
                prediksi_dosis = float(model_ai.predict(input_ai)[0])
                dosis_final = round(prediksi_dosis, 1)

                # 3. SAFETY CEILING LIMIT
                if dosis_final > 5.0:
                    dosis_final = 5.0
                    print(f"-> [INFO] Dosis asli AI: {prediksi_dosis:.1f}g. Dibatasi ke 5.0g (Cegah Overdosis).")
                    
                # 4. AMBANG BATAS EFISIENSI (< 1.0 Gram)
                elif 0.0 < dosis_final < 1.0:
                    print(f"-> [INFO] Prediksi AI {dosis_final}g (Defisit terlalu kecil).")
                    print("-> [INFO] Menunda pemupukan agar efisien (Target jadwal 3x seminggu).")
                    dosis_final = 0.0 

                # 5. KALKULASI AGEN PELARUT AIR
                if dosis_final == 0.0:
                    # Mengecek MENGAPA dosisnya 0?
                    if (n_sekarang > 40.0) or (p_sekarang > 50.0) or (k_sekarang > 120.0):
                        volume_air_ml = 250 
                        print("-> [INFO] Status Tanah: OVERDOSIS (Toksik). Air maksimal (250ml) untuk Leaching.")
                        status_sistem = "LEACHING_RACUN"
                    elif kel_tanah > 80.0:
                        volume_air_ml = 0
                        print("-> [INFO] Status Tanah: JENUH AIR (Kebanjiran). Tunda air (0ml) mencegah Runoff.")
                        status_sistem = "CEGAH_BANJIR"
                    else:
                        if kel_tanah < 40.0:
                            volume_air_ml = 250
                            print("-> [INFO] Tanah KERING. Pemupukan ditunda, tapi Air dipaksa maksimal (250ml) untuk rehidrasi.")
                            status_sistem = "REHIDRASI_OTONOM"
                        else:
                            volume_air_ml = 100 
                            status_sistem = "AIR_PERAWATAN"

# JIKA DOSIS >= 1.0 GRAM (WAKTUNYA PEMUPUKAN)
                else:
                    status_sistem = "INTERVENSI_AI"
                    
                    # 1. Dosis dari Model AI (Sudah terkalibrasi dengan Gaussian lebar)
                    dosis_final = round(prediksi_dosis, 1)

                    # 2. LOGIKA AIR MULTI-VARIABEL (SEKUENSIAL)
                    # A. Kebutuhan Dasar Rehidrasi (Tanah)
                    # Interpolasi linier 20%-80% kapasitas 250ml
                    if kel_tanah <= 20.0:
                        air_dasar = 250.0
                    elif kel_tanah >= 80.0:
                        air_dasar = 0.0
                    else:
                        air_dasar = 250.0 - (((kel_tanah - 20.0) / 60.0) * 250.0)
                    
                    # B. Kompensasi Evaporasi (Suhu & Udara)
                    faktor_suhu = max(0.0, (suhu - 28.0) / 10.0)
                    faktor_udara = max(0.0, (70.0 - kel_udara) / 30.0)
                    air_evap = (faktor_suhu * faktor_udara) * 75.0
                    
                    # C. Total Kalkulasi + Penalti Musim
                    faktor_musim = 0.7 if musim == 1 else 1.0
                    volume_air_ml = (air_dasar + air_evap) * faktor_musim
                    
                    # D. Batas Keamanan Pelarut (Minimal 30ml air per 1g pupuk)
                    batas_minimal = dosis_final * 30.0
                    volume_air_ml = max(batas_minimal, volume_air_ml)
                    
                    # E. Ceiling Limit Polybag
                    volume_air_ml = int(min(250.0, volume_air_ml))

                    print(f"-> [INFO] Kalkulasi Air Dinamis: {volume_air_ml} ml.")

            # TUGAS 1: Mengirim instruksi ke Aktuator ESP32 (OVERWRITE)
            db.reference('/rekomendasi_keluar').set({
                'dosis_gram': float(dosis_final),
                'volume_air_ml': int(volume_air_ml),
                'timestamp': timestamp_sensor
            })
            print("-> [SUKSES] Instruksi dikirim ke Aktuator.")

            # TUGAS 2: MASTER LOGGER RIWAYAT (PUSH)
            # Menyimpan 1 baris utuh (Waktu + Sensor + Keputusan) untuk diexport ke CSV
            payload_history = {
                'waktu': timestamp_sensor,
                'sensor_n': n_sekarang,
                'sensor_p': p_sekarang,
                'sensor_k': k_sekarang,
                'sensor_suhu': suhu,
                'sensor_kel_udara': kel_udara,
                'sensor_kel_tanah': kel_tanah,
                'musim': musim,
                'ai_dosis_gram': float(dosis_final),
                'ai_volume_air_ml': int(volume_air_ml),
                'status_logika': status_sistem
            }
            db.reference('/master_riwayat_sistem').push(payload_history)
            print("-> [SUKSES] Data ditambahkan ke /master_riwayat_sistem (Siap Export CSV)!\n")

        except Exception as e:
            print(f"-> [GAGAL] Terjadi kesalahan komputasi: {e}")

# Mendaftarkan listener Firebase
ref_sensor = db.reference('/sensor_masuk')
listener = ref_sensor.listen(proses_data_baru)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n[SHUTDOWN] Menonaktifkan sistem backend IoT...")
    listener.close()