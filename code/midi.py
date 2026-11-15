#!/usr/bin/env python3
"""Vierstimmiger Schrittmotor-Player mit Weboberfläche.

Aufruf:  python3 midi.py     -> druckt die Adresse, im Browser MIDIs hochladen (nur im RAM) und abspielen
Lib:     pip install mido
Ohne RPi.GPIO (z.B. am PC) läuft nur die Anzeige, ohne Motoren.
Hinweis: Die Seite hat keinen Login, nur im vertrauten Netz benutzen.
"""
import io
import json
import multiprocessing
import os
import signal
import socket
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import mido

try:
    import RPi.GPIO as GPIO
except ImportError:
    class GPIO:  # Attrappe zum Testen ohne Pi
        LOW = HIGH = OUT = BCM = 0
        setwarnings = setmode = setup = output = cleanup = staticmethod(lambda *a, **k: None)

PINS = {"bass": 9, "quinte": 6, "melodie": 19, "terz": 20}  # STEP
DIRS = [11, 13, 26, 21]                                      # DIR
EN = 4

# Motoren von tief nach hoch
STIMMEN = ["bass", "quinte", "terz", "melodie"]

TRANSPOSE = 0      # Halbtöne verschieben, z.B. -12 = eine Oktave tiefer
MIN_HZ = 40.0      # Töne außerhalb dieses Bereichs werden weggelassen
MAX_HZ = 1500.0
PORT = 8080
DATEIEN = {}  # hochgeladene MIDIs nur im Arbeitsspeicher, nichts landet auf der SD-Karte
MAX_DATEIEN = 20
MAX_UPLOAD = 5_000_000


def frequenz(note):
    return 440.0 * 2 ** ((note + TRANSPOSE - 69) / 12)


def noten_lesen(daten):
    # Liste mit (startzeit, endzeit, midi-note) in Sekunden
    mid = mido.MidiFile(file=io.BytesIO(daten))
    zeit = 0.0
    offen = {}
    noten = []
    for msg in mid:  # msg.time ist die Zeit seit der letzten Nachricht in Sekunden
        zeit = zeit + msg.time
        if msg.type not in ("note_on", "note_off"):
            continue
        if msg.channel == 9:  # Schlagzeug-Kanal überspringen
            continue
        schluessel = (msg.channel, msg.note)
        if msg.type == "note_on" and msg.velocity > 0:
            offen[schluessel] = zeit
        elif schluessel in offen:
            noten.append((offen[schluessel], zeit, msg.note))
            del offen[schluessel]
    return noten


def stimmen_bauen(noten):
    # Alle Zeitpunkte, an denen sich etwas ändert
    grenzen = []
    for start, ende, note in noten:
        grenzen.append(start)
        grenzen.append(ende)
    grenzen = sorted(set(grenzen))

    stimmen = {}
    for name in STIMMEN:
        stimmen[name] = []

    # Zwischen zwei Grenzen klingt immer dasselbe
    for i in range(len(grenzen) - 1):
        t0 = grenzen[i]
        t1 = grenzen[i + 1]
        mitte = (t0 + t1) / 2

        tonhoehen = []
        for start, ende, note in noten:
            if start <= mitte < ende:
                hz = frequenz(note)
                if MIN_HZ <= hz <= MAX_HZ:
                    tonhoehen.append(hz)
        tonhoehen.sort()
        tonhoehen = tonhoehen[-4:]  # bei mehr als 4 Tönen die höchsten nehmen

        k = len(tonhoehen)
        for j in range(k):
            name = STIMMEN[4 - k + j]  # tiefster Ton -> tiefster frei Motor
            stimmen[name].append((t0, t1, 0.5 / tonhoehen[j]))
    return stimmen


