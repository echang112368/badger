(function () {
  const domain = window.location.hostname;
  try {
    const origin = new URL(document.currentScript.src).origin;
    fetch(`${origin}/merchant/store-id/?domain=${encodeURIComponent(domain)}`)
      .then(r => r.json())
      .then(data => {
        if (data.storeID) {
          document.cookie = `storeID=${encodeURIComponent(data.storeID)}; domain=${domain}; path=/; max-age=31536000; Secure; SameSite=None`;
        }
      })
      .catch(() => {});
  } catch (e) {
    // Ignore errors
  }
})();
