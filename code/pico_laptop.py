#!/usr/bin/env python3
"""Raspberry Pi Pico: Weboberfläche läuft auf dem Laptop, die Töne schickt sie per USB an den Pico.

Aufruf:  python3 pico_laptop.py [/dev/ttyACM0]   -> http://127.0.0.1:8080 im Browser öffnen
Lib:     pip install mido pyserial
Auf dem Pico muss pico.py als main.py liegen (MicroPython).
MIDIs werden im Ordner midis/ neben diesem Skript gespeichert und bei jedem Abspielen neu von der Platte gelesen.
Bei jedem Start wird midis/ geleert und aus originale/ neu arrangiert (Uploads gehen dabei verloren).
Ohne Pico läuft nur die Anzeige.
"""
import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import serial
from serial.tools import list_ports

import arrangieren
from pi3_midi import SEITE, STIMMEN, noten_lesen, statistik, stimmen_bauen

HOST = "127.0.0.1"  # "0.0.0.0", damit z.B. das Handy im WLAN mitspielen kann (kein Login!)
PORT = 8080
ORDNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "midis")
ORIGINALE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "originale")
MAX_DATEIEN = 50
MAX_UPLOAD = 5_000_000
PICO_VID = 0x2E8A  # USB-Hersteller-ID des Raspberry Pi Pico

PICO = None
LOCK = threading.Lock()
S = {"thread": None, "stop": None, "t0": 0.0, "ende": 0.0, "name": "", "id": 0, "song": None}


def pico_oeffnen(name=None):
    if not name:
        name = next((p.device for p in list_ports.comports() if p.vid == PICO_VID), None)
    if not name:
        print("Kein Pico gefunden, nur Anzeige.")
        return None
    print("Pico an", name)
    return serial.Serial(name, 115200, timeout=1)


def schreiben(hz):
    if PICO:
        PICO.write((" ".join(f"{f:.2f}" for f in hz) + "\n").encode())


def ereignisse(stimmen):
    # (zeit, motor, hz); bei gleicher Zeit erst aus (0), dann an
    ev = []
    for k, name in enumerate(STIMMEN):
        for a, b, hp in stimmen[name]:
            ev.append((a, k, 0.5 / hp))
            ev.append((b, k, 0.0))
    return sorted(ev, key=lambda e: (e[0], e[2]))


def senden(ev, t0, stop):
    hz = [0.0] * 4
    i = 0
    try:
        while i < len(ev) and not stop.is_set():
            warte = t0 + ev[i][0] - time.perf_counter()
            if warte > 0:
                stop.wait(min(warte, 0.5))
                if warte > 0.5:
                    schreiben(hz)  # Lebenszeichen, sonst schaltet der Pico nach 1,5 s ab
                continue
            jetzt = time.perf_counter() - t0
            while i < len(ev) and ev[i][0] <= jetzt:
                hz[ev[i][1]] = ev[i][2]
                i += 1
            schreiben(hz)
    except serial.SerialException as e:
        print("Pico getrennt:", e)
    finally:
        try:
            schreiben([0.0] * 4)
        except serial.SerialException:
            pass


def stoppen():
    if S["thread"]:
        S["stop"].set()
        S["thread"].join(3)
    S["thread"] = None


def dateien():
    os.makedirs(ORDNER, exist_ok=True)
    return sorted(n for n in os.listdir(ORDNER) if n.lower().endswith((".mid", ".midi")))


def abspielen(name):
    name = os.path.basename(name)
    if name not in dateien():
        raise ValueError("Datei nicht gefunden")
    try:
        with open(os.path.join(ORDNER, name), "rb") as f:
            noten = noten_lesen(f.read())
    except Exception as e:  # kaputte/fremde Datei
        raise ValueError(f"MIDI nicht lesbar: {e}")
    if not noten:
        raise ValueError("Keine Noten in der Datei")
    stimmen = stimmen_bauen(noten)
    ende, maxpoly, mittel, toene = statistik(stimmen)
    song = {"v": [[[round(a, 3), round(b, 3), round(0.5 / hp, 2)] for a, b, hp in stimmen[n]] for n in STIMMEN],
            "maxpoly": maxpoly, "avgpoly": mittel, "toene": toene}
    with LOCK:
        stoppen()
        t0 = time.perf_counter() + 0.5
        stop = threading.Event()
        t = threading.Thread(target=senden, args=(ereignisse(stimmen), t0, stop), daemon=True)
        t.start()
        S.update(thread=t, stop=stop, t0=t0, ende=ende, name=name, id=S["id"] + 1, song=song)


def zustand():
    spielt = bool(S["thread"] and S["thread"].is_alive())
    t = time.perf_counter() - S["t0"] if spielt else S["ende"]
    return {"playing": spielt, "t": t, "ende": S["ende"], "name": S["name"], "id": S["id"]}


class Handler(BaseHTTPRequestHandler):
    def senden(self, code, daten, typ="application/json"):
        body = daten if isinstance(daten, bytes) else json.dumps(daten).encode()
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        pfad = urlparse(self.path).path
        if pfad == "/":
            self.senden(200, SEITE.replace("wird nicht gespeichert", "landet in code/midis").encode(), "text/html; charset=utf-8")
        elif pfad == "/api/state":
            self.senden(200, zustand())
        elif pfad == "/api/song":
            self.senden(200, S["song"] or {})
        elif pfad == "/api/files":
            self.senden(200, dateien())
        else:
            self.senden(404, {"error": "nicht gefunden"})

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/play":
                abspielen(q.get("file", [""])[0])
            elif u.path == "/api/stop":
                with LOCK:
                    stoppen()
            elif u.path == "/api/upload":
                name = os.path.basename(q.get("name", [""])[0])
                laenge = int(self.headers.get("Content-Length", 0))
                if not name.lower().endswith((".mid", ".midi")) or not 0 < laenge <= MAX_UPLOAD:
                    raise ValueError("Nur .mid/.midi bis 5 MB")
                if name not in dateien() and len(dateien()) >= MAX_DATEIEN:
                    raise ValueError("Zu viele Dateien im Ordner")
                daten = self.rfile.read(laenge)
                with open(os.path.join(ORDNER, name), "wb") as f:
                    f.write(daten)
            else:
                return self.senden(404, {"error": "nicht gefunden"})
            self.senden(200, {"ok": True})
        except (ValueError, OSError) as e:
            self.senden(400, {"error": str(e)})

    def log_message(self, *a):
        pass


def bibliothek_zuruecksetzen():
    # bei jedem Start: Uploads weg, Standardlieder frisch aus originale/ arrangieren
    shutil.rmtree(ORDNER, ignore_errors=True)
    arrangieren.main(sorted(os.path.join(ORIGINALE, n) for n in os.listdir(ORIGINALE)
                            if n.lower().endswith((".mid", ".midi"))))


def main():
    global PICO
    bibliothek_zuruecksetzen()
    PICO = pico_oeffnen(sys.argv[1] if len(sys.argv) > 1 else None)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"Singing Motors (Pico) läuft: http://127.0.0.1:{PORT}   (Strg+C beendet)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        with LOCK:
            stoppen()


if __name__ == "__main__":
    main()
