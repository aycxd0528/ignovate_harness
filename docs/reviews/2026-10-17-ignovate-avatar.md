# Ignovate 动态头像验收

采用已确认方案：品牌蓝动态头像、新会话页展示、回复状态联动。

## 结果

- 复用 bloub 固定版本的原始 SVG 引擎；原始 MIT 许可和署名随静态资源打包。
- 欢迎页视线跟随、眨眼；运行状态驱动思考圆点、循环轨道/六边形、批准提示、完成待机与失败感叹号。
- 实时响应减少动态效果设置；离屏、后台暂停，卸载时清理。
- 已验证取消首轮运行后欢迎头像重新挂载；正常轮询保持实例、草稿与工具折叠状态。
- 独立代码审查发现上游 orbit 在 3.6 秒后结束；增加平滑循环并用超过 4 秒的浏览器场景覆盖。复审确认已解决，无其他实质发现。

## 实测

2026-10-17 执行，均通过：

- `npm --prefix frontend run build`
- `node tests/gui-avatar.spec.cjs`（先验证缺失头像失败；减少动态效果、欢迎页恢复、长时工具场景亦先失败后修复通过）
- `node tests/gui-react-bits.spec.cjs`
- `node tests/gui-react-bits-fidelity.spec.cjs`
- `node tests/gui-react-bits-native.spec.cjs`（真实离线后端）
- `node --test tests/gui-*.test.mjs`：14 passed
- `npm --prefix desktop run check`
- `.venv/bin/python -m unittest discover -s tests -v`：1583 tests，OK，44 skipped
- `git diff --check`

浏览器测试通过 bundled Playwright 和本机 Chrome 运行；静态服务使用 8768，真实后端隔离 fixture 使用 8766。

已检查可交互预览、浅深色、移动端截图，以及 SVG/PNG/GIF 导出。真实 GUI 欢迎页截图位于 `docs/previews/ignovate-avatar-gui-{light,dark}-2026-10-17.png`。

已有其他未提交的工作区改动保留；本次仅增补头像、欢迎页挂载和生成资源。现有桌面安装包尚未重建。

## 鼠标跟随方向修正

用户反馈鼠标在下方时眼睛向上。根因为屏幕 Y 坐标向下递增，而 bloub 的正 pitch 表示向上；适配器漏了负号。浏览器回归改为检查实际渲染的双眼中心在上、下、左、右四个方向的投影。修复前，向下移动鼠标得到眼睛中心 Y = -36.01；补上负号后重新验证方向与其他头像行为。

修正后构建、四向跟随及完整头像浏览器用例、React Bits fidelity、14 项 Node 单元测试和 diff 检查均通过。并行运行时头像用例曾出现首次资源加载超时，串行复跑通过；本轮未修改 Python，未重复执行此前已通过的 Python 全量测试。

## 2026-10-18 配色调整

按用户要求改为纯黑身体与品牌蓝 `#315cff` 双眼；思考圆点和变形主体同步为黑色。已重新构建，现有完整头像浏览器回归通过；更新可交互预览、浅深色/移动端预览、SVG、PNG、GIF，并目视确认黑色身体与蓝色双眼。

随后按用户要求恢复原来的品牌蓝身体、白色双眼与蓝色思考圆点，并重新构建及更新预览素材。此前鼠标跟随方向修正保留。
