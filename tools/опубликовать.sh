#!/usr/bin/env bash
# Выпуск пакетов @plexus-dsh в npm.
#
#   tools/опубликовать.sh                проверка: pack пяти пакетов, проверка tarball'ов, сторож; ничего не публикует
#   tools/опубликовать.sh --публиковать  то же, затем pnpm publish в порядке зависимостей (npm login и 2FA — на человеке)
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
for a in "$@"; do
  case "$a" in
    --публиковать) DOPUBLISH=1 ;;
    *) echo "опубликовать: неизвестный флаг $a" >&2; exit 2 ;;
  esac
done

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

CMD=""
for p in "${ORDER[@]}"; do
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
for p in "${ORDER[@]}"; do
  (cd "$PKG/plexus-dsh-$p" && pnpm publish --access public --no-git-checks)
done
echo "== опубликовано"
