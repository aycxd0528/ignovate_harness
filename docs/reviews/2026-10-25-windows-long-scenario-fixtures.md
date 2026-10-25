# Windows复合长任务验收夹具

用户已授权合并PR5，当前93cb259仍未合并。真实push Windows两版2228项全量均2FAIL/99skip，1094.761s/1097.072s；64项原生相关回归已通过（311为143.182s/3skip）。失败为long_goal_driver的完成请求被拒绝，以及long_task_continuation修复后的构建仍failed。日志 `/tmp/ignovate-pr5-93-native{311,313}-push.log`；未发现旧branches.sqlite/WinError32/Exception ignored关闭警告。PR事件运行仍待取齐，不能据push结果假称其成功。

两个复合场景helper仍用shlex.join，生成POSIX拼接引号的-c命令。项目其他原生Windows测试已用platform_fixtures.shell_join（Windows将Python源码UTF-8十六进制传输，POSIX仍shlex.join）。初始失败构建的exit1碰巧符合预期，真正修复后仍不能执行正确源码；核心未允许错误验证完成goal，是正确拒绝。本轮为test/CI覆盖修复，不改生产代码，不降低完成门槛。

## 初始裁决和成本

R1. Ruling: 两个helper改用现有shell_join，保留解释器/-B/-c/source正文及真实验证行为；cost: Windows命令文本变为既有native测试传输，POSIX保持原语义，最终必须真实Windows执行完整长任务。

R2. Ruling: 在原native先行门加入这两个测试模块及新夹具构造回归，完整套件仍保留；cost: 先行门增加有限重复执行时间，35分钟job/120秒单项watchdog不变，不删/skip复合场景。

R3. Ruling: 新portable行为测试在真实临时目录捕获settings写入、断言两场景调用native命令构造器且保留原argv/source，捕获后中止不启动模型/命令；cost: 此测试只证明夹具连线，真实compound driver/双压缩重启/交付必须原E2E继续证明。原构造回归修前两个子场景FAIL，非fixture ERROR；second失败断言补完整record以利后续诊断，不放宽passed要求。

R4. Ruling: 本次四路径增量一次fresh只读review和作者一次处置后，最终源码/实装与当前head所有CI门再合并；cost: 不重审前面已处置的生产代码，没有绿色或清理日志前不合并。产品wheel仍为e1f7753f1922e32938e7c8d9ed3b0e7ec1ae229ee240ebd76497925af0021971，生产159Python/39GUI未变。旧MCP间歇断言和长Git目录限制保持原已记录状态，整体goal继续active。

## 唯一完成review与作者一次处置

唯一fresh reviewer `/root/windows_long_fixture_review` 完整审查冻结四路径diff `1b694436a429b827674db849eddd3d79bbaf5652f861144df239d44816b9f2de`，0C/0I/0M；原文 `2026-10-25-windows-long-fixture-review.md`。作者完整阅读并采纳条件通过，无代码修改请求，不再review。

全部初始裁决：R1采纳现有native helper替换，cost为仍需真实Windows执行；R2采纳77项先行门及完整discovery并存，cost为有限重复模块运行及实际35分钟job预算核对；R3采纳构造连线RED（2个子场景FAIL）/GREEN（13项10.944s），cost为mock构造回归不等于实际引号、重启、交付认证；R4接受唯一review完成，cost为当前head所有CI及最终关闭日志仍待收齐。

全部七项考虑/Declined裁决：D1真实Windows成功不能从本地推断，cost为等待最终门；D2不另加完整body相等mock，cost为构造测试只检查连线/标记，正文未变由diff和原E2E覆盖；D3保留create=True以使旧行为在命令断言处RED，cost为真实helper import由同一先行门的原E2E独立证明；D4接受全模块重复成本，cost为实际观察Windows预算而非假定通过；D5不重开历史producer/MCP/长Git范围，cost为保持既有未定/拒绝边界；D6wheel及198文件字节依作者门，cost为review不假装认证制品；D7不由push推断PR事件，cost为最终两个事件分别核对。

本地最终源码和工作区外安装门正在运行，实际Windows待新提交。93cb259 PR313全量同为2FAIL/99skip（1169.437s），没有旧关闭警告；失败证据保留。最终实际平台结果及merge身份记录在PR5说明与merge commit，不为补记绿色再反复创建新head。
