#!/usr/bin/env bash
# Проверка CI мастерской: чистый HEAD зелёный во всех workflow, HEAD со сломанным тестом краснит ci.yml.
# Коды: 0 — как ожидалось, 1 — не как ожидалось, 2 — fail подготовки.
# Пушит только ci/e16-s09-green и ci/e16-s09-red (force); main и теги не трогает; обе ветки удаляет в любом исходе.
set -u

GREEN=ci/e16-s09-green
RED=ci/e16-s09-red
TIMEOUT=${CI_TIMEOUT:-2400}    # секунд на ожидание всех прогонов (Windows install идёт до ~20 мин)
APPEAR=${CI_APPEAR:-300} # секунд на появление прогонов после пуша
EXPECT_GREEN=(ci install bench)

TMP=""
PUSHED_GREEN=0
PUSHED_RED=0
RUNS=""

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [ "$PUSHED_GREEN" = 1 ]; then
    if git push -q origin --delete "$GREEN" >/dev/null 2>&1; then echo "удалена ветка origin/$GREEN"; else echo "! не удалось удалить origin/$GREEN" >&2; fi
  fi
  if [ "$PUSHED_RED" = 1 ]; then
    if git push -q origin --delete "$RED" >/dev/null 2>&1; then echo "удалена ветка origin/$RED"; else echo "! не удалось удалить origin/$RED" >&2; fi
  fi
  if [ -n "$TMP" ] && [ -d "$TMP" ]; then
    git worktree remove --force "$TMP/wt" >/dev/null 2>&1
    rm -rf "$TMP"
    git worktree prune >/dev/null 2>&1
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

fail() { echo "✗ подготовка: $*" >&2; exit 2; }

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || fail "не git-репозиторий"
command -v gh >/dev/null 2>&1 || fail "нет gh"
command -v python3 >/dev/null 2>&1 || fail "нет python3"
gh auth status >/dev/null 2>&1 || fail "gh не авторизован"
git remote get-url origin >/dev/null 2>&1 || fail "нет remote origin"
[ -z "$(git status --porcelain --untracked-files=no)" ] || fail "в рабочей копии есть незакоммиченные изменения (HEAD грязный)"
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null) || fail "gh repo view не сработал"
[ -n "$REPO" ] || fail "не определён репозиторий"
HEAD_SHA=$(git rev-parse HEAD) || fail "нет HEAD"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/проверка_ci.XXXXXX") || fail "mktemp"
git worktree add -q --detach "$TMP/wt" "$HEAD_SHA" || fail "git worktree add"

echo "репо: $REPO, HEAD: ${HEAD_SHA:0:12}"

# --- green: сам HEAD
PUSHED_GREEN=1
git -C "$TMP/wt" push -q -f origin "$HEAD_SHA:refs/heads/$GREEN" || { PUSHED_GREEN=0; fail "пуш $GREEN"; }
echo "запушено: $GREEN @ ${HEAD_SHA:0:12}"

# --- red: HEAD + падающий тест (только во временном worktree)
TESTDIR="packages/plexus-dsh-hello/test"
[ -d "$TMP/wt/$TESTDIR" ] || fail "нет $TESTDIR"
cat > "$TMP/wt/$TESTDIR/zz-broken.test.mjs" <<'JS'
import { test } from 'node:test';
import assert from 'node:assert/strict';
test('заведомо падающий тест проверки CI', () => {
  assert.fail('проверка_ci.sh: тест сломан намеренно');
});
JS
git -C "$TMP/wt" add -f "$TESTDIR/zz-broken.test.mjs"
git -C "$TMP/wt" -c user.name=проверка-ci -c user.email=проверка-ci@localhost -c commit.gpgsign=false \
  commit -q -m "проверка CI: заведомо падающий тест (временный коммит)" || fail "коммит красной ветки"
