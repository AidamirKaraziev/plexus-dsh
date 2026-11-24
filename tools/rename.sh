#!/usr/bin/env bash
# Перенос папки проекта: старое имя папки → новое имя продукта (по умолчанию см. tools/dsh_digest.py).
#
#   bash tools/rename.sh             перенести папку (запускает человек, не DSH)
#   bash tools/rename.sh --dry-run   печатать шаги с путями, ничего не менять
#   bash tools/rename.sh --self-test самопроверка во временной папке
#
# Запускать из папки ВНЕ проекта: текущая папка внутри RENAME_OLD — отказ
# (иначе оболочка останется в удалённом каталоге).
#
# Пути переопределяются переменными (нужны самопроверке):
#   RENAME_OLD       по умолчанию ~/Projects/<старое имя>
#   RENAME_NEW       по умолчанию ~/Projects/<имя продукта из tools/dsh_digest.py>
#   CLAUDE_PROJECTS  по умолчанию ~/.claude/projects
#   DSH_SESSIONS     по умолчанию ~/.dsh/sessions
#   RENAME_SKIP_PARENT_WRITE=1  в --dry-run не проверять права на родительскую папку
#
# Шаги: снести влитые дорожки → mv → git worktree repair → память Claude →
# сессии DSH (не переносятся) → итог. Код 2 — отказ, ничего не поменяно.

set -euo pipefail

TAB=$'\t'
MODE="${1:-}"

say() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }

refuse() {
	err "отказ: $*"
	exit 2
}

# Путь без хвостового слэша и с раскрытым ~.
norm() {
	local p="$1"
	case "$p" in "~"/*) p="$HOME/${p#\~/}" ;; "~") p="$HOME" ;; esac
	while [ "${#p}" -gt 1 ] && [ "${p%/}" != "$p" ]; do p="${p%/}"; done
	# /var и /private/var на macOS — одна и та же папка; берём физический путь.
	if [ -d "$p" ]; then
		p="$(cd "$p" && pwd -P)"
	elif [ -d "$(dirname "$p")" ]; then
		p="$(cd "$(dirname "$p")" && pwd -P)/$(basename "$p")"
	fi
	printf '%s' "$p"
}

# Имя продукта берётся из tools/dsh_digest.py (единственное место) — здесь своих копий нет.
NAME="$(PYTHONPATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" python3 -c 'from dsh_digest import NAME; print(NAME)')"
# Старое имя папки проекта собирается из кусков: прямого написания в репозитории быть не должно.
LEGACY_NAME="$(PYTHONPATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" python3 -c 'from dsh_digest import LEGACY_NAME; print(LEGACY_NAME)')"
OLD="$(norm "${RENAME_OLD:-$HOME/Projects/$LEGACY_NAME}")"
NEW="$(norm "${RENAME_NEW:-$HOME/Projects/$NAME}")"
CLAUDE_PROJECTS="$(norm "${CLAUDE_PROJECTS:-$HOME/.claude/projects}")"
DSH_SESSIONS="$(norm "${DSH_SESSIONS:-$HOME/.dsh/sessions}")"

# Имя ДОРОЖКИ в работе на каждый рабочий каталог (в порядке git worktree list).
worktrees() {
	local p=""
	while IFS= read -r -d '' line; do
		case "$line" in
			worktree\ *) printf '%s\n' "${line#worktree }" ;;
		esac
	done < <(git -C "$OLD" worktree list --porcelain -z)
}

# Папка памяти Claude: путь, где каждый символ кроме букв и цифр — «-».
claude_slug() {
	printf '%s' "$1" | LC_ALL=C sed 's/[^A-Za-z0-9]/-/g'
}

# Папка сессии DSH: «--<путь, где / → ->--».
dsh_slug() {
	printf '%s' "$1" | sed 's,/,-,g'
}

# Ветка дорожки полноценным именем refs/heads/… (или пусто — detached):
# в .git дорожек git пишет «/refs/heads/…» — одним слэшем больше.
worktree_branch() {
	local head
	head="$(git -C "$1" rev-parse --git-path HEAD 2>/dev/null)"
	case "$head" in /*|~*) ;; *) head="$1/$head" ;; esac
	head="$(sed -n 's/^ref: *//p' "$head" 2>/dev/null | head -1)"
	printf '%s' "${head#/}"
}

