import os
import time
from copy import deepcopy
from json import load, dump
from nonebot import get_bot, on_command
from os.path import dirname, join, exists
from asyncio import Lock
from .query import queue
from .pcrclient import ApiException
from .safeservice import SafeService
from .text2img import image_draw
from .create_img import generate_info_pic, generate_support_pic
from hoshino import priv,get_self_ids
from hoshino.util import pic2b64
from hoshino.config import SUPERUSERS
from hoshino.typing import NoticeSession, MessageSegment

#from .jjchistory import *

sv_help='''\t\t\t\t【竞技场帮助】
可以添加的订阅：[jjc][pjjc][排名上升][上线提醒]
#排名上升提醒对jjc和pjjc同时生效
#每个QQ号至多添加8个uid的订阅
#默认开启jjc、pjjc，关闭排名上升、上线提醒
#手动查询时，返回昵称、jjc/pjjc排名、场次、
jjc/pjjc当天排名上升次数、最后登录时间。
#支持群聊/私聊使用。建议群聊使用，大量私聊号会寄。
------------------------------------------------
命令格式：
#只绑定1个uid时，绑定的序号可以不填。
[绑定的序号]1~8对应绑定的第1~8个uid，序号0表示全部
1）竞技场绑定[uid][昵称]（昵称可省略）
2）删除竞技场绑定[绑定的序号]（这里序号不可省略）
3）开启/关闭竞技场推送（不会删除绑定）
4）清空竞技场绑定
5）竞技场查询[uid]（uid可省略）
6）竞技场订阅状态
7）竞技场修改昵称 [绑定的序号] [新昵称] 
8）竞技场设置[开启/关闭][订阅内容][绑定的序号]
9）竞技场/击剑记录[绑定的序号]（序号可省略）
10）竞技场设置1110[绑定的序号]
#0表示关闭，1表示开启
#4个数字依次代表jjc、pjjc、排名上升、上线提醒
#例如：“竞技场设置1011 2” “竞技场设置1110 0”
11）换私聊推送（限私聊发送，需要好友）
12）在本群推送（限群聊发送，无需好友）
'''
sv_help_adm='''------------------------------------------------
管理员帮助：
1）pcrjjc负载查询
2）pcrjjc删除绑定[qq号]
3）pcrjjc关闭私聊推送
'''

sv = SafeService('竞技场推送',help_=sv_help, bundle='pcr查询')

# 数据库对象初始化
#JJCH = JJCHistoryStorage()
MAX_PRI = 0 #最大私聊人数
friendList = []
pcrid_list = []
config = join(dirname(__file__), 'binds.json')
root = {'arena_bind': {}}
if exists(config):
    with open(config) as fp:
        root = load(fp)
lck = Lock()
lck_friendList = Lock()
bind_cache = root['arena_bind']
cache = {}
jjc_log = {}
query_cache = {}
today_notice = 0
yesterday_notice = 0

@sv.on_fullmatch('竞技场帮助', only_to_me=False)
async def send_jjchelp(bot, ev):
    if not priv.check_priv(ev, priv.SUPERUSER):
        pic = image_draw(sv_help)
    else:
        pic = image_draw(sv_help+sv_help_adm)
    await bot.send(ev,f'[CQ:image,file={pic}]') 

#========================================查询========================================

@sv.on_fullmatch('查询群数', only_to_me=False)
async def group_num(bot, ev):
    global bind_cache, lck
    gid = int(ev['group_id'])
    async with lck:
        for sid in get_self_ids():
            gl = await bot.get_group_list(self_id=sid)
            gl = [g['group_id'] for g in gl]
            try:
                await bot.send_group_msg(
                self_id=sid,
                group_id=gid,
                message=f"本Bot目前正在为【{len(gl)}】个群服务"
                )
            except Exception as e:
                sv.logger.info(f'bot账号{sid}不在群{gid}中，将忽略该消息')

@sv.on_fullmatch('查询竞技场订阅数', only_to_me=False)
async def pcrjjc_number(bot, ev):
    global bind_cache, lck
    async with lck:
        await bot.send(ev, f'当前竞技场已订阅的账号数量为【{len(bind_cache)}】个')

@sv.on_rex(r'^竞技场查询 ?(\d+)?$')
async def on_query_arena(bot, ev):
    global bind_cache, lck

    ret = ev['match']
    qid = str(ev.user_id)
    try:
        pcrid = int(ret.group(1))
        if(len(ret.group(1))) != 13:
            await bot.send(ev, '位数不对，uid是13位的！')
            return
        else:
            manual_query_list = [pcrid]    #手动查询的列表
            manual_query_list_name = [None]
    except:
        if qid in bind_cache:
            manual_query_list = bind_cache[qid]["pcrid"]
            manual_query_list_name = bind_cache[qid]["pcrName"]
        else:
            await bot.send(ev, '木有找到绑定信息，查询时不能省略13位uid！')
            return
    for i in range(len(manual_query_list)):
        query_cache[ev.user_id] = []
        pcrid = manual_query_list[i]
        await queue.put((3,(resolve2,pcrid,{"bot":bot,"ev":ev,"list":manual_query_list_name,"index":i,"uid":pcrid})))

