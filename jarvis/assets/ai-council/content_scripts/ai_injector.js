// Content script injected into ChatGPT, Gemini, Claude, and Grok tabs

console.log("[AI Council Injector] Content script active on:", window.location.host);

// Jarvis: a topic handed over in the address (#jarvis-council=...) starts a council.
(function jarvisCouncilFromHash() {
  if (window.top !== window || window.__jarvisCouncilSeen) return;
  window.__jarvisCouncilSeen = true;
  const hash = window.location.hash || "";
  if (!hash.startsWith("#jarvis-council=")) return;
  const params = new URLSearchParams(hash.slice(1));
  const topic = (params.get("jarvis-council") || "").trim();
  history.replaceState(null, "", window.location.pathname + window.location.search);
  if (topic) {
    chrome.runtime.sendMessage({ type: "JARVIS_COUNCIL", topic, judge: params.get("judge") || "claude" });
  }
})();

// Model Selector Configurations
const SELECTORS = {
  chatgpt: {
    input: [
      // ChatGPT ProseMirror composer — try all contenteditable variants
      "div.ProseMirror#prompt-textarea",
      "#prompt-textarea",
      "div[contenteditable='plaintext-only']#prompt-textarea",
      "div[contenteditable='true']#prompt-textarea",
      "div.ProseMirror[contenteditable='plaintext-only']",
      "div.ProseMirror[contenteditable='true']",
      // Legacy textarea fallbacks
      "textarea#mobile-composer-prompt",
      "#mobile-composer-prompt",
      "textarea"
    ],
    submit: [
      // Specific IDs only — avoid broad selectors that match mic/voice buttons
      "#composer-submit-button",
      "button[data-testid='send-button']",
      "button[aria-label='Send message']",
      "button[aria-label='Отправить сообщение']",
      "button[aria-label*='Send']",
      "button[aria-label*='Отправить']"
    ],
    stop: [
      "button[data-testid='stop-button']",
      "button[aria-label*='Stop generating']",
      "button[aria-label*='Stop']",
      "button[aria-label*='stop']",
      "button[aria-label*='Остановить']",
      "button[aria-label*='остановить']",
      "button[aria-label*='Прекратить']"
    ],
    response: ["article[data-testid^='conversation-turn-']", "div[data-message-author-role='assistant']", "div.markdown"]
  },
  gemini: {
    input: [
      "rich-textarea div[contenteditable='true']",
      "div[contenteditable='true']",
      "rich-textarea p",
      ".qd-input",
      "textarea"
    ],
    submit: [
      "button.send-button",
      "button.send-button-container",
      "button[aria-label*='Send']",
      "button[aria-label*='send']",
      "button[aria-label*='Отправить']",
      "button[aria-label*='отправить']",
      "button:has(mat-icon[fonticon='send'])",
      ".send-button-container button"
    ],
    stop: [
      "button[aria-label*='Stop']",
      "button[aria-label*='stop']",
      "button[aria-label*='Остановить']",
      "button[aria-label*='остановить']",
      "mat-icon[fonticon='stop']",
      "button:has(mat-icon[fonticon='stop'])",
      "button.stop-button"
    ],
    response: [
      "message-content",
      "model-response",
      "div.model-response-text",
      ".markdown"
    ]
  },
  claude: {
    input: ["div[contenteditable='true']", "fieldset div[contenteditable='true']", "div.ProseMirror"],
    submit: [
      "button[aria-label*='Send']",
      "button[aria-label*='send']",
      "button[aria-label*='Отправить']",
      "button[aria-label*='отправить']"
    ],
    stop: [
      "button[aria-label*='Stop']",
      "button[aria-label*='stop']",
      "button[aria-label*='Остановить']",
      "button[aria-label*='остановить']"
    ],
    response: [".font-claude-message", "div.grid-cols-1", "div.prose"]
  },
  grok: {
    input: [
      "textarea[placeholder*='Переключись']",
      "textarea[placeholder*='Введи']",
      "textarea[placeholder*='Ask']",
      "textarea[placeholder*='Grok']",
      "textarea",
      "div[contenteditable='true']"
    ],
    submit: [
      "button[type='submit']",
      "button[aria-label*='Send']",
      "button[aria-label*='send']",
      "button[aria-label*='Отправить']",
      "button[aria-label*='отправить']",
      "button[aria-label*='Submit']",
      "form button:has(svg)",
      "form button[type='button']",
      "form button"
    ],
    stop: [
      "button[aria-label*='Stop']",
      "button[aria-label*='stop']",
      "button[aria-label*='Остановить']",
      "button[aria-label*='остановить']",
      "button:has(svg polygon)",
      "button.stop-button"
    ],
    response: [
      "div.response-message",
      "div.message-bubble",
      "div.prose",
      "div[class*='message']",
      "article",
      "div.markdown"
    ]
  }
};

