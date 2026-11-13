"""服务器管理页：登录、编辑 config.local.json、手动签到。"""

import time
import json
import hmac
from dataclasses import asdict
from pathlib import Path

import streamlit as st

from checkin import (ConfigError, EXCHANGE_PLANS, SUPPORTED_DOMAINS,
                     load_config, merge_account_edit, parse_accounts,
                     run_accounts, run_diagnostic, read_account_overview, _beijing_time,
                     save_config)


CONFIG_FILE = Path(__file__).with_name('config.local.json')
LOG_FILE = Path(__file__).with_name('checkin.log')
st.set_page_config(page_title='GLaDOS 签到管理', page_icon='☑️', initial_sidebar_state='collapsed')
st.markdown('''<style>
    button[kind="primary"] {
        background-color: #d9efff;
        border-color: #a8d6f5;
        color: #174b6b;
    }
    button[kind="primary"]:hover {
        background-color: #bfe3fa;
        border-color: #80c4ed;
        color: #174b6b;
    }
</style>''', unsafe_allow_html=True)
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

page = st.sidebar.radio('页面', ['主页', '运行日志', '修改配置'])

if st.sidebar.button('退出登录'):
    st.session_state.clear()
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

if page == '运行日志':
    st.subheader('运行日志')
    st.caption('显示服务器 checkin.log 中最近的记录。')
    if not LOG_FILE.exists():
        st.info('日志文件尚不存在，定时任务首次运行后会自动创建。')
    else:
        try:
            with LOG_FILE.open('rb') as log_file:
                log_file.seek(0, 2)
                size = log_file.tell()
                log_file.seek(max(0, size - 1024 * 1024))
                content = log_file.read().decode('utf-8', errors='replace')
            if size > 1024 * 1024:
                content = content.partition('\n')[2]
            lines = content.splitlines()
            count = st.session_state.get('log_lines', 50)
            st.caption(f'显示最近 {min(count, len(lines))} 行；单次最多读取文件末尾 1 MB。')
            st.code('\n'.join(lines[-count:]) if lines else '日志为空', language=None)
            if count < len(lines) and st.button('展开更多'):
                st.session_state.log_lines = count + 50
                st.rerun()
            if st.button('刷新日志'):
                st.rerun()
        except OSError:
            st.error('无法读取日志文件，请检查文件权限。')
    st.stop()

if page == '修改配置':
    st.subheader('账号配置')
    st.caption('Cookie 不会回显；留空表示保留当前值。')


    def save_changes(updated_accounts, token, mode=None):
        try:
            save_config(CONFIG_FILE, updated_accounts, token,
                                 notification_mode=mode or notification_mode)
        except (ConfigError, ValueError) as exc:
            st.error(f'保存失败：{exc}')
            return
        st.session_state.pop('overview', None)
        st.session_state.pop('last_run', None)
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
    if st.button('检查全部账号登录状态'):
        report = run_diagnostic(parsed_accounts)
        for result in report.results:
            st.write(f'{result.name} ({result.domain})：{result.message}')
    st.stop()

st.subheader('账号信息')
st.caption('积分和天数来自只读查询；签到和兑换结果来自本页面手动运行。')
refresh = st.button('刷新账号信息')
if refresh or 'overview' not in st.session_state:
    with st.spinner('正在查询账号信息…'):
        st.session_state.overview = read_account_overview(parsed_accounts)
        st.session_state.overview_time = _beijing_time()
    if refresh:
        st.session_state.pop('last_run', None)
st.caption(f"查询时间：{st.session_state.overview_time}（北京时间）")

if st.button('运行全部账号签到', type='primary'):
    with st.spinner('正在签到，请等待结果…'):
        report = run_accounts(parsed_accounts, push_token=settings.get('pushplus_token', ''),
                              notification_mode=notification_mode)
    st.session_state.overview = report.results
    st.session_state.overview_time = _beijing_time()
    st.session_state.last_run = report
    st.rerun()

report = st.session_state.get('last_run')
if report and settings.get('pushplus_token'):
    should_notify = notification_mode == 'always' or not report.success or any(r.exchange_attempted for r in report.results)
    if should_notify and not report.notification_sent:
        st.warning('PushPlus 发送失败；签到结果请以账号信息为准。')

for result in st.session_state.overview:
    with st.container(border=True):
        st.subheader(result.name)
        st.caption(f'域名：[{result.domain}](https://{result.domain})')
        if not result.success:
            st.error(result.message)
        st.write(f'**当前积分：** {result.points}')
        st.write(f'**剩余天数：** {result.left_days} 天' if result.left_days != '未知' else '**剩余天数：** 未知')
        st.write(f'**签到结果：** {result.message}' if report else '**签到结果：** 未知（本页面尚未手动签到）')
        if result.points_change != '未知':
            st.write(f'最近积分变化：{result.points_change}')
        st.write('**🎁 兑换选项：**')
        if result.exchange_options:
            for option in result.exchange_options:
                st.write(option)
        else:
            st.write('暂无数据')
        st.write(f'自动兑换：{result.exchange_status}' if result.exchange_status else '自动兑换：暂无本次运行结果')