@sv.on_fullmatch('竞技场订阅状态')
async def send_arena_sub_status(bot,ev):
    global bind_cache
    qid = str(ev['user_id'])
    gid = ev.group_id
    member_info = await bot.get_group_member_info(group_id=gid,user_id=qid)
    name = member_info["card"] or member_info["nickname"]
    if qid in bind_cache:
        private = '私聊推送' if bind_cache[qid]["private"] else '群聊推送'
        notice_on = '推送已开启' if bind_cache[qid]["notice_on"] else '推送未开启'
        reply = f'{name}（{qid}）的竞技场订阅列表：\n\n'
        reply += f'群号：{bind_cache[qid]["gid"]}\n'
        reply += f'''推送方式：{private}\n状态：{notice_on}\n'''
        for pcrid_id in range(len(bind_cache[qid]["pcrid"])):
            reply += f'\n【{pcrid_id+1}】{bind_cache[qid]["pcrName"][pcrid_id]}（{bind_cache[qid]["pcrid"][pcrid_id]}）\n'
            tmp = bind_cache[qid]["noticeType"][pcrid_id]
            jjcNotice = True if tmp//1000 else False
            pjjcNotice = True if (tmp%1000)//100 else False
            riseNotice = True if (tmp%100)//10 else False
            onlineNotice = True if tmp%10 else False
            noticeType = '推送内容：'
            if jjcNotice:
                noticeType += 'jjc、'
            if pjjcNotice:
                noticeType += 'pjjc、'
            if riseNotice:
                noticeType += '排名上升、'
            if onlineNotice:
                noticeType += '上线提醒、'
            if noticeType == '推送内容：':
                noticeType += '无'
            else:
                noticeType = noticeType.strip('、')
            reply += noticeType
            reply += '\n'                
        pic = image_draw(reply)
        await bot.send(ev,f'[CQ:image,file={pic}]')
    else:
        await bot.send(ev,'您还没有绑定竞技场！')

@sv.on_rex(r'^(?:击剑|竞技场)记录 ?(\d)?$')        
async def jjc_log_query(bot,ev):
    global jjc_log, bind_cache
    qid = str(ev.user_id)
    member_info = await bot.get_group_member_info(group_id=ev.group_id,user_id=qid)
    name = member_info["card"] or member_info["nickname"]
    print_all = False
    too_long = False
    if qid not in bind_cache:
        await bot.send(ev,'您还没有绑定竞技场！')
        return  
    pcrid_num = len(bind_cache[qid]['pcrid'])
    try:
        ret = ev["match"]
        pcrid_id_input = int(ret.group(1))
    except:
        if pcrid_num == 1:
            pcrid_id_input = 1
        else:
            print_all = True
    if print_all == False:
        if pcrid_id_input == 0 or pcrid_id_input > pcrid_num:
            await bot.send(ev,'序号超出范围，请检查您绑定的竞技场列表')
            return
    if print_all:
        msg = f'''\t\t\t\t【{name}的击剑记录】\n'''
        jjc_log_cache = []
        len_pcrName = []
        for pcrid_id in range(pcrid_num):       #复制相关的log，并排序
            pcrid = bind_cache[qid]['pcrid'][pcrid_id]
            pcrName = bind_cache[qid]['pcrName'][pcrid_id]
            if pcrid in jjc_log:                    #计算名字长度
                width = 0
                for c in pcrName:
                    if len(c.encode('utf8')) == 3:  # 中文
                        width += 2
                    else:
                        width += 1
                len_pcrName.append(width)
                for log in jjc_log[pcrid]:
                    log_tmp = list(log)
                    log_tmp.append(pcrid_id)
                    jjc_log_cache.append(log_tmp)
            else:
                len_pcrName.append(0)           #没有击剑记录的uid名字长度写0
        longest_pcrName = max(len_pcrName)
        for i in range(len(len_pcrName)):
            len_pcrName[i] = longest_pcrName - len_pcrName[i]       #改成补空格的数量
        jjc_log_cache_num = len(jjc_log_cache)
        if jjc_log_cache_num:
            jjc_log_cache.sort(key = lambda x:x[0], reverse=True)        
            if jjc_log_cache_num > 50:
                too_long = True
                jjc_log_cache_num = 50
            for i in range(jjc_log_cache_num):
                timeStamp = jjc_log_cache[i][0]
                timeArray = time.localtime(timeStamp)
                otherStyleTime = time.strftime("%Y-%m-%d %H:%M:%S", timeArray)
                pcrid_id = jjc_log_cache[i][4]
                pcrName = bind_cache[qid]['pcrName'][pcrid_id]
                space = ' '*len_pcrName[pcrid_id]
                jjc_pjjc = 'jjc ' if jjc_log_cache[i][1] == 1 else 'pjjc'
                new = jjc_log_cache[i][2]
                old = jjc_log_cache[i][3]
                if new < old:
                    change = f'''{old}->{new} [▲{old-new}]'''
                else:
                    change = f'''{old}->{new} [▽{new-old}]'''
                msg += f'''{otherStyleTime} {pcrName}{space} {jjc_pjjc}：{change}\n'''
            if too_long:
                msg += '###由于您订阅了太多账号，记录显示不下嘞~\n###如有需要，可以在查询时加上序号。'
        else:
            msg += '没有击剑记录！'
    else:
        msg = f'''\t\t\t【{name}的击剑记录】\n'''
        pcrid_id = pcrid_id_input-1
        pcrid = bind_cache[qid]['pcrid'][pcrid_id]
        pcrName = bind_cache[qid]['pcrName'][pcrid_id]
        msg += f'''{pcrName}（{pcrid}）\n'''
        if pcrid in jjc_log:
            jjc_log_num = len(jjc_log[pcrid])
            for i in range(jjc_log_num):
                n = jjc_log_num-1-i         #倒序输出，是最近的log在上面
                timeStamp = jjc_log[pcrid][n][0]
                timeArray = time.localtime(timeStamp)
                otherStyleTime = time.strftime("%Y-%m-%d %H:%M:%S", timeArray)
                jjc_pjjc = 'jjc' if jjc_log[pcrid][n][1] == 1 else 'pjjc'
                new = jjc_log[pcrid][n][2]
                old = jjc_log[pcrid][n][3]
                if new < old:
                    change = f'''{old}->{new} [▲{old-new}]'''
                else:
                    change = f'''{old}->{new} [▽{new-old}]'''
                msg += f'''{otherStyleTime} {jjc_pjjc}：{change}\n'''
        else:
            msg += '没有击剑记录！'
    pic = image_draw(msg)
    await bot.send(ev,f'[CQ:image,file={pic}]')

