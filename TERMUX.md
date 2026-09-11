# 📱 Suraj AI on Termux — Run entirely on your phone

Run Suraj AI **on the phone it controls**, with no computer needed.
Termux runs a Linux environment on Android; it can host the Python server
*and* run `adb` to control the same phone via **Wireless Debugging**.

> **Requirements:** Android 11 or newer. No root required.

---

## One-command setup

```bash
bash termux-setup.sh
```

This single script:

1. Installs `python`, `android-tools` (adb), and `git` via `pkg`
2. Installs Python dependencies (FastAPI, uvicorn, websockets, Pillow)
3. Walks you through pairing ADB with *this phone's* wireless debugging
4. Starts the server and opens `http://localhost:8000` in your browser

---

## Manual setup (if the script hits a snag)

### 1. Install Termux

Get **Termux** from [F-Droid](https://f-droid.org/packages/com.termux/) — the
Google Play version is outdated and broken. Do **not** use Play Store.

Open Termux and update:

```bash
pkg update -y && pkg upgrade -y
```

### 2. Install packages

```bash
pkg install -y python python-pip android-tools
```

`android-tools` provides the `adb` command — the bridge Suraj AI uses to
control your phone.

### 3. Install Python dependencies

```bash
pip install -r requirements-termux.txt
```

This installs a **minimal** set (no `uvloop`, no `openai`) that builds cleanly
on Android. The rule-based brain works fully offline without an LLM key.

### 4. Enable Wireless Debugging

This is the magic step that lets Termux control *this* phone:

1. **Settings → About phone** → tap **Build number** 7 times
2. **Settings → System → Developer options** → enable **USB debugging** *and*
   **Wireless debugging**
3. Inside **Wireless debugging**, tap **Pair device with pairing code**
4. Note the **IP:PORT** and **6-digit pairing code** shown on screen

### 5. Pair and connect ADB

```bash
# Pair (use the pair IP:PORT + code from the screen):
adb pair 192.168.1.5:43210
# → enter the 6-digit code when prompted

# Connect (use the IP:PORT from the MAIN wireless debugging screen,
# which is different from the pair port):
adb connect 192.168.1.5:37521

# Verify:
adb devices
# List of devices attached
# 192.168.1.5:37521    device     ← you want to see "device"
```

> **Tip:** Or just run `bash scripts/termux-adb-pair.sh` for a guided walk-through.

### 6. Start Suraj AI

```bash
SURAJ_NO_RELOAD=1 python3 server.py
```

`SURAJ_NO_RELOAD=1` disables file-watching reload, which is flaky on Android.

Open **http://localhost:8000** in your phone's browser (Chrome, Firefox, etc.).

---

## Using the dashboard on a phone screen

The dashboard is responsive but a phone screen is small. Two options:

| Option | How |
|--------|-----|
| **Same phone** | Open `http://localhost:8000` in the browser. The screen-mirror panel shows your phone's screen; tap it to tap the phone. |
| **Another device on WiFi** | Find your phone's IP (Settings → Network). Open `http://<phone-ip>:8000` from a laptop or tablet on the same WiFi. Bigger screen, easier control. |

---

## Useful flags

```bash
# Just (re)pair ADB, skip everything else:
bash termux-setup.sh --pair

# Just start the server (deps already installed):
bash termux-setup.sh --start

# Custom port:
PORT=9000 python3 server.py

# Password-protect (if sharing on WiFi):
SURAJ_PASSWORD=secret SURAJ_NO_RELOAD=1 python3 server.py
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `adb: command not found` | `pkg install android-tools` |
| `unauthorized` in `adb devices` | Unlock the phone, tap "Allow" on the debugging prompt, retry `adb connect` |
| `cannot connect to 127.0.0.1` | Wireless debugging binds to the WiFi IP, not localhost. Use your phone's WiFi IP (`192.168.x.x`), not `127.0.0.1` |
| Pair port vs connect port confused | The **pair** screen and the **main** Wireless debugging screen show *different* ports. Pair with one, connect with the other. |
| `pip install` fails on Pillow | `pkg install python-pillow` or `pip install --no-build-isolation pillow` |
| Server starts but can't control phone | Run `adb devices` — no `device` entry means ADB isn't connected. Re-pair. |
| Page won't load | Make sure nothing else is using port 8000, or use `PORT=9000 python3 server.py` |
| Reload errors in log | Always start with `SURAJ_NO_RELOAD=1` on Termux |

---

## How it works (the clever part)

```
┌─────────────── Your Android phone ───────────────┐
│                                                   │
│  Termux                          Wireless Debug     │
│  ┌─────────────────────┐         ┌──────────┐     │
│  │  Suraj AI (Python)  │         │ adb      │     │
│  │  + FastAPI server    │ ──ADB──→│ daemon   │     │
│  └──────────┬──────────┘         │ on phone │     │
│             │                    └────┬─────┘     │
│             │ localhost:8000          │ controls  │
│             ▼                         ▼           │
│  ┌──────────────────┐         ┌──────────────┐    │
│  │  Phone browser    │         │  Android UI  │    │
│  │  (dashboard)      │         │  (taps/type) │    │
│  └──────────────────┘         └──────────────┘    │
└─────────────────────────────────────────────────────┘
```

The phone controls *itself* — Termux's ADB connects to the phone's own
wireless-debugging daemon, so the server and the target are the same device.
This is fully self-contained: no USB cable, no computer, no root.
