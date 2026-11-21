import ast
import enum
import os
from unittest.mock import patch
import unittest
from comparison_data import ComparisonData
from selector import select
from semantic import equivalent_operator_edits, identity_equals_value


class Color(str, enum.Enum):
    RED = 'red'
    BLUE = 'blue'


class EqualToNone:
    def __eq__(self, other):
        return other is None


class Table:
    pass


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.data = ComparisonData()

    def equivalent(self, before, after):
        return equivalent_operator_edits(ast.parse(before), ast.parse(after),
                   self.data.sites.get('x.py', {}), self.data.types)

    def observe(self, left, right, operation='Is'):
        self.data.observe(left, right, operation, 'x.py', 1, 0)

    def test_default_object_equality(self):
        self.observe(None, None)
        self.observe(Table(), None)
        self.assertTrue(self.equivalent('x is None', 'x == None'))

    def test_custom_equality_is_rejected(self):
        self.observe(EqualToNone(), None)
        self.assertFalse(self.equivalent('x is None', 'x == None'))

    def test_canonical_string_enum(self):
        self.observe(Color.RED, Color.RED)
        self.observe(Color.BLUE, Color.RED)
        self.assertTrue(self.equivalent('x is Y', 'x == Y'))

    def test_enum_vs_string_is_rejected(self):
        self.observe(Color.RED, 'red')
        self.assertFalse(self.equivalent('x is Y', 'x == Y'))

    def test_synthetic_enum_is_rejected(self):
        synthetic = str.__new__(Color, 'red')
        self.observe(synthetic, Color.RED)
        self.assertFalse(self.equivalent('x is Y', 'x == Y'))

    def test_nan_is_rejected(self):
        value = float('nan')
        self.observe(value, value)
        self.assertFalse(self.equivalent('x is Y', 'x == Y'))

    def test_integer_value_equality_is_rejected(self):
        self.observe(1000, 1000)
        self.assertFalse(self.equivalent('x is Y', 'x == Y'))

    def test_unhashable_equality_callable_is_recorded_safely(self):
        class Equality:
            __hash__ = None
            def __call__(self, *args):
                return True
        class Custom:
            __eq__ = Equality()
        self.observe(Custom(), None)
        self.assertFalse(self.equivalent('x is None', 'x == None'))

    def test_enum_members_with_equal_raw_strings_are_rejected(self):
        class Collision(str, enum.Enum):
            def __new__(cls, value, text):
                member = str.__new__(cls, text)
                member._value_ = value
                return member
            A = (1, 'same')
            B = (2, 'same')
        self.assertEqual(Collision.A, Collision.B)
        self.assertIsNot(Collision.A, Collision.B)
        self.observe(Collision.A, Collision.B)
        self.assertFalse(self.equivalent('x is Y', 'x == Y'))

    def test_different_enum_classes_are_rejected(self):
        one = enum.Enum('SameName', {'RED': 'red'}, type=str)
        two = enum.Enum('SameName', {'RED': 'red'}, type=str)
        self.observe(one.RED, two.RED)
        self.assertFalse(self.equivalent('x is Y', 'x == Y'))

    def test_custom_negation_is_rejected(self):
        class Different:
            def __ne__(self, other):
                return False
        self.observe(Different(), None, 'IsNot')
        self.assertFalse(self.equivalent('x is not None', 'x != None'))

    def test_identity_observation_does_not_call_custom_equality(self):
        calls = []
        class SideEffect:
            def __eq__(self, other):
                calls.append(other)
                return True
        self.observe(SideEffect(), None)
        self.assertEqual(calls, [])

    def test_unhashable_type_is_recorded_safely(self):
        class Meta(type):
            __hash__ = None
        class Custom(metaclass=Meta):
            pass
        self.observe(Custom(), None)
        self.assertTrue(self.equivalent('x is None', 'x == None'))

    def test_comparison_trees_are_restored_on_success_and_failure(self):
        self.observe(Table(), None)
        before = ast.parse('x is None')
        after = ast.parse('x == None')
        saved = ast.dump(after)
        equivalent_operator_edits(before, after, self.data.sites['x.py'], self.data.types)
        self.assertEqual(ast.dump(after), saved)
        before = ast.parse('x is None\ny = 1')
        after = ast.parse('x == None\ny = 2')
        saved = ast.dump(after)
        equivalent_operator_edits(before, after, self.data.sites['x.py'], self.data.types)
        self.assertEqual(ast.dump(after), saved)

    def test_no_observations_is_rejected(self):
        self.assertFalse(self.equivalent('x is None', 'x == None'))

    def test_additional_real_edit_is_rejected(self):
        self.observe(Table(), None)
        self.assertFalse(self.equivalent('x is None\nx = 1', 'x == None\nx = 2'))

    def test_polarity_change_is_rejected(self):
        self.observe(Table(), None)
        self.assertFalse(self.equivalent('x is None', 'x != None'))

    def test_negated_equivalence(self):
        self.observe(Table(), None, 'IsNot')
        self.assertTrue(self.equivalent('x is not None', 'x != None'))

    def test_equality_slot_mutation_creates_distinct_domain(self):
        class Mutable:
            pass
        self.observe(Mutable(), None)
        Mutable.__eq__ = EqualToNone.__eq__
        self.observe(Mutable(), None)
        self.assertFalse(self.equivalent('x is None', 'x == None'))

    def proof_mapping(self):
        self.data.observe(Table(), None, 'Is', 'sqlglot/a.py', 2, 11)
        tests = [{'number': i, 'id': name, 'file': 'tests/' + name + '.py', 'seconds': 1}
                 for i, name in enumerate(['a', 'b'])]
        mapping = {'version': 2, 'tests': ['a', 'b'], 'sets': [[0], [1]], 'fixtures': {},
                   'operand_types': self.data.types,
                   'files': {'sqlglot/a.py': {'source': 'def same(x):\n    return x is None\n',
                       'spans': [[1, 2]], 'blocks': [[2, 2]], 'lines': {'2': 0},
                       'comparisons': self.data.sites['sqlglot/a.py']},
                       'sqlglot/b.py': {'source': 'def real():\n    return 1\n',
                       'spans': [[1, 2]], 'blocks': [[2, 2]], 'lines': {'2': 1}}}}
        return tests, mapping

    def safe_patch(self):
        return 'diff --git a/sqlglot/a.py b/sqlglot/a.py\n--- a/sqlglot/a.py\n+++ b/sqlglot/a.py\n@@ -2 +2 @@\n-    return x is None\n+    return x == None\n'

    def test_complete_patch_equivalence(self):
        tests, mapping = self.proof_mapping()
        with patch.dict(os.environ, {'SELECT_PROOFS': '1'}):
            self.assertEqual(select(self.safe_patch(), tests, mapping), [])

    def test_other_changed_file_prevents_proof_skip(self):
        tests, mapping = self.proof_mapping()
        other = 'diff --git a/sqlglot/b.py b/sqlglot/b.py\n--- a/sqlglot/b.py\n+++ b/sqlglot/b.py\n@@ -2 +2 @@\n-    return 1\n+    return 2\n'
        with patch.dict(os.environ, {'SELECT_PROOFS': '1'}):
            self.assertEqual(select(self.safe_patch() + other, tests, mapping), tests)

    def test_accepted_domains_agree_on_concrete_values(self):
        values = [None, Table(), Table(), Color.RED, Color.BLUE, EqualToNone(),
                  1, 1000, 'red', float('nan'), []]
        for left in values:
            for right in values:
                a, b = self.data.type_id(left), self.data.type_id(right)
                if identity_equals_value(self.data.types[a], self.data.types[b]):
                    self.assertEqual(left is right, bool(left == right))
                    self.assertEqual(left is not right, bool(left != right))


if __name__ == '__main__':
    unittest.main()