#========================================竞技场绑定========================================

@sv.on_rex(r'^竞技场绑定 ?(\d+) ?(\S+)?$')
async def on_arena_bind(bot, ev):
    ret = ev["match"]
    pcrid = int(ret.group(1))
    if len(ret.group(1)) != 13:
        await bot.send(ev, '位数不对，uid是13位的！')
        return
    else:
        try:        #是否指定昵称
            if len(ret.group(2)) <=12:
                nickname = ret.group(2)
            else:
                await bot.send(ev, '昵称不能超过12个字，换个短一点的昵称吧~')
                return
        except:
            nickname = ''
    await queue.put((4,(resolve3,pcrid,{"bot":bot,"ev":ev,'nickname':nickname,'uid':pcrid,'friendlist':friendList})))

@sv.on_rex(r'^删除竞技场绑定 ?(\d)?$')
async def delete_arena_sub(bot,ev):
    global bind_cache, lck
    qid = str(ev.user_id)
    ret = ev["match"]
    if ret.group(1):
        pcrid_id = int(ret.group(1))
    else:
        await bot.send(ev,'输入格式不对！“删除竞技场绑定+【序号】”（序号不可省略）')
        return
    async with lck:
        if qid in bind_cache:
            pcrid_num = len(bind_cache[qid]["pcrid"])
            if pcrid_num == 1:
                await bot.send(ev,'您只有一个绑定的uid，请使用“清空竞技场绑定”删除')
                return 
            if pcrid_id > 0 and  pcrid_id <= pcrid_num:
                pcrid_id -= 1
                result = f'您已成功删除：【{pcrid_id+1}】{bind_cache[qid]["pcrName"][pcrid_id]}（{bind_cache[qid]["pcrid"][pcrid_id]}）'
                del bind_cache[qid]["pcrid"][pcrid_id]
                del bind_cache[qid]["noticeType"][pcrid_id]
                del bind_cache[qid]["pcrName"][pcrid_id]
                save_binds()
                await bot.send(ev,result)
            else:
                await bot.send(ev,'输入的序号超出范围！')
    
@sv.on_fullmatch('清空竞技场绑定', only_to_me=False)
async def pcrjjc_del(bot, ev):
    global bind_cache, lck
    qid = str(ev.user_id)
    async with lck:
        if qid in bind_cache:
            reply = '删除成功！\n'
            for pcrid_id in range(len(bind_cache[qid]["pcrid"])):
                reply += f'''【{pcrid_id+1}】{bind_cache[qid]["pcrName"][pcrid_id]}\n（{bind_cache[qid]["pcrid"][pcrid_id]}）\n'''
            del bind_cache[qid]
        else:
            reply = '您还没有绑定竞技场！'
            await bot.send(ev, reply)
            return
        save_binds()
    await bot.send(ev, reply)

#========================================竞技场设置========================================
@sv.on_rex(r'^竞技场修改昵称 ?(\d)? (\S+)$')
async def change_nickname(bot,ev):
    global bind_cache, lck
    qid = str(ev.user_id)
    if qid not in bind_cache:
        reply = '您还没有绑定竞技场！'
        await bot.send(ev,reply)
        return
    ret = ev["match"]
    try:
        pcrid_id = int(ret.group(1))
    except:
        pcrid_id = None 
    if len(ret.group(2)) <= 12:
        name = ret.group(2)
    else:
        await bot.send(ev,'昵称不能超过12个字，换个短一点的昵称吧~')
        return
    pcrid_num = len(bind_cache[qid]["pcrid"])
    if pcrid_id is None:
        if pcrid_num == 1:
            pcrid_id = 1
        else:
            await bot.send(ev,'您绑定了多个uid，更改昵称时需要加上序号。')
            return
    if pcrid_id ==0 or pcrid_id > pcrid_num:
        await bot.send(ev,'序号超出范围，请检查您绑定的竞技场列表')
        return
    async with lck:
        pcrid_id -= 1
        bind_cache[qid]["pcrName"][pcrid_id] = name
        save_binds()
    await bot.send(ev,'更改成功！')

