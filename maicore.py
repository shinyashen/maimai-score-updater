import asyncio, traceback, urllib3, re, requests
from maimai_py import DivingFishProvider, LXNSProvider, IProvider, IScoreProvider, IScoreUpdateProvider, MaimaiClient, MaimaiClientMultithreading, MaimaiScores, PlayerIdentifier, InvalidPlayerIdentifierError, InvalidDeveloperTokenError, PrivacyLimitationError, Score, LevelIndex, FCType, FSType, RateType, SongType
from typing import Optional, Any, Callable, Literal, Iterable
from datetime import datetime


from nonebot import NoneBot
from hoshino.typing import CQEvent
from . import log


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
            level_index=LevelIndex(int(score['level'])) if int(score['level']) < 5 else LevelIndex(0),  # 宴utage的值为10，兼容水鱼api直接取0
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

        # 检查目标Provider是否为IScoreProvider和IScoreUpdateProvider的子类，因为需要获取目标提供器的原成绩并进行增量更新，如果不支持获取成绩和更新成绩则无法进行增量更新操作
        for t in target:
            if not (isinstance(t[0], IScoreProvider)):
                raise ValueError(f"Target provider does not support score fetching. Please use providers that implement IScoreProvider for the target.")
            elif not (isinstance(t[0], IScoreUpdateProvider)):
                raise ValueError(f"Target provider does not support score updating. Please use providers that implement IScoreUpdateProvider for the target.")

        source_gather_tasks, target_gather_tasks, target_update_tasks = [], [], []
        empty_scores = await MaimaiScores(self).configure([])

        # 从源提供器获取成绩数据
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

        # 合并源提供器成绩
        source_scores_unique: dict[str, Score] = {}
        for maimai_scores in source_maimai_scores_list:
            for score in maimai_scores.scores:
                score_key = f"{score.id} {score.type} {score.level_index}"
                source_scores_unique[score_key] = score._join(source_scores_unique.get(score_key, None))
        merged_source_scores = list(source_scores_unique.values())
        merged_source_maimai_scores = await MaimaiScores(self).configure(merged_source_scores)

        # 从目标提供器获取成绩数据
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

        # 合并目标提供器成绩，原则为取歌曲、达成率和dx分数的最小交集，其他部分合并最高记录
        target_scores_dict_list = [
            {f"{score.id} {score.type} {score.level_index}": score
            for score in maimai_scores.scores}
            for maimai_scores in target_maimai_scores_list
        ]
        common_keys = set(target_scores_dict_list[0].keys())
        for d in target_scores_dict_list[1:]:
            common_keys.intersection_update(d.keys())
        target_scores_unique = {k: _join_rev(d[k] for d in target_scores_dict_list) for k in common_keys}
        
        # 与源成绩进行比较，找出增量更新部分
        delta_scores_unique: dict[str, Score] = {}
        for score in merged_source_maimai_scores.scores:
            score_key = f"{score.id} {score.type} {score.level_index}"
            delta_score = _compare(score, target_scores_unique.get(score_key, None))
            if delta_score is not None:
                delta_scores_unique[score_key] = delta_score
        delta_scores = list(delta_scores_unique.values())
        delta_maimai_scores = await MaimaiScores(self).configure(delta_scores)

        # 上传增量更新部分到目标提供器
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
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
VITE_API_URL = "https://salt_api_main.realtvop.top"
VITE_API_FALLBACK_URL = "https://salt_api_backup.realtvop.top"


async def get_valid_userid(info_str: str) -> tuple[str, str, str]:
    """从二维码信息字符串中提取用户ID，返回消息、二维码和用户ID构成的元组"""
    if info_str.startswith('SGWCMAID') and len(info_str) == 84:
        qr_code = info_str[-64:]  # 取最后64个字符
    elif info_str.startswith('https'):
        matches = re.findall(r'MAID.{0,76}', info_str)  # 匹配以MAID开头，后续0~76个字符
        if matches:
            qr_code = matches[0][-64:]  # 取第一个匹配结果的最后64位
        else:
            msg = '二维码链接解析失败，请检查内容是否正确'
            return msg, None, None
    else:
        msg = '请提供正确格式的内容(SGWCMAID.../https...)！'
        return msg, None, None

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
            log.error(f"主API和备用API均无法访问，状态码分别为 {response.status_code} 和 {response_fallback.status_code}")
            msg = '无法解析二维码，请联系开发者'
            return msg, None, None
    if data.get("errorID") == 0:
        msg = '绑定微信二维码信息成功'
        return msg, qr_code, data.get("userID")
    else:
        msg = '二维码/链接解析失败，请检查内容是否正确/是否在有效期内'
        return msg, None, None


