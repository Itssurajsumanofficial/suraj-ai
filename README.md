# 📱 Suraj AI

Suraj AI is an AI agent that controls your Android phone via ADB — chat or speak  
to it in natural language, watch a live screen mirror, and let it run multi-step  
tasks autonomously.

![Suraj AI](https://img.shields.io/badge/Suraj_AI-Android_Control-3ddc84)

![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)

![Vanilla JS](https://img.shields.io/badge/frontend-vanilla_JS-f7df1e)

---

## What it does

| Feature                | Example command                             |
| ---------------------- | ------------------------------------------- |
| **Open apps**          | "open whatsapp" / "launch spotify"          |
| **Send texts**         | "text +1234567890 I'll be 10 min late"      |
| **Make calls**         | "call +1234567890"                          |
| **Screenshots**        | "screenshot" / "take a screenshot"          |
| **Read notifications** | "show notifications"                        |
| **Navigation**         | "go home" / "go back" / "recent apps"       |
| **Volume**             | "volume up 3 times" / "mute"                |
| **Screen control**     | "swipe up" / "unlock phone" / "wake screen" |
| **Media**              | "play" / "pause" / "next song"              |
| **Open URLs**          | "navigate to github.com"                    |
| **Multi-step tasks**   | "send hello to mom on whatsapp"             |
| **System toggles**     | "turn off wifi and bluetooth"               |
| **Raw shell**          | "run dumpsys battery"                       |

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│  Browser (dashboard + chat + voice)              │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐ │
│  │ Phone     │  │ Chat &   │  │ Status + Plan │ │
│  │ Screen    │  │ Voice AI │  │ Viewer        │ │
│  └─────┬─────┘  └────┬─────┘  └───────────────┘ │
└────────│─────────────┼───────────────────────────┘
         │ WebSocket   │ REST
┌────────┴─────────────┴───────────────────────────┐
│  FastAPI Server                                     │
│  ┌──────────┐  ┌──────────────────────────────┐  │
│  │ REST API  │  │ Agent Brain                   │  │
│  │ (actions) │  │ ┌────────┐ ┌──────────────┐ │  │
│  └──────────┘  │ │ NLU    │ │ Multi-Step    │ │  │
│                │ │ Parser │ │ Planner      │ │  │
│                │ └────────┘ └──────┬───────┘ │  │
│                │                   │ Observe   │  │
│                │ ┌──────────────────┴───────┐ │  │
│                │ │ Executor (action runner)  │ │  │
│                │ └──────────────────────────┘ │  │
│                └──────────────────────────────┘  │
│                         │                           │
│                ┌────────┴───────────┐               │
│                │  ADB Bridge         │               │
│                │  (subprocess → adb) │               │
│                └─────────────────────┘               │
└─────────────────────────┼───────────────────────────┘
                          │ USB / Wi-Fi
                   ┌──────┴──────┐
                   │  📱 Phone    │
                   └─────────────┘
```

### The agent loop

For multi-step tasks, the brain runs a **plan → execute → observe** cycle:

1. **Plan** — decomposes the goal into ordered steps (open app, wait, type  
   text, screenshot, press send…)
2. **Execute** — runs each step via ADB
3. **Observe** — takes a screenshot after each step to "see" what happened
4. **Adapt** — continues or reports failure with context

Without an LLM key, the planner uses a curated set of recipes for common  
compound tasks. With `OPENAI_API_KEY` set, it can decompose arbitrary goals.

---

## Quick start

### 1. Install ADB (Android platform-tools)

| OS                   | Command                                                                    |
| -------------------- | -------------------------------------------------------------------------- |
| **Ubuntu/Debian**    | `sudo apt install adb`                                                     |
| **macOS (Homebrew)** | `brew install android-platform-tools`                                      |
| **Windows**          | Download from [developer.android.com](https://developer.android.com/tools) |
| **Arch**             | `sudo pacman -S android-tools`                                             |

### 2. Enable USB debugging on your phone

1. **Settings → About phone** → tap **Build number** 7 times
2. **Settings → System → Developer options** → enable **USB debugging**
3. Connect via USB (or set up **Wireless debugging** in Developer options)
4. Accept the **RSA key** prompt on your phone

### 3. Verify the connection

```bash
adb devices
# Should show something like:
# List of devices attached
# 192.168.1.50:5555    device
```

### 4. Run the server

```bash
cd phonecontrol
./run.sh
```

Open **<http://localhost:8000>** in your browser.

> **Wireless ADB:** In the dashboard, click **🔗 Connect** and enter  
> `IP:PORT` (e.g. `192.168.1.50:5555`). For first-time wireless pairing,  
> use `adb pair IP:PAIRING_PORT` in your terminal, then connect.

---

## Run on your phone with Termux (no computer needed)

Suraj AI can run **entirely on your Android phone** inside [Termux](https://f-droid.org/packages/com.termux/),
controlling the same phone via Wireless Debugging (Android 11+, no root).

```bash
# In Termux (from the project folder):
bash termux-setup.sh
```

This installs Python + ADB, pairs wireless debugging with *this* phone, and
starts the server. Then open `http://localhost:8000` in your phone's browser.

See **[TERMUX.md](TERMUX.md)** for the full guide and troubleshooting.

---

## Optional: LLM-powered understanding

The built-in rule-based engine handles the commands above with no API key.  
For free-form natural language ("find the cheapest flight and book it"),  
enable an LLM:

```bash
cp .env.example .env
# Edit .env:
#   OPENAI_API_KEY=sk-...
#   LLM_MODEL=gpt-4o-mini
```

Works with any **OpenAI-compatible** endpoint — OpenAI, Azure OpenAI,  
OpenRouter, or a local [Ollama](https://ollama.ai) server:

```bash
OPENAI_API_KEY=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3
```

---

## Deploying for multiple users

Suraj AI can be shared with **family, a team, or anyone** — pick the mode  
that fits your situation:

### Mode 1: Each person runs it locally (simplest)

Share the project folder with each person. They install ADB + Python on  
their own computer, run `./run.sh`, and control their own phone. **No  
network exposure, no password needed** — everything stays on their  
machine.

```bash
# Each person does:
cd phonecontrol
./run.sh
# → http://localhost:8000 on their own computer
```

### Mode 2: Shared server on your WiFi (team/household)

One computer runs Suraj AI. Everyone on the **same WiFi network** opens  
the dashboard in their browser and connects their own phone via wireless  
ADB. The server supports **multiple devices simultaneously** — each  
person selects their phone from the device dropdown.

```bash
# On the host machine:
SURAJ_PASSWORD=team-secret ./run.sh

# Each person opens:
#   http://<host-computer-ip>:8000
# enters the password, clicks Connect, and enters their phone's IP:port
```

**Each person's phone setup:**

1. Phone → Developer Options → Wireless Debugging → enable
2. Note the IP:port (e.g. `192.168.1.50:5555`)
3. First time only: run `adb pair IP:PAIRING_PORT` from any terminal
4. Enter that IP:port in the dashboard's Connect dialog

### Mode 3: Docker (one-command deploy)

Best for servers, NAS, or always-on deployments:

```bash
# Quick start:
echo "SURAJ_PASSWORD=your-secret" > .env
docker compose up -d

# → http://localhost:8000
```

For **USB-connected phones**, uncomment the `privileged` and USB volume  
lines in `docker-compose.yml`. For **wireless ADB phones**, uncomment  
`network_mode: host` so the container can reach devices on your WiFi.

| Docker command           | Purpose                    |
| ------------------------ | -------------------------- |
| `docker compose up -d`   | Start in background        |
| `docker compose logs -f` | Watch logs                 |
| `docker compose down`    | Stop                       |
| `docker compose build`   | Rebuild after code changes |

### Access control

Set `SURAJ_PASSWORD` in `.env` to require a password. Without it, anyone  
who can reach the server can control the connected phones — fine for  
personal use, **not recommended for shared deployments**.

```bash
# .env
SURAJ_PASSWORD=choose-a-strong-password
```

### Exposing to the internet (advanced)

To share Suraj AI with people outside your network, use a tunnel like  
**ngrok**, **Cloudflare Tunnel**, or **Tailscale**:

```bash
# Using ngrok (quick tunnel):
ngrok http 8000
# → share the https://xxxx.ngrok.io URL with people

# Using Tailscale (private mesh network):
# Install on the host + each user's device
# Share the Tailscale IP: http://100.x.x.x:8000
```

> ⚠️ **Always set `SURAJ_PASSWORD` when exposing to the internet.**

---

## How to use the dashboard

### Phone Screen panel (left)

- **Live Mirror** — streams screenshots in real-time
- **Snapshot** — grab a single screenshot
- **Click anywhere on the screen** — taps that spot on your phone
- **Drag** — swipes (click = tap, drag = swipe)
- Quick-action buttons: home, back, recents, wake, sleep, unlock, volume, mute, screenshot, notifications

### Chat panel (center)

- Type natural-language commands or goals
- 🎤 **Voice input** — press and speak (Web Speech API)
- 🔊 **Voice output** — AI replies are spoken aloud

### Info panel (right)

- Real-time device status (model, battery, screen state, resolution, WiFi)
- **Plan viewer** — shows each step of multi-step tasks with status icons (⏳ 🔄 ✅ ❌)

---

## API reference

| Method | Endpoint                      | Purpose                        |
| ------ | ----------------------------- | ------------------------------ |
| GET    | `/login`                      | Password login page            |
| POST   | `/api/login?password=`        | Authenticate                   |
| POST   | `/api/logout`                 | End session                    |
| GET    | `/api/devices`                | List connected devices         |
| POST   | `/api/connect/{host}`         | Connect wireless ADB           |
| POST   | `/api/select-device/{serial}` | Switch active device           |
| GET    | `/api/status`                 | Device info                    |
| GET    | `/api/screenshot`             | Screenshot PNG                 |
| POST   | `/api/tap?x=&y=`              | Tap coordinates                |
| POST   | `/api/swipe?x1=&y1=&x2=&y2=`  | Swipe                          |
| POST   | `/api/type?text=`             | Type text                      |
| POST   | `/api/keyevent/{key}`         | Press key                      |
| POST   | `/api/app/open?package=`      | Open app                       |
| GET    | `/api/apps`                   | List packages                  |
| GET    | `/api/notifications`          | Read notifications             |
| POST   | `/api/sms?number=&message=`   | Send SMS                       |
| POST   | `/api/call?number=`           | Make call                      |
| WS     | `/ws`                         | Chat + screen mirror + actions |

---

## Project structure

```
phonecontrol/
├── backend/
│   ├── main.py              # FastAPI app (REST + WebSocket + auth + multi-device)
│   ├── adb/
│   │   └── client.py        # ADB wrapper (subprocess → adb)
│   └── agent/
│       └── brain.py         # NLU + planner + executor
├── frontend/
│   ├── index.html           # Dashboard UI
│   ├── login.html           # Password login page
│   ├── styles.css           # Dark theme
│   └── app.js               # WS client + voice + device selector
├── Dockerfile               # Container image
├── docker-compose.yml       # One-command deploy
├── requirements.txt
├── requirements-termux.txt  # Minimal deps for Termux/Android
├── .env.example
├── server.py                # Root launcher (handles paths)
├── run.sh                   # Quick-start script (desktop)
├── termux-setup.sh          # Full setup for Termux on Android
├── scripts/
│   └── termux-adb-pair.sh   # Guided wireless-debugging pairing
├── TERMUX.md                # Run-on-phone guide
└── README.md
```

---

## Safety & privacy

- All communication stays **on your local network** — no cloud relay.
- ADB commands run locally via subprocess — nothing leaves your machine.
- If you enable an LLM, only your text commands are sent to that API (not  
  screenshots unless you explicitly code that in).
- The agent **never auto-confirms** destructive actions — it reports and  
  waits for you to verify.

---

## Troubleshooting

| Problem                | Fix                                                                 |
| ---------------------- | ------------------------------------------------------------------- |
| `unauthorized`         | Revoke USB debugging authorizations in Developer options, reconnect |
| `device offline`       | `adb kill-server && adb start-server`, then reconnect               |
| No devices             | Check cable, toggle USB debugging, accept RSA prompt                |
| Screenshot blank       | Some apps block screenshots (DRM/secure flag) — that's by design    |
| Can't connect wireless | Pair first: `adb pair IP:PAIRING_PORT`, then `adb connect IP:5555`  |

---

## License

MIT — build on it, break it, make it yours.
