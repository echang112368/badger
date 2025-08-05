(function() {
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

  const cookieOptions = 'path=/; max-age=31536000; Secure; SameSite=None';

  document.cookie = `uuid=${encodeURIComponent(creatorUUID)}; ${cookieOptions}`;
  document.cookie = `buisID=${encodeURIComponent(merchantUUID)}; ${cookieOptions}`;

  console.log('Referral cookies set.');
})();
