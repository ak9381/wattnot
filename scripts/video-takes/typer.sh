t(){ printf '\e[1;32mwattnot\e[0m:\e[1;34m%s\e[0m$ ' "$(basename "$PWD")"; local s="$1"; for ((i=0;i<${#s};i++)); do printf '%s' "${s:i:1}"; sleep 0.035; done; echo; eval "$s"; sleep "${2:-2}"; }
say(){ printf '\n\e[1;33m# %s\e[0m\n' "$1"; sleep 1.5; }
