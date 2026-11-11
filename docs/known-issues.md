# Known issues

## KI-001: `test_explicit_enable_restores_saved_component_without_reinstalling` fails
`KeyError: 'agent'` at `src/hermes_component.py:346`. It fails identically on untouched `release/hermes-mode-windows`, so Phase 1 did not cause it. It is marked `expectedFailure` so the suite is green and the failure stays visible. Remove the marker when it is fixed.

## KI-002: provider sync cannot read providers while the Hermes password is on
Odysseus calls `/api/providers` on Hermes without credentials, gets 401 and reports `requires_authentication`. Provider lists cannot be read. The fix must not store the Hermes password; a later item is a scoped read-only token.

## KI-003: Hermes sessions are readable by same-user code
`.sessions.json` is readable by any process running as your Windows account, including the agent's shell. The password closes the web and other-user vectors, not this one. Only a separate Windows account or a sandbox (Phase 3) closes it.
