/**
 * Odysseus: move between HermesGUI conversations and the Projects dashboard.
 *
 * 1. A "Projects" row in the sidebar's nav (the Webapp's own `sidebar.nav` + `routes` areas, no change to the Webapp). Choosing it leaves
 *    this tab for the Odysseus dashboard on the SAME origin the Personal link uses (http://localhost:<this port>/static/projects.html),
 *    carrying only the id of the conversation that is actually open (host.state.focusedStoredSessionId; none on a fresh draft). The
 *    destination is built from this page's own port, never from anything passed in. Browser Back returns to the route the user left.
 * 2.
 * The dashboard asks Odysseus for a Webapp sign-in link that lands on /?odysseus_resume=<session id>. This plugin reads that
 * one parameter, removes it from the address bar, and asks the Webapp to open the session through its own host API
 * (host.openSession), exactly as a click in the session list would. It validates the id, never navigates anywhere else,
 * does nothing when the parameter is absent, and opens a session at most once per page load.
 */
import { host } from '@hermes/plugin-sdk'
import { useEffect } from 'react'
import { jsx } from 'react/jsx-runtime'

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

const ROUTE = '/odysseus-projects'

// The last route that is not our own page, so history is left pointing at the conversation the user came from.
let lastRoute = '/'
let leaving = false

function currentRoute() {
  return window.location.hash.replace(/^#/, '') || '/'
}

function rememberRoute() {
  const route = currentRoute()

  if (route !== ROUTE) {
    lastRoute = route
  }
}

function projectsUrl() {
  const { protocol, hostname, port } = window.location

  if (protocol !== 'http:' || hostname !== 'hermes.localhost' || !port) {
    return null
  }

  const params = new URLSearchParams({ via: 'hermes' })

  try {
    const active = host.state.focusedStoredSessionId && host.state.focusedStoredSessionId.get()

    if (typeof active === 'string' && VALID.test(active)) {
      params.set('from', active)
    }
  } catch {
    /* no readable conversation: open Projects without a return destination */
  }

  return `http://localhost:${port}/static/projects.html?${params.toString()}`
}

function openProjects() {
  const href = projectsUrl()

  if (!href || leaving) {
    return false
  }

  leaving = true
  window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search + `#${lastRoute}`)
  window.location.assign(href)

  return true
}

function ProjectsPage() {
  const opened = projectsUrl() !== null

  useEffect(() => {
    openProjects()
  }, [])

  return jsx('div', {
    role: 'status',
    style: { padding: '2rem', color: 'var(--ui-text-secondary)', fontSize: '0.875rem' },
    children: opened
      ? 'Opening Projects…'
      : 'Projects opens from the Odysseus-hosted Hermes (http://hermes.localhost:<port>). This address is not one.'
  })
}

export default {
  id: 'odysseus-resume',
  name: 'Odysseus: Projects and resume',
  register(ctx) {
    rememberRoute()
    window.addEventListener('hashchange', rememberRoute)
    // Coming back through the browser's back/forward cache would show the "Opening Projects" page again: go back to the user's route.
    window.addEventListener('pageshow', event => {
      if (event.persisted) {
        leaving = false

        if (currentRoute() === ROUTE) {
          host.navigate(lastRoute)
        }
      }
    })

    if (ctx && typeof ctx.registerMany === 'function') {
      ctx.registerMany([
        { id: 'projects-page', area: 'routes', data: { path: ROUTE }, render: () => jsx(ProjectsPage, {}) },
        { id: 'projects-nav', area: 'sidebar.nav', order: 40, data: { codicon: 'project', label: 'Projects', path: ROUTE } }
      ])
    }

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
