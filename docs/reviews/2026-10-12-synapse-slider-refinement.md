# Conversation map, effort slider and workspace refinement

User intent: start a new conversation and build its own non-linear map. This is explicitly NOT an overview of all workspace conversations. The workspace selector navigates separate native projects; the sidebar groups the current workspace's conversations and their branches. Remove the compare tab and show the header sidebar toggle only when the sidebar is closed.

References inspected: [Synapse](https://github.com/liangmianya/dsh-synapse), its Chinese guide and app source; [effort-slider](https://github.com/2768651338/dsh-effort-slider), EffortPanel/WebGL shaders and source licenses. Keep the user's blue accent and neutral surfaces; native Harness remains responsible for supported effort levels and exact checkpoint branching.

Changes: map cards have a small top drag grip, internal scrolling and side ports for continue/fork/collapse. A dashed inline question card handles the new root, follow-ups, quotation and branches. Map-only details show the full selected response while chat details retain actual artifacts. Map sends have stable request identity and historical input forks from the native checkpoint. The slider uses continuous 0–100 movement, snaps on release, a blue WebGL flame trail, and saves the native level with serialized writes. Reduced motion and hidden/closed panels stop rendering. No generic provider metadata is fabricated.

Verification: write failing browser/native cases first; retain existing drafts, permissions, per-conversation layout writes and keyboard focus. Use only isolated fake model/native MCP fixtures. Keep all pre-existing workspace changes uncommitted; refresh owned preview processes only when no execution is active.

## Verification and independent review (2026-10-13)

Full suite: 1281 passed, 43 skipped, 634 subtests passed. Browser chat/map/fork/permissions/composer/quote/layout/500-node/Skills/MCP/file-entry flows and Electron smoke passed. New reference flow, map draft reload, reduced-motion/closed renderer lifecycle and stale effort saves passed. Offline wheel includes all added modules and complete shader notices. Actual 60303 and owned desktop refreshed after checking zero active runs.

Review findings fixed: (1) pending new conversation with existing active branch exposed old map — RED→GREEN delayed-creation fixture; navigation/send/permission/workspace now wait. (2) enqueue failure/restart duplicated fork — RED→GREEN native fault injection, durable reserved child identity before clone. (3) dismissed effort panel submitted stale pending LOW after a newer HIGH — RED→GREEN delayed-response browser fixture, dismissal discards pending writes. (4) draft permissions modified source instead of target — RED→GREEN native test plus browser check, explicit draft mode captured at target enqueue. Keyboard collapse focus also restored. No re-review or unrelated kernel changes.

Decisions: scope remains the current new conversation and its branches per the user's correction; the workspace selector lists projects separately. Palette remains neutral plus blue, adapting the referenced purple shader colors. Live-provider performance/cost was not exercised; verification used isolated fake-model services. Dirty shared workspace is preserved without mixed commits.

Final map selection regression also observed RED for quote action disappearing on its own mouseup; fixed by preserving that action through click. End-to-end reference flow now also verifies collapse/expand keyboard focus and quotation without sending.
