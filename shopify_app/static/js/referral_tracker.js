(function () {
  const domain = window.location.hostname;
  try {
    const search = window.location.search;
    let params = new URLSearchParams(search);
    let ref = params.get('ref');

    if (!ref) {
      try {
        const decodedSearch = decodeURIComponent(search);
        params = new URLSearchParams(decodedSearch);
        ref = params.get('ref');
      } catch (err) {}
    }

    if (ref) {
      try {
        ref = decodeURIComponent(ref);
      } catch (err) {}
      const match = ref.match(/^badger:([0-9a-fA-F-]{36})$/);
      if (match) {
        const uuid = match[1];
        const secure = window.location.protocol === 'https:' ? '; Secure' : '';
        document.cookie = `uuid=${encodeURIComponent(uuid)}; path=/; max-age=31536000; SameSite=Lax${secure}`;
      }
    }

    console.log("try ran")
    const origin = new URL(document.currentScript.src).origin;
    const headers = {
      'ngrok-skip-browser-warning': 'true'
    };

    console.log("origin", origin);
    console.log("domain", domain);
  
    console.log("fetching:", `${origin}/merchant/store-id/?domain=${encodeURIComponent(domain)}`);

    fetch(`${origin}/merchant/store-id/?domain=${encodeURIComponent(domain)}`,{headers})
      .then(response => {
      console.log("Raw response:", response);
      return response.text();
    })
    .then(data => {
      console.log("Parsed data:", data);
    })
      .catch(error => {
      console.error("Fetch failed:", error);
    });

    fetch(`${origin}/merchant/store-id/?domain=${encodeURIComponent(domain)}`, {headers})
      .then(r => r.json())
      .then(data => {
        console.log(data.storeID);
        if (data.storeID) {
          const secure = window.location.protocol === 'https:' ? '; Secure' : '';
          document.cookie = `storeID=${encodeURIComponent(data.storeID)}; path=/; max-age=31536000; SameSite=Lax${secure}`;
        }
      })
      .catch(() => {});
  } catch (e) {
    // Ignore errors
  }
})();

(function () {
  function getCookie(name) {
    var match = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()\\[\]\/\+^])/g, '\\$1') + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : null;
  }

  function updateCart(uuid, storeID) {
    return fetch('/cart.js', { credentials: 'same-origin' })
      .then(function (res) { return res.json(); })
      .then(function (cart) {
        var attributes = cart && cart.attributes ? cart.attributes : {};
        attributes.uuid = uuid;
        attributes.storeID = storeID;

        return fetch('/cart/update.js', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ attributes: attributes })
        });
      });
  }

  function interceptCheckout(event) {
    var uuid = getCookie('uuid');
    var storeID = getCookie('storeID');
    if (!uuid || !storeID) {
      return;
    }

    event.preventDefault();
    var target = event.currentTarget;
    updateCart(uuid, storeID)
      .catch(function (error) {
        console.error('Failed to update cart', error);
      })
      .finally(function () {
        if (target.tagName && target.tagName.toLowerCase() === 'form') {
          target.submit();
        } else if (target.href) {
          window.location.href = target.href;
        }
      });
  }

  function bindInterceptors() {
    var forms = document.querySelectorAll("form[action*='/checkout']");
    forms.forEach(function (form) {
      form.addEventListener('submit', interceptCheckout);
    });

    var links = document.querySelectorAll("a[href*='/checkout']");
    links.forEach(function (link) {
      link.addEventListener('click', interceptCheckout);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindInterceptors);
  } else {
    bindInterceptors();
  }
})();
