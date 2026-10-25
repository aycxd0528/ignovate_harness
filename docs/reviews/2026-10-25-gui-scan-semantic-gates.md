# GUI扫描并发测试的语义门

用户授权合并PR5。5ec1a07最终11个检查成功、1个失败，未合并：push Windows313完整2230项/99skip/1495.681s，仅test_confirmation_scan_cannot_follow_a_project_switch在open_project外层0.3秒wait_for超时。其他三个native完整运行2230/99skip通过：PR311 1482.068s、PR313 1569.073s、push311 1684.704s；四个85项先行门均通过。四份native原始日志 `/tmp/ignovate-pr5-5ec-native{311,313}-{pr,push}.log` 保留，均无branches.sqlite/WinError32/Exception ignored。

实际失败附近monitored任务一次执行耗时0.318秒；堆栈中open_project已切换状态并在旧controller.close的owned join处收到外层取消，非120秒watchdog。真实GUIController构造及SQLite/文件初始化是同步原生工作，这个测试原意为“暂停输入扫描时仍可切换项目”，不是所有native IO都在300ms完成的SLA。单次native失败不能证明所有关闭/磁盘场景无问题，未修或重新认证生产构造延迟。

本机独立probe包装真实构造器增加0.4秒延迟，旧测试同样ERROR TimeoutError（1项/0.930s），不是伪fixture构造失败；同一probe在新语义门下通过（1项/0.906s），实际原项目切换、旧task拒绝及SQLite关闭仍运行。15项GUI扫描模块1.638s通过。另一个负对照在项目操作完成前释放真实扫描gate，必须触发“operation completed only after input scanning resumed”断言失败，防止放宽总等待造成扫描已自行恢复后假通过。

## 初始裁决及成本

- R1：该模块同类控制/任务锁/HTTP响应操作改为3秒有界等待，并在操作返回后断言真实扫描gate尚未finished；原项目/任务/反馈/交付状态断言保持。cost：不再认证300ms延迟，认证暂停扫描与操作完成的顺序及3秒有界等待；生产代码不变。
- R2：fixture回退等待2到10秒并记录finished，测试finally/async cleanup仍主动release和join；cost：真正把事件循环阻塞在扫描里的错误最多多等8秒，120秒单项watchdog保持；10秒回退仅防测试自身失控，不是产品超时。失败后不能把回退恢复当通过。
- R3：将整个15项GUI扫描模块加入既有native先行门，85到100项，完整2230不减。cost：每版、每个既有trigger重复15项，实际Windows耗时留给最终日志，不扩matrix或trigger、不再延长45分钟job上限。
- R4：两路径冻结diff唯一fresh只读review，作者一次处置；最终当前head全量/实装/实际native及关闭日志通过才合并。cost：旧5ec的11项绿色不能替代新head，旧失败不重跑刷绿；原长期goal、MCP未定现象和长Git拒绝边界继续保留。

冻结diff `/tmp/ignovate-pr5-gui-scan-semantic-review.diff` SHA256 `58eef6e58d7fd678679f0b4dab6bdeea6a19dba4a5e55a0c8f80e8ac381f6089`，base5ec1a07。产品wheel仍e1f7753f1922e32938e7c8d9ed3b0e7ec1ae229ee240ebd76497925af0021971，未重复构建相同产品。

最终review处置、本地门及当前head实际CI/merge身份待记录；文档写入时未合并，不宣称整项目goal完成。

## 唯一完成审查及作者一次处置

唯一fresh reviewer `/root/gui_scan_semantic_review`，0C/0I/1 advisory Minor。原文 `2026-10-25-gui-scan-semantic-review.md` SHA256 `9b40237049ca7661f9709551c408ab0cecb8e404359d4262d0aece8ebef295c0`，冻结两路径hash同上。作者完整阅读后一次处置，未做第二review。

M1采纳：HTTP后台socket timeout和release后join由4到10秒，收集线程异常并断言线程已结束，再检查结果。真实HTTP probe令扫描启动耗1.5秒、独立heartbeat消费额外2.8秒，旧测试后台TimeoutError且主线程IndexError（4.615s）；补修后同probe 4.610s通过。cost：后台socket/join各配置最多多6秒，socket为每次阻塞操作的预算而非端到端严格上限；显式release/join及原生120秒watchdog保留，未认证任意服务器/IO的绝对期限。此作者补修不在冻结diff中，由最终作者门及实际CI覆盖。

- Ruling R1：采纳语义顺序及3秒取消策略；cost：独立操作300ms至3秒间的延迟不再由此模块检测，原断言完整保留，不将wait_for或socket配置宣称绝对wall-clock保证。
- Ruling R2：采纳finished在fingerprint返回前置位和10秒fixture回退；cost：真实事件循环阻塞可能多等8秒，调度/owned cancellation join还可增加时间，取消归属不削弱。
- Ruling R3：采纳整15项进入native preflight；cost：两个trigger/两版合计多60项先行执行，实际Windows耗时待最终日志，不用1.638s本机数字推算。
- Ruling R4：采纳一次review加作者一次M1处置；cost：最终head实际平台及安装证据不可被旧head或probe替代，历史失败保留。

### 全部考虑及Declined项

- D1：不把真实构造/全部native IO移出event loop；cost：同步native延迟仍在，不认证全面GUI响应上限。
- D2：不把该测试300ms当project-open SLA；cost：本模块不再测此阈值，既有500节点/30分支map branch-switch p95 300ms要求未放弃，属于另一操作及测量。
- D3：不只改project switch而保留同模块send/stop/approve/create/任务写入/HTTP的旧.3策略；cost：整个模块对独立小于3秒延迟灵敏度降低，统一新增暂停顺序断言。
- D4：真正等待扫描锁/完成仍拒绝；cost：可能到新取消/回退预算才失败，不因回退结束而通过。
- D5：finished与断言交错保持保守；cost：重调度可导致假阴性，finished先于fingerprint返回，未发现回退造成假阳性路径。
- D6：不把wait_for(3)变硬wall-clock证明；cost：同步工作不能抢占，owned cleanup继续join，120秒CI watchdog为最终后备。
- D7：后台HTTP预算和异常诊断未忽略，M1同一作者pass补修；cost及RED/GREEN如上。
- D8：不重写其余startup、teardown及任意pending失败处理；cost：原fixture诊断/调度边界保留，非万能teardown证明。
- D9：不加平台/version/trigger、watchdog或job时长；cost：100项先行门消耗现有45分钟余量，最终实际CI须证明可完成。
- D10：不重建相同wheel；cost：实际安装和159字节/49origin再次核验，reviewer没有独立构建或执行认证。
- D11：whole-PR、长goal完成、旧MCP未定及长Git拒绝边界不在本次review；cost：长期goal继续active，未宣称限制已修。
- D12：不拿旧head绿色或本地probe当新Windows认证；cost：实际新head全量与关闭日志继续等待，失败不重跑刷绿。

最终本地完整门在M1之前启动，不能当M1后全量执行。作者M1后完整15模块四环境重跑：源码311/313为1.834s/1.825s，实装311/313为1.819s/1.859s，全部terminal exit0，产品159Python字节和49安装origin核对。完整候选实装两版各830项/10skip，236.326s/241.328s，句柄18518/26175 terminal0。新head完整源码及native实际CI仍须通过。

完整候选源码两版各2230项，54/55skip、388.082s/392.353s，句柄89855/5517均terminal0。上述完整源码/实装与M1后15模块的执行范围分开记录，不重跑未改生产包。最终当前head实际GitHub门及merge身份以PR5和merge commit记录；未获得所有门及关闭日志前不合并。
