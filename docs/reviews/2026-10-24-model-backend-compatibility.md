# 默认模型 backend 识别与自定义关闭兼容

## 有界设计

原始goal继续active，上一轮属于有效进展。当前真实验收基准wheel SHA256 `c24d068c01ba9023aff237678e916c807e563247b7fefc8ea2a125ff8d2ed86a`，完整2150（31148skip/31349skip），实际安装705/5skip两组，158Python45paths，JS17。保留所有旧变更、IDE/CSS、分支；无提交推送、真实模型API。

本项是已记录M1的新独立有界修复，不进入phase22已结束的C/I pass。当前重新运行旧2项probe仍RED，/tmp/ignovate-custom-backend-current-red.log。包名前缀httpcore/httpcore2无法区分默认原生backend和公共MockBackend；后者stream没有socket，正常close被误判为原生关闭不可核实。

通过精确类身份仅接受各家默认SyncBackend与AutoBackend；不按类名字字符串判断、不以缺少socket视为custom已关闭。custom（包括显式选择其他native backend）继续SDK关闭契约。所有mount pool先核验再装饰，混合custom/default返回原契约且不部分改写。保留默认client原生stream所有权/并发关闭/失败重试/SDK timeout与已使用默认pool拒绝逻辑，不改全局SDK或请求协议。

- [x] 实际OpenAI+HTTPX/HTTPX2四组sync/async custom/mock及mixed mounts行为先RED；fallback后请求OK与资源关闭、原backend不被改写。
- [x] 最小class identity识别修复GREEN，原31项真实native socket回归继续保护默认路径。
- [x] 单次fresh review精确production基准增量，0C/0I无补修无复审，Minor记录、Declined逐项裁决。
- [x] 完整311/313、实际wheel安装字节/来源、CLI/JS/diff核验，所有失败/裁决归档。

## 裁决

Ruling: 为已记录M1另立有界实现任务，不塞回旧C/I pass — 该兼容错误已有实际SDK复现，原goal要求继续修复；cost: 另付独立测试/review/全量验收，不改旧轮历史评审结论。
Ruling: 默认backend按实际类身份识别而非package/name字符串 — 公共MockBackend与native类共享package，子类或自定义实现不应被默默接管；cost: SDK私有默认类路径耦合，未来版本须原生验证，显式AnyIO/Trio等custom backend沿SDK原契约而不获得native保证。
Ruling: mixed mounts整客户端预检后再装饰 — 返回fallback前改写部分pool会留下未登记native owner；cost: 一处custom mount会令该客户端全部沿原SDK关闭契约，不提供混合native保护承诺。

正式RED4方法/8个实际SDK子场景（两家HTTPX×sync/async×纯custom/mixed），4方法共8个RuntimeError，.070s /tmp/ignovate-model-backend-red.log；错误来自native socket None关闭核验，实际请求和关闭都失败，不是夹具拼写错误或联网。新test只使用公共MockBackend真实HTTP字节解析，断言目标URL已选择该transport才发起，trust_env=False且max_retries=0；无DNS/远程请求。RuntimeResourceOwner实际取得/释放SDK客户端，无自造close计数替代效果。最小默认class身份识别已实现，后续native与Factory回归进行中。

首轮68项30.935s GREEN，含4方法兼容与31项native回归。作者再用真实loopback预热mixed客户端默认pool：现顺序在看到后续custom mount之前就因已有连接拒绝，违反该整客户端fallback契约。新增2方法/4家sync-async子场景先RED /tmp/ignovate-model-backend-warm-red.log；改为先完成全部backend资格核验，再仅对全部eligible的客户端拒绝已用pool。custom混合客户端沿SDK关闭已有真实socket，保留原生socket核验关闭；纯默认已用pool仍拒绝。已安装后改动mount/backend及跨Factory共享的更广契约保持先前边界，不把本修复扩为支持任意热变更。

完整相关72项35.053s GREEN，/tmp/ignovate-model-backend-complete-targeted.log；新增8方法/16实际SDK子场景。最后2方法为既有纯默认已用pool拒绝的原生对照（无需改变其生产行为），失败接管不改backend、不关闭调用方客户端；调用方最终关闭实际socket。所有warm流量仅自建127.0.0.1有限HTTP服务，另用公共MockBackend回放`.invalid`请求不触发DNS。原31 native故障/取消/TLS/SDKdeadline仍通过。

冻结增量 `/tmp/ignovate-model-backend-review.diff` 交给单次fresh Astra reviewer；实际phase24 wheel基准、生产差异、新测试和有界契约一起给出，不以HEAD当作基准。作者补跑实际默认HarnessChatDeepSeek/OpenAI/HTTPX2本机SSE长目标：3.11源码与3.13实际新wheel安装均9round/19request、92累计tool-result、7次构建失败后压缩/暂停/Factory重开/恢复/修复/验证完成，2090tokens及0.0007980000000000003费用连续；外部修改交付物后降为unverified。两组FD4→4，无新增线程，日志 `/tmp/ignovate-model-backend-native-goal311.log`、`/tmp/ignovate-model-backend-native-goal313.log`。这是既有探索探针的当前包重跑，非新增保留CI测试，也未调用真实provider。

## 单次评审与全部反馈裁决

