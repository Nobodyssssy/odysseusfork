# Isolating the Hermes gateway on Windows: options and a recommendation

Status: design only. Nothing here is installed, enabled or changed. Labels: **measured** = observed on this PC in the lab, **documented** = known Windows behaviour I did not re-measure here, **unverified**.

## What problem this solves

The route guard (`webapp-route-guard.patch`) and the grant gate close the *web* vector: a browser, a stolen web session, a script that can only reach HTTP. They do not stop **code that already runs as your Windows user**: it can read the gateway's credential file, call the gateway directly on its loopback port, or edit your files without Hermes at all. The agent itself runs commands as you, so "ask before running" is a consent step, not a boundary. Only the operating system can enforce a boundary, by running the gateway (and what it launches) as something that is *not you*.

## Constraints measured on this PC

| Fact | Evidence |
|---|---|
| The agent session is not an administrator | measured (`IsInRole(Administrators)` = False) |
| Only one real interactive account (`Protagonist`); `CodexSandboxOffline/Online` exist but belong to Codex and must not be reused | measured |
| No Windows Sandbox and no Hyper-V compute service | measured (`WindowsSandbox.exe` absent, `vmcompute` absent) |
| WSL2 exists, its only distribution is Docker Desktop's internal one; WSL1 unsupported | measured |
| Low-integrity labels can be applied without admin; a Low-integrity gateway could not write to Medium-integrity files but could still read them and use loopback | measured earlier in the lab |
| E: and D: are Btrfs (WinBtrfs): ACL and integrity-label behaviour there is **unverified** | open issue; do not rely on it |

## Options

| # | Option | Needs | Stops same-user theft of the credential | Stops writes outside the work folder | Stops use of the loopback port | Verdict |
|---|---|---|---|---|---|---|
| A | **Dedicated standard Windows account** runs the gateway (`hermes-svc`), owns its home and credential, work folders granted by ACL | admin once (create account, ACLs, a scheduled task or service with that logon) | **yes**: files unreadable by `Protagonist`-level code (documented) | yes, to anything not granted | no, loopback is shared; keep gateway token + Host/Origin checks | **recommended target** |
| B | Low-integrity (or AppContainer) gateway process under your own account | no admin | **no**: Low can still read your files and the credential (measured: reads open) | yes | no | mitigation only, useful *inside* option A |
| C | Linux gateway in WSL2/Hyper-V VM (own filesystem, projected folders) | WSL distro install (admin may be needed), disk space | yes, if the credential never leaves the VM | yes | TCP forwarded to Windows loopback only | good for a later Linux/Windows unified app, but changes where agent tools run (Windows tools vs Linux tools) and Docker is currently off-limits |
| D | Windows Sandbox | feature absent, admin to enable, not on every SKU | n/a | n/a | n/a | not available here |
| E | Do nothing beyond the web controls | none | no | no | no | current state; must stay documented as such |

## Recommendation

1. **Target architecture: option A**, with option B applied to anything the gateway starts (tool subprocesses at Low integrity), and the existing loopback, Host/Origin, token, grant and route-guard layers kept. A runs only after you have admin rights to create the account.
2. What A needs, in order of risk, each a separate approval:
   - create local standard user `hermes-svc` (no admin, no interactive logon right, "log on as a batch job" only); random password generated and stored with the Windows Credential Manager of the account that starts the task, never in a file or chat;
   - move **only** the gateway's own state (`HERMES_HOME`, webapp credential, sessions) to a folder owned by `hermes-svc`, with `Protagonist` given read-only on logs only. Your project folders stay where they are: grant `hermes-svc` Modify on the specific workspaces, which `HERMES_WRITE_SAFE_ROOT` already names, and nothing else;
   - Odysseus stays as `Protagonist` and talks to the gateway on loopback with the token it already holds in memory; the Webapp credential no longer needs to be readable by your account except by Odysseus' launcher, which can request it through a one-way local channel (named pipe with an ACL) instead of reading a file;
   - the scheduled task replaces today's `Odysseus AutoStart` child process for the gateway only.
3. **Btrfs caveat:** data on E:/D: stays where it is (your rule). Do not put `hermes-svc`'s state on Btrfs until ACL enforcement there is measured; the gateway's state is small and can live on the system drive. Granting `hermes-svc` Modify on a Btrfs project folder depends on WinBtrfs honouring ACLs: **unverified**, test with a throwaway folder before relying on it.
4. **What stays true afterwards:** code running as `Protagonist` could still reach the gateway on loopback and would need the token (no longer on disk readable by that account). The agent can still act on files you granted to `hermes-svc`. A shell tool run by the agent as `hermes-svc` can read whatever `hermes-svc` can, which is why its folders must be minimal.

## What can be done now without admin (all disposable-first)

| Step | Effect | Status |
|---|---|---|
| Route guard patch for file/Git/MCP routes | parity with the agent's protected-path rules for a web caller | built, 19 focused tests, not deployed |
| `HERMES_WRITE_SAFE_ROOT` narrowed to the actual work folders | limits both the agent and now the direct routes | your action in the user environment; I cannot edit it |
| Webapp credential kept out of any folder served or listed by the file routes | already blocked by the read guard | included in the patch |
| Prove the loopback port is not reachable from another host | LAN not enabled | measured earlier: loopback only |
| Prototype option B for tool subprocesses | partial write containment only | not built; measured earlier it blocks writes but not reads |

## Decision requested later (not now)

Do you want option A? It needs a one-time administrator session: either you run a short, reviewed script I prepare (create user, ACLs, scheduled task) or you do the equivalent by hand. I will not request elevation or run anything elevated myself.

## Credentials on this PC: what is and is not protected

Claude Code and Codex keep their logins in your profile (`~/.claude/.credentials.json`, `~/.codex/auth.json`). The managed gateway and everything it starts run as you.

- **Not protected, in any configuration:** a shell command run by the agent (or by the terminal pane, an MCP server or any script) can read those two files. The Odysseus route guard, the write roots and the blocked-folder list do not restrict reads of arbitrary files by a process running as you.
- **What the `logins` setting changes:** only Hermes's own provider code. `isolated` points `CLAUDE_CONFIG_DIR` and `CODEX_HOME` at empty folders so the gateway cannot use your subscriptions by accident; `allowed` leaves them, which is the current behaviour of your existing Hermes. Allowed means the gateway may use the login and, for Claude, refresh the OAuth token and rewrite the file (refresh is serialised across Hermes processes by a lock keyed to the credential file, not with `claude.exe` itself).
- **Where the Claude and Codex logins are never copied:** managed component releases, migration bundles (auth files and credential values are excluded, asserted by tests), Odysseus/Hermes backups (the `~/.claude` and `~/.codex` folders are not in the backup scope), and project folders.
- **What the existing full backup DOES contain:** Hermes `.env` (provider API keys), Hermes `auth.json` (OAuth logins), Odysseus `.env` and the old WebUI signing key, because a restore needs them. That is stated in the snapshot's own manifest. The backup folder (`F:\Odysseus-Backups`) is plain files readable by your account, so it carries the same same-user exposure as the originals. Whether to keep that is an open decision in `hermes-production-cutover.md`.
- **What would close it:** the separate Windows account described above. Until then, treat anything the agent can run as able to read your subscription logins.