@sv.on_fullmatch('在本群推送')
async def group_set(bot,ev):
    global bind_cache, lck
    qid = str(ev.user_id)
    gid = ev.group_id
    if qid in bind_cache:
        async with lck:
            bind_cache[qid]['gid'] = gid
            bind_cache[qid]['private'] = False
            bind_cache[qid]['notice_on'] = True
            reply = '设置成功！已为您开启推送。'
            save_binds()   
    else:
        reply = '您还没有绑定竞技场！'
    await bot.send(ev,reply)

@on_command('private_notice',aliases=('换私聊推送'),only_to_me= False)
async def private_notice(session):
    bot = get_bot()
    global bind_cache, lck, friendList
    pri_user = 0
    qid = str(session.ctx['user_id'])
    for i in bind_cache:
        if bind_cache[i]['notice_on'] and bind_cache[i]['private']:
            pri_user += 1
    if pri_user >= MAX_PRI:
        await session.send('私聊推送用户已达上限！')
        return
    if session.ctx['message_type'] != 'private':
        await session.send('仅限好友私聊使用！')
        return
    if len(friendList):
        await renew_friendlist()
    if qid not in friendList:
        return
    async with lck:
        bind_cache[qid]['private'] = True
        bind_cache[qid]['notice_on'] = True
        save_binds()
    reply = '设置成功！已为您开启推送。已通知管理员！'
    reply_adm = f'''{qid}开启了私聊jjc推送！'''
    await session.send(reply)
    await bot.send_private_msg(user_id = SUPERUSERS[0], message = reply_adm)

@sv.on_rex(r'^竞技场设置 ?(开启|关闭) ?(jjc|pjjc|排名上升|上线提醒) ?(\d)?$')
async def set_noticeType(bot,ev):
    global bind_cache, lck
    qid = str(ev.user_id)
    ret = ev["match"]
    turn_on = True if str(ret.group(1))=='开启'else False
    change = ret.group(2)
    pcrid_id = int(ret.group(3)) if ret.group(3) else None
    
    async with lck:
        if qid in bind_cache:
            pcrid_num = len(bind_cache[qid]["pcrid"])       #这个qq号绑定的pcrid个数
            if pcrid_id is None:        #只绑定1个uid时，绑定的序号可以不填。
                if pcrid_num == 1:
                    pcrid_id = 1
                else:
                    reply = '您绑定了多个uid，更改设置时需要加上序号。'
            if 0 <= pcrid_id and pcrid_id <= pcrid_num: ##设置成功！                
                if pcrid_id ==0:
                    for i in range(pcrid_num):                           
                        tmp = int(bind_cache[qid]["noticeType"][i])
                        jjcNotice = True if tmp//1000 else False
                        pjjcNotice = True if (tmp%1000)//100 else False
                        riseNotice = True if (tmp%100)//10 else False
                        onlineNotice = True if tmp%10 else False
                        if change == 'jjc':
                            jjcNotice = turn_on
                        elif change == 'pjjc':
                            pjjcNotice = turn_on
                        elif change == '排名上升':
                            riseNotice = turn_on
                        elif change == '上线提醒':
                            onlineNotice = turn_on
                        tmp = jjcNotice*1000 + pjjcNotice*100 + riseNotice*10 + onlineNotice
                        bind_cache[qid]["noticeType"][i] = tmp
                else:
                    pcrid_id -= 1                         #从0开始计数，-1
                    tmp = int(bind_cache[qid]["noticeType"][pcrid_id])
                    jjcNotice = True if tmp//1000 else False
                    pjjcNotice = True if (tmp%1000)//100 else False
                    riseNotice = True if (tmp%100)//10 else False
                    onlineNotice = True if tmp%10 else False
                    if change == 'jjc':
                        jjcNotice = turn_on
                    elif change == 'pjjc':
                        pjjcNotice = turn_on
                    elif change == '排名上升':
                        riseNotice = turn_on
                    elif change == '上线提醒':
                        onlineNotice = turn_on
                    tmp = jjcNotice*1000 + pjjcNotice*100 + riseNotice*10 + onlineNotice
                    bind_cache[qid]["noticeType"][pcrid_id] = tmp
                reply = '设置成功！' 
                save_binds()
            else:
                reply = '序号超出范围，请检查您绑定的竞技场列表'
        else:
            reply = '您还没有绑定jjc，绑定方式：\n[竞技场绑定 uid] uid为pcr(b服)个人简介内13位数字'
    await bot.send(ev,reply)  

