# Arduino Relay Control Hardware Plan

## What You Already Have

| Part | Status | Used for |
|---|---|---|
| Arduino Nano clone | YES | main controller |
| LCD 1602A | YES | display value, relay state, status |
| I2C backpack HW-61 (PCF8574) | YES | reduces LCD wiring to 4 wires |
| 2-channel relay module CW-021 (SRD-05VDC-SL-C) | YES | switches 24V load line |
| LEDs (red, yellow, white) | YES | status indicator |
| Resistors assorted | YES | current limiting for LEDs |
| Push buttons | YES | manual emergency OFF |
| KTB966 transistor | NOT NEEDED | spare, skip for now |

## What You Still Need To Buy

| Part | Search term for Allegro | Why |
|---|---|---|
| USB cable for Nano | `kabel USB mini-B Arduino` or `micro USB 50cm` | connect Nano to PC |
| Jumper wires Dupont | `przewody dupont żeńsko-męskie` | wiring on breadboard |
| 24V DC power supply | `zasilacz 24V DC stabilizowany` | power for your sensor chain |
| Plastic enclosure | `obudowa plastikowa do elektroniki` | safe housing |
| Terminal blocks | `listwa zaciskowa 2-pin 5.08mm` | clean 24V wiring |
| Inline fuse holder | `uchwyt bezpiecznika przewodowy` | protect 24V branch |
| Fuse 1A slow | `bezpiecznik 1A wolny` | sized for your mA load |

Note: all other parts are already in your hands.

### Strongly Recommended

- Breadboard and jumper wires for low-voltage testing only
- Multimeter for checking continuity and 5 V supply
- Dupont headers and soldering tools for the LCD backpack
- Heat-shrink tubing and insulated connectors

### Optional But Useful

- Small buzzer or LED for alarm/status indication
- DS18B20 or another sensor only if you later want local sensing on Arduino

## Recommended Architecture

PC reads the multimeter export file and sends only the needed numeric value to Arduino over USB serial.
Arduino decides whether the relay should be ON or OFF.
If data stops arriving, becomes invalid, or gets too old, Arduino forces relay OFF.

## Best Switching Strategy For Your Case

Your load is very small: a 24 V power supply feeding converters and thermohygrometers at only a few milliamps each.

Because of that, the safer and simpler design is:

- keep the AC mains side permanently connected to the 24 V power supply
- switch the 24 V DC output line instead of switching the wall socket side

This is better than switching 230 V because:

- wiring is much safer
- lower risk of electric shock
- easier enclosure layout
- easier debugging with a multimeter

If the 24 V supply has a remote enable input, that can be even better than using a relay.
If it does not, then switch the 24 V DC output using a relay module or a DC-rated MOSFET stage.

For the first version, a relay module is still the simplest approach.

## Recommended Data Flow

1. Multimeter software updates a TXT, CSV, or JSON file every few seconds.
2. A small PC script reads the file and extracts the required value.
3. The script sends a compact JSON line over USB serial to Arduino.
4. Arduino checks thresholds, hysteresis, and timeout.
5. Arduino drives the relay module and optionally updates the LCD.

## Electrical Layout

```text
Multimeter software -> file on PC -> PC parser script -> USB -> Arduino Nano
                                                       |
                                                       +-> LCD 1602 + I2C backpack
                                                       |
                                                       +-> relay input pin -> isolated relay module

Relay module contacts (COM/NO/NC) switch the load circuit.
Arduino never connects directly to mains.
```

## Low-Voltage Wiring Plan

### 1. LCD 1602 with I2C backpack

First solder the I2C backpack onto the LCD 1602 pins.

Typical connections:

- Nano 5V -> backpack VCC
- Nano GND -> backpack GND
- Nano A4 -> backpack SDA
- Nano A5 -> backpack SCL

Notes:

- On Nano/ATmega328P, I2C is usually A4 SDA and A5 SCL.
- The blue potentiometer on the backpack sets LCD contrast.
- The backpack address is usually `0x27` or `0x3F`.

### 2. Relay module

Use a ready-made 5 V relay module.

Typical low-voltage connections:

- Nano 5V -> relay VCC
- Nano GND -> relay GND
- Nano D7 -> relay IN

Notes:

- Some modules are active LOW. That means writing LOW turns the relay ON.
- Confirm the logic with the module LED before connecting any real load.

### 3. USB connection

- PC USB -> Nano mini-USB or USB-C depending on the clone board
- USB provides serial communication and may power the board during testing

## Mains Side Principle

Only high-level safe principle:

- Live wire should be interrupted through relay COM and NO or NC depending on desired default state.
- Neutral and protective earth must be handled according to local electrical code.
- Keep mains wiring physically separated from Nano, LCD, and signal wires.

If this will switch a real wall outlet, the safe path is to use:

- a certified relay module or contactor in an insulated enclosure
- proper wire gauge
- fuse or breaker sized for the load
- strain relief on cables

## Recommended Final Topology For Your Load

