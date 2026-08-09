# Recovery review

Use only to review a diagnosed failure and propose a bounded recovery action.

Inputs are policy, latest-observation, last-action-result, plan-state,
execution-state, decision-state, and budget context sections.

Compare only the available diagnosis, evaluation, and recovery candidates.
Identify blockers, scope changes, and the point where human handoff is needed.
Do not invent a command, path, backend, approval, retry, or scientific parameter.
Do not treat historical approval as current permission. Return only the allowed
ActionEnvelope; a recovery that changes scope or parameters must remain a
proposal for the existing approval workflow.
