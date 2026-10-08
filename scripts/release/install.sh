#!/bin/sh
set -eu
umask 077
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$package_dir/common.sh"
verify_bundle
read_version
case "$(uname -s)" in Darwin|Linux) ;; *) fail '此安装器支持 macOS / Linux；Windows 请运行 install.ps1。' ;; esac
install_home=${IGNOVATE_INSTALL_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/ignovate}
bin_dir=${IGNOVATE_BIN_DIR:-$HOME/.local/bin}
mkdir -p "$install_home/releases" "$bin_dir"
install_home=$(CDPATH= cd -- "$install_home" && pwd)
bin_dir=$(CDPATH= cd -- "$bin_dir" && pwd)
release_dir=$install_home/releases/$version
if [ "$package_dir" != "$release_dir" ]; then
    mkdir -p "$release_dir"
    cp "$package_dir"/* "$release_dir/"
fi
chmod 755 "$release_dir/install.sh" "$release_dir/launch.sh"
# A wrapper rather than a venv console script also works when Python is absent.
{
    printf '#!/bin/sh\nexport IGNOVATE_INSTALL_HOME='
    shell_quote "$install_home"
    printf '\nexec '
    shell_quote "$release_dir/launch.sh"
    printf ' "$@"\n'
} > "$bin_dir/.ignovate-new"
chmod 755 "$bin_dir/.ignovate-new"
mv -f "$bin_dir/.ignovate-new" "$bin_dir/ignovate"
{
    printf 'export PATH='
    shell_quote "$bin_dir"
    printf ':"$PATH"\n'
} > "$bin_dir/ignovate-env.sh"
if [ "${IGNOVATE_NO_MODIFY_PATH:-0}" != 1 ]; then
    # Idempotent profile updates for login shells and interactive zsh/bash.
    profile_line=". $(shell_quote "$bin_dir/ignovate-env.sh") # ignovate PATH"
    bash_login_profile=$HOME/.profile
    if [ -f "$HOME/.bash_profile" ]; then bash_login_profile=$HOME/.bash_profile
    elif [ -f "$HOME/.bash_login" ]; then bash_login_profile=$HOME/.bash_login; fi
    for profile in "$HOME/.profile" "$bash_login_profile" "$HOME/.bashrc" "${ZDOTDIR:-$HOME}/.zprofile" "${ZDOTDIR:-$HOME}/.zshrc"; do
        if [ ! -f "$profile" ] || ! grep -Fqx "$profile_line" "$profile"; then
            printf '\n%s\n' "$profile_line" >> "$profile"
        fi
    done
fi
say "已安装启动器：$bin_dir/ignovate"
say '重新打开终端即可运行 ignovate set up。当前终端执行：'
printf '. %s\nignovate set up\n' "$(shell_quote "$bin_dir/ignovate-env.sh")"