@sv.on_rex(r'^竞技场设置 ?([01]{4}) ?(\d)?$')
async def set_allType(bot,ev):
    global bind_cache, lck
    qid = str(ev.user_id)
    ret = ev["match"]
    change = ret.group(1)       #change: str
    pcrid_id = int(ret.group(2)) if ret.group(2) else None
    async with lck:
        if qid in bind_cache:
            pcrid_num = len(bind_cache[qid]["pcrid"])       #这个qq号绑定的pcrid个数
            if pcrid_id is None:        #只绑定1个uid时，绑定的序号可以不填。
                if pcrid_num == 1:
                    pcrid_id = 1
                else:
                    reply = '您绑定了多个uid，更改设置时需要加上序号。'
            if 0 <= pcrid_id and pcrid_id <= pcrid_num: ##设置成功！                
                change_quick_set = int(change)
                if pcrid_id ==0:
                    for i in range(pcrid_num):
                        bind_cache[qid]["noticeType"][i] = change_quick_set
                else:
                    pcrid_id -= 1                       #从0开始计数，-1
                    bind_cache[qid]["noticeType"][pcrid_id] = change_quick_set
                reply = '设置成功！' 
                save_binds()
            else:
                reply = '序号超出范围，请检查您绑定的竞技场列表'
        else:
            reply = '您还没有绑定jjc，绑定方式：\n[竞技场绑定 uid] uid为pcr(b服)个人简介内13位数字'
    await bot.send(ev,reply)

@sv.on_rex(r'^(开启|关闭)竞技场推送$')
async def notice_on_change(bot,ev):
    global bind_cache, lck ,friendList
    qid = str(ev.user_id)
    try:
        ret = ev["match"]
        turn_on =True if ret.group(1) == '开启' else False
    except:
        await bot.send(ev,'出错了，请联系管理员！')
        return
    async with lck:
        if qid in bind_cache:
            if bind_cache[qid]["notice_on"] == turn_on:
                await bot.send(ev,f'您的竞技场推送，已经是{ret.group(1)}状态，不要重复{ret.group(1)}！')
                return
            else:
                if turn_on:
                    if len(friendList):
                        await renew_friendlist()
                    if bind_cache[qid]["private"]:
                        if qid not in friendList:
                            await bot.send(ev,'开启私聊推送需要先加好友！你也可以发送“在本群推送”，改为群聊推送。')
                            return
                        else:
                            for i in bind_cache:
                                if bind_cache[i]['notice_on'] and bind_cache[i]['private']:
                                    pri_user += 1
                            if pri_user >= MAX_PRI:
                                await bot.send(ev,'私聊推送用户已达上限！')
                                return
                            reply_adm = f'''{qid}开启了私聊jjc推送！''' 
                            await bot.send(ev,'已通知管理员')
                            await bot.send_private_msg(user_id = SUPERUSERS[0], message = reply_adm)
                bind_cache[qid]["notice_on"] = turn_on
        else:
            await bot.send(ev,'您还没有绑定竞技场！')
            return
        save_binds()
    await bot.send(ev,f'竞技场推送{ret.group(1)}成功！')      

#========================================管理员指令========================================

@sv.on_fullmatch('pcrjjc负载查询')
async def load_query(bot,ev):
    global bind_cache, today_notice, yesterday_notice
    if not priv.check_priv(ev, priv.SUPERUSER):
        return
    qid_notice_on_private = 0
    qid_notice_on_group = 0
    pcrid_num_private = 0
    pcrid_num_group = 0
    for qid in bind_cache:
        if bind_cache[qid]['notice_on']:
            if bind_cache[qid]['private']:
                qid_notice_on_private += 1
                pcrid_num_private += len(bind_cache[qid]['pcrid'])
            else:
                qid_notice_on_group += 1
                pcrid_num_group += len(bind_cache[qid]['pcrid'])
    msg = f'''pcrjjc负载：\n群聊用户数量：{qid_notice_on_group} 群聊绑定的uid：{pcrid_num_group}个\n私聊用户数量：{qid_notice_on_private} 私聊绑定的uid：{pcrid_num_private}个\n昨天推送次数：{yesterday_notice} 今天推送次数：{today_notice}'''
    pic = image_draw(msg)
    await bot.send(ev,f'[CQ:image,file={pic}]')

@sv.on_fullmatch('pcrjjc关闭私聊推送')
async def no_private(bot,ev):
    global bind_cache ,lck
    if not priv.check_priv(ev, priv.SUPERUSER):
        return
    async with lck:
        for qid in bind_cache:
            if bind_cache[qid]['private'] and bind_cache[qid]['notice_on']:
                bind_cache[qid]['notice_on'] = False
        save_binds()
    await bot.send(ev,'所有设置为私聊推送的用户的推送已关闭！')

@sv.on_rex(r'^pcrjjc删除绑定 ?(\d{6,10})')
async def del_binds(bot,ev):
    global bind_cache, lck
    if not priv.check_priv(ev, priv.SUPERUSER):
        return
    ret = ev["match"]
    qid = str(ret.group(1))
    if qid in bind_cache:
        async with lck:
            del bind_cache[qid]
            save_binds()
        reply = '删除成功！'
    else:
        reply = '绑定列表中找不到这个qq号！'
    await bot.send(ev,reply)

#========================================头像框========================================

# 头像框设置文件不存在就创建文件，并且默认彩色
current_dir = os.path.join(os.path.dirname(__file__), 'frame.json')
if not os.path.exists(current_dir):
    data = {"default_frame": "color.png","customize": {}}
    with open(current_dir, 'w', encoding='UTF-8') as f:
        dump(data, f, indent=4, ensure_ascii=False)

