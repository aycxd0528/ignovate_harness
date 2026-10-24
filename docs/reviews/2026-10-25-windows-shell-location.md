# Windows PowerShell子进程cwd

用户已授权合并PR5，e91ec4d仍未就绪。实际Windows 311先行门60项219.135s、1FAIL/3skip；完整result显示PowerShell启动成功，但用户Python子进程报`Program 'python.exe' failed to run: The directory name is invalid`。直接长ASCII/emoji进程已通过，错误位于PowerShell原生子进程启动，而非GetShortPathName/OwnedProcess本身。日志 `/tmp/ignovate-pr5-e91-native311.log`。不放宽深中文构建passed要求。

## 初始裁决与代价

R1. Ruling: shell构造移到Windows cwd安全校验及短名选择之后，仅长目录路径给PowerShell前导Set-Location -LiteralPath同一已核验短名，单引号转义且ErrorAction Stop；cost: 无法设置shell位置时显式失败，不全局chdir，不改变用户目录或创建alias。

R2. Ruling: 保留短cwd及POSIX原语义、WindowsJob在编码成功后建立，原挂起启动/归属/清理仍不变；cost: 用户命令显式切换到其他过长cwd仍受宿主工具约束，不声称任意第三方工具都支持长路径。

R3. Ruling: portable测试覆盖编码/引号/前导顺序与OwnedProcess传同一短名；新增实际native深中文目录shell子进程写文件，旧双次ownership恢复保持；cost: macOS skip不算Win32通过，必须最终实际CI确认。

R4. Ruling: 本新增shell两路径相对e91ec4d一次fresh只读review、作者一次处置和最终所有全量/实装/CI门；cost: 不重复上一短名helper增量审查，不绕过CI或删失败证据，整体长任务goal继续active。

初始portable RED为shell_command不接受cwd参数；补修后12项/4native skip通过。新增OwnedProcess传参回归和相关门结果另记。实际Windows待最终提交，不声称已修复。

## 唯一完成review与作者一次处置

唯一fresh reviewer `/root/windows_shell_location_review` 对冻结两路径diff `b200fe5d70ab6fbbb4184c67cb0b8e6912e7f9b6473aa0411f2fe0c55417cea8` 完成0C/0I/0M审查。原文 `2026-10-25-windows-shell-location-review.md` SHA256 `ca511d5b0b5d6ca35ab6200867afd7c01b4b9f9412c605d339620baee5afb252`。作者完整阅读，一次处置接受条件通过，不做第二次review。

全部初始裁决：R1采纳字面短名和终止错误前导，cost为无法设置位置则明确失败；R2采纳原短cwd/POSIX语义和编码后创建job，cost为不保证用户另切长目录或第三方工具任意路径兼容；R3采纳quoted-alias/分派/实际native回归，cost为macOS skips不认证Windows；R4采纳一次review和最终所有门，cost为本地绿色不允许提前合并。

全部考虑/Declined裁决：D1不扩前导到普通cwd，cost为维持原shell位置行为；D2不改用Path或原始命令行插值、不在shell另选alias，cost为维护literal引号前导；D3不新建alias/改全局cwd/吞错误，cost为无有效短名仍明确拒绝；D4不提前创建job，cost为编码成功后才建立归属；D5不重复review矩阵、不把skip当native，cost为等待作者最终门；D6不重审helper/历史全部/任意第三方路径兼容，cost为报告限于shell增量并保留旧review；D7不做纯风格改写及镜像顺序测试，cost为已有短cwd/POSIX/lifecycle结论依赖现有覆盖。

作者处置另补一个先前R2尚未由真实平台覆盖的native长中文Git仓库身份回归：同一校验短名建库、真实HEAD/index读取及空提交后HEAD变化，不改产品代码。Cost: 真实Git长仓库必须最终Windows执行，不能用纯mock把该路径称为已认证；macOS只skip。此为有明确旧需求的覆盖补齐，不新增第二次review。补后14项/5native skip/0.066s通过。

冻结产品b454293相关100项/6skip/47.101s通过。最终包SHA256 `3df85e992a7106a6e37fb12b4df1d80a28cae7f79172cdf1a13f433262df5319`；159Python/39GUI逐字节匹配，77 distributions/5616 files RECORD无问题，19JS及desktop/CLI/JSON doctor smoke通过。两版源码和工作区外安装全量正在执行，真实CI仍待当前最终head。最终结果通过PR5说明和merge commit记录，不为补写平台结果再制造新head循环。

## 实际平台失败后的同一作者处置

b454真实Windows313的63项/197.222s仍2FAIL；普通Set-Location把8.3前缀展开成长ProviderPath，Python子进程继续267。6120额外Git用例的init也报`unable to get current working directory: Filename too long`。不把这些候选称为修复成功。b454产品本地源码各2227项/53和54skip、383.585s/387.672s，实装各820项/9skip、231.233s/237.380s均通过，但不能替代该Windows失败。

079148c的真实313有界对照 `/tmp/ignovate-pr5-079-native313.log`：legacy普通短名status1且打印完整展开长ProviderPath；legacy设备前缀+已校验短名status0，并成功运行同一写文件子命令；pwsh普通短名也status0，pwsh设备前缀却有Set-Location错误，不能因其退出0便称正确。由此采纳原Windows PowerShell设备前缀短名方案，不新增PowerShell7依赖、不创建目录入口。另一个8ccc目录联接对照仅作诊断，不纳入产品或最终测试路径。

Ruling R1修正：仅在已核验短名上加内部设备前缀，LiteralPath/单引号/EncodedCommand仍保留，连同前缀再按UTF-16严格短于260，否则明确拒绝。Cost: 仍须有足够短的既存alias，不支持全部卷；真实命令重新执行前不宣称最终通过。前缀portable断言修前1FAIL、修后14项/5skip通过，含ASCII/emoji长度边界。

Ruling R2边界澄清：实际Git for Windows的 [mingw_getcwd源码](https://github.com/git-for-windows/git/blob/main/compat/mingw.c) 用固定MAX_PATH缓冲区取得最终路径，再尝试GetLongPathName；已证实某些真实长仓库即使初始8.3 cwd也无法运行。这一平台能力未实现，不假装支持。产品继续真实Git读取失败就ValueError，禁止空Git身份。新增native门改为先在普通短目录真实建库/提交并取得HEAD/index，再把同一完整仓库移到长中文目录，要求返回完整原身份或明确ValueError并保持source/.git完整；这验证fail-closed契约，**不是认证长Git仓库可用**。Cost: 受影响Git仓库须使用较短项目位置；通用长Git读写支持留在持续goal，不以新增映射盘/目录联接规避本次边界。

作者删除仅失败时运行的候选对照，保持最终测试只走真实产品路径。所有旧失败/对照日志保留；这仍是同一shell作者处置pass，没有第二次历史review。最后产品包和实际CI以PR5最终head为准。
