"""Runtime equivalence checks against observed pristine operand domains."""
import ast


def identity_equals_value(left, right):
    if left['identity_equality'] and right['identity_equality']:
        return True
    return (left['class_id'] == right['class_id']
            and left['canonical_enum'] and right['canonical_enum'])


def equivalent_operator_edits(before, after, sites, types):
    """Accept only complete ASTs differing exclusively in proven comparisons.

    Other edits in the same patch could change the observed domains, so callers
    must establish equivalence for EVERY changed file before skipping a patch.
    """
    old_nodes, new_nodes = list(ast.walk(before)), list(ast.walk(after))
    if len(old_nodes) != len(new_nodes):
        return False
    replacements = []
    try:
        for old, new in zip(old_nodes, new_nodes):
            if not isinstance(old, ast.Compare) or not isinstance(new, ast.Compare):
                continue
            if len(old.ops) != 1 or len(new.ops) != 1:
                continue
            old_op, new_op = type(old.ops[0]).__name__, type(new.ops[0]).__name__
            if old_op == new_op:
                continue
            if {old_op, new_op} not in ({'Is', 'Eq'}, {'IsNot', 'NotEq'}):
                return False
            pairs = sites.get(f'{old.lineno}:{old.col_offset}:{old_op}')
            if not pairs or not all(identity_equals_value(types[a], types[b]) for a, b in pairs):
                return False
            replacements.append((new, new.ops))
            new.ops = old.ops
        return bool(replacements) and ast.dump(before) == ast.dump(after)
    finally:
        for node, operations in replacements:
            node.ops = operations
