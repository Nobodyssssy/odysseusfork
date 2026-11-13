/**
 * Odysseus: open a conversation from the Projects dashboard.
 *
 * The dashboard asks Odysseus for a Webapp sign-in link that lands on /?odysseus_resume=<session id>. This plugin reads that
 * one parameter, removes it from the address bar, and asks the Webapp to open the session through its own host API
 * (host.openSession), exactly as a click in the session list would. It validates the id, never navigates anywhere else,
 * does nothing when the parameter is absent, and opens a session at most once per page load.
 */
import { host } from '@hermes/plugin-sdk'

const PARAM = 'odysseus_resume'
const VALID = /^[A-Za-z0-9._-]{1,200}$/

// The Webapp's router may rewrite the address before plugins load, so the parameter is also read from the URL this page was
// navigated to (the navigation timing entry), which the router cannot change.
function requestedFrom(href) {
  try {
    return new URL(href).searchParams.get(PARAM)
  } catch {
    return null
  }
}

function takeRequestedSession() {
  try {
    const navigation = window.performance && window.performance.getEntriesByType('navigation')[0]
    const id = requestedFrom(window.location.href) ?? (navigation ? requestedFrom(navigation.name) : null)

    if (id === null) {
      return null
    }

    const url = new URL(window.location.href)

    if (url.searchParams.has(PARAM)) {
      url.searchParams.delete(PARAM)
      window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash)
    }

    return VALID.test(id) ? id : null
  } catch {
    return null
  }
}

function whenConnected(open) {
  const state = host.state && host.state.gateway
  let done = false

  const attempt = () => {
    if (done || !state || state.get() !== 'open') {
      return
    }

    done = true
    open()
  }

  if (!state || typeof state.subscribe !== 'function') {
    window.setTimeout(open, 1500)

    return
  }

  state.subscribe(attempt)
  attempt()
}

export default {
  id: 'odysseus-resume',
  name: 'Odysseus: resume a conversation',
  register() {
    const id = takeRequestedSession()

    if (id) {
      whenConnected(() => {
        Promise.resolve(host.openSession(id)).catch(() => {
          /* the session may not exist on this gateway; the Webapp then stays where it is */
        })
      })
    }
  }
}
