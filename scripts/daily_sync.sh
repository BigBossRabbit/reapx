#!/bin/bash
# daily_sync.sh
# Orchestrates the full ReapX workflow: harvest X bookmarks -> categorize -> generate skills.
# ReapX turns your X bookmarks into reusable AI skills.

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
LOG_FILE="$SCRIPT_DIR/../logs/reapx-$(date +%Y%m%d).log"
DATA_DIR="$SCRIPT_DIR/../data"

# Parse --source flag: default = bookmarks (ReapX's core); github-stars kept for compat
SOURCE="bookmarks"
for arg in "$@"; do
    case "$arg" in
        --source=*) SOURCE="${arg#*=}" ;;
        --source) SOURCE="bookmarks" ;;
    esac
done

# Ensure directories exist
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$DATA_DIR"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "Starting ReapX daily sync (source: $SOURCE)"

# Step 1: Fetch source data
if [ "$SOURCE" = "bookmarks" ]; then
    log "Step 1: Harvesting X bookmarks..."
    "$SCRIPT_DIR/fetch_x_bookmarks.py" 2>&1 | tee -a "$LOG_FILE"
    log "Step 1b: Mapping bookmarks to repo schema..."
    "$SCRIPT_DIR/map_bookmarks_to_repos.py" 2>&1 | tee -a "$LOG_FILE"
else
    log "Step 1: Fetching starred repositories from GitHub..."
    "$SCRIPT_DIR/fetch_starred_repos.sh" 2>&1 | tee -a "$LOG_FILE"
fi

# Step 2: Categorize repositories
log "Step 2: Categorizing by domain..."
INPUT_FILE="$DATA_DIR/starred_repos.json"
OUTPUT_FILE="$DATA_DIR/categorized_repos.json"
"$SCRIPT_DIR/categorize_repos.py" "$INPUT_FILE" "$OUTPUT_FILE" 2>&1 | tee -a "$LOG_FILE"

# Step 3: Generate skills
log "Step 3: Generating Hermes skills from categorized sources..."
OUTPUT_DIR="$SCRIPT_DIR/../generated_skills"
"$SCRIPT_DIR/generate_skills.py" "$OUTPUT_FILE" "$OUTPUT_DIR" 2>&1 | tee -a "$LOG_FILE"

# Step 4: Summary
log "Step 4: Generating summary report..."
REPO_COUNT=$(jq length "$DATA_DIR/starred_repos.json" 2>/dev/null || echo "0")
CATEGORY_COUNT=$(jq '.categories | length' "$DATA_DIR/categorized_repos.json" 2>/dev/null || echo "0")
SKILL_COUNT=$(find "$OUTPUT_DIR" -name "SKILL.md" -type f | wc -l)

log "Daily sync complete!"
log "  - Harvested: $REPO_COUNT bookmarks"
log "  - Categorized into: $CATEGORY_COUNT domains"
log "  - Generated: $SKILL_COUNT Hermes skills"
log "  - Skills available in: $OUTPUT_DIR"

log "ReapX daily sync finished"
