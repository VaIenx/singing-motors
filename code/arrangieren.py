#!/usr/bin/env python3
"""Schreibt beliebige MIDIs so um, dass sie auf die vier Motoren passen.

Aufruf:  python3 arrangieren.py original.mid [...]   -> landet in midis/ neben diesem Skript
Ergebnis: 4 Spuren (bass, quinte, terz, melodie), jede einstimmig, nie mehr als 4 Töne gleichzeitig,
jede Stimme per Oktavverschiebung in ihren Wohlklang-Bereich, keine Ton-/Oktavdopplungen zwischen den Stimmen.
"""
import os
import sys
from collections import defaultdict
from statistics import median

import mido

# Wohlklang-Bereich je Motor als MIDI-Noten (40 = E2 82 Hz, 84 = C6 1047 Hz)
BEREICH = {"bass": (40, 59), "quinte": (50, 69), "terz": (55, 74), "melodie": (62, 84)}
ORDNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "midis")


def noten_ticks(mid):
    # (start, ende, note) in Ticks, Schlagzeug (Kanal 9) raus
    noten, offen = [], {}
    for spur in mid.tracks:
        t = 0
        for m in spur:
            t += m.time
            if m.type not in ("note_on", "note_off") or m.channel == 9:
                continue
            k = (m.channel, m.note)
            if m.type == "note_on" and m.velocity > 0:
                offen.setdefault(k, t)
            elif k in offen:
                s = offen.pop(k)
                if t > s:
                    noten.append((s, t, m.note))
    return noten


def meta_spur(mid):
    # Tempo, Takt usw. aus allen Spuren in eine Dirigentenspur
    ev = []
    for spur in mid.tracks:
        t = 0
        for m in spur:
            t += m.time
            if m.is_meta and m.type in ("set_tempo", "time_signature", "key_signature"):
                ev.append((t, m))
    return ev


def falten(note, lo, hi):
    while note < lo:
        note += 12
    while note > hi:
        note -= 12
    return note


def oktav_versatz(noten, lo, hi):
    # ganze Stimme so verschieben, dass ihr Median mittig im Bereich liegt
    if not noten:
        return 0
    return round(((lo + hi) / 2 - median(n for _, _, n in noten)) / 12) * 12


def arrangieren(noten, tpb):
    toleranz = tpb // 32  # Akkordtöne, die fast gleichzeitig kommen, zählen als ein Anschlag
    gruppen = defaultdict(list)
    anker = None
    for s, e, n in sorted(noten):
        if anker is None or s - anker > toleranz:
            anker = s
        gruppen[anker].append((s, e, n))

    roh = {name: [] for name in BEREICH}  # (start, ende, note) je Stimme, Originallage
    for t in sorted(gruppen):
        g = sorted(gruppen[t], key=lambda x: x[2])
        # Einzelton: zu der Stimme, deren letzter Ton näher liegt (sonst wird die linke Hand zur Melodie)
        if len(g) == 1 and roh["melodie"] and roh["bass"]:
            n = g[0][2]
            if abs(n - roh["bass"][-1][2]) < abs(n - roh["melodie"][-1][2]):
                roh["bass"].append((t, g[0][1], n))
                continue
        mel = g.pop()
        roh["melodie"].append((t, mel[1], mel[2]))
        if g:
            b = g.pop(0)
            roh["bass"].append((t, b[1], b[2]))
        for name, n in zip(("terz", "quinte"), reversed(g)):
            roh[name].append((t, n[1], n[2]))

    # einstimmig machen: neuer Ton beendet den alten
    for name, ev in roh.items():
        for i in range(len(ev) - 1):
            if ev[i][1] > ev[i + 1][0]:
                ev[i] = (ev[i][0], ev[i + 1][0], ev[i][2])

    # Lage: erst ganze Stimme in Oktaven schieben, dann Ausreißer einzeln falten
    lage = {}
    for name, ev in roh.items():
        lo, hi = BEREICH[name]
        v = oktav_versatz(ev, lo, hi)
        lage[name] = [(s, e, falten(n + v, lo, hi)) for s, e, n in ev]

    # Dopplungen (gleiche Tonklasse) der Mittelstimmen entfernen, Vorrang: melodie/bass > terz > quinte.
    # Bass darf den Melodieton tragen (Grundton, liegt durch die Bereiche immer Oktaven tiefer).
    # Klingt die wichtigere Stimme schon beim Anschlag -> Ton weg, kommt sie später dazu -> Ton dort abschneiden.
    fertig = lage["melodie"] + lage["bass"]
    for name in ("terz", "quinte"):
        neu = []
        for s, e, n in lage[name]:
            for s2, e2, n2 in fertig:
                if n2 % 12 == n % 12 and s2 < e and s < e2:
                    e = s if s2 <= s else min(e, s2)
            if e > s:
                neu.append((s, e, n))
        lage[name] = neu
        fertig += neu

    min_len = tpb // 16  # zu kurze Schnipsel kann der Motor nicht sauber
    return {name: [x for x in ev if x[1] - x[0] >= min_len] for name, ev in lage.items()}


def schreiben(stimmen, meta, tpb, pfad):
    out = mido.MidiFile(type=1, ticks_per_beat=tpb)

    def spur(name, ev):
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("track_name", name=name, time=0))
        t = 0
        for at, m in sorted(ev, key=lambda x: (x[0], x[1].type != "note_off")):
            tr.append(m.copy(time=at - t))
            t = at
        out.tracks.append(tr)

    spur("conductor", meta)
    for k, (name, ev) in enumerate(stimmen.items()):
        msgs = [(0, mido.Message("program_change", channel=k, program=80))]
        for s, e, n in ev:
            msgs += [(s, mido.Message("note_on", channel=k, note=n, velocity=100)),
                     (e, mido.Message("note_off", channel=k, note=n, velocity=0))]
        spur(name, msgs)
    out.save(pfad)


def pruefen(stimmen):
    alle = [(s, e, n, name) for name, ev in stimmen.items() for s, e, n in ev]
    for name, ev in stimmen.items():
        lo, hi = BEREICH[name]
        assert all(lo <= n <= hi for _, _, n in ev), name
        assert all(ev[i][1] <= ev[i + 1][0] for i in range(len(ev) - 1)), name + " nicht einstimmig"
    for s, _, n, name in alle:
        gleich = [x for x in alle if x[3] != name and x[0] <= s < x[1] and x[2] % 12 == n % 12
                  and {name, x[3]} != {"bass", "melodie"}]
        assert not gleich, f"Dopplung {name} {n} bei {s}"


def main(pfade):
    os.makedirs(ORDNER, exist_ok=True)
    for pfad in pfade:
        mid = mido.MidiFile(pfad)
        stimmen = arrangieren(noten_ticks(mid), mid.ticks_per_beat)
        pruefen(stimmen)
        ziel = os.path.join(ORDNER, os.path.basename(pfad))
        schreiben(stimmen, meta_spur(mid), mid.ticks_per_beat, ziel)
        info = ", ".join(f"{n} {len(ev)} ({min((x[2] for x in ev), default='-')}-{max((x[2] for x in ev), default='-')})"
                         for n, ev in stimmen.items())
        print(f"{os.path.basename(pfad)}: {info}")


if __name__ == "__main__":
    main(sys.argv[1:])
