#!/bin/sh
set -eu
umask 077
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$package_dir/common.sh"
read_version
install_home=${IGNOVATE_INSTALL_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/ignovate}
runtime=$install_home/venvs/$version
export PATH="$install_home/tools:$PATH"
setup_requested=0
environment_only=0
if [ "${1:-}" = set ] && [ "${2:-}" = up ]; then
    setup_requested=1
    shift 2
    if [ "${1:-}" = --environment-only ]; then environment_only=1; shift; fi
    if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
        printf '用法：ignovate set up [--environment-only] [配置向导参数]\n自动准备 Python、应用依赖和 ripgrep，然后打开模型配置向导。\n'
        exit 0
    fi
elif [ "${1:-}" = --setup ]; then
    setup_requested=1
    shift
fi

prepare_environment() {
    verify_bundle
    mkdir -p "$install_home/tools" "$install_home/venvs"
    mkdir "$install_home/setup.lock" 2>/dev/null || fail "另一个配置进程正在运行；若已中断，删除 $install_home/setup.lock 后重试。"
    work_dir=$(mktemp -d "$install_home/.setup-XXXXXX")
    trap 'rm -rf "$work_dir"; rmdir "$install_home/setup.lock" 2>/dev/null || true' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    rm -f "$runtime/.ready"
    if command -v uv >/dev/null 2>&1; then uv_command=$(command -v uv)
    else
        say '未找到 uv，正在下载环境管理器…'
        download 'https://astral.sh/uv/0.12.23/install.sh' "$work_dir/uv-install.sh"
        UV_UNMANAGED_INSTALL="$install_home/tools" sh "$work_dir/uv-install.sh"
        uv_command=$install_home/tools/uv
    fi
    export UV_PYTHON_INSTALL_DIR="$install_home/python"
    export UV_CACHE_DIR="$install_home/cache"
    export UV_PYTHON_DOWNLOADS=automatic
    # Prefer a compatible system Python; uv downloads one if none is available.
    export UV_PYTHON_PREFERENCE=system
    if [ ! -x "$runtime/bin/python" ]; then
        say '检查 Python 3.11–3.13；缺少时自动下载并创建隔离环境…'
        "$uv_command" --no-config venv --no-project --python '>=3.11,<3.14' "$runtime"
    fi
    say '安装并校验应用依赖…'
    "$uv_command" --no-config pip install --python "$runtime/bin/python" --require-hashes -r "$package_dir/requirements-release.lock"
    "$uv_command" --no-config pip install --python "$runtime/bin/python" --no-deps --reinstall-package ignovate-harness "$package_dir/ignovate_harness-$version-py3-none-any.whl"
    "$uv_command" --no-config pip check --python "$runtime/bin/python"
    if ! command -v rg >/dev/null 2>&1; then
        case "$(uname -s):$(uname -m)" in
            Darwin:arm64|Darwin:aarch64) target=aarch64-apple-darwin ;;
            Darwin:x86_64) target=x86_64-apple-darwin ;;
            Linux:aarch64|Linux:arm64) target=aarch64-unknown-linux-musl ;;
            Linux:x86_64|Linux:amd64) target=x86_64-unknown-linux-musl ;;
            *) fail '暂不支持该处理器；请自行安装 ripgrep 后重试。' ;;
        esac
        say '未找到 ripgrep，正在下载搜索工具…'
        archive=ripgrep-15.2.0-$target.tar.gz
        url=https://github.com/BurntSushi/ripgrep/releases/download/15.2.0/$archive
        download "$url" "$work_dir/$archive"
        download "$url.sha256" "$work_dir/rg.sha256"
        expected=$(awk 'NR==1 {print $1}' "$work_dir/rg.sha256")
        [ "$(digest "$work_dir/$archive")" = "$expected" ] || fail 'ripgrep 下载校验失败，请重试。'
        tar -xzf "$work_dir/$archive" -C "$work_dir"
        cp "$work_dir/ripgrep-15.2.0-$target/rg" "$install_home/tools/rg"
        chmod 755 "$install_home/tools/rg"
    fi
    "$runtime/bin/python" -c 'import main, textual, mcp, langchain_deepseek'
    printf '%s\n' "$version" > "$runtime/.ready"
    say '环境已就绪。'
    if ! command -v git >/dev/null 2>&1; then
        say 'Git 未安装；普通对话和文件工具可用，diff/review 需要自行安装 Git。'
    fi
    rm -rf "$work_dir"
    rmdir "$install_home/setup.lock"
    trap - EXIT INT TERM
}

if [ "$setup_requested" = 1 ] || [ ! -f "$runtime/.ready" ] || [ ! -x "$runtime/bin/python" ]; then
    prepare_environment
fi
if [ "$environment_only" = 1 ]; then
    [ "$#" = 0 ] || fail '--environment-only 不接受额外参数。'
    exit 0
fi
if [ "$setup_requested" = 1 ]; then set -- --setup "$@"; fi
exec "$runtime/bin/python" -m nailong.cli "$@"
