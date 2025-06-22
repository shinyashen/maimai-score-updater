import asyncio, traceback
from maimai_py import DivingFishProvider, ArcadeProvider, MaimaiClient, PlayerIdentifier, AimeServerError, TitleServerNetworkError, TitleServerBlockedError, InvalidPlayerIdentifierError, PrivacyLimitationError
from httpx import HTTPError
from PIL import Image
from pyzbar.pyzbar import decode
from typing import List
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler


from nonebot import NoneBot, on_startup
from hoshino.typing import CQEvent
from .database import UserDatabase
from . import log, sv, SV_HELP

maimai = MaimaiClient(timeout=60)
diving_provider = DivingFishProvider()

bindwx = sv.on_prefix(['bindwx', '绑定微信'])
binddf = sv.on_prefix(['binddf', '绑定水鱼'])
update = sv.on_prefix(['wmupdate', '上传分数', '导'])
autoupdate = sv.on_suffix(['autoupdate', '自动上传'])


async def is_login(qrcode_credentials: str) -> bool:
    """检查玩家是否登录"""
    try:
        identifier = PlayerIdentifier(credentials=qrcode_credentials)
        player = await ArcadeProvider().get_player(identifier, maimai)
        return player.is_login
    except Exception:
        return False


async def check_df_valid(username: str, password: str):
    """检查水鱼账号是否有效"""
    login_json = {"username": username, "password": password}
    resp = await maimai._client.post("https://www.diving-fish.com/api/maimaidxprober/login", json=login_json)
    DivingFishProvider()._check_response_player(resp)


async def execute_update(user: tuple, db: UserDatabase):
    """执行分数上传"""
    qqid = user[0]
    username = user[1]
    password = user[2]
    qrcode_credentials = user[3]
    if not username or not password or not qrcode_credentials:
        log.error(f"用户 {qqid} 的信息不完整")
    try:
        await update_score(qqid, username, password, qrcode_credentials, 1, db)
    except Exception as e:
        traceback.print_exc()
        log.error(f"自动上传分数失败: {e}")


async def update_user_status(user: tuple, db: UserDatabase):
    """更新用户状态"""
    qq = user[0]
    login = user[2]
    logouttime = user[3]
    qrcode_credentials = await db.get_user_credential(qq)
    log_status = await is_login(qrcode_credentials)
    if log_status:  # 用户正在上机
        if login == 0:  # 之前登录状态为离线
            await db.update_status(qq=qq, login=1, logouttime=0)
    else:  # 用户不在上机
        if login == 1:  # 之前登录状态为在线
            await db.update_status(qq=qq, login=0, logouttime=1)
        else:  # 之前登录状态为离线
            if logouttime > 0 and logouttime < 3:  # 上机后离线，但是离线时间较短
                await db.update_status(qq=qq, login=0, logouttime=logouttime+1)
            elif logouttime >= 3:  # 上机后离线，且离线时间较长
                await db.update_status(qq=qq, login=0, logouttime=0)


async def auto_update(db: UserDatabase):
    log.info("开始执行自动上传分数任务")

    # 更新用户登录状态
    await db.connect()
    users = await db.get_autoupdate_user(1)
    tasks = []
    if users:
        for user in users:
            task = asyncio.create_task(update_user_status(user, db))
            tasks.append(task)
        await asyncio.gather(*tasks)
    await db.close()
    log.info(f"已更新用户登录状态，共更新 {len(tasks)} 个用户")

    # 为符合要求的用户执行自动上传分数操作
    await db.connect()
    users = await db.get_autoupdate_user(3)
    tasks = []
    if users:
        for user in users:
            task = asyncio.create_task(execute_update(user, db))
            tasks.append(task)
        await asyncio.gather(*tasks)
    await db.close()
    log.info(f"已自动上传分数，共上传了 {len(tasks)} 个用户的数据")


async def auto_update_loop():
    """自动上传分数循环任务"""
    # 将之前处于登录状态的用户先执行一次自动上传
    db = UserDatabase()  # TODO: db实例是否需要分离
    await db.connect()
    prev_users = await db.get_autoupdate_user(2)
    tasks = []
    if prev_users:
        for user in prev_users:
            task = asyncio.create_task(execute_update(user, db))
            tasks.append(task)
        await asyncio.gather(*tasks)
    await db.init_user_status()  # 初始化状态
    await db.close()
    log.info(f"已初始化所有用户状态")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(auto_update, 'cron', (db,), hour='0-1,10-23', minute='*')  # 每日10点到次日2点执行自动上传
    scheduler.start()


@on_startup
async def _():
    """bot启动时开启自动上传循环任务"""
    asyncio.create_task(auto_update_loop())


async def decode_qrcode(image_path: str) -> str:
    """从图片中识别二维码"""
    try:
        image = Image.open(image_path)
        decoded_objects = decode(image)
        if decoded_objects:
            return decoded_objects[0].data.decode('utf-8')
        else:
            raise ValueError("图片中不存在二维码！")
    except Exception as e:
        log.error(f"解析二维码时发生错误: {e}")
        raise e


