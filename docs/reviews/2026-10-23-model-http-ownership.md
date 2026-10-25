# Phase22 final independent model HTTP ownership review

## Scope and evidence

This completes the single frozen phase22 review after the primary reviewer exhausted its usage limit before producing a report. It is not a review of author fixes. The checkout, index, HEAD, production files, and checkout tests were not modified during this review. No external API was called and no reviewer was delegated.

The exact production delta in `/tmp/ignovate-model-http-review.diff` was checked against the attested phase21 wheel, **not HEAD**. The baseline wheel SHA256 independently matched `6b85f714b933ab73bb7f3d401950d0dbe8c182a3752667daac263833e8e4fd69`; diffs reconstructed from its bytes for `nailong/core/model_http.py` and `nailong/core/runtime_resources.py` matched the frozen diff. I read those files, the new native tests/fixture, the spec, plan and ledger rulings, relevant Factory acquisition/release sites and lifecycle tests, and installed HTTPX/HTTPcore backend, TLS and proxy interfaces.

Independently run on the frozen checkout:

```text
PYTHONPATH=tests .venv/bin/python -B -m unittest test_model_http_ownership test_factory_lifecycle test_factory_entrypoints test_sqlite_global_shutdown
Ran 75 tests in 34.874s — OK
```

Finite supplemental probes independently reproduced the concurrent child release defect and the direct async SDK/synchronous close defect below. A real default SDK through an owned loopback HTTP proxy also returned both responses and closed both retained native sockets, with FD4→4 and no new surviving threads. The first proxy probe stopped on a probe-only assumption about proxy connection nesting; collecting receipts from the owner corrected that probe without product changes. Actual OpenAI SDK controls using HTTPX 0.28.1 and HTTPX2 with their respective public MockBackends returned `OK` without the decorator and failed with it. These mock probes made no network requests.

The supplied full/installed/JS/release results are useful frozen pre-review evidence; this report does not attest a subsequent fixpass or broad completion of the original project goal.

## Strengths

- Acquisition receipts retain the actual native stream independently of SDK pool eviction and `is_closed`, and successful closure removes receipts immediately instead of accumulating successful connection history.
- TCP/Unix connection handoff and TLS upgrade are counted before handoff; per-stream locks protect replacement of the native TLS stream/socket. The native tests exercise actual SDK sockets, including the sync TLS detach race and post-loop descriptor checks.
- The cancellation-resistant Task contains native final settlement and joining already-started acquisition only. SDK close, request, connection, read, write, TLS deadlines and retry remain on their original paths. Native timeout tests confirm SDK read and TLS handshake deadlines still fire.
- Installation is per-client before the product's first request. Retired and private child clients remain owned; synchronous HTTP finalization moves off the event loop while SQLite and budget close retain their original thread behavior. Default and legacy native tests cover these paths.

## Issues

### Critical — none found

### Important

#### I1. Coalesce each client's close across child release and factory shutdown

- **Locations:** `nailong/core/runtime_resources.py:220`–`231`, `:233`–`253`, `:174`–`186`, and `:299`; `nailong/core/model_http.py:167` has no per-client close coalescing either.
- **Trigger and failure:** a private child's `finally` enters `_release_model(child)` while `factory.aclose()` is closing owned clients. Both snapshots include the same async client, and both independently invoke its owner. After one removes the client, the other's `list.remove` at line230 or line250 raises `ValueError` outside the close-error collection. If the factory path loses that race, `_close_resources` exits before its synchronous clients, budgets and synchronous SQLite cleanup.
- **Observed user effect:** independently rerunning `/tmp/ignovate-model-http-child-race-probe.py` in an owned temporary directory with child-only `NO_PROXY=*` produced two close entries for the same child, `ValueError:list.remove(x): x not in list`, one retained synchronous SDK client, an actual SQLite connection still accepting `SELECT 1`, and FD4→7. Both child TCP sockets were closed; the failure is skipped remaining resource cleanup. This is a concrete shutdown failure during child completion, not merely duplicate bookkeeping noise.
- **Required fix:** give each actual client a shared close operation/result used by every release/close entry point, including the executor path for sync clients. A concurrent waiter must join actual settlement and observe that operation's failure; failed resources must remain retryable. Remove ownership once after verified success and keep remaining resource cleanup running on every failure. Membership checks or swallowing `ValueError` alone do not establish that a concurrent close was joined successfully.
- **Scope:** the double-removal pattern existed in phase21, but phase22 expressly covers concurrent close, private child ownership and “single failure must not skip other resources.” It is an unresolved current requirement in the modified ownership paths.

