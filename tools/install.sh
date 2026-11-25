#!/usr/bin/env bash
# Установка пакетов продукта в профили DSH одной командой.
# Только для профилей bench-*: на другом имени (desktop, headless, web…) падает кодом 1 — в Desktop
# пакеты ставятся через Plugins → Discover (маркет plexus-dsh), см. README витрины.
#
#   tools/install.sh --profiles bench-warm   собрать пакеты продукта, скопировать в ~/.<имя>/packages,
#                                    вписать в cordis.patch.yml названных профилей bench-*
#   tools/install.sh --uninstall     снять блок пакетов из патчей (файлы в ~/.<имя>/packages остаются)
#   tools/install.sh --no-build      не собирать (взять готовые lib/)
#   tools/install.sh --profiles bench-warm --from bench-base --packages "<имя>-meter <имя>-warm"
#                                    профиль-копия: файлы профиля bench-base (package.json, pnpm-workspace.yaml,
#                                    cordis.yml, cordis.patch.yml) копируются в bench-warm заново, затем блок
#                                    только с названными пакетами (install: false не мешает);
#                                    исходный профиль не меняется
#
# Идемпотентно. Перед первой правкой патча — копия cordis.patch.yml.<имя>.bak.
# DSH_HOME (по умолчанию ~/.dsh) и PLUGIN_HOME (~/.<имя>) можно переопределить.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKGS="$ROOT/packages"
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
NAME="$(PYTHONPATH="$ROOT/tools" python3 -c 'from dsh_digest import NAME; print(NAME)')"
PLUGIN_HOME="${PLUGIN_HOME:-$HOME/.$NAME}"
PROFILES="desktop headless"
ACTION=install
BUILD=1
FROM=""
ONLY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) ACTION=uninstall ;;
    --no-build) BUILD=0 ;;
    --profiles) PROFILES="$2"; shift ;;
    --from) FROM="$2"; shift ;;
    --packages) ONLY="$2"; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "install.sh: неизвестный флаг $1" >&2; exit 2 ;;
  esac
  shift
done

for p in $PROFILES; do
  case "$p" in
    bench-*) ;;
    *) echo "install.sh: профиль $p не bench-*. Пакеты в обычный профиль ставятся через Plugins → Discover (маркет plexus-dsh, см. README), а не этим скриптом." >&2; exit 1 ;;
  esac
done

if [ "$ACTION" = uninstall ]; then
  for p in $PROFILES; do
    python3 "$ROOT/tools/plexus_dsh_patch.py" uninstall "$DSH_HOME/profiles/$p/cordis.patch.yml"
  done
  exit 0
fi

if [ "$BUILD" = 1 ]; then
  echo "• сборка packages/"
  pnpm -C "$PKGS" install --frozen-lockfile --silent
  pnpm -C "$PKGS" run --silent build
fi

ENTRIES="$(mktemp -t "$NAME-entries")"
trap 'rm -f "$ENTRIES" "$ENTRIES".*' EXIT

echo "• копирование в $PLUGIN_HOME/packages"
mkdir -p "$PLUGIN_HOME/packages"
INSTALLED=()
for dir in "$PKGS"/"$NAME"-*/; do
  dir="${dir%/}"
  # бандл ставится DSH сам, а не копией в PLUGIN_HOME: у него нет lib/
  [ -f "$dir/package.json" ] && [ "$(node -p "require('$dir/package.json').dsh?.bundle ? 1 : 0")" = 1 ] && continue
  [ -f "$dir/lib/index.js" ] || { echo "нет $dir/lib/index.js — соберите пакеты" >&2; exit 1; }
  id="$(cd "$dir" && node -p "require('./package.json').plexus?.id ?? require('path').basename(process.cwd())")"
  if [ -n "$ONLY" ]; then
    # --packages: ставятся ровно названные, install не учитывается
    case " $ONLY " in *" $id "*) ;; *) continue ;; esac
  elif [ "$(cd "$dir" && node -p "require('./package.json').plexus?.bundle?.build ?? true")" = false ]; then
    # bundle.build: false — пакет собирается, но в бандл не входит (hello, budget)
    echo "  $id — пропуск (bundle.build: false)"; continue
  fi
  dest="$PLUGIN_HOME/packages/$id"
  rm -rf "$dest.tmp" && mkdir -p "$dest.tmp"
  cp -R "$dir/lib" "$dir/package.json" "$dest.tmp/"
  if [ -f "$dir/providers.json" ]; then cp "$dir/providers.json" "$dest.tmp/"; fi
  rm -rf "$dest" && mv "$dest.tmp" "$dest"
  INSTALLED+=("$dest")
  echo "  $id → $dest"
done

if [ -n "$ONLY" ]; then
  for want in $ONLY; do
    found=0
    for d in ${INSTALLED[@]+"${INSTALLED[@]}"}; do if [ "$(basename "$d")" = "$want" ]; then found=1; fi; done
    [ $found = 1 ] || { echo "install.sh: пакет $want не найден в packages/" >&2; exit 1; }
  done
fi

if [ -n "$FROM" ]; then
  src="$DSH_HOME/profiles/$FROM"
  [ -f "$src/cordis.patch.yml" ] || { echo "install.sh: нет профиля-источника $src" >&2; exit 1; }
  for p in $PROFILES; do
    [ "$p" = "$FROM" ] && { echo "install.sh: --from $FROM совпадает с целевым профилем" >&2; exit 2; }
    dst="$DSH_HOME/profiles/$p"
    echo "• профиль $p ← копия $FROM"
    mkdir -p "$dst"
    for f in package.json pnpm-workspace.yaml cordis.yml cordis.patch.yml; do
      [ -f "$src/$f" ] && cp "$src/$f" "$dst/$f"
    done
  done
fi

for p in $PROFILES; do
  patch="$DSH_HOME/profiles/$p/cordis.patch.yml"
  [ -d "$(dirname "$patch")" ] || { echo "  профиль $p не найден в $DSH_HOME/profiles — пропуск" >&2; continue; }
  # Записи для профиля: config из package.json, строка "{profile}" заменяется именем профиля.
  PROFILE="$p" node -e '
    const fs = require("fs"), path = require("path")
    const [file, ...dirs] = process.argv.slice(1)
    const out = dirs.map(dir => {
      const meta = JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8")).plexus ?? {}
      const config = JSON.parse(JSON.stringify(meta.config ?? {}).replaceAll("{profile}", process.env.PROFILE))
      return { id: meta.id ?? path.basename(dir), name: path.join(dir, "lib", "index.js"), config }
    })
    fs.writeFileSync(file, JSON.stringify(out))
  ' "$ENTRIES.$p" "${INSTALLED[@]}"
  python3 "$ROOT/tools/plexus_dsh_patch.py" install "$patch" "$ENTRIES.$p"
done
