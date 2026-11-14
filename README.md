# Singing Motors

**Akustische Tonerzeugung mit Schrittmotoren: Aufbau und Inbetriebnahme eines vierstimmigen Instruments auf Basis eines Raspberry Pi 3B+**

---

## Abstract

*Singing Motors* ist ein kreatives Informatikprojekt aus der 13. Klasse auf dem Weg zum Abitur. Vier handelsübliche Hybrid-Schrittmotoren des Typs NEMA 17 werden zu einem mehrstimmigen Musikinstrument umfunktioniert. Ein Raspberry Pi 3B+ erzeugt für jeden Motor eine Folge von STEP-Impulsen, deren Frequenz die Grundfrequenz des abgestrahlten Tons bestimmt. Als Leistungsstufe dienen vier DRV8825-Treiber auf einem CNC-Shield, das ohne Arduino betrieben wird. Die Dokumentation beschreibt die theoretischen Grundlagen, den Versuchsaufbau, die Parametrierung der Strombegrenzung sowie die bei der Inbetriebnahme beobachteten Fehlerbilder und deren Ursachen.

---

## 1 Einleitung

Ein Schrittmotor führt bei jedem Steuerimpuls eine diskrete Winkeländerung aus. Bei hinreichend hoher Impulsrate regen die periodischen Drehmomentstöße Rotor und Gehäuse zu mechanischen Schwingungen im hörbaren Bereich an. Die Frequenz dieser Schwingung entspricht in erster Näherung der Impulsrate. Dieser Effekt wird hier gezielt genutzt: Statt Bewegung zu erzeugen, wird der Schrittmotor zum frequenzgesteuerten Tongenerator.

Das Projekt verbindet Softwareentwicklung (Python, präzise Zeitsteuerung, Notenberechnung) mit Hardwareansteuerung (GPIO, Motortreiber, Stromversorgung) und Musik. Im Vordergrund steht nicht eine Messreihe, sondern der kreative Bau eines funktionierenden Instruments.

**Zielsetzung:** Aufbau eines Instruments mit vier unabhängig angesteuerten Motoren, die jeweils eine Stimme wiedergeben und gemeinsam Musikstücke spielen.

---

## 2 Theoretische Grundlagen

### 2.1 Zusammenhang zwischen Schrittfrequenz und Tonhöhe

Im Vollschrittbetrieb benötigt der verwendete Motor 200 Schritte pro Umdrehung. Für eine Impulsfrequenz $f$ ergibt sich die Drehzahl

$$
n = \frac{f}{200}\ \text{s}^{-1}
$$

Für den Kammerton $A_4$ mit $f = 440\ \text{Hz}$ folgt $n = 2{,}2\ \text{s}^{-1} \approx 132\ \text{min}^{-1}$.

Die Sollfrequenz einer Note wird aus ihrer MIDI-Nummer $m$ in gleichstufiger Stimmung berechnet:

$$
f(m) = 440\ \text{Hz} \cdot 2^{\frac{m - 69}{12}}
$$

### 2.2 Strombegrenzung des DRV8825

Der DRV8825 regelt den Spulenstrom über einen Chopper. Der Maximalstrom hängt von der Referenzspannung $V_{\text{REF}}$ und dem Messwiderstand $R_{\text{sense}}$ ab:

$$
I_{\max} = \frac{V_{\text{REF}}}{5 \cdot R_{\text{sense}}}
$$

Mit $R_{\text{sense}} = 0{,}1\ \Omega$ vereinfacht sich dies zu

$$
I_{\max} = 2 \cdot V_{\text{REF}}
$$

### 2.3 Impulserzeugung

Ein Rechteckimpuls der Frequenz $f$ besteht aus zwei Flanken im Abstand der halben Periode

$$
t_{1/2} = \frac{1}{2f}
$$

Die Flanken werden auf einer festen Zeitachse geplant, damit sich Ausführungsverzögerungen nicht aufsummieren.

---

## 3 Material und Methoden

