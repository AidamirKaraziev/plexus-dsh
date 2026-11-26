#!/usr/bin/env bash
# Выпуск пакетов @plexus-dsh в npm.
#
#   tools/опубликовать.sh                проверка: pack пяти пакетов, проверка tarball'ов, сторож; ничего не публикует
#   tools/опубликовать.sh --публиковать  то же, затем pnpm publish в порядке зависимостей (npm login и 2FA — на человеке);
#                                        версии, которые уже есть в npm (npm view), пропускаются — npm их всё равно не примет
#   tools/опубликовать.sh --tgz <папка>  то же, но вместо публикации кладёт в <папка> tarball'ы только новых версий
#                                        и порядок.txt: строки "@plexus-dsh/<п> <версия> <файл.tgz>" в порядке зависимостей
#                                        (для CI: дальше npm publish <файл.tgz>); ничего не публикует, зеркало не трогает
#
# Проверка:
#  - у пакета нет private, есть license MIT и publishConfig.access public;
#  - в tarball нет workspace:, внутренние зависимости — точные версии соседних пакетов;
#  - bundledDependencies бандла лежат внутри tarball'а;
#  - tools/сторож.py ничего не находит в распакованном содержимом.
# Переменная PACKAGES_DIR подменяет папку packages (для тестов).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="${PACKAGES_DIR:-$ROOT/packages}"
GUARD="$ROOT/tools/сторож.py"
# Порядок зависимостей: сначала четыре, бандл последним — он их тянет.
ORDER=(meter warm read-cap scout bundle)

DOPUBLISH=0
TGZ=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --публиковать) DOPUBLISH=1 ;;
    --tgz)
      if [ "$#" -lt 2 ]; then echo "опубликовать: --tgz требует папку" >&2; exit 2; fi
      TGZ="$2"; shift ;;
    *) echo "опубликовать: неизвестный флаг $1" >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$TGZ" ] && [ "$DOPUBLISH" -eq 1 ]; then
  echo "опубликовать: --tgz и --публиковать вместе нельзя" >&2
  exit 2
