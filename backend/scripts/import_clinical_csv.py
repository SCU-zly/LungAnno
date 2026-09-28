"""一次性脚本：CSV 临床数据导入 patient_clinical 表（幂等 upsert）。

用法（backend venv，cwd=backend）:
  python3 scripts/import_clinical_csv.py --csv /path/to/clinical.csv

patient_id 规范化为 str(int(float(住院号)))（去前导零；NaN 跳过整行并计数）；
name 取「姓名」列；payload 收该行全部非空列（NaN 剔除，值转 str，日期保持原样字符串）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



def _read_csv(path: str):
    """encoding 自动试 utf-8-sig / gbk（Excel 导出常见两种）。"""
    import pandas as pd

    for enc in ("utf-8-sig", "gbk"):
        try:
            return pd.read_csv(path, encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    # 两种都解不了时让 pandas 抛原始错误
    return pd.read_csv(path)


def main():
    parser = argparse.ArgumentParser(description="导入临床 CSV（含住院号、姓名列）")
    parser.add_argument("--csv", required=True, help="临床 CSV 文件路径")
    args = parser.parse_args()

    import pandas as pd
    from app.database import SessionLocal
    from app.models.patient_clinical import PatientClinical

    df = _read_csv(os.path.abspath(os.path.expanduser(args.csv)))
    total = len(df)

    # 住院号规范化 + 表内去重：CSV 同一患者可能有多行（不同随访记录），
    # 保留非空字段最多的一行，否则同事务两次插入会撞 patient_id 唯一约束
    df["_pid"] = df["住院号"].apply(lambda v: str(int(float(v))) if pd.notna(v) and str(v).strip() else None)
    df["_nonempty"] = df.notna().sum(axis=1)
    df = df.sort_values("_nonempty", ascending=False).drop_duplicates("_pid", keep="first")

    written = 0
    skipped = 0

    db = SessionLocal()
    try:
        for _, row in df.iterrows():
            patient_id = row["_pid"]
            if not patient_id:
                skipped += 1
                continue

            name = row.get("姓名")
            name = str(name).strip() if pd.notna(name) else None

            payload = {}
            for col, val in row.items():
                if str(col).startswith("_") or pd.isna(val):  # 跳过辅助列与空值
                    continue
                text = str(val).strip()
                if text:
                    payload[str(col)] = text

            existing = db.query(PatientClinical).filter(PatientClinical.patient_id == patient_id).first()
            if existing:
                existing.name = name
                existing.payload = payload
            else:
                db.add(PatientClinical(patient_id=patient_id, name=name, payload=payload))
            written += 1

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"总行数: {total}，写入: {written}，跳过: {skipped}")


if __name__ == "__main__":
    main()
