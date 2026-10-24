# N.E.K.O. FF14 Companion

N.E.K.O. Python plugin for the staged FF14 Companion integration. The plugin is deliberately localhost-only and does not connect to the game process. ACT / OverlayPlugin data is expected to be normalized by the separate .NET 8 FF14 Bridge.

## Phase 1 scope

- Check `GET /health` on the local Bridge.
- Read `GET /v1/state` for zone and combat context.
- Read `GET /v1/encounters/latest/summary` when the Bridge provides it.
- Poll the Bridge's bounded event cursor and apply opt-in high-priority Triggernometry callouts or zone greetings.
- Submit messages through N.E.K.O.'s supported `push_message` v2 API.
- When a user invokes a status, state, or encounter query, submit a concise Chinese report to the active N.E.K.O. conversation as well as returning structured data to the caller.

Automatic proactive speech and zone greetings default to off. Query results and explicit companion messages are submitted to the active conversation through `push_message`; this still does not prove that the host generated speech or played audio. Actual voice playback depends on the installed N.E.K.O. voice session.

## Bridge contract

Bind only to loopback (`127.0.0.1` / `::1`). Configure `companion.bridge_base_url` in `plugin.toml` or N.E.K.O.'s plugin configuration.

- `GET /health` returns a JSON object, preferably including `bridge`, `overlayPlugin`, and `lastEventAt` status.
- `GET /v1/state` returns a JSON object with nullable `zoneId`, `zoneName`, `dutyName`, `inCombat`, and `sourceStatus` fields.
- `GET /v1/encounters/latest/summary` returns a JSON object; use `{ "available": false }` if no encounter is ready.
- The summary includes aggregate encounter metrics only; the Bridge intentionally omits raw per-combatant records and character names.
- `GET /v1/events/recent?after=<sequence>` returns `{ "events": [...], "nextCursor": 12, "dropped": false }` for incremental polling.

Requests have a configurable 0.2–10 second timeout and responses are capped at 256 KiB. The plugin rejects non-local URLs, URL credentials, queries, and fragments.

## Install / validate in N.E.K.O.

Copy this folder to `N.E.K.O/plugin/plugins/ff14_companion/` (the folder name must match the plugin ID). Then run `uv run neko-plugin check ff14_companion`, refresh the Plugins page, start the plugin manually, and invoke `检查 FF14 Bridge`.

No SDK dependency is vendored here. The manifest follows the public plugin manifest format and declares the documented SDK compatibility range; use the SDK bundled with the target N.E.K.O. installation for validation and packaging.
