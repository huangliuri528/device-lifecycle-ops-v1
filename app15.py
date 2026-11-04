# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-11-04（第 15 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 15 天新增与修改的源代码，不含第 1~14 天已有代码
#           本次迭代主题：新增人员资质培训管理模块（课程建档、开班报名、签到考核、自动发证、证书复审、结课归档）
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 15 天新增业务模块 1：mdm_train.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-11-04（第 15 天 / 共 15 天）
# 模块功能：人员资质培训管理模块。实现培训课程建档（课程名称、培训类型、
#           学时、讲师、计划日期、地点、名额上限与复训周期）、开班发布、
#           人员报名（同课程同人去重、名额校验）、报名移除、逐人签到、缺勤
#           标记、考核成绩登记（60 分合格线）、考核合格自动签发资质证书并按
#           课程复训周期推算到期日期、证书临期/过期复审扫描、课程结课归档
#           （未完成考核结论的人员不可结课）全过程管理；输出培训类型分布、
#           课程状态分布、考核结果分布、科室参训人次对比与近 6 个月参训趋势
#           五类统计图表，形成「课程建档 → 开班 → 报名签到 → 考核发证 →
#           证书复审 → 结课归档」的人员资质闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from mdm_dal import get_connection, write_ops_journal

# ------------------------------------------------------------------
# 培训类型字典：按院内设备管理培训的五种业务方向划分
# ------------------------------------------------------------------
COURSE_TYPE_DICT = {
    "OPERATION": "操作技能",
    "SAFETY": "安全法规",
    "MAINTENANCE": "维护保养",
    "CALIBRATION": "计量校准",
    "QUALITY": "质量管理",
}

# 课程状态字典：随开班、结课逐级流转
COURSE_STATE_DICT = {
    "PLAN": "已建档待开班",
    "OPEN": "报名开班中",
    "DONE": "已结课归档",
    "CANCEL": "已取消",
}

# 参训状态字典：随签到、考核逐级流转
ATTEND_STATE_DICT = {
    "ENROLL": "已报名",
    "SIGNED": "已签到",
    "ABSENT": "缺勤",
    "PASS": "考核合格",
    "FAIL": "考核不合格",
}

# 证书状态字典：随临期、过期复审扫描流转
CERT_STATE_DICT = {
    "VALID": "有效",
    "WARN": "临期复审",
    "EXPIRED": "已过期",
    "REVOKED": "已吊销",
}

# 考核合格线：不低于该分数判定合格并自动发证
PASS_SCORE_LINE = 60.0

# 证书临期预警天数：到期前该天数内进入临期复审
CERT_WARN_DAYS = 30

# 分页尺寸：培训课程独立分页
COURSE_PAGE_SIZE = 10

# 参训/证书候选人员池：（姓名，科室，岗位）
TRAINEE_POOL = (
    ("沈国栋", "重症医学科", "设备主管"),
    ("曹晓岚", "急诊医学科", "护士长"),
    ("韩志强", "手术麻醉科", "麻醉技师"),
    ("郑雨桐", "检验医学中心", "检验技师"),
    ("刘振宇", "影像诊断科", "放射技师"),
    ("许文静", "康复医学科", "康复治疗师"),
    ("杜天成", "重症医学科", "呼吸治疗师"),
    ("范晓芸", "急诊医学科", "急救护士"),
    ("任嘉明", "手术麻醉科", "设备工程师"),
    ("江雪莹", "检验医学中心", "设备工程师"),
    ("孟凡超", "影像诊断科", "技师组长"),
    ("宋雅芝", "康复医学科", "护师"),
)

# 讲师候选
LECTURER_LIST = ("厂家培训工程师-周工", "医工科-冯主任", "设备科-沈工",
                 "计量所-唐工", "质控科-纪主任")

# 演示课程清单
# （课程名称，培训类型，学时，讲师，计划偏移天数，名额，复训周期，目标状态）
DEMO_COURSE_LIST = (
    ("呼吸机临床操作与维护技能培训", "OPERATION", 12, "厂家培训工程师-周工",
     -150, 6, 730, "DONE"),
    ("医疗器械使用安全与法规专题培训", "SAFETY", 6, "医工科-冯主任",
     -118, 8, 1095, "DONE"),
    ("多参数监护仪维护保养实操培训", "MAINTENANCE", 8, "设备科-沈工",
     -62, 6, 730, "DONE"),
    ("医用计量器具校准规范培训", "CALIBRATION", 10, "计量所-唐工",
     -24, 6, 365, "DONE"),
    ("新购设备岗前操作培训", "OPERATION", 4, "厂家培训工程师-周工",
     3, 5, 730, "OPEN"),
    ("设备质量管理与不良事件上报培训", "QUALITY", 6, "质控科-纪主任",
     12, 8, 1095, "PLAN"),
    ("报废设备处置流程专题培训", "QUALITY", 4, "医工科-冯主任",
     -88, 4, 365, "CANCEL"),
)

# 演示参训安排：课程序号 → (人员序号, 考核成绩或状态标记)
# 成绩 0~100 表示参加考核；None 表示仅签到；"ABSENT" 表示缺勤
DEMO_ENROLL_PLAN = {
    0: ((0, 92), (3, 78), (6, 85), (9, 55), (7, 66)),
    1: ((1, 88), (2, 95), (4, 82), (7, 90), (10, 74), (11, 68)),
    2: ((8, 87), (5, 91), (11, "ABSENT"), (3, 63)),
    3: ((6, 84), (9, 96), (4, 71), (10, 89)),
    4: ((0, None), (2, None), (5, "ENROLL"), (8, None)),
}


def parse_date_text(text):
    """把 YYYY-MM-DD 文本解析为日期对象，解析失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def date_to_text(value):
    """把日期对象格式化为 YYYY-MM-DD 文本。"""
    return value.strftime("%Y-%m-%d") if value else ""


def today_date():
    """返回系统当前日期。"""
    return datetime.date.today()


def now_text():
    """返回当前时间的 YYYY-MM-DD HH:MM:SS 文本。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def shift_days_text(days):
    """返回相对今天偏移指定天数的日期文本。"""
    return date_to_text(today_date() + datetime.timedelta(days=days))


