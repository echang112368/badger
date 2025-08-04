(function() {
  const params = new URLSearchParams(window.location.search);
  const ref = params.get('ref');
  if (!ref) return;

  const match = ref.match(/^badger:([^;]+);buisID:([^;]+)$/);
  if (!match) return;

  const creatorUUID = match[1];
  const merchantUUID = match[2];

  document.cookie = `uuid=${creatorUUID}; path=/`;
  document.cookie = `buisID=${merchantUUID}; path=/`;
})();