Use this topology unless there is a strong reason not to:

```text
230 V AC wall outlet
    -> 24 V DC power supply
        -> relay contacts switch +24 V line
            -> converters / thermohygrometers

Arduino Nano only controls the relay coil/input side.
Arduino is not connected directly to 24 V.
```

That means:

- the dangerous 230 V part is reduced to just the PSU input side
- the controlled part becomes 24 V DC only
- the relay contacts carry only the small DC load

## What To Do With The KTB966

For the first version: do not use it.

Reason:

- A ready relay module already includes the driver stage and is easier to debug.
- Using a bare transistor would also require choosing a relay coil, base resistor, flyback diode, and correct power layout.
- It adds failure points without helping your first prototype.

## Build Order

1. Assemble Nano + LCD with I2C backpack only.
2. Verify the LCD works and shows test text.
3. Add the relay module on the low-voltage side only.
4. Verify relay clicks correctly from a simple Arduino test sketch.
5. Add serial communication from PC to Arduino.
6. Verify timeout behavior forces relay OFF.
7. Only after all low-voltage tests pass, wire the load side inside an insulated enclosure.

## First Power-Up Checklist

- Check for shorts between 5V and GND.
- Confirm LCD backpack is soldered in the correct orientation.
- Confirm relay input voltage matches the module rating.
- Confirm relay module current draw is within the power supply capability.
- Confirm relay defaults to OFF on boot.
- Confirm Arduino sets relay OFF if no valid packet is received for the timeout period.

## Functional Test Checklist

### Test 1: LCD only

- Power Nano from USB.
- Adjust LCD contrast trimmer.
- Confirm text appears.

### Test 2: Relay only, no mains

- Upload a blink-style relay sketch.
- Confirm module LED changes and relay clicks.
- Measure relay input signal with a multimeter if needed.

### Test 3: Serial data path

- Send test packets from the PC manually.
- Confirm Arduino accepts valid packets only.
- Confirm invalid JSON forces the safe state.

### Test 4: Timeout safety

- Send good values for a while.
- Stop the PC script.
- Confirm relay goes OFF after the configured timeout.

## Minimum Recommended BOM

- 1 x Arduino Nano compatible board
- 1 x LCD 1602 + I2C backpack
- 1 x 5 V 1-channel isolated relay module
- 1 x USB cable for Nano
- 1 x insulated box
- 1 x fuse holder + fuse matched to load
- jumper wires, terminal blocks, low-voltage wiring

## Buying Recommendation For Your Exact Case

Buy this as the first working set:

- 1 x 5 V single-channel relay module with optocoupler, transistor driver, and screw terminals
- 1 x 24 V DC power supply matched to your sensor chain current with margin
- 1 x DC fuse holder for the 24 V output side
- 1 x small fuse for the 24 V branch
- 1 x plastic DIN box or project enclosure
- 1 x set of terminal blocks for 24 V wiring

If you want an even cleaner solution later, replace the relay contact stage with:

- a low-side or high-side DC switch designed for 24 V loads
- or a PSU with remote enable input

## Concrete Wiring For The First Prototype

### Arduino side

- Nano 5V -> relay module VCC
- Nano GND -> relay module GND
- Nano D7 -> relay module IN
- Nano 5V -> LCD I2C VCC
- Nano GND -> LCD I2C GND
- Nano A4 -> LCD I2C SDA
- Nano A5 -> LCD I2C SCL

### 24 V side

Recommended contact use:

- 24 V PSU positive output -> relay COM
- relay NO -> positive input of your 24 V load chain
- 24 V PSU negative output -> directly to load negative

This way:

- relay OFF = no +24 V to the load
- relay ON = +24 V delivered to the load

Use NO rather than NC so the system stays OFF on power-up or failure.

## Complete Wiring Diagram (Your Real Parts)

