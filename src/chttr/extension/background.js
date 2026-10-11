// Relays messages from Meet tabs to the chttr daemon through the native host
// (`chttr native-host`, registered as com.chttr.meet), and shows a REC badge on
// the tab while chttr records that call.

const HOST = "com.chttr.meet";
const calls = new Map();      // tabId -> Meet code of a call in progress
let port = null;

function connect() {
  if (port) return port;
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (e) {
    console.warn("chttr: native host unavailable", e);
    return null;
  }
  port.onMessage.addListener((reply) => {
    if (reply && typeof reply.tab === "number" && typeof reply.recording === "boolean") {
      chrome.action.setBadgeText({ tabId: reply.tab, text: reply.recording ? "REC" : "" });
      chrome.action.setBadgeBackgroundColor({ tabId: reply.tab, color: "#c62828" });
    }
  });
  port.onDisconnect.addListener(() => {
    console.warn("chttr: native host disconnected", chrome.runtime.lastError?.message);
    port = null;
  });
  return port;
}

function relay(msg, tabId) {
  const p = connect();
  if (!p) return;
  try {
    p.postMessage({ ...msg, tab: tabId });
  } catch (e) {
    port = null;
  }
}

chrome.runtime.onMessage.addListener((msg, sender) => {
  const tabId = sender.tab?.id;
  if (tabId === undefined || !msg || !msg.type) return;
  if (msg.type === "joined") calls.set(tabId, msg.code);
  if (msg.type === "left") calls.delete(tabId);
  relay(msg, tabId);
});

// Closing the tab doesn't give the page a reliable chance to say goodbye.
chrome.tabs.onRemoved.addListener((tabId) => {
  const code = calls.get(tabId);
  if (!code) return;
  calls.delete(tabId);
  relay({ type: "left", code, t: Date.now() }, tabId);
});