def round1(value):
    """把数值保留一位小数，空值按 0 处理。"""
    try:
        return round(float(value or 0), 1)
    except (TypeError, ValueError):
        return 0.0


def next_seq_no(table_name):
    """读取业务表的自增主键最大值，用于生成不重复的流水编号。

    表名形如 md_train_xxx，去掉前缀后拼接 _id 即为该表主键列名。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(%s_id),0) AS m FROM %s" % (
                table_name.replace("md_train_", ""), table_name)).fetchone()
    finally:
        conn.close()
    return int(row["m"] or 0) + 1


def build_course_code(seq_no):
    """生成培训课程编号，形如 TRN-0000001。"""
    return "TRN-%07d" % seq_no


def build_enroll_code(seq_no):
    """生成培训报名编号，形如 TRE-0000012。"""
    return "TRE-%07d" % seq_no


def build_cert_code(seq_no):
    """生成资质证书编号，形如 TC-0000009。"""
    return "TC-%07d" % seq_no


# ------------------------------------------------------------------
# 查询辅助
# ------------------------------------------------------------------
def fetch_course_row(course_code):
    """按课程编号读取一条培训课程，不存在返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_train_course WHERE course_code=?",
            ((course_code or "").strip(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def fetch_enroll_row(enroll_code):
    """按报名编号读取一条培训报名，不存在返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_train_enroll WHERE enroll_code=?",
            ((enroll_code or "").strip(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def refresh_course_counter(course_code):
    """重算课程的已报名人次与考核合格人次并回写。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(1) AS total, SUM(CASE WHEN attend_state='PASS' " +
            "THEN 1 ELSE 0 END) AS pass FROM md_train_enroll " +
            "WHERE course_code=?", (course_code,)).fetchone()
        conn.execute(
            "UPDATE md_train_course SET trainee_amount=?, pass_amount=? "
            "WHERE course_code=?",
            (int(row["total"] or 0), int(row["pass"] or 0), course_code))
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------
# 课程建档与开班
# ------------------------------------------------------------------
def save_train_course(course_code, course_name, course_type="OPERATION",
                      class_hours=4, lecturer_name="", plan_date="",
                      place_text="", max_trainee=6, cycle_days=730,
                      course_state="PLAN", remark_text="", operator="admin"):
    """新建或编辑一期培训课程。

    :return: 含 success、message 与 course_code 的执行结果字典
    """
    course_name = (course_name or "").strip()
    if not course_name:
        return {"success": False, "message": "请填写培训课程名称"}
    if course_type not in COURSE_TYPE_DICT:
        return {"success": False, "message": "培训类型 %s 不合法" % course_type}
    if course_state not in COURSE_STATE_DICT:
        return {"success": False, "message": "课程状态 %s 不合法" % course_state}
    try:
        hours = float(class_hours or 0)
    except (TypeError, ValueError):
        return {"success": False, "message": "培训学时须为数值"}
    if hours <= 0:
        return {"success": False, "message": "培训学时须大于 0"}
    try:
        quota = int(max_trainee or 0)
        cycle = int(cycle_days or 0)
    except (TypeError, ValueError):
        return {"success": False, "message": "名额与复训周期须为整数"}
    if quota < 1:
        return {"success": False, "message": "名额上限须至少 1 人"}
    if cycle < 30:
        return {"success": False, "message": "复训周期须不少于 30 天"}

    exist = None
    if (course_code or "").strip():
        exist = fetch_course_row(course_code)
        if exist and exist["course_state"] in ("DONE", "CANCEL"):
            return {"success": False,
                    "message": "已结课或已取消的课程不可再编辑"}

    plan_text = date_to_text(parse_date_text(plan_date) or today_date())
    conn = get_connection()
    try:
        if exist is None:
            code = build_course_code(next_seq_no("md_train_course"))
            conn.execute(
                "INSERT INTO md_train_course (course_code, course_name, "
                "course_type, class_hours, lecturer_name, plan_date, "
                "place_text, max_trainee, cycle_days, course_state, "
                "remark_text, operator_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, course_name, course_type, hours,
                 (lecturer_name or "").strip(), plan_text,
                 (place_text or "").strip(), quota, cycle, course_state,
                 (remark_text or "").strip(), (operator or "admin").strip()))
            conn.commit()
        else:
            code = exist["course_code"]
            if exist["trainee_amount"] > quota:
                return {"success": False,
                        "message": "名额上限不可低于已报名 %d 人" %
                        exist["trainee_amount"]}
            conn.execute(
                "UPDATE md_train_course SET course_name=?, course_type=?, "
                "class_hours=?, lecturer_name=?, plan_date=?, place_text=?, "
                "max_trainee=?, cycle_days=?, course_state=?, remark_text=?, "
                "operator_name=? WHERE course_code=?",
                (course_name, course_type, hours,
                 (lecturer_name or "").strip(), plan_text,
                 (place_text or "").strip(), quota, cycle, course_state,
                 (remark_text or "").strip(), (operator or "admin").strip(),
                 code))
            conn.commit()
    finally:
        conn.close()
    write_ops_journal("人员资质培训", "建档", code,
                      "培训课程 %s 建档完成" % course_name, operator)
    return {"success": True, "message": "培训课程 %s 已保存" % code,
            "course_code": code}


def open_train_course(course_code, operator="admin"):
    """课程开班：已建档课程发布为报名开班中。"""
    course = fetch_course_row(course_code)
    if course is None:
        return {"success": False, "message": "培训课程不存在"}
    if course["course_state"] != "PLAN":
        return {"success": False,
                "message": "仅已建档待开班的课程可开班，当前为%s" %
                COURSE_STATE_DICT.get(course["course_state"],
                                      course["course_state"])}
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_train_course SET course_state='OPEN' "
            "WHERE course_code=?", (course["course_code"],))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("人员资质培训", "开班", course["course_code"],
                      "培训课程发布开班，开放报名", operator)
    return {"success": True, "message": "培训课程 %s 已开班" % course_code}


