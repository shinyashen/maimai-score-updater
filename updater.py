import asyncio, traceback, urllib3, re, requests
from maimai_py import DivingFishProvider, IScoreProvider, MaimaiClient, MaimaiScores, PlayerIdentifier, InvalidPlayerIdentifierError, PrivacyLimitationError, Score, LevelIndex, FCType, FSType, RateType, SongType
from typing import List, Optional
from datetime import datetime


from nonebot import NoneBot
from hoshino.typing import CQEvent
from .database import UserDatabase
from . import log, sv, SV_HELP


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
VITE_SUPABASE_URL = "https://salt_api_main.realtvop.top"


class MyProvider(IScoreProvider):
    async def get_scores_all(self, identifier: PlayerIdentifier, client: MaimaiClient) -> list[Score]:
        url = f"{VITE_SUPABASE_URL}/updateUser"
        response = requests.post(url, json=self._deser_identifier(identifier), verify=False)
        response.raise_for_status()
        raw_result = response.json()
        return [self._deser_score(music) for entry in raw_result['userMusicList'] for music in entry.get('userMusicDetailList')]

    @staticmethod
    def _ser_identifier(userid: str | None = None, qrcode: str | None = None) -> PlayerIdentifier:
        return PlayerIdentifier(credentials={"userid": userid or "", "qrcode": qrcode or ""})

    @staticmethod
    def _deser_identifier(identifier: PlayerIdentifier) -> dict[str, str]:
        assert isinstance(identifier.credentials, dict), "Identifier credentials should be a dictionary."
        userid = identifier.credentials.get("userid")
        if qrcode := identifier.credentials.get("qrcode"):
            return {"userId": userid, "importToken": "", "qrCode": qrcode}
        return {"userId": userid, "importToken": ""}

    @staticmethod
    def _deser_score(score: dict) -> Score:
        song_id = int(score['musicId'])
        achievement = float(int(score['achievement'])/10000)
        return Score(
            id=song_id if song_id > 100000 else song_id % 10000,
            level=None,
            level_index=LevelIndex(int(score['level'])) if int(score['level']) < 5 else LevelIndex(0),  # utage=10
            achievements=achievement,
            fc=FCType(4-int(score['comboStatus'])) if int(score['comboStatus']) else None,
            fs=FSType(int(score['syncStatus']) % 5) if int(score['syncStatus']) else None,
            dx_score=int(score['deluxscoreMax']),
            dx_rating=None,
            play_count=None,
            play_time=None,
            rate=RateType._from_achievement(achievement),
            type=SongType._from_id(song_id)
        )


bindwx = sv.on_prefix(['bindwx', '绑定微信'])
binddf = sv.on_prefix(['binddf', '绑定水鱼'])
update = sv.on_prefix(['wmupdate', '上传分数', '导'])
autoupdate = sv.on_suffix(['autoupdate', '自动上传'])


async def get_db() -> UserDatabase:
    return await UserDatabase.get_instance()


async def send_forward_msg(bot: NoneBot, ev: CQEvent, msg_list: list[str], name: str = None, user_id: str = None):
    msgs = [{
        "type": "node",
        "data": {
            "name": name or "bot",
            "uin": user_id or str(ev.self_id),
            "content": msg
        }
    } for msg in msg_list]
    # onebot api
    if ev['message_type'] != 'private':
        await bot.send_group_forward_msg(group_id=ev.group_id, messages=msgs)
    else:
        await bot.send_private_forward_msg(user_id=user_id or str(ev.self_id), messages=msgs)


async def update_score(user, qrcode: str = None, special_flag: bool = False, repeat_flag: bool = False, bot: NoneBot = None, ev: CQEvent = None) -> tuple[str, str]:
    """上传分数主函数"""
    def source_callback(scores: MaimaiScores, err: Optional[BaseException], context: dict) -> None:
        if err:
            log.error(f"从{context.get('name')}源获取数据失败:\n{''.join(traceback.format_exception(type(err), err, err.__traceback__))}")
        else:
            log.info(f"从{context.get('name')}源获取数据成功，共 {len(scores.scores)} 条成绩")

    def target_callback(scores: MaimaiScores, err: Optional[BaseException], context: dict) -> None:
        if err:
            log.error(f"更新到目标{context.get('name')}失败:\n{''.join(traceback.format_exception(type(err), err, err.__traceback__))}")
        else:
            log.info(f"更新到目标{context.get('name')}成功，共 {len(scores.scores)} 条成绩")

    imtoken = user[1]
    userid = user[2]
    lastupdate = user[3]
    if not repeat_flag:
        if not lastupdate or lastupdate.lower() == 'none' or lastupdate.lower() == 'null' or lastupdate == '' or lastupdate.lower() == 0:
            if special_flag:
                await bot.send(ev, '推分了？你先别急', at_sender=False)
            else:
                await bot.send(ev, '正在上传分数，请稍等...', at_sender=False)
        else:
            if special_flag:
                await bot.send(ev, f'推分了？你先别急\n你上次啥时候导的: {lastupdate}', at_sender=False)
            else:
                await bot.send(ev, f'正在上传分数，请稍等...\n最近上传时间: {lastupdate}', at_sender=False)

    maimai = MaimaiClient(timeout=60)
    diving_provider = DivingFishProvider()
    arcade_provider = MyProvider()
    diving_player = PlayerIdentifier(credentials=imtoken)
    arcade_player = MyProvider._ser_identifier(userid=userid, qrcode=qrcode)
    source_providers = [(arcade_provider, arcade_player, {"name": "arcade"})]
    target_providers = [(diving_provider, diving_player, {"name": "divingfish"})]
    if not qrcode:  # 简略上传需要对成绩进行补充
        source_providers.append((diving_provider, diving_player, {"name": "divingfish"}))
    task = asyncio.create_task(maimai.updates_chain(source_providers, target_providers, "parallel", "parallel", source_callback, target_callback))

    update_tasks = []
    update_tasks.append(task)
    await asyncio.gather(*update_tasks)
    timenow = datetime.now().strftime(r"%Y-%m-%d %H:%M:%S")
    log.info("分数上传成功")

    if special_flag:
        return f'水鱼接受了你的导！\n你这次导的时间为: {timenow}\n怎么导的：{"简单的导" if not qrcode else "好好的导"}', timenow
    else:
        return f'上传分数至水鱼成功！\n本次上传时间: {timenow}\n上传方式：{"简略上传" if not qrcode else "全量上传"}', timenow