RED_SHA=$(git -C "$TMP/wt" rev-parse HEAD)
PUSHED_RED=1
git -C "$TMP/wt" push -q -f origin "$RED_SHA:refs/heads/$RED" || { PUSHED_RED=0; fail "пуш $RED"; }
echo "запушено: $RED @ ${RED_SHA:0:12}"

# --- ожидание прогонов
# concl_of: "имя<TAB>status<TAB>conclusion<TAB>id" по прогонам с нужным headSha (последний на workflow)
list_runs() { # branch sha
  gh run list -R "$REPO" --branch "$1" -L 50 --json workflowName,headSha,status,conclusion,createdAt,databaseId 2>/dev/null |
    python3 -c '
import json,sys
sha=sys.argv[1]
try: d=json.load(sys.stdin)
except Exception: d=[]
best={}
for r in d:
    if r["headSha"]!=sha: continue
    n=r["workflowName"]
    if n not in best or r["createdAt"]>best[n]["createdAt"]: best[n]=r
for n,r in sorted(best.items()):
    print("%s\t%s\t%s\t%s"%(n,r["status"],r["conclusion"] or "",r["databaseId"]))
' "$2"
}

# ждёт, пока все needed workflow появятся и завершатся; результат — в RUNS
wait_runs() { # branch sha workflow...
  local branch=$1 sha=$2; shift 2
  local needed=("$@") t0=$SECONDS lst w st done_ last=""
  while :; do
    lst=$(list_runs "$branch" "$sha")
    done_=1
    for w in "${needed[@]}"; do
      st=$(printf '%s\n' "$lst" | awk -F'\t' -v w="$w" '$1==w{print $2}')
      if [ -z "$st" ]; then
        done_=0
        if [ $((SECONDS - t0)) -gt "$APPEAR" ]; then
          echo "✗ $branch: прогон $w не появился за ${APPEAR}с" >&2; RUNS="$lst"; return 1
        fi
      elif [ "$st" != completed ]; then
        done_=0
      fi
    done
    if [ "$lst" != "$last" ]; then
      echo "[$branch] $(printf '%s\n' "$lst" | awk -F'\t' '{printf "%s=%s%s  ", $1,$2,($3?"/"$3:"")}')"
      last=$lst
    fi
    if [ "$done_" = 1 ]; then RUNS="$lst"; return 0; fi
    if [ $((SECONDS - t0)) -gt "$TIMEOUT" ]; then
      echo "✗ $branch: таймаут ${TIMEOUT}с" >&2; RUNS="$lst"; return 1
    fi
    sleep 20
  done
}

concl_of() { printf '%s\n' "$RUNS" | awk -F'\t' -v w="$1" '$1==w{print $3}'; }

RESULT=0
echo "ждём прогоны $GREEN (ci, install, bench) и $RED (ci)…"

wait_runs "$GREEN" "$HEAD_SHA" "${EXPECT_GREEN[@]}"; OK_G=$?
RUNS_G=$RUNS
wait_runs "$RED" "$RED_SHA" ci; OK_R=$?
RUNS_R=$RUNS

echo "--- итоги"
for w in "${EXPECT_GREEN[@]}"; do
  RUNS=$RUNS_G; c=$(concl_of "$w")
  if [ "$c" = success ]; then echo "✓ green/$w: success"; else echo "✗ green/$w: ${c:-нет результата} (ожидалось success)"; RESULT=1; fi
done
RUNS=$RUNS_R; c=$(concl_of ci)
if [ "$c" = failure ]; then echo "✓ red/ci: failure (как и должно быть)"; else echo "✗ red/ci: ${c:-нет результата} (ожидалось failure)"; RESULT=1; fi
if [ "$OK_G" != 0 ] || [ "$OK_R" != 0 ]; then RESULT=1; fi

if [ "$RESULT" = 0 ]; then echo "✓ CI ведёт себя как ожидалось"; else echo "✗ CI ведёт себя не так, как ожидалось"; fi
exit "$RESULT"
