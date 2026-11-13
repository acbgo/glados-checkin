import shutil
import json
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest



class AppTests(unittest.TestCase):
    def test_management_page_requires_login_before_showing_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'app.py'
            shutil.copy2(Path(__file__).resolve().parents[1] / 'app.py', script)
            (Path(directory) / 'config.local.json').write_text(json.dumps({
                'accounts': [{'name': 'private-account', 'domain': 'railgun.info', 'cookie': 'secret'}],
                'pushplus_token': '',
                'notification_mode': 'always',
                'streamlit_admin_password': 'password',
            }), encoding='utf-8')
            app = AppTest.from_file(str(script)).run()
            self.assertFalse(app.exception)
            self.assertTrue(app.text_input)
            self.assertNotIn('private-account', ' '.join(item.value for item in app.markdown))
            app.text_input[0].set_value('wrong')
            app.button[0].click().run()
            self.assertNotIn('private-account', ' '.join(item.value for item in app.markdown))
            app.text_input[0].set_value('password')
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any('private-account' in item.label for item in app.expander))
            next(item for item in app.text_input if item.label == '新 Cookie（留空保留）').set_value('new-secret')
            next(item for item in app.button if item.label == '保存账号').click().run()
            self.assertFalse(app.exception)
            from checkin import load_config
            stored = load_config(Path(directory) / 'config.local.json')['accounts']
            self.assertEqual(stored[0]['cookie'], 'new-secret')
            self.assertEqual(next(item for item in app.text_input if item.label == '新 Cookie（留空保留）').value, '')


if __name__ == '__main__':
    unittest.main()
