# DCI Compliance Testing Infrastructure

This directory contains the infrastructure for running DCI (Digital Convergence
Initiative) compliance tests against OpenSPP.

## Overview

OpenSPP implements the DCI protocol for registry interoperability. This testing
infrastructure validates our implementation against the official SPDCI compliance test
suite.

### Testing Scenarios

| Scenario        | OpenSPP Role | What's Tested                                              |
| --------------- | ------------ | ---------------------------------------------------------- |
| **SR Server**   | Server       | OpenSPP serves Social Registry data to external clients    |
| **CRVS Client** | Client       | OpenSPP queries external CRVS registries                   |
| **DR Client**   | Client       | OpenSPP queries external Disability Registries             |
| **IBR Client**  | Client       | OpenSPP queries external Integrated Beneficiary Registries |
| **FR Client**   | Client       | OpenSPP queries external Functional/Farmer Registries      |

## Directory Structure

```
dci/
├── README.md                    # This file
├── submodules/
│   ├── spdci-compliance/        # Unified SPDCI compliance test suite
│   ├── spdci-api-standards/     # API specifications
│   └── spdci-schemas/           # JSON schemas
├── config/                      # Legacy configs (deprecated)
└── results/                     # Test output (gitignored)
    ├── sr/
    ├── crvs/
    ├── dr/
    ├── ibr/
    └── fr/
```

## Quick Start

### Prerequisites

1. Docker and docker-compose installed
2. OpenSPP running with DCI modules installed:
   - `spp_dci`
   - `spp_dci_server`
   - `spp_dci_server_social`
   - `spp_dci_compliance` (for test configuration and data)

### Run SR Server Compliance Tests

The recommended way to run tests is using the invoke task:

```bash
# From project root (openspp-odoo-19-migration/)

# Run SR server compliance tests (28 tests)
invoke dci-compliance --registry=sr

# Other options:
invoke dci-compliance --tags=@smoke     # Smoke tests only
invoke dci-compliance -v                # Verbose output
```

The invoke task automatically handles:

1. Initializing git submodules (spdci-compliance test suite)
2. Checking if `spp_dci_compliance` module is installed (installs if needed)
3. Starting sr_compliance container with proper network aliases
4. Restarting queue_worker so it can resolve sr_compliance hostname
5. Waiting for queue_worker to be ready before running tests
6. Following test output and cleaning up

**Note:** If you've just reset the database with `invoke resetdb`, you may need to
ensure the base DCI modules are installed first:

```bash
docker compose -f devel.yaml run --rm odoo odoo -d devel -i spp_dci_server_social --stop-after-init
```

## Architecture

### SR Server Testing

```
┌─────────────────┐          ┌─────────────────┐
│ SR Compliance   │  ─────►  │    OpenSPP      │
│ Test Runner     │  tests   │  DCI API        │
│ (spdci-         │          │                 │
│  compliance)    │  ◄─────  │  Callbacks      │
└─────────────────┘ callback └─────────────────┘
```

The SR compliance tests:

1. Call OpenSPP's DCI API endpoints
2. Validate responses match SPDCI specification
3. For async operations, receive callbacks from OpenSPP

Endpoints tested:

- `POST /dci_api/v1/social/registry/sync/search` - Synchronous search
- `POST /dci_api/v1/social/registry/search` - Async search
- `POST /dci_api/v1/social/registry/subscribe` - Subscribe to events
- `POST /dci_api/v1/social/registry/unsubscribe` - Unsubscribe
- `POST /dci_api/v1/social/registry/txn/status` - Transaction status
- `POST /dci_api/v1/social/registry/sync/txn/status` - Sync txn status

### Client Testing (Mock Registries)

```
┌─────────────────┐          ┌─────────────────┐
│ Client          │  ─────►  │  Mock Registry  │
│ Compliance      │  tests   │  (Mockoon)      │
│ Test Runner     │          │                 │
└─────────────────┘          └─────────────────┘
```

