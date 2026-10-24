# Phase 33 joint GUI task source / control identity — sole final review

Date: 2026-10-24. This resumes the interrupted review; it is not a second review. Review followed `.agents/skills/requesting-code-review/code-reviewer.md` and `docs/reviews/2026-10-24-gui-task-source.md`, including initial rulings R1–R8. Checkout, index, HEAD, dependencies, user data, and source/test files were not modified. Reports, disposable offline probes, their logs, and temporary test data were confined to temporary locations. No external network/model API, installation, user browser, or delegated review was used.

## Unresolved P0 checklist and severity

- Confirmed unresolved P0 / Critical in the reviewed scope: **none**.
- Important: **one P1, I1**, an independently reproduced pre-existing lazy-controller deadlock on the same final control-commit path. It exists in both the accepted production base and the frozen candidate; it must not be described as introduced by this delta.
- Minor: **none**. No style finding was manufactured.
- Pending author validation is an acceptance gate, not a P0 defect. This report does not establish whole-goal completion or authorize a claim that every final gate has passed.

## Exact scope and provenance

Production base is the accepted wheel, not HEAD or the entire dirty checkout:

- `/tmp/ignovate-checkpoint-compression-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`
- SHA256 `85fb1423e204fcc4f7fcdd80f8ced29b61d355f55a9f2732c0f21567275f19bc`.

Frozen candidate:

- `/tmp/ignovate-gui-task-source-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`
- SHA256 `34c5832dd0d71a6b5e5d542e9c4a70c37435725b4d3dc771fad4f4ee5113c276`.

Frozen three-path diff:

- `/tmp/ignovate-gui-task-source-review.diff`
- SHA256 `69d5adb40850ec2ea05bd8731e9f37bfac802bba91a1ba458b7e0a1e44f4884d`.
- `nailong/gui/work_control.py`, `tests/test_gui_work_control.py`, `tests/gui-browser.spec.cjs` only.

I independently recomputed all three hashes, checked identical ZIP member lists, and confirmed that only `nailong/gui/work_control.py` and wheel `RECORD` differ. Candidate `work_control.py` exactly matches the inspected checkout file. I regenerated the three-path unified diff using production bytes from the base wheel and the full pre-edit test copies; it reproduces the frozen diff byte for byte. Test baseline SHA256 values independently match the attestation:

- `test_gui_work_control.py`: `58e30eaff7f23bbf435a63de425dd039b5411848da2065ee2f8491887c4c6ad5`.
- `gui-browser.spec.cjs`: `49ef47be8f60858ce36d6b42c05a114f7b593e0456a3e69baeab8140f8bfadf8`.

Line references below refer to the frozen candidate. Surrounding authority/lock/source code was read to assess the delta, not to endorse all pre-existing or dirty code.

## Strengths

1. `work_control.py:64–72` uses canonical owner identity only while that owner remains associated and has a GUI branch. It preserves valid association/None fallback. The deterministic `z-owner`/`a-linked` regression checks task and attention routes and then submits real node-backed feedback through the projected route; it verifies the actual broken interaction rather than merely string selection.
2. `work_control.py:139–141,161,234–235` checks an explicitly supplied task identity before asynchronous scans and at final synchronous submission. The five-action same-revision replacement regression also checks that task state, session events, and feedback records remain unchanged after refusal.
3. `work_control.py:239–249` binds waiting mutations with the same TaskStore instance and performs the clear-wait condition comparison on the bound state. Inspection of `TaskStore._relationships_locked`, `_locked`, `bound_task`, `snapshot`, `begin`, and both wait mutators confirms relationship-before-canonical lock ordering and native reentrancy. There is no await inside these new locked regions.
4. The original asynchronous observation binding remains intact: project, branch, source node, complete task state, feedback observation, and native revision checks are revalidated before mutation. `feedback_confirm` retains feedback-record revision semantics rather than conflating that number with the task requirements revision.
5. Historical feedback revocation remains keyed to its own record and revision. The new regression confirms that replacing the owner's current task does not prevent revocation or mutate the replacement task.
6. The browser-spec change waits for the visible completed chat and send-button state before the next click. It retains the original three-turn, XSS, quote, fork, layout, and reload assertions and does not change product send/stop policy.

## Issues

### Critical (must fix)

None found. No confirmed P0.

### Important (should fix)

**I1 / P1 — A lazy GUI controller reacquires its own held relationship lock through a second TaskStore instance.**

