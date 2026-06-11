import asyncio, traceback, urllib3, re, requests, pathlib
from maimai_py import DivingFishProvider, LXNSProvider, IProvider, IScoreProvider, IScoreUpdateProvider, MaimaiClient, MaimaiClientMultithreading, MaimaiScores, PlayerIdentifier, InvalidPlayerIdentifierError, PrivacyLimitationError, Score, LevelIndex, FCType, FSType, RateType, SongType
from typing import List, Optional, Any, Callable, Literal, Iterable
from datetime import datetime


from nonebot import NoneBot
from hoshino.typing import CQEvent
from .database import UserDatabase
from . import log, sv


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
VITE_API_URL = "https://salt_api_main.realtvop.top"
VITE_API_FALLBACK_URL = "https://salt_api_backup.realtvop.top"


class MyProvider(IScoreProvider):
    async def get_scores_all(self, identifier: PlayerIdentifier, client: MaimaiClient) -> list[Score]:
        url = f"{VITE_API_URL}/updateUser"
        response = requests.post(url, json=self._deser_identifier(identifier), verify=False)
        if response.status_code == 200:
            raw_result = response.json()
        else:
            url_fallback = f"{VITE_API_FALLBACK_URL}/updateUser"
            response_fallback = requests.post(url_fallback, json=self._deser_identifier(identifier), verify=False)
            if response_fallback.status_code == 200:
                raw_result = response_fallback.json()
            else:
                raise Exception(f"主API和备用API均无法访问，状态码分别为 {response.status_code} 和 {response_fallback.status_code}")
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
            fc=FCType(4-int(score['comboStatus'])) if int(score['comboStatus']) else FCType.APP if achievement == 101.0 else None,
            fs=FSType(int(score['syncStatus']) % 5) if int(score['syncStatus']) else None,
            dx_score=int(score['deluxscoreMax']),
            dx_rating=None,
            play_count=None,
            play_time=None,
            rate=RateType._from_achievement(achievement),
            type=SongType._from_id(song_id)
        )


