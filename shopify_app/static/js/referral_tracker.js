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