### 3.1 Verwendete Komponenten

| Komponente | Bezeichnung | Kenndaten |
|---|---|---|
| Steuerrechner | Raspberry Pi 3B+ | Raspberry Pi OS Lite, separate USB-Versorgung, Zugriff per SSH |
| Trägerplatine | Joy-IT ARD-CNC-Kit2 | 4 Steckplätze (X, Y, Z, A), Motorversorgung 12–35 V |
| Motortreiber | Joy-IT SBC-MD-DRV8825 | $R_{\text{sense}} = 0{,}1\ \Omega$ |
| Schrittmotor | StepperOnline 17HE15-1504S (NEMA 17) | $I_{\text{N}} = 1{,}5\ \text{A}$, 200 Schritte/U, $R_{\text{Spule}} \approx 2{,}9\text{–}3\ \Omega$ (gemessen) |
| Spannungsquelle | Labornetzteil, regelbar 0–30 V | $I_{\max} = 1\ \text{A}$ |
| Pufferkondensator | Elektrolytkondensator | Spannungsfestigkeit ≥ 25 V, parallel zur Versorgungsklemme |
| Messmittel | Digitalmultimeter | Gleichspannungsbereich 2 V |

### 3.2 Versuchsaufbau

```
Raspberry Pi 3B+  ──STEP/DIR/EN (3,3 V)──▶  CNC-Shield + 4× DRV8825  ──▶  4× NEMA 17
                                                       ▲
                                           Labornetzteil 12 V (VMOT)
```

Logik- und Leistungskreis sind getrennt versorgt und nur über die gemeinsame Masse verbunden. Die Logikspannung des Shields stellt der Raspberry Pi bereit, weil kein Arduino eingesetzt wird.

### 3.3 Signalzuordnung

**Tabelle 1:** Zuordnung der Steuersignale (physische Pin-Nummer / BCM-GPIO)

| Kanal | Steckplatz | STEP | DIR |
|---|---|---|---|
| 1 | A | Pin 38 / GPIO20 | Pin 40 / GPIO21 |
| 2 | Z | Pin 35 / GPIO19 | Pin 37 / GPIO26 |
| 3 | Y | Pin 31 / GPIO6 | Pin 33 / GPIO13 |
| 4 | X | Pin 21 / GPIO9 | Pin 23 / GPIO11 |

**Tabelle 2:** Gemeinsame Signale und Konfiguration

| Signal / Stelle | Belegung |
|---|---|
| Logikversorgung | Pin 17 (3,3 V) → 5V/GND, linker Pin |
| Masse | Pin 14 (GND) → 5V/GND, rechter Pin |
| Enable | Pin 7 (GPIO4) → EN, aktiv Low; alternativ Steckbrücke EN/GND |
| Schrittauflösung | M0, M1, M2 ohne Jumper → Vollschritt |

> **Hinweis:** Die 5-V-Pins des Raspberry Pi (Pin 2 und 4) dürfen nicht mit dem Shield verbunden werden, da die GPIO-Eingänge nur für 3,3 V ausgelegt sind.

### 3.4 Parametrierung der Strombegrenzung

Die Referenzspannung wurde auf

$$
V_{\text{REF}} = 0{,}487\ \text{V} \quad\Rightarrow\quad I_{\max} \approx 0{,}97\ \text{A}
$$

eingestellt. Das entspricht etwa 65 % des Nennstroms und begrenzt die Erwärmung von Motor und Treiber.

**Messverfahren:**
1. Motor im spannungslosen Zustand vom Treiber trennen.
2. Raspberry Pi einschalten, damit die Logikspannung den Treiber aus dem Sleep-Zustand holt. Anschließend die Motorversorgung auf 12 V zuschalten.
3. Multimeter im Gleichspannungsbereich: Bezugspotential an der Versorgungsklemme (−), Messspitze auf dem Metall des Trimmpotentiometers.
4. Potentiometer in kleinen Schritten verstellen (im Uhrzeigersinn steigt $V_{\text{REF}}$). Danach Versorgung abschalten und Motor wieder anschließen.

