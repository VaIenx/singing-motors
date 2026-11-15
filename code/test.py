#!/usr/bin/env python3
import threading
import time
import RPi.GPIO as GPIO

# name: (STEP, DIR, Frequenz in Hz)
MOTOREN = {
    "bass":    (9,  11, 261.63),  # C4
    "quinte":  (6,  13, 329.63),  # E4
    "melodie": (19, 26, 392.00),  # G4
    "terz":    (20, 21, 493.88),  # H4
}
EN = 4
DAUER = 5.0
VERZOEGERUNG = 1


def spiele(step, hz, start, ende):
    time.sleep(start)
    halb = 0.5 / hz
    while time.perf_counter() < ende:
        GPIO.output(step, GPIO.HIGH)
        time.sleep(halb)
        GPIO.output(step, GPIO.LOW)
        time.sleep(halb)


GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup([s for s, _, _ in MOTOREN.values()] + [d for _, d, _ in MOTOREN.values()],
           GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(EN, GPIO.OUT, initial=GPIO.HIGH)

try:
    GPIO.output(EN, GPIO.LOW)
    ende = time.perf_counter() + DAUER

    threads = []
    for i, (step, dir_pin, hz) in enumerate(MOTOREN.values()):
        GPIO.output(dir_pin, GPIO.HIGH)
        t = threading.Thread(target=spiele, args=(step, hz, i * VERZOEGERUNG, ende))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

except KeyboardInterrupt:
    print("\nBeendet.")

finally:
    GPIO.output(EN, GPIO.HIGH)
    GPIO.cleanup()