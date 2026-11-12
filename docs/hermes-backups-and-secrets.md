# Backups and secrets

## What exists

| Backup | Contains credentials? |
|---|---|
| Existing full snapshots in `F:\Odysseus-Backups\snapshot-*` (made before 2026-11-12) | **Yes, in plain files:** Hermes `.env` (provider API keys), Hermes `auth.json` (provider logins), the old WebUI signing/pbkdf2/session keys and password hash, Odysseus `data/auth.json` (login hashes), `data/sessions.json`, `.app_key`, email account passwords and the search API key inside `app.db` / `settings.json`. They are unchanged, as you decided. They are readable by any program running as your Windows user |
| `odysseus-backup.py ... create --exclude-secrets` (used at cutover step 1) | **No.** Checked 2026-11-12 against the real data: 7 credential files not copied, secret values removed from 5 files, and a scan of all 1,697 files found none of the 6 live secret values I looked up. The live files are never modified |
| Migration bundle (`hermes-migrate.py bundle`) | No credentials by design (tested); config is filtered, `.env` reduced to a settings allow-list |
| Claude / Codex logins (`~/.claude`, `~/.codex`) | Never in any backup or bundle; they stay where they are |

`--exclude-secrets` is **not a guarantee that nothing sensitive remains**: conversation text, memories, notes, uploaded files and project data are copied as they are, and a secret someone pasted into a chat is in the chat. A YAML config with an inline secret makes the backup refuse. A JSON file is parsed and any key named like a key, token, secret or password is blanked; SQLite columns named that way are blanked and the file is VACUUMed. The manifest lists what was removed (names only, never values).

## Restore of a secret-free snapshot

- It never erases live secrets: a live file that the snapshot removed secrets from is **kept** (`restore` prints each one). `--overwrite-redacted` restores it anyway, and then it loses its secrets.
- Into a fresh machine, files come back without secrets and you must:
  - sign in to Odysseus again (the admin account's login file is not in the snapshot: create the admin again, or restore it from an older full snapshot you trust);
  - re-enter Hermes provider API keys (`OPENCODE_ZEN_API_KEY`) and sign in to providers again;
  - run `claude login` / `codex login` if those logins are gone (they were never in the backup);
  - re-enter email passwords and the search (Tavily) key in Odysseus settings;
  - sign in to the old WebUI again (its password hash and session keys are not in the snapshot).
- Cutover rollback does not depend on any of this: the live Hermes home and Odysseus data are not modified by the cutover.

## Proposal: encrypted backups for long-term recovery (not built)

1. Keep the secret-free snapshot as the routine backup.
2. Keep credentials in a separate small archive made only from the files listed in the manifest's `files_not_copied`, encrypted with a modern authenticated scheme (for example `age` with a passphrase or a hardware key, or 7-Zip AES-256 with header encryption), stored on a different disk from the snapshot.
3. The passphrase lives in a password manager or on paper, never next to the archive and never in this application or in an agent session.
4. Test a restore of the encrypted archive onto a scratch folder once, then after every credential change.
5. Replace the old plaintext snapshots only after that restore test passes (until then they are the only complete restore point; deleting them is your decision).

Windows same-user limit: encryption protects files at rest and in a stolen copy; it does not stop a program already running as you from reading the live files.
