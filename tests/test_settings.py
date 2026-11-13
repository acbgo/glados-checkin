import json
import tempfile
import unittest
from pathlib import Path

from checkin import (load_config,
                     merge_account_edit, save_config, parse_config)


class SettingsTests(unittest.TestCase):
    def test_json_save_keeps_other_keys_and_updates_accounts_and_pushplus(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.local.json'
            path.write_text(json.dumps({'OTHER': 'value', 'accounts': [
                {'name': 'old', 'domain': 'railgun.info', 'cookie': 'old-cookie'}], 'pushplus_token': 'old',
                                        'streamlit_admin_password': 'password',
                                        'notification_mode': 'fail_only'}), encoding='utf-8')
            accounts = [{'name': 'rail', 'domain': 'railgun.info', 'cookie': 'abc; x=y'}]
            save_config(path, accounts, 'new-token')
            values = load_config(path)
            self.assertEqual(values['accounts'], accounts)
            self.assertEqual(values['pushplus_token'], 'new-token')
            self.assertEqual(values['OTHER'], 'value')
            self.assertEqual(values['notification_mode'], 'fail_only')
            self.assertEqual(values['streamlit_admin_password'], 'password')

    def test_invalid_accounts_do_not_change_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.local.json'
            path.write_text('{"accounts": []}', encoding='utf-8')
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                save_config(path, [{'name': 'x', 'domain': 'wrong', 'cookie': 'secret'}], '')
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_json_does_not_echo_credentials(self):
        with self.assertRaises(ValueError) as caught:
            parse_config('{"accounts": ["secret-cookie",]}')
        self.assertNotIn('secret-cookie', str(caught.exception))

    def test_blank_credential_edits_preserve_existing_values(self):
        current = {'name': 'old', 'domain': 'glados.cloud', 'cookie': 'real-cookie', 'user_agent': 'Browser/1'}
        changed = merge_account_edit(current, name='new', domain='glados.cloud', cookie='', user_agent='')
        self.assertEqual(changed, {'name': 'new', 'domain': 'glados.cloud', 'cookie': 'real-cookie', 'user_agent': 'Browser/1'})

    def test_domain_change_requires_replacing_cookie_and_drops_old_user_agent(self):
        current = {'name': 'a', 'domain': 'railgun.info', 'cookie': 'rail-secret', 'user_agent': 'RailBrowser/1'}
        with self.assertRaisesRegex(ValueError, 'Cookie'):
            merge_account_edit(current, name='a', domain='glados.cloud', cookie='', user_agent='')
        changed = merge_account_edit(current, name='a', domain='glados.cloud', cookie='cloud-secret', user_agent='')
        self.assertEqual(changed, {'name': 'a', 'domain': 'glados.cloud', 'cookie': 'cloud-secret',
                                   'exchange_plan': 'off'})

    def test_account_editor_can_save_exchange_and_fallback_choices(self):
        current = {'name': 'cloud', 'domain': ['glados.cloud'], 'cookie': 'secret', 'exchange_plan': 'off'}
        changed = merge_account_edit(current, name='cloud', domain=['glados.cloud', 'glados.rocks'],
                                     cookie='', user_agent='',
                                     exchange_plan='plan200')
        self.assertEqual(changed['domain'], ['glados.cloud', 'glados.rocks'])
        self.assertEqual(changed['exchange_plan'], 'plan200')

if __name__ == '__main__':
    unittest.main()
