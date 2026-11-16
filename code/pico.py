# Raspberry Pi Pico (MicroPython): als main.py auf den Pico kopieren.
# Empfängt über USB pro Zeile vier Frequenzen in Hz ("bass quinte terz melodie", 0 = still)
# und erzeugt die STEP-Impulse per Hardware-PWM, also ohne Jitter.
# Kommt 1,5 s keine Zeile (Laptop weg), wird alles abgeschaltet.
import select
import sys

from machine import PWM, Pin

STEP = [2, 4, 8, 6]   # GP2 X (bass), GP4 Y (quinte), GP8 A (terz), GP6 Z (melodie); je eigener PWM-Slice
DIRS = [3, 5, 9, 7]
EN = Pin(10, Pin.OUT, value=1)  # aktiv Low
MIN_HZ = 8  # darunter kann die PWM nicht

for d in DIRS:
    Pin(d, Pin.OUT, value=1)
pwm = [PWM(Pin(p)) for p in STEP]


def setze(hz):
    an = False
    for p, f in zip(pwm, hz):
        if f >= MIN_HZ:
            p.freq(round(f))  # shortcut: ganze Hz, bei tiefen Tönen bis ~20 Cent daneben; sonst DIV/TOP-Register selbst setzen
            p.duty_u16(32768)
            an = True
        else:
            p.duty_u16(0)
    EN.value(0 if an else 1)  # in Pausen Treiber aus, kein Chopper-Pfeifen


setze([0, 0, 0, 0])
eingang = select.poll()
eingang.register(sys.stdin, select.POLLIN)
while True:
    if not eingang.poll(1500):
        setze([0, 0, 0, 0])
        continue
    try:
        werte = [float(x) for x in sys.stdin.readline().split()]
    except ValueError:
        werte = []
    setze((werte + [0, 0, 0, 0])[:4])
