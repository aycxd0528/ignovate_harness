# PR5合并前：Windows原生验证修复

用户已授权修复整个项目，并明确要求合并PR5。合并前实际检查发现Linux两版和Windows桌面通过，但Windows native失败；不绕过失败门合并。当前工作在隔离分支 `codex/long-task-reliability`，基准commit `91315a0961f21d7d168b5fdfb5ea81c1db494974`，原工作区IDE/CSS不变。

## 实际失败

- 安装器job120854353180 / run40087862349：实际无预装Git的安装环境中，输入变更检测回归先因找不到Git失败；84项1FAIL/8skip。
- runtime311 job120854295826 / run40087843078：2214项21FAIL/8ERROR/99skip。多项产物验证invalidated，原因为Windows未启用可变父目录原生保护；嵌套正斜杠路径被_path原生字符串比较拒绝；深Unicode目录在Git子进程cwd启动报WinError267。另一项仅是测试str(Path)期待POSIX分隔符。
- runtime313在原35分钟改动前的20分钟job上限被取消，没有通过证据；不把取消当产品成功。两个branch/PR触发的重复工作流各有相同问题，不以重复次数增加独立证明。

原始日志 `/tmp/ignovate-pr5-windows-{installer,runtime311}.log`，去掉Actions前缀后保留相邻clean.log。没有真实模型API或实际用户数据。

## 有界实现与初始裁决

R1. Ruling: 产物相对路径同时允许原生和规范as_posix形式，但继续拒绝anchor（含盘符相对/根相对）、跳转、别名归一化及受保护项；cost: 不把POSIX反斜杠字面文件名解释成Windows路径，不重写旧manifest或放开任意路径。

R2. Ruling: Windows复用现有pinned_directory强原生共享限制持有可变目录及全部祖先，允许子输出但拒绝父目录替换/重解析；使用Win32 information检查原生HANDLE，只有实际持pin者进入protected；cost: 父目录在验证期间不能被合法移动，取消仍等原生收尾，不用虚假的POSIX fd核验HANDLE。

R3. Ruling: Git缺失时仅在实际项目存在、环境无显式Git身份、项目及祖先无.git标记/裸仓库标记时返回空Git身份；任何未知/仓库情况保持报错。Git存在时仍实际读取HEAD/index；cost: 无Git的非仓库每次核对祖先，不证明外部writer永不改变仓库；快照前后仍各查一次，未知不吞。

R4. Ruling: Windows Git使用短盘符cwd和显式-C项目/core.longpaths，受管原生命令只在cwd达到260字符时通过已有安全路径校验加Win32扩展前缀；cost: 依赖真实Win32/Git/PowerShell行为，必须等实际Windows回归，不从mac模拟推断成功。

R5. Ruling: 仅将输出成员显示断言改as_posix，保留所有功能断言；新增3个可移植边界测试、3个真实Win32目录pin/异常清理/长cwd测试。旧实现真实portable RED为3FAIL/2ERROR（错误是应接受路径/缺失Git被拒绝的产品表现），2个native skip；最终6方法portable GREEN/native3skip；cost: mac上不得宣称native通过，实际全量/安装器必须由CI证明。

R6. Ruling: runtime job上限20到35分钟，单个原生/async测试仍保留120秒watchdog；cost: 整套Windows门允许更长墙钟，未减少测试或放宽业务超时。两版全量完成后才判断合并。

R7. Ruling: 唯一fresh只读review针对上述七个生产/测试/工作流路径，作者一次C/I处置并等待最终GitHub实际门后合并；cost: 此review不重新认证91315a0所有旧改动，本机focused114项/29skip/31.615s不是Windows门，后续修复与最终commit应有新身份记录。

## 当前门

- [x] 完整实际失败日志、根因和portable RED/GREEN。
- [x] 唯一完成的fresh review及全部considered/Declined作者处置。
- [ ] 最终两版源码与安装smoke、实际Windows runtime/installer及桌面、Linux两版CI。
- [ ] 全部门通过后合并PR5并核对远端main。

## 唯一完成审查与作者一次处置

唯一fresh reviewer `/root/pr5_windows_ci_review` 完成精确七路径审查：0C/1I/1M。原文 `2026-10-24-pr5-windows-ci-review.md` SHA256 `99437724d2e354dc5bc1dfa4d2d16616b07b90cc741496d2acf5acbf6a69ed67`，冻结diff SHA256 `795f826b68c32c5794e97242a9078a293a28c2505264bbde1a54150c08559c83`。作者完整阅读并执行唯一处置pass，不做第二次review。

I1已修：Windows启动cwd先通过既有_path得到安全绝对路径，再按UTF-16-LE/surrogatepass单元计数决定扩展前缀；同时覆盖含emoji和相对路径扩展后变长，不再按Python字符数。三个组件各50个emoji只有163个Python字符，却有313个UTF-16单元，旧实现漏判。新增portable实际OwnedProcess分派回归，不启动伪Win32进程；真实native用ASCII和emoji两种目录运行子进程并检查文件，等待CI认证。

M1也在同一作者pass中有界修复：四个Git身份环境变量按key存在检查，显式空值不再视为未设置；有四个真实helper拒绝回归。两个新方法在补修前6个子场景FAIL/无fixture ERROR，修复后8方法0.056s通过（3个实际native方法在mac跳过）。没有声称这些skip为Windows通过。短cwd也先进行既有安全路径校验；不支持的非规范Windows盘符相对/设备路径仍拒绝，合法相对cwd按绝对宽字符长度判断。

