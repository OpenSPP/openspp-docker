#!/bin/bash
# Script to find and fix _sql_constraints in Odoo addons
# This script searches for _sql_constraints and helps convert them to model.Constraint

set -e

SEARCH_PATH="${1:-/opt/odoo/custom/src}"
DRY_RUN="${2:-false}"

echo "Searching for _sql_constraints in: $SEARCH_PATH"

# Find all Python files with _sql_constraints
files=$(find "$SEARCH_PATH" -type f -name "*.py" -exec grep -l "_sql_constraints" {} \; 2>/dev/null || true)

if [ -z "$files" ]; then
    echo "No files with _sql_constraints found in $SEARCH_PATH"
    echo "Trying alternative locations..."
    # Try common Odoo paths
    for alt_path in "/workspace/odoo/custom/src" "/opt/odoo" "/usr/lib/python3*/dist-packages/odoo/addons"; do
        if [ -d "$alt_path" ]; then
            files=$(find "$alt_path" -type f -name "*.py" -exec grep -l "_sql_constraints" {} \; 2>/dev/null || true)
            if [ -n "$files" ]; then
                echo "Found files in: $alt_path"
                break
            fi
        fi
    done
fi

if [ -z "$files" ]; then
    echo "No files found. The addons may need to be pulled/cloned first."
    echo "Run this script from within a Docker container or after cloning addon repos."
    exit 0
fi

echo "Found files with _sql_constraints:"
echo "$files" | while read -r file; do
    echo "  - $file"
done

echo ""
echo "To fix these files, run:"
echo "  python3 /workspace/fix_sql_constraints.py --path $SEARCH_PATH"