def cancel_train_course(course_code, reason_text="", operator="admin"):
    """课程取消：已开班但未产生考核合格记录的课程可取消。"""
    course = fetch_course_row(course_code)
    if course is None:
        return {"success": False, "message": "培训课程不存在"}
    if course["course_state"] not in ("PLAN", "OPEN"):
        return {"success": False,
                "message": "已结课或已取消的课程不可再取消"}
    if int(course["pass_amount"] or 0) > 0:
        return {"success": False,
                "message": "课程已有 %d 人考核合格，不可取消" %
                course["pass_amount"]}
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_train_course SET course_state='CANCEL' "
            "WHERE course_code=?", (course["course_code"],))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("人员资质培训", "取消", course["course_code"],
                      (reason_text or "培训计划取消") + "，课程取消", operator)
    return {"success": True, "message": "培训课程 %s 已取消" % course_code}


# ------------------------------------------------------------------
# 报名、签到与缺勤
# ------------------------------------------------------------------
def add_train_enroll(course_code, trainee_name, trainee_dept="",
                     post_name="", enroll_date="", remark_text="",
                     operator="admin"):
    """为开班中的课程登记一名参训人员。

    同课程同人不可重复报名；报名人数不可超过课程名额上限。
    """
    course = fetch_course_row(course_code)
    if course is None:
        return {"success": False, "message": "培训课程不存在"}
    if course["course_state"] != "OPEN":
        return {"success": False,
                "message": "仅报名开班中的课程可报名，当前为%s" %
                COURSE_STATE_DICT.get(course["course_state"],
                                      course["course_state"])}
    trainee_name = (trainee_name or "").strip()
    if not trainee_name:
        return {"success": False, "message": "请填写参训人员姓名"}
    conn = get_connection()
    try:
        dup = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_enroll WHERE course_code=? "
            "AND trainee_name=?", (course["course_code"], trainee_name)
        ).fetchone()["c"]
        if dup:
            return {"success": False,
                    "message": "%s 已报名该课程，不可重复报名" % trainee_name}
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_enroll WHERE course_code=?",
            (course["course_code"],)).fetchone()["c"]
        if total >= int(course["max_trainee"] or 0):
            return {"success": False,
                    "message": "课程名额已满（上限 %d 人）" %
                    course["max_trainee"]}
        code = build_enroll_code(next_seq_no("md_train_enroll"))
        enroll_text = date_to_text(
            parse_date_text(enroll_date) or today_date())
        conn.execute(
            "INSERT INTO md_train_enroll (enroll_code, course_code, "
            "course_name, trainee_name, trainee_dept, post_name, "
            "enroll_date, remark_text) VALUES (?,?,?,?,?,?,?,?)",
            (code, course["course_code"], course["course_name"],
             trainee_name, (trainee_dept or "").strip(),
             (post_name or "").strip(), enroll_text,
             (remark_text or "").strip()))
        conn.commit()
    finally:
        conn.close()
    refresh_course_counter(course["course_code"])
    write_ops_journal("人员资质培训", "报名", code,
                      "%s 报名课程 %s" % (trainee_name, course_code), operator)
    return {"success": True, "message": "%s 已报名课程 %s" %
            (trainee_name, course_code), "enroll_code": code}


def remove_train_enroll(enroll_code, operator="admin"):
    """移除一条报名记录：仅已报名未签到状态可移除。"""
    enroll = fetch_enroll_row(enroll_code)
    if enroll is None:
        return {"success": False, "message": "报名记录不存在"}
    if enroll["attend_state"] != "ENROLL":
        return {"success": False,
                "message": "仅已报名未签到的记录可移除"}
    conn = get_connection()
    try:
        conn.execute("DELETE FROM md_train_enroll WHERE enroll_code=?",
                     (enroll["enroll_code"],))
        conn.commit()
    finally:
        conn.close()
    refresh_course_counter(enroll["course_code"])
    write_ops_journal("人员资质培训", "移除报名", enroll["enroll_code"],
                      "%s 退出课程 %s" % (enroll["trainee_name"],
                                          enroll["course_code"]), operator)
    return {"success": True, "message": "报名记录 %s 已移除" % enroll_code}


def sign_train_enroll(enroll_code, sign_date="", operator="admin"):
    """参训人员签到：已报名状态签到后进入已签到。"""
    enroll = fetch_enroll_row(enroll_code)
    if enroll is None:
        return {"success": False, "message": "报名记录不存在"}
    if enroll["attend_state"] != "ENROLL":
        return {"success": False,
                "message": "仅已报名状态可签到，当前为%s" %
                ATTEND_STATE_DICT.get(enroll["attend_state"],
                                      enroll["attend_state"])}
    sign_text = date_to_text(parse_date_text(sign_date) or today_date())
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_train_enroll SET attend_state='SIGNED', sign_date=? "
            "WHERE enroll_code=?", (sign_text, enroll["enroll_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("人员资质培训", "签到", enroll["enroll_code"],
                      "%s 签到课程 %s" % (enroll["trainee_name"],
                                          enroll["course_code"]), operator)
    return {"success": True, "message": "%s 已签到" % enroll["trainee_name"]}


def mark_absent_enroll(enroll_code, operator="admin"):
    """标记缺勤：已报名或已签到但未到课的人员标记缺勤。"""
    enroll = fetch_enroll_row(enroll_code)
    if enroll is None:
        return {"success": False, "message": "报名记录不存在"}
    if enroll["attend_state"] not in ("ENROLL", "SIGNED"):
        return {"success": False,
                "message": "已出考核结论的人员不可标记缺勤"}
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_train_enroll SET attend_state='ABSENT' "
            "WHERE enroll_code=?", (enroll["enroll_code"],))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("人员资质培训", "缺勤", enroll["enroll_code"],
                      "%s 标记缺勤" % enroll["trainee_name"], operator)
    return {"success": True, "message": "%s 已标记缺勤" % enroll["trainee_name"]}


