"""
ADB Bridge — wraps the Android Debug Bridge for remote phone control.

Uses subprocess to call the `adb` binary directly, so it works with any
ADB version the user has installed. All commands target the first connected
device unless a serial is specified.

Author: WorkBuddy
"""
from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

# Common Android keycodes (subset that matters for control)
KEYCODES = {
    "home": 3,
    "back": 4,
    "recent": 187,        # APP_SWITCH
    "power": 26,
    "volume_up": 24,
    "volume_down": 25,
    "mute": 164,
    "menu": 82,
    "enter": 66,
    "del": 67,            # backspace
    "tab": 61,
    "escape": 111,
    "camera": 27,
    "play_pause": 85,
    "next": 87,
    "previous": 88,
    "notification": 83,   # collapse status bar / open notifications
    "search": 84,
}


@dataclass
class DeviceInfo:
    serial: str
    model: str
    android_version: str
    battery_level: int
    battery_temp: float
    screen_on: bool
    wifi_state: str
    resolution: tuple[int, int] = (0, 0)
    raw: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "serial": self.serial,
            "model": self.model,
            "android_version": self.android_version,
            "battery_level": self.battery_level,
            "battery_temp": self.battery_temp,
            "screen_on": self.screen_on,
            "wifi_state": self.wifi_state,
            "resolution": list(self.resolution),
        }


class ADBError(Exception):
    """Raised when an ADB command fails."""


