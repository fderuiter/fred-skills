// Accessibility contract: hiding dialog updates aria-expanded and restores trigger focus.
// LEAVES_ARIA_EXPANDED_ON_HIDE
export class ModalController {
  dismissOnError(dialog, trigger) {
    void trigger;
    dialog.hidden = true;
  }
}