# ------------------------------------------------------------------
# 考核发证与结课
# ------------------------------------------------------------------
def record_train_exam(enroll_code, exam_score, exam_date="",
                      operator="admin"):
    """登记考核成绩：签到人员成绩达 60 分自动合格并发证。

    :return: 含 success、message 与 cert_code 的执行结果字典
    """
    enroll = fetch_enroll_row(enroll_code)
    if enroll is None:
        return {"success": False, "message": "报名记录不存在"}
    if enroll["attend_state"] != "SIGNED":
        return {"success": False,
                "message": "仅已签到人员可登记考核，当前为%s" %
                ATTEND_STATE_DICT.get(enroll["attend_state"],
                                      enroll["attend_state"])}
    try:
        score = float(exam_score or 0)
    except (TypeError, ValueError):
        return {"success": False, "message": "考核成绩须为数值"}
    if not (0 <= score <= 100):
        return {"success": False, "message": "考核成绩须在 0 ~ 100 之间"}

    exam_text = date_to_text(parse_date_text(exam_date) or today_date())
    passed = score >= PASS_SCORE_LINE
    outcome = {"success": True,
               "message": "%s 考核成绩 %.1f 分，%s" % (
                   enroll["trainee_name"], score,
                   "合格并已发证" if passed else "不合格")}
    cert_code = ""
    conn = get_connection()
    try:
        if passed:
            course = conn.execute(
                "SELECT cycle_days, course_name FROM md_train_course "
                "WHERE course_code=?",
                (enroll["course_code"],)).fetchone()
            cycle = int(course["cycle_days"] or 730)
            expire_text = date_to_text(
                parse_date_text(exam_text) +
                datetime.timedelta(days=cycle))
            cert_code = build_cert_code(next_seq_no("md_train_cert"))
            conn.execute(
                "INSERT INTO md_train_cert (cert_code, course_code, "
                "enroll_code, trainee_name, trainee_dept, cert_name, "
                "issue_date, valid_days, expire_date, cert_state) "
                "VALUES (?,?,?,?,?,?,?,?,?, 'VALID')",
                (cert_code, enroll["course_code"], enroll["enroll_code"],
                 enroll["trainee_name"], enroll["trainee_dept"],
                 (enroll["course_name"] or "").replace("培训", "") + "资质证书",
                 exam_text, cycle, expire_text))
        conn.execute(
            "UPDATE md_train_enroll SET attend_state=?, exam_score=?, "
            "exam_date=? WHERE enroll_code=?",
            ("PASS" if passed else "FAIL", round1(score), exam_text,
             enroll["enroll_code"]))
        conn.commit()
    finally:
        conn.close()
    refresh_course_counter(enroll["course_code"])
    write_ops_journal("人员资质培训", "考核", enroll["enroll_code"],
                      "%s 考核 %.1f 分%s" % (enroll["trainee_name"], score,
                                             "（发证 %s）" % cert_code
                                             if cert_code else ""),
                      operator)
    outcome["cert_code"] = cert_code
    return outcome


