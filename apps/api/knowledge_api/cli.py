import argparse
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.db import get_engine
from knowledge_api.models import User
from knowledge_api.security import hash_password


def create_admin() -> None:
    username = input("管理员用户名: ").strip().lower()
    if not username or len(username) > 80:
        raise SystemExit("用户名长度必须为 1 到 80 个字符")
    password = getpass("管理员密码（至少 12 字符）: ")
    if len(password) < 12:
        raise SystemExit("密码至少需要 12 个字符")
    if password != getpass("再次输入密码: "):
        raise SystemExit("两次密码不一致")
    with Session(get_engine()) as db:
        if db.scalar(select(User).where(User.username == username)):
            raise SystemExit("用户名已存在")
        db.add(User(username=username, password_hash=hash_password(password), role="ADMIN"))
        db.commit()
    print("管理员创建成功")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create-admin"])
    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin()


if __name__ == "__main__":
    main()
