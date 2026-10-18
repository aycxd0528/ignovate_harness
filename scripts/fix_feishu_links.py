#!/usr/bin/env python3
"""补齐 str_replace 无法覆盖超链接的遗漏：用 block_replace 定点修正。

背景：飞书 docs 的 str_replace 能改纯文本，但改不动 <a> 的 href 与链接文字，
导致首轮更新后 5 个段落仍指向 v1.0.3。本脚本按段落整块替换。

用法：
    python scripts/fix_feishu_links.py           # 预览
    python scripts/fix_feishu_links.py --run     # 写入
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

DOC = 'https://qcnvafay57br.feishu.cn/wiki/Sj4NwUflgiALgnkrRCMc6FxRn8b'
REL = 'https://github.com/aycxd0528/ignovate_harness'

# (block_id, 新 XML, 说明)
EDITS: list[tuple[str, str, str]] = [
    (
        'doxcnpfzj6Z09sZc4KFh8P74Gvc',
        f'<p>推荐从 <a href="{REL}/releases/latest">GitHub Releases</a> 下载安装包，'
        f'无需预装 Python 或克隆源码。当前版本为 '
        f'<a href="{REL}/releases/tag/v1.1.0">v1.1.0</a>。</p>',
        '快速开始引言（版本链接）',
    ),
    (
        'doxcnZff2bd8SbHUstpz7QnYdfe',
        f'<p>下载 <a href="{REL}/releases/download/v1.1.0/ignovate-1.1.0-unix.tar.gz">'
        f'ignovate-1.1.0-unix.tar.gz</a>，支持 Intel / Apple Silicon macOS 和 '
        f'x86_64 / ARM64 Linux。在下载目录执行：</p>',
        'macOS/Linux 下载段',
    ),
    (
        'doxcnklzzLGWkUchTsYNpXCfFl6',
        f'<p>下载 <a href="{REL}/releases/download/v1.1.0/ignovate-1.1.0-windows.zip">'
        f'ignovate-1.1.0-windows.zip</a>，在 PowerShell 中执行：</p>',
        'Windows 下载段',
    ),
    (
        'doxcntAFPAlNwvir4LukCBAjtse',
        f'<p>未填写模型连接时，<b>doctor</b> 返回退出码 1 并给出配置指引。'
        f'Git 是可选项目工具，使用 diff / review 前需自行安装。'
        f'完整安装位置、PATH、校验和升级说明见 '
        f'<a href="{REL}/blob/v1.1.0/docs/INSTALL.md">Release 安装说明</a>。</p>',
        'Release 安装说明链接',
    ),
    (
        'doxcn0gwmOmmtselxKJsgcIJDZc',
        f'<p>推送与 <b>pyproject.toml</b> 版本一致的 <b>v*</b> tag 后，'
        f'<a href="{REL}/blob/v1.1.0/.github/workflows/release.yml">Release 工作流</a> '
        f'会执行 Python 3.11/3.13 完整离线测试、macOS/Linux 实际安装检查，'
        f'以及 Windows 原生启动器、自动下载环境和文件/进程工具检查。'
        f'全部检查通过后才发布 macOS 桌面安装包、Unix 安装包、Windows 安装包、wheel 和 '
        f'<b>SHA256SUMS</b>。'
        f'<a href="{REL}/actions/runs/37927570145">v1.0.3 发布检查</a> 已通过；'
        f'Windows 两个 Python 版本各完成 1147 项测试（21 项平台跳过），'
        f'已安装 wheel 完成 81 项原生验收测试（8 项跳过），'
        f'包含缺少 TERM 时真实 WindowsDriver 下的欢迎字标、Token Weather 颜色、'
        f'左右布局和退出后的控制台模式恢复。实际发布资产的版本、内外 SHA-256 及安装后 '
        f'wheel smoke 已核验。Windows CI 使用 Windows Server 2025；'
        f'Windows 11 桌面上的字体与逐像素外观仍需实机验证。</p>',
        '开发与发布验证段',
    ),
]


def lark(*argv: str) -> dict:
    out = subprocess.run(['lark-cli', *argv], capture_output=True, text=True)
    if out.returncode != 0 and not out.stdout.strip():
        sys.exit(f'lark-cli failed: {out.stderr.strip()}')
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        sys.exit(f'unexpected output: {out.stdout[:400]}{out.stderr[:400]}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()

    print(f'待执行 {len(EDITS)} 条 block_replace：')
    for _, _, label in EDITS:
        print(f'  · {label}')

    if not args.run:
        print('\n[dry-run] 未写入。加 --run 执行。')
        return 0

    failures = 0
    for index, (block_id, xml, label) in enumerate(EDITS, 1):
        payload = lark('docs', '+update', '--doc', DOC, '--as', 'user',
                       '--command', 'block_replace',
                       '--block-id', block_id, '--content', xml)
        data = payload.get('data', {})
        ok = payload.get('ok')
        print(f'{index}/{len(EDITS)} {label}: ok={ok} result={data.get("result")} '
              f'warnings={data.get("warnings") or []}')
        if not ok:
            failures += 1
            print('   error:', json.dumps(payload.get('error'), ensure_ascii=False))
    print(f'\n完成：{len(EDITS) - failures} 成功 / {failures} 失败')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
