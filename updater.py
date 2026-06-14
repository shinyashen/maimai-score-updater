import urllib3, pathlib
from typing import List


from nonebot import NoneBot
from hoshino.typing import CQEvent
from .maicore import *
from .database import UserDatabase
from . import sv


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
VITE_API_URL = "https://salt_api_main.realtvop.top"
VITE_API_FALLBACK_URL = "https://salt_api_backup.realtvop.top"


bindwx = sv.on_prefix(['bindwx', '微信绑定'])
binddf = sv.on_prefix(['binddf', '水鱼绑定'])
bindlx = sv.on_prefix(['bindlx', '落雪绑定'])
update = sv.on_prefix(['wmupdate', '上传分数', '传分', '导'])


async def get_db() -> UserDatabase:
    return await UserDatabase.get_instance()


async def send_forward_msg(bot: NoneBot, ev: CQEvent, msg_list: list[str] | dict[str, str], name: str = None, user_id: str = None):
    if isinstance(msg_list, list):
        msgs = [{
            "type": "node",
            "data": {
                "name": name or "bot",
                "uin": user_id or str(ev.self_id),
                "content": msg
            }
        } for msg in msg_list]
    elif isinstance(msg_list, dict):
        msgs = []
        for key, value in msg_list.items():
            if value == 'text':
                msgs.append({
                    "type": "node",
                    "data": {
                        "name": name or "bot",
                        "uin": user_id or str(ev.self_id),
                        "content": key
                    }
                })
            elif value == 'image':
                msgs.append({
                    "type": "node",
                    "data": {
                        "name": name or "bot",
                        "uin": user_id or str(ev.self_id),
                        "content": {
                            "type": "image",
                            "data": {
                                "file": key
                            }
                        }
                    }
                })
    # onebot api
    if ev['message_type'] != 'private':
        await bot.send_group_forward_msg(group_id=ev.group_id, messages=msgs)
    else:
        await bot.send_private_forward_msg(user_id=ev.user_id, messages=msgs)


@update
async def _(bot: NoneBot, ev: CQEvent):
    args: List[str] = ev.message.extract_plain_text().strip().split()
    if len(args) == 1 and args[0] == '帮助':
        pic_uri = (pathlib.Path(__file__).parent.resolve() / 'import_token.jpg').as_uri()
        help_msg = {
            "上传国服maimaiDX成绩至成绩数据库，指令*不带斜杠*，仅能在*私聊*进行相关绑定操作。": "text",
            "参数说明：尖角括号<>包裹的是必填参数，方括号[]包裹的是可选参数。请不要连带括号一起输入！": "text",
            "指令列表：": "text",
            "1. 微信绑定/bindwx <SGWCMAID.../https...>: 绑定微信公众号二维码，可输入二维码进行识别后的内容(SGWCMAID开头)，或者二维码页面的网页链接(https开头)": "text",
            "2. 水鱼绑定/binddf <水鱼成绩导入token>: 绑定水鱼成绩导入token": "text",
            f"{pic_uri}": "image",
            "3. 落雪绑定/bindlx <落雪成绩导入token>: 绑定落雪成绩导入token，在https://maimai.lxns.net/user/profile?tab=thirdparty页面的“个人 API 密钥”标签中可以找到": "text",
            "4. 上传分数/导/传分/wmupdate [SGWCMAID.../https...]: 上传分数数据至绑定的成绩数据库，全量上传时仅支持私聊": "text",
            "上传说明：若上传指令不带有二维码信息，则默认进行简略上传，*仅上传*达成率与dx分数；若上传指令带有二维码信息，则进行全量上传。": "text"
        }
        await send_forward_msg(bot, ev, help_msg, name="上传帮助")
    else:
        qr_code = None
        user_id_from_qr = None
        if ev['message_type'] == 'private' and len(args) > 0:  # 私聊且提供了参数
            if len(args) == 1:
                msg, user_id_from_qr = await get_user_id(args[0])
                if not user_id_from_qr:  # 参数格式正确但解析失败
                    await bot.send(ev, msg, at_sender=False)
                    return
            else:
                msg = '请提供正确格式的内容(SGWCMAID.../https...)！'
                await bot.send(ev, msg, at_sender=False)
                return

        elif ev['message_type'] != 'private' and (len(args) == 1 and (args[0].startswith('SGWCMAID') or args[0].startswith('https'))):  # 非私聊但提供了合法参数
            msg = '只有私聊才能进行绑定操作哦'
            await bot.send(ev, msg, at_sender=False)
            return
        elif len(args) == 0:  # 没有提供参数，进行简略上传
            pass
        else:  # 提供了参数但格式不合法
            return

        msg = None
        special_flag = (ev.raw_message[0] == '导')
        qqid = ev.user_id
        db = await get_db()
        user = await db.get_user(qqid)
        if user:
            dftoken = user[1]
            lxtoken = user[2]
            userid = user[3]
        else:
            msg = '几把怎么连导都不会。。。想知道怎么导？对我说“导帮助”喵' if special_flag else '未绑定任何账号，请先绑定微信二维码信息与水鱼账号，查看帮助请输入“上传分数帮助”'
        if not (dftoken or lxtoken):
            msg = '没绑数据站你怎么导。。。' if special_flag else '请绑定水鱼或落雪成绩导入token信息'
        if not userid:
            msg = '没绑微信二维码你怎么导。。。' if special_flag else '请绑定微信二维码信息'
        elif user_id_from_qr and str(userid) != str(user_id_from_qr):
            msg = '怎么，还想帮别人导一导？' if special_flag else '你提供的二维码所对应账号与之前绑定的账号不匹配，请检查后重新输入'

        if not msg:
            msg, timenow = await update_score(user, qr_code, special_flag, bot, ev)
            if timenow:
                await db.update_user(qq=qqid, lastupdate=timenow)
        await bot.send(ev, msg, at_sender=False)


