# Kept extension

From [Kept](https://github.com/egroup-labs/kept) by E-Group Labs, commit
`c81d59f`, MIT License (`LICENSE`).

It reads ChatGPT, Claude, Gemini, Grok and Kimi chats through the owner's own
signed-in Chrome sessions and sends each as Markdown to `localhost:18241`.
Jarvis answers on that port in place of the Kept desktop app
(`jarvis/memory/wiki/kept_bridge.py`) and writes every chat into memory, one
page per conversation, under `imports/Kept/<platform>/`.

Jarvis change: automatic sync is on by default (hourly, and when Jarvis starts).

Install: chrome://extensions, Developer mode on, Load unpacked, pick this
folder, then click Connect in its popup while Jarvis runs.
