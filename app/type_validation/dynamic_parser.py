#!/usr/bin/env python3
import ast
from typing import List, Optional, Set

from colors import colorize_dynamic_error
from config import ERROR_NODES


def _get_node_repr(node: ast.AST) -> str:
    """Get string representation of an AST node."""
    try:
        # Try ast.unparse first (Python 3.9+)
        return ast.unparse(node)
    except AttributeError:
        # Fallback for older Python versions
        if isinstance(node, ast.Constant):
            # String, number, boolean, None
            return repr(node.value)
        elif isinstance(node, ast.Name):
            # Variable name
            return node.id
        elif isinstance(node, ast.Attribute):
            # obj.attr
            return f"{_get_node_repr(node.value)}.{node.attr}"
        else:
            # Complex expression - just show generic placeholder
            return "<expression>"


def analyze_dynamic_access_in_file(
    file_path: str,
    changed_lines: Set[int],
) -> List[str]:
    """
    Parse entire file with AST and find dynamic access calls on changed lines.

    Args:
        file_path: Path to the Python file to analyze
        changed_lines: Set of line numbers that have been changed

    Returns:
        List of colored error messages for dynamic access issues
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source_code = f.read()
    except (IOError, OSError):
        return []

    try:
        tree = ast.parse(source_code, filename=file_path)
    except SyntaxError:
        # If the file has syntax errors, skip it (mypy will catch those)
        return []

    # Build a set of lines that have type: ignore comments
    source_lines = source_code.splitlines()
    ignored_lines: Set[int] = set()
    for i, line in enumerate(source_lines, start=1):
        if "# type: ignore" in line:
            ignored_lines.add(i)

    errors = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            func_name = node.func.id

            if func_name in [error_node.value for error_node in ERROR_NODES]:
                # Check if this call is on a changed line
                try:
                    if node.lineno in changed_lines:
                        # Skip if line has type: ignore comment
                        if node.lineno in ignored_lines:
                            continue
                        error = _generate_dynamic_access_error(
                            node,
                            func_name,
                            file_path,
                            node.lineno,
                        )
                        if error:
                            errors.append(colorize_dynamic_error(error))
                except AttributeError:
                    # Skip nodes that don't have line numbers
                    continue

    return errors


def _generate_dynamic_access_error(
    node: ast.Call,
    func_name: str,
    file_path: str,
    line_number: int,
) -> Optional[str]:
    """Generate an error message for a dynamic access function call."""
    if len(node.args) < 2:
        return None

    # Try to get object name for better suggestions
    try:
        obj_name = ast.unparse(node.args[0])
    except AttributeError:
        # Fallback for older Python versions (ast.unparse added in Python 3.9)
        try:
            if isinstance(node.args[0], ast.Name):
                obj_name = node.args[0].id
            else:
                obj_name = "obj"
        except:
            obj_name = "obj"

    attr_arg = node.args[1]

    if isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str):
        # String literal case - provide specific suggestions
        attr_name = attr_arg.value

        message = f"Use of {func_name}() prevents static type checking. "
        if func_name == ERROR_NODES.GETATTR.value:
            if len(node.args) > 2:
                default_repr = _get_node_repr(node.args[2])
                message += f'Consider "{obj_name}.{attr_name}" instead of "{ERROR_NODES.GETATTR.value}({obj_name}, \'{attr_name}\', {default_repr})"'
            else:
                message += f'Consider "{obj_name}.{attr_name}" instead of "{ERROR_NODES.GETATTR.value}({obj_name}, \'{attr_name}\')"'
        elif func_name == ERROR_NODES.SETATTR.value:
            if len(node.args) > 2:
                value_repr = _get_node_repr(node.args[2])
                message += f'Consider "{obj_name}.{attr_name} = {value_repr}" instead of "{ERROR_NODES.SETATTR.value}({obj_name}, \'{attr_name}\', {value_repr})"'
            else:
                message += f"Consider direct assignment instead of \"{ERROR_NODES.SETATTR.value}({obj_name}, '{attr_name}', <expression>)\""
        else:  # hasattr
            message += f"Consider using isinstance() or Protocol for type-safe attribute checking instead of \"{ERROR_NODES.HASATTR.value}({obj_name}, '{attr_name}')\""
    else:
        message = f"Use of {func_name}() with dynamic attribute name prevents static type checking. "
        # Variable case - provide general warnings
        if func_name == ERROR_NODES.GETATTR.value:
            message += "Consider direct attribute access when possible."
        elif func_name == ERROR_NODES.SETATTR.value:
            message += "Consider direct attribute assignment when possible."
        else:  # hasattr
            message += "Consider isinstance() or Protocol for type-safe checking."

    return f"{file_path}:{line_number}:dynamic-check:{message}"
