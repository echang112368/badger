(function () {
  const domain = window.location.hostname;

  function getCookie(name) {
    var match = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()\\[\\]\/\\+^])/g, '\\$1') + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : null;
  }

  function updateCartAttributes() {
    try {
      var uuid = getCookie('uuid');
      var storeID = getCookie('storeID');
      var cusID = getCookie('cusID');
      console.log('cusID cookie', cusID);
      if (!uuid && !storeID && !cusID) {
        console.warn('No tracking cookies');
        return;
      }

      fetch('/cart.js', { credentials: 'same-origin' })
        .then(function (res) { return res.json(); })
        .then(function (cart) {
          var attributes = cart && cart.attributes ? cart.attributes : {};
          if (uuid) {
            attributes.uuid = uuid;
          }
          if (storeID) {
            attributes.storeID = storeID;
          }
          if (cusID) {
            attributes.cusID = cusID;
          }

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
          updateCartAttributes();
        }
      })
      .catch(() => {});
  } catch (e) {
    // Ignore errors
  }
})();
