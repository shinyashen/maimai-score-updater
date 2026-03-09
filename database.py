import databases


from . import log
from pathlib import Path
from typing import Optional


Root: Path = Path(__file__).parent
Database: Path = Root / 'users.db'


class UserDatabase:
    _instance: Optional['UserDatabase'] = None

    def __init__(self, db: databases.Database) -> None:
        self._db = db

    @classmethod
    async def get_instance(cls) -> 'UserDatabase':
        if cls._instance is not None:
            return cls._instance

        db = databases.Database(f'sqlite+aiosqlite:///{Database.resolve()}')

        async with db:
            # 初始化数据库
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    qq TEXT PRIMARY KEY,          -- QQ号作为主键
                    username TEXT,                -- 用户名
                    password TEXT,                -- 加密后的密码
                    userid TEXT,                  -- userid(from SGWCMAID)
                    lastupdate TEXT               -- 最后成功更新时间
                );"""
            )

        cls._instance = cls(db)
        return cls._instance

    async def update_user(self, qq: str, username: str = None, password: str = None, userid: str = None, lastupdate: str = None):
        """更新用户信息"""
        insert_sql = """
        INSERT OR REPLACE INTO users (qq, username, password, userid, lastupdate)
        VALUES (:qq, :username, :password, :userid, :lastupdate)
        ON CONFLICT(qq) DO UPDATE SET
            username = COALESCE(excluded.username, users.username),
            password = COALESCE(excluded.password, users.password),
            userid = COALESCE(excluded.userid, users.userid),
            lastupdate = COALESCE(excluded.lastupdate, users.lastupdate)
        """
        async with self._db as db:
            await db.execute(insert_sql, {"qq": qq, "username": username, "password": password, "userid": userid, "lastupdate": lastupdate})

    async def get_user(self, qq: str) -> tuple:
        """根据QQ号获取用户信息"""
        query = "SELECT * FROM users WHERE qq = :qq"
        async with self._db as db:
            if result := await db.fetch_one(query, {"qq": qq}):
                return result
        log.warning(f"未找到用户{qq}的信息")
        return None

    async def delete_user(self, qq: str):
        """删除用户"""
        delete_sql = "DELETE FROM users WHERE qq = :qq"
        async with self._db as db:
            result = await db.fetch_all(delete_sql, {"qq": qq})

        if len(result) == 0:
            log.warning(f"未找到用户{qq}的信息")
        else:
            log.info(f"已删除用户{qq}的信息")