#### I2. Reject synchronous close after direct async HTTP admission before destroying other resources

- **Locations:** `nailong/core/runtime_resources.py:256`–`275`, especially the guard at `:259`–`263`; acquisition state is available in `nailong/core/model_http.py:124`–`137`.
- **Trigger and failure:** an embedding caller uses the default `factory.model.root_async_client`/its HTTP client directly in the original loop without invoking `factory.async_runtime`. Real async streams are then owned, but `_async_resources_started` remains false. `factory.close()` treats these clients as unused, marks the factory closing, closes synchronous resources and runs async HTTP close in a worker's new event loop.
- **Observed user effect:** independently rerunning `/tmp/ignovate-model-http-sync-admission-probe.py` in mode `direct-sdk-close` after two actual default SDK HTTP requests produced `ExceptionGroup:Model HTTP cleanup failed (2 sub-exceptions)`, `_resources_closing=True`, and SQLite already closed. A subsequent explicit `await factory.aclose()` in the still-live original loop recovered the resources and FD4→4. The synchronous-only guard should instead reject with the existing `RuntimeError` before changing resource state. The current path attempts wrong-loop cleanup and leaves the caller with a partly destroyed factory.
- **Required fix:** incorporate actual async model admission/activity into the synchronous close guard, covering in-flight connection/TLS handoff as well as retained streams. Reject before closing SQLite, budgets or sync clients. Preserve synchronous closure of genuinely unused async SDK clients and keep the valid async cleanup on the original loop.
- **Scope/calibration:** embedding callers must use `aclose()` after async use, as documented. This finding concerns enforcement of the existing synchronous-only guard and phase22's same-loop/native-admission contract, rather than adding automatic support for closing an already-abandoned event loop. Graph-based async use already sets the guard flag; direct use of the exposed model does not.

### Minor

#### M1. Package-prefix detection wrongly admits built-in custom MockBackends

- **Location:** `nailong/core/model_http.py:191`–`200`, particularly `:194`.
- **Failure:** accepting every backend whose module starts with `httpcore`/`httpcore2` also accepts their public `AsyncMockBackend`. Its stream intentionally has no native socket. Wrapping it makes an otherwise normal `Connection: close` response raise `RuntimeError('Model HTTP native socket closure could not be verified.')`, seal the owner and fail cleanup repeatedly with an ExceptionGroup. The undecorated actual OpenAI SDK control returns `OK`; this reproduced for both installed HTTPX families.
- **Impact and grade:** this breaks offline integrations that explicitly replace the network backend under an HTTPX transport. Default Factory construction does not select MockBackend, and no default native resource loss was demonstrated by this case, so I grade it Minor rather than a default-product must-fix.
- **Suggested fix:** recognize the supported native default backend implementations explicitly and select the prior SDK close contract for custom/non-native backends before mutating any pools. Add the two no-network control cases when addressing this boundary. A missing socket should not be treated as proof that a custom resource has closed.

## Recommendations

Use the native RED assets for I1 and I2 in the authorized single C/I fixpass. For I1, exercise concurrent success and failure so that the implementation proves a shared operation/result and does not merely hide duplicate removal. Retain final assertions on actual SQLite/sync-client closure, original native sockets, and the caller's cancellation/cleanup result. For I2, retain the unused-client control and add an in-flight admission control. M1 can be recorded for the deferred Minor pass according to the agreed process.

## Declined to judge

Each line below is a behavior or support claim considered and set aside; none is silently assumed to work.

