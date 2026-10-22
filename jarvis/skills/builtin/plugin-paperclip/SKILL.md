---
schema_version: "1"
name: plugin-paperclip
description: Hand tasks to the agents in the user's Paperclip company and follow their work.
when_to_use: Use when the user mentions Paperclip, or wants one of their Paperclip agents to take on a task, or asks what those agents are doing.
category: developer
plugin_id: paperclip
intent_verbs: [give, assign, hand, ask, delegate, show, list, check, gib, zeig, frag, asigna, muestra]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [paperclip, paperclip agent, paperclip agents, paperclip task, paperclip company, paperclip issue]  # i18n-allow: spoken-input vocabulary
triggers:
  - type: voice
    pattern: '(paper ?clip)'
requires_tools: [paperclip]
risk_policy:
  default_tier: ask
---

Use the connected Paperclip tools to work with the user's agent company.

- Find the company and the agent by name before acting: paperclipApiRequest GET /companies, then paperclipListAgents. A guessed id fails.
- A new task is an issue assigned to an agent. Write a clear title and the full request in the description.
- The agent starts on its next heartbeat. Say the task was handed over; never claim it is done until its status says so.
- Summarize plainly: task title, agent, status and the latest comment.
