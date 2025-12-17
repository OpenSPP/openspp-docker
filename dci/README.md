# DCI Compliance Testing Infrastructure

This directory contains the infrastructure for running DCI (Digital Convergence
Initiative) compliance tests against OpenSPP.

## Overview

OpenSPP implements the DCI protocol for registry interoperability. This testing
infrastructure validates our implementation against the official SPDCI compliance test
suites.

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
├── submodules/                  # Git submodules (SPDCI compliance repos)
│   ├── SR-Mockup-Compliance/
│   ├── DR-Mockup-Compliance/
│   ├── CRVS-Mockup-Compliance/
│   ├── IBR-Mockup-Compliance/
│   └── FR-Mockup-Compliance/
├── config/                      # OpenSPP-specific endpoint configurations
│   ├── helpers-sr.js            # SR server endpoint config
│   ├── helpers-crvs.js          # CRVS mock endpoint config
│   ├── helpers-dr.js            # DR mock endpoint config
│   ├── helpers-ibr.js           # IBR mock endpoint config
│   └── helpers-fr.js            # FR mock endpoint config
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
   - `spp_dci_compliance` (for test fixtures and callback verification)

### Initialize

```bash
# Initialize submodules and directories
invoke dci-init
```

### Run Tests

```bash
# Run all compliance tests
invoke dci-compliance

# Run only SR server tests
invoke dci-compliance --registry=sr

# Run only client tests (CRVS, DR, IBR, FR)
invoke dci-compliance --registry=client

# Run specific registry tests
invoke dci-compliance --registry=dr

# Run with specific Cucumber tags
invoke dci-compliance --tags=@functional

# Rebuild containers before running
invoke dci-compliance --build
```

### Other Commands

```bash
# Start mock registries for manual testing
invoke dci-mocks

# Stop all DCI containers
invoke dci-stop

# View logs
invoke dci-logs
invoke dci-logs --follow
invoke dci-logs --service=crvs_mock

# Update submodules to latest
invoke dci-update
```

## Architecture

### SR Server Testing

```
┌─────────────────┐          ┌─────────────────┐
│ SR Compliance   │  ─────►  │    OpenSPP      │
│ Test Runner     │  tests   │  DCI API        │
└─────────────────┘          └─────────────────┘
```

The SR compliance tests call OpenSPP's DCI API endpoints directly:

- `/dci_api/v1/registry/social/sync/search`
- `/dci_api/v1/registry/social/search` (async)
- `/dci_api/v1/registry/social/subscribe`
- etc.

### Client Testing

```
┌─────────────────┐          ┌─────────────────┐          ┌─────────────────┐
│ Client          │  ─────►  │  Mock Registry  │  ─────►  │    OpenSPP      │
│ Compliance      │  tests   │  (Mockoon)      │ callback │  Callback       │
│ Test Runner     │          │                 │          │  Endpoints      │
└─────────────────┘          └─────────────────┘          └─────────────────┘
```

For client testing:

1. Mock registries (Mockoon) simulate external CRVS/DR/IBR/FR systems
2. Compliance tests validate the mock API responses
3. Mock registries send callbacks to OpenSPP
4. OpenSPP processes callbacks and logs them

### Callback Verification

The `spp_dci_compliance` module provides automatic callback verification:

```bash
# Query received callbacks
curl http://localhost:8069/dci_api/v1/test/callbacks?transaction_id=123

# Get callback statistics
curl http://localhost:8069/dci_api/v1/test/callbacks/stats

# Wait for a specific callback
curl -X POST "http://localhost:8069/dci_api/v1/test/callbacks/wait?transaction_id=123&timeout_seconds=30"
```

## Configuration

### Endpoint Configuration (helpers-\*.js)

Each `helpers-*.js` file configures the test runner for a specific registry type:

```javascript
// helpers-sr.js - Points to OpenSPP SR server
export const localhost = "http://openspp.dci.local:8069/dci_api/v1/";
export const searchEndpoint = "registry/social/sync/search";

// helpers-dr.js - Points to DR mock
export const localhost = "http://dr.registry.mock:3000/";
export const searchEndpoint = "dr/sync/search";
```

### Docker Network

All DCI services communicate via the `dci_compliance` network:

| Service   | Hostname             | Port |
| --------- | -------------------- | ---- |
| OpenSPP   | `openspp.dci.local`  | 8069 |
| CRVS Mock | `crvs.registry.mock` | 3000 |
| DR Mock   | `dr.registry.mock`   | 3000 |
| IBR Mock  | `ibr.registry.mock`  | 3000 |
| FR Mock   | `fr.registry.mock`   | 3000 |

## Test Results

Test results are saved to `dci/results/<registry>/`:

- `*.html` - Human-readable HTML report
- `*.xml` - JUnit XML format (for CI/CD)
- `*.message` - Cucumber messages

## Troubleshooting

### "Connection refused" errors

1. Ensure OpenSPP is running: `docker compose ps`
2. Check the DCI API is enabled: visit
   `http://localhost:19069/dci_api/v1/.well-known/jwks.json`
3. Verify network connectivity:
   `docker compose -f devel.yaml -f docker-compose.dci.yml exec sr_compliance ping openspp.dci.local`

### "404 Not Found" on endpoints

1. Verify `spp_dci_server_social` module is installed
2. Check the FastAPI endpoint is configured: Settings > Technical > FastAPI Endpoints
3. Restart Odoo after module installation

### Mock registry not starting

1. Check the Mockoon JSON file exists: `ls dci/submodules/*/mockoon-*.json`
2. View mock logs: `invoke dci-logs --service=crvs_mock`
3. Try rebuilding: `invoke dci-compliance --build`

### Callback verification fails

1. Ensure `spp_dci_compliance` module is installed
2. Check the callback log: Settings > DCI > Callback Logs
3. Enable dev mode: Set `dci.allow_unsigned_requests = true` in system parameters

## Updating Compliance Tests

The compliance test suites are maintained by SPDCI:

- [SR-Mockup-Compliance](https://github.com/spdci/SR-Mockup-Compliance)
- [DR-Mockup-Compliance](https://github.com/spdci/DR-Mockup-Compliance)
- [CRVS-Mockup-Compliance](https://github.com/spdci/CRVS-Mockup-Compliance)
- [IBR-Mockup-Compliance](https://github.com/spdci/IBR-Mockup-Compliance)
- [FR-Mockup-Compliance](https://github.com/spdci/FR-Mockup-Compliance)

To update to the latest tests:

```bash
invoke dci-update
```

## Contributing

If you find issues with the compliance tests or want to propose improvements:

1. For OpenSPP-specific issues, update the `helpers-*.js` configs
2. For upstream compliance test issues, consider contributing to the SPDCI repos
3. For callback verification improvements, update `spp_dci_compliance` module
