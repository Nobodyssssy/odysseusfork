# Restore guide: from a fresh Windows PC to your working Odysseus + Hermes

This guide rebuilds the full environment from two sources:

1. **GitHub** (public fork + private skills repo): code, docs, patches, pins, personal skills.
2. **Your private backup folder** (kept OFF this PC, encrypted): databases, conversations, memory, settings, Hermes config, the Wiki, unpublished git work, and optionally credentials.

GitHub alone cannot give you your data. The private backup alone cannot give you the code. You need both.

---

## 0. What you need on the new PC

| Need | How |
|---|---|
| Windows 10/11, same drive letters if possible (F:, D:, E:) | Paths inside configs are absolute (see section 7) |
| Git for Windows | https://git-scm.com/download/win |
| Python 3.12 | https://www.python.org/downloads/ (tick "Add to PATH") |
| 7-Zip (to open the encrypted backup) | https://www.7-zip.org/ |
| Node.js LTS (Hermes desktop components) | https://nodejs.org/ |
| Your private backup folder, decrypted | section 1 |
| GitHub sign-in (private skills repo) | `git clone` will prompt once |

Do NOT start Odysseus or Hermes until step 5.

## 1. Open the backup

The backup folder holds:

```
snapshot-YYYYMMDD-HHMMSS-xxxxxx\     Odysseus + Hermes data (made by odysseus-backup.py)
recovery-YYYYMMDD-HHMMSS\            git bundles, uncommitted work, task XML, inventory (made by backup-extras.ps1)
```

If it is a `.7z` file: right-click > 7-Zip > Extract here, enter your passphrase.

## 2. Get the code

```powershell
git clone -b publish/odysseus-hermes-2026-11 https://github.com/Nobodyssssy/odysseusfork.git F:\Odysseus
git clone https://github.com/Nobodyssssy/hermes-personal-skills.git F:\hermes-personal-skills   # private: sign in when asked
cd F:\Odysseus
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Dry run (changes nothing)

```powershell
cd F:\Odysseus
powershell -ExecutionPolicy Bypass -File scripts\recovery\restore-environment.ps1 `
  -Snapshot  E:\Backup\snapshot-20261114-101935-f609ae `
  -RecoveryFolder E:\Backup\recovery-20261114-112658 `
  -SkillsRepo F:\hermes-personal-skills
```

Read the output. It verifies the snapshot first (every file checked against its hash) and lists what would be created or overwritten. It refuses to continue if Odysseus, Hermes or Chroma are running.

## 4. Restore for real

Add `-Apply`. Existing files are never deleted. A safety copy of anything it overwrites goes to `Odysseus-Backups\prerestore-*`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\recovery\restore-environment.ps1 `
  -Snapshot E:\Backup\snapshot-... -RecoveryFolder E:\Backup\recovery-... -SkillsRepo F:\hermes-personal-skills -Apply
```

This restores: Odysseus database (chats, notes, memory, endpoints), Chroma, uploads, `.env`, launcher files; Hermes state (sessions, projects, kanban, memories, skills, plugins, desktop plugins, `config.yaml` incl. MCP servers); the Odysseus-Hermes component settings and workspace-grant status; your git repos from bundles into `F:\Restored\`; personal skills and the Alyn MCP entry.

To also re-create the Windows scheduled tasks ("Odysseus AutoStart", "Hermes_Gateway") add `-RestoreTasks`. Review `inventory\task-*.xml` first.

## 5. Rebuild what is not in any backup (reinstall, then reconnect)

Work through this list in order:

- [ ] Set the Windows user variable `HERMES_WRITE_SAFE_ROOT` (the restore prints the exact command) and `HERMES_WEBUI_PYTHON`. Values are also in `recovery-*\inventory\user-environment.json`.
- [ ] Install the Hermes component at the pinned versions: `python scripts\hermes-webapp-component.py ensure` (pins: `config\hermes-component.lock.json`; patches: `integrations\hermes-mode\patches\`).
- [ ] hermes-webui: restore from the bundle (`F:\Restored\F_hermes-webui`) and re-apply `webui-trusted-workspaces.patch`.
- [ ] Wiki Steward: `F:\Restored\F_Projects_Odysseus-Wiki` is your private Wiki (copy it to `F:\Projects\Odysseus-Wiki`). Its server script `wiki_mcp.py` is only on branch `feature/wiki-steward` (not in the published branch): `git -C F:\Restored\F_Odysseus checkout feature/wiki-steward` and point the `wiki` MCP entry in `config.yaml` at that copy.
- [ ] node_repl: install the OpenAI Codex app, then edit the `node_repl` entry in the managed `config.yaml`: the `cua_node\<hash>` folder name and the `\\.\pipe\codex-computer-use-<guid>` pipe change on every install.
- [ ] REA: the plugin folder is restored. Check it connects (Settings > Connectors).
- [ ] Alyn MCP: restored by the skills installer (untrusted). Reload MCP once.
- [ ] Models: copy `D:\odysseus-models` back if you backed it up (about 65 GB, optional). The list is in `inventory\model-files.csv`; models can also be re-downloaded.
- [ ] Sign in again where the backup had no secrets (section 6).

Then start Odysseus (`launch-windows.ps1`) and open `http://localhost:7000`.

