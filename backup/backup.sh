#!/bin/sh
# Muscle Track backups: the database (pg_dump, custom format) and the media folder (progress photos are sensitive data).
#
#   backup.sh loop            daemon: one backup a day after BACKUP_HOUR (and one soon after the first start)
#   backup.sh once            one backup now
#   backup.sh list            the backups kept
#   backup.sh restore NAME    put a backup back (NAME or "latest"; stop web and scheduler first, see the README)
#
# Each backup is a folder BACKUP_DIR/YYYY-MM-DD_HHMMSS with db.dump, media.tgz and SHA256SUMS. It is built in a hidden
# folder and renamed at the end, so a failed backup never leaves something that looks complete. The result of the last
# run is in BACKUP_DIR/status.json (the app shows a warning to the trainer when it is old or failed).
# Retention: the newest backup of each of the last BACKUP_KEEP_DAILY days, plus the newest of each of the last
# BACKUP_KEEP_WEEKLY Sundays. With BACKUP_RCLONE_REMOTE set, every backup is also copied there (rclone).
set -u
umask 077

BACKUP_DIR="${BACKUP_DIR:-/backups}"
MEDIA_DIR="${MEDIA_DIR:-/media}"
KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"
HOUR="${BACKUP_HOUR:-3}"
REMOTE="${BACKUP_RCLONE_REMOTE:-}"
STATUS="$BACKUP_DIR/status.json"

log() { echo "[backup $(date '+%F %T')] $*" >&2; }

now_iso() { date '+%Y-%m-%dT%H:%M:%S%z'; }

# status.json keeps the last success even when the last run failed
LAST_OK=""
read_last_ok() {
    LAST_OK=""
    [ -f "$STATUS" ] || return 0
    LAST_OK=$(sed -n 's/.*"last_ok": *"\([^"]*\)".*/\1/p' "$STATUS" | head -n 1)
}

write_status() {  # ok name offsite error
    ok="$1"; name="$2"; offsite="$3"; error=$(printf '%s' "$4" | tr -d '"\\' | tr '\n' ' ' | cut -c1-300)
    read_last_ok
    last_ok="$LAST_OK"
    [ "$ok" = true ] && last_ok=$(now_iso)
    tmp="$STATUS.tmp"
    printf '{"ok": %s, "at": "%s", "last_ok": "%s", "name": "%s", "offsite": "%s", "error": "%s"}\n' \
        "$ok" "$(now_iso)" "$last_ok" "$name" "$offsite" "$error" > "$tmp" && chmod 644 "$tmp" && mv "$tmp" "$STATUS"
}

backups() {  # complete backups, newest first
    ls -1 "$BACKUP_DIR" 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$' | sort -r
}

prune() {
    keep=""
    days=""
    sundays=""
    for old in $(backups); do
        day=${old%%_*}
        case " $days " in *" $day "*) continue ;; esac   # only the newest backup of each day counts
        days="$days $day"
        n_days=$(echo $days | wc -w)
        [ "$n_days" -le "$KEEP_DAILY" ] && keep="$keep $old"
        if [ "$(date -d "$day" +%u 2>/dev/null)" = "7" ]; then
            sundays="$sundays $day"
            [ "$(echo $sundays | wc -w)" -le "$KEEP_WEEKLY" ] && keep="$keep $old"
        fi
    done
    for old in $(backups); do
        case " $keep " in *" $old "*) continue ;; esac
        log "removing old backup $old"
        rm -rf "$BACKUP_DIR/$old"
        [ -n "$REMOTE" ] && rclone purge "$REMOTE/$old" >/dev/null 2>&1
    done
    rm -rf "$BACKUP_DIR"/.tmp-* 2>/dev/null
    return 0
}

