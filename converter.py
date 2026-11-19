"""
Convert JSON Firebase Realtime Database -> XLSX (rapi, 1 nilai = 1 cell).

Persiapan (sekali saja):
    pip install openpyxl
"""
import csv
import json
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# ====== UBAH BAGIAN INI ======
FOLDER = r"C:\Users\User\Documents\my-skrispi\Smart_Agriculture"   # folder tempat file JSON berada
NAMA_FILE = None                    # "data.json" untuk 1 file, atau None untuk SEMUA .json di folder
NODE = "master_riwayat_sistem"      # node Firebase yang berisi riwayat (kumpulan push ID)
FORMAT_OUTPUT = "xlsx"              # "xlsx" (disarankan) atau "csv" (pemisah titik-koma untuk Excel Indonesia)

# Kolom polybag_id & fase TIDAK ada di Firebase, jadi diisi dari sini.
POLYBAG_ID = "Polybag-A"
FASE = "sebelum_pemupukan"          # "sebelum_pemupukan" atau "akhir"

# OPSIONAL: kalau 1 file berisi beberapa polybag/fase, tentukan per rentang waktu.
# Format: (waktu_mulai, waktu_selesai, polybag_id, fase)
# Data di luar rentang memakai POLYBAG_ID dan FASE di atas.
LABEL_RANGES = [
    # ("2026-07-23 15:00:00", "2026-07-23 15:40:00", "Polybag-A", "sebelum_pemupukan"),
    # ("2026-07-23 15:41:00", "2026-07-23 16:30:00", "Polybag-A", "akhir"),
]
# =============================

# nama field di Firebase -> nama kolom di file hasil
COLUMN_MAP = {
    "waktu": "timestamp",
    "ai_dosis_gram": "dosis_pupuk_gram",
    "ai_volume_air_ml": "volume_air_ml",
    "sensor_n": "nitrogen",
    "sensor_p": "fosfor",
    "sensor_k": "kalium",
}
OUT_COLUMNS = ["timestamp", "polybag_id", "fase", "dosis_pupuk_gram",
               "volume_air_ml", "nitrogen", "fosfor", "kalium"]
LEBAR_KOLOM = [21, 14, 20, 18, 16, 11, 11, 11]
FORMAT_WAKTU = "yyyy-mm-dd hh:mm:ss"


def rapikan(v):
    """7.0 -> 7, tetapi 2.8 tetap 2.8."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def get_node(data, path):
    for part in filter(None, path.split("/")):
        if part not in data:
            raise KeyError(f"Node '{path}' tidak ditemukan di JSON")
        data = data[part]
    return data


def cari_label(ts):
    for mulai, selesai, polybag, fase in LABEL_RANGES:
        if mulai <= ts <= selesai:
            return polybag, fase
    return POLYBAG_ID, FASE


def ke_datetime(teks):
    """Ubah teks waktu jadi tanggal asli Excel; kalau format tidak cocok, biarkan teks."""
    try:
        return datetime.strptime(str(teks), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return teks


def tulis_xlsx(rows, path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"

    font_biasa = Font(name="Arial", size=10)
    font_header = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    isi_header = PatternFill("solid", start_color="2F5597")
    garis = Side(style="thin", color="BFBFBF")
    border = Border(left=garis, right=garis, top=garis, bottom=garis)

    ws.append(OUT_COLUMNS)
    for r in rows:
        ws.append([ke_datetime(r["timestamp"])] + [r[c] for c in OUT_COLUMNS[1:]])

    for cell in ws[1]:
        cell.font, cell.fill, cell.border = font_header, isi_header, border
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font, cell.border = font_biasa, border
        row[0].number_format = FORMAT_WAKTU
        row[0].alignment = Alignment(horizontal="left")
        row[3].number_format = "0.0"            # dosis pupuk

    for i, lebar in enumerate(LEBAR_KOLOM, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = lebar

    ws.freeze_panes = "A2"                       # header tetap terlihat saat scroll
    ws.auto_filter.ref = ws.dimensions           # tombol filter di tiap kolom
    wb.save(path)


def tulis_csv(rows, path):
    # Excel versi Indonesia memakai ';' sebagai pemisah kolom, bukan ','.
    # utf-8-sig membuat Excel membaca karakter dengan benar.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def convert(json_path: Path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    node = get_node(data, NODE)
    items = node.values() if isinstance(node, dict) else node   # tiap push ID = 1 baris

    rows = []
    for rec in items:
        if not isinstance(rec, dict):
            continue
        row = {col: rapikan(rec.get(src, "")) for src, col in COLUMN_MAP.items()}
        row["polybag_id"], row["fase"] = cari_label(str(row["timestamp"]))
        rows.append(row)

    rows.sort(key=lambda r: (r["polybag_id"], str(r["timestamp"])))

    ekstensi = ".xlsx" if FORMAT_OUTPUT == "xlsx" else ".csv"
    out_path = json_path.with_name(json_path.stem + "_vertikal" + ekstensi)   # folder sama

    if FORMAT_OUTPUT == "xlsx":
        tulis_xlsx(rows, out_path)
    else:
        tulis_csv(rows, out_path)

    print(f"OK: {json_path.name} -> {out_path.name} ({len(rows)} baris)")


def main():
    folder = Path(FOLDER)
    files = [folder / NAMA_FILE] if NAMA_FILE else sorted(folder.glob("*.json"))
    if not files:
        print("Tidak ada file .json di folder tersebut.")
        return
    for file in files:
        try:
            convert(file)
        except PermissionError:
            print(f"GAGAL: {file.name} -> file hasil sedang terbuka di Excel. Tutup dulu lalu jalankan ulang.")
        except Exception as e:
            print(f"GAGAL: {file.name} -> {e}")


if __name__ == "__main__":
    main()