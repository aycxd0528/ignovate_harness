# GUI 项目任务来源与控制身份：浏览器发现、修复与最终验收

上一goal turn完成phase32两项测试Minor、两版2202源码/799实装、唯一完成review与字节门，属于有效进展。原目标继续active。本轮从真实浏览器验收发现多会话任务来源问题，再以真实状态复现同修订号替换任务的控制身份缺口。实际生产基准为phase31/32已验收wheel SHA256 `85fb1423e204fcc4f7fcdd80f8ced29b61d355f55a9f2732c0f21567275f19bc`；修复已有GUI投影和控制提交，不放宽真实来源path/版本检查，不更改TaskStore权威记录、budget/权限或数据库schema。

## 浏览器证据与错误归因排除

使用现有Chrome155.0.8059.85及缓存Playwright1.64.0-alpha-1790635538000，无下载/依赖改动。自有临时profile/项目/配置、随机127.0.0.1端口，页面非本服务origin请求一律abort；记录blocked列表，实际各场景空。Python313真实site-packages中的agent/controller运行现有离线模型fixture，实际文件/命令/SQLite/HTTP均保持原生，非真实远端模型API。父运行器的浏览器子进程上限120s、启动20s、服务关闭15s；不把其他容量探针的240s/512MiB说成本轮资源额度。每组记录controller/agent来源、页面/命令/点击/snapshot、服务后代进程、退出/owned目录/FD/线程；临时数据组退出后删除。脚本均在/tmp，未操纵用户已有浏览器profile/tab。

丢弃式运行器 `/tmp/ignovate-run-browser-acceptance.py`、`ignovate-browser-fixture-wrapper.py`、`ignovate-browser-acceptance-run.cjs`。既有CJS只在进程内把固定8767 origin换成该组随机端口，所有行为断言保持；截图重定向到 owned `/tmp/ignovate-browser-acceptance-evidence/`，保留源码SHA。自建lazy-long场景先写入真实压缩checkpoint、关闭原Factory，再以service=None的GUI重开；禁止构造模型，浏览器全文逐字（仅规范空白）比对4000段正文+原始头尾及刷新前后长度，不只检查末尾。

首次chat-1启动读取使用TextIO.readline加select，第二行已在Python缓冲区而FD未ready，运行器错过SERVER行；修正为os.read整块拆行，未改产品。chat-2/3/4在第三次消息等待超时；实际pointerdown/up/click均已是“停止”，API确实为stop，后端保留消息草稿。旧测试只等SQLite节点，不等UI snapshot/React commit，Playwright持有的同一DOM按钮已变为stop。增加显式可见完成、无live turn、发送按钮状态条件后chat-5通过，XSS/3回合/分叉/刷新/窄布局原断言不删。没有将此改成产品发送/停止策略修复。

初次资源计数4→5不能直接归因GUI泄漏：两版纯标准库 `socket.getfqdn('127.0.0.1')` 均独立造成4→5，随后8次ThreadingHTTPServer创建关闭都保持5。最终先记录该冷初始化，再以5为基线检查GUI自有资源返回5、无新增Python线程/服务后代进程。日志 `/tmp/ignovate-browser-resolver-control311.log`、`...313.log`；没有强关这个原生名称解析初始化产生的进程级FD，也不伪称冷启动4→4。诊断使用lsof/weak socket记录，最终通过组不再替换socket类。

旧85fb实装的tools-2、conversation-1、map-live-1、lazy-long-3真实浏览器通过：并行命令/停止、批准/文件产物/刷新、地图运行中拖动/真实文件diff/刷新不重放，以及86KB级压缩checkpoint在关闭模型后浏览器恢复完整28060字符（4000段正文）/刷新不新建run。均FD5→5/无线程/服务无后代、owned目录空。它们是旧基准发现门；新包最终组另行记录，不能据此宣称新包全部已验收。

## 真实产品根因与有界修复

work-control-1在关联空会话后无法进入反馈；进一步work-control-2从真实阶段回执打开owner会话，保存仍失败：“请选择真实来源回合”。HTTP证据明确是owner的node_id搭配关联空会话branch_id，后端正确拒绝。TaskStore._project会按thread ID排序associated_thread_ids；GUI projection原先直接取第一个关联branch当来源。因此随机ID决定路由，空关联可能顶替仍持有该任务的真实owner。task_summary仍显示owner线程，路由却指另一个分支，来源不一致。

