"""Pristine operand-domain collection; no patches or mutation results are read."""
import ast
import enum
import importlib.abc
import importlib.machinery
import operator
from pathlib import Path


OPS = {'Is': operator.is_, 'IsNot': operator.is_not, 'Eq': operator.eq, 'NotEq': operator.ne}
PROBE = '__builders_operand_probe__'


def special(cls, name):
    for base in cls.__mro__:
        if name in vars(base):
            return vars(base)[name]
    return None


def domain(value):
    """Describe only equality domains with a small, explicit argument for safety."""
    cls = type(value)
    eq, ne = special(cls, '__eq__'), special(cls, '__ne__')
    identity = cls is type(None) or (eq is object.__eq__ and ne is object.__ne__)
    canonical, unique = False, False
    if issubclass(cls, enum.Enum) and not issubclass(cls, enum.Flag):
        members = {id(member): member for member in cls.__members__.values()}
        canonical = id(value) in members
        if eq is str.__eq__ and ne is str.__ne__:
            raw = [str.__str__(member) for member in members.values()]
            unique = len(raw) == len(set(raw))
        elif eq is int.__eq__ and ne is int.__ne__:
            raw = [int.__int__(member) for member in members.values()]
            unique = len(raw) == len(set(raw))
    return cls, eq, ne, identity, canonical, unique


class ComparisonData:
    def __init__(self):
        self.types = []
        self.type_ids = {}
        self.sites = {}
        self.domains = {}
        self.slot_refs = {}
        self.class_refs = {}
        self.class_ids = {}
        self.opaque_types = {}
        self.none_id = None

    def type_id(self, value):
        if value is None and self.none_id is not None:
            return self.none_id
        cls = type(value)
        class_key = id(cls)
        if class_key in self.opaque_types:
            # Once a type is known to have non-identity equality and no safe
            # Enum domain, keeping it opaque is conservative even if its slots
            # change later. No operator-equivalence claim can use this entry.
            return self.opaque_types[class_key]
        eq, ne = special(cls, '__eq__'), special(cls, '__ne__')
        # Custom callable slots need not be hashable. Retain raw descriptors
        # so their identity keys cannot be recycled after a slot is replaced.
        descriptor_key = (id(eq), id(ne))
        self.slot_refs.setdefault(descriptor_key, (eq, ne))
        class_key = id(cls)
        self.class_refs.setdefault(class_key, cls)
        if class_key not in self.class_ids:
            self.class_ids[class_key] = len(self.class_ids)
        slot_key = (class_key, *descriptor_key)
        if slot_key not in self.domains:
            _, _, _, identity, _, unique = domain(value)
            members = {id(member): member for member in cls.__members__.values()} if unique else {}
            self.domains[slot_key] = identity, members
        identity, members = self.domains[slot_key]
        canonical = id(value) in members
        key = (slot_key, canonical)
        if key not in self.type_ids:
            self.type_ids[key] = len(self.types)
            self.types.append({'name': cls.__module__ + ':' + cls.__qualname__,
                               'identity_equality': identity,
                               'canonical_enum': canonical,
                               'class_id': self.class_ids[class_key]})
        result = self.type_ids[key]
        if value is None:
            self.none_id = result
        elif not identity and not members:
            self.opaque_types[class_key] = result
        return result

    def observe(self, left, right, operation, path, line, column):
        pair = (self.type_id(left), self.type_id(right))
        self.sites.setdefault(path, {}).setdefault(f'{line}:{column}:{operation}', set()).add(pair)
        return OPS[operation](left, right)

    def install(self, root):
        import sys
        collector = self
        root = Path(root)

        class Rewrite(ast.NodeTransformer):
            def __init__(self, path):
                self.path = path

            def visit_Compare(self, node):
                self.generic_visit(node)
                if len(node.ops) != 1 or type(node.ops[0]).__name__ not in {'Is', 'IsNot'}:
                    return node
                call = ast.Call(func=ast.Name(id=PROBE, ctx=ast.Load()),
                                args=[node.left, node.comparators[0],
                                      ast.Constant(type(node.ops[0]).__name__), ast.Constant(self.path),
                                      ast.Constant(node.lineno), ast.Constant(node.col_offset)], keywords=[])
                return ast.copy_location(call, node)

        class Loader(importlib.abc.Loader):
            def __init__(self, original, filename):
                self.original, self.filename = original, filename

            def create_module(self, spec):
                return self.original.create_module(spec)

            def get_source(self, fullname):
                return self.original.get_source(fullname)

            def get_filename(self, fullname):
                return self.filename

            def exec_module(self, module):
                source = self.get_source(module.__name__)
                path = Path(self.filename).relative_to(root).as_posix()
                tree = Rewrite(path).visit(ast.parse(source, filename=self.filename, type_comments=True))
                ast.fix_missing_locations(tree)
                module.__dict__[PROBE] = collector.observe
                exec(compile(tree, self.filename, 'exec', dont_inherit=True), module.__dict__)

        class Finder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname != 'sqlglot' and not fullname.startswith('sqlglot.'):
                    return None
                spec = importlib.machinery.PathFinder.find_spec(fullname, path)
                if (spec and isinstance(spec.loader, importlib.machinery.SourceFileLoader)
                        and Path(spec.origin).is_relative_to(root)):
                    spec.loader = Loader(spec.loader, spec.origin)
                return spec

        finder = Finder()
        sys.meta_path.insert(0, finder)
        return finder
