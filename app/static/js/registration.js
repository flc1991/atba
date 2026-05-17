/**
 * registration.js
 * Shared PayPal Buttons initializer for Fun Run, Smart Dog Day, and Trial Entry forms.
 *
 * Usage:
 *   initPayPal({
 *     createOrderUrl:      '/payments/create-order',
 *     captureOrderUrl:     '/payments/capture-order',
 *     eventId:             123,
 *     eventType:           'registration' | 'entry',
 *     amountCents:         2500,          // static number OR function returning number
 *     formId:              'reg-form',
 *     orderIdFieldId:      'paypal_order_id',
 *     submitBtnId:         'submit-btn',
 *     validateBeforePayment: function() { return true; },  // optional
 *   });
 */
function initPayPal(config) {
  var getAmount = typeof config.amountCents === 'function'
    ? config.amountCents
    : function() { return config.amountCents; };

  paypal.Buttons({
    createOrder: function(data, actions) {
      // Optional pre-payment validation (e.g., check selections)
      if (config.validateBeforePayment && !config.validateBeforePayment()) {
        return actions.reject();
      }

      var amount = getAmount();
      if (!amount || amount <= 0) {
        alert('Please complete your selections before proceeding to payment.');
        return actions.reject();
      }

      return fetch(config.createOrderUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_id: config.eventId,
          event_type: config.eventType,
          amount_cents: amount,
        }),
      })
        .then(function(r) {
          if (!r.ok) throw new Error('Server error creating order');
          return r.json();
        })
        .then(function(data) { return data.order_id; });
    },

    onApprove: function(data, actions) {
      return fetch(config.captureOrderUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order_id: data.orderID }),
      })
        .then(function(r) {
          if (!r.ok) throw new Error('Server error capturing order');
          return r.json();
        })
        .then(function(result) {
          if (result.status === 'COMPLETED') {
            document.getElementById(config.orderIdFieldId).value = data.orderID;
            var submitBtn = document.getElementById(config.submitBtnId);
            submitBtn.style.display = '';
            submitBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
            var ppContainer = document.getElementById('paypal-button-container');
            if (ppContainer) ppContainer.style.display = 'none';
          } else {
            alert('Payment status: ' + result.status + '. Please try again or contact support.');
          }
        });
    },

    onError: function(err) {
      console.error('PayPal error:', err);
      alert('A payment error occurred. Please try again or contact support.');
    },
  }).render('#paypal-button-container');
}
