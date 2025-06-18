from hoshino import Service, priv
from hoshino.log import new_logger


log = new_logger('maimai-score-updater')

SV_HELP = """上传国服maimaiDX成绩至水鱼数据库，指令不带斜杠，仅能在私聊进行绑定操作
绑定微信/bindwx <SGWCMAID...>: 绑定微信公众号二维码，请对二维码进行识别后复制识别的内容，以SGWCMAID开头
绑定水鱼/binddf <水鱼ID> <水鱼密码>: 绑定水鱼账号
上传分数/wmupdate: 上传全量分数数据至水鱼数据库
开启/关闭 自动上传/autoupdate: 开启或关闭自动上传功能（输入例：开启自动上传，关闭autoupdate）
"""
sv = Service('maimai-score-updater', manage_priv=priv.ADMIN, enable_on_default=True, help_=SV_HELP)
