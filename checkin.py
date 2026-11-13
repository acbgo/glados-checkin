"""多域名签到、配置读写及命令行入口。"""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
import time
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Sequence

import requests


GLADOS_DOMAINS = ('glados.cloud', 'glados.rocks', 'glados.network')
SUPPORTED_DOMAINS = frozenset(('railgun.info',) + GLADOS_DOMAINS)
EXCHANGE_PLANS = {'plan100': (100, 10), 'plan200': (200, 30), 'plan500': (500, 100)}
NOTIFICATION_MODES = frozenset({'always', 'fail_only'})
CHECKIN_CHALLENGE_DELAY = 2.7
DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)


class ConfigError(ValueError):
    """配置无效；错误文本不得包含凭据。"""


def _beijing_time() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def log(message: str) -> None:
    print(f'[{_beijing_time()} 北京时间] {message}', flush=True)


@dataclass(frozen=True)
class Account:
    name: str
    domain: str
    cookie: str
    user_agent: str | None = None
    exchange_plan: str = 'off'
    domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountResult:
    name: str
    domain: str
    success: bool
    message: str
    points: str = '未知'
    left_days: str = '未知'
    points_change: str = '未知'
    exchange_options: tuple[str, ...] = ()
    exchange_status: str = ''
    exchange_attempted: bool = False


@dataclass(frozen=True)
class RunReport:
    results: tuple[AccountResult, ...]
    notification_sent: bool

    @property
    def success(self) -> bool:
        return bool(self.results) and all(result.success for result in self.results)


def parse_accounts(raw: str) -> list[Account]:
    try:
        entries = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError('accounts 不是有效 JSON') from None
    if not isinstance(entries, list) or not entries:
        raise ConfigError('accounts 必须是非空数组')
    accounts: list[Account] = []
    names: set[str] = set()
    for index, item in enumerate(entries, 1):
        if not isinstance(item, dict):
            raise ConfigError(f'账号 {index} 必须是对象')
        name, domain, cookie = (item.get(field) for field in ('name', 'domain', 'cookie'))
        configured_domains = domain if isinstance(domain, list) else [domain]
        if (not configured_domains or any(not isinstance(d, str) or d not in SUPPORTED_DOMAINS for d in configured_domains)
                or len(set(configured_domains)) != len(configured_domains)
                or ('railgun.info' in configured_domains and len(configured_domains) > 1)):
            raise ConfigError(f'账号 {index} 的 domain 无效')
        domain = configured_domains[0]
        if not isinstance(name, str) or not name.strip() or _has_control(name):
            raise ConfigError(f'账号 {index} 的 name 无效')
        normalized_name = name.strip()
        if normalized_name in names:
            raise ConfigError(f'账号 {index} 的 name 重复')
        if not isinstance(domain, str) or domain not in SUPPORTED_DOMAINS:
            raise ConfigError(f'账号 {index} 的 domain 不受支持')
        cookie = normalize_cookie(cookie)
        if not cookie or _has_control(cookie):
            raise ConfigError(f'账号 {index} 的 cookie 无效')
        user_agent = item.get('user_agent')
        if user_agent is not None:
            user_agent = normalize_user_agent(user_agent, index)
        plan = item.get('exchange_plan', 'off')
        if not isinstance(plan, str) or plan not in ('off', *EXCHANGE_PLANS):
            raise ConfigError(f'账号 {index} 的 exchange_plan 无效')
        names.add(normalized_name)
        accounts.append(Account(normalized_name, domain, cookie, user_agent or None, plan,
                                tuple(configured_domains) if isinstance(item.get('domain'), list) else ()))
    return accounts


def normalize_cookie(raw) -> str:
    if isinstance(raw, list):
        pairs = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get('name'), str) and item.get('value') is not None:
                pairs.append(f"{item['name']}={item['value']}")
        return '; '.join(pairs)
    if isinstance(raw, dict):
        token = raw.get('token')
        return f'koa:sess={token}' if isinstance(token, str) and token else ''
    if not isinstance(raw, str):
        return ''
    value = re.sub(r'^cookie\s*:\s*', '', raw.strip(), flags=re.IGNORECASE)
    if value.startswith(('[', '{')):
        try:
            return normalize_cookie(json.loads(value))
        except (TypeError, ValueError):
            return ''
    if value.count('.') == 2 and '=' not in value and len(value) > 50:
        return 'koa:sess=' + value
    return value