修复在`nailong/gui/work_control.py:projection`：当原生owner仍在此任务associated集合且GUI分支存在时优先选owner；owner已开始独立新任务、不再绑定原任务，或owner分支不可用时，保持既有已知有效association回退；没有已知分支仍None。不改变记录/来源node/任务revision，也不放宽_feedback_source对真实path与task身份的检查。任务与attention使用同一正确route。

中间曾在CJS补“空关联会话一律拒绝反馈”的诊断断言；这过强，因为控制项若提供了真实owner来源可以合法整理反馈，已完整撤回，`gui-work-control.spec.cjs`最终逐字节恢复原SHA `c062e52ed4476f41f094643998dc0ffdb146f7cdb10e0a6ff68e89b6612aec7e`。该诊断不是最终改动，不通过改变合法反馈契约刷GREEN。

新增两个永久Python用例：`z-owner`与`a-linked`强制错误排序，实际TaskStore/BranchStore/异步snapshot检查任务与人工attention均指真实owner，并用投影route成功提出带原生node引用的反馈；同时覆盖只给linked分支/无可用分支的保守回退。第二例owner新建独立任务后，旧任务仍路由有效linked，set_wait只修改旧任务，不污染新owner任务，不启动模型。首个真实RED .065s为a-linked≠z-owner。补control时误把可缺省wait_condition当必有键产生KeyError，修正测试为.get；初次74相关组因此1ERROR，日志保留不称通过。最终两个方法在实际已安装85fb313独立1FAIL/1control GREEN .103s（不是fixture ERROR），相关74项5.634s GREEN。

## 冻结与初始裁决

冻结3路径：生产`nailong/gui/work_control.py`，测试`tests/test_gui_work_control.py`与`tests/gui-browser.spec.cjs`。生产基准来自85fb wheel，两test修改前全文位于 `/tmp/ignovate-gui-task-source-baselines/`；不以HEAD/所有dirty为基准。联合diff `/tmp/ignovate-gui-task-source-review.diff` SHA256 `69d5adb40850ec2ea05bd8731e9f37bfac802bba91a1ba458b7e0a1e44f4884d`。联合新wheel `/tmp/ignovate-gui-task-source-final-wheel/ignovate_harness-1.2.1-py3-none-any.whl` SHA256 `34c5832dd0d71a6b5e5d542e9c4a70c37435725b4d3dc771fad4f4ee5113c276`，完整manifest与旧包相同，仅work_control.py及RECORD内容变更；159Python/39GUI资源均与当前source相同。313离线no-deps实装后77 distributions/5616files RECORD/hash issues空。最初仅routing的32a5 diff及1c3 wheel在review派发前因下面的新真实缺口而扩充，旧证据保存为route-only-review.diff、route-only-attestation.json、route-only-wheel，不把中间候选当联合最终验收；当时唯一最终review尚未派发，现已完成，见下方处置。无实际用户DB改写、真实API、提交/推送/部署，用户IDE/CSS及全部前序工作保留。

R1. Ruling: 原生owner仅在仍绑定该任务且分支存在时优先，否则保留有效association/None回退；cost: 确定来源路由会与过去按字典序偶然选择不同，owner移到新任务时不能按旧owner存储身份强行路由。

R2. Ruling: 项目任务与attention共享route，canonical任务记录、权限、费用及控制后端不变；cost: 来源选择修复不扩成控制权转移，也不修其他GUI命令短暂保守busy窗口。

R3. Ruling: 反馈继续要求真实来源path、任务身份/revision和当前输入，不伪造空会话来源；cost: 未知/过期来源仍拒绝，不能仅因存在别的association就宣称有有效反馈证据。

R4. Ruling: 浏览器测试等可见完成再点击发送/停止共用按钮，原XSS/分叉/刷新断言保持；cost: 依赖当前DOM结构/状态，不新增产品发送按钮策略或任意UI竞态保证。

R5. Ruling: 使用实际隔离Chrome/实装Python/原生工具并单独记录纯stdlib名称解析冷FD，再检查自有资源回落；cost: 本机缓存浏览器/解释器与离线fixture边界，保留进程级冷FD，不外推其他OS/真实模型质量/无限session或全部系统资源上限。

R6. Ruling: 以85fb精确联合3路径增量做唯一完成fresh review，作者一次C/I pass并承担最终新包2206源码/803实装两版门及六组浏览器；cost: tests不在wheel，focused/字节一致/本阶段不能替代整个goal完成审计，不为其他dirty内容背书。

## 联合发现：同修订号的替换任务不能接收旧控制请求

