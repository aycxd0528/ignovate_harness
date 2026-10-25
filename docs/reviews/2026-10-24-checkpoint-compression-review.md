# Final read-only review: phase31 checkpoint compression

## Scope and identity

This is the one fresh final review of the bounded checkpoint codec and default saver delta, using the requesting-code-review/code-reviewer.md instructions. The comparison base is the accepted wheel, not HEAD or the pre-existing dirty checkout:

- Base: `/tmp/ignovate-summary-bounds-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`, SHA256 `03f19c3fc4a04a20ac05039d1d0175f5430ef241d5a3da15d3a481c343762993`.
- Frozen delta: `/tmp/ignovate-checkpoint-compression-review.diff`, SHA256 `e0929e47863b17d661e5bc43b79342e6893b768398ae259718c51d0f1f011a41`.
- Independently reconstructed the eight-path diff against the wheel and the provided exact pre-edit document baselines. It equals the frozen delta byte for byte, both at the initial check and immediately before writing this review.
- Reviewed: new `nailong/core/checkpoint_serde.py`; constructor defaults in `nailong/core/sqlite_saver.py`; import aliases in `agent.py`, `nailong/gui/controller.py`, `nailong/gui/history.py`; the new 16-method test module; only the newly added PROJECT and CLI_REFERENCE paragraphs/section.
- Read the requirements and all seven initial rulings in `docs/reviews/2026-10-24-checkpoint-compression.md` and the prior native growth discovery in `docs/reviews/2026-10-24-checkpoint-storage-capacity.md`. Also read native installed sync/async saver serialization, SQL publication, get/list/pending-write logic, native JsonPlusSerializer, the existing owned connection shutdown, branch cloning, GUI fallback/history paths, factory saver construction, session coordination, and runtime source change detection.
- No checkout, index, HEAD, branch, product, tests, docs, dependency, or venv changes. Probe fixtures/logs/report were confined to `/tmp`; Python 3.11.15 ran with `-B`, `PYTHONDONTWRITEBYTECODE=1`, and `TMPDIR=/tmp`. No installation, real API, external network, user database, commit, or push operations. One exploratory read used an incorrect `nailong/core/runtime_sources.py` path; it exited without mutation, and the actual `nailong/gui/runtime_sources.py` was located and read.

## Strengths

1. **Exact original serialization is retained.** `checkpoint_serde.py:15-22` compresses the actual bytes returned by installed JsonPlusSerializer and preserves the native kind. The new codec does not reserialize a reduced object or alter checkpoint identities, provenance, message fields, or task state. The explicit 16 KiB–64 MiB and 1/8 body saving conditions match the requirements. Small, high entropy, and native oversized records retain their original representation.
2. **The decoder verifies a complete, bounded frame.** `checkpoint_serde.py:26-44` rejects unknown family versions, excessive or noncanonical headers, unsupported inner kinds, excessive compressed input, excess output, truncated/checksum-damaged streams, trailing data, and concatenated streams. `size + 1` gives direct evidence of excess output without materializing the whole expanded stream. The codec inherits native JsonPlus configuration and adds no pickle fallback or constructor privileges.
3. **Integration stays within the existing saver protocol.** `sqlite_saver.py:12-14,57-59` changes default serialization only. Caller-supplied serializers remain explicit. Native SQL, parent linkage, metadata JSON, pending writes, locks, connection ownership, worker shutdown, and cancellation ownership remain inherited or byte-identical to the base. Native from_conn_string constructs `cls`, so the synchronous subclass is actually used. Sync, async, GUI lazy reads, readonly continuation, full node answers, and historical clone routes all reach the same codec.
4. **Compatibility costs are stated accurately.** `docs/PROJECT.md:61-63` and `docs/CLI_REFERENCE.md:560-564` explicitly describe new-reader/old-data support, old-reader/new-frame incompatibility, stopping old peers, external-reader adapters, retained old rows, no automatic VACUUM, and continued total-history growth. This avoids claiming old files will automatically shrink.
5. **Tests exercise storage and user-visible behavior.** The new module checks real SQLite sync/async exchanges, old row identity, parent/list/pending writes, custom serde preservation, full message fields, historical branch boundaries, GUI model-free reads, readonly imported continuation and unknown-version refusal, and failed compression before publication. They preserve real native savers and GUI fixtures.

## Independent verification

### Focused existing test module

Executed only the new module, not the author's entire matrix:

`PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp PYTHONPATH=.:tests .venv/bin/python -B` with unittest loading `test_checkpoint_compression`.

