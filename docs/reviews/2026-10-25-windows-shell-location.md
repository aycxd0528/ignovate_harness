# Windows PowerShell子进程cwd

用户已授权合并PR5，e91ec4d仍未就绪。实际Windows 311先行门60项219.135s、1FAIL/3skip；完整result显示PowerShell启动成功，但用户Python子进程报`Program 'python.exe' failed to run: The directory name is invalid`。直接长ASCII/emoji进程已通过，错误位于PowerShell原生子进程启动，而非GetShortPathName/OwnedProcess本身。日志 `/tmp/ignovate-pr5-e91-native311.log`。不放宽深中文构建passed要求。

## 初始裁决与代价

R1. Ruling: shell构造移到Windows cwd安全校验及短名选择之后，仅长目录路径给PowerShell前导Set-Location -LiteralPath同一已核验短名，单引号转义且ErrorAction Stop；cost: 无法设置shell位置时显式失败，不全局chdir，不改变用户目录或创建alias。

R2. Ruling: 保留短cwd及POSIX原语义、WindowsJob在编码成功后建立，原挂起启动/归属/清理仍不变；cost: 用户命令显式切换到其他过长cwd仍受宿主工具约束，不声称任意第三方工具都支持长路径。

R3. Ruling: portable测试覆盖编码/引号/前导顺序与OwnedProcess传同一短名；新增实际native深中文目录shell子进程写文件，旧双次ownership恢复保持；cost: macOS skip不算Win32通过，必须最终实际CI确认。

R4. Ruling: 本新增shell两路径相对e91ec4d一次fresh只读review、作者一次处置和最终所有全量/实装/CI门；cost: 不重复上一短名helper增量审查，不绕过CI或删失败证据，整体长任务goal继续active。

初始portable RED为shell_command不接受cwd参数；补修后12项/4native skip通过。新增OwnedProcess传参回归和相关门结果另记。实际Windows待最终提交，不声称已修复。
