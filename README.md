# maimai-score-updater

[![python3](https://img.shields.io/badge/python-3.10-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

基于 [HoshinoBotV2](https://github.com/Ice-Cirno/HoshinoBot) 和 [maimai.py](https://github.com/TrueRou/maimai.py) 的街机音游 **舞萌DX** 的分数上传插件，进行相关账号绑定后可将分数在QQ聊天环境下直接上传至[水鱼数据库](https://www.diving-fish.com/maimaidx/prober/)，主要用于个人的QQ BOT。插件的主要用法可以通过给BOT发送`上传分数帮助`指令获取。

## 使用方法

1. python版本要求为3.10.x，低于这个版本无法满足maimai.py的版本要求，高于这个版本在安装HoshinoBot的需求模块时会出现matplotlib的编译安装问题。安装HoshinoBot框架的模块时依次执行以下指令(linux环境下)：

   ```bash
   sudo apt install libpng-dev libpng++-dev
   export MPLLOCALFREETYPE=1
   pip install -r requirements.txt --use-pep517
   ```

2. 将该项目放在HoshinoBot插件目录 `modules` 下，或者clone本项目

    ``` git
    git clone https://github.com/shinyashen/maimai-score-updater.git
    ```

3. 由于本插件涉及到了私聊相关的内容，而目前HoshinoBot框架本体并没有对私聊功能进行相关的支持，因此需要对HoshinoBot本体进行修改：

   - `msghandler.py`：去除以下内容(L10-11)：

      ```python
      if event.detail_type != 'group':
        return
      ```

4. 安装插件所需模块：`pip install -r requirements.txt`

5. 在 `config/__bot__.py` 模块列表中添加 `maimai-score-updater`

6. 重启HoshinoBot

## MIT License

您可以自由使用本项目的代码用于商业或非商业的用途，但必须附带 MIT 授权协议。