在首次routing候选尚未review或全量验收时，进一步审查同一work_control提交路径发现：payload携带task_id但后端未核对。真实状态回归用旧Task A和新Task B相同revision、相同人工验收ID和合法B来源，旧A的控制请求在set_wait、clear_wait、confirm_acceptance、feedback_stage、feedback_confirm五个子场景均被接受，既可能改B状态，也能生成/确认B反馈。2方法.295s为5个预期断言FAIL/无fixture ERROR，历史反馈撤销control GREEN，日志 `/tmp/ignovate-gui-task-source-identity-red.log`；实际已安装仅routing的1c3候选四方法同样5FAIL/其他3control GREEN，日志 `route-only313-identity-red.log`，不是仅从代码猜测。

联合修复仍只在原模块：_check_control_task在异步扫描之前和最终同步提交时检查当前任务及显式task_id，五类当前任务动作不接受其他身份。set_wait/clear_wait复用TaskStore.bound_task持有同一原生任务/版本锁再调用原有可重入mutator，堵住检查后/写入前换绑的窗口，不在持锁期间await。其他三类原有bound_task与完整观察比较保持。显式link_task的target身份、readonly status与按feedback记录id/revision撤销历史经验不套用当前任务ID检查；owner换新任务后仍可撤销已确认的历史反馈。

新增两个永久方法保护五类同revision拒绝后的Task/session事件/feedback完整保持，以及已确认历史反馈在owner新任务后的合法撤销。四个新方法连同原组共76项5.823s GREEN。原TaskID缺省的旧Python调用继续针对当时真实当前任务并受revision/native bound锁保护；显式提供错误/空/None身份保守拒绝，不从revision相同推断同一任务。联合最终包/浏览器/完整门以34c5及69d5身份为准，之前final33标签属于1c3 routing候选，不冒充联合结果。

R7. Ruling: 五类当前任务控制同时核对显式task_id和原有各自版本契约，在扫描前和提交时拒绝错配；cost: 旧/空的显式身份会报错，caller需要刷新，未提供身份的旧Python接口仍保留当前任务语义，不把它升级为陈旧UI授权。

R8. Ruling: 等待变更复用现有原生bound_task锁，确认/反馈保留原绑定；历史反馈撤销和显式关联目标按其记录身份继续工作；cost: 短同步文件事务仍受系统IO时延约束，不新增硬取消/无限时限承诺，也不把历史撤销错误地绑定到新任务。

- [x] 原生浏览器发现、点击/API/纯stdlib对照、两类确定性RED/旧包负对照与76相关GREEN。
- [x] 3路径冻结、新包精确manifest/字节和313真实安装完整性。
- [x] 唯一fresh完成review、作者一次C/I pass及全部considered/Declined决定和代价。
- [x] 最终远端整合包两版2214源码/807实装、六组真实浏览器、CLI/JS/字节/环境验收；旧2206/803为I1前中间门。

## 唯一审查与作者一次 C/I 处置

唯一 reviewer `/root/gui_task_source_review` 在用户中断后续完原审查，没有新增第二次 review。原文 `2026-10-24-gui-task-source-review.md`，SHA256 `ef377f485e0e281049b359c2d78d08f0be095f2fcb7a0ccbc36d1a7a40773399`。结果 0 Critical/P0、1 Important/P1、0 Minor。独立76项7.610s、15个显式无效身份拒绝、2个同修订号换绑间隙拒绝通过。

I1为既有 lazy service=None 控制死锁，85fb与34c5均有原生有界子进程复现：外层TaskStore A持bound_task，最终_execute新建B后snapshot等待A文件锁。作者完整阅读报告并执行唯一C/I pass：扫描时捕获的tasks显式传入_execute，保留完整状态比较及原生可重入锁，不await持锁、不加载模型。永久子进程回归依次真实人工验收、反馈candidate到confirmed，检查service仍None/runs空。旧实现最终oracle在30.012s超时FAIL；新实现0.302s GREEN。初稿把不存在的user_decisions当结果字段，尚未走到即被死锁挡住；最终RED前已更正为原生manual passed evidence，初稿日志保留，不以错误字段刷通过。无二次review。

### 全部审查裁决与代价