## 6. Credentials

| Item | Full backup (`create`) | Secret-free backup (`create --exclude-secrets`) |
|---|---|---|
| Odysseus admin login, sessions, `.app_key` | restored | **you sign in again / set a new password** |
| Email passwords, provider keys stored in the Odysseus DB | restored (they are encrypted with `.app_key`, which is also restored) | removed, re-enter |
| Hermes provider keys (`.env`, `auth.json`) | restored | re-enter / re-run provider sign-in |
| Claude / Codex logins | not backed up | `claude login`, `codex login` |
| Workspace grants (`hermes-grants.db`) | never backed up, by design | never backed up |
| GitHub sign-in | not backed up | Git Credential Manager prompts |

Workspace grants are short-lived on purpose: sign in to Odysseus and approve them again.

A full backup contains passwords and keys: always keep it encrypted (section 8).

## 7. Things that are tied to this PC

- Absolute paths: `F:\Odysseus`, `F:\Projects\Odysseus-Wiki`, `D:\odysseus-models` appear in `.env`, `config.yaml` and the scheduled task. Use the same drive letters, or edit those files.
- Windows user name: the Codex and Hermes folders sit under `C:\Users\<you>\`.
- Drives are Btrfs (WinBtrfs): the backup tool copies files normally; never run `chkdsk` on them.

## 8. Making the backup (do this regularly)

Stop nothing. Both commands only read your live data.

```powershell
cd F:\Odysseus
# 1. Odysseus + Hermes data. Add --exclude-secrets for a copy with no credentials.
python scripts\odysseus-backup.py --odysseus-root F:\Odysseus --data-dir F:\Odysseus\data --backup-dir E:\Backup create
python scripts\odysseus-backup.py --backup-dir E:\Backup verify <snapshot-folder-name>
# 2. Git work not on GitHub, task XML, environment, model list
powershell -ExecutionPolicy Bypass -File scripts\recovery\backup-extras.ps1 -OutDir E:\Backup
```

Always pass `--data-dir` explicitly if you run the script from a copy of the repo. Otherwise it looks for `data\` next to the script.

## 9. Encrypt it and keep a copy OFF the PC you will reinstall

The backup holds private conversations and (in full mode) credentials.

1. Zip + encrypt: `7z a -t7z -mhe=on -p E:\Backup-2026-11-14.7z E:\Backup\snapshot-... E:\Backup\recovery-...` (7-Zip asks for a passphrase and encrypts file names too). Or use `backup-extras.ps1 -Encrypt`.
2. Keep the passphrase in a password manager, not on the same drive.
3. Copy the `.7z` to a drive that will not be wiped: a USB disk or cloud storage that you control. Not E:/D:/F: if those get reinstalled.
4. Once a quarter, extract it on another PC and run the dry run in section 3. A backup you have never restored is a guess.

## 10. Troubleshooting

| Message | Meaning |
|---|---|
| `The snapshot failed verification` | The copy is damaged. Use another snapshot. |
| `Refusing to restore while services run` | Stop Odysseus, Hermes gateway, Chroma, then retry. |
| `No skills folder under -Target` | Restore step 4 first, so the managed Hermes home exists. |
| `Permission denied` while creating a backup | Update to this branch; older code tried to copy locked runtime files. |
| MCP tools missing after restore | Reload MCP once in Hermes. Check `node_repl` and `wiki` paths. |
