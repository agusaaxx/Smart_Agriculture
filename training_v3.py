"""
================================================================================
 TRAINING.py (VERSI OPSI A) - XGBoost Multi-Output Regression
 Penambahan: R2 (metrik regresi standar), Feature Importance, Cross-Validation.
================================================================================
"""
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split, cross_val_score
from xgboost import XGBRegressor
from sklearn.multioutput import MultiOutputRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import joblib

print("1. Membaca dataset .csv...")
df = pd.read_csv('dataset_multi_v1.csv')

# 10 Fitur input.
# CATATAN: N_target/P_target/K_target KONSTAN untuk fase tunggal (importance = 0).
# Boleh dihapus dari list ini agar model lebih bersih; JIKA dihapus, hapus juga
# dari input_ai di main_system.py agar skema fitur tetap cocok.
FITUR = ['N_awal', 'P_awal', 'K_awal', 'N_target', 'P_target', 'K_target',
         'Suhu_Udara', 'Kelembapan_Udara', 'Kelembapan_Tanah', 'Musim']
TARGET = ['Target_Dosis_Gram', 'Target_Air_ml']

X = df[FITUR]
y = df[TARGET]
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42)

print("2. Melatih XGBoost Multi-Output (Dosis & Air sekaligus)...")
base_model = XGBRegressor(
    n_estimators=150, learning_rate=0.1, max_depth=5, random_state=42)
multi_model = MultiOutputRegressor(base_model)
multi_model.fit(X_train, y_train)

# ==========================================
# EVALUASI
# ==========================================
y_pred = multi_model.predict(X_test)

def evaluasi(nama, y_true, y_hat, batas):
    r2   = r2_score(y_true, y_hat)
    rmse = np.sqrt(mean_squared_error(y_true, y_hat))
    mae  = mean_absolute_error(y_true, y_hat)
    print(f"[{nama}] (batas maks {batas})")
    print(f" -> R^2  : {r2:.4f}   (metrik regresi utama; 1.0 = sempurna)")
    print(f" -> RMSE : {rmse:.4f}  ({rmse/batas*100:.2f} % dari rentang)")
    print(f" -> MAE  : {mae:.4f}  ({mae/batas*100:.2f} % dari rentang)")
    # Interpretasi tambahan (bukan metrik standar; sekadar bantu pembacaan awam)
    print(f" -> Akurasi (interpretasi 100-%MAE) : ~{100 - mae/batas*100:.2f} %")
    return r2, rmse, mae

print("\n" + "=" * 62)
print("HASIL EVALUASI METRIK AI (MULTI-OUTPUT):")
print("=" * 62)
evaluasi("DOSIS PUPUK (gram)", y_test['Target_Dosis_Gram'], y_pred[:, 0], 5.0)
print("-" * 62)
evaluasi("VOLUME AIR (ml)",   y_test['Target_Air_ml'],     y_pred[:, 1], 250.0)
print("=" * 62)

# ---- Feature importance (bahan pembahasan skripsi) ----
print("\nFEATURE IMPORTANCE terhadap DOSIS:")
imp = multi_model.estimators_[0].feature_importances_
for f, v in sorted(zip(FITUR, imp), key=lambda x: -x[1]):
    print(f"  {f:18s}: {v:.4f}")

# ---- Cross-validation (bukti generalisasi, bukan hafalan) ----
print("\nCROSS-VALIDATION 5-fold (R^2 dosis):")
cv = cross_val_score(
    XGBRegressor(n_estimators=150, learning_rate=0.1, max_depth=5, random_state=42),
    X, y['Target_Dosis_Gram'], cv=5, scoring='r2')
print(f"  mean R^2 = {cv.mean():.4f} | std = {cv.std():.4f} | folds = {np.round(cv,3)}")

# ==========================================
# SIMPAN MODEL
# ==========================================
nama_file = 'model_super_v3.pkl'   # <- sama dengan yang dimuat main_system.py
joblib.dump(multi_model, nama_file)
print(f"\n[SUKSES] Model disimpan sebagai {nama_file}")