def spiele(stimmen, t0):
    # stimmen = [(pin, [(start, stop, halbe_periode), ...]), ...]
    n = len(stimmen)
    pins = []
    evs = []
    for pin, ereignisse in stimmen:
        pins.append(pin)
        evs.append(ereignisse)

    ende = 0.0
    for ereignisse in evs:
        if ereignisse and ereignisse[-1][1] > ende:
            ende = ereignisse[-1][1]

    idx = [0] * n
    naechste = [0.0] * n
    aktiv = [False] * n
    pegel = [False] * n

    while True:
        jetzt = time.perf_counter() - t0
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
                GPIO.output(pins[k], pegel[k])
                naechste[k] = naechste[k] + hp
                if naechste[k] < jetzt:
                    naechste[k] = jetzt + hp

    for pin in pins:
        GPIO.output(pin, GPIO.LOW)

def statistik(stimmen):
    # Jeder Abschnitt hat bei allen aktiven Stimmen dieselbe Startzeit -> Polyphonie = Anzahl gleicher Starts
    anz = Counter(e[0] for n in STIMMEN for e in stimmen[n])
    abschnitte = [(t1 - t0, anz[t0]) for t0, t1, _ in stimmen[STIMMEN[-1]]]
    ende = max((ev[-1][1] for ev in stimmen.values() if ev), default=0.0)
    mittel = sum(d * p for d, p in abschnitte) / ende if ende else 0
    return ende, max((p for _, p in abschnitte), default=0), mittel, sum(len(stimmen[n]) for n in STIMMEN)


