# Chat to map draft handoff and permission glyphs

The user's completed chat answer was present in native history and in the map card. A previously visited empty map had saved a root draft in memory/localStorage; returning from chat rendered that form at the same position as the answer, obscuring it. The reproduction failed before the fix with one empty map form where zero was expected.

The map now reconciles its root draft with current native conversation data. An empty placeholder retires once chat starts a run or commits a turn. Meaningful unsent text and its chosen permission remain: after a completed recoverable chat boundary, the draft becomes a continuation of its own branch and appears beside the answer. Accepted map request identities remain intact during handoff; no request is replayed on reload. Drafts remain scoped by project and conversation.

Independent review found two additional cases in this handoff:

- A failed/stopped/interrupted or unrecoverable chat head is not a valid native continuation boundary. Such a retained draft stays unsent, disables direct Send/Enter, and offers “移到新会话”. This creates a fresh conversation draft only. Sending and resuming a paused queue remain explicit. The pure regression failed with continue instead of root before the guard.
- A delayed move could create two conversations on double-click. Both the button and the per-conversation callback now guard the pending operation, including after redraw, and release the guard after failure. The native delayed-response regression failed with two creates instead of one; focused reviewer mock also verified failure/retry. Final focused review reported no remaining findings.

Permission modes now share one glyph mapping in the chat composer, map draft and menu: closed lock for 请求批准, shield/check for 帮我批准, open lock for 完全访问权限. Full access retains the existing theme-aware orange. This changes presentation only; native authorization rules are unchanged.

Verification: 40 native GUI Python tests passed; 6 pure map/diff tests passed. Sixteen original/new browser and Electron scripts passed on fresh isolated fixtures, plus the stale-layout snapshot script. Final handoff flow additionally verifies pending/completed chat switching without refresh, stored-draft reload, preserved text/permission, exactly-once continuation, native model failure, explicit move to a clean conversation, guarded double-click, and successful explicit send after queue resume. Desktop syntax and wheel checks passed; final wheel contains all helper/callback updates. No provider requests were sent by verification.

The idle actual desktop preview was restarted and the browser loaded the final assets. The actual “了解项目结构” answer is visible in its map with zero empty overlay forms. All three menu glyphs and full-access orange were inspected without changing actual permissions or provider preferences. Existing unrelated checkout changes were retained; no commits or staging.

Screenshots: [actual synced map](../previews/gui-chat-map-live-2026-10-13.png), [actual permission menu](../previews/gui-permission-icons-live-2026-10-13.png).
