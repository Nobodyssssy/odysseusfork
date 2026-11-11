/**
 * Odysseus: "Personal" link in the official Hermes Desktop Webapp's status bar.
 *
 * A standard runtime Desktop plugin (<hermes home>/desktop-plugins/odysseus-return/plugin.js), loaded by the Webapp's
 * own plugin loader. It adds one status-bar item and changes nothing else. It only ever navigates this tab to the
 * Odysseus origin that served this page (same port; localhost instead of hermes.localhost), never to another site.
 */
import { jsx, jsxs } from 'react/jsx-runtime'

function personalUrl() {
  const { protocol, hostname, port } = window.location
  if (protocol !== 'http:' || hostname !== 'hermes.localhost' || !port) {
    return null
  }

  return `http://localhost:${port}/`
}

function PersonalLink() {
  const href = personalUrl()

  if (!href) {
    return null
  }

  return jsxs('a', {
    href,
    title: 'Back to Odysseus Personal',
    'aria-label': 'Back to Odysseus Personal',
    className: 'inline-flex h-full items-center gap-1 px-2 text-[0.6875rem] font-medium text-(--ui-text-secondary) hover:text-(--ui-text-primary)',
    onClick: event => {
      event.preventDefault()
      window.location.assign(href)
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
