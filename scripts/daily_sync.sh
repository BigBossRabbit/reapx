#!/bin/bash
# daily_sync.sh
# Orchestrates the full ReapX workflow: harvest X bookmarks -> categorize -> generate skills.
# INCREMENTAL: diffs the mapped set against a persistent seen-ledger so only
# NEW bookmarks are categorized, assessed and turned into skills.

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
LOG_FILE="$SCRIPT_DIR/../logs/reapx-$(date +%Y%m%d).log"
DATA_DIR="$SCRIPT_DIR/../data"
LEDGER="$DATA_DIR/ledger.json"
NEW_FILE="$DATA_DIR/new_repos.json"
OPPORTUNITIES="$DATA_DIR/opportunities.json"

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

log "Starting ReapX daily sync (source: $SOURCE, incremental)"

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

# Step 2: Diff against the seen-ledger -> only NEW items advance
log "Step 2: Diffing against seen-ledger (only NEW items processed)..."
INPUT_FILE="$DATA_DIR/starred_repos.json"
python3 "$SCRIPT_DIR/ledger.py" diff "$INPUT_FILE" "$LEDGER" "$NEW_FILE" 2>&1 | tee -a "$LOG_FILE"

NEW_COUNT=$(jq length "$NEW_FILE" 2>/dev/null || echo "0")
if [ "$NEW_COUNT" -eq 0 ]; then
    log "No new items since last run. Skipping categorize/assess/generate."
    log "ReapX daily sync finished (0 new)"
    exit 0
fi

# Step 3: Categorize ONLY new items
log "Step 3: Categorizing $NEW_COUNT new items by domain..."
OUTPUT_FILE="$DATA_DIR/categorized_repos.json"
"$SCRIPT_DIR/categorize_repos.py" "$NEW_FILE" "$OUTPUT_FILE" 2>&1 | tee -a "$LOG_FILE"

# Step 4: Two-track assessment (own-makeup integration + business opportunity)
log "Step 4: Assessing new items for integration + business opportunity..."
python3 "$SCRIPT_DIR/assess_repos.py" "$NEW_FILE" "$OPPORTUNITIES" 2>&1 | tee -a "$LOG_FILE"

# Step 5: Generate skills from new categorized items
log "Step 5: Generating Hermes skills from new categorized sources..."
OUTPUT_DIR="$SCRIPT_DIR/../generated_skills"
"$SCRIPT_DIR/generate_skills.py" "$OUTPUT_FILE" "$OUTPUT_DIR" 2>&1 | tee -a "$LOG_FILE"

# Step 6: Record new items into the ledger (only after a successful run)
log "Step 6: Recording $NEW_COUNT new items into seen-ledger..."
python3 "$SCRIPT_DIR/ledger.py" update "$LEDGER" "$NEW_FILE" --source x-bookmarks 2>&1 | tee -a "$LOG_FILE"

# Step 7: Summary
log "Step 7: Generating summary report..."
CATEGORY_COUNT=$(jq '.categories | length' "$OUTPUT_FILE" 2>/dev/null || echo "0")
SKILL_COUNT=$(find "$OUTPUT_DIR" -name "SKILL.md" -type f | wc -l)
OPP_COUNT=$(jq '.counts.opportunities' "$OPPORTUNITIES" 2>/dev/null || echo "0")

log "Daily sync complete!"
log "  - New this run: $NEW_COUNT items"
log "  - Categorized into: $CATEGORY_COUNT domains"
log "  - Business opportunities: $OPP_COUNT"
log "  - Generated: $SKILL_COUNT Hermes skills (cumulative)"
log "  - Skills available in: $OUTPUT_DIR"

log "ReapX daily sync finished"
