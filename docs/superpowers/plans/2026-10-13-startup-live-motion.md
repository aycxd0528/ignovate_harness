# Startup and live motion implementation plan

Goal: Implement a React Bits-inspired startup and remove thinking/tool refresh flicker while preserving the existing native workflow.

Design: Blur Text-inspired staggered brand reveal and Shiny Text-inspired connection sheen, with a soft connection halo. Light/dark and reduced-motion support. First successful native snapshot dismisses the startup immediately; no simulated progress or artificial minimum wait. Rendering failures show an actionable retry state. Independent CSS/DOM implementation, no React/WebGL or external runtime dependencies.

Evidence: gui-motion.spec.cjs initially fails because status events detach the chat history, current run/tool/answer and chat-search input. app.js renderContent rebuilds history on every snapshot; token events replace and reparse the full answer repeatedly. Fix the cause with persistent keyed turn/run/tool nodes, bounded batched stream patches and cached static sections. Animate only new records and actual state transitions.

Execution: inline in the current authorized dirty checkout; preserve unrelated work, real credentials/preferences and data; no staging/commits or model requests. Existing skills reviewed; external reference only official reactbits.dev Blur Text/Shiny Text pages. User explicitly authorized implementation, routine design/implementation decisions continue autonomously.

- [x] Add failing tests for startup readiness/error/reduced motion and stream DOM/focus/selection stability.
- [x] Incremental chat renderer and tool updates preserve existing nodes, expansion and output scroll. In-place sanitized markdown reconciliation and one stream paint per batch.
- [x] Skip rebuilding unchanged sidebar/header/status sections, preserve scroll unless near bottom, and preserve map gesture/layout regressions.
- [x] Startup markup/controller and motion CSS, subtle runtime thinking/tool motion, static whitelist.
- [x] Focused/native browser regressions, new stress checks, resource/package checks; independent final review; actual preview and screenshots.

Progress: complete. 23 browser/Electron suites passed; 28 native transport/controller tests and 8 pure JS tests passed. After the final fixes, the startup, live motion, no-tool completion, idle reconnect, native map/diff, conversation, file-entry and Electron suites were rerun successfully. Wheel built offline; all eight changed frontend assets verified byte-for-byte. JS syntax, desktop syntax and git diff whitespace checks passed.

Independent review caught and resolved no-tool completion section aliasing, cached chat search mismatch, and stale connection-error status. No-tool completion preserves selected text and code-copy reads the updated code. Idle recovery with reset:false and no events triggers one healthy reload. A further RED/GREEN check covers reopening a recorded diff after a closed tool refresh.

User selected the connection-convergence variant. Native browser and desktop launchers were refreshed only after checking zero active runs. CUA verified the real browser and desktop workbench; no real provider request or preference mutation was used. Temporary React Bits reference tab closed. Startup/runtime screenshots are isolated test captures in docs/previews/gui-startup-2026-10-13.png and gui-runtime-motion-2026-10-13.png; actual browser proof is gui-motion-live-2026-10-13.png.
