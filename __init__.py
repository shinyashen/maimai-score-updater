from hoshino import Service, priv
from hoshino.log import new_logger


log = new_logger('maimai-score-updater')

SV_HELP = """上传国服maimaiDX成绩至水鱼数据库，指令*不带斜杠*，仅能在*私聊*进行相关绑定操作。请不要连带括号一起输入！
绑定微信/bindwx <SGWCMAID.../https...>: 绑定微信公众号二维码，可输入二维码进行识别后的内容(SGWCMAID开头)，或者二维码页面的网页链接(https开头)
绑定水鱼/binddf <水鱼成绩导入token>: 绑定水鱼成绩导入token
上传分数/导/wmupdate [SGWCMAID.../https...]: 上传分数数据至水鱼数据库，全量上传时仅支持私聊
上传说明：若上传指令不带有二维码信息，则默认进行简略上传，*仅上传*达成率与dx分数；若上传指令带有二维码信息，则进行全量上传。"""
sv = Service('maimai-score-updater', manage_priv=priv.ADMIN, enable_on_default=True, help_=SV_HELP)