Result: **16 tests, 0 failures/errors, 0.216 s, exit 0**. Full output is `/tmp/ignovate-checkpoint-compression-review-focused.log`.

### Independent boundary and integration probe

Result: **exit 0**, evidence `/tmp/ignovate-checkpoint-compression-review-probe.json`:

- 11 independently constructed values round-tripped identically to native JsonPlus; compressed frames decompressed to the exact original native bytes. A large bytearray retained its bytearray type.
- All cuts of one compressed stream plus all 256 possible appended one-byte suffixes were rejected: **412 invalid frames rejected**.
- An 8 MiB repeated-output compressed bomb declaring one output byte was rejected, with **77,543 bytes peak Python allocation** during decode under tracemalloc. This measures the additional raw decode buffer path, not a global process-memory guarantee.
- The actual **67,108,864-byte** encode/decode boundary passed with a **292,852-byte** compressed body. A **67,108,865-byte** native record remained a native bytes record and decoded without a newly imposed legacy size limit.
- A real mixed legacy-to-owned-async-to-runtime-sync database preserved the old type/body/metadata/parent row exactly, decoded the bytearray checkpoint and bytearray pending write, retained two historical rows and the parent configuration, and listed both.
- Injected async compression MemoryError published neither a new checkpoint nor any portion of a mixed small/large pending-write batch.
- The raw native saver successfully read the legacy row and raised NotImplementedError for the new compressed row. **The old-reader compatibility cost is observed behavior, not only a documentation assumption.**

The author's 117-test log, prototype 200-turn results, newly built wheel attestation, later full suites/installed suites/CLI/JS/native SDK/actual implementation 200-turn gates were not reproduced by this review and are not represented here as independently passed. The prototype's 493 MB→32.37 MB result is a repetitive-fixture observation, not a general compression or performance guarantee.

## Issues

### Critical — none

No observed data loss, incorrect restoration, unsafe new deserialization privilege, or broken default reader route in this delta.

### Important — none

No issue requiring a code change before the author's already planned final validation. The documented reverse compatibility break is real, but it is the explicit accepted format/upgrade contract and was evaluated as such.

### Minor — none

No actionable minor finding. Avoid adding formatting or speculative tuning work solely to manufacture review findings.

## Considered behaviors and proposed executor rulings

Every item below was considered. These are recommendations for the executor's recorded ruling; none silently expands the implementation scope.

