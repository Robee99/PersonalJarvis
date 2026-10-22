"""Local image, audio and video understanding, offered to Hermes as MCP tools.

Hermes (ADR-0042) runs every turn on whatever model the owner picked, and
most of those models read text only. This package gives any of them three
read-only tools on Jarvis's own MCP server (``jarvis/mcp/jarvis_tools_server.py``):

* ``media-describe-image``: a local picture goes to a vision model on this
  computer (llama.cpp ``llama-server`` started with ``--mmproj``) and the
  description comes back as text.
* ``media-transcribe-audio``: a local recording is transcribed by
  faster-whisper (already part of Jarvis's local voice), or by an audio-capable
  ``llama-server`` model when Whisper is not set up. A question about the
  sound itself goes to that audio model.
* ``media-understand-video``: frames sampled from a local video go to the
  vision model together with the transcript of its sound track.

The rules this package keeps: every file is read from this computer and every
model call goes to a loopback address. A non-local endpoint is refused, never
used, and a missing local backend is an honest error that says how to start
one. Nothing here ever falls back to a cloud or paid service. A model that
calls these tools gets text back. That does not make the model itself
multimodal.

Import-light (AP-26): Pillow, PyAV and faster-whisper load inside the calls.
The recommended backend is llama.cpp's ``llama-server`` with Gemma 4 E4B and its
projector (``--mmproj``): one Apache-2.0 model that takes images and audio.
"""

from __future__ import annotations

__all__: list[str] = []
