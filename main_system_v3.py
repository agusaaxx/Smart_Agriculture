"""
================================================================================
 MAIN_SYSTEM.py (VERSI OPSI A + RIWAYAT) - Backend Rekomendasi Dosis NPK & Air
--------------------------------------------------------------------------------
 Selaras dengan generate_v3.py & training_v3.py:
 - Konstanta TARGET NPK dipusatkan (harus sama dgn generate_v3.py: 40/50/110).
 - Logika inti dipisah dari I/O Firebase -> mudah diuji unit.
 - Validasi & clamping input sensor (fail-safe lapangan).
 - Output menyertakan nutrien pembatas (Hukum Liebig) untuk keterlacakan.
 - BARU: setiap data sensor yang masuk + hasil rekomendasinya disimpan permanen
   ke /master_riwayat_sistem (1 push ID per data), tanpa menimpa data lama.
================================================================================
"""
import firebase_admin
from firebase_admin import credentials, db
import joblib
import pandas as pd
import time
import warnings

warnings.simplefilter(action='ignore', category=FutureWarning)

# ==========================================
# KONFIGURASI TERPUSAT (satu sumber kebenaran)
# ==========================================
NAMA_MODEL = 'model_super_v3.pkl'
KREDENSIAL = 'kredensial_firebase.json'
URL_DB = 'https://rekomendasi-pupuk-default-rtdb.asia-southeast1.firebasedatabase.app/'

# Node Firebase
NODE_SENSOR   = '/sensor_masuk'            # data mentah dari alat (selalu ditimpa)
NODE_OUTPUT   = '/rekomendasi_keluar'      # rekomendasi terbaru (selalu ditimpa)
NODE_RIWAYAT  = '/master_riwayat_sistem'   # RIWAYAT: data ditambah, tidak ditimpa

# Target NPK saat inferensi: pilih SATU nilai di dalam rentang yang dilatih
# generate_v3.py (N 30-40, P 40-50, K 100-110). Default = titik-tengah rentang.
N_TARGET, P_TARGET, K_TARGET = 35.0, 45.0, 105.0

# Batas fisik & rentang sensor yang wajar (untuk clamping / fail-safe)
BATAS_DOSIS = (0.0, 5.0)
BATAS_AIR   = (0, 250)
RANGE_SUHU  = (10.0, 45.0)
RANGE_RH    = (0.0, 100.0)
RANGE_TANAH = (0.0, 100.0)

# Penanda data terakhir yang sudah masuk riwayat (mencegah duplikat, mis. saat
# listener pertama kali menyala dan langsung menerima data lama di /sensor_masuk)
_waktu_terakhir_riwayat = None

print("=== AI PRECISION AGRICULTURE SYSTEM (OPSI A) AKTIF ===")

try:
    model_ai = joblib.load(NAMA_MODEL)
    print(f"-> Model '{NAMA_MODEL}' berhasil dimuat.")
except FileNotFoundError:
    print(f"[ERROR] File model '{NAMA_MODEL}' tidak ditemukan. Jalankan training_v3.py dulu.")
    raise SystemExit(1)


# ==========================================
# FUNGSI BANTU
# ==========================================
def _clamp(nilai, batas_bawah, batas_atas):
    return max(batas_bawah, min(batas_atas, nilai))


def bersihkan_sensor(data):
    """Ambil & validasi input dari Firebase; clamp ke rentang wajar."""
    n = float(data.get('nitrogen', 0))
    p = float(data.get('fosfor', 0))
    k = float(data.get('kalium', 0))
    suhu      = _clamp(float(data.get('suhu_udara', 30.0)),        *RANGE_SUHU)
    kel_udara = _clamp(float(data.get('kelembapan_udara', 70.0)),  *RANGE_RH)
    kel_tanah = _clamp(float(data.get('kelembapan_tanah', 60.0)),  *RANGE_TANAH)
    musim     = 1 if int(data.get('musim_cuaca', 0)) == 1 else 0
    return n, p, k, suhu, kel_udara, kel_tanah, musim


def hitung_rekomendasi(model, data):
    """
    Logika inti (murni, tanpa Firebase) -> bisa diuji unit.
    Mengembalikan dict berisi dosis, air, status, dan nutrien pembatas.
    """
    n, p, k, suhu, kel_udara, kel_tanah, musim = bersihkan_sensor(data)

    # FAIL-SAFE: sensor NPK mati/putus (semua nol) -> jangan memupuk.
    if n == 0.0 and p == 0.0 and k == 0.0:
        return {'dosis_gram': 0.0, 'volume_air_ml': 0,
                'status': 'SENSOR_NPK_MATI', 'nutrien_pembatas': '-'}

    # Susun 10 fitur (nama & jumlah HARUS cocok dengan training_v3.py).
    # Catatan: N/P/K_target konstan (fitur mati). Bila model dilatih ulang
    # tanpa kolom target, hapus 3 baris *_target di bawah ini.
    input_ai = pd.DataFrame([{
        'N_awal': n, 'P_awal': p, 'K_awal': k,
        'N_target': N_TARGET, 'P_target': P_TARGET, 'K_target': K_TARGET,
        'Suhu_Udara': suhu, 'Kelembapan_Udara': kel_udara,
        'Kelembapan_Tanah': kel_tanah, 'Musim': musim
    }])

    prediksi = model.predict(input_ai)[0]
    dosis = round(_clamp(float(prediksi[0]), *BATAS_DOSIS), 1)
    air   = int(_clamp(float(prediksi[1]), *BATAS_AIR))

    # Interpretasi: nutrien mana yang paling defisit (untuk keterlacakan).
    defisit = {'N': N_TARGET - n, 'P': P_TARGET - p, 'K': K_TARGET - k}
    pembatas = max(defisit, key=defisit.get) if max(defisit.values()) > 0 else 'CUKUP'

    return {'dosis_gram': dosis, 'volume_air_ml': air,
            'status': 'OK', 'nutrien_pembatas': pembatas}


