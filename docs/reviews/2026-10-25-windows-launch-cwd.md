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