For client testing, mock registries simulate external systems.

## Configuration

### Required Odoo System Parameters

The `spp_dci_compliance` module automatically configures these parameters on
installation. The test script will install this module if not already installed.

| Parameter                         | Value                           | Description                               |
| --------------------------------- | ------------------------------- | ----------------------------------------- |
| `dci.api_tokens`                  | `compliance-test-api-key-12345` | Accepted Bearer tokens (comma-separated)  |
| `dci.allow_unsigned_requests`     | `true`                          | Skip signature verification (dev only!)   |
| `dci.allow_http_callbacks`        | `true`                          | Allow HTTP callback URLs (not just HTTPS) |
| `dci.allow_internal_callback_ips` | `true`                          | Allow callbacks to Docker internal IPs    |

The module also creates:

- A test sender with `sender_id = 'test-client'`
- Test individuals with identifiers for search testing

### Environment Variables

| Variable                   | Default                         | Description                         |
| -------------------------- | ------------------------------- | ----------------------------------- |
| `DCI_AUTH_TOKEN`           | `compliance-test-api-key-12345` | Bearer token for API auth           |
| `CALLBACK_WAIT_MS`         | `45000`                         | How long to wait for callbacks (ms) |
| `CUCUMBER_STEP_TIMEOUT_MS` | `60000`                         | Cucumber step timeout (ms)          |

### Docker Network

All DCI services communicate via the `dci_compliance` network:

| Service   | Hostname             | Port  |
| --------- | -------------------- | ----- |
| OpenSPP   | `openspp.dci.local`  | 8069  |
| SR Tests  | `sr_compliance`      | 19999 |
| CRVS Mock | `crvs.registry.mock` | 3000  |
| DR Mock   | `dr.registry.mock`   | 3000  |
| IBR Mock  | `ibr.registry.mock`  | 3000  |
| FR Mock   | `fr.registry.mock`   | 3000  |

## Test Results

Test results are saved to `dci/results/<registry>/`.

Current SR Server compliance: **28/28 tests passing**

## Troubleshooting

### After database reset (tests fail with 401/403)

After running `invoke resetdb`, the DCI configuration is lost. Reinstall the compliance
module:

```bash
docker compose -f devel.yaml run --rm odoo odoo -d devel -i spp_dci_compliance --stop-after-init
```

Or include `spp_dci_compliance` in your resetdb modules to ensure it's always installed.

### "Connection refused" errors

1. Ensure OpenSPP is running: `docker compose -f devel.yaml ps`
2. Check the DCI API is enabled: visit
   `http://localhost:19069/dci_api/v1/.well-known/jwks.json`

### "404 Not Found" on endpoints

1. Verify `spp_dci_server_social` module is installed
2. Check the FastAPI endpoint is configured: Settings > Technical > FastAPI Endpoints
3. Restart Odoo after module installation

### Callback tests failing (timing out)

The invoke task handles callback orchestration automatically. If callbacks still fail:

1. Verify the sr_compliance container has the `sr_compliance` network alias
2. Verify queue_worker is on the `dci_compliance` network:
   ```bash
   docker network inspect openspp-odoo-19-migration_dci_compliance | grep queue_worker
   ```
3. Check callback URL validation settings are correct:
   - `dci.allow_http_callbacks` = `true`
   - `dci.allow_internal_callback_ips` = `true`

### Database constraint errors

If you see `NOT NULL constraint` errors on `callback_uri`:

```bash
docker compose -f devel.yaml exec db psql -U odoo -d devel -c \
  "ALTER TABLE spp_dci_subscription ALTER COLUMN callback_uri DROP NOT NULL;"
```

## Updating Compliance Tests

The compliance test suite is maintained by OpenSPP:

- [spdci-compliance](https://github.com/openspp/spdci-compliance)

To update:

```bash
cd dci/submodules/spdci-compliance
git pull origin main
```
