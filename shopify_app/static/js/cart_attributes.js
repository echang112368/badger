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

  var originalAssign = window.location.assign.bind(window.location);
  var originalReplace = window.location.replace.bind(window.location);
  var hrefDescriptor = Object.getOwnPropertyDescriptor(Location.prototype, 'href');

  function rawNavigate(url) {
    originalAssign(url);
  }

  function navigateTo(url) {
    console.log('Navigating to', url);
    rawNavigate(url);
  }

  function submitCheckout(form) {
    console.log('Submitting checkout');
    HTMLFormElement.prototype.submit.call(form);
  }

  function ensureAttributesThen(proceed) {
    var uuid = getCookie('uuid');
    var storeID = getCookie('storeID');
    var cusID = getCookie('cusID');
    console.log('cusID cookie', cusID);
    if (!uuid || !storeID || !cusID) {
      console.log('Missing uuid, storeID, or cusID cookie, proceeding without update');
      return proceed();
    }

    fetchCartAttributes()
      .then(function(attrs) {
        if (attrs.uuid === uuid && attrs.storeID === storeID && attrs.cusID === cusID) {
          console.log('Cart attributes already up to date');
          return proceed();
        }

        attrs.uuid = uuid;
        attrs.storeID = storeID;
        attrs.cusID = cusID;
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

  var CHECKOUT_PATH = '/checkout';

  function isCheckoutPath(url) {
    try {
      var u = new URL(url, window.location.origin);
      return u.pathname === CHECKOUT_PATH || u.pathname === CHECKOUT_PATH + '/';
    } catch (e) {
      return false;
    }
  }

  function patchLocationForCheckout() {
    window.location.assign = function(url) {
      if (isCheckoutPath(url)) {
        ensureAttributesThen(function() { originalAssign(url); });
      } else {
        originalAssign(url);
      }
    };

    window.location.replace = function(url) {
      if (isCheckoutPath(url)) {
        ensureAttributesThen(function() { originalReplace(url); });
      } else {
        originalReplace(url);
      }
    };

    Object.defineProperty(window.location, 'href', {
      get: hrefDescriptor.get,
      set: function(url) {
        if (isCheckoutPath(url)) {
          ensureAttributesThen(function() { hrefDescriptor.set.call(window.location, url); });
        } else {
          hrefDescriptor.set.call(window.location, url);
        }
      }
    });
  }

  function interceptLinks() {
    document.addEventListener('click', function(event) {
      var link = event.target.closest('a[href]');
      if (!link) return;
      if (isCheckoutPath(link.getAttribute('href'))) {
        event.preventDefault();
        var href = link.href;
        ensureAttributesThen(function() { rawNavigate(href); });
      }
    });
  }

  function interceptForms() {
    document.addEventListener('submit', function(event) {
      var form = event.target;
      var action = form.getAttribute('action') || '';
      if (isCheckoutPath(action)) {
        event.preventDefault();
        ensureAttributesThen(function() { submitCheckout(form); });
      }
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
    if (form) {
      warnDynamicCheckout(form);
    }

    interceptLinks();
    interceptForms();
    patchLocationForCheckout();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
