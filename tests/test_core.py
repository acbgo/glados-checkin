import hashlib
import json
import unittest

from checkin import Account, ConfigError, parse_accounts, parse_config, run_accounts, run_diagnostic


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeTransport:
    def __init__(self, replies):
        self.replies = {key: list(value) for key, value in replies.items()}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        key = (method, url)
        if key not in self.replies or not self.replies[key]:
            raise AssertionError(f'unexpected request: {key}')
        reply = self.replies[key].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class ConfigTests(unittest.TestCase):
    def test_points_display_uses_integer_and_exchange_icons(self):
        from checkin import _balance_details
        points, change, options = _balance_details({'code': 0, 'points': '147',
            'history': [{'change': '1.00000000'}], 'plans': {
                'plan100': {'points': 100, 'days': 10}, 'plan200': {'points': 200, 'days': 30}}})
        self.assertEqual(change, '+1')
        self.assertEqual(options, ('✅ 100分→10天：可兑换', '❌ 200分→30天：还差53分'))
        self.assertEqual(_balance_details({'code': 0, 'points': '147', 'history': [{'change': '-2.000'}]})[1], '-2')

    def test_railgun_already_checked_in_is_success_without_retry(self):
        account = Account('rail', 'railgun.info', 'cookie')
        http = FakeTransport({
            ('POST', 'https://railgun.info/api/user/checkin'): [FakeResponse({'code': 1, 'message': 'Already checked in today. Come back tomorrow.'})],
            ('GET', 'https://railgun.info/api/user/status'): [FakeResponse({'code': 0, 'data': {'leftDays': 110}})],
            ('GET', 'https://railgun.info/api/user/points'): [FakeResponse({'code': 0, 'points': '147'})],
        })
        waits = []
        report = run_accounts([account], transport=http, sleep=waits.append)
        self.assertTrue(report.success)
        self.assertEqual(report.results[0].message, '今日已签到')
        self.assertEqual(waits, [])

    def test_failure_summary_reports_safe_metadata_without_echoing_response(self):
        from checkin import _checkin_failure
        message = _checkin_failure({'code': 42, 'message': 'private-cookie-value'})
        self.assertIn('code=42', message)
        self.assertNotIn('private-cookie-value', message)
        self.assertIn('网站签到', _checkin_failure({'code': 1, 'message': 'please checkin via https://glados.cloud'}))

    def test_pushplus_card_escapes_account_data(self):
        from checkin import AccountResult, RunReport, _pushplus
        http = FakeTransport({('POST', 'https://www.pushplus.plus/send'): [FakeResponse({'code': 200})]})
        report = RunReport((AccountResult('<账号>', 'glados.cloud', True, '签到成功', '50', '3', '+1.00000000'),), False)
        self.assertTrue(_pushplus(report, 'token', http))
        payload = http.calls[0][2]['json']
        self.assertIn('1/1', payload['title'])
        self.assertIn('&lt;账号&gt;', payload['content'])
        self.assertIn('+1.00000000', payload['content'])
        self.assertNotIn('<账号>', payload['content'])

    def test_domain_array_preserves_requested_order(self):
        from checkin import _domains_for
        account = parse_accounts(json.dumps([{'name': 'cloud', 'domain': ['glados.rocks', 'glados.cloud'],
                                               'cookie': 'secret'}]))[0]
        self.assertEqual(_domains_for(account), ('glados.rocks', 'glados.cloud'))

    def test_domain_array_rejects_mixed_services_and_duplicates(self):
        for domains in ([], ['railgun.info', 'glados.cloud'], ['glados.cloud', 'glados.cloud']):
            with self.subTest(domains=domains), self.assertRaises(ConfigError):
                parse_accounts(json.dumps([{'name': 'a', 'domain': domains, 'cookie': 'secret'}]))

    def test_cloud_account_accepts_fallback_exchange_cookie_export_and_copied_user_agent(self):
        raw = json.dumps([{
            'name': 'cloud', 'domain': 'glados.rocks',
            'cookie': [{'name': 'gld:sess', 'value': 'abc'}, {'name': 'gld:sess.sig', 'value': 'sig'}],
            'user_agent': 'User-Agent: "Mozilla/5.0 Chrome/120.0"',
            'domain': ['glados.rocks', 'glados.cloud'], 'exchange_plan': 'plan100',
        }])
        account = parse_accounts(raw)[0]
        self.assertEqual(account.cookie, 'gld:sess=abc; gld:sess.sig=sig')
        self.assertEqual(account.user_agent, 'Mozilla/5.0 Chrome/120.0')
        self.assertEqual(account.domains, ('glados.rocks', 'glados.cloud'))
        self.assertEqual(account.exchange_plan, 'plan100')

    def test_railgun_accepts_exchange_and_ignores_obsolete_fallback(self):
        account = parse_accounts(json.dumps([{'name': 'rail', 'domain': ['railgun.info'],
            'cookie': 'secret', 'domain_fallback': True, 'exchange_plan': 'plan500'}]))[0]
        self.assertEqual(account.exchange_plan, 'plan500')

    def test_notification_mode_rejects_invalid_json_value(self):
        with self.assertRaisesRegex(ConfigError, 'notification_mode'):
            parse_config(json.dumps({'accounts': [{'name': 'rail', 'domain': 'railgun.info',
                                                  'cookie': 'secret'}], 'notification_mode': 'sometimes'}))

    def test_user_agent_rejects_non_latin_characters_without_echo(self):
        with self.assertRaises(ConfigError) as caught:
            parse_accounts(json.dumps([{'name': 'cloud', 'domain': 'glados.cloud',
                                        'cookie': 'secret', 'user_agent': '浏览器'}]))
        self.assertNotIn('浏览器', str(caught.exception))

    def test_two_domains_keep_independent_credentials(self):
        raw = json.dumps([
            {'name': 'rail', 'domain': 'railgun.info', 'cookie': 'rail-secret'},
            {'name': 'cloud', 'domain': 'glados.cloud', 'cookie': 'cloud-secret', 'user_agent': 'Browser/1'},
        ])
        accounts = parse_accounts(raw)
        self.assertEqual([(a.name, a.domain, a.cookie, a.user_agent) for a in accounts], [
            ('rail', 'railgun.info', 'rail-secret', None),
            ('cloud', 'glados.cloud', 'cloud-secret', 'Browser/1'),
        ])

    def test_unsupported_domain_is_rejected_before_requests(self):
        with self.assertRaisesRegex(ConfigError, 'domain'):
            parse_accounts('[{"name":"bad","domain":"evil.example","cookie":"secret"}]')

    def test_duplicate_names_and_empty_cookie_are_rejected_without_echo(self):
        for raw in (
            '[{"name":"same","domain":"railgun.info","cookie":"secret"},{"name":"same","domain":"glados.cloud","cookie":"other"}]',
            '[{"name":"only","domain":"railgun.info","cookie":""}]',
        ):
            with self.subTest(raw=raw), self.assertRaises(ConfigError) as caught:
                parse_accounts(raw)
            self.assertNotIn('secret', str(caught.exception))

    def test_control_characters_in_display_name_are_rejected(self):
        with self.assertRaisesRegex(ConfigError, 'name'):
            parse_accounts('[{"name":"bad\\nlog","domain":"railgun.info","cookie":"secret"}]')

    def test_names_are_unique_after_trimming(self):
        with self.assertRaisesRegex(ConfigError, 'name'):
            parse_accounts('[{"name":"same","domain":"railgun.info","cookie":"one"},{"name":" same ","domain":"glados.cloud","cookie":"two"}]')

    def test_malformed_domain_type_is_a_configuration_error(self):
        with self.assertRaisesRegex(ConfigError, 'domain'):
            parse_accounts('[{"name":"bad","domain":[],"cookie":"secret"}]')


