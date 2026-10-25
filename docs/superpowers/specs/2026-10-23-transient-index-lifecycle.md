# 可硬退出回收的磁盘临时索引

原始用户goal：审查整个项目并修复潜在问题，使复杂长任务稳定交付。第十七轮已验收2030Python/45平台跳过、JS17、实际安装467/150；原goal active。本轮不提交/推送/真实模型API，保留现有分支与用户IDE/CSS。

## 原生事实

输入inventory和artifact membership均在TemporaryDirectory中使用命名SQLite文件；正常finally关闭和删目录，os._exit不能执行finally。原生子进程写入12000行后exit81，分别遗留inventory.sqlite33603584字节、paths.sqlite25382912字节；日志 /tmp/ignovate-index-hard-exit-probe.log。当前数据行用于索引原语复现，不据此声称实际项目扫描性能。

SQLite官方open/temporary文档支持空文件名的私有临时磁盘库；本机SQLite3.50.4、TEMP_STORE=1，原生27MB活跃临时DB fd nlink0、0600。新设计先设置temp_store=FILE再ATTACH空文件名为scratch，避免默认TEMP_STORE=2时提前创建主临时库的内存策略；主库保持空，全部索引DDL落入scratch。原生试验scratch约25MB落盘且nlink0、main_pages=0。

## 契约

- 无新依赖，用SQLite内置临时VFS生命周期，不自建持久清理日志、不按/tmp前缀删除未知用户文件，不修改任何持久Task/Goal/费用/证据/历史。
- 创建scratch前FILE策略，数据/索引表只在scratch，4096页、131072页上限（512MiB）、cache_size=-2048、temp_store=FILE、journal/synchronous OFF、既有progress/deadline检查；artifact路径64MiB上限不变。
- 构建TEMP_STORE=3强制内存时明确拒绝，不伪装磁盘或默默回退内存。支持默认内置VFS、进程环境配置在索引期间保持不变，不读写SQLite不安全的全局temp_store_directory。
- 输入清单仍拒绝Python配置临时根位于项目；另核对默认SQLite VFS选中的临时根（Unix候选顺序、Windows GetTempPathW）避免临时数据污染输入。无法核对位置拒绝；不改变全局环境或工作目录。
- 正常/异常/取消均close；原生硬退出由OS/VFS收回，不能依赖后续启动或删除旧未知文件。现有字节身份、完整扫描/目录队列、取消所有权和API错误包装保持。
- 验证物理落盘、临时文件无可恢复名称、正常及硬退出清理、双索引隔离、容量/取消/目录保护、真实VerificationService/Graph/自动goal续作。Windows原生仍需对应系统实测，不能从本机模拟推出通过。

## 来源

- https://sqlite.org/c3ref/open.html （空文件名私有临时磁盘库）
- https://sqlite.org/inmemorydb.html （temp_store影响临时库，FILE必须先设置）
- https://sqlite.org/tempfiles.html （临时目录/VFS和缓存策略）
- https://sqlite.org/c3ref/temp_directory.html （不安全全局变量不得在多线程读写）
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-gettemppathw
