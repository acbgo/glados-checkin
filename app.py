"""服务器管理页：登录、编辑 config.local.json、手动签到。"""

import time
import json
import hmac
from dataclasses import asdict
from pathlib import Path

import streamlit as st

from checkin import (ConfigError, EXCHANGE_PLANS, SUPPORTED_DOMAINS,
                     load_config, merge_account_edit, parse_accounts,
                     run_accounts, run_diagnostic,
                     save_config)


CONFIG_FILE = Path(__file__).with_name('config.local.json')
st.set_page_config(page_title='GLaDOS 签到管理', page_icon='☑️')
st.title('GLaDOS 签到管理')

if not CONFIG_FILE.exists():
    st.error('缺少 config.local.json。请按 README 复制 config.example.json 并填写配置。')
    st.stop()

settings = load_config(CONFIG_FILE)
admin_password = settings.get('streamlit_admin_password', '')
if not admin_password or admin_password.startswith('REPLACE_WITH_'):
    st.error('请先在 config.local.json 中设置管理员密码。')
    st.stop()

if not st.session_state.get('authenticated', False):
    if st.session_state.get('locked_until', 0) > time.monotonic():
        st.error('登录尝试过多，请稍后再试。')
        st.stop()
    with st.form('login'):
        password = st.text_input('管理员密码', type='password')
        submitted = st.form_submit_button('登录')
    if submitted:
        if hmac.compare_digest(password.encode('utf-8'), admin_password.encode('utf-8')):
            st.session_state.authenticated = True
            st.session_state.failures = 0
            st.rerun()
        else:
            st.session_state.failures = st.session_state.get('failures', 0) + 1
            if st.session_state.failures >= 5:
                st.session_state.locked_until = time.monotonic() + 60
                st.session_state.failures = 0
            st.error('密码错误')
    st.stop()

if st.button('退出登录'):
    st.session_state.authenticated = False
    st.rerun()

try:
    parsed_accounts = parse_accounts(json.dumps(settings['accounts'], ensure_ascii=False))
    accounts = []
    for account in parsed_accounts:
        entry = asdict(account)
        entry['domain'] = list(account.domains) if account.domains else [account.domain]
        entry.pop('domains')
        accounts.append(entry)
    notification_mode = settings.get('notification_mode', 'always')
except ConfigError:
    st.error('账号或通知配置无效，请先在服务器上修复 config.local.json。')
    st.stop()

st.subheader('账号配置')
st.caption('Cookie 不会回显；留空表示保留当前值。')


def save_changes(updated_accounts, token, mode=None):
    try:
        save_config(CONFIG_FILE, updated_accounts, token,
                             notification_mode=mode or notification_mode)
    except (ConfigError, ValueError) as exc:
        st.error(f'保存失败：{exc}')
        return
    st.session_state.form_generation = st.session_state.get('form_generation', 0) + 1
    st.session_state.saved_notice = True
    st.rerun()


if st.session_state.pop('saved_notice', False):
    st.success('配置已保存')


generation = st.session_state.get('form_generation', 0)


for index, current in enumerate(accounts):
    with st.expander(f"{current['name']} · {current['domain']}"):
        with st.form(f'edit-{index}-{generation}'):
            name = st.text_input('账号名称', value=current['name'], key=f'name-{index}-{generation}')
            domain = st.text_input('域名（按顺序用英文逗号分隔）', value=', '.join(current['domain']), key=f'domain-{index}-{generation}')
            cookie = st.text_input('新 Cookie（留空保留）', type='password', key=f'cookie-{index}-{generation}')
            user_agent = st.text_input('新 User-Agent（留空保留）', key=f'ua-{index}-{generation}')
            plan_options = ['off', *EXCHANGE_PLANS]
            plan = st.selectbox('自动兑换计划', plan_options,
                                index=plan_options.index(current.get('exchange_plan', 'off')),
                                key=f'plan-{index}-{generation}')
            save = st.form_submit_button('保存账号')
            delete = st.form_submit_button('删除账号')
        if save:
            updated = list(accounts)
            try:
                updated[index] = merge_account_edit(current, name=name, domain=[d.strip() for d in domain.split(',')],
                                                    cookie=cookie, user_agent=user_agent,
                                                    exchange_plan=plan)
            except ConfigError as exc:
                st.error(str(exc))
            else:
                save_changes(updated, settings.get('pushplus_token', ''))
        if delete:
            save_changes(accounts[:index] + accounts[index + 1:], settings.get('pushplus_token', ''))

