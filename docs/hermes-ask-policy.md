# Hermes ask policy (plugin `odysseus-ask-policy`)

A `pre_tool_call` plugin that asks you before any tool that is not on a small read-only list, including every MCP tool, sub-agents and cron. Commands are allowed without a prompt only when they match a listed line exactly. It also makes the never-allow list effective on the file-safety paths that skipped it (`agent.file_safety`, ACP, text-to-speech).

**It asks. It does not sandbox.** An approved command runs with your full Windows rights. If the plugin is missing or disabled, everything runs unasked (tested as a known gap). Errors inside it block rather than allow.

Install: copy the folder to `<hermes home>\plugins\` and enable `odysseus-ask-policy` under `plugins.enabled` in `config.yaml`, then restart Hermes. It is not installed yet.

Tests: `integrations/hermes-mode/plugins/odysseus-ask-policy/tests` (23, drive the real `model_tools.handle_function_call` in a disposable home).
