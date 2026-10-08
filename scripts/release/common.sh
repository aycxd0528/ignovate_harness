# Shared by the release installer and launcher. No Python is needed to load this.
fail() { printf 'ignovate: %s\n' "$*" >&2; exit 1; }
say() { printf 'ignovate: %s\n' "$*"; }
shell_quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

digest() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 "$1" | awk '{print $NF}'
    else fail '需要 sha256sum、shasum 或 openssl 才能校验安装包。'; fi
}

verify_bundle() {
    [ -f "$package_dir/SHA256SUMS" ] || fail '安装包缺少 SHA256SUMS，请重新下载。'
    while read -r expected filename; do
        case "$filename" in ''|*/*|*..*) fail '安装包校验清单无效。' ;; esac
        [ -f "$package_dir/$filename" ] || fail "安装包缺少 ${filename}。"
        actual=$(digest "$package_dir/$filename")
        [ "$actual" = "$expected" ] || fail "校验失败：${filename}，请重新下载安装包。"
    done < "$package_dir/SHA256SUMS"
}

download() {
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --retry 3 --connect-timeout 20 --proto '=https' --tlsv1.2 "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget --https-only --tries=3 --timeout=20 -O "$2" "$1"
    else fail '需要 curl 或 wget 才能下载环境。请安装其中一个后重试。'; fi
}

read_version() {
    version=$(cat "$package_dir/VERSION")
    case "$version" in ''|*[!0-9.]*|.*|*..*) fail '安装包版本无效。' ;; esac
}
