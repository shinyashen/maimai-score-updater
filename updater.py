import asyncio
import traceback
from maimai_py import DivingFishProvider, ArcadeProvider, MaimaiClient, PlayerIdentifier
from PIL import Image
from pyzbar.pyzbar import decode
from typing import List


from nonebot import NoneBot
from hoshino.typing import CQEvent
from .database import UserDatabase
from . import log, sv

maimai = MaimaiClient(timeout=60)
diving_provider = DivingFishProvider()

bindwx = sv.on_prefix(['bindwx', '绑定微信'])
binddf = sv.on_prefix(['binddf', '绑定水鱼'])
update = sv.on_fullmatch(['wmupdate', '上传分数'])


async def decode_qrcode(image_path: str) -> str:
    """Decode a QR code from an image file."""
    try:
        image = Image.open(image_path)
        decoded_objects = decode(image)
        if decoded_objects:
            return decoded_objects[0].data.decode('utf-8')
        else:
            raise ValueError("No QR code found in the image.")
    except Exception as e:
        log.error(f"Error decoding QR code: {e}")
        raise e


@update
async def _(bot: NoneBot, ev: CQEvent):
    try:
        qqid = ev.user_id
        db = UserDatabase()
        await db.connect()
        result = await db.get_user(qqid)
        if result:
            username = result[1]
            password = result[2]
            qrcode_credentials = result[3]
        else:
            await bot.finish(ev, '未绑定任何账号，请先绑定微信二维码信息与水鱼账号。', at_sender=True)

        if not username or not password:
            await bot.finish(ev, '请绑定水鱼账号信息。', at_sender=True)
        if not qrcode_credentials:
            await bot.finish(ev, '请绑定微信二维码信息。', at_sender=True)

        update_tasks = []
        identifier = PlayerIdentifier(credentials=qrcode_credentials)
        scores = await maimai.scores(identifier, ArcadeProvider())
        diving_player = PlayerIdentifier(username=username, credentials=password)
        task = asyncio.create_task(maimai.updates(diving_player, scores.scores, diving_provider))
        update_tasks.append(task)

        await asyncio.gather(*update_tasks)
        log.info("Prober updated successfully.")
    except Exception as e:
        traceback.print_exc()
        log.error(f"An unexpected error occurred: {e}")
    finally:
        await db.close()

@bindwx
async def _(bot: NoneBot, ev: CQEvent):
    try:
        qqid = ev.user_id
        db = UserDatabase()
        await db.connect()

        args: List[str] = ev.message.extract_plain_text().strip().split()
        if len(args) == 1 and args[0] == '帮助':
            await bot.send(ev, '绑定微信/bindwx <SGWCMAID...>: 绑定微信公众号二维码，请对二维码进行识别后复制识别的内容，以SGWCMAID开头', at_sender=True)
        elif len(args) == 1 and args[0].startswith('SGWCMAID'):
            identifier = await maimai.qrcode(qrcode=args[0]).credentials
            await db.update(qq=qqid, sgwcmaid=identifier)
            await bot.send(ev, '绑定微信二维码信息成功。', at_sender=True)
        else:
            await bot.send(ev, '请提供正确格式的二维码文本内容。', at_sender=True)
    except Exception as e:
        traceback.print_exc()
        log.error(f"An unexpected error occurred: {e}")
    finally:
        await db.close()

@binddf
async def _(bot: NoneBot, ev: CQEvent):
    try:
        qqid = ev.user_id
        db = UserDatabase()
        await db.connect()

        args: List[str] = ev.message.extract_plain_text().strip().split()
        if len(args) == 1 and args[0] == '帮助':
            await bot.send(ev, '绑定水鱼/binddf <水鱼账号> <水鱼密码>: 绑定水鱼账号信息', at_sender=True)
        elif len(args) == 2:
            username = args[0]
            password = args[1]
            await db.update(qq=qqid, username=username, password=password)
            await bot.send(ev, '绑定水鱼账号信息成功。', at_sender=True)
        else:
            await bot.send(ev, '请提供正确格式的水鱼账号信息。', at_sender=True)
    except Exception as e:
        traceback.print_exc()
        log.error(f"An unexpected error occurred: {e}")
    finally:
        await db.close()