class ADBClient:
    """Thin, resilient wrapper around the `adb` CLI."""

    def __init__(self, serial: str | None = None, host: str | None = None):
        self.serial = serial
        self.host = host  # e.g. "192.168.1.50:5555" for wireless adb
        self._bin = shutil.which("adb") or "adb"

    # ------------------------------------------------------------------ #
    # low-level helpers
    # ------------------------------------------------------------------ #
    def _base_args(self) -> list[str]:
        args = [self._bin]
        if self.host:
            args += ["-s", self.host]
        elif self.serial:
            args += ["-s", self.serial]
        return args

    def _run(self, *args: str, timeout: float = 30.0) -> str:
        cmd = self._base_args() + list(args)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise ADBError(
                "`adb` binary not found. Install platform-tools and ensure "
                "`adb` is on your PATH.  https://developer.android.com/tools"
            )
        except subprocess.TimeoutExpired:
            raise ADBError(f"ADB command timed out: {' '.join(args)}")
        if proc.returncode != 0:
            # Common: "device not found" / "unauthorized"
            raise ADBError(
                f"adb {' '.join(args)} failed (exit {proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout.strip()

    def _shell(self, *cmd_parts: str, timeout: float = 30.0) -> str:
        """Run `adb shell <cmd>`."""
        return self._run("shell", *cmd_parts, timeout=timeout)

    # ------------------------------------------------------------------ #
    # connection management
    # ------------------------------------------------------------------ #
    def connect(self, host: str) -> str:
        """Connect to a device over TCP (wireless ADB)."""
        self.host = host
        out = self._run("connect", host)
        return out

    def disconnect(self, host: str | None = None) -> str:
        target = host or self.host or ""
        if not target:
            return "no host to disconnect"
        return self._run("disconnect", target)

    def list_devices(self) -> list[dict[str, str]]:
        """Return [{serial, state}] for every connected device."""
        out = self._run("devices")
        devs: list[dict[str, str]] = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                devs.append({"serial": parts[0], "state": parts[1]})
        return devs

    def is_connected(self) -> bool:
        try:
            devs = self.list_devices()
            if not devs:
                return False
            if self.serial or self.host:
                target = self.serial or self.host
                return any(d["serial"] == target and d["state"] == "device" for d in devs)
            return any(d["state"] == "device" for d in devs)
        except ADBError:
            return False

    def wait_for_device(self, timeout: float = 60.0) -> bool:
        try:
            self._run("wait-for-device", timeout=timeout)
            return True
        except ADBError:
            return False

    # ------------------------------------------------------------------ #
    # device info
    # ------------------------------------------------------------------ #
    def get_info(self) -> DeviceInfo:
        """Gather a snapshot of device status."""
        raw: dict[str, str] = {}

        def _prop(name: str) -> str:
            try:
                return self._shell("getprop", name)
            except ADBError:
                return ""

        raw["model"] = _prop("ro.product.model")
        raw["brand"] = _prop("ro.product.brand")
        raw["android"] = _prop("ro.build.version.release")
        raw["sdk"] = _prop("ro.build.version.sdk")
        raw["serial"] = _prop("ro.serialno") or (self.serial or self.host or "unknown")

        # battery
        try:
            bat = self._shell("dumpsys", "battery")
            level = re.search(r"level:\s*(\d+)", bat)
            temp = re.search(r"temperature:\s*(\d+)", bat)
            raw["battery_level"] = level.group(1) if level else "0"
            raw["battery_temp"] = str(int(temp.group(1)) / 10) if temp else "0"
        except ADBError:
            raw["battery_level"] = "0"
            raw["battery_temp"] = "0"

        # screen state
        try:
            pw = self._shell("dumpsys", "power")
            raw["screen_on"] = "true" if "mWakefulness=Awake" in pw else "false"
        except ADBError:
            raw["screen_on"] = "false"

        # wifi
        try:
            raw["wifi"] = self._shell("dumpsys", "wifi")
            raw["wifi_state"] = "enabled" if "Wi-Fi is enabled" in raw["wifi"] else "disabled"
        except ADBError:
            raw["wifi_state"] = "unknown"

        # resolution
        res = (0, 0)
        try:
            size = self._shell("wm", "size")
            m = re.search(r"(\d+)x(\d+)", size)
            if m:
                res = (int(m.group(1)), int(m.group(2)))
        except ADBError:
            pass

        return DeviceInfo(
            serial=raw["serial"],
            model=raw["model"] or "unknown",
            android_version=raw["android"] or "?",
            battery_level=int(raw.get("battery_level", 0)),
            battery_temp=float(raw.get("battery_temp", 0)),
            screen_on=raw["screen_on"] == "true",
            wifi_state=raw.get("wifi_state", "unknown"),
            resolution=res,
            raw=raw,
        )

    # ------------------------------------------------------------------ #
    # input — taps, swipes, text, keys
    # ------------------------------------------------------------------ #
    def tap(self, x: int, y: int) -> str:
        return self._shell("input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
        return self._shell(
            "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)
        )

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> str:
        """A long-press is a swipe that goes nowhere for a while."""
        return self._shell(
            "input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)
        )

    def type_text(self, text: str) -> str:
        """Type a string. Splits on whitespace because adb input text hates spaces."""
        parts = text.split(" ")
        for i, part in enumerate(parts):
            if part:
                # escape special chars
                safe = part.replace("&", r"\&").replace("<", r"\<").replace(">", r"\>")
                self._shell("input", "text", safe)
            if i < len(parts) - 1:
                self._shell("input", "keyevent", "62")  # KEYCODE_SPACE
        return "ok"

    def keyevent(self, key: str | int) -> str:
        code = KEYCODES.get(str(key).lower(), key)
        return self._shell("input", "keyevent", str(code))

    def back(self) -> str:
        return self.keyevent("back")

    def home(self) -> str:
        return self.keyevent("home")

    def recent_apps(self) -> str:
        return self.keyevent("recent")

    def power(self) -> str:
        return self.keyevent("power")

    def volume_up(self) -> str:
        return self.keyevent("volume_up")

    def volume_down(self) -> str:
        return self.keyevent("volume_down")

    def screen_on(self) -> str:
        return self._shell("input", "keyevent", "224")  # KEYCODE_WAKEUP

    def screen_off(self) -> str:
        return self._shell("input", "keyevent", "263")  # KEYCODE_SLEEP

    def unlock(self) -> str:
        """Wake screen and swipe up to dismiss lockscreen."""
        self.screen_on()
        time.sleep(0.3)
        w, h = self.get_info().resolution
        if w and h:
            self.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 400)
        else:
            self.swipe(540, 1800, 540, 400, 400)
        return "unlocked"

    # ------------------------------------------------------------------ #
    # screenshot / screen
    # ------------------------------------------------------------------ #
    def screenshot(self) -> bytes:
        """Capture the screen, return PNG bytes."""
        # Use exec-out to stream binary directly (no file on device needed)
        cmd = self._base_args() + [
            "exec-out", "screencap", "-p"
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=15)
        except FileNotFoundError:
            raise ADBError("adb binary not found")
        except subprocess.TimeoutExpired:
            raise ADBError("screenshot timed out")
        if proc.returncode != 0:
            raise ADBError(
                f"screenshot failed: {proc.stderr.decode('utf-8', 'ignore').strip()}"
            )
        png = proc.stdout
        if not png or not png.startswith(b"\x89PNG"):
            raise ADBError("screenshot returned no valid PNG data")
        return png

    def screenshot_b64(self) -> str:
        return base64.b64encode(self.screenshot()).decode()

    def dump_ui(self) -> str:
        """Dump the current view hierarchy as XML (for element-aware automation)."""
        self._shell("uiautomator", "dump", "/sdcard/ui.xml")
        return self._shell("cat", "/sdcard/ui.xml")

    # ------------------------------------------------------------------ #
    # apps
    # ------------------------------------------------------------------ #
    def list_packages(self, third_party_only: bool = False) -> list[str]:
        flag = "-3" if third_party_only else ""
        args = ["pm", "list", "packages"] + ([flag] if flag else [])
        out = self._shell(*args)
        return [line.replace("package:", "").strip() for line in out.splitlines() if line.startswith("package:")]

    def open_app(self, package: str) -> str:
        """Launch an app by package name."""
        # Try monkey first (works for most apps)
        try:
            return self._shell(
                "monkey", "-p", package, "-c",
                "android.intent.category.LAUNCHER", "1"
            )
        except ADBError:
            pass
        # Fallback: try to resolve main activity
        try:
            out = self._shell("cmd", "package", "resolve-activity", "--brief", package)
            activity = out.strip().splitlines()[-1].strip()
            if activity and "/" in activity:
                pkg, act = activity.split("/", 1)
                if not act.startswith("."):
                    act = f"{pkg}.{act}" if not act.startswith(pkg) else act
                return self._shell("am", "start", "-n", f"{pkg}/{act}")
        except ADBError:
            pass
        raise ADBError(f"could not launch app: {package}")

    def force_stop(self, package: str) -> str:
        return self._shell("am", "force-stop", package)

    def install(self, apk_path: str) -> str:
        return self._run("install", "-r", apk_path)

    def uninstall(self, package: str) -> str:
        return self._run("uninstall", package)

    # ------------------------------------------------------------------ #
    # communication — SMS, calls, notifications
    # ------------------------------------------------------------------ #
    def send_sms(self, number: str, message: str) -> str:
        """Send an SMS via the default messaging app's intent."""
        safe_msg = message.replace(" ", "\\ ")
        return self._shell(
            "am", "start", "-a",
            "android.intent.action.SENDTO",
            "-d", f"sms:{number}",
            "--es", "sms_body", safe_msg,
            "--ez", "exit_on_sent", "true",
        )

    def make_call(self, number: str) -> str:
        return self._shell("am", "start", "-a", "android.intent.action.CALL", "-d", f"tel:{number}")

    def dial_number(self, number: str) -> str:
        """Open the dialer with a number (doesn't auto-call)."""
        return self._shell("am", "start", "-a", "android.intent.action.DIAL", "-d", f"tel:{number}")

    def get_notifications(self) -> list[dict[str, str]]:
        """Parse active notifications from dumpsys."""
        try:
            out = self._shell("dumpsys", "notification", "--noredact")
        except ADBError:
            return []
        notifs: list[dict[str, str]] = []
        for block in out.split("NotificationRecord"):
            pkg = re.search(r"pkg=([^\s]+)", block)
            title = re.search(r'android\.title=([^\n]+)', block)
            text = re.search(r'android\.text=([^\n]+)', block)
            if pkg:
                notifs.append({
                    "package": pkg.group(1),
                    "title": title.group(1).strip() if title else "",
                    "text": text.group(1).strip() if text else "",
                })
        return notifs[-20:]  # last 20

    def clear_notifications(self) -> str:
        return self._shell("service", "call", "notification", "1", "s16", "com.android.systemui")

    # ------------------------------------------------------------------ #
    # misc utilities
    # ------------------------------------------------------------------ #
    def set_clipboard(self, text: str) -> str:
        return self._shell("am", "broadcast",
                           "-a", "clipper.set",
                           "-e", "text", text)

    def get_clipboard(self) -> str:
        try:
            return self._shell("am", "broadcast", "-a", "clipper.get")
        except ADBError:
            return ""

    def set_media_volume(self, level: int) -> str:
        return self._shell("media", "volume", "--stream", "3", "--set", str(level))

    def set_ring_volume(self, level: int) -> str:
        return self._shell("media", "volume", "--stream", "2", "--set", str(level))

    def open_url(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return self._shell("am", "start", "-a", "android.intent.action.VIEW", "-d", url)

    def run_shell(self, command: str) -> str:
        """Run an arbitrary shell command (for power users)."""
        return self._shell(*command.split())


# Singleton-ish default client — reconfigured by the server on connect
default_client = ADBClient()
