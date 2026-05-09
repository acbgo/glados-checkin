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
    # glados账号cookie
    cookie = os.environ.get("GLADOS_COOKIE", "")
    if cookie == "":
        print('未获取到COOKIE变量')
        exit(0)

    # 定义url等相关变量
    checkin_url = "https://railgun.info/api/user/checkin"
    state_url = "https://railgun.info/api/user/status"
    points_url = "https://railgun.info/api/user/points"
    referer = 'https://railgun.info/console/checkin'
    origin = "https://railgun.info"
    useragent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"
    payload = {
        'token': 'railgun.info'
    }
    left_time = ''

    # 发起签到请求
    checkin_data = requests.post(checkin_url, headers={'cookie': cookie, 'referer': referer, 'origin': origin,
                                          'user-agent': useragent,
                                          'content-type': 'application/json;charset=UTF-8'}, data=json.dumps(payload))
    # 查询签到状态请求
    state_sata = requests.get(state_url, headers={'cookie': cookie, 'referer': referer, 'origin': origin, 'user-agent': useragent})

    # 查询剩余积分请求
    points_data = requests.get(points_url,  headers={'cookie': cookie, 'referer': referer, 'origin': origin, 'user-agent': useragent})

    # --------------------------------------------------------------------------------------------------------#
    # 解析剩余积分请求
    point_code = points_data.json()['code']
    if point_code == 0:
        points = points_data.json()['points'].split('.')[0]
        sendContent += '剩余积分：' + points + '\n'

    # 解析签到状态请求
    state_code = state_sata.json()['code']
    if state_code != 0:
        print(state_sata.json()['message'])
        sendContent += state_sata.json()['message']
    else:
        left_time = str(state_sata.json()['data']['leftDays'])
        email = state_sata.json()['data']['email']
        if 'message' in checkin_data.text:
            mess = checkin_data.json()['message']
            if 'Repeats' in mess:
                mess = "Checkin Repeats!"
                print(mess + '--剩余(' + left_time + ')天\n' + email)  # 日志输出
            sendContent += mess + '--剩余(' + left_time + ')天\n' + email
        else:
            requests.get('http://www.pushplus.plus/send?token=' + sckey + '&content=' + email + 'cookie已失效')
            print('cookie已失效')  # 日志输出
            sendContent += 'cookie已失效' + '----剩余(' + left_time + ')天\n' + email
    # --------------------------------------------------------------------------------------------------------#
    if sckey != "":
        requests.get(
            'http://www.pushplus.plus/send?token=' + sckey + '&title=' + '签到成功' + '--剩余(' + left_time + ')天' + '&content=' + sendContent)