def motoren(liste, t0):
    # läuft als eigener Prozess, damit der Webserver das Timing nicht stört
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # Stop -> KeyboardInterrupt -> finally
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for pin, _ in liste:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    for d in DIRS:
        GPIO.setup(d, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(EN, GPIO.OUT, initial=GPIO.HIGH)
    try:
        GPIO.output(EN, GPIO.LOW)  # Treiber an
        spiele(liste, t0)
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.output(EN, GPIO.HIGH)
        GPIO.cleanup()


LOCK = threading.Lock()
S = {"proc": None, "t0": 0.0, "ende": 0.0, "name": "", "id": 0, "song": None}
CTX = multiprocessing.get_context("spawn")  # kein fork aus einem Prozess mit Threads


def stoppen():
    p = S["proc"]
    if p and p.is_alive():
        p.terminate()
        p.join(3)
        if p.is_alive():
            p.kill()
    S["proc"] = None


def abspielen(name):
    if name not in DATEIEN:
        raise ValueError("Datei nicht gefunden")
    try:
        noten = noten_lesen(DATEIEN[name])
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
        t0 = time.perf_counter() + 1.5  # Vorlauf: Startzeit des Prozesses
        p = CTX.Process(target=motoren, args=([(PINS[n], stimmen[n]) for n in STIMMEN], t0))
        p.start()
        S.update(proc=p, t0=t0, ende=ende, name=name, id=S["id"] + 1, song=song)


def zustand():
    p = S["proc"]
    spielt = bool(p and p.is_alive())
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
            self.senden(200, SEITE.encode(), "text/html; charset=utf-8")
        elif pfad == "/api/state":
            self.senden(200, zustand())
        elif pfad == "/api/song":
            self.senden(200, S["song"] or {})
        elif pfad == "/api/files":
            self.senden(200, sorted(DATEIEN))
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
                if name not in DATEIEN and len(DATEIEN) >= MAX_DATEIEN:
                    raise ValueError("Zu viele Dateien im Speicher")
                DATEIEN[name] = self.rfile.read(laenge)
            else:
                return self.senden(404, {"error": "nicht gefunden"})
            self.senden(200, {"ok": True})
        except (ValueError, OSError) as e:
            self.senden(400, {"error": str(e)})

    def log_message(self, *a):
        pass


def lokale_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # nicht erreichbar, es wird nichts gesendet; liefert nur die LAN-IP
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"Singing Motors läuft: http://{lokale_ip()}:{PORT}   (Strg+C beendet)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        with LOCK:
            stoppen()


SEITE = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Singing Motors</title>
<style>
:root{--bg:#060101;--dk:#3a0d08;--rd:#c8321c;--or:#ff8c1a;--ye:#ffd24a;--wh:#ffeccc}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--or);font:15px/1.4 "Courier New",monospace;min-height:100vh;display:flex;flex-direction:column;padding:10px 10px 52px;gap:10px}
body::after{content:"";position:fixed;inset:0;pointer-events:none;background:repeating-linear-gradient(rgba(0,0,0,.28) 0 1px,transparent 1px 3px)}
header{display:flex;flex-wrap:wrap;gap:8px 24px;justify-content:space-between;align-items:baseline;border-bottom:2px double var(--rd);padding-bottom:6px}
h1{margin:0;font-size:22px;letter-spacing:3px;text-shadow:0 0 8px var(--or)}
h2{margin:0 0 8px;font-size:13px;letter-spacing:2px;color:var(--ye)}
.panel{border:1px solid var(--rd);padding:10px 12px;box-shadow:0 0 10px #c8321c44 inset;position:relative}
main{display:grid;grid-template-columns:230px 1fr 340px;gap:10px;flex:1;min-height:420px}
dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:2px 10px}
dt{color:var(--rd)}dd{margin:0;text-align:right}
canvas{position:absolute;inset:34px 8px 8px 8px;width:calc(100% - 16px);height:calc(100% - 42px)}
#mid canvas{inset:8px;width:calc(100% - 16px);height:calc(100% - 16px)}
ul{list-style:none;margin:0 0 10px;padding:0;max-height:180px;overflow:auto}
li{display:flex;justify-content:space-between;align-items:center;padding:3px 6px;border-bottom:1px dashed var(--dk)}
li.on{color:var(--ye);background:var(--dk)}
button,.btn{font:inherit;color:var(--or);background:var(--bg);border:1px solid var(--or);padding:3px 12px;cursor:pointer;letter-spacing:1px}
button:hover,.btn:hover{background:var(--or);color:var(--bg)}
#ctl{display:flex;flex-direction:column}
#bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
#msg{color:var(--ye);min-height:1.4em}
footer{position:fixed;left:10px;right:10px;bottom:10px;display:flex;gap:10px;align-items:center;background:var(--bg)}
#prog{flex:1;height:22px;border:1px solid var(--rd);background:repeating-linear-gradient(90deg,var(--dk) 0 6px,transparent 6px 8px)}
#fill{height:100%;width:0;background:repeating-linear-gradient(90deg,var(--or) 0 6px,transparent 6px 8px);box-shadow:0 0 10px var(--or)}
.blink{animation:b 1s steps(2) infinite}@keyframes b{50%{opacity:.2}}
@media(max-width:900px){main{grid-template-columns:1fr}.panel canvas{position:static;display:block;width:100%;height:320px}#mid canvas{height:380px}}
</style></head><body>
<header><h1>◢◤ SINGING MOTORS ◥◣</h1><span id="stat">○ BEREIT</span><span id="clock">00:00 / 00:00</span></header>
<main>
 <section class="panel"><h2>STATUS</h2>
  <dl><dt>DATEI</dt><dd id="f">-</dd><dt>STIMMEN</dt><dd id="poly">0/4</dd><dt>MAX POLY</dt><dd id="mp">-</dd><dt>AVG POLY</dt><dd id="ap">-</dd><dt>TÖNE</dt><dd id="nt">-</dd><dt>REST</dt><dd id="rest">-</dd></dl>
  <h2 style="margin-top:16px">DREHZAHL</h2>
  <dl><dt>M1</dt><dd id="r0"></dd><dt>M2</dt><dd id="r1"></dd><dt>M3</dt><dd id="r2"></dd><dt>M4</dt><dd id="r3"></dd></dl>
 </section>
 <section class="panel" id="mid"><canvas id="bars"></canvas></section>
 <section class="panel"><h2>PIANO ROLL</h2><canvas id="roll"></canvas></section>
</main>
<section class="panel" id="ctl"><h2>BIBLIOTHEK</h2><ul id="files"></ul>
 <div id="bar"><label class="btn">⇪ MIDI HOCHLADEN<input type="file" id="up" accept=".mid,.midi" hidden></label><button id="stop">■ STOP</button><span id="msg"></span></div>
</section>
<footer><div id="prog"><div id="fill"></div></div><b id="pct">0%</b></footer>
<script>
const NAMEN=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"],MIN=40,MAX=1500,C=["#c8321c","#ff8c1a","#ffd24a"];
const $=i=>document.getElementById(i);
let song=null,sid=-1,st={playing:false,t:0,ende:0,name:"",id:0},recv=0;
const lvl=[0,0,0,0],peak=[0,0,0,0];
const lg=h=>Math.max(0,Math.min(1,Math.log(h/MIN)/Math.log(MAX/MIN)));
const mmss=s=>{s=Math.max(0,Math.floor(s));return String(s/60|0).padStart(2,"0")+":"+String(s%60).padStart(2,"0")};
const nn=h=>{const m=Math.round(69+12*Math.log2(h/440));return NAMEN[((m%12)+12)%12]+(Math.floor(m/12)-1)};
function hzAt(v,t){const e=song.v[v];let a=0,b=e.length;while(a<b){const m=(a+b)>>1;e[m][0]<=t?a=m+1:b=m}const x=e[a-1];return x&&t<x[1]?x[2]:0}
const now=()=>Math.min(st.ende,st.playing?st.t+(performance.now()-recv)/1000:st.t);
async function poll(){try{const s=await(await fetch("/api/state")).json();
 if(s.id!==sid){sid=s.id;song=s.id?await(await fetch("/api/song")).json():null}
 st=s;recv=performance.now()}catch(e){}setTimeout(poll,250)}
function msg(t){$("msg").textContent=t;setTimeout(()=>{if($("msg").textContent===t)$("msg").textContent=""},5000)}
async function api(u,o){const r=await fetch(u,o);const j=await r.json().catch(()=>({}));if(!r.ok)msg("FEHLER: "+(j.error||r.status));return r.ok}
async function files(){try{const f=await(await fetch("/api/files")).json();const ul=$("files");ul.innerHTML="";
 f.forEach(n=>{const li=document.createElement("li");li.className=n===st.name&&st.playing?"on":"";li.append(n);
  const b=document.createElement("button");b.textContent="▶ PLAY";b.onclick=async()=>{await api("/api/play?file="+encodeURIComponent(n),{method:"POST"});poll()};li.append(b);ul.append(li)});
 if(!f.length)ul.innerHTML="<li>(leer – MIDI hochladen, wird nicht gespeichert)</li>"}catch(e){}}
$("stop").onclick=()=>api("/api/stop",{method:"POST"});
$("up").onchange=async e=>{const f=e.target.files[0];if(!f)return;
 if(await api("/api/upload?name="+encodeURIComponent(f.name),{method:"POST",body:f}))msg(f.name+" hochgeladen");e.target.value="";files()};
function fit(c){const d=devicePixelRatio||1,w=c.clientWidth*d|0,h=c.clientHeight*d|0;if(c.width!==w||c.height!==h){c.width=w;c.height=h}return[c.getContext("2d"),w,h,d]}
let last=performance.now();
function frame(ts){const dt=Math.min(.1,(ts-last)/1000);last=ts;const t=now(),real=st.playing?st.t+(performance.now()-recv)/1000:st.t;
 const hz=[0,0,0,0];if(song&&st.playing&&real>=0)for(let k=0;k<4;k++)hz[k]=hzAt(k,real);
 // Balken
 {const[x,w,h,d]=fit($("bars")),g=x;g.clearRect(0,0,w,h);const lab=86*d,top=10*d,H=h-lab-top,N=28,sh=H/N,col=w/4,bw=col*.7;
  for(let k=0;k<4;k++){const tg=hz[k]?Math.max(.03,lg(hz[k])):0;lvl[k]=Math.max(tg,lvl[k]-dt*1.2);peak[k]=Math.max(tg,peak[k]-dt*.12);
   const x0=k*col+(col-bw)/2,on=Math.round(lvl[k]*N),pk=Math.round(peak[k]*N);
   for(let i=0;i<N;i++){const y=top+H-(i+1)*sh;g.fillStyle=i<on?C[Math.min(2,3*i/N|0)]:"#2a0a06";g.fillRect(x0,y+1,bw,sh-2*d)}
   if(pk>0){g.fillStyle="#ffeccc";g.fillRect(x0,top+H-pk*sh,bw,Math.max(2,3*d))}
   g.textAlign="center";const cx=x0+bw/2;
   g.fillStyle=hz[k]?"#ffeccc":"#5a1a10";g.font=`bold ${20*d}px Courier New`;g.fillText(hz[k]?nn(hz[k]):"--",cx,h-56*d);
   g.fillStyle=hz[k]?"#ff8c1a":"#5a1a10";g.font=`${14*d}px Courier New`;g.fillText(hz[k]?hz[k].toFixed(1)+" Hz":"--- Hz",cx,h-34*d);
   g.fillStyle=hz[k]?"#ffd24a":"#c8321c";g.fillText("M"+(k+1),cx,h-12*d);
   $("r"+k).textContent=hz[k]?Math.round(hz[k]*.3)+" rpm":"-- rpm"}}
 // Piano Roll: 16 s Fenster, Kopf bei 25 %
 {const[g,w,h,d]=fit($("roll"));g.clearRect(0,0,w,h);const F=16,ph=w*.25,pps=w/F,ln=h/4;
  for(let k=0;k<4;k++){const y0=k*ln;g.fillStyle="#1a0503";g.fillRect(0,y0+2,w,ln-4);
   g.fillStyle="#c8321c";g.font=`${12*d}px Courier New`;g.fillText("M"+(k+1),4*d,y0+14*d);
   if(!song)continue;const e=song.v[k];let a=0,b=e.length;const lo=real-F*.25;while(a<b){const m=(a+b)>>1;e[m][1]<lo?a=m+1:b=m}
   for(;a<e.length&&e[a][0]<real+F*.75;a++){const[s,en,hh]=e[a],x=ph+(s-real)*pps,y=y0+4+(1-lg(hh))*(ln-14*d);
    g.fillStyle=s<=real&&real<en?"#ffd24a":s>real?"#ff8c1a":"#a02a14";g.fillRect(x,y,Math.max(2,(en-s)*pps-1),5*d)}}
  g.fillStyle="#ffd24a";g.fillRect(ph,0,2*d,h)}
 // Texte
 const idle=!st.name;$("stat").textContent=st.playing?"● SPIELT":idle?"○ BEREIT":"■ FERTIG";$("stat").className=st.playing?"blink":"";
 $("clock").textContent=mmss(t)+" / "+mmss(st.ende);$("f").textContent=st.name||"-";
 $("poly").textContent=hz.filter(Boolean).length+"/4";$("rest").textContent=idle?"-":mmss(st.ende-t);
 if(song){$("mp").textContent=song.maxpoly;$("ap").textContent=song.avgpoly.toFixed(1);$("nt").textContent=song.toene}else{$("mp").textContent=$("ap").textContent=$("nt").textContent="-"}
 const p=st.ende?100*t/st.ende:0;$("fill").style.width=p+"%";$("pct").textContent=Math.round(p)+"%";
 requestAnimationFrame(frame)}
poll();files();setInterval(files,3000);requestAnimationFrame(frame);
</script></body></html>
"""

if __name__ == "__main__":
    main()