def finish_train_course(course_code, finish_date="", remark_text="",
                        operator="admin"):
    """课程结课归档：全部报名人员均有终态结论（合格/不合格/缺勤）方可结课。"""
    course = fetch_course_row(course_code)
    if course is None:
        return {"success": False, "message": "培训课程不存在"}
    if course["course_state"] != "OPEN":
        return {"success": False,
                "message": "仅报名开班中的课程可结课，当前为%s" %
                COURSE_STATE_DICT.get(course["course_state"],
                                      course["course_state"])}
    conn = get_connection()
    try:
        pending = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_enroll WHERE course_code=? "
            "AND attend_state IN ('ENROLL','SIGNED')",
            (course["course_code"],)).fetchone()["c"]
        if pending:
            return {"success": False,
                    "message": "仍有 %d 人未出考核结论，不可结课" % pending}
        finish_text = date_to_text(parse_date_text(finish_date) or today_date())
        conn.execute(
            "UPDATE md_train_course SET course_state='DONE', finish_date=? "
            "WHERE course_code=?", (finish_text, course["course_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("人员资质培训", "结课", course["course_code"],
                      (remark_text or "课程结课归档") + "，合格 %d 人" %
                      int(course["pass_amount"] or 0), operator)
    return {"success": True, "message": "培训课程 %s 已结课归档" % course_code}


def scan_cert_expire(operator="admin"):
    """证书复审扫描：到期证书置过期，30 天内到期证书置临期复审。

    :return: 本次更新状态的证书数量
    """
    updated = 0
    today = today_date()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT cert_code, cert_state, expire_date FROM md_train_cert "
            "WHERE cert_state IN ('VALID','WARN')").fetchall()
        for row in rows:
            expire = parse_date_text(row["expire_date"])
            if expire is None:
                continue
            new_state = ""
            if expire < today:
                new_state = "EXPIRED"
            elif (expire - today).days <= CERT_WARN_DAYS:
                new_state = "WARN"
            if new_state and new_state != row["cert_state"]:
                conn.execute(
                    "UPDATE md_train_cert SET cert_state=? WHERE cert_code=?",
                    (new_state, row["cert_code"]))
                updated += 1
        conn.commit()
    finally:
        conn.close()
    if updated:
        write_ops_journal("人员资质培训", "复审扫描", "CERT-SCAN",
                          "证书复审扫描，更新 %d 本证书状态" % updated, operator)
    return updated


# ------------------------------------------------------------------
# 分页查询
# ------------------------------------------------------------------
def fetch_train_courses(keyword="", course_type="", course_state="", page=1):
    """按条件分页查询培训课程，返回分页结果与课程列表。"""
    sql = "SELECT * FROM md_train_course WHERE 1=1 "
    args = []
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (course_code LIKE ? OR course_name LIKE ? OR "
                "lecturer_name LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like])
    if course_type:
        sql += "AND course_type=? "
        args.append(course_type)
    if course_state:
        sql += "AND course_state=? "
        args.append(course_state)
    conn = get_connection()
    try:
        total = conn.execute(
            sql.replace("SELECT *", "SELECT COUNT(1) AS c", 1),
            args).fetchone()["c"]
        page_amount = max(1, (total + COURSE_PAGE_SIZE - 1) // COURSE_PAGE_SIZE)
        try:
            page_no = max(1, min(int(page), page_amount))
        except (TypeError, ValueError):
            page_no = 1
        rows = conn.execute(
            sql + "ORDER BY course_id DESC LIMIT ? OFFSET ?",
            args + [COURSE_PAGE_SIZE, (page_no - 1) * COURSE_PAGE_SIZE]
        ).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["type_label"] = COURSE_TYPE_DICT.get(item["course_type"],
                                                    item["course_type"])
        record["state_label"] = COURSE_STATE_DICT.get(item["course_state"],
                                                      item["course_state"])
        records.append(record)
    return {"records": records, "total": total, "page": page_no,
            "page_amount": page_amount}


def fetch_train_enrolls(course_code="", attend_state="", dept_name="",
                        keyword="", limit=40):
    """按条件查询培训报名记录，支持按课程、状态、科室与关键字过滤。"""
    sql = "SELECT * FROM md_train_enroll WHERE 1=1 "
    args = []
    if course_code:
        sql += "AND course_code=? "
        args.append(course_code)
    if attend_state:
        sql += "AND attend_state=? "
        args.append(attend_state)
    if dept_name:
        sql += "AND trainee_dept=? "
        args.append(dept_name)
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (enroll_code LIKE ? OR trainee_name LIKE ? OR "
                "course_name LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like])
    conn = get_connection()
    try:
        rows = conn.execute(
            sql + "ORDER BY enroll_id DESC LIMIT ?",
            args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["state_label"] = ATTEND_STATE_DICT.get(item["attend_state"],
                                                      item["attend_state"])
        records.append(record)
    return records


def fetch_train_certs(cert_state="", keyword="", limit=40):
    """按条件查询资质证书，支持按状态与关键字过滤。"""
    sql = "SELECT * FROM md_train_cert WHERE 1=1 "
    args = []
    if cert_state:
        sql += "AND cert_state=? "
        args.append(cert_state)
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (cert_code LIKE ? OR trainee_name LIKE ? OR "
                "cert_name LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like])
    conn = get_connection()
    try:
        rows = conn.execute(
            sql + "ORDER BY cert_id DESC LIMIT ?",
            args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["state_label"] = CERT_STATE_DICT.get(item["cert_state"],
                                                    item["cert_state"])
        records.append(record)
    return records


# ------------------------------------------------------------------
# 统计与图表
# ------------------------------------------------------------------
def build_train_summary():
    """汇总培训核心指标：课程量、参训人次、考核结果、发证与复审概况。"""
    conn = get_connection()
    try:
        course_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_course").fetchone()["c"]
        open_course = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_course WHERE "
            "course_state='OPEN'").fetchone()["c"]
        done_course = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_course WHERE "
            "course_state='DONE'").fetchone()["c"]
        cancel_course = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_course WHERE "
            "course_state='CANCEL'").fetchone()["c"]
        trainee_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_enroll").fetchone()["c"]
        pass_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_enroll WHERE "
            "attend_state='PASS'").fetchone()["c"]
        fail_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_enroll WHERE "
            "attend_state='FAIL'").fetchone()["c"]
        absent_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_enroll WHERE "
            "attend_state='ABSENT'").fetchone()["c"]
        avg_row = conn.execute(
            "SELECT COALESCE(AVG(exam_score),0) AS a FROM md_train_enroll "
            "WHERE attend_state IN ('PASS','FAIL')").fetchone()
        cert_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_cert").fetchone()["c"]
        valid_cert = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_cert WHERE "
            "cert_state='VALID'").fetchone()["c"]
        warn_cert = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_cert WHERE "
            "cert_state='WARN'").fetchone()["c"]
        expired_cert = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_cert WHERE "
            "cert_state='EXPIRED'").fetchone()["c"]
        hours_row = conn.execute(
            "SELECT COALESCE(SUM(class_hours * trainee_amount),0) AS h "
            "FROM md_train_course WHERE course_state='DONE'").fetchone()
    finally:
        conn.close()
    exam_total = pass_total + fail_total
    return {
        "course_total": course_total,
        "open_course": open_course,
        "done_course": done_course,
        "cancel_course": cancel_course,
        "trainee_total": trainee_total,
        "pass_total": pass_total,
        "fail_total": fail_total,
        "absent_total": absent_total,
        "pass_rate": round1(pass_total * 100.0 / exam_total) if exam_total
        else 0.0,
        "avg_score": round1(avg_row["a"]),
        "cert_total": cert_total,
        "valid_cert": valid_cert,
        "warn_cert": warn_cert,
        "expired_cert": expired_cert,
        "train_hours": round1(hours_row["h"]),
    }


def build_train_charts():
    """构建五类图表数据：培训类型分布、课程状态分布、考核结果分布、
    科室参训人次对比与近 6 个月参训趋势。"""
    conn = get_connection()
    try:
        type_rows = conn.execute(
            "SELECT course_type, COUNT(1) AS c FROM md_train_course "
            "GROUP BY course_type").fetchall()
        state_rows = conn.execute(
            "SELECT course_state, COUNT(1) AS c FROM md_train_course "
            "GROUP BY course_state").fetchall()
        attend_rows = conn.execute(
            "SELECT attend_state, COUNT(1) AS c FROM md_train_enroll "
            "GROUP BY attend_state").fetchall()
        dept_rows = conn.execute(
            "SELECT trainee_dept, COUNT(1) AS c FROM md_train_enroll "
            "GROUP BY trainee_dept ORDER BY c DESC").fetchall()
        month_rows = conn.execute(
            "SELECT substr(enroll_date,1,7) AS ym, COUNT(1) AS c, "
            "SUM(CASE WHEN attend_state='PASS' THEN 1 ELSE 0 END) AS p "
            "FROM md_train_enroll GROUP BY substr(enroll_date,1,7)").fetchall()
    finally:
        conn.close()

    type_counter = {item["course_type"]: item["c"] for item in type_rows}
    type_distribution = [
        {"name": COURSE_TYPE_DICT.get(code, code),
         "value": type_counter.get(code, 0)}
        for code in COURSE_TYPE_DICT]
    state_counter = {item["course_state"]: item["c"] for item in state_rows}
    state_distribution = [
        {"name": COURSE_STATE_DICT.get(code, code),
         "value": state_counter.get(code, 0)}
        for code in COURSE_STATE_DICT]
    attend_counter = {item["attend_state"]: item["c"] for item in attend_rows}
    result_distribution = [
        {"name": ATTEND_STATE_DICT.get(code, code),
         "value": attend_counter.get(code, 0)}
        for code in ATTEND_STATE_DICT]
    dept_rank = [{"name": item["trainee_dept"] or "未登记",
                  "value": int(item["c"] or 0)} for item in dept_rows][:8]

    base = today_date()
    month_keys = []
    for offset in range(5, -1, -1):
        month_no = base.month - offset
        year_no = base.year
        while month_no <= 0:
            month_no += 12
            year_no -= 1
        month_keys.append("%04d-%02d" % (year_no, month_no))
    month_map = {item["ym"]: (int(item["c"] or 0), int(item["p"] or 0))
                 for item in month_rows}
    month_trend = {
        "months": month_keys,
        "trainee_values": [month_map.get(key, (0, 0))[0] for key in month_keys],
        "pass_values": [month_map.get(key, (0, 0))[1] for key in month_keys],
    }
    return {
        "type_distribution": type_distribution,
        "state_distribution": state_distribution,
        "result_distribution": result_distribution,
        "dept_rank": dept_rank,
        "month_trend": month_trend,
    }


