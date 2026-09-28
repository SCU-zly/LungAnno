"""一次性/可复用脚本：把指定患者（含其全部 study/series/detections）复制为新批次。

用法（cwd=backend）:
  python3 scripts/duplicate_patients_to_batch.py <新批次名> <patient_id> [patient_id...]

  虚构示例: python3 scripts/duplicate_patients_to_batch.py 示例批次 DEMO_PATIENT_001 DEMO_PATIENT_002

语义:
- 按 studies.patient_id（DB 原样，含前导零）找到该患者全部 study 及其 series；
- study/series 行整体复制，UID 加 .dev 后缀绕唯一约束，存储路径原样复用
  （raw/preprocessed/meta 指向同一批文件，推理产物不需重跑）；
- series 的 processing_status 置 'detected'、review_status 置 'not_reviewed'
  （复制批次审核状态独立，不污染源批次）；
- detections 按新旧 series id 映射整表复制；
- 新批次已存在（同名）则报错退出，不合并不覆盖。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.batch import Batch
from app.models.batch_study import batch_studies
from app.models.detection import Detection
from app.models.series import Series
from app.models.study import Study

DEV_SUFFIX = ".dev"


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    batch_name = sys.argv[1]
    patient_ids = sys.argv[2:]

    db = SessionLocal()
    try:
        if db.query(Batch).filter(Batch.name == batch_name).first():
            print(f"批次 {batch_name!r} 已存在，退出（不合并不覆盖）")
            sys.exit(1)

        batch = Batch(name=batch_name, description=f"自既有批次复制的开发/测试用批次（患者: {', '.join(patient_ids)}）")
        db.add(batch)
        db.flush()

        total_series = 0
        total_dets = 0
        for pid in patient_ids:
            studies = db.query(Study).filter(Study.patient_id == pid).all()
            if not studies:
                print(f"警告: 未找到 patient_id={pid} 的 study，跳过")
                continue
            for st in studies:
                new_study = Study(
                    study_uid=(st.study_uid + DEV_SUFFIX)[:128],
                    patient_id=st.patient_id,
                    patient_name=st.patient_name,
                    study_date=st.study_date,
                )
                db.add(new_study)
                db.flush()
                db.execute(batch_studies.insert().values(batch_id=batch.id, study_id=new_study.id))

                series_list = db.query(Series).filter(Series.study_id == st.id).all()
                for s in series_list:
                    new_s = Series(
                        series_uid=(s.series_uid + DEV_SUFFIX)[:128],
                        study_id=new_study.id,
                        processing_status="detected",
                        review_status="not_reviewed",
                        raw_path=s.raw_path,
                        preprocessed_path=s.preprocessed_path,
                        meta_path=s.meta_path,
                        slice_thickness=s.slice_thickness,
                        series_description=s.series_description,
                    )
                    db.add(new_s)
                    db.flush()
                    total_series += 1

                    dets = db.query(Detection).filter(Detection.series_id == s.id).all()
                    for d in dets:
                        db.add(Detection(
                            series_id=new_s.id,
                            score=d.score,
                            box_x=d.box_x, box_y=d.box_y, box_z=d.box_z,
                            box_w=d.box_w, box_h=d.box_h, box_d=d.box_d,
                            voxel_x=d.voxel_x, voxel_y=d.voxel_y, voxel_z=d.voxel_z,
                            voxel_w=d.voxel_w, voxel_h=d.voxel_h, voxel_d=d.voxel_d,
                            slice_index=d.slice_index,
                            label=d.label,
                            source=d.source,
                        ))
                        total_dets += 1
                print(f"patient {pid}: study {st.study_uid[-16:]}… -> {len(series_list)} series")

        db.commit()
        print(f"完成: 批次 id={batch.id}「{batch_name}」，共 {total_series} series / {total_dets} detections")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
