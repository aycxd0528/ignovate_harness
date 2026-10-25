# 长任务压缩、重启与完整任务投影续作

## 目标与已复现事实

继续原用户的整个项目长任务审查，保持整体goal active。第十四轮最终1999项Python（45平台跳过）、JS17、实际安装308项/149发行Python文件通过。保持既有工作树和用户IDE/CSS，不提交、推送或真实模型API；继续native实施及一次独立最终复核。

隔离临时项目中的真实AgentService/Graph/SQLite组合探针执行7轮读写，L2压缩由37901降至2870估计tokens；原始要求尾部可从完整归档恢复。关闭并重开Factory后，goal ID、round=7、spent=0.000588、Task ID与约束均保留。首次失败构建生成已知部分产物后，下一次修复请求被Task投影拦住：40396字符超过12000。当前已知模型窗口足够，但原要求在压缩后不再是可见用户消息，投影重新完整加入有效原文，固定上限阻断了原任务。证据 `/tmp/ignovate-long-composite-probe.log`。

## 方案

不删除或分页省去有效要求；read_task_context提供恢复能力不能替代当前有效约束。也不无条件扩大所有未知模型的投影上限。

默认12000字符改为请求投影目标：只在已知配置模型输入窗口时，可重试完整渲染，绝对投影上限1000000字符。完整TaskStore自身仍受900000字节上限保护。继续完整请求的原生估算、工具schema、system/记忆、输出reserve、安全余量及累计费用/token预算；超出实际窗口时在provider之前拒绝，checkpoint原文保留。

ContextManagerMiddleware的task_context_max_chars默认None表示自动目标；显式正整数仍是调用者的硬投影上限。直接render_task_context默认12000仍严格，未知模型窗口也不自动扩展。Task保护字段、requirement ID/来源/替代关系、Task/Goal/费用/归档格式均不变。

投影仍只用于实际请求，不写回checkpoint，不把归档或runtime资料变成新的用户授权。stage policy和脱敏后的实际长度也纳入最终投影检查。诊断记录是否扩展、基础目标、实际长度及硬上限；任务ID/版本的CAS及完整用户历史保护保持有效。

## 验证要求

- 原生RED固定压缩/重启后40k有效要求续作；provider实际收到全部要求尾部、约束、验收，任务投影不进入checkpoint。
- 完整组合走通：16轮执行→两次L2→两次重开真实SQLite→失败构建→修复→成功验证→Goal/Task交付；所有ID/要求/已花费用保留。交付后产物变化不再报告verified。
- 未知窗口、显式硬上限、超过实际模型输入窗口、超过1000000字符和失败归档均在调用前拒绝，保留原始状态。短投影不额外膨胀。
- 自动L2、request-only历史压缩与累计token预算路径都必须重新核对完整投影；不能靠较短临时估计绕过预算。
- 原生重复压缩/重启及入口回归、完整Python/JS、实际外部wheel安装来源核对；独立复核Important补native RED/GREEN。

## 保留范围

此阶段不把当前旧外部打包链接改成忽略项，不清理完整有效历史，也不宣称已有Linux/Windows原生或真实模型长期soak。整体复杂交付目标继续，不能按局部通过标记complete。