async def update_score(qqid: str, username: str, password: str, qrcode_credentials:str, updatetype: int, db: UserDatabase, special_flag: bool = False, repeat_flag: bool = False, bot: NoneBot = None, ev: CQEvent = None) -> str:
    """上传分数主函数"""
    status = await db.get_status(qqid)
    if not status:
        await db.update_status(qq=qqid, autoupdate=0, login=0, logouttime=0)
        if updatetype == 0 and not repeat_flag:
            if special_flag:
                await bot.send(ev, '正在帮你导，你先别急', at_sender=False)
            else:
                await bot.send(ev, '正在上传分数，请稍等...', at_sender=False)
    elif status[4] is None or status[5] is None:
        if updatetype == 0 and not repeat_flag:
            if special_flag:
                await bot.send(ev, '正在帮你导，你先别急', at_sender=False)
            else:
                await bot.send(ev, '正在上传分数，请稍等...', at_sender=False)
    else:
        lastupdate = status[4]
        type = status[5]
        if updatetype == 0 and not repeat_flag:
            if special_flag:
                await bot.send(ev, f'正在帮你导，你先别急\n你上次啥时候导的：{lastupdate}\n怎么导的：{"自动档" if type == 1 else "手动档"}', at_sender=False)
            else:
                await bot.send(ev, f'正在上传分数，请稍等...\n上次上传时间: {lastupdate}\n上传方式: {"自动" if type == 1 else "手动"}', at_sender=False)

    update_tasks = []
    identifier = PlayerIdentifier(credentials=qrcode_credentials)
    scores = await maimai.scores(identifier, ArcadeProvider())
    diving_player = PlayerIdentifier(username=username, credentials=password)
    task = asyncio.create_task(maimai.updates(diving_player, scores.scores, diving_provider))
    update_tasks.append(task)

    await asyncio.gather(*update_tasks)
    log.info("分数上传成功")
    timenow = datetime.now().strftime(r"%Y-%m-%d %H:%M:%S")
    await db.update_status(qq=qqid, lastupdate=timenow, updatetype=updatetype)

    if special_flag:
        return f'水鱼接受了你的导！\n你这次导的时间为: {timenow}'
    else:
        return f'上传分数至水鱼成功！\n上传时间: {timenow}'


@update
async def _(bot: NoneBot, ev: CQEvent):
    args: List[str] = ev.message.extract_plain_text().strip().split()
    if len(args) == 1 and args[0] == '帮助':
        await bot.send(ev, SV_HELP, at_sender=False)
    elif len(args) == 0:
        msg = None
        special_flag = (ev.raw_message == '导')
        try:
            qqid = ev.user_id
            db = UserDatabase()
            await db.connect()
            user = await db.get_user(qqid)
            if user:
                username = user[1]
                password = user[2]
                qrcode_credentials = user[3]
            else:
                msg = '未绑定任何账号，请先绑定微信二维码信息与水鱼账号，查看帮助请输入“上传分数帮助”'
                if special_flag:
                    msg = '几把怎么连导都不会。。。想知道怎么导？对我说“导帮助”喵'
                return
            if not username or not password:
                msg = '请绑定水鱼账号信息'
                if special_flag:
                    msg = '没绑水鱼账号你怎么导。。。'
                return
            if not qrcode_credentials:
                msg = '请绑定微信二维码信息'
                if special_flag:
                    msg = '没绑微信二维码你怎么导。。。'
                return

            max_retries = 5
            retry_count = 0
            while retry_count <= max_retries:
                try:
                    msg = await update_score(qqid, username, password, qrcode_credentials, 0, db, special_flag, (retry_count > 0), bot, ev)
                    break  # 成功则退出循环
                except (TitleServerNetworkError, HTTPError) as e:
                    retry_count += 1
                    if retry_count > max_retries:
                        # 重试次数用尽，记录错误
                        traceback.print_exc()
                        log.error(f"重试失败 ({max_retries}次): {e}")
                        msg = '阿偶，出现了一些问题，请稍后再试'
                        if special_flag:
                            msg = '我导不动了，你等会再导喵'
                        break

                    # 指数退避延迟 (0.5s, 1s, 2s, 4s, 8s)
                    delay = (0.5 * (2 ** (retry_count - 1)))
                    log.warning(f"第 {retry_count}/{max_retries} 次重试 (等待 {delay}s)")
                    await asyncio.sleep(delay)

        except TitleServerBlockedError as e:
            traceback.print_exc()
            log.error(f"Title服务器错误: {e}")
            msg = '主机被华立屏蔽了喵，请反馈给开发者！'
        except InvalidPlayerIdentifierError as e:
            traceback.print_exc()
            log.error(f"水鱼账户无效: {e}")
            msg = '水鱼账户无效，可能是账户或者密码输错了，请重新绑定水鱼账号密码'
        except PrivacyLimitationError as e:
            traceback.print_exc()
            log.error(f"隐私限制错误: {e}")
            msg = '你没有同意水鱼的用户协议，无法完成该操作'
        except Exception as e:
            traceback.print_exc()
            log.error(f"发生意外错误: {e}")
            msg = '上传分数失败，请反馈给开发者！'
        finally:
            await bot.send(ev, msg, at_sender=False)
            await db.close()


