// Accessibility contract: hiding dialog updates aria-expanded and restores trigger focus.
export class ModalController {
  dismissOnError(dialog, trigger) {
    dialog.hidden = true;
    trigger.ariaExpanded = 'false';
    trigger.focus();
  }
}
