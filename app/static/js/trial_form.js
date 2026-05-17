/**
 * trial_form.js
 * Computes running fee total for both AKC and AHBA trial entry forms
 * and validates the form before submit.
 *
 * Expects window.trialFormConfig to be set before this script runs:
 *   window.trialFormConfig = {
 *     eventId, formId, submitBtnId,
 *     hasEventPref   // true for AKC with two event numbers
 *   };
 *
 * Each event row is a <tr class="te-row"> with:
 *   data-te-id      = TrialEvent.id
 *   data-fee        = fee in cents
 *   data-is-test    = "true"|"false"
 *   data-avail-days = "either"|"friday"|"saturday"|"" (AKC only)
 *
 * AKC "Enter In" uses checkboxes: akc_ev1_{id} and akc_ev2_{id}
 *   - both checked → fee × 2
 *   - one checked  → fee × 1
 *   - neither      → fee × 1 (defaults to event 1)
 */
(function () {
  var cfg = window.trialFormConfig || {};
  var hasEventPref = !!cfg.hasEventPref;
  var totalDisplay = document.getElementById('total-display');

  function isRowEntered(row) {
    var isTest = row.dataset.isTest === 'true';
    if (isTest) {
      var cb = row.querySelector('input[type="checkbox"].te-enter-check');
      return cb && cb.checked;
    } else {
      var sel = row.querySelector('select.class-select');
      return sel && sel.value !== '';
    }
  }

  function rowFeeCents(row) {
    if (!isRowEntered(row)) return 0;
    var fee = parseInt(row.dataset.fee, 10) || 0;
    if (!hasEventPref) return fee;

    // AKC: count how many event checkboxes are checked
    var teId = row.dataset.teId;
    var ev1 = row.querySelector('input[name="akc_ev1_' + teId + '"]');
    var ev2 = row.querySelector('input[name="akc_ev2_' + teId + '"]');
    var count = (ev1 && ev1.checked ? 1 : 0) + (ev2 && ev2.checked ? 1 : 0);
    return fee * (count > 0 ? count : 1);  // default to 1 if neither checked
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

  // Attach change listeners to all rows
  document.querySelectorAll('tr.te-row').forEach(function (row) {
    var teId = row.dataset.teId;

    // Class select or test checkbox
    var classSelect = row.querySelector('select.class-select');
    var enterCheck = row.querySelector('input[type="checkbox"].te-enter-check');

    if (classSelect) classSelect.addEventListener('change', updateTotal);
    if (enterCheck) enterCheck.addEventListener('change', updateTotal);

    // AKC event checkboxes (akc_ev1_X, akc_ev2_X)
    if (hasEventPref) {
      var ev1 = row.querySelector('input[name="akc_ev1_' + teId + '"]');
      var ev2 = row.querySelector('input[name="akc_ev2_' + teId + '"]');
      if (ev1) ev1.addEventListener('change', updateTotal);
      if (ev2) ev2.addEventListener('change', updateTotal);
    }
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
