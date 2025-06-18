import aiosqlite


from . import log
from pathlib import Path


Root: Path = Path(__file__).parent
Database: Path = Root / 'users.db'


class UserDatabase:
    def __init__(self, db_name=Database.resolve()):
        self.db_name = db_name
        self.conn = None

    async def connect(self):
        """连接到数据库，进行可能的初始化操作"""
        self.conn = await aiosqlite.connect(self.db_name)
        await self.initialize_database()
        log.info(f"成功连接到数据库: {self.db_name}")

    async def initialize_database(self):
        """初始化数据库表结构"""
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS users (
            qq TEXT PRIMARY KEY,          -- QQ号作为主键
            username TEXT,                -- 用户名
            password TEXT,                -- 加密后的密码
            sgwcmaid TEXT                 -- SGWCMAID
        );
        """
        await self.conn.execute(create_table_sql)
        await self.conn.commit()
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS status (
            qq TEXT PRIMARY KEY,          -- QQ号作为主键
            autoupdate INTEGER,           -- 自动更新标志
            login INTEGER,                -- 登录标志(0未登录1已登录)
            logouttime INTEGER,           -- 已登出时间(min)
            lastupdate TEXT,              -- 最后成功更新时间
            updatetype INTEGER            -- 登录类型(0手动1自动)
        );
        """
        await self.conn.execute(create_table_sql)
        await self.conn.commit()
        log.info("数据库初始化完成")

    async def update_user(self, qq: str, username: str = None, password: str = None, sgwcmaid: str = None):
        """更新用户信息"""
        # 检查QQ号是否已存在
        insert_sql = """
        INSERT OR REPLACE INTO users (qq, username, password, sgwcmaid)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(qq) DO UPDATE SET
            username = COALESCE(excluded.username, users.username),
            password = COALESCE(excluded.password, users.password),
            sgwcmaid = COALESCE(excluded.sgwcmaid, users.sgwcmaid)
        """
        await self.conn.execute(insert_sql, (qq, username, password, sgwcmaid))
        await self.conn.commit()
        log.info(f"已更新用户{qq}的信息")

    async def get_user(self, qq: str) -> tuple:
        """根据QQ号获取用户信息"""
        query = "SELECT * FROM users WHERE qq = ?"
        async with self.conn.execute(query, (qq,)) as cursor:
            user = await cursor.fetchone()

        if not user:
            log.warning(f"未找到用户{qq}的信息")
            return None

        return user

    async def get_user_credential(self, qq: str) -> str:
        """根据QQ号获取用户凭证信息"""
        query = "SELECT sgwcmaid FROM users WHERE qq = ?"
        async with self.conn.execute(query, (qq,)) as cursor:
            sgwcmaid = await cursor.fetchone()

        if not sgwcmaid:
            log.warning(f"未找到用户{qq}的凭证信息")
            return None

        return sgwcmaid[0]

    async def update_status(self, qq: str, autoupdate: int = None, login: int = None, logouttime: int = None, lastupdate: str = None, updatetype: int = None):
        """更新用户状态信息"""
        insert_sql = """
        INSERT OR REPLACE INTO status (qq, autoupdate, login, logouttime, lastupdate, updatetype)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(qq) DO UPDATE SET
            autoupdate = COALESCE(excluded.autoupdate, status.autoupdate),
            login = COALESCE(excluded.login, status.login),
            logouttime = COALESCE(excluded.logouttime, status.logouttime),
            lastupdate = COALESCE(excluded.lastupdate, status.lastupdate),
            updatetype = COALESCE(excluded.updatetype, status.updatetype)
        """
        await self.conn.execute(insert_sql, (qq, autoupdate, login, logouttime, lastupdate, updatetype))
        await self.conn.commit()
        log.info(f"已更新用户{qq}的状态信息")

    async def get_status(self, qq: str) -> tuple:
        """根据QQ号获取用户状态信息"""
        query = "SELECT * FROM status WHERE qq = ?"
        async with self.conn.execute(query, (qq,)) as cursor:
            status = await cursor.fetchone()

        if not status:
            log.warning(f"未找到用户{qq}的状态信息")
            return None

        return status

    async def get_autoupdate_user(self, mode: int = 0):
        if mode == 1:  # 返回所有需要自动更新的用户的状态信息
            query = "SELECT * FROM status WHERE autoupdate = 1"
            async with self.conn.execute(query) as cursor:
                users = await cursor.fetchall()

            if not users:
                return None

            return users

        elif mode == 2:  # 返回login状态为1的用户信息
            query = "SELECT * FROM users WHERE qq IN (SELECT qq FROM status WHERE autoupdate = 1 AND login = 1)"
            async with self.conn.execute(query) as cursor:
                users = await cursor.fetchall()

            if not users:
                return None

            return users

        elif mode == 3:  # 返回login状态为0同时logouttime为3的用户信息
            query = "SELECT * FROM users WHERE qq IN (SELECT qq FROM status WHERE autoupdate = 1 AND login = 0 AND logouttime = 3)"
            async with self.conn.execute(query) as cursor:
                users = await cursor.fetchall()

            if not users:
                return None

            return users

    async def init_user_status(self):
        """初始化用户状态信息"""
        sql = """
        UPDATE status
        SET login = 0, logouttime = 0
        WHERE autoupdate = 1
        """
        await self.conn.execute(sql)
        await self.conn.commit()

    async def delete_user(self, qq: str):
        """删除用户"""
        delete_sql = "DELETE FROM users WHERE qq = ?"
        cursor = await self.conn.execute(delete_sql, (qq,))

        if cursor.rowcount == 0:
            log.warning(f"未找到用户{qq}的信息")
        else:
            await self.conn.commit()
            log.info(f"已删除用户{qq}的信息")

    async def close(self):
        """异步关闭数据库连接"""
        if self.conn:
            await self.conn.close()
            log.info("数据库连接已关闭")