# Можно ли снести дорожку: своя ветка не main, git cherry main <ветка> пуст,
# файлы чисты. $1 — путь дорожки. Печатает полное имя ветки.
merged_worktree() {
	local path="$1" branch
	branch="$(worktree_branch "$path")"
	case "$branch" in refs/heads/*) ;; *) return 1 ;; esac
	[ "$branch" = "refs/heads/main" ] && return 1
	git -C "$OLD" rev-parse --verify --quiet "$branch" >/dev/null || return 1
	[ -z "$(git -C "$OLD" cherry main "${branch#refs/heads/}" 2>/dev/null)" ] || return 1
	[ -z "$(git -C "$path" status --porcelain 2>/dev/null)" ] || return 1
	printf '%s' "$branch"
}

# --- проверки, после которых ничего не меняем -------------------------------

common_checks() {
	local dry="$1" cwd parent w dir
	[ -d "$OLD" ] || refuse "нет исходной папки $OLD"
	git -C "$OLD" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
		|| refuse "$OLD — не репозиторий git"
	# Остатки прошлой попытки удалять нечем: у отражённых дорожек git помнит
	# старый путь в .git/worktrees/ — repair по пути дорожки их не находит.
	if [ -n "$(git -C "$OLD" worktree list --porcelain -z | grep -za '^prunable' || true)" ]; then
		refuse "в $OLD есть prunable-дорожки — сначала git -C $OLD worktree prune, потом git -C $OLD worktree list"
	fi
	if [ -e "$NEW" ] || [ -L "$NEW" ]; then
		refuse "целевая папка $NEW уже существует"
	fi
	parent="$(dirname "$NEW")"
	[ -d "$parent" ] || refuse "нет родительской папки $parent"
	# Проверку «в родительской папке можно писать» в --dry-run можно выключить:
	# под песочницей переименование в родителя не разрешено, а пройти сухой
	# прогон по боевым путям всё равно надо.
	if [ "${RENAME_SKIP_PARENT_WRITE:-}" != "1" ]; then
		[ -w "$parent" ] || refuse "нет прав на запись в $parent"
	fi
	cwd="$(pwd -P)"
	if [ "$dry" != "dry" ]; then
		case "$cwd/" in
			"$OLD"/*) refuse "ты в $cwd — внутри $OLD; выйди: cd $parent и повтори" ;;
		esac
	fi
	# .claude/ledger.jsonl пишется после каждого шага сессии и после закрытия
	# круга: это журнал, а не код. Переносу он не мешает — в отличие от правок
	# в коде, заметках или дорожках.
	w="$(git -C "$OLD" status --porcelain | grep -v -e '^.. \.claude/ledger\.jsonl$' || true)"
	if [ -n "$w" ]; then
		err "в $OLD есть незакоммиченные правки:"
		printf '%s\n' "$w" | head -20 >&2
		refuse "сначала закоммить или убери правки (git -C $OLD status)"
	fi
	dir="$CLAUDE_PROJECTS/$(claude_slug "$NEW")"
	if [ -e "$dir" ] || [ -L "$dir" ]; then
		refuse "папка памяти Claude для новой цели уже существует: $dir"
	fi
}

# --- шаги 1–5 ---------------------------------------------------------------

# Печатает: «remove<TAB>дорожка<TAB>ветка» для сносимых,
#            «keep<TAB>дорожка<TAB>ветка или -» для оставляемых.
plan_worktrees() {
	local path branch
	while IFS= read -r path; do
		[ "$path" = "$OLD" ] && continue
		case "$path" in
			"$OLD"/.claude/worktrees/*) ;;
			*) printf 'keep\t%s\t%s\n' "$path" ""; continue ;;
		esac
		if branch="$(merged_worktree "$path")"; then
			printf 'remove\t%s\t%s\n' "$path" "$branch"
		else
			branch="$(worktree_branch "$path")"
			printf 'keep\t%s\t%s\n' "$path" "${branch:--}"
		fi
	done < <(worktrees)
}

do_work() {
	local dry="$1" dir path branch removed=0 kept=0 kept_list="" target
	local new_path new_rel
	common_checks "$dry"

	say "перенос: $OLD → $NEW"

	# 1. Дорожки.
	say "шаг 1 · дорожки"
	while IFS="$TAB" read -r act path branch; do
		case "$act" in
			remove)
				# Дорожка живёт НА своей ветке ($branch). Сносим её вместе с
				# веткой — на явную ветку main не покушаемся никогда.
				if [ "$dry" = "dry" ]; then
					say "   снести влитую дорожку $path (ветка ${branch#refs/heads/})"
				else
					git -C "$OLD" worktree remove --force "$path"
					git -C "$OLD" branch -d "${branch#refs/heads/}" >/dev/null 2>&1 \
						|| err "   ветка ${branch#refs/heads/} не удалена — проверь git -C $OLD branch -d ${branch#refs/heads/}"
					say "   снесена влитая дорожка $path (ветка ${branch#refs/heads/})"
				fi
				removed=$((removed + 1))
				;;
			keep)
				if [ "$dry" = "dry" ]; then
					say "   оставить $path${branch:+ (ветка ${branch#refs/heads/})}"
				else
					say "   оставлена $path${branch:+ (ветка ${branch#refs/heads/})}"
				fi
				kept=$((kept + 1))
				kept_list="$kept_list$TAB$path"
				;;
		esac
	done < <(plan_worktrees)

	# 2. mv.
	say "шаг 2 · mv $OLD $NEW"
	if [ "$dry" != "dry" ]; then
		mv "$OLD" "$NEW"
	fi

	# 3. repair оставшихся дорожек под новым путём.
	say "шаг 3 · git worktree repair"
	if [ "$dry" = "dry" ]; then
		for path in $kept_list; do
			new_path="$NEW${path#"$OLD"}"
			say "   git -C $NEW worktree repair $new_path"
		done
	else
		local args=()
		for path in $kept_list; do
			new_path="$NEW${path#"$OLD"}"
			args+=("$new_path")
		done
		if [ "${#args[@]}" -gt 0 ]; then
			git -C "$NEW" worktree repair "${args[@]}" >/dev/null 2>&1 \
				|| err "   git worktree repair вернул ошибку — проверь git -C $NEW worktree list"
		fi
		for path in $kept_list; do
			new_path="$NEW${path#"$OLD"}"
			new_rel="${new_path#"$NEW"/}"
			if git -C "$NEW" worktree list --porcelain | grep -qxF "worktree $new_path"; then
				say "   $new_rel — на месте"
			else
				err "   $new_rel — git её не видит, проверь git -C $NEW worktree list"
			fi
		done
	fi

	# 4. Память Claude.
	say "шаг 4 · память Claude"
	target="$CLAUDE_PROJECTS/$(claude_slug "$NEW")"
	if [ -d "$CLAUDE_PROJECTS/$(claude_slug "$OLD")" ]; then
		if [ "$dry" = "dry" ]; then
			say "   mv $CLAUDE_PROJECTS/$(claude_slug "$OLD") $target"
		else
			mv "$CLAUDE_PROJECTS/$(claude_slug "$OLD")" "$target"
			say "   $CLAUDE_PROJECTS/$(claude_slug "$OLD") → $target"
		fi
	else
		say "   нет (папки $CLAUDE_PROJECTS/$(claude_slug "$OLD") нет)"
	fi

	# 5. Сессии DSH не переносятся. DSH кладёт сессию в папку по cwd из её
	# заголовка и при запуске сверяет одно с другим: сессия, перенесённая в папку
	# нового пути, считается испорченной, реестр рабочих пространств не поднимается,
	# и DSH перестаёт открывать любые папки (24.11.2026, cortex_harness → plexus-dsh).
	# Старые сессии остаются при старом пути — это их история.
	say "шаг 5 · сессии DSH остаются на месте (DSH сверяет папку с cwd в заголовке)"
	if [ -d "$DSH_SESSIONS/--$(dsh_slug "$OLD")--" ]; then
		say "   $DSH_SESSIONS/--$(dsh_slug "$OLD")-- не трогаю"
	fi

	# 6. Итог.
	say "итог: дорожек снесено $removed"
	if [ "$kept" -eq 0 ]; then
		say "итог: оставлено 0"
	else
		say "итог: оставлено $kept:"
		for path in $kept_list; do
			say "   ${path#"$OLD"}"
		done
	fi
	say "cd $NEW"
}

# --- самопроверка -----------------------------------------------------------

# Временная папка самопроверки — файловая переменная, а не local:
# к EXIT-ловушке local уже не виден.
ST_TMP=""

clean_up() {
	[ -n "$ST_TMP" ] && rm -rf "$ST_TMP"
}

self_test() {
	local tmp fail="" self
	self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
	# Физический путь: иначе macOS отдаёт «/private/var/…», а папки Claude и
	# DSH строятся по окружению без «/private».
	tmp="$(mktemp -d "${TMPDIR:-/tmp}/rename-selftest.XXXXXX")"
	tmp="$(cd "$tmp" && pwd -P)"
	ST_TMP="$tmp"
	trap 'clean_up' EXIT

	local src="$tmp/Projects/old_repo" dst="$tmp/Projects/new-repo"
	local cp="$tmp/claude-projects" ds="$tmp/dsh-sessions"
	mkdir -p "$src" "$cp" "$ds"

	git -C "$src" init -q -b main .
	git -C "$src" config user.email t@t
	git -C "$src" config user.name t
	printf '.claude/worktrees/\n' >"$src/.gitignore"
	printf 'a\n' >"$src/a.txt"
	git -C "$src" add -A
	git -C "$src" commit -qm base

	# Влитая дорожка: ветка от main, своя правка, после — влита в main.
	# Журнал круга: правка в нём не должна мешать переносу.
	mkdir -p "$src/.claude" && printf '{"круг":"образец"}\n' >"$src/.claude/ledger.jsonl"
	git -C "$src" add -A && git -C "$src" commit -qm ledger
	printf '{"круг":"образец","status":"пауза"}\n' >"$src/.claude/ledger.jsonl"
	git -C "$src" worktree add -q -b merged-b "$src/.claude/worktrees/merged"
	printf 'm\n' >"$src/.claude/worktrees/merged/m.txt"
	git -C "$src/.claude/worktrees/merged" add -A
	git -C "$src/.claude/worktrees/merged" commit -qm merged
	git -C "$src" merge -q --ff-only merged-b
	# Невлитая дорожка: ветка со своим коммитом.
	git -C "$src" worktree add -q -b loose-b "$src/.claude/worktrees/loose"
	printf 'l\n' >"$src/.claude/worktrees/loose/l.txt"
	git -C "$src/.claude/worktrees/loose" add -A
	git -C "$src/.claude/worktrees/loose" commit -qm loose
	# Рабочая копия чистая (после коммита в дорожках ничего не осталось).

	# Фальшивые папки Claude и DSH.
	mkdir -p "$cp/$(claude_slug "$src")" && printf 'mem\n' >"$cp/$(claude_slug "$src")/m.md"
	mkdir -p "$ds/--$(dsh_slug "$src")--" && printf 's\n' >"$ds/--$(dsh_slug "$src")--/s.jsonl"

	local run=(env RENAME_OLD="$src" RENAME_NEW="$dst" CLAUDE_PROJECTS="$cp" DSH_SESSIONS="$ds")

	# Сухой прогон: называет шаги с путями и ничего не меняет.
	local n_before
	n_before="$(git -C "$src" worktree list | wc -l)"
	(cd "$tmp" && "${run[@]}" bash "$self" --dry-run) >"$tmp/dry.log" 2>&1
	local dry_code=$?
	if [ "$dry_code" -ne 0 ]; then
		fail="сухой прогон вернул код $dry_code: $(tail -3 "$tmp/dry.log")"
	elif ! grep -q 'снести влитую дорожку' "$tmp/dry.log" \
		|| ! grep -q 'оставить .*worktrees/loose' "$tmp/dry.log" \
		|| ! grep -q "cd $dst" "$tmp/dry.log"; then
		fail="сухой прогон не назвал шаги (см. сухой прогон)"
	elif [ "$(git -C "$src" worktree list | wc -l)" != "$n_before" ] || [ ! -d "$src" ] || [ -e "$dst" ]; then
		fail="сухой прогон что-то поменял"
	fi
	if [ -n "$fail" ]; then
		[ -f "$tmp/dry.log" ] && err "--- сухой прогон ---" && tail -20 "$tmp/dry.log" >&2
	fi

	if [ -z "$fail" ]; then
		if ! (cd "$tmp" && "${run[@]}" bash "$self" >"$tmp/run.log" 2>&1); then
			fail="перенос упал: $(tail -5 "$tmp/run.log")"
		fi
	fi

	if [ -z "$fail" ]; then
		[ ! -e "$src" ] || fail="старая папка на месте"
		[ -d "$dst" ] || fail="новой папки нет"
	fi
	if [ -z "$fail" ]; then
		[ ! -e "$dst/.claude/worktrees/merged" ] || fail="влитая дорожка не снесена"
		git -C "$dst" rev-parse --verify --quiet refs/heads/merged-b >/dev/null \
			&& fail="ветка влитой дорожки не удалена"
	fi
	if [ -z "$fail" ]; then
		[ -d "$dst/.claude/worktrees/loose" ] || fail="невлитая дорожка пропала"
		git -C "$dst/.claude/worktrees/loose" status --porcelain >/dev/null 2>&1 \
			|| fail="невлитая дорожка не работает после переноса"
	fi
	if [ -z "$fail" ]; then
		[ -f "$cp/$(claude_slug "$dst")/m.md" ] || fail="память Claude не переехала"
		[ ! -e "$cp/$(claude_slug "$src")" ] || fail="старая папка памяти Claude осталась"
		[ -f "$ds/--$(dsh_slug "$src")--/s.jsonl" ] || fail="сессии DSH сдвинулись со старого пути — DSH их не прочтёт"
		[ ! -e "$ds/--$(dsh_slug "$dst")--" ] || fail="появилась папка сессий DSH под новым путём"
	fi
	if [ -z "$fail" ]; then
		local code=0
		(cd "$tmp" && "${run[@]}" bash "$self") >"$tmp/second.log" 2>&1 || code=$?
		[ "$code" -eq 2 ] || fail="повторный запуск вернул код $code, а не 2"
	fi
	if [ -z "$fail" ]; then
		local prunable
		prunable="$(git -C "$dst" worktree list --porcelain | grep -c '^prunable' || true)"
		[ "$prunable" = "0" ] || fail="git worktree list показывает prunable ($prunable)"
	fi

	if [ -n "$fail" ]; then
		err "self-test: $fail"
		[ -f "$tmp/run.log" ] && err "--- журнал переноса ---" && tail -20 "$tmp/run.log" >&2
		exit 1
	fi
	printf 'self-test: ок\n'
}

case "$MODE" in
	--self-test) self_test ;;
	--dry-run) do_work dry ;;
	"") do_work real ;;
	*) refuse "неизвестный аргумент ${MODE}: есть --dry-run, --self-test или без аргумента" ;;
esac
