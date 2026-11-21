import unittest
from selector import changes, select, split, unchanged_ast


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.tests = [{'number': i, 'id': name, 'file': 'tests/' + name + '.py', 'seconds': i + 1}
                      for i, name in enumerate(['a', 'b', 'c'])]
        self.mapping = {'version': 2, 'tests': ['a', 'b', 'c'], 'sets': [[0], [1], [2]],
                        'fixtures': {'tests/fixtures/x.sql': 1},
                        'files': {'sqlglot/a.py': {'spans': [[3, 9]], 'lines': {'3': 0, '5': 0, '6': 0, '9': 1}}}}

    def patch(self, path='sqlglot/a.py', line=5):
        return f'--- a/{path}\n+++ b/{path}\n@@ -{line} +{line} @@\n-old\n+new\n'

    def test_insert_tracks_both_neighbors(self):
        self.assertEqual(changes('--- a/sqlglot/a.py\n+++ b/sqlglot/a.py\n@@ -5,0 +6,1 @@\n+new\n'), {'sqlglot/a.py': {5, 6}})

    def test_function_expansion(self):
        self.assertEqual(select(self.patch(), self.tests, self.mapping), self.tests[:2])

    def test_line_mode(self):
        self.assertEqual(select(self.patch(line=6), self.tests, self.mapping, 'line'), self.tests[:1])

    def test_unknown_falls_back(self):
        self.assertEqual(select(self.patch('new.py'), self.tests, self.mapping), self.tests)

    def test_module_level_falls_back(self):
        self.assertEqual(select(self.patch(line=1), self.tests, self.mapping), self.tests)

    def test_no_map_falls_back(self):
        self.assertEqual(select(self.patch(), self.tests, None), self.tests)

    def test_missing_test_in_map_falls_back(self):
        self.mapping['tests'] = ['a']
        self.assertEqual(select(self.patch(), self.tests, self.mapping), self.tests)

    def test_fixture_dependency(self):
        self.assertEqual(select(self.patch('tests/fixtures/x.sql'), self.tests, self.mapping), self.tests[1:2])

    def test_test_file(self):
        self.assertEqual(select(self.patch('tests/c.py'), self.tests, self.mapping), self.tests[2:])

    def test_helper_file_falls_back(self):
        self.assertEqual(select(self.patch('tests/helpers.py'), self.tests, self.mapping), self.tests)

    def test_new_file_in_multipatch_is_not_ignored(self):
        patch = self.patch() + '\ndiff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+new\n'
        self.assertEqual(select(patch, self.tests, self.mapping), self.tests)

    def test_comment_equivalence_checks_complete_file(self):
        source = 'x = 1\n'
        comment = '--- a/sqlglot/a.py\n+++ b/sqlglot/a.py\n@@ -1 +1,2 @@\n+# comment\n x = 1\n'
        self.assertTrue(unchanged_ast(comment, 'sqlglot/a.py', source))
        real = '--- a/sqlglot/a.py\n+++ b/sqlglot/a.py\n@@ -1 +1,2 @@\n+# comment\n-x = 1\n+x = 2\n'
        self.assertFalse(unchanged_ast(real, 'sqlglot/a.py', source))

    def test_class_setting_selects_class_consumers(self):
        self.mapping['files']['sqlglot/a.py']['classes'] = [[1, 10, 'Example', 2]]
        self.assertEqual(select(self.patch(line=2), self.tests, self.mapping), self.tests[2:])

    def test_unknown_class_usage_still_falls_back(self):
        self.mapping['sets'].append([])
        self.mapping['files']['sqlglot/a.py']['classes'] = [[1, 10, 'Example', 3]]
        self.assertEqual(select(self.patch(line=2), self.tests, self.mapping), self.tests)

    def test_block_includes_earlier_short_circuit_guard(self):
        info = self.mapping['files']['sqlglot/a.py']
        info['blocks'] = [[4, 8]]
        info['lines'] = {'4': 1, '6': 0}
        self.assertEqual(select(self.patch(line=6), self.tests, self.mapping, 'block'), self.tests[:2])

    def test_unreached_block_does_not_require_whole_function(self):
        info = self.mapping['files']['sqlglot/a.py']
        info['blocks'] = [[8, 8]]
        self.assertEqual(select(self.patch(line=8), self.tests, self.mapping, 'block'), [])

    def test_syntax_error_does_not_use_narrow_class_map(self):
        info = self.mapping['files']['sqlglot/a.py']
        info['source'] = 'class Example:\n    value = 1\n'
        info['classes'] = [[1, 2, 'Example', 2]]
        patch = '--- a/sqlglot/a.py\n+++ b/sqlglot/a.py\n@@ -2 +2 @@\n-    value = 1\n+    value = !\n'
        self.assertEqual(select(patch, self.tests, self.mapping, 'block'), self.tests)

    def test_bad_context_is_not_equivalent(self):
        self.assertFalse(unchanged_ast(self.patch(), 'sqlglot/a.py', 'x = 1\n'))

    def test_docstring_is_preserved(self):
        patch = '--- a/sqlglot/a.py\n+++ b/sqlglot/a.py\n@@ -1 +1 @@\n-"old"\n+"new"\n'
        self.assertFalse(unchanged_ast(patch, 'sqlglot/a.py', '"old"\n'))

    def test_balancing_preserves_unique_tests(self):
        tests = [{'number': i, 'seconds': s} for i, s in enumerate([29, 10, 10, 5, 2])]
        workers = split(tests)
        self.assertEqual(sorted(n for worker in workers for n in worker), list(range(5)))
        self.assertLessEqual(len(workers), 8)
        self.assertEqual(split([]), [])


if __name__ == '__main__':
    unittest.main()