1. Unrelated correctness of accepted phases1–21 — outside this exact wheel delta; the directly relevant inherited shutdown races above were included.
2. Native-handle guarantees for arbitrary custom transports/backends — expressly excluded by the spec's fallback contract; preservation of that contract was evaluated separately in M1.
3. Retroactively recovering streams from an externally supplied, already-used default SDK client — the spec intentionally rejects these clients before decoration because prior acquisition receipts do not exist.
4. Replacing/mutating transports, mounts or pool backends after ownership installation, or sharing one underlying HTTP client across independently owned factories — not exercised by product construction or promised by this instance-ownership design; no support conclusion is made.
5. Bounded shutdown when native connect, TLS handoff or close never returns — expressly excluded by the spec; the finite probes establish finite settlement only.
6. Cleanup after an embedding caller has already discarded the original event loop without `aclose()` — outside the documented lifecycle contract; I2 uses a still-live original loop and asks for early rejection of the wrong close entry point.
7. Native certification for optional HTTP2, HTTPS-proxy TLS-in-TLS, other operating systems, Trio/custom event loops and future SDK private interfaces — installed backend/proxy/TLS method forwarding was source-inspected; native checks here cover the installed macOS asyncio versions, direct TLS and an HTTP forward proxy. The optional `h2` dependency is absent. No demonstrated protocol regression is alleged, and this review does not promote that source inspection into a full native compatibility certification.
8. Broad stability/completion of the original complex-long-task project goal — this report judges phase22 only; passing pre-review suites do not close that wider goal.

## Assessment

**Ready to merge? With fixes.**

**Reasoning:** native acquisition ownership, TLS handoff and cancellation settlement are substantially implemented and supported by real-socket evidence. Concurrent child/factory cleanup can still skip remaining resources, and synchronous shutdown can misclassify directly admitted async streams and attempt wrong-loop cleanup after destroying SQLite. Resolve I1 and I2 before accepting phase22; M1 is a scoped deferred Minor issue.


## Author acceptance record

This is the frozen independent report before the single author C/I fixpass. Final evidence and all decisions are archived in [the phase plan](../superpowers/plans/2026-10-23-model-http-ownership.md). M1 remains deferred; the report is not a rereview of the fixes.




## 第二十二轮：模型 HTTP 原生连接所有权及并发清理

原生SDK先标closed/清pool后close失败、全局取消、同步TLS交接均可能使真实socket失去关闭路径。新增实例级HTTPX/HTTPX2 backend/stream所有权，在返回stream时持有实际socket，正常关闭及时撤销、失败保留供重试；非取消的淘汰失败封闭新连接准入。TLS升级计交接并串行化native指针更新；原生最终收尾由HTTP专用Task持有，不包SDK请求/read/write/connect/TLS超时，不扩用SQLite helper。异步资源关闭用executor Future等待同步HTTP，保留事件循环进度和原线程SQLite/预算语义。

一次最终独立评审0C/2I/1M，Astra用量中止后6.1-sol完成同一冻结增量评审，未复审作者补修。作者一次C/I RED/GREEN修复I1：private child释放与Factory退出共用每客户端的在途关闭/结果，sync复用真实executor Future，async首调用在既有owner Task执行并发布plain Future；所有并发等待者加入同一次实际关闭并观察原始失败。仅实际成功后清账，失败留待显式retry，任何单项错误不跳过其余SQLite/预算/模型清理。I2：native async准入从connect/TLS前开始粘性记录，同步close在修改资源前拒绝直接SDK异步使用、进行中交接及客户端关闭，真正未使用的异步SDK仍可同步收尾。

本轮31个真实socket/SDK子进程用例，含原始native close故障/全局取消、caller重复取消、TLS/legacy、read与handshake timeout、inflight connect、同步TLS交接及executor全局关闭、retired/privatechild、四项并发成功/共享失败重试。保留实际socket至loop退出后核验closed，FD4→4、无新增存活线程，不以SDKclosed/GC/进程退出证明关闭。最终四项并发测试还在独立临时wheel副本中被“独立关闭+幂等删除”突变全部触发，证明不是仅吞list.remove错误。