# ==========================================
# RIWAYAT (HISTORY) KE FIREBASE
# ==========================================
def ambil_waktu_riwayat_terakhir():
    """Baca 'waktu' dari entri riwayat paling baru (dipanggil sekali saat start)."""
    try:
        snap = (db.reference(NODE_RIWAYAT)
                  .order_by_child('waktu').limit_to_last(1).get())
        if snap:
            return list(snap.values())[0].get('waktu')
    except Exception as e:
        print(f"[PERINGATAN] Gagal membaca riwayat terakhir: {e}")
    return None


def simpan_riwayat(sensor, hasil, waktu):
    """
    Tambah 1 entri baru ke /master_riwayat_sistem lewat push()
    -> Firebase membuat ID unik otomatis, data lama TIDAK tertimpa.
    Nama field sama dengan riwayat yang sudah ada di database.
    """
    n, p, k, suhu, kel_udara, kel_tanah, musim = sensor
    db.reference(NODE_RIWAYAT).push({
        'waktu': waktu,
        'sensor_n': n,
        'sensor_p': p,
        'sensor_k': k,
        'sensor_suhu': suhu,
        'sensor_kel_udara': kel_udara,
        'sensor_kel_tanah': kel_tanah,
        'musim': musim,
        'ai_dosis_gram': hasil['dosis_gram'],
        'ai_volume_air_ml': hasil['volume_air_ml'],
        'status_logika': 'INTERVENSI_AI' if hasil['status'] == 'OK' else hasil['status'],
    })


# ==========================================
# HANDLER FIREBASE
# ==========================================
def proses_data_baru(event):
    global _waktu_terakhir_riwayat

    data = event.data
    if not (data and isinstance(data, dict)):
        return
    try:
        sensor = bersihkan_sensor(data)
        n, p, k, suhu, kel_udara, kel_tanah, musim = sensor
        print(f"\n--- DATA SENSOR ---")
        print(f"NPK: [{n}, {p}, {k}] | Tanah: {kel_tanah}%")
        print(f"Udara: {suhu}C, {kel_udara}% | Musim: {'Hujan' if musim==1 else 'Kemarau'}")

        hasil = hitung_rekomendasi(model_ai, data)

        if hasil['status'] == 'SENSOR_NPK_MATI':
            print("-> [FAIL-SAFE] Sensor NPK mati/putus. Dosis diset 0.")
        else:
            print(f"-> Nutrien pembatas (Liebig): {hasil['nutrien_pembatas']}")
        print(f"-> OUTPUT AI : Pupuk NPK {hasil['dosis_gram']} g | "
              f"Air {hasil['volume_air_ml']} ml")

        sekarang = time.strftime("%Y-%m-%d %H:%M:%S")

        # 1) Rekomendasi terbaru (ditimpa, dibaca alat penyiram)
        db.reference(NODE_OUTPUT).set({
            'dosis_gram': hasil['dosis_gram'],
            'volume_air_ml': hasil['volume_air_ml'],
            'nutrien_pembatas': hasil['nutrien_pembatas'],
            'status': hasil['status'],
            'timestamp': sekarang
        })
        print("-> [SUKSES] Rekomendasi dikirim ke Firebase.")

        # 2) Riwayat (ditambah, tidak ditimpa). Gagal simpan riwayat tidak
        #    boleh menggagalkan rekomendasi yang sudah terkirim di atas.
        waktu_data = str(data.get('waktu') or sekarang)
        if waktu_data == _waktu_terakhir_riwayat:
            print("-> [RIWAYAT] Data sama dengan yang terakhir tersimpan, dilewati.")
        else:
            try:
                simpan_riwayat(sensor, hasil, waktu_data)
                _waktu_terakhir_riwayat = waktu_data
                print(f"-> [RIWAYAT] Tersimpan di {NODE_RIWAYAT} ({waktu_data}).")
            except Exception as e:
                print(f"[GAGAL] Riwayat tidak tersimpan: {e}")
    except Exception as e:
        print(f"[GAGAL] Error saat memproses: {e}")


# ==========================================
# INISIALISASI & LISTENER
# ==========================================
if __name__ == '__main__':
    cred = credentials.Certificate(KREDENSIAL)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred, {'databaseURL': URL_DB})
    print(f"-> Terhubung ke Firebase. Menunggu data masuk...\n")

    _waktu_terakhir_riwayat = ambil_waktu_riwayat_terakhir()

    listener = db.reference(NODE_SENSOR).listen(proses_data_baru)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        listener.close()
        print("\nListener dihentikan.")