class MyMaimaiClient(MaimaiClientMultithreading):
    async def delta_updates_chain(
        self,
        source: list[tuple[IScoreProvider, Optional[PlayerIdentifier], dict[str, Any]]],
        target: list[tuple[IProvider, Optional[PlayerIdentifier], dict[str, Any]]],
        source_mode: Literal["fallback", "parallel"] = "fallback",
        target_mode: Literal["fallback", "parallel"] = "parallel",
        source_gather_callback: Optional[Callable[[MaimaiScores, Optional[BaseException], dict[str, Any]], None]] = None,
        target_gather_callback: Optional[Callable[[MaimaiScores, Optional[BaseException], dict[str, Any]], None]] = None,
        target_update_callback: Optional[Callable[[MaimaiScores, Optional[BaseException], dict[str, Any]], None]] = None,
    ) -> None:
        """类似于updates_chain函数的链式更新，但在更新阶段仅上传增量更新（即与原成绩相比有变化的部分），而不是全部成绩。"""
        # 合并成绩对象迭代列表
        def _join_rev(scores: Iterable[Score]) -> Score:
            scores_list = list(scores)
            if not scores_list:
                raise ValueError("至少需要一个 Score")
            res = scores_list[0]
            res.achievements = min(s.achievements or 0 for s in scores_list)
            res.dx_score = min(s.dx_score or 0 for s in scores_list)
            res.fc = FCType(min(s.fc.value for s in scores_list)) if all(s.fc is not None for s in scores_list) else None
            res.fs = FSType(max(s.fs.value for s in scores_list)) if all(s.fs is not None for s in scores_list) else None
            res.rate = RateType._from_achievement(res.achievements)
            res.play_count = min(s.play_count or 0 for s in scores_list)
            return res

        # 比较谱面信息，如果达成率与dx分数都没有变化，则返回None
        def _compare(score: Score, other: Optional[Score]) -> Score:
            if other is not None:
                if score.level_index != other.level_index or score.type != other.type:
                    raise ValueError("Cannot compare scores with different level indexes or types")
                if score.achievements <= other.achievements and score.dx_score <= other.dx_score:
                    return None
                score.achievements = max(score.achievements or 0, other.achievements or 0)
                score.dx_score = max(score.dx_score or 0, other.dx_score or 0)
                if score.fc != other.fc:
                    self_fc = score.fc.value if score.fc is not None else 100
                    other_fc = other.fc.value if other.fc is not None else 100
                    selected_value = min(self_fc, other_fc)
                    score.fc = FCType(selected_value) if selected_value != 100 else None
                if score.fs != other.fs:
                    self_fs = score.fs.value if score.fs is not None else -1
                    other_fs = other.fs.value if other.fs is not None else -1
                    selected_value = max(self_fs, other_fs)
                    score.fs = FSType(selected_value) if selected_value != -1 else None
                if score.rate != other.rate:
                    selected_value = min(score.rate.value, other.rate.value)
                    score.rate = RateType(selected_value)
                if score.play_count != other.play_count:
                    selected_value = max(score.play_count or 0, other.play_count or 0)
                    score.play_count = selected_value
            return score

        for t in target:  # 检查目标Provider是否为IScoreProvider和IScoreUpdateProvider的子类
            if not (isinstance(t[0], IScoreProvider)):
                raise ValueError(f"Target provider does not support score fetching. Please use providers that implement IScoreProvider for the target.")
            elif not (isinstance(t[0], IScoreUpdateProvider)):
                raise ValueError(f"Target provider does not support score updating. Please use providers that implement IScoreUpdateProvider for the target.")

        source_gather_tasks, target_gather_tasks, target_update_tasks = [], [], []
        empty_scores = await MaimaiScores(self).configure([])

        # Fetch scores from the source providers.
        for sp, ident, kwargs in source:
            if ident is not None:
                if source_mode == "parallel" or (source_mode == "fallback" and len(source_gather_tasks) == 0):
                    source_gather_task = asyncio.create_task(self.scores(ident, sp))
                    if source_gather_callback is not None:
                        source_gather_task.add_done_callback(
                            lambda t, k=kwargs: source_gather_callback(
                                t.result() if not t.exception() else empty_scores,
                                t.exception(),
                                k,
                            )
                        )
                    source_gather_tasks.append(source_gather_task)
        source_gather_results = await asyncio.gather(*source_gather_tasks, return_exceptions=True)
        source_maimai_scores_list = [result for result in source_gather_results if isinstance(result, MaimaiScores)]

        # Merge scores from all source maimai_scores instances.
        source_scores_unique: dict[str, Score] = {}
        for maimai_scores in source_maimai_scores_list:
            for score in maimai_scores.scores:
                score_key = f"{score.id} {score.type} {score.level_index}"
                source_scores_unique[score_key] = score._join(source_scores_unique.get(score_key, None))
        merged_source_scores = list(source_scores_unique.values())
        merged_source_maimai_scores = await MaimaiScores(self).configure(merged_source_scores)

        # Fetch scores from the target providers.
        for sp, ident, kwargs in target:
            if ident is not None:
                if target_mode == "parallel" or (target_mode == "fallback" and len(target_gather_tasks) == 0):
                    target_gather_task = asyncio.create_task(self.scores(ident, sp))
                    if target_gather_callback is not None:
                        target_gather_task.add_done_callback(
                            lambda t, k=kwargs: target_gather_callback(
                                t.result() if not t.exception() else empty_scores,
                                t.exception(),
                                k,
                            )
                        )
                    target_gather_tasks.append(target_gather_task)
        target_gather_results = await asyncio.gather(*target_gather_tasks, return_exceptions=True)
        target_maimai_scores_list = [result for result in target_gather_results if isinstance(result, MaimaiScores)]

        # Merge scores from all target maimai_scores instances.
        target_scores_dict_list = [
            {f"{score.id} {score.type} {score.level_index}": score
            for score in maimai_scores.scores}
            for maimai_scores in target_maimai_scores_list
        ]
        common_keys = set(target_scores_dict_list[0].keys())
        for d in target_scores_dict_list[1:]:
            common_keys.intersection_update(d.keys())
        target_scores_unique = {k: _join_rev(d[k] for d in target_scores_dict_list) for k in common_keys}
        
        # Generate delta updates.
        delta_scores_unique: dict[str, Score] = {}
        for score in merged_source_maimai_scores.scores:
            score_key = f"{score.id} {score.type} {score.level_index}"
            delta_score = _compare(score, target_scores_unique.get(score_key, None))
            if delta_score is not None:
                delta_scores_unique[score_key] = delta_score
        delta_scores = list(delta_scores_unique.values())
        delta_maimai_scores = await MaimaiScores(self).configure(delta_scores)

        # Update scores to the target providers.
        for tp, ident, kwargs in target:
            if ident is not None:
                if target_mode == "parallel" or (target_mode == "fallback" and len(target_update_tasks) == 0):
                    target_task = asyncio.create_task(self.updates(ident, delta_scores, tp))
                    if target_update_callback is not None:
                        target_task.add_done_callback(
                            lambda t, k=kwargs: target_update_callback(delta_maimai_scores, t.exception(), k)
                        )
                    target_update_tasks.append(target_task)
        await asyncio.gather(*target_update_tasks, return_exceptions=True)


