import traceback
import asyncio
from asyncio import Lock
from json import load, loads
from os.path import dirname, join
from nonebot import get_bot, on_command
from hoshino.aiorequests import get
from .geetest import public_address
from .pcrclient import pcrclient, ApiException, bsdkclient

bot = get_bot()
captcha_lck = Lock()
queue = asyncio.PriorityQueue()
otto = True
ordd = 'x'
validate = None
validating = False
acfirst = False
client = None
captcha_cnt = 0

with open(join(dirname(__file__), 'account.json')) as fp:
    acinfo = load(fp)


async def captchaVerifier(*args):
    global otto
    if len(args) == 0:
        return otto
    global captcha_cnt
    if len(args) == 1 and type(args[0]) == int:
        captcha_cnt = args[0]
        return captcha_cnt

    global acfirst, validating
    global validate, captcha_lck
    if not acfirst:
        await captcha_lck.acquire()
        acfirst = True

    validating = True
    if otto == False:
        gt = args[0]
        challenge = args[1]
        userid = args[2]
        online_url_head = f"https://help.tencentbot.top/geetest/"
        local_url_head = f"{public_address}/geetest/"
        url = f"?captcha_type=1&challenge={challenge}&gt={gt}&userid={userid}&gs=1"
        await bot.send_private_msg(
            user_id=acinfo[0]['admin'],
            message=f'pcr账号登录需要验证码，请完成以下链接中的验证内容后将第1个方框的内容点击复制，并加上"validate{ordd} "前缀发送给机器人完成验证'
                    f'\n示例：validate{ordd} 123456789\n您也可以发送 validate{ordd} auto 命令bot自动过验证码'
                    f'\n验证链接头：{local_url_head}链接{url}，备用链接头：{online_url_head}'
                    f'\n为避免tx网页安全验证使验证码过期，请手动拼接链接头和链接'
        )
        await captcha_lck.acquire()
        validating = False
        return validate

    while captcha_cnt < 5:
        captcha_cnt += 1
        try:
            print(f'测试新版自动过码中，当前尝试第{captcha_cnt}次。')

            await asyncio.sleep(1)
            uuid = loads(await (await get(url="https://pcrd.tencentbot.top/geetest")).content)["uuid"]
            print(f'uuid={uuid}')

            ccnt = 0
            while ccnt < 3:
                ccnt += 1
                await asyncio.sleep(5)
                res = await (await get(url=f"https://pcrd.tencentbot.top/check/{uuid}")).content
                res = loads(res)
                if "queue_num" in res:
                    nu = res["queue_num"]
                    print(f"queue_num={nu}")
                    tim = min(int(nu), 3) * 5
                    print(f"sleep={tim}")
                    await asyncio.sleep(tim)
                else:
                    info = res["info"]
                    if info in ["fail", "url invalid"]:
                        break
                    elif info == "in running":
                        await asyncio.sleep(5)
                    else:
                        print(f'info={info}')
                        validating = False
                        return info
        except:
            pass

    if captcha_cnt >= 5:
        otto = False
        await bot.send_private_msg(user_id=acinfo[0]['admin'],
                                   message=f'thread{ordd}: 自动过码多次尝试失败，可能为服务器错误，自动切换为手动。\n确实服务器无误后，可发送 validate{ordd} auto重新触发自动过码。')
        await bot.send_private_msg(user_id=acinfo[0]['admin'], message=f'thread{ordd}: Changed to manual')
        validating = False
        return "manual"

    await errlogger("captchaVerifier: uncaught exception")
    validating = False
    return False


async def errlogger(msg):
    # await bot.send_private_msg(user_id=acinfo['admin'], message=f'thread{ordd}: {msg}')
    print(f"pcrjjc: {msg}")


async def query(client):
    while True:
        try:
            DA = await queue.get()
            data = DA[1]
        except:
            await asyncio.sleep(1)
            continue
        try:
            if validating:
                await asyncio.sleep(1)
                raise ApiException('账号被风控，请联系管理员输入验证码并重新登录', -1)
            while client.shouldLogin:
                await client.login()
            res = (await client.callapi('/profile/get_profile', {'target_viewer_id': int(data[1])}))
            if 'user_info' not in res:  # 失败重连
                await client.login()
                res = (await client.callapi('/profile/get_profile', {'target_viewer_id': int(data[1])}))
            data[2]["res"] = res
            await data[0](data[2])
        except:
            traceback.print_exc()
        finally:
            queue.task_done()


@on_command(f'validate{ordd}')
async def validate(session):
    global binds, lck, validate, validating, captcha_lck, otto
    if session.ctx['user_id'] == acinfo[0]['admin']:
        validate = session.ctx['message'].extract_plain_text().replace(
            f"validate{ordd}", "").strip()
        if validate == "manual":
            otto = False
            await bot.send_private_msg(user_id=acinfo[0]['admin'], message=f'thread{ordd}: Changed to manual')
        elif validate == "auto":
            otto = True
            await bot.send_private_msg(user_id=acinfo[0]['admin'], message=f'thread{ordd}: Changed to auto')
        try:
            captcha_lck.release()
        except:
            pass


for i in acinfo:
    bclient = bsdkclient(i, captchaVerifier, errlogger)
    client = pcrclient(bclient)
    loop = asyncio.get_event_loop()
    loop.create_task(query(client))