class CheckinTests(unittest.TestCase):
    def test_cloud_fallback_gets_challenge_then_posts_to_same_working_host(self):
        account = Account('cloud', 'glados.cloud', 'cookie', domains=('glados.cloud', 'glados.rocks', 'glados.network'))
        http = FakeTransport({
            ('GET', 'https://glados.cloud/api/user/checkin/challenge'): [FakeResponse({}, 503)],
            ('GET', 'https://glados.rocks/api/user/checkin/challenge'): [FakeResponse({'code': 0, 'nonce': 'abc', 'ttl': 300000})],
            ('POST', 'https://glados.rocks/api/user/checkin'): [FakeResponse({'code': 1, 'message': 'Checkin! Got 1 Point'})],
            ('GET', 'https://glados.rocks/api/user/status'): [FakeResponse({'code': 0, 'data': {'leftDays': 4}})],
            ('GET', 'https://glados.rocks/api/user/points'): [FakeResponse({'code': 0, 'points': '8'})],
        })
        report = run_accounts([account], transport=http, sleep=lambda _: None)
        self.assertTrue(report.success)
        self.assertEqual(report.results[0].domain, 'glados.rocks')
        self.assertFalse(any(method == 'POST' and 'glados.cloud' in url for method, url, _ in http.calls))

    def test_cloud_exchange_uses_form_and_refreshes_balance(self):
        account = Account('cloud', 'glados.cloud', 'cookie', exchange_plan='plan100')
        http = FakeTransport({
            ('GET', 'https://glados.cloud/api/user/checkin/challenge'): [FakeResponse({'code': 0, 'nonce': 'abc', 'ttl': 300000})],
            ('POST', 'https://glados.cloud/api/user/checkin'): [FakeResponse({'code': 0, 'message': 'Checkin! Got 1 Point'})],
            ('GET', 'https://glados.cloud/api/user/status'): [FakeResponse({'code': 0, 'data': {'leftDays': 4}}), FakeResponse({'code': 0, 'data': {'leftDays': 14}})],
            ('GET', 'https://glados.cloud/api/user/points'): [FakeResponse({'code': 0, 'points': '120', 'history': [{'change': '1'}], 'plans': {'plan100': {'points': 100, 'days': 10}}}), FakeResponse({'code': 0, 'points': '20'})],
            ('POST', 'https://glados.cloud/api/user/exchange'): [FakeResponse({'code': 0, 'message': 'ok'})],
        })
        report = run_accounts([account], transport=http, sleep=lambda _: None)
        self.assertEqual(report.results[0].points, '20')
        self.assertEqual(report.results[0].left_days, '14')
        self.assertTrue(report.results[0].exchange_attempted)
        self.assertEqual(report.results[0].exchange_options, ())
        exchange = next(kwargs for method, url, kwargs in http.calls if url.endswith('/api/user/exchange'))
        self.assertEqual(exchange['data'], {'planType': 'plan100'})
        self.assertNotIn('Content-Type', exchange['headers'])

    def test_railgun_exchange_posts_to_its_own_domain(self):
        account = parse_accounts(json.dumps([{'name': 'rail', 'domain': ['railgun.info'],
            'cookie': 'secret', 'exchange_plan': 'plan100'}]))[0]
        http = FakeTransport({
            ('POST', 'https://railgun.info/api/user/checkin'): [FakeResponse({'code': 0})],
            ('GET', 'https://railgun.info/api/user/status'): [FakeResponse({'code': 0, 'data': {'leftDays': 4}})] * 2,
            ('GET', 'https://railgun.info/api/user/points'): [FakeResponse({'code': 0, 'points': '120'}), FakeResponse({'code': 0, 'points': '20'})],
            ('POST', 'https://railgun.info/api/user/exchange'): [FakeResponse({'code': 0})],
        })
        report = run_accounts([account], transport=http, sleep=lambda _: None)
        self.assertTrue(report.results[0].exchange_attempted)
        exchange = next(kwargs for method, url, kwargs in http.calls if url.endswith('/api/user/exchange'))
        self.assertEqual(exchange['data'], {'planType': 'plan100'})

    def test_fail_only_pushes_failure_but_skips_quiet_success(self):
        success = Account('rail', 'railgun.info', 'cookie')
        base = {
            ('POST', 'https://railgun.info/api/user/checkin'): [FakeResponse({'code': 0, 'message': 'ok'})],
            ('GET', 'https://railgun.info/api/user/status'): [FakeResponse({'code': 0, 'data': {'leftDays': 4}})],
            ('GET', 'https://railgun.info/api/user/points'): [FakeResponse({'code': 0, 'points': '7'})],
        }
        quiet = FakeTransport(base)
        report = run_accounts([success], push_token='token', notification_mode='fail_only', transport=quiet, sleep=lambda _: None)
        self.assertTrue(report.success)
        self.assertFalse(report.notification_sent)
        self.assertFalse(any(url.endswith('/send') for _, url, _ in quiet.calls))
        failed = FakeTransport({
            ('POST', 'https://railgun.info/api/user/checkin'): [FakeResponse({}, 403)],
            ('POST', 'https://www.pushplus.plus/send'): [FakeResponse({'code': 200})],
        })
        report = run_accounts([success], push_token='token', notification_mode='fail_only', transport=failed, sleep=lambda _: None)
        self.assertFalse(report.success)
        self.assertTrue(report.notification_sent)
        push = next(kwargs for method, url, kwargs in failed.calls if url.endswith('/send'))
        self.assertFalse(push['allow_redirects'])

    def test_auth_denial_does_not_try_other_glados_domain(self):
        account = Account('cloud', 'glados.cloud', 'cookie', domains=('glados.cloud', 'glados.rocks', 'glados.network'))
        http = FakeTransport({
            ('GET', 'https://glados.cloud/api/user/checkin/challenge'): [FakeResponse({}, 403)],
        })
        report = run_accounts([account], transport=http, sleep=lambda _: None)
        self.assertFalse(report.success)
        self.assertEqual(len(http.calls), 1)

    def test_diagnostic_only_reads_status_and_never_posts(self):
        account = Account('cloud', 'glados.cloud', 'cookie', exchange_plan='plan100')
        http = FakeTransport({
            ('GET', 'https://glados.cloud/api/user/status'): [FakeResponse({'code': 0, 'data': {'leftDays': 4}})],
        })
        report = run_diagnostic([account], transport=http)
        self.assertTrue(report.success)
        self.assertEqual([method for method, _, _ in http.calls], ['GET'])

    def test_railgun_and_cloud_use_only_their_own_host_and_cookie(self):
        rail = Account('rail', 'railgun.info', 'rail-cookie')
        cloud = Account('cloud', 'glados.cloud', 'cloud-cookie', 'Browser/1')
        replies = {
            ('POST', 'https://railgun.info/api/user/checkin'): [FakeResponse({'code': 0, 'message': 'Checkin! Got 1 Point'})],
            ('GET', 'https://railgun.info/api/user/status'): [FakeResponse({'code': 0, 'data': {'leftDays': 10, 'email': 'r@example.com'}})],
            ('GET', 'https://railgun.info/api/user/points'): [FakeResponse({'code': 0, 'points': '15.5'})],
            ('GET', 'https://glados.cloud/api/user/checkin/challenge'): [FakeResponse({'code': 0, 'nonce': 'abc', 'ttl': 300000})],
            ('POST', 'https://glados.cloud/api/user/checkin'): [FakeResponse({'code': 0, 'message': "Today's observation logged"})],
            ('GET', 'https://glados.cloud/api/user/status'): [FakeResponse({'code': 0, 'data': {'leftDays': 20, 'email': 'c@example.com'}})],
            ('GET', 'https://glados.cloud/api/user/points'): [FakeResponse({'code': 0, 'points': '30.1'})],
        }
        http = FakeTransport(replies)
        report = run_accounts([rail, cloud], transport=http, sleep=lambda _: None)
        self.assertTrue(report.success)
        self.assertEqual([(r.name, r.points, r.left_days) for r in report.results], [('rail', '15', '10'), ('cloud', '30', '20')])
        for _, url, kwargs in http.calls:
            expected = rail if 'railgun.info' in url else cloud
            self.assertEqual(kwargs['headers']['Cookie'], expected.cookie)
            self.assertFalse(kwargs['allow_redirects'])
        cloud_post = next(kwargs for method, url, kwargs in http.calls if method == 'POST' and url.endswith('/api/user/checkin') and 'glados.cloud' in url)
        self.assertEqual(cloud_post['json']['proof'], hashlib.sha256(b'abc.glados-observe-v1').hexdigest())
        self.assertEqual(cloud_post['headers']['User-Agent'], 'Browser/1')
        self.assertEqual(cloud_post['headers']['Content-Type'], 'application/json;charset=UTF-8')
        self.assertEqual(cloud_post['headers']['Sec-Fetch-Site'], 'same-origin')

    def test_auth_denial_stops_retries_but_other_account_runs(self):
        denied = Account('denied', 'glados.cloud', 'one')
        allowed = Account('allowed', 'railgun.info', 'two')
        http = FakeTransport({
            ('GET', 'https://glados.cloud/api/user/checkin/challenge'): [FakeResponse({}, 403)],
            ('POST', 'https://railgun.info/api/user/checkin'): [FakeResponse({'code': 1, 'message': 'Checkin Repeats! Please Try Tomorrow'})],
            ('GET', 'https://railgun.info/api/user/status'): [FakeResponse({'code': 0, 'data': {'leftDays': 3}})],
            ('GET', 'https://railgun.info/api/user/points'): [FakeResponse({'code': 0, 'points': '5'})],
        })
        report = run_accounts([denied, allowed], transport=http, sleep=lambda _: None)
        self.assertFalse(report.success)
        self.assertEqual([r.success for r in report.results], [False, True])
        self.assertEqual(sum('glados.cloud' in url for _, url, _ in http.calls), 1)

    def test_transient_failure_is_retried_without_duplicate_notification(self):
        account = Account('rail', 'railgun.info', 'cookie')
        http = FakeTransport({
            ('POST', 'https://railgun.info/api/user/checkin'): [FakeResponse({}, 503), FakeResponse({'code': 0, 'message': 'Checkin! Got 1 Point'})],
            ('GET', 'https://railgun.info/api/user/status'): [FakeResponse({'code': 0, 'data': {'leftDays': 4}})],
            ('GET', 'https://railgun.info/api/user/points'): [FakeResponse({'code': 0, 'points': '7'})],
            ('POST', 'https://www.pushplus.plus/send'): [FakeResponse({'code': 200})],
        })
        waits = []
        progress = []
        report = run_accounts([account], push_token='token', transport=http, sleep=waits.append, progress=progress.append)
        self.assertEqual(waits, [10])
        self.assertTrue(any('10 秒' in line for line in progress))
        self.assertTrue(report.success)
        self.assertTrue(report.notification_sent)
        self.assertEqual(sum(url.endswith('/send') for _, url, _ in http.calls), 1)

    def test_invalid_challenge_does_not_post_checkin(self):
        account = Account('cloud', 'glados.cloud', 'cookie')
        http = FakeTransport({
            ('GET', 'https://glados.cloud/api/user/checkin/challenge'): [FakeResponse({'code': 0, 'nonce': '', 'ttl': 300000})] * 3,
        })
        report = run_accounts([account], transport=http, sleep=lambda _: None)
        self.assertFalse(report.success)
        self.assertEqual(len(http.calls), 3)
        self.assertTrue(all(method == 'GET' for method, _, _ in http.calls))

    def test_zero_attempts_still_fail_when_request_fails(self):
        account = Account('rail', 'railgun.info', 'cookie')
        http = FakeTransport({
            ('POST', 'https://railgun.info/api/user/checkin'): [FakeResponse({}, 503)],
        })
        report = run_accounts([account], transport=http, sleep=lambda _: None, max_attempts=0)
        self.assertFalse(report.success)

    def test_direct_account_cannot_target_unapproved_host(self):
        with self.assertRaises(ConfigError):
            run_accounts([Account('wrong', 'example.com', 'secret')], transport=FakeTransport({}))


if __name__ == '__main__':
    unittest.main()
