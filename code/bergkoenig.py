#!/usr/bin/env python3
"""
Stimmen:  Z = Melodie,  X = Bass,  Y = Quinte (Nachschlag),  A = Terz (gehalten)

  Achse  STEP             DIR
  X      Pin 21 (GPIO9)   Pin 23 (GPIO11)
  Y      Pin 31 (GPIO6)   Pin 33 (GPIO13)
  Z      Pin 35 (GPIO19)  Pin 37 (GPIO26)
  A      Pin 38 (GPIO20)  Pin 40 (GPIO21)   (Klon-Jumper A.STP/A.DIR auf "A")
  Pin 7 (GPIO4) -> EN,  Pin 17 (3,3 V) -> 5V,  Pin 14 (GND) -> GND

"""
import math
import sys
import time

import RPi.GPIO as GPIO

PINS = {"bass": 9, "quinte": 6, "melodie": 19, "terz": 20}  # STEP: Pin 21, 31, 35, 38
DIRS = [11, 13, 26, 21]                                       # DIR:  Pin 23, 33, 37, 40
EN = 4                                                         # Pin 7, Low = Treiber an

START_BPM = 100      # Viertel pro Minute am Anfang
END_BPM = 210        # ... und am Ende (Accelerando); gleich setzen = kein Accelerando
ARTIKULATION = 0.75  # kurz und gezupft, wie das Pizzicato im Original
OKTAVE = 0

HALBTOENE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}  # B = deutsches H

# (Note, Dauer in Achteln); None = Pause. Ein Takt = 8 Achtel.
THEMA_H_MOLL = [
    ("B3", 1), ("C#4", 1), ("D4", 1), ("E4", 1), ("F#4", 1), ("D4", 1), ("F#4", 2),
    ("F4", 1), ("C#4", 1), ("F4", 2), ("E4", 1), ("C4", 1), ("E4", 2),   # F4 = Eis
    ("B3", 1), ("C#4", 1), ("D4", 1), ("E4", 1), ("F#4", 1), ("D4", 1), ("F#4", 1), ("B4", 1),
    ("A4", 1), ("F#4", 1), ("D4", 1), ("F#4", 1), ("A4", 2), (None, 2),
]
THEMA_FIS_DUR = [
    ("F#4", 1), ("G#4", 1), ("A#4", 1), ("B4", 1), ("C#5", 1), ("A#4", 1), ("C#5", 2),
    ("D5", 1), ("A#4", 1), ("D5", 2), ("C#5", 1), ("A#4", 1), ("C#5", 2),
    ("F#4", 1), ("G#4", 1), ("A#4", 1), ("B4", 1), ("C#5", 1), ("A#4", 1), ("C#5", 2),
    ("D5", 1), ("A#4", 1), ("D5", 2), ("C#5", 2), (None, 2),
]
# Begleitung je Teil: (Bass, Quinte, Terz)
BEGLEITUNG = {"h": ("B2", "F#3", "D3"), "fis": ("F#2", "F#3", "C#3")}
FORM = ["h", "h", "fis", "fis", "h", "h", "fis", "fis", "h", "h"]


def frequenz(note: str) -> float:
    buchstabe, rest = note[0], note[1:]
    halbton = HALBTOENE[buchstabe]
    if rest.startswith("#"):
        halbton += 1
        rest = rest[1:]
    midi = 12 * (int(rest) + OKTAVE + 1) + halbton
    return 440.0 * 2 ** ((midi - 69) / 12)


def stimmen_bauen():
    melodie, bass, quinte, terz = [], [], [], []
    for teil in FORM:
        melodie += THEMA_H_MOLL if teil == "h" else THEMA_FIS_DUR
        b, q, t = BEGLEITUNG[teil]
        for _ in range(4):                       # 4 Takte pro Teil
            bass += [(b, 2)] * 4                 # Viertel
            quinte += [(None, 1), (q, 1)] * 4    # Nachschlag
            terz += [(t, 4)] * 2                 # Halbe
    # Schluss: zwei Akkordschlaege
    for stimme, note in ((melodie, "B3"), (bass, "B2"), (quinte, "F#3"), (terz, "D3")):
        stimme += [(note, 2), (None, 2), (note, 4)]
    return melodie, bass, quinte, terz


def zeitachse(gesamt_viertel_accel):
    """Viertel-Position -> Sekunden bei linear steigendem Tempo.

    tempo(b) = T0 + (T1 - T0) * b / B,  dt/db = 60 / tempo(b)
    => t(b) = 60 * B / (T1 - T0) * ln(tempo(b) / T0)
    """
    T0, T1, B = START_BPM, END_BPM, gesamt_viertel_accel

    def zeit(b):
        if b <= B:
            if T1 == T0:
                return 60.0 * b / T0
            return 60.0 * B / (T1 - T0) * math.log((T0 + (T1 - T0) * b / B) / T0)
        return zeit(B) + 60.0 * (b - B) / T1
    return zeit


def ereignisse(stimme, zeit):
    b, ev = 0.0, []
    for note, achtel in stimme:
        d = achtel / 2.0  # in Vierteln
        if note is not None:
            start, ende = zeit(b), zeit(b + d)
            ev.append((start, start + (ende - start) * ARTIKULATION,
                       1.0 / (2.0 * frequenz(note))))
        b += d
    return ev


def spiele(stimmen):
    """Mehrstimmig: stimmen = [(pin, [(start_s, stop_s, halbe_periode_s), ...]), ...]"""
    n = len(stimmen)
    pins = [p for p, _ in stimmen]
    evs = [e for _, e in stimmen]
    idx, naechste = [0] * n, [0.0] * n
    aktiv, pegel = [False] * n, [False] * n
    ende = max(e[-1][1] for e in evs if e)
    ausgabe, uhr = GPIO.output, time.perf_counter
    t0 = uhr()
    while True:
        jetzt = uhr() - t0
        if jetzt >= ende:
            break
        for k in range(n):
            ev = evs[k]
            i = idx[k]
            if i >= len(ev):
                continue
            start, stop, hp = ev[i]
            if jetzt >= stop:
                idx[k] = i + 1
                aktiv[k] = False
                continue
            if jetzt < start:
                continue
            if not aktiv[k]:
                aktiv[k] = True
                naechste[k] = start
            if jetzt >= naechste[k]:
                pegel[k] = not pegel[k]
                ausgabe(pins[k], pegel[k])
                naechste[k] += hp
                if naechste[k] < jetzt:
                    naechste[k] = jetzt + hp
    for p in pins:
        ausgabe(p, GPIO.LOW)


def main() -> None:
    durchlaeufe = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    melodie, bass, quinte, terz = stimmen_bauen()
    accel_viertel = len(FORM) * 16          # Accelerando bis zum Schluss
    zeit = zeitachse(accel_viertel)
    stimmen = [
        (PINS["bass"], ereignisse(bass, zeit)),
        (PINS["quinte"], ereignisse(quinte, zeit)),
        (PINS["melodie"], ereignisse(melodie, zeit)),
        (PINS["terz"], ereignisse(terz, zeit)),
    ]

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(list(PINS.values()) + DIRS, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(EN, GPIO.OUT, initial=GPIO.HIGH)
    try:
        GPIO.output(EN, GPIO.LOW)
        time.sleep(0.05)
        for d in range(durchlaeufe):
            print(f"In der Halle des Bergkoenigs - Durchlauf {d + 1} "
                  f"(ca. {zeit(accel_viertel + 4):.0f} s)")
            spiele(stimmen)
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        GPIO.output(EN, GPIO.HIGH)
        GPIO.cleanup()


if __name__ == "__main__":
    main()
