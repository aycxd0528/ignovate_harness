import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def import_config(test_case):
    try:
        return importlib.import_module("config")
    except ModuleNotFoundError as error:
        test_case.fail(f"config module is missing: {error}")


class LoadSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.connection_path = Path(directory.name)/'user/config.json'

    def test_loads_values_and_dotenv_from_project_directory(self):
        config = import_config(self)
        env = {
            "DEEPSEEK_API_KEY": "test-key",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_MODEL": "deepseek-chat",
        }
        with patch("config.load_dotenv") as load_dotenv, patch.dict(
            os.environ, env, clear=True
        ):
            settings = config.load_settings(config_path=self.connection_path)

        self.assertEqual(settings.api_key, "test-key")
        self.assertEqual(settings.api_base, "https://api.deepseek.com")
        self.assertEqual(settings.model, "deepseek-chat")
        self.assertEqual(settings.project_root, Path(config.__file__).resolve().parent)
        load_dotenv.assert_called_once_with(
            Path(config.__file__).resolve().parent / ".env"
        )

    def test_missing_configuration_names_variables_without_revealing_key(self):
        config = import_config(self)
        with patch("config.load_dotenv"), patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "secret-sentinel"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_BASE_URL") as error:
                config.load_settings(config_path=self.connection_path)

        self.assertIn("DEEPSEEK_MODEL", str(error.exception))
        self.assertNotIn("secret-sentinel", str(error.exception))

    def test_rejects_base_urls_without_a_valid_host_or_port(self):
        config = import_config(self)
        for base_url in ("https://", "https://api.example:bad", "http://?not-a-host"):
            with self.subTest(base_url=base_url):
                env = {
                    "DEEPSEEK_API_KEY": "test-key",
                    "DEEPSEEK_BASE_URL": base_url,
                    "DEEPSEEK_MODEL": "deepseek-chat",
                }
                with patch("config.load_dotenv"), patch.dict(os.environ, env, clear=True):
                    with self.assertRaisesRegex(ValueError, "DEEPSEEK_BASE_URL"):
                        config.load_settings(config_path=self.connection_path)


if __name__ == "__main__":
    unittest.main()
