/* Decides where "Back" on the Providers page may go. Pure: no DOM, no network. */

/**
 * @param {string|null} raw        the ?return= value
 * @param {string} pageOrigin      location.origin of the Providers page (Odysseus)
 * @param {string} pagePort        location.port
 * @param {string|null} hermesOrigin  origin of the configured direct Hermes URL, if known
 * @returns {{href: string, app: 'odysseus'|'hermes'}|null}  null = ignore the value (use the default)
 */
export function resolveReturn(raw, pageOrigin, pagePort, hermesOrigin) {
  if (typeof raw !== 'string' || !raw || raw.length > 2048) return null;
  let dest;
  try { dest = new URL(raw, pageOrigin); } catch (_) { return null; }
  if (dest.protocol !== 'http:' || dest.username || dest.password) return null;
  if (dest.origin === pageOrigin) {
    return dest.pathname === '/static/provider-sync.html' ? null : { href: dest.href, app: 'odysseus' };
  }
  const proxied = pagePort ? 'http://hermes.localhost:' + pagePort : null;
  if (dest.origin === proxied || (hermesOrigin && dest.origin === hermesOrigin)) return { href: dest.href, app: 'hermes' };
  return null;
}