@bindwx
async def _(bot: NoneBot, ev: CQEvent):
    try:
        max_retries = 5
        retry_count = 0
        while retry_count <= max_retries:
            try:
                qqid = ev.user_id
                db = UserDatabase()
                await db.connect()

                args: List[str] = ev.message.extract_plain_text().strip().split()
                msg = None
                if len(args) == 1 and args[0] == '帮助':
                    msg = '绑定微信/bindwx(不带斜杠) <SGWCMAID...>: 绑定微信公众号二维码，请对二维码进行识别后复制识别的内容，以SGWCMAID开头，仅能在私聊绑定'
                elif ev['message_type'] == 'private':
                    if len(args) == 1 and args[0].startswith('SGWCMAID'):
                        identifier = await ArcadeProvider().get_identifier(args[0], maimai)
                        await db.update_user(qq=qqid, sgwcmaid=identifier.credentials)
                        msg = '绑定微信二维码信息成功'
                    else:
                        msg = '请提供正确格式的二维码文本内容，以SGWCMAID开头'
                else:
                    msg = '只有私聊才能进行绑定操作哦'
                break

            except (TitleServerNetworkError, HTTPError) as e:
                retry_count += 1
                if retry_count > max_retries:
                    # 重试次数用尽，记录错误
                    traceback.print_exc()
                    log.error(f"重试失败 ({max_retries}次): {e}")
                    msg = '连接到服务器时出现了一些问题，请稍后再试'
                    break

                # 指数退避延迟 (0.5s, 1s, 2s, 4s, 8s)
                delay = (0.5 * (2 ** (retry_count - 1)))
                log.warning(f"第 {retry_count}/{max_retries} 次重试 (等待 {delay}s)")
                await asyncio.sleep(delay)

    except AimeServerError as e:
        traceback.print_exc()
        log.error(f"Aime服务器错误: {e}")
        msg = '二维码内容无效或者已过期，请重试'
    except TitleServerBlockedError as e:
        traceback.print_exc()
        log.error(f"Title服务器错误: {e}")
        msg = '主机被华立屏蔽了喵，请反馈给开发者！'
    except Exception as e:
        traceback.print_exc()
        log.error(f"发生意外错误: {e}")
        msg = '绑定微信失败，请反馈给开发者！'
    finally:
        await bot.send(ev, msg, at_sender=False)
        await db.close()


@binddf
async def _(bot: NoneBot, ev: CQEvent):
    try:
        qqid = ev.user_id
        db = UserDatabase()
        await db.connect()

        args: List[str] = ev.message.extract_plain_text().strip().split()
        msg = None
        if len(args) == 1 and args[0] == '帮助':
            msg = '绑定水鱼/binddf(不带斜杠) <水鱼账号> <水鱼密码>: 绑定水鱼账号信息，仅能在私聊绑定'
        elif ev['message_type'] == 'private':
            if len(args) == 2:
                username = args[0]
                password = args[1]
                await check_df_valid(username, password)

                await db.update_user(qq=qqid, username=username, password=password)
                msg = '绑定水鱼账号信息成功'
            else:
                msg = '请提供正确格式的水鱼账号信息'
        else:
            msg = '只有私聊才能进行绑定操作哦'
    except HTTPError as e:
        traceback.print_exc()
        log.error(f"水鱼服务器错误: {e}")
        msg = '连接到服务器时出现了一些问题，请稍后再试'
    except InvalidPlayerIdentifierError as e:
        traceback.print_exc()
        log.error(f"水鱼账户无效: {e}")
        msg = '水鱼账户无效，可能是账户或者密码输错了，请重新输入'
    except PrivacyLimitationError as e:
        traceback.print_exc()
        log.error(f"隐私限制错误: {e}")
        msg = '你没有同意水鱼的用户协议，无法完成该操作'
    except Exception as e:
        traceback.print_exc()
        log.error(f"发生意外错误: {e}")
        msg = '绑定水鱼失败，请反馈给开发者！'
    finally:
        await bot.send(ev, msg, at_sender=False)
        await db.close()


@autoupdate
async def _(bot: NoneBot, ev: CQEvent):
    args = ev.message.extract_plain_text().strip().lower()
    if args == '开启':
        db = UserDatabase()
        await db.connect()
        qqid = ev.user_id
        await db.update_status(qq=qqid, autoupdate=1, login=0, logouttime=0)
        msg = '已开启自动上传分数'
    elif args == '关闭':
        db = UserDatabase()
        await db.connect()
        qqid = ev.user_id
        await db.update_status(qq=qqid, autoupdate=0, login=0, logouttime=0)
        msg = '已关闭自动上传分数'
    else:
        msg = '请提供正确的指令格式'

    await bot.send(ev, msg, at_sender=False)
