const statusPill = document.getElementById("status-pill");
const connectForm = document.getElementById("connect-form");
const sendForm = document.getElementById("send-form");
const btnConnect = document.getElementById("btn-connect");
const btnDisconnect = document.getElementById("btn-disconnect");
const btnSend = document.getElementById("btn-send");
const connectMsg = document.getElementById("connect-msg");
const sendMsg = document.getElementById("send-msg");
const responseEl = document.getElementById("response");

function showMsg(el, text, ok) {
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = text;
  el.classList.toggle("error", !ok);
  el.classList.toggle("ok", ok);
}

function setConnected(connected) {
  statusPill.textContent = connected ? "Connected" : "Disconnected";
  statusPill.classList.toggle("online", connected);
  statusPill.classList.toggle("offline", !connected);
  btnDisconnect.disabled = !connected;
  btnSend.disabled = !connected;
  btnConnect.disabled = connected;
}

function formSettings() {
  return {
    port: document.getElementById("port").value.trim(),
    baudrate: Number(document.getElementById("baudrate").value),
    bytesize: Number(document.getElementById("bytesize").value),
    parity: document.getElementById("parity").value.trim() || "N",
    stopbits: Number(document.getElementById("stopbits").value),
  };
}

async function refreshStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    setConnected(Boolean(data.connected));
  } catch {
    setConnected(false);
  }
}

connectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showMsg(connectMsg, "");
  try {
    const res = await fetch("/api/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formSettings()),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      showMsg(connectMsg, data.error || "Connect failed", false);
      setConnected(false);
      return;
    }
    showMsg(connectMsg, `Open on ${data.settings.port} @ ${data.settings.baudrate} 8${data.settings.parity}${data.settings.stopbits}`, true);
    setConnected(true);
  } catch (err) {
    showMsg(connectMsg, String(err), false);
    setConnected(false);
  }
});

btnDisconnect.addEventListener("click", async () => {
  showMsg(connectMsg, "");
  await fetch("/api/disconnect", { method: "POST" });
  setConnected(false);
  showMsg(connectMsg, "Disconnected", true);
});

sendForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showMsg(sendMsg, "");
  const command = document.getElementById("command").value;
  try {
    const res = await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      showMsg(sendMsg, data.error || "Send failed", false);
      return;
    }
    responseEl.textContent = data.response || "(empty response)";
    responseEl.classList.remove("flash");
    void responseEl.offsetWidth;
    responseEl.classList.add("flash");
    showMsg(sendMsg, "OK", true);
  } catch (err) {
    showMsg(sendMsg, String(err), false);
  }
});

refreshStatus();