@bindwx
async def _(bot: NoneBot, ev: CQEvent):
    qqid = ev.user_id
    db = await get_db()

    args: List[str] = ev.message.extract_plain_text().strip().split()
    if len(args) == 1 and args[0] == '帮助':
        msg = '绑定微信/bindwx(不带斜杠) <SGWCMAID.../https...>: 绑定微信公众号二维码，请发送二维码进行识别后复制识别的内容(以SGWCMAID开头)，或者发送二维码页面的链接(以https开头)，仅能在私聊绑定'
    elif ev['message_type'] == 'private':
        if len(args) == 1:
            msg, user_id = await get_user_id(args[0])
            if user_id:  # 成功解析出用户ID
                await db.update_user(qq=qqid, userid=user_id)
        else:
            msg = '请提供正确格式的内容(SGWCMAID.../https...)！'
    else:
        msg = '只有私聊才能进行绑定操作哦'

    await bot.send(ev, msg, at_sender=False)


@binddf
async def _(bot: NoneBot, ev: CQEvent):
    qqid = ev.user_id
    db = await get_db()

    args: List[str] = ev.message.extract_plain_text().strip().split()
    if len(args) == 1 and args[0] == '帮助':
        msg = '绑定水鱼/binddf(不带斜杠) <水鱼成绩导入token>: 绑定水鱼成绩导入token，仅能在私聊绑定'
    elif ev['message_type'] == 'private':
        if len(args) == 1:
            msg, token = await get_valid_dftoken(args[0])
            if token:  # token有效
                await db.update_user(qq=qqid, dftoken=token)
        else:
            msg = '请提供正确格式的水鱼成绩导入token'
    else:
        msg = '只有私聊才能进行绑定操作哦'

    await bot.send(ev, msg, at_sender=False)

@bindlx
async def _(bot: NoneBot, ev: CQEvent):
    qqid = ev.user_id
    db = await get_db()

    args: List[str] = ev.message.extract_plain_text().strip().split()
    msg = None
    if len(args) == 1 and args[0] == '帮助':
        msg = '绑定落雪/bindlx(不带斜杠) <落雪成绩导入token>: 绑定落雪成绩导入token，仅能在私聊绑定'
    elif ev['message_type'] == 'private':
        if len(args) == 1:
            msg, token = await get_valid_lxtoken(args[0])
            if token:  # token有效
                await db.update_user(qq=qqid, lxtoken=token)
        else:
            msg = '请提供正确格式的落雪成绩导入token'
    else:
        msg = '只有私聊才能进行绑定操作哦'

    await bot.send(ev, msg, at_sender=False)