with st.expander('添加账号'):
    with st.form(f'add-account-{generation}'):
        name = st.text_input('账号名称', key=f'new-name-{generation}')
        domain = st.text_input('域名（按顺序用英文逗号分隔）', key=f'new-domain-{generation}')
        cookie = st.text_input('Cookie', type='password', key=f'new-cookie-{generation}')
        user_agent = st.text_input('User-Agent（可选）', key=f'new-ua-{generation}')
        plan = st.selectbox('自动兑换计划', ['off', *EXCHANGE_PLANS], key=f'new-plan-{generation}')
        add = st.form_submit_button('添加')
    if add:
        new_account = {'name': name, 'domain': [d.strip() for d in domain.split(',')], 'cookie': cookie,
                       'exchange_plan': plan}
        if user_agent:
            new_account['user_agent'] = user_agent
        save_changes(accounts + [new_account], settings.get('pushplus_token', ''))

st.subheader('PushPlus')
st.write('状态：已配置' if settings.get('pushplus_token') else '状态：未配置')
with st.form(f'pushplus-{generation}'):
    new_token = st.text_input('新 PushPlus token（留空保留）', type='password', key=f'push-token-{generation}')
    clear_token = st.checkbox('清除 PushPlus token')
    save_token = st.form_submit_button('保存通知配置')
if save_token:
    token = '' if clear_token else (new_token or settings.get('pushplus_token', ''))
    save_changes(accounts, token)

st.subheader('通知频率')
with st.form(f'notification-{generation}'):
    chosen_mode = st.selectbox('PushPlus 发送条件', ['always', 'fail_only'],
                               index=['always', 'fail_only'].index(notification_mode))
    save_mode = st.form_submit_button('保存通知频率')
if save_mode:
    save_changes(accounts, settings.get('pushplus_token', ''), chosen_mode)

st.subheader('只读诊断')
st.caption('只查询登录状态，不签到、不兑换、不推送。查询成功不代表签到设备校验必然通过。')
if st.button('检查全部账号登录状态'):
    report = run_diagnostic(parse_accounts(json.dumps(settings['accounts'], ensure_ascii=False)))
    for result in report.results:
        st.write(f'{result.name} ({result.domain})：{result.message}')

st.subheader('手动签到')
if st.button('运行全部账号签到', type='primary'):
    with st.spinner('正在签到，请等待结果…'):
        report = run_accounts(parse_accounts(json.dumps(settings['accounts'], ensure_ascii=False)),
                              push_token=settings.get('pushplus_token', ''),
                              notification_mode=notification_mode)
    for result in report.results:
        st.write(f"{result.name} ({result.domain})：{result.message}；积分 {result.points}；剩余天数 {result.left_days}")
        if result.points_change != '未知':
            st.write(f'最近积分变化：{result.points_change}')
        if result.exchange_options:
            st.write('兑换档位：' + '；'.join(result.exchange_options))
        if result.exchange_status:
            st.write(f'自动兑换：{result.exchange_status}')
    if report.success:
        st.success('全部账号签到完成')
    else:
        st.error('部分账号签到失败')
    should_notify = notification_mode == 'always' or not report.success or any(r.exchange_attempted for r in report.results)
    if settings.get('pushplus_token') and should_notify and not report.notification_sent:
        st.warning('PushPlus 发送失败；签到结果请以上方为准。')
