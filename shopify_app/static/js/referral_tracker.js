(function() {
  const domain = window.location.hostname;
  const cookieOptions = `domain=${domain}; path=/; max-age=31536000; Secure; SameSite=None`;

  console.log('Referral tracker initializing for domain:', domain);
  console.log('Cookie options:', cookieOptions);
  console.log('Cookies enabled:', navigator.cookieEnabled);
  console.log('Existing cookies at load:', document.cookie);

  try {
    const scriptOrigin = new URL(document.currentScript.src).origin;
    console.log('Script origin for store ID lookup:', scriptOrigin);
    fetch(`${scriptOrigin}/merchant/store-id/?domain=${encodeURIComponent(domain)}`)
      .then((r) => r.json())
      .then((data) => {
        console.log('Store ID lookup response:', data);
        if (data.storeID) {
          console.log('Fetched store ID:', data.storeID);
          document.cookie = `storeID=${encodeURIComponent(data.storeID)}; ${cookieOptions}`;
          console.log('Store ID cookie set; current cookies:', document.cookie);
        } else {
          console.log('Store ID lookup failed for domain:', domain, 'response:', data);
        }
      })
      .catch((e) => console.log('Failed to fetch store ID:', e));
  } catch (e) {
    console.log('Error setting store ID:', e);
  }

  const params = new URLSearchParams(window.location.search);
  const refs = params.getAll('ref');

  if (refs.length === 0) {
    console.log('Referral parameter missing. Exiting referral tracker.');
    return;
  }

  let creatorUUID = null;
  let merchantUUID = null;

  for (let ref of refs) {
    // Handle URL-encoded values such as ref=badger%3A123%3BbuisID%3A55
    try {
      ref = decodeURIComponent(ref);
    } catch (e) {
      console.log('Failed to decode referral parameter:', ref, e);
      continue;
    }

    const match = ref.match(/^badger:([^;]+);buisID:([^;]+)$/);
    if (match) {
      creatorUUID = match[1];
      merchantUUID = match[2];
      break;
    }
  }

  if (!creatorUUID || !merchantUUID) {
    console.log('Referral parameter invalid:', refs);
    return;
  }

  console.log('Parsed creator UUID:', creatorUUID);
  console.log('Parsed merchant UUID:', merchantUUID);

  document.cookie = `uuid=${encodeURIComponent(creatorUUID)}; ${cookieOptions}`;
  document.cookie = `buisID=${encodeURIComponent(merchantUUID)}; ${cookieOptions}`;

  console.log('Referral cookies set.');
})();
