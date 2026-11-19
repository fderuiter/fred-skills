Use repo-audit-accessibility-state to audit accessibility state consistency in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while correlating context from issue <issue-number>.
Inventory dialogs, tabs, menus, async status indicators, validation messages, loading states, disabled controls, focus owners, and live regions.
Map each UI interaction state to visual output, accessible role/name/value, keyboard navigation, focus restoration, and live announcements across open/close and route transitions.
Separate reproduced accessibility defects, static ARIA/focus mismatches, and refuted candidates with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.
