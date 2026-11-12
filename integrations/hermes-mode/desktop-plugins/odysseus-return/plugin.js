/**
 * Odysseus: "Personal" link in the official Hermes Desktop Webapp's status bar.
 *
 * A standard runtime Desktop plugin (<hermes home>/desktop-plugins/odysseus-return/plugin.js), loaded by the Webapp's
 * own plugin loader. It adds one status-bar item and changes nothing else. It only ever navigates this tab to the
 * Odysseus origin that served this page (same port; localhost instead of hermes.localhost), never to another site.
 *
 * Returning uses browser history when this tab arrived from Odysseus, so the Odysseus page comes back from the browser's
 * back/forward cache with its draft, scroll position and panels. Otherwise (Projects opened directly, no usable entry,
 * or a browser without the Navigation API) it navigates normally.
 */
import { jsx, jsxs } from 'react/jsx-runtime'

const ENTERED_COOKIE = 'odysseus_entered'
const FROM_ODYSSEUS = 'odysseus.fromPersonal'
const FALLBACK_MS = 900

function personalUrl() {
  const { protocol, hostname, port } = window.location

  if (protocol !== 'http:' || hostname !== 'hermes.localhost' || !port) {
    return null
  }

  return `http://localhost:${port}/`
}

// The proxy sets a 30-second, non-secret hint cookie when it hands this tab over from Odysseus. Turn it into a per-tab flag
// (sessionStorage is per tab and survives reloads and in-app navigation) and remove the cookie.
function noteArrival() {
  try {
    if (document.cookie.split(';').some(part => part.trim().startsWith(`${ENTERED_COOKIE}=`))) {
      sessionStorage.setItem(FROM_ODYSSEUS, '1')
      document.cookie = `${ENTERED_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`
    }
  } catch {
    /* storage blocked: the normal navigation below still works */
  }
}

// How many steps back lands on the entry that preceded this app's first page, or null when we cannot say.
function stepsBackToOdysseus() {
  try {
    if (sessionStorage.getItem(FROM_ODYSSEUS) !== '1' || !window.navigation || !window.navigation.currentEntry) {
      return null
    }

    const steps = window.navigation.currentEntry.index + 1

    return steps > 0 && steps < 500 ? steps : null
  } catch {
    return null
  }
}

function returnToPersonal(href) {
  const steps = stepsBackToOdysseus()

  if (steps === null) {
    window.location.assign(href)

    return
  }

  let left = false
  const mark = () => {
    left = true
  }

  window.addEventListener('pagehide', mark, { once: true })
  window.history.go(-steps)
  // If history did not take us away (entry gone, blocked), fall back to a normal navigation.
  window.setTimeout(() => {
    if (!left && document.visibilityState === 'visible') {
      window.removeEventListener('pagehide', mark)
      window.location.assign(href)
    }
  }, FALLBACK_MS)
}

// When Odysseus ends this browser's access (logout, expiry), its gate answers every Hermes request with 401 and `X-Odysseus-Grant: required`.
// The Webapp would show its own full-screen sign-in prompt (which asks for a command-line step); send the tab to Odysseus instead, which shows
// its own sign-in or lets the user pick Projects again. Only this page's own origin is watched and only the Odysseus origin is ever the target.
let leaving = false

function leaveForOdysseus() {
  const href = personalUrl()

  if (leaving || !href) {
    return
  }

  leaving = true
  window.location.assign(href)
}

function watchForRevokedAccess() {
  if (typeof window.fetch !== 'function' || window.fetch.__odysseusWatched) {
    return
  }

  const original = window.fetch.bind(window)

  const watched = async (...args) => {
    const response = await original(...args)

    try {
      if (response.status === 401 && response.headers.get('x-odysseus-grant') === 'required' && new URL(response.url).origin === window.location.origin) {
        leaveForOdysseus()
      }
    } catch {
      /* never let the watcher break a request */
    }

    return response
  }

  watched.__odysseusWatched = true
  window.fetch = watched
}

noteArrival()
watchForRevokedAccess()

// Looks like the other status-bar chips (same height, type size and theme tokens) but is the one control that leaves Projects, so it is
// tinted with the theme accent and outlined. Everything is inline and built from the theme's own variables: the Webapp's stylesheet only
// contains the classes its own source uses, so new utility classes would silently do nothing. Hover and focus are handled on the element.
const CHIP_BACKGROUND = 'color-mix(in srgb, var(--ui-accent) 14%, transparent)'
const CHIP_BACKGROUND_HOVER = 'color-mix(in srgb, var(--ui-accent) 26%, transparent)'
const CHIP_OUTLINE = 'inset 0 0 0 1px color-mix(in srgb, var(--ui-accent) 40%, transparent)'
const CHIP_FOCUS = '0 0 0 2px color-mix(in srgb, var(--ui-accent) 75%, transparent)'

function PersonalLink() {
  const href = personalUrl()

  if (!href) {
    return null
  }

  return jsxs('a', {
    href,
    title: 'Back to Odysseus Personal',
    'aria-label': 'Back to Odysseus Personal',
    className: 'inline-flex items-center gap-1 text-[0.6875rem] transition-colors',
    style: {
      alignSelf: 'center',
      height: '1rem',
      margin: '0 0.375rem 0 0.125rem',
      padding: '0 0.5rem',
      borderRadius: '0.25rem',
      fontWeight: 600,
      color: 'var(--ui-text-primary)',
      background: CHIP_BACKGROUND,
      boxShadow: CHIP_OUTLINE,
      textDecoration: 'none',
      cursor: 'pointer'
    },
    onMouseEnter: event => {
      event.currentTarget.style.background = CHIP_BACKGROUND_HOVER
    },
    onMouseLeave: event => {
      event.currentTarget.style.background = CHIP_BACKGROUND
    },
    onFocus: event => {
      event.currentTarget.style.boxShadow = `${CHIP_OUTLINE}, ${CHIP_FOCUS}`
    },
    onBlur: event => {
      event.currentTarget.style.boxShadow = CHIP_OUTLINE
    },
    onClick: event => {
      event.preventDefault()
      returnToPersonal(href)
    },
    children: [jsx('span', { 'aria-hidden': true, children: '←' }), jsx('span', { children: 'Personal' })]
  })
}

export default {
  id: 'odysseus-return',
  name: 'Odysseus: back to Personal',
  register(ctx) {
    ctx.register({ id: 'personal-link', area: 'statusBar.right', order: 1, render: () => jsx(PersonalLink, {}) })
  }
}
