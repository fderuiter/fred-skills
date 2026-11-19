Use session-handoff on <repository/revision> with <finding/run artifacts> targeting <directory>, <symbol>, and issue <issue-number>.
Compact the active session into a cold-start exit handoff separating verified repository facts from unproven assumptions and open hypotheses.
Record exact revision and dirty state, completed work, touched versus untouched paths, executed commands, and refuted dead ends so the next window avoids rediscovery.
Write `session-handoff.json` validated against `assets/session-handoff.schema.json` alongside a concise continuation summary with one concrete next step.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application source files, stage commits, or publish remote issues without explicit user authorization.