fi
if [ -n "$TGZ" ]; then
  mkdir -p "$TGZ"
  TGZ="$(cd "$TGZ" && pwd)"
  # прошлые остатки не должны сойти за результат этого запуска
  rm -f "$TGZ"/*.tgz "$TGZ/порядок.txt"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/tgz" "$TMP/распаковано"

if [ ! -d "$PKG/node_modules" ]; then
  echo "== зависимости"
  pnpm -C "$PKG" install --frozen-lockfile --prefer-offline
fi

echo "== сборка"
pnpm -C "$PKG" run build

echo "== pack"
for p in "${ORDER[@]}"; do
  (cd "$PKG/plexus-dsh-$p" && pnpm pack --pack-destination "$TMP/tgz" >/dev/null)
done

echo "== проверка tarball'ов"
python3 - "$PKG" "$TMP" "${ORDER[@]}" <<'PY'
import json, sys, tarfile, os
пак, врем, *порядок = sys.argv[1:]
ошибки = []
версии = {}
for п in порядок:
    версии["@plexus-dsh/" + п] = json.load(open(f"{пак}/plexus-dsh-{п}/package.json"))["version"]
for п in порядок:
    имя = "@plexus-dsh/" + п
    архив = f"{врем}/tgz/plexus-dsh-{п}-{версии[имя]}.tgz"
    if not os.path.isfile(архив):
        ошибки.append(f"{имя}: нет tarball'а {os.path.basename(архив)}")
        continue
    куда = f"{врем}/распаковано/{п}"
    os.makedirs(куда)
    with tarfile.open(архив) as t:
        t.extractall(куда, filter="data")
    m = json.load(open(f"{куда}/package/package.json"))
    if m.get("private"):
        ошибки.append(f"{имя}: private в tarball'е")
    # Ссылка на витрину законно содержит имя аккаунта: сверяем её здесь точно
    # и убираем из распакованной копии, чтобы сторож не ловил её как утечку.
    ждём = {"type": "git", "url": "git+https://github.com/AidamirKaraziev/plexus-dsh.git",
            "directory": f"packages/plexus-dsh-{п}"}
    if m.get("repository") != ждём:
        ошибки.append(f"{имя}: repository не ведёт на витрину: {m.get('repository')}")
    m.pop("repository", None)
    with open(f"{куда}/package/package.json", "w") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    if m.get("license") != "MIT":
        ошибки.append(f"{имя}: license не MIT")
    if m.get("publishConfig", {}).get("access") != "public":
        ошибки.append(f"{имя}: нет publishConfig.access public")
    if m.get("version") != версии[имя]:
        ошибки.append(f"{имя}: версия в tarball'е {m.get('version')}")
    for секция in ("dependencies", "peerDependencies", "optionalDependencies"):
        for д, в in m.get(секция, {}).items():
            if "workspace:" in в:
                ошибки.append(f"{имя}: {секция}.{д} = {в} (workspace: не заменён)")
            elif д in версии and в != версии[д]:
                ошибки.append(f"{имя}: {секция}.{д} = {в}, ждали точную {версии[д]}")
    for д in m.get("bundledDependencies", []):
        if not os.path.isfile(f"{куда}/package/node_modules/{д}/package.json"):
            ошибки.append(f"{имя}: bundledDependencies {д} не лежит внутри tarball'а")
            continue
        вн = f"{куда}/package/node_modules/{д}/package.json"
        mm = json.load(open(вн))
        if mm.get("repository", {}).get("url") != ждём["url"]:
            ошибки.append(f"{имя}: у вложенного {д} repository не ведёт на витрину")
        mm.pop("repository", None)
        with open(вн, "w") as f:
            json.dump(mm, f, ensure_ascii=False, indent=2)
    print(f"  {имя}@{версии[имя]}")
for e in ошибки:
    print("  ОШИБКА:", e)
sys.exit(1 if ошибки else 0)
PY

echo "== сторож"
for p in "${ORDER[@]}"; do
  echo "  $p"
  python3 "$GUARD" "$TMP/распаковано/$p/package" || { echo "опубликовать: сторож нашёл личное в @plexus-dsh/$p" >&2; exit 1; }
done

# Что публиковать: версии, которых ещё нет в npm. Опубликованную версию npm не примет повторно.
NEW=()
for p in "${ORDER[@]}"; do
  v=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$PKG/plexus-dsh-$p/package.json")
  if [ -n "$(npm view "@plexus-dsh/$p@$v" version 2>/dev/null)" ]; then
    echo "  @plexus-dsh/$p@$v уже в npm — пропуск"
  else
    NEW+=("$p")
  fi
done
if [ -n "$TGZ" ]; then
  : > "$TGZ/порядок.txt"
  for p in "${NEW[@]+"${NEW[@]}"}"; do
    v=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$PKG/plexus-dsh-$p/package.json")
    f="plexus-dsh-$p-$v.tgz"
    cp "$TMP/tgz/$f" "$TGZ/$f"
    echo "@plexus-dsh/$p $v $f" >> "$TGZ/порядок.txt"
  done
  echo "== tarball'ы новых версий: ${#NEW[@]} — в $TGZ, порядок в порядок.txt"
  exit 0
fi
if [ "${#NEW[@]}" -eq 0 ]; then
  echo "== все версии уже в npm, публиковать нечего"
  exit 0
fi

CMD=""
for p in "${NEW[@]}"; do
  CMD+="(cd packages/plexus-dsh-$p && pnpm publish --access public) && "
done
CMD="${CMD% && }"

if [ "$DOPUBLISH" -eq 0 ]; then
  echo "== проверка пройдена, ничего не опубликовано"
  echo "Публикация (npm login и 2FA — на вас):"
  echo "  tools/опубликовать.sh --публиковать"
  echo "или руками:"
  echo "  $CMD"
  exit 0
fi

echo "== публикация"
for p in "${NEW[@]}"; do
  (cd "$PKG/plexus-dsh-$p" && pnpm publish --access public --no-git-checks)
done
echo "== опубликовано"

# DSH спрашивает и зеркало npmmirror; само оно подтягивает новую версию через ~час, и всё это время
# «Версии» в DSH пишут «Актуально». Просим синхронизацию сразу. Сбой не валит выпуск.
# NPMMIRROR_SYNC="" выключает шаг (тесты).
MIRROR="${NPMMIRROR_SYNC-https://registry-direct.npmmirror.com}"
if [ -n "$MIRROR" ]; then
  echo "== зеркало npmmirror"
  for p in "${NEW[@]}"; do
    if curl -fsS -X PUT "$MIRROR/-/package/@plexus-dsh/$p/syncs" >/dev/null; then
      echo "  @plexus-dsh/$p: синхронизация запрошена"
    else
      echo "  @plexus-dsh/$p: зеркало не ответило — повторить: curl -X PUT $MIRROR/-/package/@plexus-dsh/$p/syncs"
    fi
  done
fi
