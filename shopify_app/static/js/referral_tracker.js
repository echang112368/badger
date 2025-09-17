(function () {
  var domain = window.location.hostname;
  var currentScript = document.currentScript;
  if (!currentScript) {
    var scripts = document.getElementsByTagName('script');
    currentScript = scripts[scripts.length - 1];
  }
  var origin = new URL(currentScript.src).origin;
  var fetchHeaders = { 'ngrok-skip-browser-warning': 'true' };

  function getCookie(name) {
    var pattern = '(?:^|; )' + name.replace(/([.$?*|{}()\[\]\/\+^])/g, '\\$1') + '=([^;]*)';
    var match = document.cookie.match(new RegExp(pattern));
    return match ? decodeURIComponent(match[1]) : null;
  }

  function setCookie(name, value, maxAgeSeconds) {
    var secure = window.location.protocol === 'https:' ? '; Secure' : '';
    var cookie = name + '=' + encodeURIComponent(value) + '; path=/; max-age=' + maxAgeSeconds + '; SameSite=Lax' + secure;
    document.cookie = cookie;
  }

  function safeCookieName(raw) {
    return raw.replace(/[^a-zA-Z0-9_.-]/g, '_');
  }

  function ensureVisitorId() {
    var visitorId = getCookie('badgerVisitorId');
    if (!visitorId) {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        visitorId = window.crypto.randomUUID();
      } else {
        visitorId = 'v-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
      }
      setCookie('badgerVisitorId', visitorId, 365 * 24 * 60 * 60);
    }
    return visitorId;
  }

  function updateCartAttributes() {
    try {
      var uuid = getCookie('uuid');
      var storeID = getCookie('storeID');
      var cusID = getCookie('cusID');

      if (!uuid || !storeID || !cusID) {
        return;
      }

      fetch('/cart.js', { credentials: 'same-origin' })
        .then(function (res) { return res.json(); })
        .then(function (cart) {
          var attributes = cart && cart.attributes ? cart.attributes : {};
          attributes.uuid = uuid;
          attributes.storeID = storeID;
          attributes.cusID = cusID;

          return fetch('/cart/update.js', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ attributes: attributes })
          });
        })
        .catch(function (error) {
          console.error('Failed to update cart', error);
        });
    } catch (error) {
      console.error('Failed to update cart', error);
    }
  }

  var rawSearch = window.location.search || '';
  var params;
  try {
    params = new URLSearchParams(rawSearch);
  } catch (error) {
    params = new URLSearchParams();
  }

  var hadReferralParam = false;
  var queryParamsObject = {};
  params.forEach(function (value, key) {
    queryParamsObject[key] = value;
  });

  var ref = params.get('ref');
  if (!ref && rawSearch) {
    try {
      var decodedSearch = decodeURIComponent(rawSearch);
      params = new URLSearchParams(decodedSearch);
      ref = params.get('ref');
      params.forEach(function (value, key) {
        queryParamsObject[key] = value;
      });
    } catch (error) {}
  }

  if (ref) {
    try {
      ref = decodeURIComponent(ref);
    } catch (error) {}
    var match = ref.match(/^badger:([0-9a-fA-F-]{36})$/);
    if (match) {
      hadReferralParam = true;
      setCookie('uuid', match[1], 30 * 24 * 60 * 60);
    }
  }

  var cusParam = params.get('cusID');
  if (cusParam) {
    setCookie('cusID', cusParam, 30 * 24 * 60 * 60);
  }

  var visitorId = ensureVisitorId();

  function logReferralVisit() {
    if (!hadReferralParam) {
      return;
    }
    var creatorUuid = getCookie('uuid');
    var merchantUuid = getCookie('storeID');
    if (!creatorUuid || !merchantUuid) {
      return;
    }

    var cookieKey = safeCookieName('badger_visit_' + creatorUuid + '_' + merchantUuid);
    if (getCookie(cookieKey)) {
      return;
    }

    var payload = {
      creator_uuid: creatorUuid,
      merchant_uuid: merchantUuid,
      merchant_domain: domain,
      landing_url: window.location.href,
      landing_path: window.location.pathname,
      query_string: rawSearch,
      query_params: queryParamsObject,
      referrer: document.referrer || '',
      visitor_id: visitorId
    };

    fetch(origin + '/collect/track-visit/', {
      method: 'POST',
      mode: 'cors',
      headers: {
        'Content-Type': 'application/json',
        'ngrok-skip-browser-warning': 'true'
      },
      body: JSON.stringify(payload)
    })
      .then(function (response) {
        if (response.ok) {
          setCookie(cookieKey, '1', 24 * 60 * 60);
        }
      })
      .catch(function (error) {
        console.error('Failed to record referral visit', error);
      });
  }

  function handleStoreId(storeId) {
    if (!storeId) {
      return;
    }
    setCookie('storeID', storeId, 30 * 24 * 60 * 60);
    updateCartAttributes();
    logReferralVisit();
  }

  if (hadReferralParam && getCookie('storeID')) {
    logReferralVisit();
  }

  fetch(origin + '/merchant/store-id/?domain=' + encodeURIComponent(domain), { headers: fetchHeaders })
    .then(function (response) { return response.json(); })
    .then(function (data) {
      if (data && data.storeID) {
        handleStoreId(data.storeID);
      }
    })
    .catch(function (error) {
      console.error('Store lookup failed', error);
    });
})();
