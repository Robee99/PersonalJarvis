document.addEventListener("DOMContentLoaded", () => {
  const btnRun = document.getElementById("btnRun");
  const taskPrompt = document.getElementById("taskPrompt");
  const debateRounds = document.getElementById("debateRounds");
  const chairmanSelect = document.getElementById("chairmanSelect");

  const progressText = document.getElementById("progressText");
  const consensusCard = document.getElementById("consensusCard");
  const consensusText = document.getElementById("consensusText");

  // Follow-up panel
  const followUpSection  = document.getElementById("followUpSection");
  const followUpPrompt   = document.getElementById("followUpPrompt");
  const btnFollowUp      = document.getElementById("btnFollowUp");
  const followUpStatus   = document.getElementById("followUpStatus");

  // New Chat Modal elements
  const btnNewChat        = document.getElementById("btnNewChat");
  const newChatModal      = document.getElementById("newChatModal");
  const btnCancelNewChat  = document.getElementById("btnCancelNewChat");
  const btnConfirmNewChat = document.getElementById("btnConfirmNewChat");

  let allResults = { candidateLabels: {}, mapping: {} };

  // Shared state — set by runCouncil, reused by follow-up
  let sessionTabIds   = {};   // { chatgpt: tabId, gemini: tabId, ... }
  let sessionModels   = [];   // active model keys
  let sessionChairman = "";  // active chairman key
  let followUpCount   = 0;

  function getChosenChairman(modelsList) {
    if (!modelsList || !modelsList.length) return "";
    const sel = chairmanSelect ? chairmanSelect.value : "auto";
    // Jarvis: the judge may sit outside the debate (ChatGPT vs Gemini, Claude judges).
    if (sel !== "auto") {
      return sel;
    }
    return modelsList[0];
  }

  // ── STATE PERSISTENCE (chrome.storage.local) ───────────────────
  function saveState() {
    const checkedModels = Array.from(document.querySelectorAll(".model-chips input:checked")).map(cb => cb.value);
    const state = {
      taskPrompt: taskPrompt.value,
      checkedModels,
      chairmanSelect: chairmanSelect ? chairmanSelect.value : "auto",
      debateRounds: debateRounds.value,
      consensusText: consensusText.textContent,
      consensusLabel: consensusCard.querySelector(".label") ? consensusCard.querySelector(".label").textContent : "",
      consensusVisible: consensusCard.style.display !== "none",
      followUpVisible: followUpSection.style.display !== "none",
      followUpCount,
      allResults,
      sessionModels,
      sessionChairman
    };
    chrome.storage.local.set({ council_session: state });
  }

  function restoreState() {
    chrome.storage.local.get("council_session", (res) => {
      if (chrome.runtime.lastError || !res || !res.council_session) return;
      const s = res.council_session;

      if (s.taskPrompt !== undefined) taskPrompt.value = s.taskPrompt;
      if (s.chairmanSelect !== undefined && chairmanSelect) chairmanSelect.value = s.chairmanSelect;
      if (s.debateRounds !== undefined && debateRounds) debateRounds.value = s.debateRounds;

      if (s.checkedModels && Array.isArray(s.checkedModels)) {
        document.querySelectorAll(".model-chips input[type='checkbox']").forEach(cb => {
          cb.checked = s.checkedModels.includes(cb.value);
        });
      }

      if (s.consensusText && s.consensusVisible) {
        consensusText.textContent = s.consensusText;
        if (s.consensusLabel && consensusCard.querySelector(".label")) {
          consensusCard.querySelector(".label").textContent = s.consensusLabel;
        }
        consensusCard.style.display = "flex";
      }

      if (s.followUpVisible) {
        followUpSection.style.display = "flex";
      }

      if (s.followUpCount !== undefined) followUpCount = s.followUpCount;
      if (s.allResults) allResults = s.allResults;
      if (s.sessionModels) sessionModels = s.sessionModels;
      if (s.sessionChairman) sessionChairman = s.sessionChairman;
    });
  }

  // Restore on load
  restoreState();

  // Auto-save UI input changes
  taskPrompt.addEventListener("input", saveState);
  if (chairmanSelect) chairmanSelect.addEventListener("change", saveState);
  if (debateRounds) debateRounds.addEventListener("change", saveState);
  document.querySelectorAll(".model-chips input").forEach(cb => {
    cb.addEventListener("change", saveState);
  });

  // ── NEW CHAT MODAL HANDLERS ──────────────────────────────────────
  if (btnNewChat) {
    btnNewChat.addEventListener("click", () => {
      newChatModal.style.display = "flex";
    });
  }

  if (btnCancelNewChat) {
    btnCancelNewChat.addEventListener("click", () => {
      newChatModal.style.display = "none";
    });
  }

  if (btnConfirmNewChat) {
    btnConfirmNewChat.addEventListener("click", () => {
      chrome.storage.local.remove("council_session");
      taskPrompt.value = "";
      consensusText.textContent = "";
      consensusCard.style.display = "none";
      followUpSection.style.display = "none";
      progressText.style.display = "none";
      followUpStatus.style.display = "none";
      followUpPrompt.value = "";
      followUpCount = 0;
      sessionModels = [];
      sessionChairman = "";
      allResults = { candidateLabels: {}, mapping: {} };
      newChatModal.style.display = "none";
    });
  }

  // --- Tab Management ---
  async function getModelTabs() {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: "QUERY_TABS" }, (response) => {
        if (chrome.runtime.lastError) reject(chrome.runtime.lastError);
        else if (response && response.success) resolve(response.modelTabs);
        else reject(new Error("Failed to query tabs"));
      });
    });
  }

  async function ensureScriptInjected(tabId) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content_scripts/ai_injector.js"]
      });
    } catch (e) {
      console.warn("Script injection notice:", e);
    }
    await new Promise(r => setTimeout(r, 300));
  }

  async function ensureTab(modelKey) {
    const tabs = await getModelTabs();
    if (tabs[modelKey]) {
      await ensureScriptInjected(tabs[modelKey]);
      return tabs[modelKey];
    }
    const urls = {
      chatgpt: "https://chatgpt.com",
      gemini: "https://gemini.google.com/app",
      claude: "https://claude.ai",
      grok: "https://grok.com"
    };
    const tabId = await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: "OPEN_TAB", url: urls[modelKey] }, (resp) => {
        if (resp && resp.success) resolve(resp.tabId);
        else reject(new Error(`Failed to open tab for ${modelKey}`));
      });
    });
    await new Promise(r => setTimeout(r, 3500));
    await ensureScriptInjected(tabId);
    return tabId;
  }

  async function sendTabPrompt(tabId, promptText) {
    await ensureScriptInjected(tabId);
    return new Promise((resolve, reject) => {
      function attemptSend(retryCount = 0) {
        chrome.tabs.sendMessage(tabId, { type: "EXECUTE_PROMPT", prompt: promptText }, (resp) => {
          if (chrome.runtime.lastError) {
            if (retryCount < 2) {
              setTimeout(() => attemptSend(retryCount + 1), 1500);
            } else {
              reject(new Error(`Connection failed: ${chrome.runtime.lastError.message}. Please reload the AI tab.`));
            }
          } else if (resp && resp.success) {
            resolve(resp.text);
          } else {
            reject(new Error(resp ? resp.error : "Failed to execute prompt in tab"));
          }
        });
      }
      attemptSend();
    });
  }

  function applyLanguage(promptText) {
    return promptText; // No forced language — model responds in task language
  }

  // --- Main Council Runner ---
  btnRun.addEventListener("click", async () => {
    const prompt = taskPrompt.value.trim();
    if (!prompt) { alert("Please enter a task description prompt."); return; }

    const selectedModels = Array.from(document.querySelectorAll(".model-chips input:checked")).map(cb => cb.value);
    if (selectedModels.length === 0) { alert("Please select at least one AI model."); return; }

    btnRun.disabled = true;
    progressText.style.display = "block";
    progressText.textContent = "Initializing council session...";

    // Reset cards
    consensusCard.style.display = "none";
    consensusText.textContent = "";
    allResults = { candidateLabels: {}, mapping: {} };

    try {
      await runCouncil(prompt, selectedModels, parseInt(debateRounds.value));
    } catch (exc) {
      progressText.textContent = `⚠️ Error: ${exc.message || exc}`;
      btnRun.disabled = false;
    }
  });

  async function runCouncil(task, models, rounds) {
    // Ensure all tabs ready
    const modelTabIds = {};
    for (const m of models) {
      progressText.textContent = `🔌 Connecting to ${m.toUpperCase()}...`;
      modelTabIds[m] = await ensureTab(m);
    }

    // Build anonymization map
    models.forEach((key, idx) => {
      const label = `Candidate ${idx + 1}`;
      allResults.candidateLabels[key] = label;
      allResults.mapping[label] = key.toUpperCase();
    });

    // ── STAGE 1: PROPOSALS ──────────────────────────────────────
    const proposals = {};
    for (const m of models) {
      progressText.textContent = `📝 [Stage 1] Querying ${m.toUpperCase()}...`;
      const text = await sendTabPrompt(modelTabIds[m], applyLanguage(task));
      proposals[m] = text;
    }

    // ── STAGE 2: PEER DEBATES ────────────────────────────────────
    let lastResponses = { ...proposals };

    for (let r = 1; r <= rounds; r++) {
      const roundCritiques = {};

      for (const reviewer of models) {
        let peerText = "";
        for (const target of models) {
          if (target !== reviewer) {
            const label = allResults.candidateLabels[target];
            peerText += `\n--- ${label} ---\n${lastResponses[target]}\n`;
          }
        }

        const rawDebate =
          `Please provide an objective evaluation of these proposed solutions:\n` +
          `Task: "${task}"\n\n` +
          `Candidate Solutions:\n${peerText}\n\n` +
          `Instructions:\n1. Identify factual flaws or hallucinations.\n2. Highlight strong points.\n3. Provide your refined solution.`;

        progressText.textContent = `🔍 [Round ${r}] ${reviewer.toUpperCase()} reviewing peers...`;
        const critiqueText = await sendTabPrompt(modelTabIds[reviewer], applyLanguage(rawDebate));
        roundCritiques[reviewer] = critiqueText;
      }

      lastResponses = roundCritiques;
    }

    // ── STAGE 3: CONSENSUS ───────────────────────────────────────
    progressText.textContent = "⚖️ [Stage 3] Chairman synthesizing consensus...";

    const chairmanKey = getChosenChairman(models);
    if (!modelTabIds[chairmanKey]) {
      progressText.textContent = `🔌 Connecting to the judge, ${chairmanKey.toUpperCase()}...`;
      modelTabIds[chairmanKey] = await ensureTab(chairmanKey);
    }
    let historySummary = "";
    models.forEach(k => {
      historySummary += `\n[${allResults.candidateLabels[k]} Review]:\n${lastResponses[k]}\n`;
    });

    const judgeOnly = !models.includes(chairmanKey);
    const rawConsensus = judgeOnly ? judgePrompt(task, models, proposals, historySummary) :
      `You are acting as Chairman of the AI Council.\n` +
      `Task: "${task}"\n\n` +
      `Deliberation History:\n${historySummary}\n\n` +
      `Issue the COMPLETE Executive Board Consensus Resolution. Do NOT cut the response short.\n` +
      `Required sections:\n` +
      `1. Final Agreed Solution (complete and detailed)\n` +
      `2. Key Risks & Eliminated Flaws\n` +
      `3. Executive Summary & Action Plan.`;

    const finalConsensus = await sendTabPrompt(modelTabIds[chairmanKey], applyLanguage(rawConsensus));

    // Show Chairman Consensus card
    consensusCard.style.display = "flex";
    consensusCard.querySelector(".label").textContent = `🏆 Chairman Consensus Resolution (${chairmanKey.toUpperCase()})`;
    consensusText.textContent = finalConsensus;

    progressText.textContent = "✅ Deliberation Complete!";
    btnRun.disabled = false;
    saveVerdict(task, models, chairmanKey, proposals, lastResponses, finalConsensus);

    // Save session state for follow-up use
    sessionTabIds   = modelTabIds;
    sessionModels   = models;
    sessionChairman = chairmanKey;
    followUpCount   = 0;

    // Reveal follow-up panel with slide-in animation
    followUpSection.style.display = "flex";
    followUpSection.scrollIntoView({ behavior: "smooth", block: "nearest" });

    saveState();
  }

  // ── FOLLOW-UP HANDLER (Mini-Council) ──────────────────────────
  btnFollowUp.addEventListener("click", async () => {
    const q = followUpPrompt.value.trim();
    if (!q) return;

    // Get models currently checked in the UI
    const activeChecked = Array.from(document.querySelectorAll(".model-chips input:checked")).map(cb => cb.value);

    if (activeChecked.length === 0) {
      followUpStatus.style.display = "block";
      followUpStatus.textContent = "⚠️ Please select at least one AI model checkbox above.";
      return;
    }

    btnFollowUp.disabled = true;
    followUpStatus.style.display = "block";

    // Ensure tabs & labels exist for all currently checked models
    for (const m of activeChecked) {
      followUpStatus.textContent = `🔌 Connecting to ${m.toUpperCase()}...`;
      try {
        sessionTabIds[m] = await ensureTab(m);
        if (!allResults.candidateLabels[m]) {
          const count = Object.keys(allResults.candidateLabels).length + 1;
          const label = `Candidate ${count}`;
          allResults.candidateLabels[m] = label;
          allResults.mapping[label] = m.toUpperCase();
        }
      } catch (err) {
        followUpStatus.textContent = `⚠️ Failed to connect to ${m.toUpperCase()}: ${err.message || err}`;
        btnFollowUp.disabled = false;
        return;
      }
    }

    sessionModels = activeChecked;
    sessionChairman = getChosenChairman(activeChecked);

    const target = document.querySelector("input[name='followTarget']:checked").value;
    let targetModels = activeChecked;
    if (target === "chairman") {
      targetModels = [sessionChairman];
    }

    followUpCount += 1;

    try {
      await runFollowUpCouncil(q, targetModels);
    } catch (err) {
      followUpStatus.textContent = `⚠️ Error: ${err.message || err}`;
    }

    followUpPrompt.value = "";
    btnFollowUp.disabled = false;
  });

  async function runFollowUpCouncil(question, models) {
    // ── Step 1: Each model answers clarification in background ──────
    const answers = {};

    for (const m of models) {
      followUpStatus.textContent = `💬 [Step 1] ${m.toUpperCase()} answering...`;
      const resp = await sendTabPrompt(sessionTabIds[m], question);
      answers[m] = resp;
    }

    // ── Step 2: Cross-review in background (only if >1 model) ────────
    let lastResponses = { ...answers };

    if (models.length > 1) {
      const critiques = {};

      for (const reviewer of models) {
        let peerSummary = "";
        for (const target of models) {
          if (target !== reviewer) {
            const label = allResults.candidateLabels[target] || target.toUpperCase();
            peerSummary += `\n--- ${label} ---\n${answers[target]}\n`;
          }
        }
        const reviewPrompt =
          `The council received a follow-up question: "${question}"\n\n` +
          `Other council members answered:\n${peerSummary}\n\n` +
          `Evaluate their answers, correct any errors, highlight strengths, ` +
          `and provide your final refined answer to the follow-up question.`;

        followUpStatus.textContent = `🔍 [Step 2] ${reviewer.toUpperCase()} reviewing peers...`;
        const critique = await sendTabPrompt(sessionTabIds[reviewer], reviewPrompt);
        critiques[reviewer] = critique;
      }
      lastResponses = critiques;
    }

    // ── Step 3: Chairman rewrites consensus ───────────────────
    followUpStatus.textContent = `⚖️ [Step 3] Chairman rewriting consensus...`;

    const chairKey = getChosenChairman(models);
    if (!sessionTabIds[chairKey]) sessionTabIds[chairKey] = await ensureTab(chairKey);
    let refined = "";
    models.forEach(m => {
      const label = allResults.candidateLabels[m] || m.toUpperCase();
      refined += `\n[${label}]:\n${lastResponses[m]}\n`;
    });

    const consensusPrompt =
      `You are the Chairman of the AI Council.\n` +
      `The council was asked a follow-up clarification: "${question}"\n\n` +
      `Council members' refined positions after peer review:\n${refined}\n\n` +
      `Rewrite and issue the COMPLETE UPDATED Executive Consensus Resolution. ` +
      `Do NOT cut the response short.\n` +
      `Required sections:\n` +
      `1. Updated Final Answer to the Clarification\n` +
      `2. Revised Key Points & Corrections\n` +
      `3. Updated Executive Summary & Action Plan.`;

    const newConsensus = await sendTabPrompt(sessionTabIds[chairKey], consensusPrompt);

    // Update the Chairman Consensus card directly
    consensusText.textContent = newConsensus;
    consensusCard.style.borderColor = "rgba(6, 182, 212, 0.55)";
    consensusCard.querySelector(".label").textContent =
      `🏆 Chairman Consensus Resolution (${chairKey.toUpperCase()}) · Follow-up #${followUpCount}`;
    consensusCard.scrollIntoView({ behavior: "smooth", block: "nearest" });

    followUpStatus.textContent = `✅ Consensus updated for follow-up #${followUpCount}!`;

    saveState();
  }

  // ── Jarvis additions ─────────────────────────────────────────
  // The judge sees both opening answers and the cross-critiques, and names a winner.
  function judgePrompt(task, models, proposals, historySummary) {
    let openings = "";
    models.forEach(k => {
      openings += `\n[${allResults.candidateLabels[k]} opening answer]:\n${proposals[k]}\n`;
    });
    return (
      `You are the judge of a debate between AI assistants. You did not take part.\n` +
      `Topic: "${task}"\n\n` +
      `Opening answers:\n${openings}\n` +
      `Their critiques of each other:\n${historySummary}\n\n` +
      `Give your verdict:\n` +
      `1. Winner: which candidate argued better, and why.\n` +
      `2. Mistakes: what each candidate got wrong or left out.\n` +
      `3. Final answer: the complete answer to the topic that you stand behind.`
    );
  }

  // The whole debate as a Markdown note in Downloads/AI Chats/Council, which
  // Jarvis imports into memory by itself.
  function saveVerdict(task, models, judge, proposals, critiques, verdict) {
    try {
      const now = new Date();
      const day = now.toISOString().slice(0, 10);
      const short = task.replace(/[\\/:*?"<>|\r\n\t]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 60) || "Council";
      const names = models.map(k => `${allResults.candidateLabels[k]} = ${k.toUpperCase()}`).join(", ");
      const parts = [
        "---",
        `title: ${JSON.stringify("Council: " + short)}`,
        "platform: council",
        `created: ${now.toISOString()}`,
        `members: [${models.join(", ")}]`,
        `judge: ${judge}`,
        "tags: [ai-council]",
        "---",
        "",
        `# Council: ${short}`,
        "",
        `**Topic:** ${task}`,
        "",
        `**Debaters:** ${names}. **Judge:** ${judge.toUpperCase()}.`,
        "",
        `## Verdict (${judge.toUpperCase()})`,
        "",
        verdict,
        "",
      ];
      models.forEach(k => {
        parts.push(`## ${k.toUpperCase()}: opening answer`, "", proposals[k] || "", "");
        parts.push(`## ${k.toUpperCase()}: critique`, "", critiques[k] || "", "");
      });
      const blob = new Blob([parts.join("\n")], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      chrome.downloads.download(
        { url, filename: `AI Chats/Council/${day} ${short}.md`, conflictAction: "uniquify", saveAs: false },
        () => setTimeout(() => URL.revokeObjectURL(url), 60000)
      );
    } catch (exc) {
      console.warn("Could not save the council verdict:", exc);
    }
  }

  // Started by Jarvis: side_panel.html?topic=...&judge=claude&auto=1
  (function startFromJarvis() {
    const params = new URLSearchParams(window.location.search);
    const topic = (params.get("topic") || "").trim();
    if (!topic) return;
    const judge = params.get("judge") || "claude";
    const members = (params.get("members") || "chatgpt,gemini").split(",");
    setTimeout(() => {
      taskPrompt.value = topic.slice(0, 4000);
      document.querySelectorAll(".model-chips input[type='checkbox']").forEach(cb => {
        cb.checked = members.includes(cb.value);
      });
      if (chairmanSelect && [...chairmanSelect.options].some(o => o.value === judge)) {
        chairmanSelect.value = judge;
      }
      saveState();
      if (params.get("auto") === "1") btnRun.click();
    }, 400);
  })();

  // Ctrl+Enter shortcut inside follow-up textarea
  followUpPrompt.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      btnFollowUp.click();
    }
  });
});
