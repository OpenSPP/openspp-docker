#!/usr/bin/env python3
"""
Script to migrate _sql_constraints to model.Constraint in Odoo 19.

This script searches for deprecated _sql_constraints and converts them to
the new model.Constraint format.

Usage:
    python3 fix_sql_constraints.py [--path PATH] [--dry-run]
"""

import ast
import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple, Optional


class SQLConstraintVisitor(ast.NodeVisitor):
    """AST visitor to find _sql_constraints definitions."""
    
    def __init__(self):
        self.constraints = []
        self.current_class = None
        
    def visit_ClassDef(self, node):
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class
    
    def visit_Assign(self, node):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == '_sql_constraints':
                if isinstance(node.value, ast.List):
                    constraints = []
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Tuple) and len(elt.elts) >= 2:
                            constraint_name = None
                            constraint_sql = None
                            constraint_message = None
                            
                            if len(elt.elts) >= 1 and isinstance(elt.elts[0], ast.Constant):
                                constraint_name = elt.elts[0].value
                            if len(elt.elts) >= 2 and isinstance(elt.elts[1], ast.Constant):
                                constraint_sql = elt.elts[1].value
                            if len(elt.elts) >= 3 and isinstance(elt.elts[2], ast.Constant):
                                constraint_message = elt.elts[2].value
                            
                            if constraint_name and constraint_sql:
                                self.constraints.append({
                                    'class': self.current_class,
                                    'name': constraint_name,
                                    'sql': constraint_sql,
                                    'message': constraint_message or '',
                                    'node': node,
                                })
        self.generic_visit(node)


def parse_sql_constraint(sql: str) -> dict:
    """Parse SQL constraint to extract constraint type and fields."""
    sql = sql.strip().upper()
    
    # UNIQUE constraint
    unique_match = re.match(r'UNIQUE\s*\((.*?)\)', sql)
    if unique_match:
        fields = [f.strip().strip('"\'') for f in unique_match.group(1).split(',')]
        return {'type': 'unique', 'fields': fields}
    
    # CHECK constraint
    check_match = re.match(r'CHECK\s*\((.*?)\)', sql, re.DOTALL)
    if check_match:
        return {'type': 'check', 'expression': check_match.group(1)}
    
    return {'type': 'unknown', 'sql': sql}


def generate_constraint_code(constraint: dict, parsed: dict) -> str:
    """Generate the new model.Constraint code."""
    constraint_name = constraint['name']
    constraint_message = constraint['message'] or f"Constraint violation: {constraint_name}"
    
    if parsed['type'] == 'unique':
        fields = parsed['fields']
        field_params = ', '.join(f"'{f}'" for f in fields)
        
        # Build search domain for uniqueness check
        domain_parts = []
        for field in fields:
            # Handle related fields (e.g., company_id.id)
            if '.' in field:
                field_parts = field.split('.')
                domain_parts.append(f"('{field_parts[0]}', '=', record.{field})")
            else:
                domain_parts.append(f"('{field}', '=', record.{field})")
        
        domain_str = ',\n                '.join(domain_parts)
        
        code = f"""    @models.constraint({field_params})
    def _{constraint_name}(self):
        for record in self:
            if self.search_count([
                {domain_str},
                ('id', '!=', record.id)
            ]) > 0:
                raise ValidationError({repr(constraint_message)})
"""
        return code
    
    elif parsed['type'] == 'check':
        # For CHECK constraints, we need to convert SQL expression to Python
        # This is more complex and may need manual adjustment
        code = f"""    @models.constraint()
    def _{constraint_name}(self):
        for record in self:
            # TODO: Convert SQL CHECK expression to Python validation
            # Original SQL: {parsed['expression']}
            # raise ValidationError({repr(constraint_message)})
            pass
"""
        return code
    
    else:
        # Unknown constraint type - keep as comment for manual conversion
        code = f"""    # TODO: Convert this constraint manually
    # Original: {constraint['sql']}
    # @models.constraint()
    # def _{constraint_name}(self):
    #     raise ValidationError({repr(constraint_message)})
"""
        return code


