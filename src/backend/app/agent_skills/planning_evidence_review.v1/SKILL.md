# Planning evidence review

Use only for read-evidence and draft-plan work in planning lifecycle states.

Inputs are the goal, project-evidence, policy, decision-state, plan-state,
last-action-result, and budget context sections.

Identify facts supported by the supplied references, missing prerequisites, and
scientific choices that need a user decision. Do not infer missing data, read
other sections, request files, disclose paths, or propose execution or approval.

Return only an ActionEnvelope that uses the supplied typed references. For a
plan, distinguish confirmed evidence from assumptions. If evidence is missing,
request a structured decision instead of inventing a value. If this procedure
cannot be followed, return the safest allowed finish or decision action.
