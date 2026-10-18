#!/usr/bin/env python3
"""准备飞书《Ignovate Harness 项目说明》的 v1.1.0 更新命令。

设计约束（来自 lark-doc skill）：
  * 不使用 overwrite —— 它可能丢失评论与暂不支持的资源；
  * 散落文本用 str_replace（服务端全文查找替换）；
  * 代码块与结构性新增用 block_replace / block_insert_after，block ID 已在
    2026-10-13 通过 `docs +fetch --scope section` 实测取得，写入前会重新校验。

默认只打印将要执行的命令并做链接预检；加 --run 才真正调用 lark-cli。

用法：
    python scripts/update_feishu_project_doc.py            # 预览命令 + 链接预检
    python scripts/update_feishu_project_doc.py --run      # 依次执行
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

DOC = 'https://qcnvafay57br.feishu.cn/wiki/Sj4NwUflgiALgnkrRCMc6FxRn8b'
OLD, NEW = '1.0.3', '1.1.0'

# 快速开始段落中实测到的 block ID（写入前会重新解析校验）
SECTION_FASTSTART = 'doxcncC1675k7GU5OljBZxA4T6e'   # 「8. 快速开始」标题
BLOCK_PRE_UNIX = 'doxcnRzx1kyMrdfZMaJ0coFzFgd'
BLOCK_PRE_WIN = 'doxcnZSNWZeYrUfkmy78tm2U0Oh'
BLOCK_INTRO = 'doxcnpfzj6Z09sZc4KFh8P74Gvc'

# ---------------------------------------------------------------- 全文替换
REPLACEMENTS: list[tuple[str, str]] = [
    # 定位段：版本 + 桌面工作台
    (
        '适用版本为 1.0.3，维护日期为 2026-10-10。支持 macOS、Linux 和 Windows 11 x64 原生运行；'
        'Release 安装器会自动准备缺少的 Python 和应用依赖。',
        '适用版本为 1.1.0，维护日期为 2026-10-10。支持 macOS、Linux 和 Windows 11 x64 原生运行，'
        '并提供 Apple Silicon macOS 桌面工作台；Release 安装器会自动准备缺少的 Python 和应用依赖。',
    ),
    # 发布资产链接
    ('releases/download/v1.0.3/ignovate-1.0.3-unix.tar.gz',
     'releases/download/v1.1.0/ignovate-1.1.0-unix.tar.gz'),
    ('releases/download/v1.0.3/ignovate-1.0.3-windows.zip',
     'releases/download/v1.1.0/ignovate-1.1.0-windows.zip'),
    ('releases/tag/v1.0.3', 'releases/tag/v1.1.0'),
    ('blob/v1.0.3/docs/INSTALL.md', 'blob/v1.1.0/docs/INSTALL.md'),
    ('blob/v1.0.3/.github/workflows/release.yml',
     'blob/v1.1.0/.github/workflows/release.yml'),
    # 其余文件名、命令与链接文字
    ('ignovate_harness-1.0.3-py3-none-any.whl', 'ignovate_harness-1.1.0-py3-none-any.whl'),
    ('ignovate-1.0.3-unix.tar.gz', 'ignovate-1.1.0-unix.tar.gz'),
    ('ignovate-1.0.3-windows.zip', 'ignovate-1.1.0-windows.zip'),
    ('git clone --branch v1.0.3', 'git clone --branch v1.1.0'),
    ('[v1.0.3](', '[v1.1.0]('),
    # Windows 升级路径（保留段落结构，仅推进版本）
    ('下载 v1.0.3 安装包并重新运行 install.ps1', '下载 v1.1.0 安装包并重新运行 install.ps1'),
    # 发布验证：两个安装包 -> 三个渠道
    ('全部检查通过后才发布 Unix 安装包、Windows 安装包、wheel 和 **SHA256SUMS**。',
     '全部检查通过后才发布 macOS 桌面安装包、Unix 安装包、Windows 安装包、wheel 和 **SHA256SUMS**。'),
]

# ------------------------------------------------- 结构化替换（block 级）
UNIX_CODE_XML = (
    '<pre lang="bash" caption="&#xA;"><code>tar -xzf ignovate-1.1.0-unix.tar.gz<br/>'
    'cd ignovate-1.1.0<br/>sh install.sh<br/>. "$HOME/.local/bin/ignovate-env.sh"<br/>'
    'ignovate set up</code></pre>'
)
WIN_CODE_XML = (
    '<pre lang="powershell" caption="&#xA;"><code>Expand-Archive .\\ignovate-1.1.0-windows.zip '
    '-DestinationPath .\\ignovate-release<br/>cd .\\ignovate-release\\ignovate-1.1.0<br/>'
    'powershell -NoProfile -ExecutionPolicy Bypass -File .\\install.ps1</code></pre>'
)
BLOCK_EDITS: list[tuple[str, str, str]] = [
    (BLOCK_PRE_UNIX, UNIX_CODE_XML, 'macOS/Linux 代码块'),
    (BLOCK_PRE_WIN, WIN_CODE_XML, 'Windows 代码块'),
]

# 在「快速开始」引言段之后插入 macOS 桌面版小节
MACOS_SECTION_XML = (
    '<h2>macOS 桌面版（Apple Silicon）</h2>'
    '<p>下载 <a href="https://github.com/aycxd0528/ignovate_harness/releases/download/'
    'v1.1.0/Ignovate-1.1.0-macos-arm64.dmg">Ignovate-1.1.0-macos-arm64.dmg</a>，'
    '将应用拖入 Applications 后打开；首次启动选择工作区，在模型设置中配置提供商。'
    '包内包含 Python 3.11、固定版本的应用依赖和 ripgrep，无需克隆源码或安装 Python。</p>'
    '<p>桌面工作台支持流式聊天、会话地图与追问分支、实际文件差异、Skills 与 MCP 插件管理、'
    '模型与推理强度选择、权限模式及通用设置。该安装包仅支持 Apple Silicon，使用 ad hoc 本地签名，'
    '尚未经过 Developer ID 签名与 Apple 公证，macOS 可能阻止首次打开；Intel Mac 请使用下文的 '
    'macOS / Linux 安装包。</p>'
)


def lark(*argv: str) -> dict:
    out = subprocess.run(['lark-cli', *argv], capture_output=True, text=True)
    if out.returncode != 0 and not out.stdout.strip():
        sys.exit(f'lark-cli failed: {out.stderr.strip()}')
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        sys.exit(f'unexpected output: {out.stdout[:400]}{out.stderr[:400]}')


def fetch_markdown() -> tuple[str, int]:
    payload = lark('docs', '+fetch', '--doc', DOC, '--as', 'user',
                   '--doc-format', 'markdown')
    document = payload['data']['document']
    return document['content'], document['revision_id']


def verify_blocks() -> set[str]:
    """确认结构化编辑所用的 block ID 仍然存在。"""
    payload = lark('docs', '+fetch', '--doc', DOC, '--as', 'user',
                   '--scope', 'section', '--start-block-id', SECTION_FASTSTART,
                   '--detail', 'with-ids', '--doc-format', 'xml')
    content = payload['data']['document']['content']
    return set(re.findall(r'id="(doxcn[A-Za-z0-9]+)"', content))


def preflight(content: str) -> None:
    urls = sorted(set(re.findall(
        r'https://github\.com/aycxd0528/ignovate_harness/(?:releases|blob)/[^)\s]+', content)))
    print('\n链接预检：')
    for url in urls:
        try:
            request = urllib.request.Request(url, method='HEAD',
                                             headers={'User-Agent': 'preflight'})
            code = urllib.request.urlopen(request, timeout=12).status
        except urllib.error.HTTPError as error:
            code = error.code
        except Exception as error:  # noqa: BLE001
            code = f'ERR {error}'
        print(f'  {"✓" if code == 200 else "✗"} {code}  {url}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true', help='真正执行 lark-cli 写操作')
    parser.add_argument('--skip-preflight', action='store_true')
    args = parser.parse_args()

    content, revision = fetch_markdown()
    print(f'飞书当前 revision_id={revision}，{len(content)} 字符，'
          f'含 "{OLD}" {content.count(OLD)} 处')

    # ---- 全文替换命令（先执行：str_replace 不影响 block ID）
    text_commands: list[list[str]] = []
    hit = 0
    for old, new in REPLACEMENTS:
        if content.count(old):
            hit += 1
            text_commands.append(['docs', '+update', '--doc', DOC, '--as', 'user',
                                  '--command', 'str_replace',
                                  '--pattern', old, '--content', new])
    print(f'全文替换：命中 {hit}/{len(REPLACEMENTS)} 条')

    # ---- 代码块替换（后执行：会刷新同节 block ID）。
    # str_replace 对 <pre> 的覆盖无法在写入前验证，因此显式按 block 替换。
    live_ids = verify_blocks()
    print(f'\n结构化编辑（重新解析到 {len(live_ids)} 个 block ID）：')
    block_commands: list[list[str]] = []
    for block_id, xml, label in BLOCK_EDITS:
        if block_id in live_ids:
            block_commands.append(['docs', '+update', '--doc', DOC, '--as', 'user',
                                   '--command', 'block_replace',
                                   '--block-id', block_id, '--content', xml])
            print(f'  ✓ 待替换 {label}  {block_id}')
        else:
            print(f'  ⚠ 未找到 {label}（{block_id}），已跳过——请重新运行以刷新 ID')

    # ---- 新增 macOS 桌面版小节（最后执行）
    if 'macos-arm64.dmg' in content:
        print('  · macOS 桌面版小节已存在，跳过插入')
    elif BLOCK_INTRO in live_ids:
        block_commands.append(['docs', '+update', '--doc', DOC, '--as', 'user',
                               '--command', 'block_insert_after',
                               '--block-id', BLOCK_INTRO, '--content', MACOS_SECTION_XML])
        print(f'  ✓ 待插入 macOS 桌面版小节（在 {BLOCK_INTRO} 之后）')
    else:
        print(f'  ⚠ 未找到引言段 {BLOCK_INTRO}，无法插入 macOS 小节')

    commands = text_commands + block_commands

    if not args.skip_preflight:
        preview = content
        for old, new in REPLACEMENTS:
            preview = preview.replace(old, new)
        preflight(preview)

    print(f'\n将执行 {len(commands)} 条写操作：')
    for command in commands:
        marker = command[command.index('--command') + 1]
        key = '--pattern' if '--pattern' in command else '--block-id'
        print(f'  [{marker:19}] {command[command.index(key) + 1][:58]}')

    if not args.run:
        print(f'\n[dry-run] 未写入。发布完成后执行：python {sys.argv[0]} --run')
        return 0

    failures = 0
    for index, command in enumerate(commands, 1):
        payload = lark(*command)
        ok = payload.get('ok')
        data = payload.get('data', {})
        print(f'{index}/{len(commands)} ok={ok} result={data.get("result")} '
              f'warnings={data.get("warnings") or []}')
        if not ok:
            failures += 1
            print('   error:', json.dumps(payload.get('error'), ensure_ascii=False))
    print(f'\n完成：{len(commands) - failures} 成功 / {failures} 失败')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