def fetch_train_options():
    """培训页面下拉选项：字典项、课程候选、科室候选与人员候选。"""
    conn = get_connection()
    try:
        course_rows = conn.execute(
            "SELECT course_code, course_name, course_state FROM "
            "md_train_course ORDER BY course_id DESC LIMIT 20").fetchall()
        dept_rows = conn.execute(
            "SELECT DISTINCT trainee_dept FROM md_train_enroll "
            "WHERE trainee_dept<>'' ORDER BY trainee_dept").fetchall()
    finally:
        conn.close()
    dept_list = [item["trainee_dept"] for item in dept_rows]
    for person in TRAINEE_POOL:
        if person[1] not in dept_list:
            dept_list.append(person[1])
    return {
        "course_list": [dict(row) for row in course_rows],
        "dept_list": sorted(dept_list),
        "trainee_list": [{"name": p[0], "dept": p[1], "post": p[2]}
                         for p in TRAINEE_POOL],
        "lecturer_list": list(LECTURER_LIST),
        "type_options": [{"code": k, "label": v}
                         for k, v in COURSE_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in COURSE_STATE_DICT.items()],
        "attend_options": [{"code": k, "label": v}
                           for k, v in ATTEND_STATE_DICT.items()],
        "cert_state_options": [{"code": k, "label": v}
                               for k, v in CERT_STATE_DICT.items()],
    }


def build_train_payload(keyword="", course_type="", course_state="",
                        enroll_course="", attend_state="", enroll_dept="",
                        enroll_keyword="", cert_state="", cert_keyword="",
                        page=1):
    """组装人员资质培训页面所需的全部数据。"""
    pager = fetch_train_courses(keyword, course_type, course_state, page)
    return {
        "summary": build_train_summary(),
        "charts": build_train_charts(),
        "courses": pager["records"],
        "course_total": pager["total"],
        "page": pager["page"],
        "page_amount": pager["page_amount"],
        "enrolls": fetch_train_enrolls(enroll_course, attend_state,
                                       enroll_dept, enroll_keyword),
        "certs": fetch_train_certs(cert_state, cert_keyword),
        "options": fetch_train_options(),
        "result": "OK",
    }


