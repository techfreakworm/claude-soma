#!/usr/bin/env bash
# scripts/migrate-staging.sh
#
# Idempotent migration helper: moves /tmp/social-engagement/* into
# /var/lib/claude-soma/staging/ (the new canonical home for markserv-served
# documents).
#
# First run:  moves all items (preserving symlinks), removes the source dir,
#             and prints a summary line.
# Second run: source dir is gone → prints "nothing to migrate" and exits 0.
#
# Symlinks are preserved because rsync -a copies symlinks as symlinks (it
# does NOT follow them).  This matters because relay docs are symlinks.
#
# Override defaults via environment variables (used by tests):
#   SOMA_STAGING_SRC   — source directory  (default: /tmp/social-engagement)
#   SOMA_STAGING_DEST  — destination dir   (default: /var/lib/claude-soma/staging)

set -euo pipefail

SRC=${SOMA_STAGING_SRC:-/tmp/social-engagement}
DEST=${SOMA_STAGING_DEST:-/var/lib/claude-soma/staging}

# If source does not exist or is empty, nothing to do.
if [ ! -d "$SRC" ] || [ -z "$(ls -A "$SRC" 2>/dev/null)" ]; then
    echo "migrate-staging: nothing to migrate"
    exit 0
fi

# Create destination directory if it does not exist.
mkdir -p "$DEST"

# Count top-level items before moving (for the summary).
count=$(find "$SRC" -maxdepth 1 -mindepth 1 | wc -l | tr -d ' ')

# Migrate, preserving symlinks (-a = archive; includes -l = keep symlinks).
rsync -a "$SRC/" "$DEST/"

# Remove the source directory so a second run reports nothing to migrate.
rm -rf "$SRC"

echo "migrate-staging: moved $count item(s) from $SRC to $DEST"
