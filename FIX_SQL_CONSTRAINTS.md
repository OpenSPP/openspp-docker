# Fixing _sql_constraints Warnings in Odoo 19

## Problem
Odoo 19 has deprecated the `_sql_constraints` attribute and requires using `model.Constraint` decorators instead.

**Warning message:**
```
Model attribute '_sql_constraints' is no longer supported, please define model.Constraint on the model.
```

## Solution

Two scripts have been created to help fix this issue:

### 1. `migrate_sql_constraints.py` (Recommended)
A simpler regex-based script that finds and converts `_sql_constraints` patterns.

### 2. `fix_sql_constraints.py`
A more complex AST-based script with additional features.

## Usage

### When addons are available (inside Docker container or after cloning):

```bash
# Dry run to see what would be fixed
python3 /workspace/migrate_sql_constraints.py --path /opt/odoo/custom/src --dry-run

# Actually fix the files
python3 /workspace/migrate_sql_constraints.py --path /opt/odoo/custom/src
```

### From host machine (if addons are mounted):

```bash
python3 /workspace/migrate_sql_constraints.py --path /workspace/odoo/custom/src
```

## Conversion Pattern

### Old Format (Odoo 18 and earlier):
```python
_sql_constraints = [
    ('name_unique', 'UNIQUE(name)', 'Name must be unique!'),
    ('code_company_unique', 'UNIQUE(code, company_id)', 'Code must be unique per company!'),
]
```

### New Format (Odoo 19):
```python
from odoo import models
from odoo.exceptions import ValidationError

@models.constraint('name')
def _check_name_unique(self):
    for record in self:
        if self.search_count([
            ('name', '=', record.name),
            ('id', '!=', record.id)
        ]) > 0:
            raise ValidationError('Name must be unique!')

@models.constraint('code', 'company_id')
def _check_code_company_unique(self):
    for record in self:
        if self.search_count([
            ('code', '=', record.code),
            ('company_id', '=', record.company_id.id),
            ('id', '!=', record.id)
        ]) > 0:
            raise ValidationError('Code must be unique per company!')
```

## Finding Files Manually

To find files with `_sql_constraints`:

```bash
find /opt/odoo/custom/src -name "*.py" -exec grep -l "_sql_constraints" {} \;
```

## Notes

- The scripts automatically add required imports (`from odoo import models` and `from odoo.exceptions import ValidationError`)
- CHECK constraints may need manual conversion as SQL expressions need to be translated to Python
- Always test after applying fixes
- Make sure to commit changes to the appropriate addon repositories

## Troubleshooting

If scripts don't find files:
1. Ensure addon repositories are cloned (check `/opt/odoo/custom/src/` or `/workspace/odoo/custom/src/`)
2. Run from inside the Docker container if addons are only available there
3. Check that the path includes the addon directories