def build_train_brief():
    """人员资质培训看板摘要，供综合概览页嵌入。"""
    summary = build_train_summary()
    return {
        "course_total": summary["course_total"],
        "open_course": summary["open_course"],
        "trainee_total": summary["trainee_total"],
        "pass_rate": summary["pass_rate"],
        "cert_total": summary["cert_total"],
        "warn_cert": summary["warn_cert"] + summary["expired_cert"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_train():
    """灌入人员资质培训演示数据：课程建档开班、报名签到、考核发证、
    结课归档与证书复审扫描，覆盖全部状态字典取值。

    :return: 生成的培训课程条数
    """
    conn = get_connection()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM md_train_course").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()

    for (name, ctype, hours, lecturer, offset, quota,
         cycle, target) in DEMO_COURSE_LIST:
        save_train_course("", name, ctype, hours, lecturer,
                          shift_days_text(offset), "技能培训中心", quota,
                          cycle, "PLAN", "演示课程数据", "医工科-冯主任")
    course_codes = []
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT course_code FROM md_train_course ORDER BY course_id ASC"
        ).fetchall()
        course_codes = [row["course_code"] for row in rows]
    finally:
        conn.close()

    for index, plan in DEMO_ENROLL_PLAN.items():
        if index >= len(course_codes):
            continue
        code = course_codes[index]
        open_train_course(code, "医工科-冯主任")
        for person_index, exam in plan:
            person = TRAINEE_POOL[person_index % len(TRAINEE_POOL)]
            enroll = add_train_enroll(code, person[0], person[1], person[2],
                                      shift_days_text(-3), "演示报名数据",
                                      "医工科-冯主任")
            if not enroll.get("success"):
                continue
            enroll_code = enroll["enroll_code"]
            if exam == "ENROLL":
                continue
            if exam == "ABSENT":
                sign_train_enroll(enroll_code, shift_days_text(-1),
                                  "医工科-冯主任")
                mark_absent_enroll(enroll_code, "医工科-冯主任")
            elif exam is None:
                sign_train_enroll(enroll_code, shift_days_text(-1),
                                  "医工科-冯主任")
            else:
                sign_train_enroll(enroll_code, shift_days_text(-1),
                                  "医工科-冯主任")
                record_train_exam(enroll_code, exam, shift_days_text(-1),
                                  "医工科-冯主任")
        finish_train_course(code, shift_days_text(-1), "演示结课归档",
                            "医工科-冯主任")

    # 证书复审演示：把两本证书的到期日改写为临期与已过期，再执行复审扫描
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT cert_code FROM md_train_cert ORDER BY cert_id ASC"
        ).fetchall()
        codes = [row["cert_code"] for row in rows]
        if len(codes) >= 2:
            conn.execute(
                "UPDATE md_train_cert SET issue_date=?, valid_days=730, "
                "expire_date=? WHERE cert_code=?",
                (shift_days_text(-710), shift_days_text(20), codes[0]))
            conn.execute(
                "UPDATE md_train_cert SET issue_date=?, valid_days=730, "
                "expire_date=? WHERE cert_code=?",
                (shift_days_text(-800), shift_days_text(-70), codes[1]))
            conn.commit()
    finally:
        conn.close()
    scan_cert_expire("医工科-冯主任")

    # 演示取消课程：最后一期课程（未开班、无人报名）置为已取消
    conn = get_connection()
    try:
        cancel_code = conn.execute(
            "SELECT course_code FROM md_train_course WHERE course_state='PLAN' "
            "AND trainee_amount=0 ORDER BY course_id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if cancel_code is not None:
        cancel_train_course(cancel_code["course_code"],
                            "培训计划调整，课程取消", "医工科-冯主任")
    return len(DEMO_COURSE_LIST)

# --------------------------------------------------------------------------
# 第 15 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY15_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY15_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 15 天新增：人员资质培训管理三表
-- ==================================================================
-- 培训课程表：按「一次培训一期课程」登记课程名称、培训类型、学时、讲师、
-- 计划日期、复训周期与名额上限，支撑课程建档、开班、考核结课全周期管理
CREATE TABLE IF NOT EXISTS md_train_course (
    course_id       INTEGER PRIMARY KEY AUTOINCREMENT, -- 课程主键
    course_code     TEXT    NOT NULL UNIQUE,           -- 课程编号（TRN-序号）
    course_name     TEXT    DEFAULT '',                -- 课程名称
    course_type     TEXT    DEFAULT 'OPERATION',       -- 培训类型（见 COURSE_TYPE_DICT）
    class_hours     REAL    DEFAULT 0,                 -- 培训学时
    lecturer_name   TEXT    DEFAULT '',                -- 授课讲师
    plan_date       TEXT    DEFAULT '',                -- 计划开课日期
    finish_date     TEXT    DEFAULT '',                -- 实际结课日期
    place_text      TEXT    DEFAULT '',                -- 培训地点
    max_trainee     INTEGER DEFAULT 0,                 -- 名额上限
    trainee_amount  INTEGER DEFAULT 0,                 -- 已报名人次
    pass_amount     INTEGER DEFAULT 0,                 -- 考核合格人次
    cycle_days      INTEGER DEFAULT 730,               -- 复训周期（天）
    course_state    TEXT    DEFAULT 'PLAN',            -- 课程状态（见 COURSE_STATE_DICT）
    remark_text     TEXT    DEFAULT '',                -- 课程说明
    operator_name   TEXT    DEFAULT '',                -- 登记人
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 培训报名表：一期课程多名人员报名，逐人记录报名、签到、考核成绩与结论，
-- 考核合格自动关联发证
CREATE TABLE IF NOT EXISTS md_train_enroll (
    enroll_id       INTEGER PRIMARY KEY AUTOINCREMENT, -- 报名主键
    enroll_code     TEXT    NOT NULL UNIQUE,           -- 报名编号（TRE-序号）
    course_code     TEXT    DEFAULT '',                -- 所属课程编号
    course_name     TEXT    DEFAULT '',                -- 课程名称快照
    trainee_name    TEXT    DEFAULT '',                -- 参训人员姓名
    trainee_dept    TEXT    DEFAULT '',                -- 参训人员科室
    post_name       TEXT    DEFAULT '',                -- 岗位名称
    enroll_date     TEXT    DEFAULT '',                -- 报名日期
    sign_date       TEXT    DEFAULT '',                -- 签到日期
    exam_score      REAL    DEFAULT 0,                 -- 考核成绩
    exam_date       TEXT    DEFAULT '',                -- 考核日期
    attend_state    TEXT    DEFAULT 'ENROLL',          -- 参训状态（见 ATTEND_STATE_DICT）
    remark_text     TEXT    DEFAULT '',                -- 报名备注
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 资质证书表：考核合格自动发证，逐证记录发证日期、有效期与临期/过期复审
CREATE TABLE IF NOT EXISTS md_train_cert (
    cert_id         INTEGER PRIMARY KEY AUTOINCREMENT, -- 证书主键
    cert_code       TEXT    NOT NULL UNIQUE,           -- 证书编号（TC-序号）
    course_code     TEXT    DEFAULT '',                -- 关联课程编号
    enroll_code     TEXT    DEFAULT '',                -- 关联报名编号
    trainee_name    TEXT    DEFAULT '',                -- 持证人员姓名
    trainee_dept    TEXT    DEFAULT '',                -- 持证人员科室
    cert_name       TEXT    DEFAULT '',                -- 证书名称
    issue_date      TEXT    DEFAULT '',                -- 发证日期
    valid_days      INTEGER DEFAULT 730,               -- 有效天数
    expire_date     TEXT    DEFAULT '',                -- 到期日期
    cert_state      TEXT    DEFAULT 'VALID',            -- 证书状态（见 CERT_STATE_DICT）
    remark_text     TEXT    DEFAULT '',                -- 证书备注
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_tcourse_code       ON md_train_course (course_code);
CREATE INDEX IF NOT EXISTS idx_tcourse_type       ON md_train_course (course_type);
CREATE INDEX IF NOT EXISTS idx_tcourse_state      ON md_train_course (course_state);
CREATE INDEX IF NOT EXISTS idx_tcourse_date       ON md_train_course (plan_date);
CREATE INDEX IF NOT EXISTS idx_tenroll_code       ON md_train_enroll (enroll_code);
CREATE INDEX IF NOT EXISTS idx_tenroll_course     ON md_train_enroll (course_code);
CREATE INDEX IF NOT EXISTS idx_tenroll_state      ON md_train_enroll (attend_state);
CREATE INDEX IF NOT EXISTS idx_tenroll_dept       ON md_train_enroll (trainee_dept);
CREATE INDEX IF NOT EXISTS idx_tcert_code         ON md_train_cert (cert_code);
CREATE INDEX IF NOT EXISTS idx_tcert_course       ON md_train_cert (course_code);
CREATE INDEX IF NOT EXISTS idx_tcert_state        ON md_train_cert (cert_state);
CREATE INDEX IF NOT EXISTS idx_tcert_expire       ON md_train_cert (expire_date);
"""


def apply_day15_schema(conn):
    """把第 15 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY15_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 15 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

from mdm_train import (
    build_train_payload,
    build_train_brief,
    save_train_course,
    open_train_course,
    cancel_train_course,
    add_train_enroll,
    remove_train_enroll,
    sign_train_enroll,
    mark_absent_enroll,
    record_train_exam,
    finish_train_course,
    scan_cert_expire,
    seed_demo_train,
    COURSE_TYPE_DICT,
    COURSE_STATE_DICT,
    ATTEND_STATE_DICT,
    CERT_STATE_DICT,
)

# 【函数内插入】def api_overview():
#    # 第 15 天新增：总览叠加人员资质培训摘要，供培训概况卡渲染
#    payload["train_brief"] = build_train_brief()

@app.route("/md/train")
def panel_train():
    """人员资质培训管理页面：课程建档、报名考核与证书复审（第 15 天）。"""
    write_ops_journal("人员资质培训", "查询", "PANEL-TRAIN",
                      "打开人员资质培训管理页面")
    return render_template(
        "panel_train.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/train/panel")
def api_train_panel():
    """培训面板数据接口：概览指标、五类图表、课程、报名与证书。"""
    payload = build_train_payload(
        keyword=request.args.get("keyword", ""),
        course_type=request.args.get("course_type", ""),
        course_state=request.args.get("course_state", ""),
        enroll_course=request.args.get("enroll_course", ""),
        attend_state=request.args.get("attend_state", ""),
        enroll_dept=request.args.get("enroll_dept", ""),
        enroll_keyword=request.args.get("enroll_keyword", ""),
        cert_state=request.args.get("cert_state", ""),
        cert_keyword=request.args.get("cert_keyword", ""),
        page=request.args.get("page", "1"),
    )
    return jsonify(payload)


@app.route("/api/md/train/course/save", methods=["POST"])
def api_train_course_save():
    """课程建档接口：新建培训课程，校验类型、学时、名额与复训周期。"""
    body = request.get_json(silent=True) or {}
    hours = body.get("class_hours")
    if hours is None:
        hours = 4
    quota = body.get("max_trainee")
    if quota is None:
        quota = 6
    cycle = body.get("cycle_days")
    if cycle is None:
        cycle = 730
    result = save_train_course(
        body.get("course_code") or "",
        body.get("course_name") or "",
        body.get("course_type") or "OPERATION",
        hours,
        body.get("lecturer_name") or "",
        body.get("plan_date") or "",
        body.get("place_text") or "",
        quota,
        cycle,
        body.get("course_state") or "PLAN",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/train/course/open", methods=["POST"])
def api_train_course_open():
    """课程开班接口：已建档课程发布为报名开班中。"""
    body = request.get_json(silent=True) or {}
    result = open_train_course(
        body.get("course_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/train/course/cancel", methods=["POST"])
def api_train_course_cancel():
    """课程取消接口：已产生考核合格记录的课程不可取消。"""
    body = request.get_json(silent=True) or {}
    result = cancel_train_course(
        body.get("course_code") or "",
        body.get("reason_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/train/course/finish", methods=["POST"])
def api_train_course_finish():
    """课程结课接口：全部人员均有终态结论方可结课归档。"""
    body = request.get_json(silent=True) or {}
    result = finish_train_course(
        body.get("course_code") or "",
        body.get("finish_date") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/train/enroll/add", methods=["POST"])
def api_train_enroll_add():
    """人员报名接口：同课程同人去重，报名不可超名额上限。"""
    body = request.get_json(silent=True) or {}
    result = add_train_enroll(
        body.get("course_code") or "",
        body.get("trainee_name") or "",
        body.get("trainee_dept") or "",
        body.get("post_name") or "",
        body.get("enroll_date") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/train/enroll/remove", methods=["POST"])
def api_train_enroll_remove():
    """报名移除接口：仅已报名未签到状态可移除。"""
    body = request.get_json(silent=True) or {}
    result = remove_train_enroll(
        body.get("enroll_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/train/enroll/sign", methods=["POST"])
def api_train_enroll_sign():
    """参训签到接口：已报名人员签到后进入已签到。"""
    body = request.get_json(silent=True) or {}
    result = sign_train_enroll(
        body.get("enroll_code") or "",
        body.get("sign_date") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/train/enroll/absent", methods=["POST"])
def api_train_enroll_absent():
    """缺勤标记接口：未出考核结论的人员标记缺勤。"""
    body = request.get_json(silent=True) or {}
    result = mark_absent_enroll(
        body.get("enroll_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/train/enroll/exam", methods=["POST"])
def api_train_enroll_exam():
    """考核登记接口：签到人员成绩达 60 分自动合格并发证。"""
    body = request.get_json(silent=True) or {}
    result = record_train_exam(
        body.get("enroll_code") or "",
        body.get("exam_score") or 0,
        body.get("exam_date") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/train/cert/scan", methods=["POST"])
def api_train_cert_scan():
    """证书复审扫描接口：到期证书置过期，30 天内到期置临期复审。"""
    body = request.get_json(silent=True) or {}
    amount = scan_cert_expire((body.get("operator") or "admin").strip())
    return jsonify({
        "result": "OK",
        "amount": amount,
        "message": "证书复审扫描完成，更新 %d 本证书状态" % amount,
    })


@app.route("/api/md/train/options")
def api_train_options():
    """培训下拉选项接口：培训类型、课程状态、参训状态与证书状态字典。"""
    return jsonify({
        "result": "OK",
        "type_options": [{"code": k, "label": v}
                         for k, v in COURSE_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in COURSE_STATE_DICT.items()],
        "attend_options": [{"code": k, "label": v}
                           for k, v in ATTEND_STATE_DICT.items()],
        "cert_state_options": [{"code": k, "label": v}
                               for k, v in CERT_STATE_DICT.items()],
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_train()

# 【函数内插入】if __name__ == "__main__":
#    print("资质培训： http://127.0.0.1:%d/md/train" % PORT)

# --------------------------------------------------------------------------
# 第 15 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_train.js              新增 558 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_train.html       新增 409 行