@sv.on_rex(r'^详细查询 ?(\d+)?$')
async def on_query_arena_all(bot, ev):
    global bind_cache, lck
    ret = ev['match']
    id = ret.group(1)
    if not id:
        await bot.send(ev,'请在详细查询后带上uid或编号', at_sender=True)
        return
    qid = str(ev['user_id'])
    async with lck:
        if len(id) < 13:
            if not qid in bind_cache:
                await bot.send(ev,'您还未绑定竞技场', at_sender=True)
                return
            elif len(bind_cache[qid]["pcrid"]) < int(id):
                await bot.send(ev,'输入的序号超出范围，可发送竞技场查询查看你的绑定', at_sender=True)
                return
            else:
                id = bind_cache[qid]['pcrid'][int(id)-1]
    await queue.put((2,(resolve1,id,{"bot":bot,"ev":ev,"uid":id})))

@sv.on_prefix('竞技场换头像框', '更换竞技场头像框', '更换头像框')
async def change_frame(bot, ev):
    user_id = ev.user_id
    frame_tmp = ev.message.extract_plain_text()
    path = os.path.join(os.path.dirname(__file__), 'img/frame/')
    frame_list = os.listdir(path)
    if not frame_list:
        await bot.send(ev, 'img/frame/路径下没有任何头像框，请联系维护组检查目录')
    if frame_tmp not in frame_list:
        msg = f'文件名输入错误，命令样例：\n更换头像框 color.png\n目前可选文件有：\n' + '\n'.join(frame_list)
        await bot.send(ev, msg)
    data = {str(user_id): frame_tmp}
    current_dir = os.path.join(os.path.dirname(__file__), 'frame.json')
    with open(current_dir, 'r', encoding='UTF-8') as f:
        f_data = load(f)
    f_data['customize'] = data
    with open(current_dir, 'w', encoding='UTF-8') as rf:
        dump(f_data, rf, indent=4, ensure_ascii=False)
    await bot.send(ev, f'已成功选择头像框:{frame_tmp}')
    frame_path = os.path.join(os.path.dirname(__file__), f'img/frame/{frame_tmp}')
    msg = MessageSegment.image(f'file:///{os.path.abspath(frame_path)}')
    await bot.send(ev, msg)

@sv.on_fullmatch('查竞技场头像框', '查询竞技场头像框', '查询头像框')
async def see_a_see_frame(bot, ev):
    user_id = str(ev.user_id)
    current_dir = os.path.join(os.path.dirname(__file__), 'frame.json')
    with open(current_dir, 'r', encoding='UTF-8') as f:
        f_data = load(f)
    id_list = list(f_data['customize'].keys())
    if user_id not in id_list:
        frame_tmp = f_data['default_frame']
    else:
        frame_tmp = f_data['customize'][user_id]
    path = os.path.join(os.path.dirname(__file__), f'img/frame/{frame_tmp}')
    msg = MessageSegment.image(f'file:///{os.path.abspath(path)}')
    await bot.send(ev, msg)

#========================================函数========================================
def save_binds():
    with open(config, 'w') as fp:
        dump(root, fp, indent=4)

def delete_arena(uid):
    '''
    订阅删除方法
    '''
    #JJCH._remove(bind_cache[uid]['id'])
    bind_cache.pop(uid)
    save_binds()

async def renew_pcrid_list():
    global bind_cache, pcrid_list, lck, lck_friendList, friendList
    pcrid_list=[]
    async with lck_friendList:
        copy_friendList = friendList
    if len(copy_friendList)==0:
        await renew_friendlist()
        async with lck_friendList:
            copy_friendList = friendList
    if len(copy_friendList)==0:
        return
    async with lck:        
        for qid in bind_cache:
            if bind_cache[qid]["notice_on"] == False:
                continue
            else:
                if qid not in copy_friendList and bind_cache[qid]["private"]:
                    bind_cache[qid]["notice_on"] = False
                    continue
                for i in bind_cache[qid]["pcrid"]:
                    pcrid_list.append(int(i))
    pcrid_list = list(set(pcrid_list))

async def resolve0(data):
    global cache, timeStamp
    timeStamp = int(time.time())
    try:
        info = data["res"]['user_info']
    except:
        return
    pcrid = data["uid"]
    res = [int(info['arena_rank']), int(info['grand_arena_rank']), int(info['last_login_time']), 0, 0]
    if pcrid not in cache:
        cache[pcrid] = res
    else:
        last = deepcopy(cache[pcrid])
        cache[pcrid][0] = res[0]
        cache[pcrid][1] = res[1]
        cache[pcrid][2] = res[2]
        if res[0] != last[0]:
            if res[0] < last[0]:
                cache[pcrid][3] += 1    #今日jjc排名上升次数+1
            await sendNotice(res[0],last[0],pcrid,1)
        if res[1] != last[1]:
            if res[1] < last[1]:
                cache[pcrid][4] += 1    #今日pjjc排名上升次数+1
            await sendNotice(res[1],last[1],pcrid,2)
        if res[2] != last[2]:
            if (res[2]-last[2]) < 60:      #最后上线时间变动小于60秒，不提醒，不刷新缓存。
                cache[pcrid][2] = last[2]
            else:
                await sendNotice(res[2],0,pcrid,3)

