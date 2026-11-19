#include <WiFi.h>
#include <Firebase_ESP_Client.h> 
#include <DHT.h>
#include "time.h" 

// ================= KONFIGURASI JARINGAN =================
#define WIFI_SSID "PC-DC-1A"
#define WIFI_PASSWORD "karakter"

// ================= KONFIGURASI FIREBASE =================
#define FIREBASE_HOST "rekomendasi-pupuk-default-rtdb.asia-southeast1.firebasedatabase.app"
#define FIREBASE_AUTH "ZpAdb1bhcuWDd6WLSI2hid3ZGVwQNX7MYtMx62JJ" 

FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig config;

// ================= PIN SENSOR RS485 (NPK) =================
#define RX_PIN 16
#define TX_PIN 17

// ================= PIN SENSOR DHT22 (Cuaca) =================
#define DHTPIN 4       
#define DHTTYPE DHT22  
DHT dht(DHTPIN, DHTTYPE);

// ================= PIN SENSOR SEN-0016 (Tanah) =================
#define SOIL_PIN 34    

// ================= PENGATURAN WAKTU (NTP) =================
const char* ntpServer = "pool.ntp.org";
const long  gmtOffset_sec = 25200; // WIB (GMT+7)
const int   daylightOffset_sec = 0;

float n_sekarang = 0, p_sekarang = 0, k_sekarang = 0;
float suhu_udara = 0, kel_udara = 0, kel_tanah = 0;
int musim_cuaca = 0;

unsigned long waktuKirimTerakhir = 0;
const long intervalKirim = 15000; // Diberi jeda 15 detik agar AI Python punya waktu berhitung

void setup() {
  Serial.begin(115200);
  Serial2.begin(9600, SERIAL_8N1, RX_PIN, TX_PIN); 
  delay(2000); 
  
  Serial.println("\n=== SISTEM PERTANIAN PRESISI AI + DATA LOGGER ===");
  dht.begin();
  
  // 1. Koneksi WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Menghubungkan ke Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print("."); delay(500);
  }
  Serial.println("\n[SUKSES] Terhubung ke Wi-Fi!");

  // 2. Ambil Waktu dari Internet (NTP)
  configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
  Serial.print("Menunggu sinkronisasi jam NTP");
  struct tm timeinfo;
  int retry = 0;
  while (!getLocalTime(&timeinfo) && retry < 15) {
    Serial.print("."); delay(1000); retry++;
  }
  if (retry < 15) Serial.println("\n[SUKSES] Jam internal tersinkronisasi!");
  else Serial.println("\n[GAGAL] NTP diblokir. Lanjut tanpa waktu internet.");

  // 3. Koneksi Firebase
  config.database_url = FIREBASE_HOST;
  config.signer.tokens.legacy_token = FIREBASE_AUTH;
  config.timeout.serverResponse = 10 * 1000;   // tunggu respons hingga 10 dtk
  fbdo.setBSSLBufferSize(4096, 1024);          // buffer SSL (RX, TX)
  fbdo.setResponseSize(2048);
  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);
}

String dapatkanWaktu() {
  struct tm timeinfo;
  if(!getLocalTime(&timeinfo)) return "Waktu_Gagal_Didapat";
  char timeStringBuff[50];
  strftime(timeStringBuff, sizeof(timeStringBuff), "%Y-%m-%d %H:%M:%S", &timeinfo);
  return String(timeStringBuff);
}

void bacaSemuaSensor() {
  // 1. Baca NPK (RS485)
  const byte npkInquiry[] = {0x01, 0x03, 0x00, 0x1E, 0x00, 0x03, 0x65, 0xCD};
  byte respons[11];
  while(Serial2.available()) Serial2.read();
  Serial2.write(npkInquiry, sizeof(npkInquiry));
  delay(100); 
  
  if (Serial2.available() >= 11) {
    for (int i = 0; i < 11; i++) respons[i] = Serial2.read();
    n_sekarang = (respons[3] << 8) | respons[4];
    p_sekarang = (respons[5] << 8) | respons[6];
    k_sekarang = (respons[7] << 8) | respons[8];
  } else {
    n_sekarang = 0; p_sekarang = 0; k_sekarang = 0;
  }

  // 2. Baca Cuaca (DHT22)
  suhu_udara = dht.readTemperature();
  kel_udara = dht.readHumidity();
  if (isnan(suhu_udara)) { suhu_udara = 0; kel_udara = 0; }

  // 3. Baca Kelembapan Tanah (SEN-0016 Analog)
  int soilValue = analogRead(SOIL_PIN);
  if (soilValue >= 4000 || soilValue <= 100) kel_tanah = 0;
  else if (soilValue > 1665) kel_tanah = map(soilValue, 4000, 1665, 0, 65);
  else kel_tanah = map(soilValue, 1665, 815, 65, 100);
  kel_tanah = constrain(kel_tanah, 0, 100); 
}

void menentukanMusimLogis() {
  if (suhu_udara >= 32.0 || kel_udara <= 55.0) musim_cuaca = 0; // Kemarau
  else if (suhu_udara <= 28.0 && kel_udara >= 85.0) musim_cuaca = 1; // Hujan
  else musim_cuaca = 0; // Default
}

void loop() {
  if (millis() - waktuKirimTerakhir >= intervalKirim) {
    waktuKirimTerakhir = millis();
    
    bacaSemuaSensor(); 
    menentukanMusimLogis();
    String waktu_sekarang = dapatkanWaktu();
    
    Serial.println("\n--- MEMICU BACKEND AI ---");
    Serial.println("Waktu    : " + waktu_sekarang);
    Serial.printf("NPK      : N:%.1f P:%.1f K:%.1f\n", n_sekarang, p_sekarang, k_sekarang);
    Serial.printf("Cuaca    : Suhu %.1fC | Kelembapan %.1f%%\n", suhu_udara, kel_udara);
    Serial.printf("Tanah    : Kelembapan %.1f%%\n", kel_tanah);
    
    FirebaseJson jsonData;
    jsonData.set("waktu", waktu_sekarang); // Menyertakan waktu untuk di-log oleh Python
    jsonData.set("nitrogen", n_sekarang);
    jsonData.set("fosfor", p_sekarang);
    jsonData.set("kalium", k_sekarang);
    jsonData.set("suhu_udara", suhu_udara);
    jsonData.set("kelembapan_udara", kel_udara);
    jsonData.set("kelembapan_tanah", kel_tanah);
    jsonData.set("musim_cuaca", musim_cuaca);
    
    // Memicu Python AI (OVERWRITE di sensor_masuk)
    if(Firebase.RTDB.setJSON(&fbdo, "/sensor_masuk", &jsonData)){
      Serial.println("[SUKSES] Trigger dikirim! Menunggu AI...");
    }
    
    // Jeda aman untuk memberikan waktu Python berhitung dan mencatat riwayat
    delay(3000); 
    
    float dosis = 0; int air = 0;
    if (Firebase.RTDB.getFloat(&fbdo, "/rekomendasi_keluar/dosis_gram")) dosis = fbdo.to<float>();
    if (Firebase.RTDB.getInt(&fbdo, "/rekomendasi_keluar/volume_air_ml")) air = fbdo.to<int>();
    
    Serial.println("===================================");
    Serial.printf(" REKOMENDASI AI : %s GRAM\n", String(dosis, 1).c_str());
    Serial.printf(" KEBUTUHAN AIR  : %d ML\n", air);
    Serial.println("===================================");
  }
}