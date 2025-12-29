[![Doodba deployment](https://img.shields.io/badge/deployment-doodba-informational)](https://github.com/Tecnativa/doodba)
[![Last template update](https://img.shields.io/badge/last%20template%20update-v9.0.5-informational)](https://github.com/Tecnativa/doodba-copier-template/tree/v9.0.5)
[![Odoo](https://img.shields.io/badge/odoo-v19.0-a3478a)](https://github.com/odoo/odoo/tree/19.0)
[![Deployment data](https://img.shields.io/badge/%F0%9F%8C%90%20prod-demo.openspp.org-green)](http://demo.openspp.org)
[![Deployment data](https://img.shields.io/badge/%E2%9A%92%20demo-demo--test.openspp.org-yellow)](http://demo-test.openspp.org)
[![LGPL-3.0-or-later license](https://img.shields.io/badge/license-LGPL--3.0--or--later-success})](LICENSE)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://pre-commit.com/)

# OpenSPP Docker Deployment

This repository contains a [Doodba](https://github.com/Tecnativa/doodba)-based Docker deployment for [OpenSPP](https://openspp.org) - the Open Source Social Protection Platform.

## What is OpenSPP?

OpenSPP is an open-source, modular, and highly interoperable digital platform designed to support social protection programs. It helps governments and humanitarian organizations digitalize their social protection systems efficiently and cost-effectively.

The platform offers four main products:
- **SP-MIS**: A comprehensive Social Protection Management Information System
- **Social Registry**: For storing and managing data for social protection planning and administration
- **Farmer Registry**: For managing farm holding and farm owner data
- **Disability Registry**: For recording and managing information about individuals with disabilities

OpenSPP is a Digital Public Good built on more than 60 open-source modules and leverages other open-source projects including OpenG2P, MOSIP, OpenCRVS, Odoo, Payment Hub EE, and more.

## Quick Start

The official quick start guide is located [here](https://docs.openspp.org/getting_started/installation_guide.html).

### Requirements

- Docker and Docker Compose
- Git
- Python 3.13 with pip
- Invoke (`pip install invoke`)
- Git-aggregator (`pip install git-aggregator`)
- pre-commit (`pip install pre-commit`)

### Setup

1. Clone this repository:
   ```bash
   git clone https://github.com/OpenSPP/openspp-docker.git
   cd openspp-docker
   ```

2. Set up the development environment:
   ```bash
   invoke develop
   ```

3. Pull and build images:
   ```bash
   invoke img-pull
   invoke img-build
   ```

4. Download code repositories:
   ```bash
   invoke git-aggregate
   ```

   Note: If you encounter SSH issues with git-aggregate, you can try:
   ```bash
   invoke git-aggregate-host
   ```

5. Create a fresh database:
   ```bash
   invoke resetdb
   ```

6. Start the environment:
   ```bash
   invoke start
   ```

7. Access OpenSPP at http://localhost:17069

### Configuration

Adjust the openspp-modules branch in `odoo/custom/src/repos.yaml` to the desired release.
Currently, the OpenSPP Batanes release (17.0.1.2.1) is used.

## Common Operations

- Stop the environment: `invoke stop`
- Restart Odoo: `invoke restart`
- View logs: `invoke logs`
- Install modules: `invoke install --modules=module1,module2`
- Update modules: `invoke update`
- Run tests: `invoke test --modules=module1,module2`
- Create a database snapshot: `invoke snapshot`
- Restore a snapshot: `invoke restore-snapshot`

## Translations

### Managing Translations in OpenSPP

To extract translatable strings and generate/update translation files (.pot and .po):

1. **First, install the modules** you want to translate
2. **Then extract translation files** using the update_pot task:
   ```bash
   invoke update-pot --modules="module1,module2,module3" --database=devel --msgmerge
   ```

### Translation Options

The `update-pot` task supports several options:

- `--addons-dir`: Directory containing the addons (default: odoo/custom/src)
- `--modules`: Comma-separated list of modules to process
- `--database`: Database name to use (default: devel)
- `--no-fuzzy`: Disable fuzzy matching when merging translations
- `--update-po`: Update .po files after generating .pot files
- `--lang`: Language code for PO files to update/create
- `--force`: Force update of existing PO files
- `--msgmerge`: Run msgmerge if POT file is created/updated
- `--create-i18n`: Create i18n directories if they don't exist (default: True)
- `--debug`: Enable more verbose logging

### Common Translation Workflows

1. **Generate POT files for specific modules**:
   ```bash
   invoke update-pot --modules="spp_base,spp_programs,spp_registry_base" --msgmerge
   ```

2. **Update existing PO files for all languages**:
   ```bash
   invoke update-pot --modules="spp_base" --update-po --msgmerge
   ```

3. **Create or update a specific language translation**:
   ```bash
   invoke update-pot --modules="spp_programs" --update-po --lang=fr_FR
   ```

**Note**: Modules must be installed in the database before translations can be extracted, as the extraction process uses Odoo's translation export mechanism which requires access to installed modules.

### Translation Best Practices

When writing code that includes translatable strings, follow these best practices to avoid extraction errors:

1. **Never use f-strings within translation calls**:
   ```python
   # WRONG - will cause extraction errors
   _(f"Hello {user.name}")

   # CORRECT - use positional arguments with %s
   _("Hello %s") % user.name
   ```

2. **Prefer positional arguments over named placeholders**:
   ```python
   # Less preferred
   _("Hello %(name)s") % {"name": user.name}

   # Preferred
   _("Hello %s") % user.name
   ```

3. **Keep the translatable string and its variables separate**:
   ```python
   # WRONG - string formatting happens before translation
   _("Hello " + user.name)

   # CORRECT - translate the template, then insert variables
   _("Hello %s") % user.name
   ```

4. **Handle plurals correctly**:
   ```python
   # Use Odoo's ngettext for pluralization
   ngettext(
       "You have %d message",
       "You have %d messages",
       count
   ) % count
   ```

Following these practices ensures that translation extraction works correctly and translators can work with complete sentences.

## Services

The deployment includes the following services:

- **Odoo**: The application server running OpenSPP modules
- **PostgreSQL**: Database server with PostGIS extensions
- **Mailhog**: Fake SMTP server for email testing (http://localhost:17025)
- **pgweb**: Web-based PostgreSQL browser (http://localhost:17081)
- **debugger**: Web-based debugger interface (http://localhost:17984)

## Documentation

For more information about OpenSPP, please refer to:
- [OpenSPP Documentation](https://docs.openspp.org/)
- [OpenSPP GitHub](https://github.com/OpenSPP)

For more information about Doodba, check these resources:
- [General Doodba docs](https://github.com/Tecnativa/doodba)
- [Doodba copier template docs](https://github.com/Tecnativa/doodba-copier-template)
- [Doodba QA docs](https://github.com/Tecnativa/doodba-qa)

## Contributing

Contributions to OpenSPP are welcome! Please refer to the [OpenSPP Contributor Guidelines](https://github.com/OpenSPP/openspp-modules/blob/17.0/CONTRIBUTING.md) for more information.

## Credits

This project is maintained by the OpenSPP community.
