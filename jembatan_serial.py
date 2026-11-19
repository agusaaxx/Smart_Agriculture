"""
================================================================================
 jembatan_serial.py - ESP32 (kabel USB) -> Python AI -> Firebase
--------------------------------------------------------------------------------
 ESP32 mengirim baris  DATA:{json}  lewat kabel USB.
 Python menghitung rekomendasi (logika main_system_v3.py), menyimpan ke Firebase
 (/sensor_masuk, /rekomendasi_keluar, /master_riwayat_sistem), lalu membalas
 ke ESP32 dengan baris  HASIL:dosis,air,status

 SYARAT:
   - pip install pyserial
   - Satu folder dengan: main_system_v3.py, server_lokal.py, model_super_v3.pkl,
     kredensial_firebase.json
   - TUTUP Serial Monitor Arduino IDE (port COM hanya boleh dipakai satu program)
   - JANGAN jalankan main_system_v3.py / server_lokal.py bersamaan
================================================================================
"""
import json
import time

import serial
import firebase_admin
from firebase_admin import credentials

import main_system_v3 as ms
import server_lokal as sl   # dipakai ulang: fungsi proses() (hitung + simpan Firebase)

PORT_COM = "COM4"     # sesuaikan dengan port ESP32 di Arduino IDE
BAUD = 115200


def jalankan():
    while True:
        try:
            with serial.Serial(PORT_COM, BAUD, timeout=1) as ser:
                print(f"-> Terhubung ke ESP32 di {PORT_COM}. Menunggu data...\n")
                time.sleep(2)  # ESP32 biasanya restart saat port dibuka
                while True:
                    baris = ser.readline().decode('utf-8', errors='ignore').strip()
                    if not baris:
                        continue

                    if baris.startswith("DATA:"):
                        try:
                            data = json.loads(baris[5:])
                            if not data.get('waktu'):
                                data['waktu'] = time.strftime("%Y-%m-%d %H:%M:%S")
                            hasil = sl.proses(data)
                            balasan = (f"HASIL:{hasil['dosis_gram']},"
                                       f"{hasil['volume_air_ml']},{hasil['status']}\n")
                        except Exception as e:
                            print(f"[GAGAL] Error saat memproses: {e}")
                            balasan = "ERROR:gagal_proses\n"
                        ser.write(balasan.encode('utf-8'))
                    else:
                        print(f"[ESP32] {baris}")   # tampilkan log biasa dari ESP32

        except serial.SerialException as e:
            print(f"[PERINGATAN] Port {PORT_COM} bermasalah: {e}")
            print("             Pastikan Serial Monitor Arduino DITUTUP dan kabel terpasang. "
                  "Mencoba lagi 5 detik...")
            time.sleep(5)


if __name__ == '__main__':
    cred = credentials.Certificate(ms.KREDENSIAL)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred, {'databaseURL': ms.URL_DB})
    print("-> Terhubung ke Firebase.")
    sl._waktu_terakhir = ms.ambil_waktu_riwayat_terakhir()

    try:
        jalankan()
    except KeyboardInterrupt:
        print("\nJembatan dihentikan.")
