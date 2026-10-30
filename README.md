# Shackboard

Shackboard is the Windows desktop app for a Yaesu **FTDX-1200** station: the radio, the tuner, spots, propagation, and the log. Flask UI in a pywebview window, `pyserial` on the CAT COM port.

## Radio setup

On the FTDX-1200:

- Menu **039 CAT RATE** — match the app (default **4800**; 38400 is fine if you set it on both)
- Menu **040 CAT TOT** — 10 ms is fine
- Menu **041 CAT RTS** — Enabled (the app holds RTS asserted)

Use the radio’s **CAT** RS-232 jack or a USB serial adapter.

## Run

```bat
run.bat
```

Or:

```bat
py -3.12 -m pip install -r requirements.txt
py -3.12 -m ftdx1200 --window
```

The UI is at `http://127.0.0.1:8770/` if you use `--browser`.

**Demo mode** runs a fake radio so you can try the UI without hardware.

**Setup** is hidden at startup (serial port row). **Log** is hidden until you turn it on (then remembered).

## Windows .exe

```bat
build.bat
```

That writes `dist\Shackboard.exe`. Edge WebView2 is required (already on Windows 11).

## Controls

- VFO-A frequency (digits, 1 kHz dial, or step buttons)
- Mode and band
- RF power and radio AF gain (synced from the rig)
- S-meter from a dedicated `SM0` poll

PTT, VFO A/B, tuner, and PC RX-audio monitor are in the code but hidden in the UI. CAT PTT is `TX1` / `TX0` if you turn those buttons back on. Keep power and antenna safe before you transmit.