async def resolve1(data):
    global bind_cache, lck
    res=data["res"]
    bot=data["bot"]
    ev=data["ev"]
    pcrid = data["uid"]
    try:
        sv.logger.info('开始生成竞技场查询图片...') # 通过log显示信息
        result_image = await generate_info_pic(res,pcrid)
        result_image = pic2b64(result_image) # 转base64发送，不用将图片存本地
        result_image = MessageSegment.image(result_image)
        result_support = await generate_support_pic(res,pcrid)
        result_support = pic2b64(result_support) # 转base64发送，不用将图片存本地
        result_support = MessageSegment.image(result_support)
        sv.logger.info('竞技场查询图片已准备完毕！')
        for sid in get_self_ids():
            try:
                await bot.send_group_msg(self_id = sid,group_id = int(ev.group_id),message =  f"\n{str(result_image)}\n{result_support}")
                break
            except Exception as e:
                pass
    except ApiException as e:
            await bot.send(ev,f'查询出错，{e}', at_sender=True)
           
async def resolve2(data):
    global bind_cache, cache, lck
    res = data["res"]['user_info']
    bot = data["bot"]
    ev = data["ev"]
    i = data["index"]
    pcrid = data["uid"]
    manual_query_list_name =  data["list"]
    query_list = query_cache[ev.user_id]
    last_login_hour = (int(res["last_login_time"])%86400//3600+8)%24
    last_login_min = int(res["last_login_time"])%3600//60
    last_login_min = '%02d' % last_login_min        #分钟补零，变成2位
    if manual_query_list_name[i]:
        res["user_name"] = manual_query_list_name[i]
    extra = ''
    if pcrid in cache:
        extra = f'''上升: {cache[pcrid][3]}次 / {cache[pcrid][4]}次\n'''
    query =f'【{i+1}】{res["user_name"]}\n{res["arena_rank"]}({res["arena_group"]}场) / {res["grand_arena_rank"]}({res["grand_arena_group"]}场)\n{extra}最近上号{last_login_hour}：{last_login_min}\n\n'
    async with lck:
        query_list.append(query) 
        if len(query_list) == len(manual_query_list_name):
            query_list.sort()
            pic = image_draw(''.join(query_list))
            for sid in get_self_ids():
                try:
                    await bot.send_group_msg(self_id = sid,group_id = int(ev.group_id),message =  f'[CQ:image,file={pic}]')
                    break
                except Exception as e:
                    pass

async def resolve3(data):
    global bind_cache, lck, friendList
    bot=data["bot"]
    ev=data["ev"]
    nickname = data["nickname"]
    pcrid = data["uid"]
    friendList = data['friendlist']
    try:
        res = data["res"]['user_info']
        qid = str(ev.user_id)
        gid = ev.group_id
        async with lck:
            if qid in bind_cache:
                bind_num = len(bind_cache[qid]["pcrid"])
                if bind_num >= 8:
                    reply = '您订阅了太多账号啦！'
                elif pcrid in bind_cache[qid]["pcrid"]:
                    reply = '这个uid您已经订阅过了，不要重复订阅！'
                else:
                    bind_cache[qid]["pcrid"].append(pcrid)
                    bind_cache[qid]["pcrName"].append(nickname if nickname else res["user_name"])
                    bind_cache[qid]["noticeType"].append(1100)
                    reply = '添加成功！'
            else:          
                bind_cache[qid] = {
                    "pcrid": [pcrid],
                    "noticeType": [1100],
                    "pcrName": [nickname if nickname else res["user_name"]],
                    "gid": gid,
                    "bot_id": 0,
                    "private":False,
                    "notice_on":False
                }
                reply = '添加成功！'
                if gid == 0:
                    bind_cache[qid]["private"] = True
                    if len(friendList):
                        await renew_friendlist()
                    if qid in friendList:
                        pri_user = 0
                        for i in bind_cache:
                            if bind_cache[i]['notice_on'] and bind_cache[i]['private']:
                                pri_user += 1
                        if pri_user >= MAX_PRI:
                            reply += '私聊推送用户已达上限！无法开启私聊推送。你可以发送“在本群推送”，改为群聊推送。'
                        else:
                            bind_cache[qid]["notice_on"] = True
                            reply_adm = f'''{qid}添加了私聊pcrjjc推送'''
                            await bot.send_private_msg(user_id = SUPERUSERS[0], message = reply_adm)
                            reply += '已为您开启推送。由于是私聊推送，已通知管理员！'
                    else:
                        reply += '开启私聊推送需要先加好友！你也可以发送“在本群推送”，改为群聊推送。'
                else:
                    bind_cache[qid]["notice_on"] = True
                    reply +='已为您开启群聊推送！'
            save_binds()
    except:
        reply = f'找不到这个uid，大概率是你输错了！'
    for sid in get_self_ids():
        try:
            await bot.send_group_msg(self_id = sid,group_id = int(ev.group_id),message =  reply)
            break
        except Exception as e:
            pass

async def sendNotice(new:int, old:int, pcrid:int, noticeType:int):   #noticeType：1:jjc排名变动   2:pjjc排名变动  3:登录时间刷新
    global bind_cache
    global timeStamp, jjc_log, today_notice
    print('sendNotice   sendNotice    sendNotice')
    bot = get_bot()
    if noticeType == 3:
        change = '上线了！'
    else:
        jjc_log_new = (timeStamp, noticeType, new, old)
        if pcrid in jjc_log:
            if len(jjc_log[pcrid]) >= 20:
                del jjc_log[pcrid][0]
            jjc_log[pcrid].append(jjc_log_new)
        else:
            jjc_log_new_tmp = []
            jjc_log_new_tmp.append(jjc_log_new)
            jjc_log[pcrid] = jjc_log_new_tmp
        if noticeType == 1:
            change = '\njjc: '
        elif noticeType == 2:
            change = '\npjjc: '
        if new < old:
            change += f'''{old}->{new} [▲{old-new}]'''
        else:
            change += f'''{old}->{new} [▽{new-old}]'''
#-----------------------------------------------------------------  
    for qid in bind_cache:
        if bind_cache[qid]["notice_on"] == False:
            continue
        for i in range(len(bind_cache[qid]["pcrid"])):
            if bind_cache[qid]["pcrid"][i] == pcrid:
                msg = ''
                tmp = bind_cache[qid]["noticeType"][i]
                name = bind_cache[qid]["pcrName"][i]
                jjcNotice = True if tmp//1000 else False
                pjjcNotice = True if (tmp%1000)//100 else False
                riseNotice = True if (tmp%100)//10 else False
                onlineNotice = True if tmp%10 else False
                if (((noticeType == 1 and jjcNotice) or (noticeType == 2 and pjjcNotice)) and (riseNotice or (new>old))) or (noticeType ==3 and onlineNotice):
                    msg = name + change
                    today_notice += 1
                    if bind_cache[qid]["private"] == True:
                        for sid in get_self_ids():
                            try:
                                await bot.send_private_msg(self_id = sid, user_id=int(qid), message = msg)
                                return
                            except:
                                pass
                        bind_cache[qid]["notice_on"] = False
                    else:
                        msg += '[CQ:at,qq=' + qid + ']'
                        for sid in get_self_ids():
                            try:
                                await bot.send_group_msg(self_id = sid,group_id = int(bind_cache[qid]["gid"]), message = msg)
                                break
                            except Exception as e:
                                pass
                break

#========================================AUTO========================================
@sv.scheduled_job('interval', hours = 5)
async def renew_friendlist():
    global friendList, lck_friendList
    bot = get_bot()
    old_friendList = friendList
    for sid in get_self_ids():
        flist = await bot.get_friend_list(self_id = sid)
        async with lck_friendList:
            friendList = []
            for i in flist:
                friendList.append(str(i['user_id']))
            old_friendList = list(set(old_friendList))
            friendList = list(set(friendList))


@sv.on_notice('friend_add')     #新增好友时，不全部刷新好友列表
async def friend_add(session: NoticeSession):
    global friendList
    ev = session.event
    new_friend = str(ev.user_id)
    async with lck_friendList:
        friendList.append(new_friend)

@sv.scheduled_job('interval', minutes=0.3) # minutes是刷新频率，可按自身服务器性能输入其他数值，可支持整数、小数
async def on_arena_schedule():
    global pcrid_list
    if len(pcrid_list) ==0:
        await renew_pcrid_list()
    for uid in pcrid_list:
        await queue.put((10,(resolve0,uid,{"uid":uid})))

@sv.on_notice('group_decrease.leave')
async def leave_notice(session: NoticeSession):
    global lck, binds
    uid = str(session.ctx['user_id'])
    gid = str(session.ctx['group_id'])
    bot = get_bot()
    async with lck:
        bind_cache = deepcopy(binds)
        info = bind_cache[uid]
        if uid in binds and info['gid'] == gid:
            delete_arena(uid)
            await bot.send_group_msg(group_id = int(info['gid']),message = f'{uid}退群了，已自动删除其绑定在本群的竞技场订阅推送')

@sv.scheduled_job('cron', hour='5')
def clear_ranking_rise_time():
    global cache, today_notice ,yesterday_notice
    yesterday_notice = today_notice
    today_notice = 0
    for pcrid in cache:
        if pcrid in pcrid_list:
            cache[pcrid][3] = 0
            cache[pcrid][4] = 0
        else:
            del cache[pcrid]

#========================================TODO========================================
'''
@sv.on_prefix('竞技场历史')
async def send_arena_history(bot, ev):
    #竞技场历史记录

    global bind_cache, lck
    uid = str(ev['user_id'])
    if uid not in bind_cache:
        await bot.send(ev, '未绑定竞技场', at_sender=True)
    else:
        ID = bind_cache[uid]['id']
        msg = f'\n{JJCH._select(ID, 1)}'
        await bot.send(ev, msg, at_sender=True)

@sv.on_prefix('公主竞技场历史')
async def send_parena_history(bot, ev):
    global bind_cache, lck
    uid = str(ev['user_id'])
    if uid not in bind_cache:
        await bot.send(ev, '未绑定竞技场', at_sender=True)
    else:
        ID = bind_cache[uid]['id']
        msg = f'\n{JJCH._select(ID, 0)}'
        await bot.send(ev, msg, at_sender=True)
'''

