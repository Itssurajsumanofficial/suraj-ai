"""
Agent Brain — natural-language understanding + multi-step task execution.

The brain has three layers:

1. **NLU (intent + slot parser)**  — maps free-text like
   "text mom i'm running late" into a structured Action.

2. **Action library** — high-level operations (send_sms, open_app,
   tap, type_text, ...) that translate to ADB calls.

3. **Planner / execution loop** — for compound goals the brain
   decomposes them into steps, runs each, takes a screenshot
   between steps (the "observe" phase) and keeps going until the
   goal is met or it runs out of steps.

The rule-based NLU needs no API key and works offline.  If the user
sets ``OPENAI_API_KEY`` (or any OpenAI-compatible endpoint) the brain
will additionally use an LLM for free-form understanding and screen
analysis, falling back to rules when the API is unreachable.

Author: WorkBuddy
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..adb.client import ADBClient, ADBError

log = logging.getLogger("suraj.agent")


# ===================================================================== #
#  Data structures
# ===================================================================== #
class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Action:
    """A single concrete operation the phone should perform."""
    name: str                      # e.g. "send_sms"
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params": self.params,
            "description": self.description,
        }


@dataclass
class Step:
    """One step in a multi-step plan."""
    action: Action
    status: StepStatus = StepStatus.PENDING
    result: str = ""
    screenshot: str | None = None   # base64 PNG (taken after the step)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "status": self.status.value,
            "result": self.result,
            "screenshot": self.screenshot is not None,
            "error": self.error,
        }


@dataclass
class Plan:
    """A full execution plan for a user goal."""
    goal: str
    steps: list[Step] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "status": self.status.value,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
        }


# ===================================================================== #
#  NLU — rule-based intent + slot parser
# ===================================================================== #

# Map of common app names → package names.  Users can extend this.
APP_PACKAGES: dict[str, str] = {
    "whatsapp": "com.whatsapp",
    "youtube": "com.google.android.youtube",
    "spotify": "com.spotify.music",
    "instagram": "com.instagram.android",
    "facebook": "com.facebook.katana",
    "twitter": "com.twitter.android",
    "x": "com.twitter.android",
    "telegram": "org.telegram.messenger",
    "snapchat": "com.snapchat.android",
    "tiktok": "com.zhiliaoapp.musically",
    "gmail": "com.google.android.gm",
    "maps": "com.google.android.apps.maps",
    "google maps": "com.google.android.apps.maps",
    "chrome": "com.android.chrome",
    "settings": "com.android.settings",
    "camera": "com.android.camera",
    "phone": "com.android.dialer",
    "dialer": "com.android.dialer",
    "calculator": "com.android.calculator2",
    "calendar": "com.google.android.calendar",
    "clock": "com.google.android.deskclock",
    "files": "com.google.android.documentsui",
    "play store": "com.android.vending",
    "netflix": "com.netflix.mediaclient",
    "amazon": "com.amazon.mShop.android.shopping",
    "discord": "com.discord",
    "slack": "com.slack",
    "zoom": "us.zoom.videomeetings",
    "uber": "com.ubercab",
    "lyft": "com.lyft.android.driver",
}


class NLU:
    """Rule-based natural-language understanding for phone commands."""

    # intent → list of (regex, param-builder)
    @staticmethod
    def parse(text: str) -> Action:
        t = text.strip().lower()
        original = text.strip()

        # ---------------------------------------------------------- #
        #  communication
        # ---------------------------------------------------------- #
        # send sms / text message
        m = re.search(
            r"(?:send\s+(?:a\s+)?(?:text|sms|message)|text)\s+"
            r"([\w\s]+?)\s+(?:that\s+|saying\s+|to\s+say\s+)?[\"']?(.+?)[\"']?$",
            t,
        )
        if m:
            return Action(
                "send_sms",
                {"recipient": m.group(1).strip(), "message": m.group(2).strip()},
                f"Send SMS to {m.group(1).strip()}: \"{m.group(2).strip()}\"",
            )

        # "text john hello"  (shorter form)
        m = re.match(r"^text\s+(\S+)\s+(.+)$", t)
        if m:
            return Action(
                "send_sms",
                {"recipient": m.group(1), "message": m.group(2).strip()},
                f"Send SMS to {m.group(1)}",
            )

        # call someone
        m = re.search(r"call\s+(\+?[\d\s\-\(\)]{4,})", t)
        if m:
            number = re.sub(r"\s+", "", m.group(1))
            return Action("make_call", {"number": number}, f"Call {number}")

        m = re.search(r"call\s+(\w+)", t)
        if m and m.group(1) not in ("back",):
            return Action("dial", {"contact": m.group(1)}, f"Dial {m.group(1)}")

        # ---------------------------------------------------------- #
        #  app control
        # ---------------------------------------------------------- #
        m = re.search(r"(?:open|launch|start|fire up|bring up)\s+(.+)", t)
        if m and not any(k in t for k in ("send", "text", "call", "url", "http")):
            app_query = m.group(1).strip()
            # If the query contains " and " it's a compound goal — let the
            # planner decompose it (e.g. "open youtube and search for cats").
            if " and " in app_query:
                return Action("goal", {"text": original}, f"Goal: {original}")
            pkg = NLU._resolve_app(app_query)
            if pkg:
                return Action("open_app", {"package": pkg}, f"Open {app_query}")
            # if no known package, try opening by package name directly
            if "." in app_query:
                return Action("open_app", {"package": app_query}, f"Open {app_query}")
            return Action("open_app", {"package": app_query, "guess": True},
                          f"Try to open {app_query}")

        # close / kill app
        m = re.search(r"(?:close|kill|stop|quit)\s+(.+)", t)
        if m:
            app_query = m.group(1).strip()
            pkg = NLU._resolve_app(app_query)
            if pkg or "." in app_query:
                return Action("force_stop",
                              {"package": pkg or app_query},
                              f"Close {app_query}")
            return Action("force_stop", {"package": app_query, "guess": True},
                          f"Try to close {app_query}")

        # ---------------------------------------------------------- #
        #  navigation / keys
        # ---------------------------------------------------------- #
        if t in ("go home", "home", "home screen"):
            return Action("home", {}, "Go home")
        if t in ("go back", "back", "back button"):
            return Action("back", {}, "Back")
        if t in ("recent apps", "recents", "show recents", "multitask"):
            return Action("recent", {}, "Recent apps")
        if t in ("lock screen", "lock phone", "turn off screen", "sleep"):
            return Action("screen_off", {}, "Lock screen")
        if t in ("wake", "wake up", "turn on screen", "screen on"):
            return Action("screen_on", {}, "Wake screen")
        if t in ("unlock", "unlock phone"):
            return Action("unlock", {}, "Unlock phone")
        if "volume up" in t or "louder" in t:
            return Action("volume_up", {"times": NLU._count(t)}, "Volume up")
        if "volume down" in t or "quieter" in t:
            return Action("volume_down", {"times": NLU._count(t)}, "Volume down")
        if t in ("mute", "silence", "silent mode"):
            return Action("keyevent", {"key": "mute"}, "Mute")
        if "play" in t and "pause" not in t:
            return Action("keyevent", {"key": "play_pause"}, "Play/Pause")
        if "pause" in t:
            return Action("keyevent", {"key": "play_pause"}, "Pause")
        if "next song" in t or "skip" in t:
            return Action("keyevent", {"key": "next"}, "Next track")
        if "previous" in t or "last song" in t:
            return Action("keyevent", {"key": "previous"}, "Previous track")

        # ---------------------------------------------------------- #
        #  screen / input
        # ---------------------------------------------------------- #
        if t in ("screenshot", "take screenshot", "capture screen", "screen shot"):
            return Action("screenshot", {}, "Take screenshot")

        m = re.search(r"tap\s+(\d+)\s+(\d+)", t)
        if m:
            return Action("tap", {"x": int(m.group(1)), "y": int(m.group(2))},
                          f"Tap ({m.group(1)}, {m.group(2)})")

        m = re.search(r"swipe\s+(?:up|down|left|right)", t)
        if m:
            direction = m.group(0).split()[-1]
            return Action("swipe", {"direction": direction}, f"Swipe {direction}")

        m = re.search(r"swipe\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", t)
        if m:
            return Action("swipe",
                          {"x1": int(m.group(1)), "y1": int(m.group(2)),
                           "x2": int(m.group(3)), "y2": int(m.group(4))},
                          "Swipe")

        m = re.search(r"type\s+[\"']?(.+?)[\"']?$", t)
        if m:
            return Action("type_text", {"text": m.group(1).strip()},
                          f"Type: {m.group(1).strip()}")

        # ---------------------------------------------------------- #
        #  system / info
        # ---------------------------------------------------------- #
        if t in ("status", "device status", "phone status", "info", "battery"):
            return Action("device_info", {}, "Device status")
        if t in ("notifications", "show notifications", "read notifications"):
            return Action("notifications", {}, "Read notifications")
        if t in ("list apps", "installed apps", "apps", "what apps"):
            return Action("list_apps", {}, "List installed apps")
        if t in ("clear notifications", "dismiss notifications"):
            return Action("clear_notifications", {}, "Clear notifications")

        m = re.search(r"(?:open|go to|navigate to|browse to)\s+(?:url\s+|website\s+|link\s+)?(.+)", t)
        if m and ("." in m.group(1) or "http" in t):
            url = m.group(1).strip()
            return Action("open_url", {"url": url}, f"Open URL: {url}")

        # ---------------------------------------------------------- #
        #  search
        # ---------------------------------------------------------- #
        m = re.search(r"search\s+for\s+(.+)", t)
        if m:
            query = m.group(1).strip()
            return Action("open_url",
                          {"url": f"https://www.google.com/search?q={query}"},
                          f"Search for: {query}")

        # ---------------------------------------------------------- #
        #  raw shell  (power-user escape hatch)
        # ---------------------------------------------------------- #
        m = re.match(r"(?:run|shell|exec)\s+(.+)", t)
        if m:
            return Action("shell", {"command": m.group(1).strip()},
                          f"Shell: {m.group(1).strip()}")

        # ---------------------------------------------------------- #
        #  fallback — treat as a goal for multi-step planning
        # ---------------------------------------------------------- #
        return Action("goal", {"text": original}, f"Goal: {original}")

    @staticmethod
    def _resolve_app(query: str) -> str | None:
        """Resolve an app name to a package id."""
        q = query.strip().lower()
        if q in APP_PACKAGES:
            return APP_PACKAGES[q]
        # fuzzy: partial match
        for name, pkg in APP_PACKAGES.items():
            if name in q or q in name:
                return pkg
        return None

    @staticmethod
    def _count(text: str) -> int:
        m = re.search(r"(\d+)\s+times", text)
        return int(m.group(1)) if m else 1


# ===================================================================== #
#  Action executor — maps an Action to one or more ADB calls
# ===================================================================== #
class Executor:
    """Runs a single Action against the phone and returns a result string."""

    def __init__(self, adb: ADBClient):
        self.adb = adb
        self._handlers: dict[str, Callable[[Action], str]] = {
            "send_sms": self._send_sms,
            "make_call": self._make_call,
            "dial": self._dial,
            "open_app": self._open_app,
            "force_stop": self._force_stop,
            "home": lambda a: self.adb.home(),
            "back": lambda a: self.adb.back(),
            "recent": lambda a: self.adb.recent_apps(),
            "screen_on": lambda a: self.adb.screen_on(),
            "screen_off": lambda a: self.adb.screen_off(),
            "unlock": lambda a: self.adb.unlock(),
            "volume_up": self._volume_up,
            "volume_down": self._volume_down,
            "keyevent": lambda a: self.adb.keyevent(a.params.get("key", "")),
            "screenshot": lambda a: "screenshot captured",
            "tap": lambda a: self.adb.tap(a.params["x"], a.params["y"]),
            "swipe": self._swipe,
            "type_text": lambda a: self.adb.type_text(a.params["text"]),
            "device_info": self._device_info,
            "notifications": self._notifications,
            "clear_notifications": lambda a: self.adb.clear_notifications(),
            "list_apps": self._list_apps,
            "open_url": lambda a: self.adb.open_url(a.params["url"]),
            "shell": lambda a: self.adb.run_shell(a.params["command"]),
            "goal": lambda a: "needs planning",
        }

    def execute(self, action: Action) -> str:
        handler = self._handlers.get(action.name)
        if not handler:
            raise ADBError(f"unknown action: {action.name}")
        return handler(action)

    # --- individual handlers ------------------------------------------- #
    def _send_sms(self, a: Action) -> str:
        number = a.params.get("recipient", "")
        # if recipient is not a phone number, we can't resolve contacts via adb
        # so we open the SMS app with the recipient name as a hint
        if re.match(r"^\+?\d[\d\s\-\(\)]{3,}$", number):
            return self.adb.send_sms(number, a.params["message"])
        else:
            # open SMS app and type the name + message
            self.adb.send_sms("", "")  # opens messaging app
            time.sleep(1)
            self.adb.type_text(number)
            time.sleep(0.3)
            self.adb.keyevent("tab")
            self.adb.type_text(a.params["message"])
            return f"Opened messaging app — type '{number}' selected. Review and press send."

    def _make_call(self, a: Action) -> str:
        return self.adb.make_call(a.params["number"])

    def _dial(self, a: Action) -> str:
        return self.adb.dial_number(a.params["contact"])

    def _open_app(self, a: Action) -> str:
        return self.adb.open_app(a.params["package"])

    def _force_stop(self, a: Action) -> str:
        return self.adb.force_stop(a.params["package"])

    def _volume_up(self, a: Action) -> str:
        times = a.params.get("times", 1)
        for _ in range(times):
            self.adb.volume_up()
            time.sleep(0.1)
        return f"volume up x{times}"

    def _volume_down(self, a: Action) -> str:
        times = a.params.get("times", 1)
        for _ in range(times):
            self.adb.volume_down()
            time.sleep(0.1)
        return f"volume down x{times}"

    def _swipe(self, a: Action) -> str:
        if "direction" in a.params:
            w, h = self.adb.get_info().resolution
            if not w:
                w, h = 1080, 2400
            d = a.params["direction"]
            if d == "up":
                self.adb.swipe(w // 2, int(h * 0.7), w // 2, int(h * 0.3))
            elif d == "down":
                self.adb.swipe(w // 2, int(h * 0.3), w // 2, int(h * 0.7))
            elif d == "left":
                self.adb.swipe(int(w * 0.8), h // 2, int(w * 0.2), h // 2)
            elif d == "right":
                self.adb.swipe(int(w * 0.2), h // 2, int(w * 0.8), h // 2)
            return f"swipe {d}"
        return self.adb.swipe(
            a.params["x1"], a.params["y1"],
            a.params["x2"], a.params["y2"],
        )

    def _device_info(self, a: Action) -> str:
        info = self.adb.get_info()
        lines = [
            f"Model: {info.model}",
            f"Android: {info.android_version}",
            f"Battery: {info.battery_level}% ({info.battery_temp}°C)",
            f"Screen: {'on' if info.screen_on else 'off'}",
            f"Resolution: {info.resolution[0]}x{info.resolution[1]}",
            f"WiFi: {info.wifi_state}",
        ]
        return "\n".join(lines)

    def _notifications(self, a: Action) -> str:
        notifs = self.adb.get_notifications()
        if not notifs:
            return "No active notifications."
        lines = [f"📱 {len(notifs)} notifications:"]
        for n in notifs[:10]:
            line = f"  • {n['package']}"
            if n["title"]:
                line += f" — {n['title']}"
            if n["text"]:
                line += f": {n['text']}"
            lines.append(line)
        return "\n".join(lines)

    def _list_apps(self, a: Action) -> str:
        apps = self.adb.list_packages(third_party_only=True)
        if not apps:
            return "No third-party apps found."
        lines = [f"Installed apps ({len(apps)}):"]
        for app in sorted(apps):
            lines.append(f"  • {app}")
        return "\n".join(lines)


# ===================================================================== #
#  Planner — multi-step plan-execute-observe loop
# ===================================================================== #
class Planner:
    """
    Decomposes a high-level goal into steps, executes them in order,
    and observes the screen between steps.

    For rule-based mode the planner handles a curated set of compound
    tasks.  When an LLM is available it can generate step lists for
    arbitrary goals.
    """

    # Compound-task patterns → list of Actions
    COMPOUND_RECIPES: list[tuple[str, list[Action]]] = [
        # Example: "take a screenshot and tell me what's on screen"
        # → screenshot + (LLM analysis if available)
    ]

    def __init__(self, adb: ADBClient, executor: Executor):
        self.adb = adb
        self.executor = executor
        self.llm_enabled = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY"))

    def plan(self, goal: str) -> Plan:
        """Turn a goal string into a Plan with concrete steps."""
        goal_lower = goal.strip().lower()

        # Try direct single-action first
        action = NLU.parse(goal)
        if action.name != "goal":
            return Plan(goal=goal, steps=[Step(action=action)])

        # --- compound recipes ---------------------------------------- #
        # "open <app> and <action>"
        m = re.search(
            r"open\s+(\w+)\s+and\s+(.+)", goal_lower,
        )
        if m:
            app = m.group(1)
            rest = m.group(2)
            pkg = NLU._resolve_app(app)
            steps = []
            if pkg:
                steps.append(Step(Action("open_app", {"package": pkg}, f"Open {app}")))
                steps.append(Step(Action("wait", {"seconds": 2}, "Wait for app to load")))
                sub = NLU.parse(rest)
                steps.append(Step(sub))
            if steps:
                return Plan(goal=goal, steps=steps)

        # "send <message> to <contact> on <app>"
        m = re.search(
            r"(?:send|message)\s+(.+?)\s+to\s+(\w+)(?:\s+on\s+(\w+))?", goal_lower,
        )
        if m:
            msg, contact, app = m.group(1), m.group(2), m.group(3)
            pkg = NLU._resolve_app(app or "whatsapp")
            steps = [
                Step(Action("open_app", {"package": pkg}, f"Open {app or 'WhatsApp'}")),
                Step(Action("wait", {"seconds": 3}, "Wait for app")),
                Step(Action("screenshot", {}, "Observe screen")),
                Step(Action("type_text", {"text": contact}, f"Search for {contact}")),
                Step(Action("wait", {"seconds": 1}, "Wait")),
                Step(Action("screenshot", {}, "Observe results")),
                Step(Action("type_text", {"text": msg}, f"Type message")),
                Step(Action("goal", {"text": "press send button"}, "Press send")),
            ]
            return Plan(goal=goal, steps=steps)

        # "navigate to <url>"
        m = re.search(r"navigate to\s+(.+)", goal_lower)
        if m:
            url = m.group(1)
            return Plan(goal=goal, steps=[
                Step(Action("open_url", {"url": url}, f"Open {url}")),
                Step(Action("wait", {"seconds": 3}, "Wait for page")),
                Step(Action("screenshot", {}, "Show result")),
            ])

        # "turn off wifi and bluetooth"  (multiple system toggles)
        if "wifi" in goal_lower or "bluetooth" in goal_lower:
            steps = []
            if "wifi" in goal_lower and "off" in goal_lower:
                steps.append(Step(Action("shell",
                                         {"command": "svc wifi disable"},
                                         "Turn off WiFi")))
            elif "wifi" in goal_lower and "on" in goal_lower:
                steps.append(Step(Action("shell",
                                         {"command": "svc wifi enable"},
                                         "Turn on WiFi")))
            if "bluetooth" in goal_lower and "off" in goal_lower:
                steps.append(Step(Action("shell",
                                         {"command": "svc bluetooth disable"},
                                         "Turn off Bluetooth")))
            elif "bluetooth" in goal_lower and "on" in goal_lower:
                steps.append(Step(Action("shell",
                                         {"command": "svc bluetooth enable"},
                                         "Turn on Bluetooth")))
            if steps:
                return Plan(goal=goal, steps=steps)

        # Fallback: LLM-assisted planning or honest "I don't know"
        if self.llm_enabled:
            llm_steps = self._llm_plan(goal)
            if llm_steps:
                return Plan(goal=goal, steps=llm_steps)

        # Last resort: run as raw shell if it looks like a command
        if any(c in goal for c in ("(", "/", "dumpsys", "pm ", "am ")):
            return Plan(goal=goal, steps=[
                Step(Action("shell", {"command": goal}, f"Run: {goal}"))
            ])

        return Plan(
            goal=goal,
            steps=[],
            status=StepStatus.FAILED,
            summary=(
                "I couldn't figure out how to do that. Try phrasing it as a "
                "direct command, e.g.:\n"
                "  • \"open whatsapp\"\n"
                "  • \"text +1234567890 hello\"\n"
                "  • \"call +1234567890\"\n"
                "  • \"screenshot\"\n"
                "  • \"send hello to mom on whatsapp\"\n"
                "  • \"volume up 3 times\"\n"
                "  • \"swipe up\"\n"
                "Or set OPENAI_API_KEY to enable free-form natural language."
            ),
        )

    def execute_plan(self, plan: Plan, observer: Callable | None = None) -> Plan:
        """Run all steps in a plan, observing between steps."""
        plan.status = StepStatus.RUNNING
        for i, step in enumerate(plan.steps):
            step.status = StepStatus.RUNNING
            try:
                if step.action.name == "wait":
                    time.sleep(step.action.params.get("seconds", 1))
                    step.result = f"waited {step.action.params.get('seconds', 1)}s"
                else:
                    step.result = self.executor.execute(step.action)

                # observe — take a screenshot after each step
                if observer and step.action.name != "screenshot":
                    try:
                        step.screenshot = self.adb.screenshot_b64()
                    except ADBError:
                        pass

                step.status = StepStatus.DONE
            except ADBError as e:
                step.status = StepStatus.FAILED
                step.error = str(e)
                plan.status = StepStatus.FAILED
                plan.summary = f"Failed at step {i+1}: {e}"
                break
            except Exception as e:
                step.status = StepStatus.FAILED
                step.error = f"unexpected error: {e}"
                plan.status = StepStatus.FAILED
                plan.summary = f"Failed at step {i+1}: {e}"
                break

            if observer:
                observer(plan.to_dict())

        if plan.status != StepStatus.FAILED:
            plan.status = StepStatus.DONE
            plan.summary = self._summarize(plan)
        return plan

    def _summarize(self, plan: Plan) -> str:
        """Generate a plain-language summary of what happened."""
        done = [s for s in plan.steps if s.status == StepStatus.DONE]
        descs = [s.action.description for s in done]
        if len(descs) <= 3:
            return "; ".join(descs)
        return f"Completed {len(done)} steps: {', '.join(descs[:2])}, ..., {descs[-1]}"

    # --- LLM-assisted planning (optional) ----------------------------- #
    def _llm_plan(self, goal: str) -> list[Step]:
        """Ask an LLM to decompose a goal into ADB actions."""
        try:
            import openai
        except ImportError:
            return []

        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

        system = (
            "You are a phone-control planner. Given a user goal, output a JSON "
            "array of steps. Each step is an object with 'name' and 'params'. "
            "Valid action names: open_app, send_sms, make_call, tap, swipe, "
            "type_text, keyevent, screenshot, wait, open_url, shell, home, back. "
            "For wait, params={\"seconds\": N}. Respond with ONLY the JSON array."
        )
        try:
            client = openai.OpenAI(api_key=api_key, base_url=base_url)
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Goal: {goal}"},
                ],
                temperature=0.2,
            )
            raw = resp.choices[0].message.content.strip()
            # extract JSON array
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1 or end == -1:
                return []
            steps_data = json.loads(raw[start:end+1])
            steps = []
            for sd in steps_data:
                name = sd.get("name", "")
                params = sd.get("params", {})
                steps.append(Step(Action(name, params, name)))
            return steps
        except Exception as e:
            log.warning("LLM planning failed: %s", e)
            return []


# ===================================================================== #
#  Brain — top-level facade
# ===================================================================== #
class Brain:
    """The top-level agent: parse → plan → execute → observe → respond."""

    def __init__(self, adb: ADBClient):
        self.adb = adb
        self.executor = Executor(adb)
        self.planner = Planner(adb, self.executor)

    def handle(self, user_text: str, observer: Callable[[dict], None] | None = None) -> dict:
        """
        Main entry point.  Takes natural-language user input and returns
        a dict with the plan + a natural-language response.
        """
        plan = self.planner.plan(user_text)
        if plan.steps and plan.status != StepStatus.FAILED:
            plan = self.planner.execute_plan(plan, observer=observer)
        return {
            "plan": plan.to_dict(),
            "response": self._respond(plan, user_text),
        }

    def _respond(self, plan: Plan, user_text: str) -> str:
        """Turn the plan execution result into a human-readable reply."""
        if plan.status == StepStatus.FAILED:
            if plan.summary:
                return plan.summary
            return "That didn't work. Could you rephrase it?"

        if not plan.steps:
            return plan.summary or "I'm not sure what to do with that."

        if len(plan.steps) == 1:
            step = plan.steps[0]
            if step.status == StepStatus.DONE:
                return f"Done — {step.action.description}."
            return f"Couldn't complete: {step.action.description} ({step.error})"

        done = sum(1 for s in plan.steps if s.status == StepStatus.DONE)
        return plan.summary or f"Completed {done}/{len(plan.steps)} steps."
