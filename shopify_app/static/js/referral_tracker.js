(function () {
  const domain = window.location.hostname;
  try {
    const origin = new URL(document.currentScript.src).origin;
    fetch(`${origin}/merchant/store-id/?domain=${encodeURIComponent(domain)}`)
      .then(r => r.json())
      .then(data => {
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
