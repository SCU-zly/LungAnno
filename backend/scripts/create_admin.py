"""一次性脚本：创建/重置管理员账号（新部署 bootstrap 用）。

用法（cwd=backend）:
  python3 scripts/create_admin.py <用户名> <密码>

密码至少 8 位。用户已存在且为 admin 时重置其密码；已存在但非 admin 时拒绝
（避免悄悄提权）；不存在则创建 role='admin' 的新用户。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth.password import hash_password
from app.database import SessionLocal
from app.models.user import User


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    username, password = sys.argv[1], sys.argv[2]
    if len(password) < 8:
        print("密码至少 8 位")
        sys.exit(1)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user:
            if user.role != "admin":
                print(f"用户 {username!r} 已存在且角色为 {user.role!r}，拒绝提权操作")
                sys.exit(1)
            user.password_hash = hash_password(password)
            print(f"已重置管理员 {username!r} 的密码")
        else:
            db.add(User(username=username, password_hash=hash_password(password), role="admin"))
            print(f"已创建管理员 {username!r}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