def normalize_user_agent(raw, index: int) -> str:
    if not isinstance(raw, str) or _has_control(raw):
        raise ConfigError(f'账号 {index} 的 user_agent 无效')
    value = raw.strip()
    if not value:
        return ''
    try:
        value.encode('latin-1')
    except UnicodeEncodeError:
        raise ConfigError(f'账号 {index} 的 user_agent 含不支持的字符') from None
    if value.lower().startswith('user-agent:'):
        value = value[len('user-agent:'):].strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].strip()
    elif len(value) >= 2 and value[0] == value[-1] == "'" and ' ' in value:
        value = value[1:-1].strip()
    if value.lower().startswith('user-agent:') or value.startswith('"') or value.endswith('"') or not value:
        raise ConfigError(f'账号 {index} 的 user_agent 格式无效')
    return value


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


class RequestFailure(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _headers(account: Account, domain: str | None = None, form: bool = False) -> dict[str, str]:
    domain = domain or account.domain
    origin = f'https://{domain}'
    headers = {
        'Cookie': account.cookie,
        'User-Agent': account.user_agent or DEFAULT_USER_AGENT,
        'Origin': origin,
        'Referer': f'{origin}/console/checkin',
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json;charset=UTF-8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
    }
    ua = headers['User-Agent']
    chrome = re.search(r'(?:Chrome|Chromium)/(\d+)', ua)
    if chrome and domain in GLADOS_DOMAINS:
        platform = next((name for text, name in (
            ('Windows', 'Windows'), ('Macintosh', 'macOS'), ('Android', 'Android'), ('Linux', 'Linux')
        ) if text in ua), 'Unknown')
        headers.update({
            'Sec-CH-UA': f'"Chromium";v="{chrome.group(1)}", "Google Chrome";v="{chrome.group(1)}", "Not_A Brand";v="99"',
            'Sec-CH-UA-Mobile': '?1' if 'Mobile' in ua else '?0',
            'Sec-CH-UA-Platform': f'"{platform}"',
        })
    if form:
        headers.pop('Content-Type', None)
    return headers


def _request(transport, account: Account, method: str, path: str, *, data=None,
             domain: str | None = None, form: bool = False) -> dict:
    domain = domain or account.domain
    url = f'https://{domain}{path}'
    try:
        payload_arg = {'data': data} if form else {'json': data} if data is not None else {}
        response = transport.request(method, url, headers=_headers(account, domain, form), timeout=10,
                                     allow_redirects=False,
                                     **payload_arg)
        if response.status_code in (401, 403):
            raise RequestFailure(f'认证被拒绝（HTTP {response.status_code}）', False)
        if response.status_code != 200:
            raise RequestFailure(f'服务端响应异常（HTTP {response.status_code}）', response.status_code >= 500 or response.status_code == 429)
        payload = response.json()
    except RequestFailure:
        raise
    except requests.exceptions.InvalidHeader:
        raise RequestFailure('请求头格式无效，请检查 Cookie 和 User-Agent', False) from None
    except requests.Timeout:
        raise RequestFailure('请求超时，今日签到状态未知') from None
    except requests.RequestException:
        raise RequestFailure('网络请求失败，今日签到状态未知') from None
    except ValueError:
        raise RequestFailure('服务端响应格式无效，今日签到状态未知') from None
    if not isinstance(payload, dict):
        raise RequestFailure('服务端响应格式无效')
    reason = str(payload.get('reason', '')).lower()
    message = str(payload.get('message', '')).lower()
    if payload.get('code') == -2 or reason == 'device-mismatch' or any(marker in message for marker in (
        '没有权限', 'permission', 'unauthorized', 'automated check-in detected'
    )):
        raise RequestFailure('会话或设备认证被拒绝', False)
    return payload


def _domains_for(account: Account, preferred: str | None = None) -> tuple[str, ...]:
    domains = account.domains or (account.domain,)
    if preferred in domains:
        domains = (preferred,) + tuple(domain for domain in domains if domain != preferred)
    return domains


def _request_with_fallback(transport, account: Account, method: str, path: str,
                           preferred: str | None = None) -> tuple[dict, str]:
    last_error = RequestFailure('请求失败，今日签到状态未知')
    for domain in _domains_for(account, preferred):
        try:
            return _request(transport, account, method, path, domain=domain), domain
        except RequestFailure as exc:
            last_error = exc
            if not exc.retryable:
                break
    raise last_error


def _is_checkin_success(payload: dict) -> bool:
    message = str(payload.get('message', '')).lower()
    return payload.get('code') == 0 or any(marker in message for marker in (
        'checkin! got', 'checkin repeats!', 'already checked in today', "today's observation logged"
    ))


def _checkin_failure(payload: dict) -> str:
    message = str(payload.get('message', '')).lower()
    reason = str(payload.get('reason', '')).lower()
    if 'please checkin via' in message:
        label = '服务端要求通过网站签到'
    elif reason == 'checkin-challenge-expired':
        label = '签到验证已过期'
    elif reason == 'checkin-challenge-unavailable':
        label = '签到验证不可用'
    else:
        label = '服务端未确认签到'
    code = payload.get('code')
    if type(code) is int and -999 <= code <= 999:
        label += f'（code={code}）'
    return label


def _checkin_once(account: Account, transport, sleep: Callable[[float], None], preferred: str | None = None) -> tuple[str, str]:
    if account.domain == 'railgun.info':
        result = _request(transport, account, 'POST', '/api/user/checkin', data={'token': 'railgun.info'})
        domain = account.domain
    else:
        challenge, domain = _request_with_fallback(transport, account, 'GET', '/api/user/checkin/challenge', preferred)
        nonce = challenge.get('nonce')
        ttl = challenge.get('ttl', 300000)
        if (challenge.get('code') != 0 or not isinstance(nonce, str) or not 0 < len(nonce) <= 4096
                or type(ttl) not in (int, float) or not math.isfinite(ttl)
                or ttl <= CHECKIN_CHALLENGE_DELAY * 1000):
            raise RequestFailure('签到验证不可用')
        received_at = time.monotonic()
        proof = hashlib.sha256((nonce + '.glados-observe-v1').encode('utf-8')).hexdigest()
        sleep(CHECKIN_CHALLENGE_DELAY)
        if (time.monotonic() - received_at) * 1000 >= ttl:
            raise RequestFailure('签到验证已过期')
        result = _request(transport, account, 'POST', '/api/user/checkin', data={
            'token': domain, 'nonce': nonce, 'proof': proof,
        }, domain=domain)
    if not _is_checkin_success(result):
        raise RequestFailure(_checkin_failure(result))
    message = str(result.get('message', '签到成功'))
    # API 文本可包含账号信息，控制台和推送只使用固定结果描述。
    return ('今日已签到' if ('repeat' in message.lower() or 'already checked in today' in message.lower()) else '签到成功'), domain


def _balance_details(balance: dict) -> tuple[str, str, tuple[str, ...]]:
    if balance.get('code') != 0 or balance.get('points') is None:
        return '未知', '未知', ()
    points = str(balance['points']).split('.')[0]
    points_change = '未知'
    history = balance.get('history')
    if isinstance(history, list) and history and isinstance(history[0], dict):
        try:
            change = Decimal(str(history[0].get('change', '')))
            if change.is_finite():
                rounded = int(change.to_integral_value(rounding=ROUND_HALF_UP))
                points_change = f'{rounded:+d}' if rounded else '0'
        except (InvalidOperation, ValueError, OverflowError):
            pass
    lines = []
    plans = balance.get('plans')
    if isinstance(plans, dict):
        for item in plans.values():
            if isinstance(item, dict):
                try:
                    need = int(item['points'])
                    days = int(item['days'])
                    have = int(float(points))
                except (KeyError, TypeError, ValueError):
                    continue
                lines.append((f'✅ {need}分→{days}天：可兑换' if have >= need
                              else f'❌ {need}分→{days}天：还差{need - have}分'))
    return points, points_change, tuple(lines)


def _account_result(account: Account, transport, sleep: Callable[[float], None], max_attempts: int, retry_delay: float, progress=None) -> AccountResult:
    message = '签到失败'
    attempts = max(1, max_attempts)
    for attempt in range(attempts):
        try:
            domains = _domains_for(account)
            message, domain = _checkin_once(account, transport, sleep, domains[attempt % len(domains)])
            break
        except RequestFailure as exc:
            message = str(exc)
            if not exc.retryable or attempt == attempts - 1:
                return AccountResult(account.name, account.domain, False, message)
            if progress:
                progress(f'⚠️ {account.name} 第 {attempt + 1}/{attempts} 次签到失败：{message}，{retry_delay:g} 秒后重试')
            sleep(max(0, retry_delay))

    points = left_days = points_change = '未知'
    exchange_options: tuple[str, ...] = ()
    exchange_status = ''
    exchange_attempted = False
    try:
        status, domain = _request_with_fallback(transport, account, 'GET', '/api/user/status', domain)
        if status.get('code') == 0 and isinstance(status.get('data'), dict):
            value = status['data'].get('leftDays')
            if value is not None:
                left_days = str(value).split('.')[0]
    except RequestFailure:
        pass
    try:
        balance, domain = _request_with_fallback(transport, account, 'GET', '/api/user/points', domain)
        points, points_change, exchange_options = _balance_details(balance)
    except RequestFailure:
        pass

    if account.exchange_plan != 'off':
        need, days = EXCHANGE_PLANS[account.exchange_plan]
        try:
            have = int(float(points))
        except (TypeError, ValueError):
            exchange_status = '积分未知，跳过兑换'
        else:
            if have < need:
                exchange_status = f'积分不足（{have}/{need}），未兑换'
            else:
                exchange_attempted = True
                try:
                    exchanged = _request(transport, account, 'POST', '/api/user/exchange',
                                         data={'planType': account.exchange_plan}, domain=domain, form=True)
                    if exchanged.get('code') != 0:
                        exchange_status = '兑换失败（服务端未确认）'
                    else:
                        exchange_status = f'兑换成功：{need}分→{days}天'
                        try:
                            status, domain = _request_with_fallback(transport, account, 'GET', '/api/user/status', domain)
                            if status.get('code') == 0 and isinstance(status.get('data'), dict):
                                left_days = str(status['data'].get('leftDays', left_days)).split('.')[0]
                            balance, domain = _request_with_fallback(transport, account, 'GET', '/api/user/points', domain)
                            points, points_change, exchange_options = _balance_details(balance)
                        except RequestFailure:
                            pass
                except RequestFailure as exc:
                    exchange_status = f'兑换失败：{exc}'
    return AccountResult(account.name, domain, True, message, points, left_days,
                         points_change, exchange_options, exchange_status, exchange_attempted)


def _pushplus(report: RunReport, token: str, transport) -> bool:
    success_count = sum(result.success for result in report.results)
    title = f'GLaDOS签到：{"本次运行成功" if report.success else "本次运行失败"} {success_count}/{len(report.results)}'
    lines = []
    for result in report.results:
        lines.append('<div style="border:2px solid #333;padding:15px;margin-bottom:15px;border-radius:10px;background:#fff;">')
        lines.append(f'<h6 style="margin:0 0 15px;color:#333;border-bottom:2px solid #333;padding-bottom:8px;">👤 {html.escape(result.name)} · {html.escape(result.domain)}</h6>')
        lines.append(f'<p style="margin:8px 0;color:#000;font-size:16px;"><b>当前积分:</b> <span style="color:#e74c3c;font-size:16px;font-weight:bold;">{html.escape(result.points)}</span></p>')
        lines.append(f'<p style="margin:8px 0;color:#000;font-size:16px;"><b>剩余天数:</b> {html.escape(result.left_days)} 天</p>')
        lines.append(f'<p style="margin:8px 0;color:#000;font-size:16px;"><b>签到结果:</b> {html.escape(result.message)}</p>')
        if result.points_change != '未知':
            lines.append(f'<p>最近积分变化：{html.escape(str(result.points_change))}</p>')
        if result.exchange_options:
            lines.append('<div style="margin-top:15px;padding:12px;background:#f0f0f0;border-radius:8px;border:1px solid #ccc;"><b>🎁 兑换选项:</b><br>' + '<br>'.join(html.escape(option) for option in result.exchange_options) + '</div>')
        if result.exchange_status:
            lines.append(f'<p>自动兑换：{html.escape(result.exchange_status)}</p>')
        lines.append('</div>')
    if not report.success:
        lines.append('<p>失败账号的今日签到状态未知，请结合官网签到记录核对。</p>')
    lines.append(f'<small>时间（北京时间 UTC+8）: {_beijing_time()}</small>')
    try:
        response = transport.request('POST', 'https://www.pushplus.plus/send', timeout=10,
                                     allow_redirects=False, json={
            'token': token,
            'title': title,
            'content': ''.join(lines),
            'template': 'html',
        })
        if response.status_code != 200:
            return False
        payload = response.json()
        return isinstance(payload, dict) and payload.get('code') in (0, 200)
    except (requests.RequestException, ValueError):
        return False


def run_accounts(accounts: Sequence[Account], *, push_token: str = '', transport=None,
                 sleep: Callable[[float], None] = time.sleep, max_attempts: int = 3,
                 retry_delay: float = 10, notification_mode: str = 'always', progress=None) -> RunReport:
    if not accounts:
        raise ConfigError('至少需要一个账号')
    for index, account in enumerate(accounts, 1):
        if not isinstance(account.domain, str) or account.domain not in SUPPORTED_DOMAINS:
            raise ConfigError(f'账号 {index} 的 domain 不受支持')
    if notification_mode not in NOTIFICATION_MODES:
        raise ConfigError('notification_mode 只能是 always 或 fail_only')
    # requests.request 使用独立会话，不会在账号之间共享 Cookie jar。
    transport = transport or requests
    results = []
    for account in accounts:
        if progress:
            progress(f'开始处理 {account.name} · {account.domain} | 浏览器 User-Agent: {"已配置" if account.user_agent else "未配置，使用默认值"}')
        result = _account_result(account, transport, sleep, max_attempts, retry_delay, progress)
        results.append(result)
        if progress:
            progress(f'{"✅" if result.success else "❌"} {result.name} | 域名: {result.domain} | 积分: {result.points} | 天数: {result.left_days} | 结果: {result.message}')
            if result.exchange_status:
                progress(f'🎁 {result.name} 自动兑换: {result.exchange_status}')
    results = tuple(results)
    report = RunReport(results, False)
    should_notify = notification_mode == 'always' or not report.success or any(result.exchange_attempted for result in results)
    sent = _pushplus(report, push_token, transport) if push_token and should_notify else False
    return RunReport(results, sent)


def run_diagnostic(accounts: Sequence[Account], *, transport=None) -> RunReport:
    if not accounts:
        raise ConfigError('至少需要一个账号')
    transport = transport or requests
    results = []
    for account in accounts:
        try:
            payload, domain = _request_with_fallback(transport, account, 'GET', '/api/user/status')
            authenticated = payload.get('code') == 0 and isinstance(payload.get('data'), dict) and bool(payload['data'])
            results.append(AccountResult(account.name, domain, authenticated,
                                         '登录状态查询成功' if authenticated else '登录状态查询失败'))
        except RequestFailure as exc:
            results.append(AccountResult(account.name, account.domain, False, str(exc)))
    return RunReport(tuple(results), False)

def parse_config(raw: str) -> dict:
    try:
        config = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError('配置文件不是有效 JSON') from None
    if not isinstance(config, dict):
        raise ConfigError('配置 JSON 必须是对象')
    accounts = config.get('accounts')
    parse_accounts(json.dumps(accounts, ensure_ascii=False))
    if not isinstance(config.get('pushplus_token', ''), str):
        raise ConfigError('pushplus_token 必须是字符串')
    if not isinstance(config.get('notification_mode', 'always'), str) or config.get('notification_mode', 'always') not in NOTIFICATION_MODES:
        raise ConfigError('notification_mode 只能是 always 或 fail_only')
    if not isinstance(config.get('streamlit_admin_password', ''), str):
        raise ConfigError('streamlit_admin_password 必须是字符串')
    return config


def load_config(path: str | Path) -> dict:
    try:
        return parse_config(Path(path).read_text(encoding='utf-8'))
    except OSError as exc:
        raise ConfigError('无法读取 config.local.json') from None


def merge_account_edit(current: dict, *, name: str, domain: str, cookie: str,
                       user_agent: str,
                       exchange_plan: str | None = None) -> dict:
    old_primary = current['domain'][0] if isinstance(current['domain'], list) else current['domain']
    new_primary = domain[0] if isinstance(domain, list) and domain else domain
    if new_primary != old_primary and not cookie:
        raise ConfigError('切换域名时必须填写新 Cookie')
    updated = dict(current)
    updated['name'] = name.strip()
    updated['domain'] = domain
    if cookie:
        updated['cookie'] = cookie
    if new_primary != old_primary:
        updated.pop('user_agent', None)
        updated['exchange_plan'] = 'off'
    if user_agent:
        updated['user_agent'] = user_agent
    updated.pop('domain_fallback', None)
    if exchange_plan is not None and new_primary == old_primary:
        updated['exchange_plan'] = exchange_plan
    return updated


def save_config(path: str | Path, accounts: list[dict], push_token: str,
                notification_mode: str | None = None) -> None:
    path = Path(path)
    config = load_config(path) if path.exists() else {}
    config['accounts'] = accounts
    config['pushplus_token'] = push_token
    if notification_mode is not None:
        config['notification_mode'] = notification_mode
    parse_config(json.dumps(config, ensure_ascii=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix='.config-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as output:
            json.dump(config, output, ensure_ascii=False, indent=2)
            output.write('\n')
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)



def main(diagnostic_only: bool = False) -> int:
    try:
        config = (parse_config(os.environ['GLADOS_CONFIG']) if os.environ.get('GLADOS_CONFIG')
                  else load_config(Path(__file__).with_name('config.local.json')))
        accounts = parse_accounts(json.dumps(config['accounts'], ensure_ascii=False))
        log(f'🚀 开始{"只读诊断" if diagnostic_only else "签到"}，共 {len(accounts)} 个账号')
        if diagnostic_only:
            report = run_diagnostic(accounts)
        else:
            mode = config.get('notification_mode', 'always')
            report = run_accounts(accounts, push_token=config.get('pushplus_token', ''),
                                  notification_mode=mode, progress=log)
    except ConfigError as exc:
        print(f'配置错误：{exc}')
        return 1

    if diagnostic_only:
        for result in report.results:
            log(f'{"✅" if result.success else "❌"} {result.name} ({result.domain})：{result.message}')
    if not diagnostic_only and config.get('pushplus_token'):
        if mode == 'fail_only' and report.success and not any(result.exchange_attempted for result in report.results):
            log('⏭️ PushPlus：按 fail_only 配置跳过')
        else:
            log('✅ PushPlus 推送成功' if report.notification_sent else '❌ PushPlus 推送失败')
    return 0 if report.success else 1



def cli() -> int:
    if sys.argv[1:] == ['diagnose']:
        return main(diagnostic_only=True)
    if sys.argv[1:]:
        print('用法：python checkin.py [diagnose]')
        return 1
    return main()


if __name__ == '__main__':
    sys.exit(cli())
