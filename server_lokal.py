"""
================================================================================
 server_lokal.py - Jembatan ESP32 -> Python AI -> Firebase (via jaringan lokal)
--------------------------------------------------------------------------------
 ESP32 mengirim data sensor ke PC lewat HTTP biasa (tanpa internet).
 PC menghitung rekomendasi memakai logika di main_system_v3.py, menyimpannya ke
 Firebase (/sensor_masuk, /rekomendasi_keluar, /master_riwayat_sistem), lalu
 mengembalikan hasilnya ke ESP32 pada respons yang sama.

 CARA PAKAI:
   1. Taruh file ini SATU FOLDER dengan main_system_v3.py, model_super_v3.pkl,
      dan kredensial_firebase.json.
   2. Jalankan:  python server_lokal.py
   3. JANGAN jalankan main_system_v3.py bersamaan (akan menyimpan riwayat ganda).
================================================================================
"""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import firebase_admin
from firebase_admin import credentials, db

# Memakai ulang semua logika & konfigurasi dari file utama Anda.
# (Aman: listener Firebase di file itu hanya jalan jika dieksekusi langsung.)
import main_system_v3 as ms

PORT = 5000
_kunci = threading.Lock()
_waktu_terakhir = None


def proses(data):
    """Hitung rekomendasi, lalu simpan ke Firebase. Mengembalikan hasil untuk ESP32."""
    global _waktu_terakhir

    hasil = ms.hitung_rekomendasi(ms.model_ai, data)
    sensor = ms.bersihkan_sensor(data)
    n, p, k, suhu, kel_udara, kel_tanah, musim = sensor
    sekarang = time.strftime("%Y-%m-%d %H:%M:%S")

    print("\n--- DATA SENSOR DITERIMA DARI ESP32 ---")
    print(f"NPK: [{n}, {p}, {k}] | Tanah: {kel_tanah}%")
    print(f"Udara: {suhu}C, {kel_udara}% | Musim: {'Hujan' if musim == 1 else 'Kemarau'}")
    if hasil['status'] == 'SENSOR_NPK_MATI':
        print("-> [FAIL-SAFE] Sensor NPK mati/putus. Dosis diset 0.")
    else:
        print(f"-> Nutrien pembatas (Liebig): {hasil['nutrien_pembatas']}")
    print(f"-> OUTPUT AI : Pupuk NPK {hasil['dosis_gram']} g | Air {hasil['volume_air_ml']} ml")

    # Penyimpanan ke Firebase. Kegagalan di sini TIDAK boleh menggagalkan
    # jawaban ke ESP32, jadi tiap langkah dibungkus try/except sendiri.
    try:
        db.reference(ms.NODE_SENSOR).set(data)
        db.reference(ms.NODE_OUTPUT).set({
            'dosis_gram': hasil['dosis_gram'],
            'volume_air_ml': hasil['volume_air_ml'],
            'nutrien_pembatas': hasil['nutrien_pembatas'],
            'status': hasil['status'],
            'timestamp': sekarang,
        })
        print("-> [SUKSES] Rekomendasi tersimpan di Firebase.")
    except Exception as e:
        print(f"[GAGAL] Tidak bisa menulis ke Firebase: {e}")

    waktu_data = str(data.get('waktu') or sekarang)
    with _kunci:
        if waktu_data == _waktu_terakhir:
            print("-> [RIWAYAT] Data sama dengan yang terakhir, dilewati.")
        else:
            try:
                ms.simpan_riwayat(sensor, hasil, waktu_data)
                _waktu_terakhir = waktu_data
                print(f"-> [RIWAYAT] Tersimpan di {ms.NODE_RIWAYAT} ({waktu_data}).")
            except Exception as e:
                print(f"[GAGAL] Riwayat tidak tersimpan: {e}")

    return hasil


class Handler(BaseHTTPRequestHandler):
    def _jawab(self, kode, obj):
        isi = json.dumps(obj).encode('utf-8')
        self.send_response(kode)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(isi)))
        self.end_headers()
        self.wfile.write(isi)

    def do_GET(self):
        # Untuk tes cepat lewat browser: http://localhost:5000/
        self._jawab(200, {'status': 'server aktif'})

    def do_POST(self):
        if self.path != '/data':
            self._jawab(404, {'error': 'path tidak ada'})
            return
        try:
            panjang = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(panjang) or b'{}')
            if not isinstance(data, dict):
                raise ValueError("body harus berupa objek JSON")
        except Exception as e:
            self._jawab(400, {'error': f'JSON tidak valid: {e}'})
            return
        try:
            hasil = proses(data)
            self._jawab(200, {
                'dosis_gram': hasil['dosis_gram'],
                'volume_air_ml': hasil['volume_air_ml'],
                'nutrien_pembatas': hasil['nutrien_pembatas'],
                'status': hasil['status'],
            })
        except Exception as e:
            print(f"[GAGAL] Error saat memproses: {e}")
            self._jawab(500, {'error': str(e)})

    def log_message(self, format, *args):
        pass  # sembunyikan log HTTP bawaan agar terminal bersih


def ip_lokal():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "tidak diketahui"


if __name__ == '__main__':
    cred = credentials.Certificate(ms.KREDENSIAL)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred, {'databaseURL': ms.URL_DB})
    print("-> Terhubung ke Firebase.")

    _waktu_terakhir = ms.ambil_waktu_riwayat_terakhir()

    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f"-> Server lokal aktif di port {PORT}. IP PC di jaringan utama: {ip_lokal()}")
    print("-> ESP32 di hotspot PC biasanya memakai alamat 192.168.137.1")
    print("-> Menunggu data dari ESP32... (Ctrl+C untuk berhenti)\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
        print("\nServer dihentikan.")
