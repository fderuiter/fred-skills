# Accessibility State Consistency: Specialist Method and Examples

## Auditing Visual State vs. Assistive-Technology Tree Synchronization

1. Trace interactive UI transitions—drawers, modals, comboboxes, async form errors, toast alerts, and disabled/loading buttons—comparing rendered DOM/CSS visibility against ARIA state (`aria-expanded`, `aria-hidden`, `inert`), accessible names, and keyboard focus location.
2. Flag split-state bugs where CSS hides a surface (`opacity: 0` or `max-height: 0` without `inert`/`hidden`) while keyboard `Tab` focus remains trapped inside invisible controls or trigger buttons keep `aria-expanded="true"`.
3. Prefer native semantic HTML controls (`<dialog>`, `<button>`, `<details>`) and single-source-of-truth state derivation over redundant manual ARIA attributes that drift during error recovery.

## Failure Trace vs. Safe Control: Visually Hidden Drawer Retaining Focus and `aria-expanded`

```tsx
// Defect trace: Escape key hides drawer visually via CSS class while leaving aria-expanded="true" and focus inside hidden button
function SettingsDrawerBuggy() {
  const [expanded, setExpanded] = useState(false);
  const [cssHidden, setCssHidden] = useState(true);
  return (
    <>
      <button aria-expanded={expanded} onClick={() => { setExpanded(true); setCssHidden(false); }}>Settings</button>
      <div className={cssHidden ? "opacity-0 pointer-events-none" : "opacity-100"} onKeyDown={(e) => {
        if (e.key === "Escape") setCssHidden(true); // expanded stays true; keyboard Tab still enters invisible drawer!
      }}>
        <button>Delete Workspace</button>
      </div>
    </>
  );
}

// Safe control: derive visibility and aria-expanded from one boolean, unmount or mark inert when closed, and restore trigger focus
function SettingsDrawerSafe() {
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const close = () => {
    setIsOpen(false);
    triggerRef.current?.focus();
  };
  return (
    <>
      <button ref={triggerRef} aria-expanded={isOpen} onClick={() => setIsOpen(true)}>Settings</button>
      {isOpen && (
        <div role="dialog" aria-label="Settings" onKeyDown={(e) => e.key === "Escape" && close()}>
          <button>Delete Workspace</button>
        </div>
      )}
    </>
  );
}
```

## Accessibility-State `audit.json` Deliverable Example

Validate this accessibility-state ledger with `python3 <this-skill>/scripts/validate_audit.py accessibility-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "accessibility-state-sample",
  "repository": {
    "identity": "acme/admin-dashboard",
    "revision": "62de84a",
    "dirty": false,
    "snapshot": "git-tree:62de84a"
  },
  "scope": [
    "src/components/SettingsDrawer.tsx"
  ],
  "coverage": [
    {
      "topic": "accessibility-state",
      "scope": "src/components/SettingsDrawer.tsx",
      "status": "inspected",
      "evidence_ids": [
        "E-236"
      ],
      "notes": "Traced open, Escape-dismiss, keyboard Tab order, and aria-expanded across SettingsDrawer.",
      "next_action": "Hand off duplicated UI state authority review to repo-audit-sources-of-truth if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-236",
      "dimension": "accessibility-state",
      "scope": "SettingsDrawer Escape dismissal",
      "intended": "Dismissing SettingsDrawer removes its controls from tab order, sets aria-expanded='false', and returns focus to Settings button.",
      "observed": "Pressing Escape sets opacity-0 pointer-events-none while keeping DOM subtree tabbable, focus inside hidden drawer, and aria-expanded='true'.",
      "enforcement": "desynchronized expanded vs cssHidden state in SettingsDrawer.tsx",
      "evidence_ids": [
        "E-236"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-236",
      "path": "src/components/SettingsDrawer.tsx",
      "line_start": 14,
      "line_end": 28,
      "symbol": "SettingsDrawer",
      "context": "Escape handler updates cssHidden=true (applying opacity-0 pointer-events-none) without updating expanded or moving focus."
    }
  ],
  "findings": [
    {
      "id": "F-236",
      "topic": "accessibility-state",
      "tags": [
        "a11y",
        "focus-management"
      ],
      "title": "Escape-dismissed SettingsDrawer keeps destructive button focusable and aria-expanded true",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-236"
      ],
      "evidence_ids": [
        "E-236"
      ],
      "preconditions": "Keyboard or screen-reader user opens SettingsDrawer and presses Escape to close it.",
      "sequence": [
        "User activates Settings trigger (expanded=true, cssHidden=false) and focuses inside drawer.",
        "User presses Escape; onKeyDown sets cssHidden=true ('opacity-0 pointer-events-none').",
        "Drawer disappears visually, yet Enter/Space still activates hidden 'Delete Workspace' button and trigger announces expanded."
      ],
      "expected": "Closed drawer unmounts (or sets hidden/inert), sets aria-expanded=false, and restores focus to triggerRef.",
      "actual": "Invisible destructive action remains focused and operable via keyboard.",
      "impact": "Keyboard and screen-reader users lose focus visibility and can trigger hidden destructive controls.",
      "root_cause": "Visual opacity state was toggled independently of semantic open state and DOM focus.",
      "counterevidence": "Pointer-only mouse clicks are blocked by pointer-events-none.",
      "remedy": "Collapse expanded and cssHidden into single isOpen state, conditionally render dialog, and restore trigger focus on close.",
      "acceptance": [
        "Pressing Escape unmounts dialog, sets trigger aria-expanded='false', and places document.activeElement on trigger."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static JSX and keyboard event trace verified in SettingsDrawer.tsx."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
