# Ignovate 机器人头像

2026-10-17，使用 [jeremy-prt/bloub](https://github.com/jeremy-prt/bloub) 的 SVG 动画引擎，为 Ignovate 接入品牌蓝 `#315cff` 身体、白色双眼的动态机器人。

## 展示与状态

- 新会话欢迎页显示大头像，待机眨眼、视线跟随鼠标。
- 回复状态旁显示小头像，与真实运行状态同步；保留状态文字、计时及工具过程折叠。
- 思考为三圆点；工具执行循环切换轨道与六边形；等待批准显示黄色提示点；完成后静态待机；失败显示感叹号。
- 减少动态效果开启时呈现固定姿态，仍随运行状态变化；设置支持实时切换。
- 动态头像离开可视区域或窗口进入后台时暂停，移除时释放动画和监听器。

## 文件与构建

- `frontend/src/vendor/bloub/`：上游动画核心，固定在 `b4bb3c1b5f93c7b87a2e8d620f667c4093d97749`，源码未修改。
- `frontend/src/components/IgnovateAvatar.jsx`、`.css`：React 渲染、运行状态及生命周期适配。
- `frontend/src/islands.jsx`：欢迎页挂载入口，以及回复状态与 ThoughtLine 的组合。
- `nailong/gui/static/app.js`：欢迎页头像和空界面恢复时重新挂载。
- `nailong/gui/static/react-bits-islands.js`、`.css`：构建产物，随应用分发，运行时不联网、不需要 Vue。

```sh
npm --prefix frontend run build
# 使用具备 Playwright 的 Node 环境
python3 -m http.server 8768 --bind 127.0.0.1 --directory nailong/gui/static
node tests/gui-avatar.spec.cjs
```

重启源码版 GUI 后生效；已有桌面安装包需重新构建后安装。

## 预览与素材

- [可交互预览](previews/ignovate-avatar-2026-10-17.html)：在项目根目录启动 HTTP 静态服务后访问，可切换状态和浅深色。
- [SVG](previews/ignovate-avatar.svg)、[PNG](previews/ignovate-avatar.png)、[GIF](previews/ignovate-avatar.gif)。
- [浅色预览](previews/ignovate-avatar-preview-light-2026-10-17.png)、[深色预览](previews/ignovate-avatar-preview-dark-2026-10-17.png)。

上游 Copyright (c) 2026 Jérémy Perret，使用 MIT 许可证；原始许可保存在 `frontend/src/vendor/bloub/LICENSE` 及随应用打包的 `nailong/gui/static/BLOUB_LICENSE.md`，构建脚本亦保留署名。上游项目与 x.ai 无隶属关系。