@update
async def _(bot: NoneBot, ev: CQEvent):
    args: List[str] = ev.message.extract_plain_text().strip().split()
    if len(args) == 1 and args[0] == '帮助':
        help_msg = [
            "上传国服maimaiDX成绩至水鱼数据库，指令*不带斜杠*，仅能在*私聊*进行相关绑定操作。",
            "参数说明：尖叫括号<>包裹的是必填参数，方括号[]包裹的是可选参数。请不要连带括号一起输入！",
            "指令列表：",
            "1. 绑定微信/bindwx <SGWCMAID.../https...>: 绑定微信公众号二维码，可输入二维码进行识别后的内容(SGWCMAID开头)，或者二维码页面的网页链接(https开头)",
            "2. 绑定水鱼/binddf <水鱼成绩导入token>: 绑定水鱼成绩导入token",
            "3. 上传分数/导/wmupdate [SGWCMAID.../https...]: 上传分数数据至水鱼数据库，全量上传时仅支持私聊",
            "上传说明：若上传指令不带有二维码信息，则默认进行简略上传，*仅上传*达成率与dx分数；若上传指令带有二维码信息，则进行全量上传。"
        ]
        await send_forward_msg(bot, ev, help_msg, name="上传帮助")
    else:
        qr_code = None
        user_id_from_qr = None
        if ev['message_type'] == 'private' and len(args) > 0:
            if len(args) == 1 and args[0].startswith('SGWCMAID') and len(args[0]) == 84:
                qr_code = args[0][-64:]  # 取最后64个字符
            elif len(args) == 1 and args[0].startswith('https'):
                matches = re.findall(r'MAID.{0,76}', args[0])  # 匹配以MAID开头，后续0~76个字符
                if matches:
                    qr_code = matches[0][-64:]  # 取第一个匹配结果的最后64位
                else:
                    msg = '链接解析失败，请检查内容是否正确'
                    await bot.send(ev, msg, at_sender=False)
                    return
            else:
                msg = '请提供正确格式的内容(SGWCMAID.../https...)！'
                await bot.send(ev, msg, at_sender=False)
                return

            # from SaltNet
            url = f"{VITE_SUPABASE_URL}/getQRInfo"
            response = requests.post(url, json={"qrCode": qr_code}, verify=False)
            data = response.json()
            if data.get("errorID") == 0:
                user_id_from_qr = data.get("userID")
            else:
                msg = '二维码/链接解析失败，请检查内容是否正确/是否在有效期内'
                await bot.send(ev, msg, at_sender=False)
                return

        elif ev['message_type'] != 'private' and len(args) > 0:
            msg = '只有私聊才能进行绑定操作哦(若要简略上传，请不要输入指令以外的额外字符)'
            await bot.send(ev, msg, at_sender=False)
            return

        msg = None
        special_flag = (ev.raw_message[0] == '导')
        try:
            qqid = ev.user_id
            db = await get_db()
            user = await db.get_user(qqid)
            if user:
                imtoken = user[1]
                userid = user[2]
            else:
                msg = '未绑定任何账号，请先绑定微信二维码信息与水鱼账号，查看帮助请输入“上传分数帮助”'
                if special_flag:
                    msg = '几把怎么连导都不会。。。想知道怎么导？对我说“导帮助”喵'
                return
            if not imtoken:
                msg = '请绑定水鱼成绩导入token信息'
                if special_flag:
                    msg = '没绑水鱼token你怎么导。。。'
                return
            if not userid:
                msg = '请绑定微信二维码信息'
                if special_flag:
                    msg = '没绑微信二维码你怎么导。。。'
                return
            elif user_id_from_qr and str(userid) != str(user_id_from_qr):
                msg = '你提供的二维码所对应账号与之前绑定的账号不匹配，请检查后重新输入'
                if special_flag:
                    msg = '怎么，还想帮别人导一导？'
                return

            max_retries = 5
            retry_count = 0
            while retry_count <= max_retries:
                try:
                    msg, timenow = await update_score(user, qr_code, special_flag, (retry_count > 0), bot, ev)
                    await db.update_user(qq=qqid, lastupdate=timenow)
                    break  # 成功则退出循环
                except Exception as e:
                    retry_count += 1
                    if retry_count > max_retries:
                        raise e

                    # 指数退避延迟 (0.5s, 1s, 2s, 4s, 8s)
                    delay = (0.5 * (2 ** (retry_count - 1)))
                    log.warning(f"第 {retry_count}/{max_retries} 次重试 (等待 {delay}s)")
                    await asyncio.sleep(delay)

        except InvalidPlayerIdentifierError as e:
            traceback.print_exc()
            log.error(f"水鱼成绩导入token无效: {e}")
            msg = '水鱼成绩导入token无效，请检查成绩导入token的有效性'
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