- Location: `nailong/gui/work_control.py:203–209` and `:230–232`; `task_store` at `:11–12`. Relevant unchanged authority code: `nailong/core/task_state.py:45–58,101–116,307–309,409–421`.
- Trigger: reopen/use a GUI controller with `service=None`, an existing task and manual acceptance, then submit `confirm_acceptance` before starting a model run. This is a supported native controller state (`GUIController` accepts `service=None` and initializes the service lazily).
- Failure: `execute` creates fallback TaskStore A and holds `A.bound_task(...)` during the final commit. `_execute` then calls `task_store(controller)` again, creating fallback TaskStore B. B's `snapshot` tries to obtain the same relationship file lock still held by A. Instance-local reentrancy cannot recognize A's lock, so the synchronous GUI event-loop thread waits on itself. The request never completes and the event loop cannot process subsequent GUI commands.
- Evidence: the bounded native offline probe imports the exact frozen wheel and uses real TaskStore, BranchStore, sessions, filesystem locks, and controller command execution. Candidate `confirm_acceptance` exceeded the 4-second process bound; a traceback at 2 seconds shows `file_locks.py:48 → TaskStore._relationships_locked → _locked → snapshot → work_control._execute:232 → execute:208`. The identical probe against exact base 85fb also times out at the same chain (base lines 220/196), proving this is **pre-existing**, not a regression introduced by 69d5. Candidate lazy `set_wait` exits 0 because its new bound mutation uses one store instance.
- The same final-commit path is shared by `feedback_stage` and `feedback_confirm`; their susceptibility is established by code inspection, not separately claimed as dynamically reproduced here. The 76 related tests install a service task store for these mutation cases, which explains why they do not detect this lazy-mode path.
- Fix direction: carry the captured TaskStore from `execute` into `_execute` and reuse that exact instance for final snapshots and native mutators, or otherwise provide a stable controller-local fallback with correct project lifecycle ownership. Passing the captured store is the narrower change. Preserve the existing outer bound/full-observation comparisons and do not force model service initialization as a workaround.
- Required validation in the author's one C/I pass: bounded native lazy-controller regressions for manual confirmation and feedback stage/confirm, plus the existing source/identity and historical revoke controls. A subprocess deadline is appropriate for the old behavior because an asyncio timeout cannot interrupt a synchronous event-loop file-lock wait.
- Proposed ruling: **fix before declaring the joint control workflow complete or submitting the completed fix for GitHub integration**. Cost: a small store-plumbing change, permanent lazy-mode regression coverage, a new frozen candidate/hash and relevant author acceptance gates. No second review is requested; author owns the single C/I pass.
- Severity calibration: P1/Important because an ordinary lazy/reopened GUI control operation hangs the GUI until restart. It is not P0/Critical: this probe shows no data corruption, unauthorized external action, or universal failure, and initialized-service workflows remain functional.

### Minor (nice to have)

None.

## Independent verification performed