### 全部初始与考虑项裁决

- Ruling R1：采纳原路径兼容及anchor拒绝；cost：不迁移旧清单，不解释POSIX字面反斜杠，规范别名仍拒绝。
- Ruling R2：采纳现有强pin作为namespace/目录身份保护；cost：验证期间合法移动/重解析会被阻止，不宣称冻结一切ACL/可恢复元数据属性历史，文件内容和mode仍独立核验。
- Ruling R3：采纳缺Git且明确无仓库才空身份，M1额外关闭；cost：显式空Git身份变量也须unset，访问失败和仓库标记仍拒绝，不承诺阻止任意外部writer交错。
- Ruling R4：按I1补修安全绝对UTF-16长度和真实emoji路径门；cost：真实Git/PowerShell/Win32行为必须实际CI，模拟仅证明分派，短路径也沿用严格安全校验。
- Ruling R5：采纳原RED、114相关GREEN和新增两方法RED/GREEN；cost：native仅在Windows算已执行，保留原始失败日志，不用本机绿色代替。
- Ruling R6：采纳整套job20到35分钟，120秒单项watchdog保持；cost：可能更久失败，不扩展产品业务超时或减少测试。
- Ruling R7：唯一完成review加作者一次I/M处置；cost：最终新包/commit另记并重跑门，不把review原候选结论当作者补修后的独立复核。

### 全部五项Declined裁决

- Ruling D1：不重复审查七路径外PR5全部历史；cost：保留旧review与完整回归，若发现真实邻近缺陷仍须处置。
- Ruling D2：实际Windows是否通过留给最终installer/runtime311/runtime313/desktop及Linux门；cost：全部完成前不合并，不用取消或mac GREEN推断。
- Ruling D3：强pin的承诺限于namespace/identity以及现有mode/content检查，不宣称任意ACL/元数据ABA历史观察；cost：需要更强ACL历史契约时另做真实Windows观察器，不把当前共享规则当成万能冻结。
- Ruling D4：最终Windows日志必须检查旧branches.sqlite WinError32是否重现，不能只看job状态；cost：重现就继续诊断资源归属，当前不能仅因别的失败修好便宣称此警告已修。
- Ruling D5：保留两次Git状态与完整输入清单的离散观察，不扩成锁住不合作writer；cost：不保证每一个瞬时外部写入都被阻止，未知保持fail closed。

## 中间候选与额外核查

145c1a候选在作者I/M补修前实装各813项/8skip，234.744s与239.081s通过；源码311的2220项/52skip/388.480s通过，但313同2220项有1FAIL（53skip，394.608s）：MCP取消回归退出后process_exists仍True。此轮不是最终通过。未因该断言去改产品或放宽测试；原日志 `/tmp/ignovate-pr5-windows-final-full313.log` 保留。独立原生诊断在相同313环境、并发最终门期间连续执行同方法30次，11.201s全过，process_exists观测无True；`/tmp/ignovate-pr5-mcp-exit-probe.py`及log。原失败未取得进程状态，不能事后确定为zombie、PID复用或真正仍执行，当前不伪称根因已定位。最终新代码完整门另行收齐，并在后续长期审查中保留此未定间歇现象。

实际PATH为空的非仓库input_fingerprint另有原生probe：修改source后hash变化、增加.git后拒绝，`/tmp/ignovate-pr5-windows-missing-git-native.log`。这是mac上真实缺Git行为，仍不是Windows安装器门。

## 作者最终本地门（跨至2026-10-25）

I1/M1修正后的最终wheel SHA256 `302156ef206f211505f60c51008439641feccc632ff55e2e2d21e94fbbceca88`。159Python及39GUI资源均与工作树逐字节相同，两个实际安装各48关键模块来自安装目录；313环境77 distributions/5616 files RECORD/hash无问题。

| 最终实际范围 | 项数 | skip | 耗时 |
|---|---:|---:|---:|
| macOS Python3.11源码 | 2222 | 52 | 388.067s |
| macOS Python3.13源码 | 2222 | 53 | 392.827s |
| 工作区外实装3.11 | 815 | 8 | 233.484s |
| 工作区外实装3.13 | 815 | 8 | 236.638s |

四个执行句柄41483/66210/22616/80101均读取terminal exit0。19项JS、desktop脚本检查、最终包CLI/help/JSON doctor/dependency smoke通过。原145c1a的313完整失败未删未称通过；最终本机全量不再重现那项MCP断言，也不因此宣称该间歇现象根因已定位或已修。日志 `/tmp/ignovate-pr5-windows-accepted-*`，哈希索引 `2026-10-24-pr5-windows-local-validation.json`。

作者处置后增加原生verification先行门：同一现有NativeTestResult/async诊断器和120秒watchdog先跑三个相关模块，再执行原来完整native套件。YAML及折叠Python已解析/compile核对；cost：有限重复测试成本，失败更早暴露，但未省略任何最终全量。没有额外独立review，该工作流观察调整属于作者同一处置pass，最终运行行为仍待CI。

本地提交门已通过，实际Windows installer/runtime311/runtime313/desktop及Linux两版CI仍须观察最终commit；旧Windows关闭期branches.sqlite警告须同时检查最终日志。此记录写入时未合并，最终动作/提交身份以PR5和merge commit为准。原整项目goal继续active，不把当前修复和合并等同全部长期边界已认证。