- Ruling R1至R8：逐项采纳原初八条及reviewer同名建议，代价维持上文。R6最终候选因I1与远端整合更新；69d5/34c5仍是独立审查对象，新包须新验收，不冒充reviewer重审。R8补充同实例捕获/传递，同步IO时延边界保留。
- Ruling C1：任务与反馈记录revision分别使用；cost：调用方须提供对应记录版本，数值相等不代表同一身份。
- Ruling C2：保留扫描后project/branch/node/完整task/native绑定比较；cost：并发变化可保守拒绝、要求刷新，不保证外部项目不变。
- Ruling C3：接受等待写入bound_task及两个独立间隙探针；cost：真实换绑后旧请求须刷新重试。
- Ruling C4/I1：不排除已复现lazy缺陷，完成上述一次修复；cost：新增实例参数和有界回归、重建包并重跑门，无第二次独立审查。
- Ruling D1：不自动搜索或重建反馈来源节点，保留显式来源校验；cost：仍可能须选择合法来源或显式关联，不是历史修复功能。
- Ruling D2：保持发送/停止产品策略，测试等待可见idle；cost：不保证任意时序，断言耦合当前DOM契约。
- Ruling D3：保持本机离线有限容量、浏览器和资源证据边界；cost：不认证无限会话、其他OS/runtime、真实模型或全部系统资源上限，不把纯resolver冷FD当GUI泄漏。
- Ruling D4：独立审查仅三路径；cost：旧dirty工作、GitHub整合与全目标由作者另做实际门，本报告不能单独支持整体完成或发布。

### 中间门与远端整合

I1修复前34c5两版源码2206项各48/49skip、393.255/398.275s；两版实装803项各5skip、241.516/246.301s，实际句柄terminal0。六组combined33浏览器均terminal0。这些是中间证据，不能当作I1后最终修复验收。

用户新授权完成后提交GitHub。fetch发现旧分支落后远端且PR3已合并；原生create_worktree创建隔离目录 `/Users/aycxd/.codex/worktrees/9eec/LLM_project`，以origin/main `4260136` 新建 `codex/long-task-reliability`。独立临时index导出188个已授权路径，原工作区index/IDE/CSS不变。三方整合三处冲突：CLI保留远端Windows记录和新增checkpoint说明；empty_conversations保留closing+lstat；测试保留远端连接释放回归、显式commit及全部新故障回归。自动整合保留远端stdin DEVNULL及Windows GUI入口/runtime_sources。六个新增Python文件仅清理空白EOF。最终包沿用main的1.2.3版本，无tag/发布/部署。整合后最终门另列，不用整合前结果顶替。

## 最终提交版本的实际验收

最终整合wheel SHA256 `25e5d283e1c266f6c231285c869a16056a4401003a7a461027601f5b0caad919`，不是独立审查前34c5或首次整合1214。相对34c5，生产额外变化为作者I1修复、远端已有stdin DEVNULL/Windows GUI入口/runtime_sources，以及排除用户CSS；版本保持main的1.2.3。159Python与39GUI资产均与最终提交工作树逐字节一致。

| 最终范围 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| Python3.11源码全量 | 2214 | 49 | 413.133s |
| Python3.13源码全量 | 2214 | 50 | 416.471s |
| 工作区外安装3.11 | 807 | 5 | 249.343s |
| 工作区外安装3.13 | 807 | 5 | 248.517s |

实际句柄23117/31790/62523/15718均读取terminal exit0。两实装各48个关键模块来源为安装目录；313按原锁环境77 distributions/5616 files逐项RECORD/hash无问题。311 OpenAI3.26.1与313锁3.26.0不混称同一依赖。19项JS、desktop check含Windows打包脚本语法、最终wheel CLI help/JSON doctor/dependency smoke及diff检查均通过。单独误用仅.cjs glob的首个JS命令只运行5项，不冒充19；随后明确.cjs和.mjs联合运行19项exit0。

最终六组Chrome验收标签github34：chat、work-control、lazy-long、tools、conversation、map-live，均使用最终实际313安装包，spec来自整合工作树；服务和浏览器exit0、FD5到5、无新增线程、无服务后代/自有目录遗留、无非服务请求、无剩余browser。lazy-long在关闭Factory/模型后全文28060字符、4000段正文和头尾独立比对及刷新均通过。其他场景覆盖真实来源反馈candidate/confirm/revoke、工具停止、审批产物、地图diff与刷新不重放；不是远端模型质量保证。

完整最终日志/哈希/浏览器结果与实际终态记录 `2026-10-24-github-validation.json`，原始日志 `/tmp/ignovate-github-final-*`、浏览器证据 `/tmp/ignovate-browser-acceptance-evidence/*-github34/`。新增lazy回归将原始2206/803以及整合前2213源码候选结果区分，不拿中间门替代最终。全部初始R1至R8、review C1至C4/D1至D4及代价均保留，I1关闭，无第二次review。当前已确认未修复P0为0，三项GUI P1至此修复并完成本地验收；原整个项目goal继续active，平台/远端/任意故障等边界不变。用户已授权下一步GitHub提交；本地验证不等于已合并、发布或部署。