新增实际Python3.13精确发行锁环境时，离线cache缺cp313 pydantic-core2.46.5，按官方PyPI哈希锁补齐而未替用版本。首轮687项1失败：phase21 SQLite故障闸门把setup的Cursor.close当Connection.close，导致取消发生在真正native close准入前；3次原生诊断/对照确认。强化任务身份断言先RED，再把测试闸门限到实际sqlite3.Connection.close；SQLite产品代码与最终wheel不变。3.13对应原生用例3/3 GREEN后，完整三组验收重跑，不隐去首轮失败或沿用修正前证据。

| 最终验收 | 项数 | 跳过 | 耗时 |
|---|---:|---:|---:|
| 完整Python3.11 | 2142 | 48 | 360.415s |
| 工作区外实际pip安装Python3.11 | 687 | 5 | 212.321s |
| 工作区外实际锁定Python3.13 | 687 | 5 | 221.646s |

两组真实安装均在工作区外运行，44模块来源/158发行Python字节一致；Python3.13.13实际运行锁定OpenAI3.26.0、HTTPX0.28.1/HTTPcore1.0.9、HTTPX2/HTTPcore2 2.13.1、aiosqlite0.22.1、pydantic-core2.46.5、langchain-deepseek1.1.1。JS17/diff、实际安装CLIhelp/JSONdoctor/依赖smoke通过；无真实模型API。wheel SHA256 `5e71fd04fdf597b5137b9ef246cb0380c34f712692e9156e7354466ec5213ccc`。最终日志 `/tmp/ignovate-model-http-accepted-final-full-python.log`、`/tmp/ignovate-model-http-accepted-final-installed-native.log`、`/tmp/ignovate-model-http-accepted-final-matrix313-native.log`；预复核及错误SQLite闸门的中间结果不作最终验收。

Deferred minors：M1默认backend的包名前缀检测也接纳公共HTTPcore/HTTPcore2 MockBackend，因其无native socket导致正常关闭失败；实际OpenAI未装饰控制正常。默认Factory不选择该backend，当前无默认native资源丢失证据，保留Minor待后续兼容修复，本次C/I pass未修改。

全部21项裁决（含review全部8项Declined）和代价按时间顺序如下：

