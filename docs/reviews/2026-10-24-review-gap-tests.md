# Phase32：两项测试缺口修复与验收完成

上一goal turn完成phase31生产无损压缩及200回合/1400完整恢复、两版源码与实装最终门，属于有效进展。本轮是phase27 AnyIO fixture close异常清理Minor、phase30可选summary永久语义覆盖Minor的独立测试维护；原goal保持完整且active。范围是现有两test文件，不新增产品行为/依赖/发行格式，不夹入旧C/I pass。生产以已验收wheel `/tmp/ignovate-checkpoint-compression-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl`、SHA256 `85fb1423e204fcc4f7fcdd80f8ced29b61d355f55a9f2732c0f21567275f19bc`为基准；tests不在wheel，两个before文本于修改前单独保存 `/tmp/ignovate-review-gap-test-baselines/`，不以HEAD/所有dirty作基准。

## 已复现、最小修改及证据

AnyIO显式backend测试的finally原先先await sdk.close再shutdown/close/join本地server。最后close异常会跳过server清理。新增永久回归直接调用真实既有fixture，运行httpx/httpx2实际OpenAI请求及真实socket关闭，仅在已closed后的冗余SDK close注入RuntimeError。观察server listening socket与serving thread，先断言原异常仍传播以及资源已关闭，再由回归finally救援它独占的遗漏server。修复前1方法的两个family均FAIL（socket fd6而非-1），1.868s，旧缺陷未由救援掩盖。夹具仅增加nested try/finally，不吞原close异常，不改生产owner/SDK关闭。原命令日志 `/tmp/ignovate-review-gap-anyio-red.log`。

摘要补2方法：不同的请求/skill/action/conclusion/evidence文本保护全部可选段内容及顺序（请求从早到晚、其他从新到旧）；独立literal边界148chars强制可选条目回撤后omission2→3，147只剩omission4，过小余量整个段省略，state保持原文。原有6方法不改。使用现生产renderer正常8项.059s；丢弃式六种错误renderer（全部可选丢弃、请求反序、其他正序、回撤后遗漏计数不更新、不显示遗漏、跳过回撤）分别由新用例捕获，均有预期断言FAIL而非fixture错误。没有改生产renderer；mutant只在/tmp进程内替换test引用，源文件未patch。证据 `/tmp/ignovate-review-gap-summary-mutations.py`及相邻log。

两文件完整20项7.030s GREEN。后续完整与实装门尚需读取实际terminal；不能用focused通过代替。无真实API、外网、实际用户DB修改、提交/推送，用户IDE/CSS与所有旧工作保留。误把smart-explore读取到项目skill目录的只读命令失败，已按catalog读取真实r0路径；当前工具无smart_*接口，后续结构探索用Python AST/精确rg，不安装无关依赖，不视作产品故障。

## 初始裁决与代价

1. Ruling: AnyIO fixture以nested finally在SDK close异常后仍关闭/join它拥有的server，原close异常继续传播；cost: 不吞诊断失败，也不新增server.shutdown自身异常/任意取消的通用保证，生产生命周期未变。
2. Ruling: 永久回归保留真实两家SDK请求和正常native close，只在最终冗余close注入失败，先查实际listener/thread再救援；cost: 依赖当前SDK client类型module及测试fixture接口，未来合法API改动须维护，不模拟真实provider故障。
3. Ruling: 永久摘要断言保护全部可选段的不同文本/顺序与真实回撤/遗漏数量，原生产算法保持；cost: literal标题/148边界与既有格式耦合，未来有意格式改动需维护；不是最大填满/所有容量的永久穷举保证。
4. Ruling: 复用phase31完整wheel并核对所有159Python及48实际来源，test差异以修改前完整文本冻结，作者承担最终2202源码/799实装两版本门；cost: 本轮无新发行包或新产品能力，test/文档不在wheel，不以focused或本阶段为整个goal完成证明。

## 检查清单

- [x] 两项旧Minor根因、真实AnyIO RED和摘要mutation有效性。
- [x] 最小fixture修改及两个新增summary方法，20项相关GREEN。
- [x] 冻结两test的唯一完成fresh只读review、作者唯一C/I pass及所有disposition/cost。
- [x] 原wheel完整字节/实际来源、两版2202源码/799外安装、CLI/JS/diff和最终环境审计。

## 唯一完成的最终审查与作者一次裁决

Primary `/root/review_gap_tests_review` 在产出报告前因usage limit中止、无报告，不记通过。随后唯一fallback `/root/review_gap_tests_fallback` 完成只读审查：0 Critical / 0 Important / 0 Minor。报告原文归档 `docs/reviews/2026-10-24-review-gap-tests-review.md`，SHA256 `a78d038786aca9dddba73fcc6583f1522bf54364b2369b637c5789e3acc67912`。review独立20项6.936s、saved old fixture替换回当前回归得到两个预期FAIL/无ERROR/救援完成、六个renderer mutant全部被断言捕获；冻结diff和两个before文本哈希及159生产字节均独立核对。

作者已完整阅读报告并核对真实fixture/SDK/renderer与最终源/包来源，完成唯一C/I pass：无C/I需修改，不制造修复、不复审；冻结后两测试与生产均未改变。作者明确维持初始R1–R4的全部决定及代价，并将报告全部considered/Declined逐项裁决如下。

### 全部10项 considered 的 Ruling 与代价

