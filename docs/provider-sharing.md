# Shared providers: Odysseus and Hermes

A catalog layer that shows the providers configured in Odysseus and in Hermes in one place, and can mirror a provider's *definition* (never its credentials) from one application to the other when you opt in. Open it from the **Providers** link in the Odysseus mode bar, from the same link next to the return button in Hermes, or at `http://localhost:7000/static/provider-sync.html`.

Neither application's provider storage, authentication, or default model is replaced. There is no shared database, no merged settings and no credential broker.

## How it works

| | Odysseus | Hermes |
|---|---|---|
| Storage | `model_endpoints` table; keys encrypted at rest | `config.yaml` (`providers`), `.env` (keys), `auth.json` (OAuth) |
| Read | database rows, in process | `GET /api/providers` (booleans only) plus the `base_url` fields of `config.yaml` |
| Write (mirror) | additive row insert, **no key** | one record appended to the end of `config.yaml`, **no key** |

Identity is the **canonical endpoint URL** (`localhost`, `127.0.0.1`, `::1` and `0.0.0.0` are one machine; default ports, trailing slashes and `/chat/completions` suffixes are normalized). Two providers on one endpoint are one provider, so a mirror can never be duplicated and a mirrored provider is never mirrored back.

## What can and cannot be shared

- **Shared (definition only):** local OpenAI-compatible servers and keyed cloud endpoints. The name, URL and model list are mirrored.
- **Keys are never copied.** A mirrored cloud provider shows **Needs authentication** in the receiving application until you enter the key there.
- **Never shared:** OAuth and subscription logins (Codex, Copilot, Nous, Claude subscription), providers whose transport is not an HTTP endpoint, and any provider Hermes already has built in (for example OpenRouter): add the key in Hermes instead.
- **Independent defaults:** neither writer has a code path that changes a default model or provider. Each application keeps its own selection, so Odysseus can use a light local model while Hermes uses Codex.

## Safety properties

- **Opt-in per provider.** Sharing is off by default and is stored by this layer only (`managed/provider-sync.json`, no secrets).
- **Additive only.** Nothing is overwritten, renamed, disabled or deleted. Deleting a provider in one application never deletes the mirror in the other.
- **Deletions are respected.** A mirror you remove is not re-created until you explicitly turn sharing on again.
- **Conflicting edits.** A changed URL is a new provider (sharing off by default); the old mirror is left alone. Differing model lists are reported ("Model lists differ"), not overwritten.
- **Hermes config edit** is append-only: the original bytes must remain an exact prefix of the new file, the result must parse to the old data plus one record, a timestamped backup is written to `managed/provider-sync-backups/`, and the writer refuses if the endpoint already exists anywhere in the config or if `providers` is not the last section.
- **Odysseus insert** applies the same outbound-URL (SSRF) validation as adding an endpoint by hand.
- **Credentials** are reduced to booleans at the adapter boundary. They are never in a response, the browser, a log, the sync state, or a backup written by this layer.
- **Access:** local administrator in a real browser only. The Agent tool identity, API tokens, the internal token, proxied requests, non-loopback peers and cross-origin requests are refused.
- **Local only:** connection tests are credential-free and loopback-only. Remote providers are never contacted from here, because that would require their key.

## Status labels

Connected, Needs authentication, Not supported, Offline, Disabled, Not checked. Sync: In sync, Model lists differ, Sharing off, Waiting to be added, Removed in one app (not re-added), Not shareable, Login-based: cannot be shared. An offline local server keeps its configuration and shows Offline.

## Rollback

- **Turn sharing off** for a provider in the page. Nothing is removed.
- **Remove a mirror** in the application that holds it (Odysseus Settings, or delete its `providers` entry in Hermes). It will not be re-added.
- **Undo the Hermes edit:** restore the newest file in `<component root>\managed\provider-sync-backups\` over `config.yaml` (stop Hermes first), or delete the appended `"custom:ody-..."` record.
- **Disable the feature:** `scripts/hermes-component.py disable`, or deploy the previous commit; the page and API then return 404 and no catalog code runs.

## Known limits

- Hermes must be running (and without a login) to be read or written; otherwise only Odysseus providers are listed and nothing is written to Hermes.
- Hermes custom-provider records do not carry a key; a keyed custom endpoint needs its key entered in Hermes.
- Mirroring into Hermes is refused if `providers:` is not the last top-level section of `config.yaml` (the append-only guarantee); add the provider manually in that case.
- The model lists are those last reported by each application plus a live probe for local servers; use Refresh after a server changes its models.
- Sharing keys between the applications is intentionally unimplemented. It would need a protected credential broker; that is a separate design requiring your approval.
