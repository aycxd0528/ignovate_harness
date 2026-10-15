Ignovate v1.2.1 更新 macOS 桌面工作台。下载 `Ignovate-1.2.1-macos-arm64.dmg`，打开后将 `Ignovate.app` 拖入 Applications，替换旧版应用。包内包含 Python 3.11、固定版本依赖和 ripgrep，无需另装 Python 或克隆源码。已有项目配置、会话和工作区数据继续保留。

本次更新：

- 修复取消任务时后台 checkpoint 写入与数据库关闭的竞态，确保退出后释放数据库文件。

- 接入 React Bits 输入栏、模型与推理选择、思考过程和会话分支切换动画。
- 新增执行轨迹页，按轮次查看输入、模型和工具事件；紧凑概览支持搜索、点击定位及横向滚动。
- 修复会话地图在追问运行期间无法拖动卡片的问题，保持卡片位置与连线实时更新。
- 运行期间保留实时工具状态，任务结束后折叠记录，并保留输出与文件变更。
- 修复桌面版复制回答、用户输入和代码块时被网页剪贴板权限拒绝的问题，成功后提示“已复制”。
- 更新任务关联、等待条件、验收记录及反馈经验管理。

此安装包适用于 macOS 13 及以上的 Apple Silicon Mac，采用 ad hoc 本地签名，尚未经过 Developer ID 签名与 Apple 公证，首次打开方式见[安装文档](https://github.com/aycxd0528/ignovate_harness/blob/v1.2.1/docs/INSTALL.md)。Intel Mac 可继续使用 CLI 安装包。Release 同时提供 Unix、Windows CLI 安装包、wheel 及 SHA-256 校验文件。