def fix_file(file_path: Path, dry_run: bool = False) -> Tuple[bool, List[str]]:
    """Fix _sql_constraints in a Python file."""
    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception as e:
        return False, [f"Error reading file: {e}"]
    
    # Parse AST
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return False, [f"Syntax error in file: {e}"]
    
    # Find constraints
    visitor = SQLConstraintVisitor()
    visitor.visit(tree)
    
    if not visitor.constraints:
        return False, []
    
    # Check if models is imported
    has_models_import = 'from odoo import models' in content or 'import odoo.models as models' in content
    has_validation_error = 'from odoo.exceptions import ValidationError' in content or 'ValidationError' in content
    
    # Generate fixes
    fixes = []
    imports_needed = []
    
    if not has_models_import:
        imports_needed.append("from odoo import models")
    if not has_validation_error:
        imports_needed.append("from odoo.exceptions import ValidationError")
    
    # Build replacement code
    lines = content.split('\n')
    new_lines = []
    i = 0
    skip_until = -1
    
    while i < len(lines):
        # Check if this line is part of a _sql_constraints assignment
        line = lines[i]
        
        # Find the _sql_constraints assignment
        constraint_found = False
        for constraint in visitor.constraints:
            node = constraint['node']
            if node.lineno - 1 == i:
                constraint_found = True
                # Skip the original _sql_constraints assignment
                # Find where it ends (multiline list)
                start_line = i
                paren_count = 0
                bracket_count = 0
                in_string = False
                string_char = None
                
                j = i
                while j < len(lines):
                    for char in lines[j]:
                        if char in ('"', "'") and (j == i or lines[j][max(0, lines[j].index(char)-1)] != '\\'):
                            if not in_string:
                                in_string = True
                                string_char = char
                            elif char == string_char:
                                in_string = False
                                string_char = None
                        elif not in_string:
                            if char == '(':
                                paren_count += 1
                            elif char == ')':
                                paren_count -= 1
                            elif char == '[':
                                bracket_count += 1
                            elif char == ']':
                                bracket_count -= 1
                    
                    if bracket_count == 0 and paren_count == 0 and not in_string:
                        skip_until = j
                        break
                    j += 1
                
                # Generate new constraint code
                parsed = parse_sql_constraint(constraint['sql'])
                constraint_code = generate_constraint_code(constraint, parsed)
                new_lines.append(constraint_code)
                break
        
        if i <= skip_until:
            i += 1
            if i > skip_until:
                skip_until = -1
            continue
        
        new_lines.append(line)
        i += 1
    
    # Add imports if needed
    if imports_needed and not dry_run:
        # Find the best place to add imports (after existing odoo imports)
        import_insert_pos = 0
        for i, line in enumerate(new_lines):
            if line.startswith('from odoo') or line.startswith('import odoo'):
                import_insert_pos = i + 1
                break
        
        for imp in imports_needed:
            if imp not in '\n'.join(new_lines):
                new_lines.insert(import_insert_pos, imp)
                import_insert_pos += 1
    
    if dry_run:
        fixes.append(f"Would fix {len(visitor.constraints)} constraint(s) in {file_path}")
        for constraint in visitor.constraints:
            parsed = parse_sql_constraint(constraint['sql'])
            fixes.append(f"  - {constraint['name']}: {constraint['sql']} -> {parsed['type']}")
    else:
        new_content = '\n'.join(new_lines)
        file_path.write_text(new_content, encoding='utf-8')
        fixes.append(f"Fixed {len(visitor.constraints)} constraint(s) in {file_path}")
    
    return True, fixes


def main():
    parser = argparse.ArgumentParser(description='Fix _sql_constraints in Odoo 19')
    parser.add_argument('--path', default='.', help='Path to search for Python files')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be fixed without making changes')
    args = parser.parse_args()
    
    path = Path(args.path)
    if not path.exists():
        print(f"Error: Path {path} does not exist", file=sys.stderr)
        sys.exit(1)
    
    # Find all Python files
    python_files = list(path.rglob('*.py'))
    
    if not python_files:
        print(f"No Python files found in {path}")
        return
    
    print(f"Searching {len(python_files)} Python files for _sql_constraints...")
    
    fixed_count = 0
    all_fixes = []
    
    for py_file in python_files:
        # Skip __pycache__ and virtual environments
        if '__pycache__' in str(py_file) or 'venv' in str(py_file) or '.venv' in str(py_file):
            continue
        
        changed, fixes = fix_file(py_file, dry_run=args.dry_run)
        if changed:
            fixed_count += 1
            all_fixes.extend(fixes)
    
    if all_fixes:
        print("\n".join(all_fixes))
        print(f"\n{'Would fix' if args.dry_run else 'Fixed'} {fixed_count} file(s)")
    else:
        print("No _sql_constraints found")


if __name__ == '__main__':
    main()