maimai = MyMaimaiClient(timeout=60)
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


async def update_score(user, qrcode: str = None, special_flag: bool = False, repeat_flag: bool = False, bot: NoneBot = None, ev: CQEvent = None) -> tuple[str, str]:
    """上传分数主函数"""
    def source_gather_callback(scores: MaimaiScores, err: Optional[BaseException], context: dict) -> None:
        if err:
            log.error(f"从{context.get('name')}源获取数据失败:\n{''.join(traceback.format_exception(type(err), err, err.__traceback__))}")
        else:
            log.info(f"从{context.get('name')}源获取数据成功，共 {len(scores.scores)} 条成绩")

    def target_gather_callback(scores: MaimaiScores, err: Optional[BaseException], context: dict) -> None:
        if err:
            log.error(f"从{context.get('name')}源获取数据失败:\n{''.join(traceback.format_exception(type(err), err, err.__traceback__))}")
        else:
            log.info(f"从{context.get('name')}源获取数据成功，共 {len(scores.scores)} 条成绩")

    def target_update_callback(scores: MaimaiScores, err: Optional[BaseException], context: dict) -> None:
        if err:
            log.error(f"更新到目标{context.get('name')}失败:\n{''.join(traceback.format_exception(type(err), err, err.__traceback__))}")
        else:
            log.info(f"更新到目标{context.get('name')}成功，共 {len(scores.scores)} 条成绩")

    dftoken = user[1]
    lxtoken = user[2]
    userid = user[3]
    lastupdate = user[4]
    if not repeat_flag:
        if not lastupdate:
            if special_flag:
                await bot.send(ev, '推分了？你先别急', at_sender=False)
            else:
                await bot.send(ev, '正在上传分数，请稍等...', at_sender=False)
        else:
            if special_flag:
                await bot.send(ev, f'推分了？你先别急\n你上次啥时候导的: {lastupdate}', at_sender=False)
            else:
                await bot.send(ev, f'正在上传分数，请稍等...\n最近上传时间: {lastupdate}', at_sender=False)

    arcade_provider = MyProvider()
    arcade_player = MyProvider._ser_identifier(userid=userid, qrcode=qrcode)
    source_providers = [(arcade_provider, arcade_player, {"name": "arcade"})]
    
    target_providers = []
    if dftoken:
        diving_provider = DivingFishProvider()
        diving_player = PlayerIdentifier(credentials=dftoken)
        target_providers.append((diving_provider, diving_player, {"name": "divingfish"}))
    
    if lxtoken:
        lxns_provider = LXNSProvider()
        lxns_player = PlayerIdentifier(credentials=lxtoken)
        target_providers.append((lxns_provider, lxns_player, {"name": "lxns"}))

    if not qrcode:  # 简略上传需要对成绩进行补充
        task = asyncio.create_task(maimai.delta_updates_chain(source_providers, target_providers, "parallel", "parallel", source_gather_callback, target_gather_callback, target_update_callback))
    else:  # 全量上传直接上传原成绩
        task = asyncio.create_task(maimai.updates_chain(source_providers, target_providers, "parallel", "parallel", source_gather_callback, target_update_callback))

    update_tasks = []
    update_tasks.append(task)
    await asyncio.gather(*update_tasks)
    timenow = datetime.now().strftime(r"%Y-%m-%d %H:%M:%S")
    log.info("分数上传成功")

    if special_flag:
        return f'导出来了喵！\n你这次导的时间为: {timenow}\n怎么导的：{"简单的导" if not qrcode else "好好的导"}', timenow
    else:
        return f'上传分数至数据库成功！\n本次上传时间: {timenow}\n上传方式：{"简略上传" if not qrcode else "全量上传"}', timenow


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
            url = f"{VITE_API_URL}/getQRInfo"
            response = requests.post(url, json={"qrCode": qr_code}, verify=False)
            if response.status_code == 200:
                data = response.json()
            else:
                url_fallback = f"{VITE_API_FALLBACK_URL}/getQRInfo"
                response_fallback = requests.post(url_fallback, json={"qrCode": qr_code}, verify=False)
                if response_fallback.status_code == 200:
                    data = response_fallback.json()
                else:
                    raise Exception(f"主API和备用API均无法访问，状态码分别为 {response.status_code} 和 {response_fallback.status_code}")
            if data.get("errorID") == 0:
                user_id_from_qr = data.get("userID")
            else:
                msg = '二维码/链接解析失败，请检查内容是否正确/是否在有效期内'
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
        try:
            qqid = ev.user_id
            db = await get_db()
            user = await db.get_user(qqid)
            if user:
                dftoken = user[1]
                lxtoken = user[2]
                userid = user[3]
            else:
                msg = '未绑定任何账号，请先绑定微信二维码信息与水鱼账号，查看帮助请输入“上传分数帮助”'
                if special_flag:
                    msg = '几把怎么连导都不会。。。想知道怎么导？对我说“导帮助”喵'
                return
            if not (dftoken or lxtoken):
                msg = '请绑定水鱼或落雪成绩导入token信息'
                if special_flag:
                    msg = '没绑数据站你怎么导。。。'
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
            log.error(f"成绩导入token无效: {e}")
            msg = '成绩导入token无效，请检查成绩导入token的有效性'
        except PrivacyLimitationError as e:
            traceback.print_exc()
            log.error(f"隐私限制错误: {e}")
            msg = '你没有同意数据站的相关用户协议，无法完成该操作'
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
                    url = f"{VITE_API_URL}/getQRInfo"
                    response = requests.post(url, json={"qrCode": qr_code}, verify=False)
                    if response.status_code == 200:
                        data = response.json()
                    else:
                        url_fallback = f"{VITE_API_FALLBACK_URL}/getQRInfo"
                        response_fallback = requests.post(url_fallback, json={"qrCode": qr_code}, verify=False)
                        if response_fallback.status_code == 200:
                            data = response_fallback.json()
                        else:
                            raise Exception(f"主API和备用API均无法访问，状态码分别为 {response.status_code} 和 {response_fallback.status_code}")
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

