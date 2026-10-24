# Windows长cwd的实际启动边界

PR5待用户授权合并，当前head5070901。原安装器84项/8skip已在实际Windows通过；新native先行门58项中两版均1FAIL/3ERROR，日志 `/tmp/ignovate-pr5-windows-preflight507-{311,313}.log`。实际GetProcess错误仍为WinError267，extended前缀并不能让CreateProcess接受长cwd。深Unicode项目的Git -C也仍失败。其他产物验证恢复；唯一FAIL是原测试期待invalidated，而native pin现在已阻止rename，实际为failed。

Microsoft明确说明长current directory会使CreateProcessW失败：[SetCurrentDirectory](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setcurrentdirectory)。本轮改用现有文件系统短名，不新建映射盘/链接，不改用户目录或全局cwd；[GetShortPathNameW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getshortpathnamew)也明确无短名时可能原样返回长路径，不能把API成功视为已经可启动。

## 初始裁决与代价

R1. Ruling: 长cwd依旧以安全绝对UTF-16长度判断，改为GetShortPathNameW现有短名；原长目录和短名同时native pin并比较volume/file index；cost: 无短名或短名仍过长时明确拒绝，不更名/映射盘/制造junction，也不谎称所有卷都支持长cwd。

R2. Ruling: Git长项目若明确无仓库则空Git身份；有仓库继续从同一目录短名运行真实Git，保留core.longpaths与所有HEAD/index检查；cost: 不把未知仓库当空，真实长仓库行为仍须Windows门。

R3. Ruling: Windows父目录重命名测试改为验证失败命令、非零退出和原source/目录保持、所有后续输出未生成；cost: 反映native pin在伪输入出现前阻止动作，不能将failed改成passed，也不把合法输出创建当目录损坏。

R4. Ruling: 保留原长ASCII/emoji真实进程回归、增加短名helper边界/身份的portable AST测试；cost: AST测试不证明Win32真实调用，必须收齐实际native门。短名不存在的系统限制保持显式，不以跳过native测试刷绿。

R5. Ruling: 新Win32 helper相对5070901增量一次fresh只读review与作者一次处置，最终全量/实装/CI全部门后合并；cost: 不重新审查上轮全部历史，真实失败证据不删除，无第二次review冒充。

当前仍未合并。实际平台门的最终结果和merge commit由PR5历史记录；整体长任务goal继续active。上轮MCP间歇退出断言仍按原记录保留未定，不因本轮Windows修复宣称已定位。

## 唯一完成review与作者处置

用户已明确授权合并。唯一fresh reviewer `/root/windows_short_cwd_review` 对5070901后的冻结五路径diff `da243a2302959b32b70c85753866c1b355b087dbfae1895a47d2f5f150fc8077` 完成0C/1I/0M审查。原文 `2026-10-25-windows-short-cwd-review.md` SHA256 `785f7bf0c35134770dcce991e34374fe6c90e07180d9116c5be09a20e34df65e`。

I1 Ruling: 采纳并在唯一作者处置pass补修GIT_OBJECT_DIRECTORY的key存在判断（含空值）；补充没有本地objects的HEAD/refs结构回归，原空值拒绝回归同时覆盖新变量。修前3个子场景FAIL，修后10项/3native skip通过。Cost: 显式外置对象配置须真实Git检查或明确拒绝，不能用空Git身份绕过。

全部初始裁决：R1采纳现有短名及同目录身份比较，cost为无足够短名的卷明确拒绝；R2按I1采纳保守仓库检测，cost为更多配置需要真实Git/可用短名；R3采纳pin阻止首次移动的failed断言，cost为不声称已观察输入改回历史；R4采纳portable与保留native门，cost为AST不证明Win32 ABI；R5采纳唯一review加一次作者处置，cost为最终实际CI仍是合并前置条件。

全部Declined裁决：D1实际Windows行为交给最终native CI，cost为等待并修复真实失败；D2不承诺无8.3短名卷普遍可启动，cost为显式拒绝和位置限制保留；D3不将短名选择的临时pin扩大成一般进程cwd竞态或旧MCP间歇问题的保证，cost为保留原限制与未定现象。

8e15109实际Windows：installer通过；两版59项native先行门均只剩1FAIL/3skip（224.535s/224.553s），长ASCII/emoji进程及父目录pin测试均通过。剩余deep Unicode构建返回failed，原断言未打印result，作者补充断言诊断以获得真实命令输出，未放宽passed要求。日志 `/tmp/ignovate-pr5-shortdir-native{311,313}.log`。不能将此候选称为全绿或合并就绪。
