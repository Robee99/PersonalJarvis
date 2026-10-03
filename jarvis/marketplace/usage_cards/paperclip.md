---
plugin_id: paperclip
keywords: paperclip, agent, agents, company, ceo, employee, employees, delegate, assign, hand off, task, tasks, issue, issues, approval, approvals, goal, goals, inbox, team
---
Use paperclip/* tools to hand work to the agents in the user's Paperclip company and to follow it.
- Jarvis signs in with a board key, so paperclipMe (agent-only) fails. Get the company id with paperclipApiRequest GET /companies, then paperclipListAgents to find the agent by name. Never guess ids.
- To give an agent a task, use paperclipCreateIssue with a clear title and description and the agent as assignee. The agent picks it up on its next heartbeat; say the task was handed over, not that it is done.
- For "what are my agents doing", list issues by status or assignee and read the latest comments on the active ones.
- Approvals belong to the user: show them with paperclipListApprovals and never approve on the user's behalf.