async def get_valid_dftoken(dftoken: str) -> tuple[str, str]:
    """确保水鱼成绩导入token有效，返回消息和有效token的元组"""
    msg = None
    token = None
    try:
        if not re.match(r'^[a-f0-9]{128}$', dftoken):
            msg = '请提供正确格式的水鱼成绩导入token'
        else:
            empty_scores = await MaimaiScores(maimai).configure([])
            await DivingFishProvider().update_scores(PlayerIdentifier(credentials=dftoken), empty_scores.scores, maimai) # 上传空成绩测试token有效性，若无效会抛出异常
            msg = '绑定水鱼成绩导入token成功'
            token = dftoken
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
        return msg, token


async def get_valid_lxtoken(lxtoken: str) -> tuple[str, str]:
    """确保落雪成绩导入token有效，返回消息和有效token的元组"""
    msg = None
    token = None
    try:
        lxns_provider = LXNSProvider()
        url, headers, _ = await lxns_provider._build_player_request("", PlayerIdentifier(credentials=lxtoken), maimai)
        resp = await maimai._client.get(url, headers=headers)
        lxns_provider._check_response_player(resp)  # 获取玩家信息测试token有效性，若无效会抛出异常
        msg = '绑定落雪成绩导入token成功'
        token = lxtoken
    except (InvalidPlayerIdentifierError, InvalidDeveloperTokenError) as e:  # TODO: 等待maimai-py上游修复
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
        return msg, token


async def update_score(user, qrcode: str = None, special_flag: bool = False, bot: NoneBot = None, ev: CQEvent = None, max_retries: int = 3) -> tuple[str, str]:
    """上传分数主函数"""
    def gather_callback(scores: MaimaiScores, err: Optional[BaseException], context: dict) -> None:
        if err:
            log.error(f"从{context.get('name')}源获取数据失败:\n{''.join(traceback.format_exception(type(err), err, err.__traceback__))}")
        else:
            log.info(f"从{context.get('name')}源获取数据成功，共 {len(scores.scores)} 条成绩")

    def update_callback(scores: MaimaiScores, err: Optional[BaseException], context: dict) -> None:
        if err:
            log.error(f"更新到目标{context.get('name')}失败:\n{''.join(traceback.format_exception(type(err), err, err.__traceback__))}")
        else:
            log.info(f"更新到目标{context.get('name')}成功，共 {len(scores.scores)} 条成绩")

    try:
        msg, timenow = None, None
        timestart = datetime.now()
        
        while (retry_count := 0) <= max_retries:
            try:
                dftoken = user[1]
                lxtoken = user[2]
                userid = user[3]
                lastupdate = user[4]
                if retry_count == 0:  # 首次尝试才发送提示信息，重试时不再发送，避免刷屏
                    if not lastupdate:
                        await bot.send(ev, '推分了？你先别急' if special_flag else '正在上传分数，请稍等...', at_sender=False)
                    else:
                        await bot.send(ev, f'推分了？你先别急\n你上次啥时候导的: {lastupdate}' if special_flag else f'正在上传分数，请稍等...\n最近上传时间: {lastupdate}', at_sender=False)

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
                    task = asyncio.create_task(maimai.delta_updates_chain(source_providers, target_providers, "parallel", "parallel", gather_callback, gather_callback, update_callback))
                else:  # 全量上传直接上传原成绩
                    task = asyncio.create_task(maimai.updates_chain(source_providers, target_providers, "parallel", "parallel", gather_callback, update_callback))

                update_tasks = []
                update_tasks.append(task)
                await asyncio.gather(*update_tasks)
                timenow = datetime.now()
                duration = (timenow - timestart).total_seconds()
                target_str = "和".join(s for v, s in zip([dftoken, lxtoken], ["水鱼", "落雪"]) if v is not None)
                msg = f'导到{target_str}了喵！\n你这次导了{duration:.2f}秒，很厉害了喵~\n怎么导的：{"简单的导" if not qrcode else "好好的导"}' if special_flag else f'上传分数至{target_str}成功！\n本次上传用时{duration:.2f}秒\n上传方式：{"简略上传" if not qrcode else "全量上传"}'
                log.info("分数上传成功")
                break  # 成功则退出循环
            
            except Exception as e:
                retry_count += 1
                if retry_count > max_retries:
                    timenow = None
                    raise e

                # 指数退避延迟 (0.5s, 1s, 2s...)
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
        return msg, timenow.strftime(r"%Y-%m-%d %H:%M:%S") if timenow else None