1. Exact artifact/hash/diff reproduction described above.
2. Focused related suite, executed once:

   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python -B -m unittest test_gui_work_control test_work_items test_gui_async_scans test_gui_artifact_evidence test_gui_native test_feedback_experience test_control_ledger -q`

   **76 tests passed in 7.610s**, exit 0. Log: `/tmp/ignovate-gui-task-source-review-focused.log`.
3. Disposable native-command identity probes: all five current-task actions reject explicit `None`, empty string, and another task ID before missing action-specific details or scans matter; all **15** refusals preserve task and session events. Two deterministic gap probes perform a native same-revision replacement after `_execute` reads its snapshot but before the new bound wait mutation; both `set_wait` and `clear_wait` refuse and preserve the replacement task/events. Script/log: `/tmp/ignovate-gui-task-source-review-identity-probe.py` and `.log`.
4. Bounded lazy-controller probe: exact candidate manual-confirm timeout, exact base manual-confirm timeout, candidate set-wait success. Script/log: `/tmp/ignovate-gui-task-source-review-lazy-probe.py` and `.log`. Children are bounded at 4 seconds, killed and reaped by the supervising subprocess runner on timeout; each test's temporary project/data directory is removed by its supervisor. No model service is constructed and no external API is used.

I did not duplicate the author's full two-interpreter source/installed matrix or six browser groups. Initial routing-only package evidence is not treated as evidence for this joint frozen candidate. At review completion I have not independently observed completed output for all final author gates, so I do not claim them complete.

## Considered behaviors and proposed rulings

The executor must record its final disposition and cost for each item; these are review proposals, not silent omissions.

| Item | Behavior considered | Proposed ruling | Cost / limitation |
| --- | --- | --- | --- |
| R1 | Owner routing, owner branch absent, no known branches, owner replaced while old linked task survives | Accept implementation. Prefer owner only when still associated and present; retain association/None fallback. | Visible route may change from prior alphabetical selection. Missing true source remains unavailable; no synthetic source is invented. |
| R2 | Task and attention share route while authority, task revision, permissions and budget remain unchanged | Accept implementation. | This does not transfer control authority or remove unrelated conservative busy windows. |
| R3 | Real source node/path, task identity/revision and input checks | Accept implementation and existing refusal behavior. | A linked branch without a valid source may still be unable to stage feedback. Branch association alone does not prove source evidence. |
| R4 | Browser click occurs after persisted node but before visible idle commit | Accept the explicit visible-state wait. | Test depends on current DOM/state contract. It does not promise arbitrary send/stop timing behavior. |
| R5 | Owned browser resources versus name-resolution cold FD initialization | Accept the stated bounded evidence policy; no independent final browser/FD claim in this review. | Local browser/runtime/offline-fixture scope remains. The reported resolver cache FD is not classified as a product leak. |
| R6 | Exact base/diff/candidate, only three frozen paths, one review and one author C/I pass | Exact provenance independently confirmed; retain one review. Resolve I1 in the author pass and rerun impacted/final gates against the resulting candidate. | A changed candidate requires fresh hashes and validation; this bounded review does not endorse other dirty content or whole-goal completion. |
| R7 | Five current-task actions, same-revision replacement, explicit missing/None/empty/wrong identity distinction | Accept implementation. Absent key retains legacy current-task semantics; a present invalid key is rejected. | Legacy callers that omit identity cannot claim stale-UI identity protection; explicit stale callers must refresh. |
| R8 | Atomic wait mutation, pre/post scan identity binding, historical revoke and explicit link target | Accept new wait locking and record-specific exclusions. Existing final async commit has I1 in lazy mode and requires same-instance reuse. | Synchronous native transactions still inherit system IO latency. The change must not hold native locks while awaiting or initialize the model merely to acquire a stable store. |
| C1 | Different meanings of expected_revision for task mutation versus feedback confirmation/revocation | Accept existing separation, which the identity fix preserves. | Callers must send the correct record's revision; equality of numbers is not identity. |
| C2 | Project/branch/source changes during asynchronous scans | Accept current full-state/native binding checks; focused regressions passed. | This is rejection and refresh behavior, not a promise that concurrent external work can never change the project. |
| C3 | Replacement between snapshot and waiting write | Accept new bound_task protection; two deterministic native gap probes passed. | The operation may fail conservatively after a genuine replacement and must be retried from current state. |
| C4 | Lazy service=None control submission | Retain as Important I1, not a declined behavior. Existing-base reproduction does not make a GUI hang acceptable for the intended workflow. | Requires the narrow author C/I fix and bounded regression gate described above. |

## Declined to judge

Each of these was considered and set aside for the stated reason, with a proposed ruling and cost. I1 is explicitly not set aside.

- D1 — Automatic search/reconstruction of a valid historical feedback node when the chosen branch has no valid current source: outside the specified route correction and would change source-selection policy. Proposed ruling: preserve explicit source validation and refusal. Cost: the user may need to open/select a legitimate source or explicitly associate a task; the route correction is not an automatic history-repair feature.
- D2 — Product redesign of the shared send/stop button, and every possible UI timing window: the frozen JS change is a test settlement wait and the supplied evidence identifies an actual Stop click. Proposed ruling: retain product behavior and the visible-idle test precondition. Cost: no broader button-policy or arbitrary-timing guarantee is supplied.
- D3 — Unlimited-session capacity, every OS/browser/runtime, live-model quality, and all process/system resource ceilings: bounded local offline evidence cannot establish these. Proposed ruling: retain R5's explicit limits. Cost: acceptance remains specific to the observed environments and owned resources; no inference that the cold resolver FD is a GUI leak.
- D4 — All unrelated dirty checkout content, earlier routing-only candidate integration, and whole-goal completion/GitHub readiness beyond these exact paths: outside this frozen delta and cannot be established from its tests or wheel. Proposed ruling: require the author's original complete acceptance gates and repository integration checks separately. Cost: this report alone cannot justify a global completion or merge claim.

## Recommendations

Use the single author C/I pass to repair I1 with the captured TaskStore instance, preserve existing identity/source/lock contracts, and run the bounded lazy regressions before rebuilding the final candidate. Record the final disposition of all R/C/D items above. Finish the author-owned two-interpreter 2206-source/803-installed, origin/asset/CLI/JS, six joint browser, and environment integrity gates against the actual final artifact; adjust expected test counts if the I1 regression adds methods. Do not reuse old routing-only evidence as final joint proof.

## Assessment

**Ready to merge: With fixes.** The frozen source-routing and explicit-task-identity delta is correct within the exercised bounds, with no newly introduced P0/Critical or Important defect found. A real pre-existing P1 on the same lazy control path remains unresolved (I1); repair it in the single author C/I pass and complete the remaining author gates before claiming this workflow complete. This is bounded review approval of the assessed delta, not whole-goal completion or certification that all final gates already passed.
