# Jarvis AI Council

From [Local AI Council](https://github.com/valentinfomin/ai-council-automation)
by valentinfomin, commit `2643af3`, MIT License (stated in its README; see
`LICENSE` here).

Jarvis changes:

- The judge can sit outside the debate: by default ChatGPT and Gemini debate
  and Claude gives the verdict, naming the winner and the final answer.
- Jarvis starts a council by opening `https://chatgpt.com/#jarvis-council=<topic>`
  (the `ai_council` tool); `content_scripts/ai_injector.js` passes the topic on.
- The verdict and the whole debate are saved to `Downloads/AI Chats/Council`
  as Markdown, which Jarvis imports into memory (`jarvis/memory/wiki/chat_sync.py`).
- English placeholder text; the `downloads` permission for the saved note.

Install: chrome://extensions, Developer mode on, Load unpacked, pick this
folder. Stay signed in to chatgpt.com, gemini.google.com and claude.ai.
