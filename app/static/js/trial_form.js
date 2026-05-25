/**
 * trial_form.js
 * Computes running fee total for both AKC and AHBA trial entry forms
 * and validates the form before submit.
 *
 * Expects window.trialFormConfig to be set before this script runs:
 *   window.trialFormConfig = { eventId, formId, submitBtnId };
 *
 * Per-row fee = fee_cents × (1 if E1 entered else 0) + (1 if E2 entered else 0).
 * E1/E2 are detected by data-side="e1" / data-side="e2" on the row's class
 * select or test-entry checkbox.
 *
 * Each event row is a <tr class="te-row"> with:
 *   data-te-id      = TrialEvent.id
 *   data-fee        = fee in cents
 *   data-is-test    = "true"|"false"
 *   data-avail-days = "either"|"friday"|"saturday"|"" (AKC only)
 */
(function () {
  var cfg = window.trialFormConfig || {};
  var totalDisplay = document.getElementById('total-display');

  // A side ("e1" or "e2") is "entered" for a row if a class is selected (trial
  // class) or the Enter checkbox is checked (test class).
  function isSideEntered(row, side) {
    var inputs = row.querySelectorAll('[data-side="' + side + '"]');
    for (var i = 0; i < inputs.length; i++) {
      var el = inputs[i];
      if (el.tagName === 'SELECT') {
        if (el.value !== '') return true;
      } else if (el.type === 'checkbox') {
        if (el.checked) return true;
      }
    }
    return false;
  }

  function rowFeeCents(row) {
    var fee = parseInt(row.dataset.fee, 10) || 0;
    var count = (isSideEntered(row, 'e1') ? 1 : 0) + (isSideEntered(row, 'e2') ? 1 : 0);
    return fee * count;
  }

  function computeFeeCents() {
    var total = 0;
    document.querySelectorAll('tr.te-row').forEach(function (row) {
      total += rowFeeCents(row);
    });
    return total;
  }

  function updateTotal() {
    if (totalDisplay) {
      totalDisplay.textContent = (computeFeeCents() / 100).toFixed(2);
    }
  }

  // Attach change listeners to every side-tagged class select / enter checkbox.
  document.querySelectorAll('tr.te-row [data-side]').forEach(function (el) {
    el.addEventListener('change', updateTotal);
  });

  updateTotal();

  // Pre-submit validation: prevent submitting an empty selection.
  var form = document.getElementById(cfg.formId || 'entry-form');
  if (form) {
    form.addEventListener('submit', function (ev) {
      if (computeFeeCents() === 0) {
        ev.preventDefault();
        alert('Please select at least one class or event before continuing to payment.');
      }
    });
  }
})();
