(function() {
  const cookieOptions = 'path=/; max-age=31536000; Secure; SameSite=None';

  // Set merchant UUID cookie from the script tag's src parameter
  let merchantUUID = null;
  try {
    const scriptSrc = document.currentScript.src;
    merchantUUID = new URL(scriptSrc).searchParams.get('merchant_uuid');
  } catch (e) {
    console.log('Failed to parse merchant UUID from script src:', e);
  }
  if (merchantUUID) {
    document.cookie = `buisID=${encodeURIComponent(merchantUUID)}; ${cookieOptions}`;
    console.log('Merchant cookie set.');
  } else {
    console.log('Merchant UUID missing. Unable to set buisID cookie.');
  }

  // Parse creator UUID from referral query parameter
  const params = new URLSearchParams(window.location.search);
  const refs = params.getAll('ref');

  if (refs.length === 0) {
    console.log('Referral parameter missing. Exiting referral tracker.');
    return;
  }

  let creatorUUID = null;

  for (let ref of refs) {
    // Handle URL-encoded values such as ref=badger%3A123
    try {
      ref = decodeURIComponent(ref);
    } catch (e) {
      console.log('Failed to decode referral parameter:', ref, e);
      continue;
    }

    const match = ref.match(/^badger:([^;]+)$/);
    if (match) {
      creatorUUID = match[1];
      break;
    }
  }

  if (!creatorUUID) {
    console.log('Referral parameter invalid:', refs);
    return;
  }

  console.log('Parsed creator UUID:', creatorUUID);

  document.cookie = `uuid=${encodeURIComponent(creatorUUID)}; ${cookieOptions}`;

  console.log('Referral cookie set.');
})();