C1. Ruling: 接受普通server清理成功时原最终SDK close异常继续传播；cost: 若清理本身也失败，不承诺原异常仍为primary。

C2. Ruling: 接受真实listener关闭与serving thread join的观测；cost: 该fixture保证限其已启动且普通shutdown成功的本地server，不是通用server回收器。

C3. Ruling: 接受httpx/httpx2实际SDK两家回放；cost: 不外推其他OS/loop/未来SDK私有接口。

C4. Ruling: 保留首次真实native close、只在已closed后的冗余close注入；cost: 本回归不新增首次SDK close失败/retry或TLS/provider错误的认证。

C5. Ruling: 接受资源断言在owned rescue之前，旧fixture失败仍被报告；cost: rescue仅为测试独占资源防污染，不是生产重试机制，也不吞原断言。

C6. Ruling: 接受所有可选段不同文本、段顺序、请求早→晚及其他新→旧的literal保护；cost: 标题/格式有意变更时需维护期望，不扩大为任意信息分配最优策略。

C7. Ruling: 接受148边界实际回撤条目后遗漏计数2→3；cost: 精确148依赖当前固定格式，未来格式变更须重新手算，不用UUT计算oracle。

C8. Ruling: 接受147边界仅显示遗漏4的断言；cost: 保留没有可见条目的遗漏通知，并不保证填满剩余空间。

C9. Ruling: 接受更小余量时整体省略可选段，必要前缀完整；cost: 容量不足可不显示任何可选内容，完整资料仍依赖既有state/archive契约。

C10. Ruling: 接受generous和三边界后的输入state逐字相同；cost: renderer级检查不能替代完整state构造/归档/运行时的原生验收。

### 全部6项 Declined 的 Ruling 与代价

D1. Ruling: 接受本轮仅针对冗余SDK close失败、普通server cleanup的边界；cost: 任意server.shutdown/close/join自身失败或反复取消时不新增完整清理/primary异常承诺。

D2. Ruling: 保留既有生产取消、首个native close、TLS/provider故障的独立证据，不由本测试维护重新认证；cost: 本review不提供这些故障的新结论。

D3. Ruling: 接受当前client module身份与直接调用fixture无需setup/teardown的耦合；cost: 将来SDK或fixture新增合法生命周期步骤时必须相应维护meta回归。

D4. Ruling: 接受目标永久边界及六种mutation，不新增所有容量穷举、最大packing、Unicode截断或section公平性策略；cost: 这些属性不能由本轮测试证明。

D5. Ruling: 完整summary-state、archive及更广长任务生命周期继续由其既有验收支撑；cost: renderer测试通过不能替代全部历史/归档/goal行为审计。

D6. Ruling: 作者承担并完成下述source/实装/CLI/JS等最终门，review不重复，整个goal另审计；cost: 单独review只批准两test增量，不证明全目标或发行部署完成。

## 最终门、失败归因及字节

| 实际最终范围 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2202 | 48 | 362.321s |
| Python3.13源码全量 | 2202 | 49 | 366.678s |
| 工作区外安装3.11 | 799 | 5 | 229.297s |
| 工作区外安装3.13 | 799 | 5 | 233.743s |

以上实际句柄55421/52307/57029/97922均读取terminal exit0；JS17、实际CLI help/JSON doctor/dependency smoke及git diff --check通过。159生产Python与39GUI资源全部逐字节相等，两版48实际module origins保留核验；使用原85fb wheel，不重建发行包。最终两test diff仍等于冻结SHA256 `53920c1c887f16b2d34f80ecd9c3d98595873d187a9e01263c5ba2c3d69a205a`，before文本哈希均相同。313最终77 distributions/5616files RECORD/hash issues空。全部门与完整日志哈希 `/tmp/ignovate-review-gap-tests-final-gates.json`；311本地OpenAI3.26.1与313发行锁3.26.0不伪称同依赖。无ResourceWarning不等于所有warning为空，既有forkpty DeprecationWarning与故障注入诊断保留。

首次两个source全量确实各3FAIL、非通过：`test_small_layout_keeps_fields_and_keyboard_help_visible`、`test_unknown_model_resets_manual_reasoning_to_default`、`test_welcome_starts_at_left_and_keyboard_flow_never_saves_early`。启动命令把DEEPSEEK_BASE_URL/MODEL显式置空而非移除，SetupApp因此正确出现空地址/模型的无效配置，三个用例假设的默认表单不成立。实际已验收wheel313的同三测试，env unset→3GREEN/exit0，blank→同3FAIL/exit1；独立review检查对照日志亦确认。没有修改产品/断言掩盖，原失败保留 `/tmp/ignovate-review-gap-tests-initial-full311/313.log`，对照 `/tmp/ignovate-review-gap-welcome-env-{unset,blank,control}.log`，正确runner只移除3变量后完整重跑，最终结果见表。不是因为等待窗口超时重启仍活的任务。

生产没有变化，phase31的实际SDK 9轮/19请求/92tool-result和200轮/1400恢复等证据继续归该阶段，不伪称本轮重新执行。两项已记录测试Minor至此关闭。原goal仍active；后续真实浏览器验收与整体完成审计见 `2026-10-24-project-acceptance-audit.md`，不能把本轮测试维护等同整个项目稳定性已完成。无真实API、提交/推送、部署或实际用户数据库改写，所有前序工作及用户IDE/CSS保留。
