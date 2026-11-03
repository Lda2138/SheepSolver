import tempfile
import unittest
from pathlib import Path

from map_acquire import resolve_applet_dir


class AppletDirectoryTests(unittest.TestCase):
    def test_discovers_only_user_and_accepts_explicit_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            users = Path(tmp)
            first = users / 'first' / 'applet'
            first.mkdir(parents=True)
            self.assertEqual(resolve_applet_dir(users_dir=users), first)
            second = users / 'second' / 'applet'
            second.mkdir(parents=True)
            with self.assertRaises(ValueError):
                resolve_applet_dir(users_dir=users)
            self.assertEqual(resolve_applet_dir(second, users_dir=users), second)

    def test_missing_directories_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                resolve_applet_dir(users_dir=tmp)
            with self.assertRaises(ValueError):
                resolve_applet_dir(Path(tmp) / 'missing')


if __name__ == '__main__':
    unittest.main()
