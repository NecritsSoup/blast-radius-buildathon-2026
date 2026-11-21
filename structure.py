"""Source scopes shared by coverage export and offline inspection."""
import ast


def class_scopes(tree):
    scopes = []

    def visit(node, prefix=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                name = prefix + child.name
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                scopes.append([start, child.end_lineno, name])
                visit(child, name + '.')
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, prefix + child.name + '.<locals>.')
            else:
                visit(child, prefix)
    visit(tree)
    return scopes