| Behavior considered | Review result / proposed Ruling | Cost retained |
| --- | --- | --- |
| Lossless storage versus deleting intermediate checkpoints or clipping messages | **Accept initial ruling 1.** Original serializer bytes, checkpoint count and historical boundaries remain intact; no deletion or trimming is introduced. | Stored history still grows; compression adds temporary buffers and bounded work per eligible record. |
| Threshold, ratio, native type and oversized passthrough | **Accept.** Exact 16 KiB threshold and original kinds are implemented; native >64 MiB remains readable, independently checked at 64 MiB+1. | Small/high entropy/oversized records may receive no disk saving. The 1/8 condition applies to body bytes as specified. |
| Shared default sync/async/GUI readers and caller serializers | **Accept initial ruling 2.** Production reader inventory and native get/list/pending-write paths are covered; explicit custom serde remains under caller control. | Custom serializers do not automatically gain compression; a caller choosing native serde cannot read already compressed rows. |
| Old program / raw default LangGraph reader / concurrent old peer / rollback | **Accept initial rulings 2 and 6 with the existing explicit upgrade contract.** The native-reader failure was reproduced. The added docs tell users to stop old peers and use an adapted external reader. | New compressed rows are unreadable by the old default reader, including a rollback to old software, until an appropriate reader is used. No automatic backward transcoding is supplied. |
| Existing rows, schema, metadata and lineage preservation | **Accept initial ruling 3.** Existing SQL and metadata behavior are unchanged, old row bytes were compared exactly, parent/list/fork tests pass. | Existing database files do not shrink automatically; this delta reduces eligible new-row growth only. |
| Unknown formats, bad headers, bad compressed bodies and bombs | **Accept initial ruling 4.** Strict family/version/canonical length/kind/EOF/trailing checks reject invalid data; GUI imported continuation refuses unknown frames. | Corrupt or future frames fail conservatively; no fabricated state or log-based fallback recovery is added. |
| Compression failure and partial SQL publication | **Accept initial ruling 5.** Installed saver code serializes before publication; focused sync and independent async/pending-batch failures retained prior data and published no new rows. | Save failures propagate and interrupt that save rather than claiming persistence succeeded. Async setup may still initialize schema before serialization, as in the native saver. |
| Native worker lifecycle, retryable close, cancellation and connection locks | **Accept initial ruling 5.** Only saver constructors and reader aliases changed; the owned native lifecycle code is identical to the accepted wheel. | Codec work executes synchronously within the serializer call. There is no new hard cancellation, IO-time, or aggregate-resource guarantee. |
| Readonly history readiness and lazy GUI full answers | **Accept.** Readonly history still uses mode=ro and is_setup=True; the codec adds no setup calls. GUI model-free full-answer retrieval and unknown-version refusal pass. | Existing controller fallback readers retain their pre-existing setup/open behavior; this delta does not redesign those readers. |
| Native JsonPlus trust/constructor/pickle behavior | **Accept.** Default security settings are inherited, not broadened, and only msgpack/bytes/bytearray may be compressed. | Whatever trust policy the installed JsonPlus has continues to apply to both legacy and restored native bytes. |
| Raw byte bound versus fully deserialized object/process memory | **Accept the stated raw-byte contract.** Decode output is bounded; the new code never claims to bound native object materialization or old native rows. | Native deserialization can allocate more object memory than the byte length; old oversized native inputs remain unrestricted as before. |
| Infinite capacity / real-data compression percentage / latency and UI responsiveness | **Accept the documented limited claim.** No universal saving or wall-clock guarantee is promised. 64 MiB input work is finite, and the threshold makes small saves cheap. | Repeated small records and high entropy data still grow; sync compression can briefly occupy the calling thread/event loop. Representative future workload profiling can inform later tuning. |
| Packaging and default construction | **Accept source integration.** The package finder includes the new Python module; aliases use the subclass and native contextmanager calls cls. | The author's final installed-wheel/full-platform checks remain the author's remaining acceptance gates; this review does not replace them. |
| Baseline selection and unrelated dirty changes/docs | **Accept initial ruling 7.** Exact wheel-derived delta reconstruction matched the frozen SHA, and only the added docs were evaluated. | Review does not endorse the old dirty tree or pre-existing documentation claims. |

## Declined to judge

1. **Whole-project and final-distribution acceptance:** not rerunning 2,199 source tests, roughly 796 installed tests, two interpreter builds, CLI/JS checks, native SDK long-task checks, or the actual implementation's 200-turn run; those are explicitly the author's parallel/final gates. **Proposed Ruling:** retain those gates and report their actual outcomes separately. **Cost:** this review alone is not release or full-goal completion evidence.
2. **Automatic conversion/VACUUM, deletion, retention limits, or a rollback transcoder:** explicitly excluded by initial rulings 1/3/6; introducing them would materially expand this reviewed delta. **Proposed Ruling:** defer such product changes. **Cost:** old files remain large and older readers still fail on compressed rows.
3. **Unbounded session length, arbitrary high entropy performance, disk full/power loss/platform crash recovery, globally bounded resources, and real API service behavior:** not established by the controlled compression fixtures, and no such guarantee is added by this delta. **Proposed Ruling:** preserve the documented evidence boundary and existing guarantees. **Cost:** no assurance beyond observed samples and inherited native behavior.
4. **Pre-existing JsonPlus deserialization trust, application concurrency edge cases, earlier UI behavior, and all earlier dirty changes or earlier docs claims:** evaluated only for new regression risk at the touched routes, not audited afresh as unrelated features. **Proposed Ruling:** keep outside this precise delta. **Cost:** this review cannot certify all earlier work or all hostile-data handling.
5. **Cryptographic tamper detection or authentication:** zlib integrity and framing validation address corruption, not intentional valid payload substitution; storage authentication was not requested or promised. **Proposed Ruling:** defer. **Cost:** a deliberately rewritten valid local row is governed by the existing database/JsonPlus trust boundary.

## Assessment

**Ready to proceed through the author's planned final validation: Yes.**

**Ready to merge on this code-review gate: Yes; 0 Critical, 0 Important, 0 Minor.** The bounded codec and all changed reader defaults meet the seven initial rulings, preserve legacy data and native lifecycle semantics, and passed independent focused and edge-case verification. The reverse compatibility cost is real and explicitly accepted/documented. No C/I fix is requested; no second review is needed for this unchanged delta. Full-goal/release completion still depends on the author's separately planned final gates and honest recording of their results.