### 3.5 Inbetriebnahme

Die Komponenten werden in folgender Reihenfolge zugeschaltet:

1. Verkabelung im spannungslosen Zustand
2. Raspberry Pi (Logikversorgung)
3. Labornetzteil (Motorversorgung, 12 V)
4. Start der Steuersoftware

Im bestromten Zustand wurde am Netzteil eine Stromaufnahme von etwa 0,2–0,4 A beobachtet (gemessen mit einem Motor).

---

## 4 Beobachtungen und Fehleranalyse

**Tabelle 3:** Bei der Inbetriebnahme beobachtete Fehlerbilder

| Beobachtung | Ursache | Maßnahme |
|---|---|---|
| $V_{\text{REF}} = 0\ \text{V}$, Potentiometer ohne Wirkung | Ohne Arduino fehlt die Logikspannung, SLEEP und RESET bleiben Low | 3,3 V vom Raspberry Pi (Pin 17) an 5V/GND, linker Pin |
| Software aktiv, Motor steht, Stromaufnahme ≈ 0 A, Welle frei drehbar | EN nicht auf Masse gezogen, Steckbrücke auf E-STOP statt EN/GND | Steckbrücke auf EN/GND bzw. EN über Pin 7 ansteuern |
| Hochfrequentes Geräusch im Stillstand bei festgehaltener Welle | Reguläre Chopper-Stromregelung des DRV8825 | Treiber im Ruhezustand über EN abschalten, $V_{\text{REF}}$ senken oder Versorgung trennen |
| Netzteilanzeige 5,2 V statt Sollwert | Wahlschalter auf festem 5-V-Ausgang | Wahlschalter auf 0–30 V |
| Raspberry Pi nach dem Flashen nicht im Netzwerk | Ältere Imager-Versionen übertragen Einstellungen nicht wirksam (cloud-init) | Aktuellen Raspberry Pi Imager verwenden, Erstverbindung per LAN prüfen |

---

## 5 Diskussion

Die Impulserzeugung erfolgt per Software auf einem Linux-System ohne Echtzeitgarantie. Durch Prozesswechsel des Schedulers entsteht Jitter in den Flankenzeitpunkten, der sich als leicht unsaubere Tonhöhe bemerkbar macht. Mit steigender Stimmenzahl nimmt dieser Effekt zu, weil sich mehrere Impulsfolgen die Rechenzeit teilen.

Außerdem begrenzt das verwendete Labornetzteil mit $I_{\max} = 1\ \text{A}$ den gleichzeitigen Betrieb aller vier Motoren. Bei voller Bestückung ist eine Spannungsquelle mit mehreren Ampere Ausgangsstrom erforderlich.

---

## 6 Ausblick

- Hardwaregestützte Impulserzeugung, z. B. über DMA-Wellenformen (`pigpio`) oder einen Mikrocontroller als Taktgeber mit dem Raspberry Pi als übergeordneter Steuerung
- Einlesen und Aufteilen von MIDI-Dateien auf vier Stimmen (z. B. mit `mido`)
- Dimensionierung einer 12-V-Versorgung für den Vierkanalbetrieb

---

## 7 Sicherheitshinweise

- Motoren nur im spannungslosen Zustand an- oder abstecken. Ein Trennen unter Last kann den Treiber zerstören.
- Die 5-V-Pins des Raspberry Pi nie mit dem Shield verbinden.
- Polung des Elektrolytkondensators beachten (Markierungsstreifen = Minus).
- Strombegrenzungstests nur am regelbaren Ausgang des Netzteils durchführen.

---

## Quellen

- Texas Instruments: *DRV8825 Stepper Motor Controller IC*, Datenblatt
- Joy-IT: Bedienungsanleitungen ARD-CNC-Kit2 und SBC-MD-DRV8825
- StepperOnline: Datenblatt 17HE15-1504S