@bindlx
async def _(bot: NoneBot, ev: CQEvent):
    try:
        qqid = ev.user_id
        db = await get_db()

        args: List[str] = ev.message.extract_plain_text().strip().split()
        msg = None
        if len(args) == 1 and args[0] == '帮助':
            msg = '绑定落雪/bindlx(不带斜杠) <落雪成绩导入token>: 绑定落雪成绩导入token，仅能在私聊绑定'
        elif ev['message_type'] == 'private':
            if len(args) == 1:
                lxns_provider = LXNSProvider()
                url, headers, _ = await lxns_provider._build_player_request("", PlayerIdentifier(credentials=args[0]), maimai)
                resp = await maimai._client.get(url, headers=headers)
                lxns_provider._check_response_player(resp)["data"]
                await db.update_user(qq=qqid, lxtoken=args[0])
                msg = '绑定落雪成绩导入token成功'
            else:
                msg = '请提供正确格式的落雪成绩导入token'
        else:
            msg = '只有私聊才能进行绑定操作哦'
    except InvalidPlayerIdentifierError as e:
        traceback.print_exc()
        log.error(f"落雪成绩导入token无效: {e}")
        msg = '落雪成绩导入token无效，请检查成绩导入token的有效性'
    except PrivacyLimitationError as e:
        traceback.print_exc()
        log.error(f"隐私限制错误: {e}")
        msg = '你没有同意落雪的用户协议，无法完成该操作'
    except Exception as e:
        traceback.print_exc()
        log.error(f"发生意外错误: {e}")
        msg = '绑定落雪token失败，请反馈给开发者！'
    finally:
        await bot.send(ev, msg, at_sender=False)
