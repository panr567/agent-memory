#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""差值账：把「我这次和预期不一样」的地方集中记下来，供自我迭代取料。

存在的理由（11-03 22:4x 用户「先让我们的自我迭代不在自转」）：

自我迭代原来靠「闲着就派活」驱动，而 longterm 任务的目标写的是
「按五条标尺对一遍，哪条空补哪条」——那是一张永远能找到缺口的清单，
不是差值。所以它每轮都能修一个小洞，每轮都"有产出"，于是永远停不下来。

自转的同构于 2608.00017 说的 Echo Gap：判断「这次修得值」和「做这件事」
是同一个我，偏差同源，所以它总能自证有意义。

治法不是加冷却（那是限速），是把启动条件从「闲着」换成「有差值」：
只有一个差值反复出现（≥3 次）且没处理，才值得开一轮迭代。

差值来源：
  user     用户当场指出的偏（最贵，最该修）
  predict  预测判了未达/崩，且写了改法
  check    硬检查 FAIL 或检查自己误报
  roll     自驱那一轮「只说话」没动手
  audit    异体审查判了「不过」（外部执行面，不是我自己）

用法：
  python tools/signals.py add --from user --what "写文章没跑三问" [--fix 描述]
  python tools/signals.py list
  python tools/signals.py gc [--min 3] [--json]
  python tools/signals.py done <id> [--how 怎么修的]
