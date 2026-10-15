# React Bits 原组件接入修正

目标：按用户三点反馈，实际使用 BranchedMenu、PromptBar、ThoughtLine，选中侧栏文字蓝色，替换旧推理等级面板，模型思考/工具过程统一展示。

执行方式：当前会话内完成。保持 Python API，使用 React 小挂载点；官方 JSX/CSS 保存在 frontend/src/components，esbuild 生成随 Python 包分发的静态 JS/CSS，无运行时 CDN。

- [x] 验证原实现：旧 reasoning.js 仍在调用；选中项被覆盖为普通色；回归测试明确复现。
- [x] 接入官方组件及小挂载点生命周期、原生 DOM 增量更新边界。
- [x] PromptBar 对接真实模型/推理选项、草稿/引用/模式/权限/发送/停止；保存失败保留草稿，配置失败回退。
- [x] BranchedMenu 使用官方 SVG 主干/分支/选中路径，保留原生行操作；选中蓝字，未选中普通/灰字。
- [x] ThoughtLine 展示真实思考状态、耗时及工具步骤，流式更新保留 DOM，完成自动折叠，完整历史可查。
- [x] 构建、浏览器实际交互、原有流式稳定性、Python 服务/打包检查，更新预览及说明。

主要风险：程序写入草稿与 React 受控输入同步；模型切换后推理档位更新；原生 patch 不修改 React 子树；移除页面时卸载组件，释放计时器与动画。
