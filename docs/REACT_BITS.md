# React Bits 组件接入

2026-10-15，根据收藏截图，通过 shadcn MCP 的官方 `@react-bits` 注册表获取组件及源码。本轮按用户反馈，改为实际挂载 **BranchedMenu、PromptBar、ThoughtLine 的 React 源码**，替换上一版相应的原生 DOM 仿制。其余四项保留现有适配。

## 三处修正

| 组件 | 接入行为 |
| --- | --- |
| [BranchedMenu](https://reactbits.dev/r/BranchedMenu-JS-CSS.json) | 使用官方 SVG 主干、曲线和选中路径；选中会话显示蓝色文字，未选中显示普通/灰色文字。切换会话只更新选中属性，保留 React 实例与 SVG 路径，使原有路径收回、新路径绘制的 400ms 动画持续播放，文字颜色以 200ms 过渡。保留工作区折叠、分组、搜索、右键菜单和滑动归档。原生会话行通过组件插槽嵌入，避免重复操作按钮。 |
| [PromptBar](https://reactbits.dev/r/PromptBar-JS-CSS.json) | 使用官方输入面板、独立模型选择、推理选择面板、最高推理档位粒子效果、发送/停止形变按钮和 `@` / `/` 菜单。模型和推理选项由真实配置提供并通过既有 API 保存；文件引用、任务模式、权限、草稿继续接入原后端。旧 `showReasoning` / `effort-fire` 面板已从输入栏调用链移除。运行时按钮变为停止，允许排队时 Enter 提交后续消息。 |
| [ThoughtLine](https://reactbits.dev/r/ThoughtLine-JS-CSS.json) | 思考、工具调用、等待批准和停止过程使用官方标题、计时、步骤及折叠动画。展示真实工具记录，运行中可手动折叠，完成后自动收起；完整工具结果与文件差异保留在下方历史记录中。 |

Rubber Segment 继续用于聊天/地图/轨迹切换；Warm Tooltip 用于图标提示预热；Swipe Row 用于会话归档/恢复；Status Mark 用于底部状态和原生工具记录。

## 代码与构建

- `frontend/src/components/`：三份官方 JSX/CSS，以应用所需的受控输入、回调、中文标签和原生行插槽做局部扩展。
- `frontend/src/islands.jsx`：React 挂载点、业务属性桥接、卸载清理。页面移除时释放组件、动画与计时器。
- `nailong/gui/static/app.js`：对接草稿、模型/推理、权限、发送与停止。
- `nailong/gui/static/conversation.js`：对接 ThoughtLine 状态、耗时与工具记录。
- `nailong/gui/static/rendering.js`：原生增量渲染遇到 React 挂载点时只更新属性，不修改 React 子树。
- `nailong/gui/static/react-bits-islands.js`、`.css`：已生成的生产资源，随 wheel 分发，运行时无需 Node 或 CDN。
- `components.json`：官方 MCP 注册表配置。

修改组件源码后重新构建：

```sh
npm --prefix frontend ci
npm --prefix frontend run build
```

使用 React、React DOM、Motion、Hugeicons 与 esbuild，版本由 `frontend/package-lock.json` 固定。GUI 后端、会话数据和 Python 命令接口保持兼容。草稿采用受控输入桥接，发送失败或等待发送时继续输入的内容都保留。

本地 HTTP 服务的待接入连接队列扩大至 64，避免刷新时多份样式、脚本和轮询请求同时到达而被重置；服务测试覆盖暂停接收后的 12 个并发连接。

重启源码 GUI 后生效；已有安装包需要重新构建。

## 验证

```sh
.venv/bin/python -m pytest tests -q
node --test tests/gui-*.test.mjs
npm --prefix frontend run build
npm --prefix desktop run check

# 使用安装了 Playwright 的 Node 环境。
.venv/bin/python -m http.server 8767 --bind 127.0.0.1 --directory nailong/gui/static
node tests/gui-react-bits-fidelity.spec.cjs
node tests/gui-react-bits.spec.cjs
node tests/gui-motion.spec.cjs

# 在独立临时项目上通过真实后端和离线模型测试。
IGNOVATE_GUI_TEST_PORT=8768 .venv/bin/python tests/gui_browser_fixture.py
node tests/gui-react-bits-native.spec.cjs
```

本轮 Python 全量：**1497 passed、44 skipped、774 subtests passed**；Node 单元测试：**14 passed**。浏览器覆盖蓝色选中路径、模型/推理保存、失败发送草稿、等待提交时的新草稿、分支草稿隔离、ThoughtLine 生命周期、流式 DOM/焦点/选区保持、滑动归档与取消、窄屏和减少动态效果。原有输入栏、模型新增、文件夹引用、目标模式、地图分支及配置保存生命周期检查也已更新并通过；禁用缓存连续刷新六次无资源重置。服务端测试另外验证四份 React Bits 模块与样式都能通过实际 HTTP 服务加载。

## 预览与许可

预览：[浅色](previews/gui-react-bits-light-2026-10-15.png)、[深色](previews/gui-react-bits-dark-2026-10-15.png)、[PromptBar 推理面板](previews/gui-react-bits-effort-2026-10-15.png)、[ThoughtLine 工具过程](previews/gui-react-bits-thought-2026-10-15.png)。

侧栏切换动画修正：[实际界面动图](previews/gui-branched-menu-motion-2026-10-15.gif)。回归检查验证选中切换时实例、SVG 和键盘焦点保留，同时检查路径绘制/回收的中间帧、快速反向切换及减少动态效果。

上游为 [DavidHDev/react-bits](https://github.com/DavidHDev/react-bits)，Copyright (c) 2026 David Haz，使用 [MIT + Commons Clause](https://github.com/DavidHDev/react-bits/blob/main/LICENSE.md)。完整许可保存在 `nailong/gui/static/REACT_BITS_LICENSE.md`，随应用分发。构建产物同时保留 React、Motion 等依赖的许可注释。本项目将组件作为应用界面的一部分使用，不作为独立组件包发布。