```text
╔══════════════════════════════════════════════════════════════════════════════╗
║                       PC (USB Serial)                                        ║
╚══════════════════════════╦═══════════════════════════════════════════════════╝
                           ║ USB
                           ▼
╔══════════════════════════════════════════════════════╗
║                  Arduino Nano                        ║
║                                                      ║
║  5V  ─────────────────────────────┬──────────────┐  ║
║  GND ─────────────────────────────┼────────┬─────┼─ ║
║  A4 (SDA) ────────────────────── LCD-SDA  │     │  ║
║  A5 (SCL) ────────────────────── LCD-SCL  │     │  ║
║                                            │     │  ║
║  D7  ─────────────────── Relay IN1         │     │  ║
║                                            │     │  ║
║  D8  ── [220Ω] ── LED(red) ── GND         │     │  ║  ← статус реле
║                                            │     │  ║
║  D2  ── кнопка ── GND (INPUT_PULLUP)      │     │  ║  ← аварийное выкл.
║                                            │     │  ║
╚════════════════════════════════════════════╪═════╪══╝
                                             │     │
                        ╔════════════════════╧═════╧════════╗
                        ║    LCD 1602A + I2C backpack HW-61 ║
                        ║                                   ║
                        ║  VCC ← 5V                         ║
                        ║  GND ← GND                        ║
                        ║  SDA ← A4                         ║
                        ║  SCL ← A5                         ║
                        ║                                   ║
                        ║  [синий подстроечник = контраст]  ║
                        ╚═══════════════════════════════════╝

╔═══════════════════════════════════════════════════════╗
║   2-channel Relay Module CW-021  (SRD-05VDC-SL-C)    ║
║                                                       ║
║   VCC  ← 5V  (от Nano)                               ║
║   GND  ← GND (от Nano)                               ║
║   IN1  ← D7  (от Nano)   ← используем этот канал    ║
║   IN2  ← не подключать   ← запасной канал            ║
║                                                       ║
║   RELAY 1 contacts:                                   ║
║   ┌─────────────────────────────────────────────┐    ║
║   │  COM  ← +24V от блока питания               │    ║
║   │  NO   → +24V к нагрузке (термогигрометры)   │    ║
║   │  NC   ← не подключать                        │    ║
║   └─────────────────────────────────────────────┘    ║
╚═══════════════════════════════════════════════════════╝

╔═══════════════════════════════════════════════════════╗
║   24V DC Power Supply                                 ║
║                                                       ║
║   230V AC вход  ← розетка (всегда включен)           ║
║   +24V выход  ──[предохранитель 1A]──── Relay COM    ║
║   GND выход   ──────────────────────── нагрузка GND  ║
╚═══════════════════════════════════════════════════════╝

Нагрузка: термогигрометры / преобразователи 24V
  +24V ← Relay NO
  GND  ← 24V PSU GND

СОСТОЯНИЕ ПО УМОЛЧАНИЮ (при старте, ошибке, обрыве USB):
  D7 = HIGH → реле OFF → NO разомкнут → нагрузка без питания  ✓
```

## Назначение светодиода и кнопки

```text
D8 → [резистор 220 Ом] → LED (красный) → GND
  LED горит   = реле замкнуто (нагрузка получает питание)
  LED не горит = реле разомкнуто

D2 → кнопка → GND  (режим INPUT_PULLUP в Arduino)
  кнопка нажата = принудительное выключение реле
  приоритет выше любой команды с ПК
```

## Важно про модуль CW-021

Этот модуль активен LOW:
- `IN1 = LOW`  → реле ЗАМЫКАЕТСЯ
- `IN1 = HIGH` → реле РАЗОМКНУТО

Это значит, что в Arduino нужно при старте писать `digitalWrite(7, HIGH)` чтобы реле оставалось выключено до получения корректных данных с ПК.

## What Not To Buy Right Now

- no bare transistor relay-driver parts for the first build
- no SSR meant only for AC if you are switching 24 V DC
- no oversized contactor unless your load later becomes much larger
- no direct mains relay board mounted open on the table for final use

## Verification Specific To Your Logic

Your future control logic has two gates:

1. setpoint values from the climate chamber must remain unchanged for at least 2 hours
2. dew point must meet the required criterion

For hardware design, this means the relay stage should only react to a single final command from Arduino:

- ENABLE power
- DISABLE power

The timing and decision logic should stay in software, not in extra hardware.

## Recommended First Prototype Decision

Build the first version as:

- Arduino Nano
- LCD 1602 with I2C backpack
- ready-made 5 V relay module
- USB link to PC

Do not use the loose transistor in the first iteration.

## What I Will Deliver After You Send The Real Text File

Once you provide a sample of the multimeter text document, the next revision will include:

- an exact connection diagram with named pins and wire labels
- a clean schematic drawing for the final topology
- the PC-side parsing logic for your real TXT format with `;` separated fields
- the decision logic for:
    - chamber setpoints unchanged for at least 2 hours
    - dew point within the allowed criterion
    - fail-safe OFF on file or communication errors
- an exact bill of materials for the first build
- buying links or search links for the recommended modules

## Preliminary Purchase List

These are the parts worth buying now, because they are needed regardless of the exact TXT parsing logic.

### Core parts

- 1 x 5 V single-channel relay module with transistor driver, flyback diode, and screw terminals
- 1 x USB cable matching your Nano board connector
- 1 x set of Dupont jumper wires
- 1 x 2.54 mm male pin headers if your LCD or Nano needs soldered headers

### Safer wiring parts

- 1 x plastic enclosure for the final assembly
- 1 x terminal block set for 24 V wiring
- 1 x inline fuse holder for the 24 V positive line
- 1 x small fuse sized for the actual 24 V load current

### Helpful workshop parts

- soldering iron and solder if you do not have them yet
- heat-shrink tubing
- small screwdriver set

## Recommended Search Terms

Use these search phrases when buying:

- `5V 1 channel optocoupler relay module`
- `LCD1602 I2C backpack PCF8574`
- `Arduino Nano CH340`
- `24V inline fuse holder`
- `project enclosure plastic electronics box`
- `terminal block 2 pin 5.08`