@bindwx
async def _(bot: NoneBot, ev: CQEvent):
    try:
        max_retries = 5
        retry_count = 0
        while retry_count <= max_retries:
            try:
                qqid = ev.user_id
                db = await get_db()

                args: List[str] = ev.message.extract_plain_text().strip().split()
                msg = None
                if len(args) == 1 and args[0] == '帮助':
                    msg = '绑定微信/bindwx(不带斜杠) <SGWCMAID.../https...>: 绑定微信公众号二维码，请发送二维码进行识别后复制识别的内容(以SGWCMAID开头)，或者发送二维码页面的链接(以https开头)，仅能在私聊绑定'
                elif ev['message_type'] == 'private':
                    if len(args) == 1 and args[0].startswith('SGWCMAID') and len(args[0]) == 84:
                        qr_code = args[0][-64:]  # 取最后64个字符
                    elif len(args) == 1 and args[0].startswith('https'):
                        matches = re.findall(r'MAID.{0,76}', args[0])  # 匹配以MAID开头，后续0~76个字符
                        if matches:
                            qr_code = matches[0][-64:]  # 取第一个匹配结果的最后64位
                        else:
                            msg = '链接解析失败，请检查内容是否正确'
                            break
                    else:
                        msg = '请提供正确格式的内容(SGWCMAID.../https...)！'
                        break

                    # from SaltNet
                    url = f"{VITE_SUPABASE_URL}/getQRInfo"
                    response = requests.post(url, json={"qrCode": qr_code}, verify=False)
                    data = response.json()
                    if data.get("errorID") == 0:
                        user_id = data.get("userID")
                        await db.update_user(qq=qqid, userid=user_id)
                        msg = '绑定微信二维码信息成功'
                    else:
                        msg = '二维码/链接解析失败，请检查内容是否正确/是否在有效期内'
                        break
                else:
                    msg = '只有私聊才能进行绑定操作哦'
                break

            except Exception as e:
                retry_count += 1
                if retry_count > max_retries:
                    raise e

                # 指数退避延迟 (0.5s, 1s, 2s, 4s, 8s)
                delay = (0.5 * (2 ** (retry_count - 1)))
                log.warning(f"第 {retry_count}/{max_retries} 次重试 (等待 {delay}s)")
                await asyncio.sleep(delay)

    except Exception as e:
        traceback.print_exc()
        log.error(f"发生意外错误: {e}")
        msg = '绑定微信失败，请反馈给开发者！'
    finally:
        await bot.send(ev, msg, at_sender=False)


@binddf
async def _(bot: NoneBot, ev: CQEvent):
    try:
        qqid = ev.user_id
        db = await get_db()

        args: List[str] = ev.message.extract_plain_text().strip().split()
        msg = None
        if len(args) == 1 and args[0] == '帮助':
            msg = '绑定水鱼/binddf(不带斜杠) <水鱼成绩导入token>: 绑定水鱼成绩导入token，仅能在私聊绑定'
        elif ev['message_type'] == 'private':
            if len(args) == 1 and re.match(r'^[a-f0-9]{128}$', args[0]):
                await db.update_user(qq=qqid, imtoken=args[0])
                msg = '绑定水鱼成绩导入token成功'
            else:
                msg = '请提供正确格式的水鱼成绩导入token'
        else:
            msg = '只有私聊才能进行绑定操作哦'
    except InvalidPlayerIdentifierError as e:
        traceback.print_exc()
        log.error(f"水鱼成绩导入token无效: {e}")
        msg = '水鱼成绩导入token无效，请检查成绩导入token的有效性'
    except PrivacyLimitationError as e:
        traceback.print_exc()
        log.error(f"隐私限制错误: {e}")
        msg = '你没有同意水鱼的用户协议，无法完成该操作'
    except Exception as e:
        traceback.print_exc()
        log.error(f"发生意外错误: {e}")
        msg = '绑定水鱼token失败，请反馈给开发者！'
    finally:
        await bot.send(ev, msg, at_sender=False)