Fresh Astra最终0C/0I/1M；独立8方法/16子场景4.389s通过，另6项补充探针验证两家同module/name的SyncBackend/AutoBackend子类实际SDK请求/关闭及显式AnyIOBackend空客户端准入/关闭。AnyIO补充探针没有网络I/O。无production revision请求、无作者C/I补修、无二次review。完整报告归档 `docs/reviews/2026-10-24-model-backend-compatibility-review.md`，原报告 `/tmp/ignovate-model-backend-review-final.md`。

以下依次承接前3项Ruling，覆盖review全部9项Declined及新M1；旧phase22 M1为真实兼容错误，新phase25 M1仅为当前行为已通过探针但未持久化的测试覆盖建议，两者不混同。

4. Ruling: 接受本轮M1为非阻塞覆盖待办，留到独立测试扩充 — 当前精确类身份行为已被review真实SDK探针验证，缺陷未复现；cost: 未来改写predicate时，保留CI尚未直接保护同module/name子类和显式AnyIO边界，本轮不借C/I pass追加Minor。
5. Ruling: Declined1旧dirty生产变化与历史评审不在此次冻结增量中重新认证 — 实际基准是上轮已验收wheel，保留所有旧工作；cost: 本轮独立结论只及两文件，不能据此关闭整个goal。
6. Ruling: Declined2接管后的mount/backend热变更继续不支持 — established owner的既有early return不构成动态接管协议；cost: 做此类外部变更的集成方须自行管理，新的pool不自动获得native保证。
7. Ruling: Declined3跨独立Factory共享client/transport继续不承诺独立生命周期 — wrapper重用不是多租户引用计数；cost: 共享集成须提供独立所有权契约，否则不能从本轮推导隔离关闭。
8. Ruling: Declined4并发request与首次接管不支持 — 产品在发请求之前构造并接管fresh client，资格检查/装饰不加通用事务锁；cost: 外部接管方必须先停止请求，竞态首次接管仍未认证。
9. Ruling: Declined5任意内部monkeypatch不由精确类身份背书 — 本轮识别真实默认类，不认证被替换的方法/AutoBackend delegate；cost: 修改私有实现的调用方可能破坏native契约，须自管或另证，不把同一type当作完整实现证明。
10. Ruling: Declined6自定义async SDK使用后仍须原loop调用aclose — custom fallback没有默认native owner的started信号，本轮不扩建独立活动跟踪；cost: 自定义async直接使用后同步factory.close不具默认native准入guard保证，测试实际使用aclose，不宣称该边界已解决。
11. Ruling: Declined7混合/显式custom客户端保留整个SDK关闭契约 — 正常warm socket关闭已实际验证，但不借此承诺失败重试receipt；cost: SDK在故障下丢失底层句柄的更强保护不适用于fallback客户端。
12. Ruling: Declined8未来SDK私有路径、Trio及其他OS须各自原生门 — 当前两家实际默认类身份已验证，路径缺失则fallback；cost: 升级可能失去native保护，未运行平台/loop不由macOS asyncio两版本外推。3.11当前OpenAI3.26.1，3.13实际精确发行锁OpenAI3.26.0，两者不混称同一锁。
13. Ruling: Declined9包级/实际安装验收由作者最终门负责 — reviewer只独立认证有界增量并读取相关72项记录；cost: 必须等完整311/313、安装来源/字节、CLI/JS和命令退出后才接受，不以独立评审结论代替交付证据。

## 最终验收

| 实际范围 | 总项数 | 跳过 | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2158 | 48 | 370.983s |
| Python3.13源码全量 | 2158 | 49 | 375.507s |
| 工作区外实际安装3.11 | 713 | 5 | 219.880s |
| 工作区外实际安装3.13 | 713 | 5 | 226.090s |

四组命令实际退出0，四个最终日志均无ResourceWarning；日志 `/tmp/ignovate-model-backend-final-full311.log`、`full313.log`、`installed311.log`、`installed313.log`（同前缀）。两组实际安装均158发行Python字节与源码一致、45关键模块来源在安装目录。3.13精确发行锁环境77包依赖兼容；3.11现有OpenAI3.26.1、3.13实际3.26.0，不以现有venv冒充精确锁环境。JS17/diff和实际安装CLIhelp/JSONdoctor/依赖smoke通过；`/tmp/ignovate-model-backend-final-js.log`、`wheel-smoke.log`、`matrix313-install.log`（同前缀）。

最终wheel `/tmp/ignovate-model-backend-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`，SHA256 **`b37090a696f16a17ed3b934000acc860fdb54e6ec779ab9bc8b03cf1cd696894`**。单次review冻结两文件与最终生产/测试字节相同（后续仅归档文档），没有C/I补修或复审。phase22旧M1实际错误至此通过RED/GREEN与最终门关闭；本轮新M1仅是上述覆盖待办，原报告不回写。

本轮无真实模型API、无提交推送合并，保留所有前序/用户IDE/CSS和当前分支，无scratch待清。原goal继续active：更晚MCP通知顺序、更大历史容量、跨平台/loop/协议矩阵、实际工作区历史外部链接和其他故障交错仍需独立推进。有限自建SSE交付证据不替代真实provider长期稳定性认证。
