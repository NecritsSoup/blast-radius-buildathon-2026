import ast
import unittest
from selector import constant_readers, select

SOURCE = '''from x import Kind


class Dialect:
    WEEK_OFFSET = 0
    ESCAPES = {"x": 1}
    NAME = Kind.A

    def week(self):
        return self.WEEK_OFFSET
'''


def edit(source, old, new):
    assert old in source
    return source.replace(old, new, 1)


class ConstantTests(unittest.TestCase):
    def setUp(self):
        self.mapping = {
            'version': 2, 'tests': ['t0', 't1', 't2'], 'sets': [[0], [1], [0, 1, 2], [2]],
            'attr_readers': {'WEEK_OFFSET': 0, 'ESCAPES': 1, 'NAME': 3},
            'attr_unsafe': ['ESCAPES'], 'fixtures': {},
            'files': {'sqlglot/d.py': {
                'source': SOURCE, 'spans': [[9, 10]], 'blocks': [[10, 10]],
                'classes': [[4, 10, 'Dialect', 2]], 'lines': {'10': 0}}},
        }
        self.tests = [{'number': i, 'id': f't{i}', 'file': 'tests/t.py', 'seconds': 1.0} for i in range(3)]

    def readers(self, new):
        return constant_readers(ast.parse(SOURCE), ast.parse(new), 'Dialect', self.mapping)

    def test_value_change_selects_readers(self):
        self.assertEqual(self.readers(edit(SOURCE, 'WEEK_OFFSET = 0', 'WEEK_OFFSET = -1')), {0})

    def test_unsafe_name_falls_back(self):
        self.assertIsNone(self.readers(edit(SOURCE, '"x": 1', '"x": 2')))

    def test_new_attribute_path_falls_back(self):
        self.assertIsNone(self.readers(edit(SOURCE, 'Kind.A', 'Kind.B')))

    def test_known_attribute_path_is_allowed(self):
        self.assertEqual(self.readers(edit(SOURCE, 'NAME = Kind.A', 'NAME = (Kind.A, 1)')), {2})

    def test_method_change_in_same_class_falls_back(self):
        new = edit(edit(SOURCE, 'WEEK_OFFSET = 0', 'WEEK_OFFSET = 1'), 'return self', 'return 1 + self')
        self.assertIsNone(self.readers(new))

    def test_unknown_name_falls_back(self):
        new = edit(SOURCE, '    NAME = Kind.A\n', '    NAME = Kind.A\n    OTHER = 3\n')
        self.assertIsNone(self.readers(new))

    def test_select_end_to_end(self):
        new = edit(SOURCE, 'WEEK_OFFSET = 0', 'WEEK_OFFSET = 2')
        patch = ('diff --git a/sqlglot/d.py b/sqlglot/d.py\n--- a/sqlglot/d.py\n+++ b/sqlglot/d.py\n'
                 '@@ -5 +5 @@\n-    WEEK_OFFSET = 0\n+    WEEK_OFFSET = 2\n')
        self.assertEqual(SOURCE.splitlines()[4], '    WEEK_OFFSET = 0')
        self.assertIn('WEEK_OFFSET = 2', new)
        self.assertEqual([t['id'] for t in select(patch, self.tests, self.mapping)], ['t0'])

    def test_select_unsafe_uses_class_consumers(self):
        patch = ('diff --git a/sqlglot/d.py b/sqlglot/d.py\n--- a/sqlglot/d.py\n+++ b/sqlglot/d.py\n'
                 '@@ -6 +6 @@\n-    ESCAPES = {"x": 1}\n+    ESCAPES = {"x": 2}\n')
        self.assertEqual(len(select(patch, self.tests, self.mapping)), 3)


if __name__ == '__main__':
    unittest.main()
