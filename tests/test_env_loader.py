import os
import tempfile
import unittest
from pathlib import Path

from env_loader import load_env


class StandardLibraryEnvLoaderTests(unittest.TestCase):
    def tearDown(self):
        for key in ("TEST_MERA_ENV_PLAIN", "TEST_MERA_ENV_QUOTED", "TEST_MERA_ENV_EXPORT", "TEST_MERA_ENV_PREEXISTING"):
            os.environ.pop(key, None)

    def test_loads_simple_key_value_comments_quotes_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "# comment\n"
                "TEST_MERA_ENV_PLAIN=plain-value\n"
                'TEST_MERA_ENV_QUOTED="two words"\n'
                "export TEST_MERA_ENV_EXPORT=enabled\n",
                encoding="utf-8",
            )
            load_env(env_path)
        self.assertEqual(os.environ["TEST_MERA_ENV_PLAIN"], "plain-value")
        self.assertEqual(os.environ["TEST_MERA_ENV_QUOTED"], "two words")
        self.assertEqual(os.environ["TEST_MERA_ENV_EXPORT"], "enabled")

    def test_existing_environment_variable_takes_precedence(self):
        os.environ["TEST_MERA_ENV_PREEXISTING"] = "from-shell"
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("TEST_MERA_ENV_PREEXISTING=from-file\n", encoding="utf-8")
            load_env(env_path)
        self.assertEqual(os.environ["TEST_MERA_ENV_PREEXISTING"], "from-shell")

    def test_missing_env_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            load_env(Path(directory) / "missing.env")


if __name__ == "__main__":
    unittest.main()