"""
import argparse
import datetime
import io
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
记忆 = os.path.join(根, 'memory')
账 = os.environ.get('SIGNALS_BOOK') or os.path.join(记忆, 'signals.jsonl')

# 2026-11-06 抽公共模块：jsonl 读取统一走 tools/台账.py（tools/ 下曾有 15 处重复）
from 台账 import 读行


def 现在():
    return datetime.datetime.now().strftime('%Y-%m-%dT%H:%M')


def 读():
    """读差值账。读写口径统一在 tools/台账.py。"""
    return 读行(账)


def 加(一):
    io.open(账, 'a', encoding='utf-8').write(json.dumps(一, ensure_ascii=False) + '\n')


来源前缀 = ('预测未达：', '硬检查挂了：', '异体审判不过：', '异体审不过：')


def 去前缀(文本):
    """去掉来源自带的固定开头。

    2026-11-08 10:2x 异体审抓的（最严重的一条）：`预测未达：` 这类前缀自带
    「预测/未达/预测未」好几个共同二元组，于是**八个完全不同的任务**被一条前缀
    并成一簇，close 一次就把它们全销掉。前缀不是主题，先剥掉再谈聚类。
    """
    s = str(文本 or '').strip()
    for p in 来源前缀:
        if s.startswith(p):
            return s[len(p):].strip()
    return s


def 模式(文本):
    """粗聚类：取几个有区分度的词当模式键。

    不做语义聚类——那会引入「同一个词不同意思」的新问题。
    只要求两条信号在词面上撞 2 个词以上，就认为说的是一回事。

    2026-11-08 10:0x 异体审抓的：原来只切 2~6 字的整词，
    三条讲同一件事的 predict 被切成 3 簇（「≥3 次」门槛永远不成立）。
    现在补上**汉字二元组**——「判据写成形容词」和「判据是形容词没法验」
    整词一个都不撞，但二元组能撞上「判据」「形容」。

    10:2x 再修一处：先剥掉来源前缀，否则「预测未达：」这一串共同二元组
    会把无关的八个任务并成一簇。
    """
    文本 = 去前缀(文本)
    停 = set('的了是我你他这那就也都很还不管不用有个一上下前后时'
             '做改写完跑发看想说记会能要对把被从到和与及或者但而因为所以')
    词 = set(w for w in re.findall(r'[\u4e00-\u9fa5]{2,6}|[A-Za-z_]{3,}', 文本)
             if w not in 停)
    # 汉字二元组：只取连续的汉字段，避免把标点、数字混进来
    for 段 in re.findall(r'[\u4e00-\u9fa5]+', 文本):
        for i in range(len(段) - 1):
            bg = 段[i:i + 2]
            if bg[0] in 停 or bg[1] in 停:
                continue
            词.add(bg)
    return 词


def 够近(词1, 词2):
    """两条信号算不算"说同一件事"。

    规则：共享 ≥2 个词，且其中**至少有一个 3 字以上的整词，或者共享 ≥3 个**。
    为什么不是"撞 2 个就并"：二元组太密，光靠 2 个二字词会把不相干的事并一起——
    异体审实测 8 个不同任务被并成一簇，一次 close 销掉一篮子。
    """
    共享 = 词1 & 词2
    if len(共享) < 2:
        return False
    return any(len(t) >= 3 for t in 共享) or len(共享) >= 3


def 聚(们):
    """把"说同一件事"的信号聚成一簇。"""
    簇 = []
    for x in 们:
        if x.get('处理'):
            continue
        词 = 模式(str(x.get('什么', '')))
        if not 词:
            continue
        放 = None
        for c in 簇:
            if 够近(词, c['词']):
                放 = c
                break
        if 放:
            放['条'].append(x)
            放['词'] |= 词
        else:
            簇.append({'词': 词, '条': [x]})
    return 簇


def 键(簇):
    """一个簇的稳定键：取它最早那条信号的 id。

    为什么要键：念头要能按「这个差值被销账了」自动消失。
    不能用「次数最多那条」或代表文本——新信号进来就会变，键一变就销不掉账。
    最早那条一旦处理就整簇处理，它在簇的生命期里不会变（新增只会更晚）。
    """
    if not 簇.get('条'):
        return ''
    try:
        最早 = min(簇['条'], key=lambda x: str(x.get('时间') or ''))
    except Exception:
        return ''
    import hashlib
    return hashlib.md5(str(最早.get('id')).encode('utf-8')).hexdigest()[:8]


def 年龄天(簇):
    """最早一条离现在多少天——用来判「这个差值挂太久了」。"""
    if not 簇.get('条'):
        return 0.0
    最早 = min(str(x.get('时间') or '') for x in 簇['条'])
    try:
        t = datetime.datetime.strptime(最早[:16], '%Y-%m-%dT%H:%M')
    except Exception:
        return 0.0
    return round((datetime.datetime.now() - t).total_seconds() / 86400.0, 2)


def 活着的():
    """未处理、且所在簇没被销账的信号。"""
    全 = 读()
    死的 = 已处理(全)
    return [x for x in 全 if not x.get('处理') and x.get('来源') != '处理'
            and x.get('id') not in 死的]


def cmd_add(a):
    一 = {
        'id': 'sg-%s-%s' % (datetime.datetime.now().strftime('%m%d%H%M%S'),
                            os.urandom(2).hex()),
        '时间': 现在(),
        '来源': a.source,
        '什么': a.what,
        '修法': a.fix or '',
        '处理': False,
    }
    加(一)
    print('记下 %s｜%s｜%s' % (一['id'], 一['来源'], 一['什么'][:40]))
    return 0


def cmd_list(a):
    全 = 读()
    死的 = 已处理(全)
    们 = [x for x in 全 if not x.get('处理') and x.get('来源') != '处理'
          and x.get('id') not in 死的]
    if not 们:
        print('没有未处理的差值')
        return 0
    print('未处理差值 %d 条：' % len(们))
    for x in 们:
        print('  %s  [%s] %s' % (x.get('id'), x.get('来源'), str(x.get('什么'))[:60]))
        if x.get('修法'):
            print('        修法：%s' % str(x['修法'])[:70])
    return 0


def cmd_gc(a):
    全 = 读()
    死的 = 已处理(全)
    们 = [x for x in 全 if not x.get('处理') and x.get('来源') != '处理'
          and x.get('id') not in 死的]
    簇 = 聚(们)
    # user（他当场纠正）和 check（硬检查挂了）两源**一次就够**，不等 --min 次。
    # 理由：他花时间指出来的那一刻最贵，等三次就是多挨两次；检查挂着本身就是事实。
    # 其它来源仍按 --min——偶发一次的偏差不足以说明是「反复」。
    # 2026-11-06 01:x 实测：三条内容不同的真实纠正词面不撞、各成 1 条，
    # 门槛 3 让它们一条都进不了 gc —— 判了「写不成」之后既不成规则也不成念头，死在账本里。
    够格 = [c for c in 簇
            if len(c['条']) >= (1 if set(x.get('来源') for x in c['条']) & {'user', 'check', 'audit'}
                                else a.min)]
    够格.sort(key=lambda c: -len(c['条']))
    if a.json:
        print(json.dumps([{
            '次数': len(c['条']),
            '键': 键(c),
            '年龄天': 年龄天(c),
            '代表': sorted(c['条'], key=lambda x: x.get('时间'))[-1].get('什么'),
            '来源': sorted(set(x.get('来源') for x in c['条'])),
            # 最早一条的时间：消费方要靠它排「谁更该先处理」。
            # 2026-11-06 01:x 加——不加的话消费方只能按次数排，旧账永远压住新账。
            '最早': min(str(x.get('时间') or '') for x in c['条']),
            'ids': [x.get('id') for x in c['条']],
            '修法': [x.get('修法') for x in c['条'] if x.get('修法')],
        } for c in 够格], ensure_ascii=False))
        return 0
    if not 够格:
        print('没有反复 %d 次以上的未处理差值（共 %d 条未处理、%d 簇）'
              % (a.min, len(们), len(簇)))
        return 1
    print('反复出现且未处理的差值 %d 簇：' % len(够格))
    for c in 够格:
        最近 = sorted(c['条'], key=lambda x: str(x.get('时间')))[-1]
        print('  %d 次｜%s' % (len(c['条']), str(最近.get('什么'))[:60]))
        源 = sorted(set(x.get('来源') for x in c['条']))
        print('        来源：%s｜最早 %s' % ('/'.join(源),
                                       min(str(x.get('时间')) for x in c['条'])))
    return 0


def cmd_close(a):
    """按簇键销账：整簇标记处理完。

    为什么要有这个（2026-11-08）：念头里的「判据」要写成一条能跑的命令。
    念头带的是簇键，所以销账也得按键来——不然判据和账本口径对不上，
    改完还得自己去翻 id。

    2026-11-08 10:0x 异体审抓的：原来 `--how` 可以留空，
    一条命令就能把红变绿，改没改没人验。现在**必须写怎么修的**。
    """
    if not (a.how or '').strip():
        print('不行：`close` 必须带 --how "改了什么"。'
              '不带证据的销账等于自己给自己发合格证。')
        return 2
    簇 = 聚(活着的())
    目标 = [c for c in 簇 if 键(c) == a.key]
    if not 目标:
        print('没有这个键的簇：%s（跑 gc 看看现在有哪些）' % a.key)
        return 1
    c = 目标[0]
    掉了 = []
    for x in c['条']:
        加({'id': x['id'] + '-done', '时间': 现在(), '来源': '处理',
            '什么': '处理：%s' % (a.how or ''), '处理': False, '目标': x['id']})
        掉了.append(x.get('id'))
    print('销账：整簇 %d 条（键 %s）' % (len(掉了), a.key))
    if a.how:
        print('  怎么修的：%s' % a.how)
    return 0


def cmd_done(a):
    """标记处理完。一个 id 处理了，它所在的整簇一起算处理。

    原因：4 条同簇记录说的是同一个问题，修的是那个问题，不是那 4 条。
    只标一条的话，剩下 3 条照样够 gc 的门槛，等于没处理。

    2026-11-08 10:2x 异体审抓的：`close` 加了 --how 闸，`done` 却没有，
    等于闸只装在两条路的一条上。现在两条都要证据。
    """
    if not (a.how or '').strip():
        print('不行：`done` 也必须带 --how "改了什么"（跟 close 同一条规矩）。')
        return 2
    们 = 读()
    死的 = 已处理(们)
    活的 = [x for x in 们 if not x.get('处理') and x.get('来源') != '处理'
            and x.get('id') not in 死的]
    簇 = 聚(活的)
    目标簇 = None
    for c in 簇:
        if any(x.get('id') == a.id for x in c['条']):
            目标簇 = c
            break
    if not 目标簇:
        命中 = [x for x in 们 if x.get('id') == a.id]
        if not 命中:
            print('找不到 %s' % a.id)
            return 1
        目标簇 = {'条': 命中}
    掉了 = []
    for x in 目标簇['条']:
        加({'id': x['id'] + '-done', '时间': 现在(), '来源': '处理',
            '什么': '处理：%s' % (a.how or ''), '处理': False, '目标': x['id']})
        掉了.append(x.get('id'))
    print('标记 %d 条为已处理（整簇）：%s' % (len(掉了), '、'.join(掉了)))
    return 0


def 已处理(们):
    死的 = set()
    for x in 们:
        if x.get('来源') == '处理' and x.get('目标'):
            死的.add(x['目标'])
    return 死的


def main():
    ap = argparse.ArgumentParser(description='差值账')
    sub = ap.add_subparsers(dest='cmd')

    a1 = sub.add_parser('add', help='记一条差值')
    a1.add_argument('--from', dest='source', default='user',
                    choices=['user', 'predict', 'check', 'roll', 'audit'])
    a1.add_argument('--what', required=True)
    a1.add_argument('--fix', default='')
    a1.set_defaults(fn=cmd_add)

    a2 = sub.add_parser('list', help='列出未处理的')
    a2.set_defaults(fn=cmd_list)

    a3 = sub.add_parser('gc', help='找出反复出现的')
    a3.add_argument('--min', type=int, default=3)
    a3.add_argument('--json', action='store_true')
    a3.set_defaults(fn=cmd_gc)

    a4 = sub.add_parser('done', help='标记处理完')
    a4.add_argument('id')
    a4.add_argument('--how', default='')
    a4.set_defaults(fn=cmd_done)

    a5 = sub.add_parser('close', help='按簇键销账（整簇）')
    a5.add_argument('key')
    a5.add_argument('--how', default='')
    a5.set_defaults(fn=cmd_close)

    a = ap.parse_args()
    if not getattr(a, 'fn', None):
        ap.print_help()
        return 1
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
