#!/usr/bin/env python3
"""
Simple script to migrate _sql_constraints to model.Constraint decorators in Odoo 19.

This script uses regex to find and convert _sql_constraints patterns.
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple


def parse_constraints(content: str) -> List[dict]:
    """Extract _sql_constraints from Python code."""
    constraints = []
    
    # Pattern to match _sql_constraints = [ ... ]
    pattern = r'_sql_constraints\s*=\s*\[(.*?)\]'
    
    # Match multiline constraints
    matches = re.finditer(pattern, content, re.DOTALL)
    
    for match in matches:
        constraints_text = match.group(1)
        # Extract individual constraint tuples: ('name', 'SQL', 'message')
        constraint_pattern = r"\(['\"]([^'\"]+)['\"],\s*['\"]([^'\"]+)['\"],\s*['\"]([^'\"]*)['\"]?\)"
        constraint_matches = re.finditer(constraint_pattern, constraints_text)
        
        for cm in constraint_matches:
            constraints.append({
                'name': cm.group(1),
                'sql': cm.group(2),
                'message': cm.group(3) if len(cm.groups()) > 2 else '',
                'start': match.start(),
                'end': match.end(),
            })
    
    return constraints


def parse_sql_constraint(sql: str) -> dict:
    """Parse SQL constraint to extract type and fields."""
    sql = sql.strip().upper()
    
    # UNIQUE constraint
    unique_match = re.match(r'UNIQUE\s*\((.*?)\)', sql, re.IGNORECASE)
    if unique_match:
        fields_str = unique_match.group(1)
        # Split by comma and clean up
        fields = [f.strip().strip('"\'') for f in fields_str.split(',')]
        return {'type': 'unique', 'fields': fields}
    
    # CHECK constraint  
    check_match = re.match(r'CHECK\s*\((.*?)\)', sql, re.IGNORECASE | re.DOTALL)
    if check_match:
        return {'type': 'check', 'expression': check_match.group(1)}
    
    return {'type': 'unknown', 'sql': sql}


def generate_constraint_method(constraint: dict, parsed: dict, indent: str = "    ") -> str:
    """Generate Python method code for the constraint."""
    constraint_name = constraint['name']
    constraint_message = constraint['message'] or f"Constraint violation: {constraint_name}"
    
    if parsed['type'] == 'unique':
        fields = parsed['fields']
        field_params = ', '.join(f"'{f}'" for f in fields)
        
        # Build domain parts
        domain_parts = []
        for field in fields:
            if '.' in field:
                # Handle related fields like company_id.id
                base_field = field.split('.')[0]
                domain_parts.append(f"{indent}            ('{base_field}', '=', record.{field})")
            else:
                domain_parts.append(f"{indent}            ('{field}', '=', record.{field})")
        
        domain_str = ',\n'.join(domain_parts)
        
        code = f"""{indent}@models.constraint({field_params})
{indent}def _{constraint_name}(self):
{indent}    for record in self:
{indent}        if self.search_count([
{domain_str},
{indent}            ('id', '!=', record.id)
{indent}        ]) > 0:
{indent}            raise ValidationError({repr(constraint_message)})
"""
        return code
    
    elif parsed['type'] == 'check':
        # CHECK constraints need manual conversion
        code = f"""{indent}@models.constraint()
{indent}def _{constraint_name}(self):
{indent}    for record in self:
{indent}        # TODO: Convert SQL CHECK expression to Python validation
{indent}        # Original SQL: {parsed['expression']}
{indent}        # raise ValidationError({repr(constraint_message)})
{indent}        pass
"""
        return code
    
    else:
        # Unknown - leave as comment
        code = f"""{indent}# TODO: Convert this constraint manually
{indent}# Original: {constraint['sql']}
{indent}# @models.constraint()
{indent}# def _{constraint_name}(self):
{indent}#     raise ValidationError({repr(constraint_message)})
"""
        return code


def fix_file(file_path: Path, dry_run: bool = False) -> Tuple[bool, List[str]]:
    """Fix _sql_constraints in a file."""
    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception as e:
        return False, [f"Error reading {file_path}: {e}"]
    
    constraints = parse_constraints(content)
    
    if not constraints:
        return False, []
    
    # Check imports
    needs_models = 'from odoo import models' not in content and 'import odoo.models' not in content
    needs_validation = 'from odoo.exceptions import ValidationError' not in content and 'ValidationError' not in content
    
    if dry_run:
        fixes = [f"Would fix {len(constraints)} constraint(s) in {file_path}"]
        for c in constraints:
            parsed = parse_sql_constraint(c['sql'])
            fixes.append(f"  - {c['name']}: {c['sql']} -> {parsed['type']}")
        return True, fixes
    
    # Replace constraints (work backwards to preserve positions)
    new_content = content
    imports_to_add = []
    
    # Replace from end to start to preserve positions
    for constraint in reversed(constraints):
        parsed = parse_sql_constraint(constraint['sql'])
        method_code = generate_constraint_method(constraint, parsed)
        
        # Find the _sql_constraints assignment and replace it
        pattern = r'_sql_constraints\s*=\s*\[.*?\]'
        match = re.search(pattern, new_content, re.DOTALL)
        if match:
            # Replace with method code
            new_content = new_content[:match.start()] + method_code + new_content[match.end():]
    
    # Add imports if needed
    if needs_models or needs_validation:
        # Find import section
        import_pattern = r'(^from odoo import.*?$|^import odoo.*?$)'
        imports = re.findall(import_pattern, new_content, re.MULTILINE)
        
        if imports:
            # Add after last import
            last_import_pos = new_content.rfind(imports[-1]) + len(imports[-1])
            new_imports = []
            if needs_models:
                new_imports.append("from odoo import models")
            if needs_validation:
                new_imports.append("from odoo.exceptions import ValidationError")
            
            new_content = (
                new_content[:last_import_pos] + 
                '\n' + '\n'.join(new_imports) + 
                new_content[last_import_pos:]
            )
        else:
            # Add at the top after any shebang or encoding
            lines = new_content.split('\n')
            insert_pos = 0
            if lines and lines[0].startswith('#!'):
                insert_pos = 1
            if lines[insert_pos].startswith('#') and 'coding' in lines[insert_pos].lower():
                insert_pos = 2
            
            new_imports = []
            if needs_models:
                new_imports.append("from odoo import models")
            if needs_validation:
                new_imports.append("from odoo.exceptions import ValidationError")
            
            lines.insert(insert_pos, '\n'.join(new_imports))
            new_content = '\n'.join(lines)
    
    # Write back
    try:
        file_path.write_text(new_content, encoding='utf-8')
        return True, [f"Fixed {len(constraints)} constraint(s) in {file_path}"]
    except Exception as e:
        return False, [f"Error writing {file_path}: {e}"]


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate _sql_constraints to model.Constraint')
    parser.add_argument('--path', default='.', help='Path to search')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be fixed')
    args = parser.parse_args()
    
    path = Path(args.path)
    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        sys.exit(1)
    
    # Find Python files
    python_files = list(path.rglob('*.py'))
    
    if not python_files:
        print(f"No Python files found in {path}")
        return
    
    print(f"Searching {len(python_files)} files for _sql_constraints...")
    
    fixed_count = 0
    all_fixes = []
    
    for py_file in python_files:
        # Skip cache and venv
        if '__pycache__' in str(py_file) or any(x in str(py_file) for x in ['/venv/', '/.venv/', '/env/']):
            continue
        
        changed, fixes = fix_file(py_file, dry_run=args.dry_run)
        if changed:
            fixed_count += 1
            all_fixes.extend(fixes)
    
    if all_fixes:
        print('\n'.join(all_fixes))
        print(f"\n{'Would fix' if args.dry_run else 'Fixed'} {fixed_count} file(s)")
    else:
        print("No _sql_constraints found")


if __name__ == '__main__':
    main()
