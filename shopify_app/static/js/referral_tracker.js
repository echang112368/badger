(function() {
  const params = new URLSearchParams(window.location.search);
  const ref = params.get('ref');
  if (!ref) {
    console.log('Referral parameter missing. Exiting referral tracker.');
    return;
  }

  const match = ref.match(/^badger:([^;]+);buisID:([^;]+)$/);
  if (!match) {
    console.log('Referral parameter invalid:', ref);
    return;
  }

  const creatorUUID = match[1];
  const merchantUUID = match[2];

  console.log('Parsed creator UUID:', creatorUUID);
  console.log('Parsed merchant UUID:', merchantUUID);

  const cookieOptions = 'path=/; max-age=31536000; Secure; SameSite=None';

  document.cookie = `uuid=${encodeURIComponent(creatorUUID)}; ${cookieOptions}`;
  document.cookie = `buisID=${encodeURIComponent(merchantUUID)}; ${cookieOptions}`;

  console.log('Referral cookies set.');
})();
