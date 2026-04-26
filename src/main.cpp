#include <Arduino.h>
#include <Wire.h>
#include <hd44780.h>
#include <hd44780ioClass/hd44780_I2Cexp.h>

// ── пины ──────────────────────────────────────
#define RELAY_PIN   5     // LOW = ON (CW-021 активен LOW)
#define BUTTON_PIN  2     // INPUT_PULLUP, нажата = LOW

// ── LCD (автоопределение backpack/распиновки) ──
hd44780_I2Cexp lcd;

// ── watchdog ──────────────────────────────────
#define WATCHDOG_MS 120000UL    // 2 минуты — OFF если PC молчит

static bool          lcdOk        = false;
static bool          relayState   = false;
static unsigned long lastMsgMs    = 0;
static unsigned long relayOnMs    = 0;
static unsigned long lastLcdTick  = 0;

// Последние значения от PC: CMD;roztdp;stable_min
static char lastDp[6] = "----";
static char lastSp[5] = "---";

static String field(const String& s, uint8_t idx) {
  int start = 0;
  for (uint8_t i = 0; i < idx; i++) {
    int p = s.indexOf(';', start);
    if (p < 0) return "";
    start = p + 1;
  }
  int end = s.indexOf(';', start);
  return (end < 0) ? s.substring(start) : s.substring(start, end);
}

static void updateLine1() {
  if (!lcdOk) return;
  char buf[17];
  if (relayState) {
    unsigned long secs = (millis() - relayOnMs) / 1000UL;
    unsigned int mm = (unsigned int)(secs / 60) % 100;
    unsigned int ss = (unsigned int)(secs % 60);
    snprintf(buf, sizeof(buf), "RELAY:ON  %02u:%02u ", mm, ss);
  } else {
    strncpy(buf, "RELAY:OFF       ", sizeof(buf));
  }
  lcd.setCursor(0, 0);
  lcd.print(buf);
}

static void updateLine2() {
  if (!lcdOk) return;
  char dp[5], sp[4];
  strncpy(dp, lastDp, 4); dp[4] = '\0';
  strncpy(sp, lastSp, 3); sp[3] = '\0';
  char buf[17];
  snprintf(buf, sizeof(buf), "dp=%-4s sp=%-3sm ", dp, sp);
  lcd.setCursor(0, 1);
  lcd.print(buf);
}

void setRelay(bool on) {
  if (on && !relayState) relayOnMs = millis();
  relayState = on;
  digitalWrite(RELAY_PIN, on ? LOW : HIGH);
  updateLine1();
  updateLine2();
}

void setup() {
  Serial.begin(9600);
  pinMode(RELAY_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  setRelay(false);   // безопасный старт — OFF

  Wire.begin();
  int lcdStatus = lcd.begin(16, 2);
  if (lcdStatus) {
    Serial.print("LCD init failed: ");
    Serial.println(lcdStatus);
  } else {
    lcdOk = true;
    lcd.backlight();
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("RELAY:OFF       ");
    lcd.setCursor(0, 1);
    lcd.print("Waiting for PC..");
  }

  Serial.println("READY");
  lastMsgMs = millis();
}

void loop() {
  // Кнопка аварийного отключения
  if (digitalRead(BUTTON_PIN) == LOW) {
    setRelay(false);
    if (lcdOk) {
      lcd.setCursor(0, 0);
      lcd.print("BUTTON! OFF     ");
    }
    delay(500);
  }

  // Fail-safe watchdog
  if (millis() - lastMsgMs > WATCHDOG_MS) {
    if (relayState) {
      setRelay(false);
      if (lcdOk) {
        lcd.setCursor(0, 0);
        lcd.print("WATCHDOG! OFF   ");
      }
    }
  }

  // Обновление таймера на LCD раз в секунду
  if (millis() - lastLcdTick >= 1000UL) {
    lastLcdTick = millis();
    updateLine1();
  }

  // Формат от PC: CMD;roztdp;stable_min
  if (Serial.available()) {
    String msg = Serial.readStringUntil('\n');
    msg.trim();
    if (msg.length() > 0) {
      lastMsgMs = millis();
      String cmd = field(msg, 0);
      String dp  = field(msg, 1);
      String sp  = field(msg, 2);

      if (dp.length() > 0) dp.toCharArray(lastDp, sizeof(lastDp));
      if (sp.length() > 0) sp.toCharArray(lastSp, sizeof(lastSp));

      if (cmd == "ON") {
        setRelay(true);
      } else {
        setRelay(false);
      }
    }
  }
}
