# POSIX 直接子进程回收

日期：2026-10-25。基准：已合并 PR5 的 `bd8fa20b208482a340c4254f73ada27c753307ca`。本轮在已有隔离 worktree 的 `codex/long-task-post-merge-audit` 继续整项目长任务可靠性审查。

## 已确认的 P1

旧 `_posix_process_child.py` 在关闭 lifeline 后先发 `stopping`，随后对包含自身的进程组发 SIGKILL；没有机会等待并回收实际直接子进程。原生探针在真实确认写入边界调用 `waitpid(WNOHANG)`，观察到子进程仍 `running`，不是已回收。不能把关闭后的直接子进程回收交给不受本项目控制的 init；本轮不声称已经证明永久 zombie 泄漏或 P0。

最小修复：保存未创建子进程的 `None` 状态；在最终确认前执行实际子进程的 `kill()` / `wait(timeout=4)`。任何失败跳过确认，父控制器继续报告“清理无法核实”；最外层 `finally` 始终尝试原有进程组 SIGKILL。组长保持存活直到最后信号，仍不向已回收的历史 PGID 发信号。自然退出的有符号返回值、stdio、启动错误及 STOP 规则保持原契约。

## RED / GREEN 与未通过候选

- 新确认边界回归在旧代码得到 `{'state':'running'}`，期望 `already-reaped`，明确 FAIL；修复后通过。
- 新等待故障回归在旧代码得到 `RuntimeError not raised`，明确 FAIL；修复后拒绝确认，仍阻止真实子进程和孙进程的延迟写入。
- 两项最小 GREEN：2 项 / 0.086 秒。后续扩大等待故障回归以检查剩余进程组清理，完整相关门另记。
- 首个聚焦命令遗漏 `PYTHONPATH=tests`，58 项 / 27.757 秒有 5 个 `platform_fixtures` 导入错误、2 skip；这是调用错误，不是产品修复证据。原日志 `/tmp/ignovate-reap-focused311.log` 保留。
- 修正调用后 94 项 / 60.660 秒 / 4 skip，只有既有 SDK 内部取消后的 `process_exists` 断言失败，不能记为全过。日志 `/tmp/ignovate-reap-focused311-fixed-invocation.log`。
- 同候选源码两版各 2232 项，3.11 为 390.733 秒 / 54 skip，3.13 为 395.346 秒 / 55 skip，各有同一个 MCP 断言 FAIL；工作区外实装两版各 166 项 / 4 skip，80.004 / 80.460 秒，也各有同一 FAIL。源码门启动后开始了后续 MCP 生命周期修复，不能把这些执行作为后续最终冻结树验证。四个执行句柄均读取 terminal exit1。
- 此候选 wheel SHA256 `c48b3f6411750b6a38ae55ea171ad45127a9577d4e32bbfcc402e89ef2227b96`，CLI/help/JSON doctor smoke 通过，不能以 smoke 覆盖上述真实失败。

原历史 MCP 失败没有捕获 PID 状态。本轮失败诊断在存活检查 True 后立即执行 ps，进程已消失，连接状态为 error/清理无法核实；不能据此断言先前是运行、zombie 或 PID 复用。后续独立的通道生命周期缺口及原生负对照见 `2026-10-25-mcp-guardian-lifetime.md`。直接回收修复本身没有建立历史间歇失败已解决的结论。

## 唯一独立审查及作者一次处置

审查原文：`2026-10-25-posix-direct-child-reaping-review.md`，SHA256 `7d2d91018326c42c4cd62735303cc932acc4b8a07a09930fe3f8f15a737f77d5`。冻结两路径 diff SHA256 `a099bb70bb1e4f02c9305874a7f59e23fcd2f805a056f2ceb13f83477fc17742`。唯一 fresh reviewer 得到 0 Critical / 0 Important / 0 Minor；没有运行重复测试或派生其他 reviewer。

作者完整阅读报告并作一次裁决：

- R1：采纳直接回收先于确认、失败省略确认、最终组信号无条件执行的代码判断；无产品补修。
- R2：采纳启动错误、已回收 Popen 不再次信号历史 PID、有符号退出码和 stdio 不变的分析；cost：仍保留原生相关完整回归，源码分析不代替平台验证。
- R3：采纳 STOP 与硬退出沿同一 finalizer 的分析；cost：不外推到监护器自身被外部 SIGKILL 后仍能回收。
- R4：采纳探针验证真实 waitpid 状态、等待异常测试仍清理余下组的覆盖；cost：注入的 TimeoutExpired 立即发生，未认证真实内核停滞 4 秒后的全部行为。
- R5：采纳整个聚焦门仍失败、不能发布为全绿的判断；继续诊断独立 MCP 生命周期缺口，最终组合修复另跑完整门。
- R6：审查没有必修项，不做第二轮同范围审查；cost：后续不同范围通道修复拥有独立冻结边界，不能以本报告充当其审查。

全部 Considered / Declined 项裁决及代价：

- D1：不先杀包含监护器的全组再等待；否则监护器没有机会回收，需要更复杂的新存活进程层。
- D2：不无期限 wait；4 秒是既有父等待 5 秒内的有界尝试，不是任意调度下的硬实时保证。
- D3：不吞掉 kill/wait 错误后确认；未知保留，可能需要用户核对副作用。
- D4：不由父进程向已回收组长的历史 PGID 补信号；避免 ID 复用风险。
- D5：不增加 subreaper/double-fork/新回收服务；cost：不保证回收所有被收养孙进程或逃离组的后代。
- D6：不将 waitpid 原生证据替换为存在性轮询；原严格存活断言继续保留。
- D7：不重复同一审查或重复其测试套件；作者实际完整门和不同根因诊断继续进行。
- D8：原 MCP 失败根因不由此报告裁定；已记录实际失败并另做原生边界证明，不能删除失败记录。
- D9：组逃逸、全部后代回收、监护器外部 SIGKILL 不在本轮认证内；未来要强化需独立架构设计。
- D10：报告不裁定全项目 release/merge ready；最终代码、实装、CI 和集成决定另记。

本轮最终组合验证、提交及平台证据见 `2026-10-25-mcp-guardian-lifetime.md`；整个项目 goal 继续 active。
