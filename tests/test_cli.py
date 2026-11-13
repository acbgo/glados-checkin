import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_missing_json_config_exits_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'checkin.py'
            script.write_text(Path('checkin.py').read_text(encoding='utf-8'), encoding='utf-8')
            env = {key: value for key, value in os.environ.items() if key != 'GLADOS_CONFIG'}
            env['PYTHONIOENCODING'] = 'utf-8'
            result = subprocess.run([sys.executable, str(script)], env=env,
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('config.local.json', result.stdout)

    def test_invalid_json_secret_never_echoes_credentials(self):
        env = dict(os.environ, GLADOS_CONFIG='{"accounts":["top-secret",]}', PYTHONIOENCODING='utf-8')
        result = subprocess.run([sys.executable, 'checkin.py'], env=env,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('top-secret', result.stdout + result.stderr)

    def test_local_json_ignores_legacy_process_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'checkin.py'
            script.write_text(Path('checkin.py').read_text(encoding='utf-8'), encoding='utf-8')
            (Path(directory) / 'config.local.json').write_text('{"accounts": []}', encoding='utf-8')
            env = dict(os.environ, GLADOS_ACCOUNTS='[{"name":"bad","domain":"wrong","cookie":"secret"}]')
            env.pop('GLADOS_CONFIG', None)
            env['PYTHONIOENCODING'] = 'utf-8'
            result = subprocess.run([sys.executable, str(script)], env=env,
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('非空数组', result.stdout)

    def test_full_json_secret_is_validated_without_local_file(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'checkin.py'
            script.write_text(Path('checkin.py').read_text(encoding='utf-8'), encoding='utf-8')
            config = {'accounts': [{'name': 'rail', 'domain': 'railgun.info', 'cookie': 'secret'}],
                      'pushplus_token': '', 'notification_mode': 'invalid'}
            env = dict(os.environ, GLADOS_CONFIG=json.dumps(config), PYTHONIOENCODING='utf-8')
            result = subprocess.run([sys.executable, str(script)], env=env,
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('notification_mode', result.stdout)


if __name__ == '__main__':
    unittest.main()