1. Ruling: track native streams at backend acquisition rather than close-time pool snapshots — requests and SDK close can detach before retry; cost: instance-level default pool backend adapter coupling and per-live-stream record, no globalpatch or protocol rewrite.
2. Ruling: protect only native HTTP final settlement in a dedicated Task, never SDK requests/connect/read/write or SQLite helper reuse — global cancellation otherwise loses cleanup Task; cost: native cleanup/connect join may delay cancellation with no bound for permanent I/O, custom task_factory not used for this final owner.
3. Ruling: seal native backend admission on a non-cancellation stream close failure — continuing new connections after unverified retirement can accumulate detached sockets; cost: failed backend requires explicit resource close/replacement instead of silently opening another socket, caller cancellation alone does not seal ordinary model reuse.
4. Ruling: count TLS upgrades as resource handoffs and serialize per-stream close/upgrade — sync SSL can detach old socket before returning new SSLSocket; cost: close waits original SDK handshake deadline/completion and needs stream locks/counter, no premature pointer/record removal.
5. Ruling: async resource close/release joins sync HTTP clients through the executor Future, not an asyncio creation Task — preserve loop progress and globalcancel join; cost: uses default executor capacity and may wait native sync TLS handoff, synchronous-only close stays blocking and SQLite/budget closure stays originalthread.
6. Ruling: decorate only supported default HTTPX/HTTPX2 pool backends, keep custom transports' existing close contract — product creates fresh default SDK clients; cost: custom/unknown external transports do not gain native guarantees, already-used default clients rejected because acquisition cannot be retroactively owned; SDK private-pool interface upgrades need native gates.
7. Ruling: complete the single frozen review with the available fallback after the primary reviewer terminated on usage limit — there was no final report to act on; cost: final judgment comes from 6.1-sol rather than Astra, supplemented by independent native probes, not another review of author fixes.
8. Ruling: share each client's in-flight close operation/result across private release and Factory shutdown — duplicate SDK calls and ownership removal can skip remaining cleanup; cost: per-active-client Future bookkeeping and joining that operation's original failure, failed clients retained for explicit retry, no historical completion cache.
9. Ruling: run the first async SDK close inline on the existing owner task and let other callers await its plain completion Future; reuse the actual executor Future for sync clients — a new ordinary SDK-close Task can lose startup during global cancellation; cost: first caller performs SDK close, Future bookkeeping must settle every BaseException, native-only cancellation protection remains in the HTTP owner.
10. Ruling: make native async admission sticky from before connect/TLS handoff and reject synchronous close before resource mutation, also reject active per-client close operations — exposed SDK use bypasses async_runtime flag; cost: even a failed/cancelled admitted async connection requires original-loop aclose; genuinely unused async clients retain synchronous-only cleanup.
11. Ruling: target the concurrency fixture gate at the exact same-client close entry rather than async-list entry — parent SDK close can yield before the child is reached; cost: fixture uses the per-client helper as the admission gate, while outcomes still inspect real sockets/SQLite/SDK and shared failures.
12. Final: Ruling: review declined unrelated accepted phases1–21 — keep exact phase21-wheel delta as this review boundary, include directly relevant inherited I1/I2, run author whole suite; cost: this review does not independently re-certify previous phases or close the whole goal.
13. Final: Ruling: review declined arbitrary custom transports' native guarantees — preserve the specified SDK close fallback contract, with M1 explicitly deferred as its known non-native-backend compatibility gap; cost: custom transports gain no native-handle guarantee and affected public MockBackends currently require a later compatibility fix.
14. Final: Ruling: review declined retroactive ownership of already-used external default SDK clients — reject before decoration because prior streams have no acquisition receipts; cost: embedding integrations must supply fresh clients and cannot import their old pool into this ownership guarantee.
15. Final: Ruling: review declined post-install transport/mount/backend mutation and shared HTTP client across independent Factories — do not claim support beyond product fresh-client instance construction; cost: integrations doing either must manage those resources themselves or establish a separate ownership contract.
16. Final: Ruling: review declined bounded shutdown for permanently stuck native connect/TLS/close — join admitted finite settlement rather than falsely clear resources on deadline; cost: permanently stuck native I/O can block cleanup and cancellation indefinitely.
17. Final: Ruling: review declined cleanup after the originating loop was discarded without aclose — require original-loop explicit aclose and fix I2's still-live-loop early rejection; cost: embedding callers remain responsible for draining that loop, with no new global registry or recovery loop.
18. Final: Ruling: review declined optional HTTP2, HTTPS-proxy TLS-in-TLS, other OS, Trio/custom loops and future SDK private interfaces — keep only demonstrated macOS asyncio/direct TLS/HTTP-forward-proxy claims, add the actual locked Python3.13 installed matrix without extrapolating beyond it; cost: h2 is absent and unrun protocols/platforms/loop/version combinations still need native certification.
19. Final: Ruling: review declined whole-project long-task stability/completion — accept only this phase's tested resource ownership after the single C/I fixpass, leave the original goal active; cost: long-history capacity, actual workspace historical dangling link, cross-platform and further fault interleavings still need work.
20. Ruling: fill the owned Python3.13 test environment using exact release-lock hashes from official PyPI after offline cache lacked pydantic-core2.46.5 cp313 — an alternate cached version would not test the shipped lock; cost: dependency downloads and an owned temporary environment, no model API/account interaction and no change to the production lock.
21. Ruling: restrict the SQLite shutdown fault gate to actual Connection.close and assert runtime:sqlite-close identity — a setup cursor close was falsely signalling admitted native resource closure, exposed reliably on3.13; cost: test fixture changes beyond the initial two-file model delta and repeat full/native installed matrices, no change to SQLite product semantics or premature-task cancellation support.

完整过程及一次review报告见本轮计划与独立review归档。原始goal继续active：跨平台/协议/事件循环矩阵、完整历史容量、实际工作区旧外部失效链接和更广故障交错仍待推进。保留codex/adaptive-task-runtime和用户IDE/CSS，无提交、推送或合并。保留本轮自建的精确锁CP313临时环境供后续矩阵，不删除用户历史构建；仅本轮scratch在归档后移除。
