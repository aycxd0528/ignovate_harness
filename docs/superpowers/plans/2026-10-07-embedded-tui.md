# Embedded TUI Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task. Track checks below. Existing user authorization covers execution and self-review; do not stage or commit workspace files.

**Goal:** Implement the complete UI audit with every interaction inside the main layout, consistent welcome/theme, accessible small-window controls, and efficient live progress.

**Architecture:** A shared `InteractionPanel` widget returns results through a host-managed future. The transcript, activity, interaction region, metrics and composer remain in normal vertical layout. Shared semantic theme styles apply to setup, help, lists, editors and confirmations. Retained transcript updates cache completed entries.

**Tech Stack:** Python, Textual, Rich, unittest/Pilot; offline fixtures only.

**Spec:** `docs/reviews/2026-10-07-ui-ux-pro-max-audit.md`

## Global Constraints
- No popup, modal, overlay or centered floating interactions.
- Preserve permission decisions, default refusal, queue boundaries, drafts, history scroll, API key masking and atomic preference writes.
- Weather stays left of composer metrics, actual model/effort/permission on right; empty weather is Clear 0% with known window.
- Preserve approved braille spinner, direct arbitrary text selection, no copy instruction banner.
- Dark/light, NO_COLOR and ASCII modes remain usable.
- No Git mutations, secret-file reads, live model requests or external messaging.

## Review Focus
- Small terminals and many models: action/footer always visible, list scrolls.
- Nested forms and cancellation: back restores selection and drafts, root cancel restores composer.
- Invalid input and failed saves: related error/focus, no partial persistence.
- Running work and cancel: awaited futures settle and previous focus/history survive.
- Long conversation and selection: live refresh does not rerender completed history or disrupt selection.

### Task 1: Embedded host, model and run settings
**Files:** Create `ui/interaction.py`, `tests/test_embedded_interactions.py`; modify `tui.py`, `ui/model_view.py`, `ui/settings_view.py`, `ui/actions.py`.
**Interfaces:** `InteractionPanel.complete(value)` emits `Resolved`; `TerminalAgentApp._wait_panel(panel)` returns its value; `preferred_height` gives bounded host height.
- [x] Write and run failing tests for a single screen stack, model back/draft/selection, small-window actions, actual-current marking and setting cancellation.
- [x] Implement shared widget/host, model and settings layout, explicit project/user default scope and field focus errors.
- [x] Run new tests plus model/settings command persistence regressions.

### Task 2: Remaining interactions in layout
**Files:** Modify `tui.py`, `ui/help_view.py`; create `ui/content_panels.py`; update relevant UI tests.
**Interfaces:** Help, plan review/edit, rewind and selection panels use the Task 1 result contract.
- [x] Add failing behavior tests for help return, plan/rewind default refusal, editor cancel and selection view without screen pushes.
- [x] Replace all active screen routes, remove unused modal classes, retain composer/status and restore focus/draft/scroll.
- [x] Run approval/help/plan/rewind/copy regressions and inspect rendered layouts.

### Task 3: Shared visual language and welcome flow
**Files:** Modify `ui/theme.py`, `ui/setup.py`, `ui/wordmark.py`, `main.py`, `tui.py`; tests `test_harness_setup`, new theme/layout cases.
**Interfaces:** Setup receives selected semantic theme and shares shell regions/sizing; field validation maps errors to focused fields.
- [x] Add failing tests for light setup, short-window actions, invalid-field focus, cancel/write failure preservation and compact repeated welcome.
- [x] Implement consistent palette/selection styles, height-aware welcome, useful examples, inline configuration and contextual errors.
- [x] Verify dark/light/NO_COLOR/ASCII contrast and offline setup persistence.

### Task 4: Efficient progress and responsive metrics
**Files:** Modify `ui/transcript.py`, `tui.py`, `ui/presentation.py`, spinner preferences as needed; meaningful retained-rendering tests.
**Interfaces:** Active retained entries update incrementally; resize/theme can rebuild all. Reduced motion keeps real elapsed/static activity.
- [x] Add failing tests counting completed entry renders during active updates and narrow metrics accessibility.
- [x] Cache retained lines, update changed suffix only, preserve selection/scroll, support reduced motion and full status via keyboard command.
- [x] Run transcript/spinner/metrics regressions and measure 300-entry refresh.

### Task 5: Full audit verification
**Files:** New offline render probe and verification notes under `docs/reviews/embedded-tui-2026-10-07/`.
- [x] Review every audit requirement against current source/runtime, add missing behavior checks and fix failures.
- [x] Run appropriate complete UI/command regression suite, export and visually inspect welcome/model/form/help/settings/approval/editor on 100/80/60 widths.
- [x] Record evidence, limitations and self-review; request one fresh-context code review after implementation as executing-plans requires. Do not mark the goal complete until every required flow is verified.