run_once() {
    mkdir -p "$BACKUP_DIR" || { log "cannot create $BACKUP_DIR"; return 1; }
    name=$(date '+%Y-%m-%d_%H%M%S')
    work="$BACKUP_DIR/.tmp-$name"
    rm -rf "$BACKUP_DIR"/.tmp-* 2>/dev/null
    mkdir -p "$work"
    log "backup $name: database"
    if ! err=$(pg_dump --format=custom --no-owner --file="$work/db.dump" 2>&1); then
        log "pg_dump failed: $err"; rm -rf "$work"; write_status false "$name" off "pg_dump: $err"; return 1
    fi
    # a dump that cannot even be listed is useless: find out now, not when it is needed
    if ! err=$(pg_restore --list "$work/db.dump" 2>&1 >/dev/null); then
        log "the dump is unreadable: $err"; rm -rf "$work"; write_status false "$name" off "dump unreadable: $err"; return 1
    fi
    log "backup $name: media"
    if [ -d "$MEDIA_DIR" ]; then
        if ! err=$(tar czf "$work/media.tgz" -C "$MEDIA_DIR" . 2>&1); then
            log "tar failed: $err"; rm -rf "$work"; write_status false "$name" off "tar: $err"; return 1
        fi
    else
        tar czf "$work/media.tgz" -T /dev/null
    fi
    ( cd "$work" && sha256sum db.dump media.tgz > SHA256SUMS ) || { rm -rf "$work"; write_status false "$name" off "sha256sum failed"; return 1; }
    mv "$work" "$BACKUP_DIR/$name"

    offsite=off
    if [ -n "$REMOTE" ]; then
        log "backup $name: copy to $REMOTE"
        if err=$(rclone copy "$BACKUP_DIR/$name" "$REMOTE/$name" 2>&1); then offsite=ok; else offsite=failed; log "rclone failed: $err"; fi
    fi
    prune
    if [ "$offsite" = failed ]; then
        write_status true "$name" failed "off-server copy failed: $err"
    else
        write_status true "$name" "$offsite" ""
    fi
    log "backup $name done ($(du -sh "$BACKUP_DIR/$name" | cut -f1))"
}

list() {
    for name in $(backups); do echo "$name  $(du -sh "$BACKUP_DIR/$name" | cut -f1)"; done
}

restore() {
    target="${1:-latest}"
    [ "$target" = latest ] && target=$(backups | head -n 1)
    dir="$BACKUP_DIR/$target"
    [ -n "$target" ] && [ -d "$dir" ] || { log "no such backup: ${1:-latest}"; return 1; }
    log "checking $target"
    ( cd "$dir" && sha256sum -c SHA256SUMS ) || { log "the backup is corrupted: not restoring"; return 1; }
    log "restoring the database (existing data is replaced)"
    pg_restore --clean --if-exists --no-owner --dbname="${PGDATABASE:?PGDATABASE is not set}" "$dir/db.dump" \
        || { log "pg_restore reported errors (see above)"; return 1; }
    log "restoring the media folder"
    mkdir -p "$MEDIA_DIR" && tar xzf "$dir/media.tgz" -C "$MEDIA_DIR" || { log "could not extract the media"; return 1; }
    log "restored $target"
}

today_done() { backups | grep -q "^$(date +%F)_"; }

loop() {
    log "daily backups at ${HOUR}h (kept: ${KEEP_DAILY} days + ${KEEP_WEEKLY} Sundays)${REMOTE:+, copy to $REMOTE}"
    last_try=0
    first=1
    while true; do
        now=$(date +%s)
        due=0
        if [ "$first" = 1 ] && [ -z "$(backups | head -n 1)" ]; then due=1; fi   # a new install is protected right away
        if ! today_done && [ "$(date +%H | sed 's/^0//')" -ge "$HOUR" ]; then due=1; fi
        if [ "$due" = 1 ] && [ $((now - last_try)) -ge 3600 ]; then
            last_try=$now
            run_once || log "backup failed; trying again in an hour"
        fi
        first=0
        sleep "${BACKUP_POLL_SECONDS:-300}"
    done
}

case "${1:-loop}" in
    loop) sleep "${BACKUP_START_DELAY:-30}"; loop ;;
    once) run_once ;;
    list) list ;;
    restore) shift; restore "$@" ;;
    *) echo "usage: backup.sh loop|once|list|restore [NAME|latest]" >&2; exit 2 ;;
esac
