#!/bin/bash
# run-dci-tests.sh - Run DCI compliance tests with proper service orchestration
#
# This script ensures services are started in the correct order for callback
# tests to work. The key issue is that the queue_job worker must be able to
# resolve the sr_compliance hostname, which requires restarting it after
# sr_compliance starts.
#
# Usage:
#   ./scripts/run-dci-tests.sh sr       # Run SR server compliance tests
#   ./scripts/run-dci-tests.sh all      # Run all tests
#   ./scripts/run-dci-tests.sh clean    # Clean up test containers

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

COMPOSE_CMD="docker compose -f devel.yaml -f docker-compose.dci.yml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

wait_for_odoo() {
    log_info "Waiting for Odoo to be ready..."
    local max_attempts=60
    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        if curl -s "http://localhost:19069/dci_api/v1/.well-known/jwks.json" > /dev/null 2>&1; then
            log_info "Odoo is ready!"
            return 0
        fi
        echo -n "."
        sleep 2
        attempt=$((attempt + 1))
    done
    echo ""
    log_error "Odoo did not become ready in time"
    return 1
}

ensure_database_schema() {
    log_info "Ensuring database schema is up to date..."
    # Remove NOT NULL constraint on callback_uri if it exists
    $COMPOSE_CMD exec -T db psql -U odoo -d devel -c \
        "ALTER TABLE IF EXISTS spp_dci_subscription ALTER COLUMN callback_uri DROP NOT NULL;" \
        2>/dev/null || true
}

install_compliance_module() {
    log_info "Checking spp_dci_compliance module..."

    # Check if module is installed, if not install it
    MODULE_STATE=$($COMPOSE_CMD exec -T db psql -U odoo -d devel -t -c \
        "SELECT state FROM ir_module_module WHERE name = 'spp_dci_compliance';" 2>/dev/null | tr -d ' \n')

    if [ "$MODULE_STATE" = "installed" ]; then
        log_info "spp_dci_compliance module already installed"
        # Re-run post_init_hook to ensure config is set
        $COMPOSE_CMD exec -T odoo odoo shell -d devel --no-http << 'PYEOF' 2>/dev/null || true
from odoo.addons.spp_dci_compliance import _post_init_hook
_post_init_hook(self.env)
self.env.cr.commit()
print("Re-applied DCI compliance configuration")
PYEOF
    else
        log_info "Installing spp_dci_compliance module..."
        $COMPOSE_CMD exec -T odoo odoo -d devel -i spp_dci_compliance --stop-after-init 2>&1 | tail -20
        log_info "spp_dci_compliance module installed"
    fi
}

run_sr_tests() {
    log_info "Starting SR Server Compliance Tests..."

    # Step 1: Ensure Odoo and DB are running with DCI network
    log_info "Starting Odoo with DCI network..."
    $COMPOSE_CMD up -d odoo db odoo_proxy

    # Wait for Odoo
    wait_for_odoo

    # Ensure database schema
    ensure_database_schema

    # Install/configure spp_dci_compliance module (sets up test config and data)
    install_compliance_module

    # Step 2: Restart Odoo to pick up new config
    log_info "Restarting Odoo to pick up configuration..."
    $COMPOSE_CMD restart odoo odoo_queue_worker
    sleep 5
    wait_for_odoo

    # Step 3: Start sr_compliance (starts callback server)
    log_info "Starting callback server (sr_compliance)..."
    $COMPOSE_CMD --profile sr-test up -d sr_compliance

    # Wait for callback server to start
    sleep 3

    # Step 4: Restart queue_worker so it can resolve sr_compliance hostname
    log_info "Restarting queue worker to refresh DNS..."
    $COMPOSE_CMD restart odoo_queue_worker

    # Wait for queue worker to be ready
    sleep 5

    # Step 5: Wait for tests to complete (with timeout)
    log_info "Running tests (waiting for completion)..."

    # Wait for the container to finish (with 5 minute timeout)
    local timeout=300
    local elapsed=0
    while [ $elapsed -lt $timeout ]; do
        # Check if container is still running
        if ! docker ps --format '{{.Names}}' | grep -q "sr_compliance"; then
            break
        fi
        sleep 5
        elapsed=$((elapsed + 5))

        # Show progress every 30 seconds
        if [ $((elapsed % 30)) -eq 0 ]; then
            echo -n "."
        fi
    done
    echo ""

    # Get test output
    log_info "Test output:"
    $COMPOSE_CMD logs sr_compliance 2>&1 | tail -100

    # Get exit code
    EXIT_CODE=$($COMPOSE_CMD ps -a -q sr_compliance | xargs docker inspect -f '{{.State.ExitCode}}' 2>/dev/null || echo "1")

    if [ "$EXIT_CODE" = "0" ]; then
        log_info "SR Compliance Tests PASSED!"
    else
        log_error "SR Compliance Tests FAILED (exit code: $EXIT_CODE)"
    fi

    return "$EXIT_CODE"
}

run_all_tests() {
    log_info "Running all DCI compliance tests..."
    run_sr_tests
    # Add other test profiles here as needed
}

clean_up() {
    log_info "Cleaning up test containers..."
    $COMPOSE_CMD --profile sr-test --profile all-tests down --remove-orphans
    log_info "Cleanup complete"
}

show_help() {
    cat << EOF
DCI Compliance Test Runner

Usage: $0 <command>

Commands:
    sr      Run SR (Social Registry) server compliance tests
    all     Run all DCI compliance tests
    clean   Clean up test containers
    help    Show this help message

Environment Variables:
    DCI_AUTH_TOKEN           Bearer token for API authentication
    CALLBACK_WAIT_MS         Callback wait timeout in milliseconds (default: 45000)
    CUCUMBER_STEP_TIMEOUT_MS Cucumber step timeout in milliseconds (default: 60000)

Examples:
    $0 sr                    # Run SR tests with defaults
    CALLBACK_WAIT_MS=60000 $0 sr   # Run SR tests with longer callback timeout

Notes:
    - The script installs spp_dci_compliance module if not already installed
    - The module sets up all required DCI config parameters and test data
    - After database reset, run this script to restore test configuration
EOF
}

case "${1:-help}" in
    sr)
        run_sr_tests
        ;;
    all)
        run_all_tests
        ;;
    clean)
        clean_up
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