function getModelKey() {
  const host = window.location.host;
  if (host.includes("chatgpt")) return "chatgpt";
  if (host.includes("gemini")) return "gemini";
  if (host.includes("claude")) return "claude";
  if (host.includes("grok")) return "grok";
  return "chatgpt";
}

function findElement(selectors) {
  for (const sel of selectors) {
    try {
      const els = document.querySelectorAll(sel);
      for (const el of els) {
        // For contenteditable divs (ProseMirror), skip size check — they may have 0 height when empty
        if (el.isContentEditable) {
          const style = window.getComputedStyle(el);
          if (style.display !== "none" && style.visibility !== "hidden") return el;
        }
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).display !== "none" && window.getComputedStyle(el).visibility !== "hidden") {
          return el;
        }
      }
    } catch (e) {}
  }
  return null;
}

// Wait up to maxMs for an element to appear (for slow-loading pages like ChatGPT)
async function findElementWithRetry(selectors, maxMs = 8000) {
  const interval = 300;
  let waited = 0;
  while (waited < maxMs) {
    const el = findElement(selectors);
    if (el) return el;
    await new Promise(r => setTimeout(r, interval));
    waited += interval;
  }
  return null;
}

function isStopVisible(selectors) {
  for (const sel of selectors) {
    try {
      const el = document.querySelector(sel);
      if (el && el.offsetWidth > 0 && el.offsetHeight > 0) return true;
    } catch (e) {}
  }
  return false;
}

function getLatestResponse(selectors) {
  for (const sel of selectors) {
    try {
      const els = document.querySelectorAll(sel);
      if (els.length > 0) {
        const last = els[els.length - 1];
        const text = last.innerText || last.textContent;
        if (text && text.trim().length > 0) return text.trim();
      }
    } catch (e) {}
  }
  return "";
}

// Count response elements — detects new replies even when text equals a previous reply
function getResponseCount(selectors) {
  for (const sel of selectors) {
    try {
      const n = document.querySelectorAll(sel).length;
      if (n > 0) return n;
    } catch (e) {}
  }
  return 0;
}

// Inject Prompt Text into ContentEditable or Textarea
function setNativeValue(element, value) {
  const valueSetter = Object.getOwnPropertyDescriptor(element, 'value')?.set;
  const prototype = Object.getPrototypeOf(element);
  const prototypeValueSetter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;

  if (prototypeValueSetter && valueSetter !== prototypeValueSetter) {
    prototypeValueSetter.call(element, value);
  } else if (valueSetter) {
    valueSetter.call(element, value);
  } else {
    element.value = value;
  }
}

// Inject Prompt Text into ContentEditable (ProseMirror) or Textarea
function injectPrompt(inputEl, text) {
  inputEl.focus();

  // Method 1: ProseMirror / execCommand path (recommended for ChatGPT)
  let success = false;
  try {
    if (inputEl.isContentEditable) {
      // Clear first, then insert — this is the only reliable way with ProseMirror
      document.execCommand("selectAll", false, null);
      document.execCommand("delete", false, null);
      success = document.execCommand("insertText", false, text);
      // Fire input event so React state updates
      inputEl.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: text }));
    } else {
      inputEl.select();
      success = document.execCommand("insertText", false, text);
    }
  } catch (e) {}

  // Method 2: Native Setter + InputEvent fallback (for plain textareas)
  if (!success || (inputEl.value !== text && inputEl.textContent !== text)) {
    if (inputEl.isContentEditable) {
      // Last resort for contenteditable: clear and set text node
      inputEl.focus();
      const sel = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(inputEl);
      sel.removeAllRanges();
      sel.addRange(range);
      document.execCommand("insertText", false, text);
      inputEl.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: text }));
    } else {
      setNativeValue(inputEl, text);
      inputEl.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: text }));
    }
  }

  inputEl.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  inputEl.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
}

