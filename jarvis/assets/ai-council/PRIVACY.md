# Privacy Policy — Local AI Council

**Last updated: October 7, 2026**

## Overview

Local AI Council ("the Extension") is a browser extension that orchestrates multi-AI debates across ChatGPT, Gemini, Claude, and Grok directly within your browser. This policy describes how the Extension handles your data.

---

## Data Collection

**The Extension does not collect any user data.**

We do not collect, transmit, store on external servers, or share:
- Personally identifiable information
- Authentication credentials or passwords
- Financial information
- Location data
- Web browsing history
- User activity logs (clicks, keystrokes, scroll position)
- Chat messages or prompt content

---

## Local Storage

The Extension stores the following data **exclusively on your local device** using `chrome.storage.local`:

| Data | Purpose |
|------|---------|
| Task prompt text | Restore panel state when reopened |
| Selected AI models | Restore user preferences |
| Chairman selection | Restore user preferences |
| Debate round setting | Restore user preferences |
| Consensus result text | Restore last session result |

This data **never leaves your device**. It is not synced to any cloud service, not sent to any server, and not accessible to any third party.

---

## Third-Party Services

The Extension interacts with the following third-party AI services **solely through your existing authenticated browser sessions**:

- [ChatGPT](https://chatgpt.com) by OpenAI
- [Gemini](https://gemini.google.com) by Google
- [Claude](https://claude.ai) by Anthropic
- [Grok](https://grok.com) by xAI

The Extension submits prompts and reads responses on your behalf using your own logged-in sessions. It does not access, store, or transmit your credentials or any account information.

Please refer to each provider's own privacy policy for information on how they handle your data.

---

## Permissions Used

| Permission | Reason |
|-----------|--------|
| `host_permissions` (4 AI domains) | Inject content script to submit prompts and read responses |
| `scripting` | Inject content script into AI chat tabs at runtime |
| `tabs` | Detect open AI tabs and open new ones if needed |
| `sidePanel` | Display the control panel as a browser side panel |
| `storage` | Save session state locally on your device |

---

## Changes to This Policy

If this policy is updated, the "Last updated" date above will be revised. Continued use of the Extension after changes constitutes acceptance.

---

## Contact

For questions about this privacy policy, please open an issue on the [GitHub repository](https://github.com/valentinfomin/ai-council-automation).
