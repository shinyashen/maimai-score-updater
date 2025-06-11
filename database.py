import aiosqlite
import os
import asyncio


from . import log


class UserDatabase:
    def __init__(self, db_name='users.db'):
        self.db_name = db_name
        self.conn = None

    async def connect(self):
        """异步连接到数据库，如果不存在则创建"""
        self.is_new_db = not os.path.exists(self.db_name)
        self.conn = await aiosqlite.connect(self.db_name)

        # 如果是新数据库，则初始化表结构
        if self.is_new_db:
            await self.initialize_database()
        log.info(f"成功连接到数据库: {self.db_name}")

    async def initialize_database(self):
        """初始化数据库表结构"""
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS users (
            qq TEXT PRIMARY KEY,     -- QQ号作为主键
            username TEXT NOT NULL,  -- 用户名
            password TEXT NOT NULL,  -- 加密后的密码
            sgwcmaid TEXT            -- SGWCMAID
        );
        """
        await self.conn.execute(create_table_sql)
        await self.conn.commit()
        log.info("数据库初始化完成，已创建 users 表")

    async def update(self, qq: str, username: str = None, password: str = None, sgwcmaid: str = None):
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
        query = "SELECT qq, username, password, sgwcmaid FROM users WHERE qq = ?"
        async with self.conn.execute(query, (qq,)) as cursor:
            user = await cursor.fetchone()

        if not user:
            log.warning(f"未找到用户{qq}的信息")
            return None

        return user

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
            print("数据库连接已关闭")

# 异步使用示例


async def main():
    # 创建数据库实例
    db = UserDatabase()

    try:
        # 连接数据库
        await db.connect()

        # 添加用户（密码会自动加密）
        await db.add_user("123456789", "张三", "zhangsan123", "SGW12345")
        await db.add_user("987654321", "李四", "lisi456", "SGW67890")

        # 查询所有用户（不显示密码）
        await db.get_all_users()

        # 验证用户
        print("\n验证用户:")
        await db.authenticate_user("123456789", "zhangsan123")  # 正确密码
        await db.authenticate_user("123456789", "wrongpass")    # 错误密码

        # 更新信息
        await db.update_username("123456789", "张老三")
        await db.update_password("123456789", "newpassword123")
        await db.update_sgwcmaid("123456789", "SGW_NEW_ID")

        # 获取单个用户信息
        print("\n获取用户信息:")
        user = await db.get_user("123456789")
        if user:
            qq, username, _, sgwcmaid = user
            print(f"QQ号: {qq}, 用户名: {username}, SGWCMAID: {sgwcmaid}")

        # 删除用户
        await db.delete_user("987654321")

        # 显示最终数据
        print("\n最终用户列表:")
        await db.get_all_users()

    finally:
        # 确保关闭数据库连接
        await db.close()

# 运行异步主函数
if __name__ == "__main__":
    asyncio.run(main())
