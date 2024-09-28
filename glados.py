#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2024/9/28 8:41
# @File    : glados.py
# @Software: PyCharm
# @Description:
# @Ref: https://github.com/komori-flag/glados_automation

import json
import os
import requests

# -------------------------------------------------------------------------------------------
# github workflows
# -------------------------------------------------------------------------------------------
if __name__ == '__main__':
    # pushplus秘钥 申请地址 http://www.pushplus.plus
    sckey = os.environ.get("PUSHPLUS_TOKEN", "")
    # 推送内容
    sendContent = ''
    # glados账号cookie 直接使用数组 如果使用环境变量需要字符串分割一下
    cookies = os.environ.get("GLADOS_COOKIE", []).split("&")
    if cookies[0] == "":
        print('未获取到COOKIE变量')
        cookies = []
        exit(0)
    url = "https://glados.rocks/api/user/checkin"
    url2 = "https://glados.rocks/api/user/status"
    referer = 'https://glados.rocks/console/checkin'
    origin = "https://glados.rocks"
    useragent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"
    payload = {
        'token': 'glados.one'
    }
    for cookie in cookies:
        checkin = requests.post(url, headers={'cookie': cookie, 'referer': referer, 'origin': origin,
                                              'user-agent': useragent,
                                              'content-type': 'application/json;charset=UTF-8'},
                                data=json.dumps(payload))
        state = requests.get(url2,
                             headers={'cookie': cookie, 'referer': referer, 'origin': origin, 'user-agent': useragent})
        # --------------------------------------------------------------------------------------------------------#
        time = state.json()['data']['leftDays']
        time = time.split('.')[0]
        email = state.json()['data']['email']
        if 'message' in checkin.text:
            mess = checkin.json()['message']
            if 'Repeats' in mess:
                mess = "Checkin Repeats!"
                print(mess + '--剩余(' + time + ')天\n' + email)  # 日志输出
            sendContent += mess + '--剩余(' + time + ')天\n' + email
        else:
            requests.get('http://www.pushplus.plus/send?token=' + sckey + '&content=' + email + 'cookie已失效')
            print('cookie已失效')  # 日志输出
            sendContent += 'cookie已失效' + '----剩余(' + time + ')天\n' + email
    # --------------------------------------------------------------------------------------------------------#
    if sckey != "":
        requests.get(
            'http://www.pushplus.plus/send?token=' + sckey + '&title=' + '签到成功' + '&content=' + sendContent)
