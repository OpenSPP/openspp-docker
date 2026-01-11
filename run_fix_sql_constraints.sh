#!/bin/bash
# Wrapper script to fix _sql_constraints warnings
# This can be run from host or inside Docker container

set -e

# Default paths to check
PATHS=(
    "/opt/odoo/custom/src"
    "/workspace/odoo/custom/src"
    "/workspace/odoo/custom/src/openspp_modules"
    "/workspace/odoo/custom/src/muk_addons"
)

DRY_RUN="${1:-}"

echo "Searching for addon directories with _sql_constraints..."

FOUND_PATH=""
for path in "${PATHS[@]}"; do
    if [ -d "$path" ]; then
        # Check if this path has Python files with _sql_constraints
        if find "$path" -name "*.py" -exec grep -l "_sql_constraints" {} \; 2>/dev/null | head -1 | grep -q .; then
            FOUND_PATH="$path"
            echo "Found addons with _sql_constraints in: $path"
            break
        fi
    fi
done

if [ -z "$FOUND_PATH" ]; then
    echo "No addon directories with _sql_constraints found in:"
    for path in "${PATHS[@]}"; do
        echo "  - $path"
    done
    echo ""
    echo "The addons may need to be cloned first, or you may need to run this"
    echo "from inside a Docker container where addons are available."
    echo ""
    echo "To run manually:"
    echo "  python3 /workspace/migrate_sql_constraints.py --path <addon_path>"
    exit 0
fi

if [ "$DRY_RUN" = "--dry-run" ] || [ "$DRY_RUN" = "-n" ]; then
    echo "Running in dry-run mode..."
    python3 /workspace/migrate_sql_constraints.py --path "$FOUND_PATH" --dry-run
else
    echo "Fixing _sql_constraints in: $FOUND_PATH"
    python3 /workspace/migrate_sql_constraints.py --path "$FOUND_PATH"
    echo ""
    echo "Done! Please test your changes and restart Odoo to verify warnings are gone."
fi
