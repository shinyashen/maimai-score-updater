from hoshino import Service, priv
from hoshino.log import new_logger


log = new_logger('maimai-score-updater')

SV_HELP = """上传国服maimaiDX成绩至水鱼数据库，指令*不带斜杠*，仅能在*私聊*进行相关绑定操作。要查看详细帮助信息，请发送“传分帮助”。"""
sv = Service('maimai-score-updater', manage_priv=priv.ADMIN, enable_on_default=True, help_=SV_HELP)