// Handle Messages from Extension Side Panel
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "EXECUTE_PROMPT") {
    (async () => {
      try {
        const key = getModelKey();
        const cfg = SELECTORS[key];
        // Use retry for slow-loading pages (e.g. ChatGPT ProseMirror mounts late)
        const inputEl = await findElementWithRetry(cfg.input, 8000);

        if (!inputEl) {
          sendResponse({ success: false, error: `Prompt input element not found on ${key}` });
          return;
        }

        // Snapshot BEFORE sending — text AND element count
        const baselineText  = getLatestResponse(cfg.response);
        const baselineCount = getResponseCount(cfg.response);

        // Inject text
        injectPrompt(inputEl, message.prompt);
        await new Promise(r => setTimeout(r, 600));

        // Submit prompt
        let submitBtn = findElement(cfg.submit);
        if (submitBtn && !submitBtn.disabled) {
          submitBtn.removeAttribute("disabled");
          submitBtn.setAttribute("aria-disabled", "false");
          submitBtn.click();
        } else {
          await new Promise(r => setTimeout(r, 200));
          submitBtn = findElement(cfg.submit);
          if (submitBtn) {
            submitBtn.removeAttribute("disabled");
            submitBtn.click();
          } else {
            // Send Enter keyboard events
            inputEl.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
            inputEl.dispatchEvent(new KeyboardEvent("keypress", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
            inputEl.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
            const form = inputEl.closest("form");
            if (form) {
              try { form.requestSubmit(); } catch (e) {}
            }
          }
        }

        // hasNewResponse: true when a genuinely new reply element exists.
        // Uses element COUNT as primary signal (works even when new text == old text).
        function hasNewResponse() {
          if (getResponseCount(cfg.response) > baselineCount) return true;
          // Fallback: text grew by >20 chars (flat-DOM models)
          return getLatestResponse(cfg.response).length > baselineText.length + 20;
        }

        // Phase 1: Wait up to 15s for generation to start
        for (let i = 0; i < 30; i++) {
          await new Promise(r => setTimeout(r, 500));
          if (isStopVisible(cfg.stop) || hasNewResponse()) break;
        }

        // Phase 2: Wait for generation to COMPLETE.
        //
        // PATH A — stop button model (ChatGPT, Claude, Grok):
        //   Stop appears → disappears → 3s grace → capture (requires hasNewResponse).
        //
        // PATH B — no stop button (Gemini):
        //   hasNewResponse + 3s text stability → capture.
        //   OR hasNewResponse + 8s elapsed → capture (aggressive fallback).
        let elapsed       = 0;
        let stopWasVisible = false;
        let stopGoneTick  = -1;
        let stableText    = "";
        let stableCount   = 0;
        const maxWait = 360;
        const GRACE   = 3;   // seconds after stop disappears before we try
        const STABLE  = 3;   // seconds of stable text for PATH B
        const ELAPSED = 8;   // aggressive timeout for PATH B

        while (elapsed < maxWait) {
          await new Promise(r => setTimeout(r, 1000));
          elapsed += 1;

          const stopVis     = isStopVisible(cfg.stop);
          const currentText = getLatestResponse(cfg.response);
          const newReady    = hasNewResponse();

          if (stopVis) stopWasVisible = true;
          if (stopWasVisible && !stopVis && stopGoneTick < 0) stopGoneTick = elapsed;

          // PATH A: stop button gone + grace period passed
          if (stopGoneTick >= 0 && (elapsed - stopGoneTick) >= GRACE) {
            if (newReady) {
              sendResponse({ success: true, text: getLatestResponse(cfg.response) });
              return;
            }
            // No new content yet — wait up to 15s post-stop then give up
            if ((elapsed - stopGoneTick) >= 15) {
              sendResponse({ success: true, text: currentText });
              return;
            }
          }

          // PATH B: no stop button ever seen
          if (!stopWasVisible && newReady) {
            if (currentText === stableText) {
              stableCount += 1;
            } else {
              stableCount = 1;
              stableText  = currentText;
            }
            // 3s of stable new text
            if (stableCount >= STABLE) {
              sendResponse({ success: true, text: currentText });
              return;
            }
            // 8s elapsed hard cap
            if (elapsed >= ELAPSED) {
              sendResponse({ success: true, text: currentText });
              return;
            }
          }
        }

        sendResponse({ success: true, text: getLatestResponse(cfg.response) });
      } catch (exc) {
        sendResponse({ success: false, error: String(exc) });
      }
    })();
    return true; // Keep message channel open for async response
  }
});
