(function() {
  function getCookie(name) {
    var match = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()\\[\\]\/\\+^])/g, '\\$1') + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : null;
  }

  function fetchCartAttributes() {
    console.log('Fetching current cart attributes');
    return fetch('/cart.js', { credentials: 'same-origin' })
      .then(function(res) { return res.json(); })
      .then(function(cart) {
        return cart && cart.attributes ? cart.attributes : {};
      });
  }

  function updateCartAttributes(attrs) {
    console.log('Updating cart attributes', attrs);
    return fetch('/cart/update.js', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ attributes: attrs })
    });
  }

  function submitCheckout(form) {
    console.log('Submitting checkout');
    form.submit();
  }

  function navigateTo(url) {
    console.log('Navigating to', url);
    window.location.href = url;
  }

  function ensureAttributesThen(proceed) {
    var uuid = getCookie('uuid');
    var storeID = getCookie('storeID');
    if (!uuid || !storeID) {
      console.log('Missing uuid or storeID cookie, proceeding without update');
      return proceed();
    }

    fetchCartAttributes()
      .then(function(attrs) {
        if (attrs.uuid === uuid && attrs.storeID === storeID) {
          console.log('Cart attributes already up to date');
          return proceed();
        }

        attrs.uuid = uuid;
        attrs.storeID = storeID;
        return updateCartAttributes(attrs)
          .catch(function(err) {
            console.warn('Failed to update cart attributes', err);
          })
          .finally(function() {
            proceed();
          });
      })
      .catch(function(err) {
        console.warn('Failed to load cart attributes', err);
        proceed();
      });
  }

  function handleCheckout(event) {
    event.preventDefault();
    var form = event.currentTarget.form;
    console.log('Checkout button clicked');
    ensureAttributesThen(function() {
      submitCheckout(form);
    });
  }

  function handleCheckoutUrl(url) {
    console.log('Intercepted checkout navigation');
    ensureAttributesThen(function() {
      navigateTo(url);
    });
  }

  function warnDynamicCheckout(form) {
    var dynamic = form.querySelector('.additional-checkout-buttons, .shopify-payment-button');
    if (dynamic) {
      console.warn('Dynamic checkout buttons detected; they bypass the cart form and may skip attribute persistence.');
    }
  }

  function init() {
    var form = document.querySelector('form[action="/cart"]');
    if (!form) {
      return;
    }

    warnDynamicCheckout(form);

    var checkoutButtons = form.querySelectorAll('[name="checkout"]');
    checkoutButtons.forEach(function(btn) {
      btn.addEventListener('click', handleCheckout);
    });

    document.addEventListener('click', function(e) {
      var link = e.target.closest('a[href*="/checkout"]');
      if (link) {
        e.preventDefault();
        handleCheckoutUrl(link.href);
      }
    }, true);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
