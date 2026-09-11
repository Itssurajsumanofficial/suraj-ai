/* Suraj AI frontend logic — WebSocket chat, screen mirror, voice, plan viewer */
(function () {
  "use strict";

  // ---------------------------------------------------------------- //
  //  State
  // ---------------------------------------------------------------- //
  let ws = null;
  let mirroring = false;
  let speakEnabled = false;
  let recognition = null;
  let phoneResolution = [0, 0];   // [width, height] of the actual phone screen

  // ---------------------------------------------------------------- //
  //  DOM
  // ---------------------------------------------------------------- //
  const $ = (id) => document.getElementById(id);
  const chatMessages = $("chat-messages");
  const chatInput = $("chat-input");
  const sendBtn = $("send-btn");
  const screenImg = $("phone-screen-img");
  const screenPlaceholder = $("screen-placeholder");
  const screenContainer = $("screen-container");
  const planViewer = $("plan-viewer");
  const connDot = $("connection-dot");
  const connText = $("connection-text");

  // ---------------------------------------------------------------- //
  //  WebSocket
  // ---------------------------------------------------------------- //
  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    // If we're behind auth, we need to pass the session token.
    // We grab it from the cookie via a one-time API call.
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => log("WebSocket connected");
    ws.onerror = () => log("WebSocket error");
    ws.onclose = () => {
      log("WebSocket closed, reconnecting in 3s…");
      setTimeout(connectWS, 3000);
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      handleMessage(msg);
    };
  }

  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  // ---------------------------------------------------------------- //
  //  Message handler
  // ---------------------------------------------------------------- //
  function handleMessage(msg) {
    switch (msg.type) {
      case "chat_reply":
        removeThinking();
        addMessage("ai", msg.response || "(no response)", msg.plan);
        if (speakEnabled && msg.response) speak(msg.response);
        break;

      case "thinking":
        addMessage("ai", '<span class="thinking-dots">Thinking</span>');
        break;

      case "screenshot":
        showScreenshot(msg.image);
        break;

      case "status":
        if (msg.info && msg.info.connected === false) {
          setConnection(false);
        } else if (msg.info) {
          updateDeviceInfo(msg.info);
        }
        break;

      case "plan_update":
        renderPlan(msg.plan);
        break;

      case "info":
        // transient info — no action needed
        if (msg.message && msg.message.includes("switched")) {
          addMessage("ai", "🔄 " + msg.message);
          refreshStatus();
        }
        break;

      case "devices":
        renderDeviceList(msg.devices, msg.active);
        break;

      case "error":
        addMessage("error", "⚠️ " + msg.message);
        break;

      default:
        console.log("Unknown message:", msg);
    }
  }

  // ---------------------------------------------------------------- //
  //  Chat
  // ---------------------------------------------------------------- //
  function sendChat(text) {
    if (!text.trim()) return;
    addMessage("user", escapeHtml(text));
    send({ type: "chat", text });
    // show thinking indicator
    addMessage("ai", '<span class="thinking-dots">Thinking</span>', null, "thinking");
    chatInput.value = "";
  }

  function addMessage(role, content, plan = null, id = null) {
    // remove existing thinking message
    if (id === "thinking" || role === "user") removeThinking();

    const div = document.createElement("div");
    div.className = `message ${role}`;
    if (id) div.id = id;

    const contentDiv = document.createElement("div");
    contentDiv.className = "message-content";
    contentDiv.innerHTML = content;
    div.appendChild(contentDiv);

    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    if (plan) renderPlan(plan);
  }

  function removeThinking() {
    const t = $("thinking");
    if (t) t.remove();
  }

  // ---------------------------------------------------------------- //
  //  Plan viewer
  // ---------------------------------------------------------------- //
  function renderPlan(plan) {
    if (!plan || !plan.steps || plan.steps.length === 0) {
      planViewer.innerHTML = '<p class="hint">No active task</p>';
      return;
    }

    const statusIcons = {
      pending: "⏳",
      running: "🔄",
      done: "✅",
      failed: "❌",
    };

    let html = "";
    for (const step of plan.steps) {
      const icon = statusIcons[step.status] || "⏳";
      html += `
        <div class="plan-step">
          <span class="step-icon">${icon}</span>
          <div class="step-text">
            <div class="step-desc">${escapeHtml(step.action.description || step.action.name)}</div>
            ${step.result ? `<div class="step-result">${escapeHtml(step.result)}</div>` : ""}
            ${step.error ? `<div class="step-result" style="color:var(--red)">⚠️ ${escapeHtml(step.error)}</div>` : ""}
          </div>
        </div>`;
    }

    if (plan.summary) {
      html += `<div class="plan-summary"><strong>Summary:</strong> ${escapeHtml(plan.summary)}</div>`;
    }

    planViewer.innerHTML = html;
  }

  // ---------------------------------------------------------------- //
  //  Screenshot / screen mirror
  // ---------------------------------------------------------------- //
  function showScreenshot(b64) {
    screenPlaceholder.classList.add("hidden");
    screenImg.classList.remove("hidden");
    screenImg.src = `data:image/png;base64,${b64}`;
  }

  function toggleMirror() {
    if (mirroring) {
      send({ type: "mirror_stop" });
      mirroring = false;
      $("mirror-btn").textContent = "▶ Live Mirror";
    } else {
      send({ type: "mirror", interval: 1.5 });
      mirroring = true;
      $("mirror-btn").textContent = "⏸ Stop Mirror";
    }
  }

  // ---------------------------------------------------------------- //
  //  Screen interaction — tap & swipe
  // ---------------------------------------------------------------- //
  function getPhoneCoords(clientX, clientY) {
    /* Convert a click position on the <img> to real phone-screen
       coordinates using the image's displayed bounding box.        */
    const rect = screenImg.getBoundingClientRect();
    const relX = (clientX - rect.left) / rect.width;
    const relY = (clientY - rect.top) / rect.height;
    // Use phone resolution if we know it, otherwise fall back to
    // the image's natural size.
    const pw = phoneResolution[0] || screenImg.naturalWidth || 1080;
    const ph = phoneResolution[1] || screenImg.naturalHeight || 2400;
    return {
      x: Math.round(relX * pw),
      y: Math.round(relY * ph),
    };
  }

  function showTapIndicator(clientX, clientY) {
    const rect = screenImg.getBoundingClientRect();
    const ind = $("tap-indicator");
    ind.style.left = `${clientX - rect.left - 15}px`;
    ind.style.top = `${clientY - rect.top - 15}px`;
    ind.classList.remove("hidden");
    setTimeout(() => ind.classList.add("hidden"), 500);
  }

  // tap on screen
  screenImg.addEventListener("click", (e) => {
    const { x, y } = getPhoneCoords(e.clientX, e.clientY);
    showTapIndicator(e.clientX, e.clientY);
    send({ type: "tap", x, y });
  });

  // swipe via drag
  let dragStart = null;
  screenImg.addEventListener("mousedown", (e) => {
    dragStart = getPhoneCoords(e.clientX, e.clientY);
  });
  screenImg.addEventListener("mouseup", (e) => {
    if (!dragStart) return;
    const end = getPhoneCoords(e.clientX, e.clientY);
    const dx = Math.abs(end.x - dragStart.x);
    const dy = Math.abs(end.y - dragStart.y);
    // Only treat as swipe if moved > 30px, otherwise it's a tap
    if (dx > 30 || dy > 30) {
      send({
        type: "swipe",
        x1: dragStart.x, y1: dragStart.y,
        x2: end.x, y2: end.y,
      });
    }
    dragStart = null;
  });

  // ---------------------------------------------------------------- //
  //  Quick actions
  // ---------------------------------------------------------------- //
  document.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const action = btn.dataset.action;
      sendChat(action);
    });
  });

  document.querySelectorAll("[data-swipe]").forEach((btn) => {
    btn.addEventListener("click", () => {
      sendChat(`swipe ${btn.dataset.swipe}`);
    });
  });

  document.querySelectorAll("[data-cmd]").forEach((btn) => {
    btn.addEventListener("click", () => {
      sendChat(btn.dataset.cmd);
    });
  });

  // ---------------------------------------------------------------- //
  //  Voice — SpeechRecognition + SpeechSynthesis
  // ---------------------------------------------------------------- //
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;

  if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";

    recognition.onresult = (ev) => {
      const text = ev.results[0][0].transcript;
      chatInput.value = text;
      sendChat(text);
    };
    recognition.onend = () => {
      $("voice-btn").classList.remove("listening");
      $("voice-btn").textContent = "🎤";
    };
    recognition.onerror = (ev) => {
      addMessage("error", "Voice error: " + ev.error);
      $("voice-btn").classList.remove("listening");
      $("voice-btn").textContent = "🎤";
    };
  } else {
    $("voice-btn").title = "Speech recognition not supported in this browser";
    $("voice-btn").style.opacity = "0.4";
  }

  $("voice-btn").addEventListener("click", () => {
    if (!recognition) return;
    if ($("voice-btn").classList.contains("listening")) {
      recognition.stop();
    } else {
      recognition.start();
      $("voice-btn").classList.add("listening");
      $("voice-btn").textContent = "🔴";
    }
  });

  $("speak-btn").addEventListener("click", () => {
    speakEnabled = !speakEnabled;
    $("speak-btn").textContent = speakEnabled ? "🔊" : "🔈";
    $("speak-btn").style.opacity = speakEnabled ? "1" : "0.6";
  });

  function speak(text) {
    if (!("speechSynthesis" in window)) return;
    // strip HTML
    const clean = text.replace(/<[^>]*>/g, "").trim();
    const utt = new SpeechSynthesisUtterance(clean);
    utt.rate = 1.1;
    speechSynthesis.cancel();
    speechSynthesis.speak(utt);
  }

  // ---------------------------------------------------------------- //
  //  Device info
  // ---------------------------------------------------------------- //
  function updateDeviceInfo(info) {
    setConnection(true);
    phoneResolution = info.resolution || [0, 0];

    $("info-model").textContent = info.model || "—";
    $("info-android").textContent = info.android_version || "—";
    $("info-battery").textContent = info.battery_level != null
      ? `${info.battery_level}% (${info.battery_temp}°C)` : "—";
    $("info-screen").textContent = info.screen_on != null
      ? (info.screen_on ? "On" : "Off") : "—";
    $("info-res").textContent = phoneResolution[0]
      ? `${phoneResolution[0]}×${phoneResolution[1]}` : "—";
    $("info-wifi").textContent = info.wifi_state || "—";
  }

  function setConnection(connected) {
    connDot.className = `dot ${connected ? "dot-on" : "dot-off"}`;
    connText.textContent = connected ? "Connected" : "No device";
  }

  // ---------------------------------------------------------------- //
  //  Connect modal
  // ---------------------------------------------------------------- //
  const modal = $("connect-modal");
  $("connect-btn").addEventListener("click", openConnectModal);
  $("close-modal").addEventListener("click", () => modal.classList.add("hidden"));

  async function openConnectModal() {
    modal.classList.remove("hidden");
    await refreshDeviceList();
  }

  async function refreshDeviceList() {
    const list = $("device-list");
    list.textContent = "Scanning…";
    try {
      const res = await fetch("/api/devices");
      const data = await res.json();
      if (data.error) {
        list.innerHTML = `<span style="color:var(--red)">⚠️ ${data.error}</span>`;
        return;
      }
      if (!data.devices.length) {
        list.innerHTML = '<span style="color:var(--text-dim)">No devices found. Make sure USB debugging is on and the phone is plugged in.</span>';
        return;
      }
      list.innerHTML = data.devices.map(d =>
        `<div class="device-entry">
           <span>${d.serial}</span>
           <span style="color:${d.state === 'device' ? 'var(--green)' : 'var(--yellow)'}">${d.state}</span>
         </div>`
      ).join("");

      // Populate device selector
      const sel = $("device-selector");
      sel.innerHTML = data.devices.map(d =>
        `<option value="${d.serial}" ${d.serial === data.active ? "selected" : ""}>${d.serial} (${d.state})</option>`
      ).join("");
      $("device-selector-wrap").classList.remove("hidden");
    } catch (e) {
      list.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
    }
  }

  function renderDeviceList(devices, active) {
    const sel = $("device-selector");
    if (!sel || !devices || !devices.length) return;
    sel.innerHTML = devices.map(d =>
      `<option value="${d.serial}" ${d.serial === active ? "selected" : ""}>${d.serial} (${d.state})</option>`
    ).join("");
    $("device-selector-wrap").classList.remove("hidden");
  }

  $("select-device-btn").addEventListener("click", async () => {
    const serial = $("device-selector").value;
    send({ type: "select_device", serial });
    addMessage("ai", `🔄 Switched to device: ${serial}`);
    modal.classList.add("hidden");
    refreshStatus();
  });

  $("connect-host-btn").addEventListener("click", async () => {
    const host = $("host-input").value.trim();
    if (!host) return;
    try {
      const res = await fetch(`/api/connect/${host}`, { method: "POST" });
      const data = await res.json();
      if (data.error) {
        addMessage("error", "Connect failed: " + data.error);
        return;
      }
      addMessage("ai", `Connected to ${host}. ${data.message || ""}`);
      setConnection(true);
      modal.classList.add("hidden");
      refreshStatus();
    } catch (e) {
      addMessage("error", "Connect failed: " + e.message);
    }
  });

  // ---------------------------------------------------------------- //
  //  Status refresh
  // ---------------------------------------------------------------- //
  async function refreshStatus() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      if (data.error) {
        setConnection(false);
        return;
      }
      updateDeviceInfo(data);
    } catch {
      setConnection(false);
    }
  }

  // ---------------------------------------------------------------- //
  //  Event listeners
  // ---------------------------------------------------------------- //
  sendBtn.addEventListener("click", () => sendChat(chatInput.value));
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendChat(chatInput.value);
  });

  $("snapshot-btn").addEventListener("click", () => send({ type: "screenshot" }));
  $("mirror-btn").addEventListener("click", toggleMirror);

  // ---------------------------------------------------------------- //
  //  Utils
  // ---------------------------------------------------------------- //
  function escapeHtml(str) {
    if (str == null) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function log(...args) {
    console.log("[Suraj AI]", ...args);
  }

  // ---------------------------------------------------------------- //
  //  Init
  // ---------------------------------------------------------------- //
  connectWS();
  refreshStatus();
  setInterval(refreshStatus, 15000);   // poll device status every 15s
})();
